#!/usr/bin/env python3
"""Recheck current PR/findings under the shared fixer lock. No PR code executed."""
from __future__ import annotations

import json
import os
import re
import subprocess

DEVIN = "devin-ai-integration"
DEVIN_ID = 158243242
SAFE_REF_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._/-]+$")
SAFE_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
PROTECTED_REFS = frozenset({"master", "main", "HEAD"})
# Human pause: a live PR label, or repo variable CLAUDE_FIXER_PAUSE=true for
# every PR. Checked under the shared lock before any checkout or reservation.
PAUSE_LABELS = frozenset({"claude:pause", "hold"})
LOOP_CAP = 8
# GitHub's PR commits endpoint lists at most 250 commits (oldest first), so a
# longer PR may hide its newest commits. Treat a possibly truncated list as at
# the cap: fail closed until a human reviews the branch.
COMMIT_LIST_LIMIT = 250
QUERY = """query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$name) { pullRequest(number:$number) {
    reviewThreads(first:100, after:$cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { id isResolved isOutdated comments(first:1) { nodes {
        databaseId author { login __typename } body
      } } }
    }
  } }
}"""


def eligible_comment_ids(threads: list[dict]) -> list[int]:
    findings = []
    for thread in threads:
        if thread.get("isResolved") is not False or thread.get("isOutdated") is not False:
            continue
        roots = (thread.get("comments") or {}).get("nodes") or []
        if not roots:
            continue
        root = roots[0]
        author = root.get("author") or {}
        if (author.get("login") not in {DEVIN, DEVIN + "[bot]"}
                or author.get("__typename") != "Bot"):
            continue
        matches = re.findall(r"<!-- devin-review-comment (\{.*?\}) -->", root.get("body", ""))
        if len(matches) != 1:
            continue
        try:
            if json.loads(matches[0]).get("kind") != "bug":
                continue
        except (ValueError, AttributeError):
            continue
        comment_id = root.get("databaseId")
        if isinstance(comment_id, int) and not isinstance(comment_id, bool) and comment_id > 0:
            findings.append(comment_id)
    return sorted(set(findings))


def api(path: str):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def pause_reason(pr: dict) -> str:
    if os.environ.get("CLAUDE_FIXER_PAUSE") == "true":
        return "repo variable CLAUDE_FIXER_PAUSE"
    labels = pr.get("labels")
    # Unreadable labels fail closed: a hold must not be missed.
    if not isinstance(labels, list) or not all(isinstance(label, dict) for label in labels):
        return "unreadable PR labels"
    held = sorted(PAUSE_LABELS & {str(label.get("name") or "").lower() for label in labels})
    return f"label {held[0]}" if held else ""


def claude_streak(repo: str, number: str) -> int:
    """Same rule as the Devin-path loop cap: Claude commits after the newest human commit."""
    commits, page = [], 1
    while True:
        batch = api(f"repos/{repo}/pulls/{number}/commits?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("invalid commit listing")
        commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    if len(commits) >= COMMIT_LIST_LIMIT:
        return LOOP_CAP
    count = 0
    for item in reversed(commits):
        login = str((item.get("author") or {}).get("login") or "-")
        name = str(((item.get("commit") or {}).get("author") or {}).get("name") or "-")
        if login == "claude[bot]" or name in {"claude[bot]", "claude"}:
            count += 1
        elif login.endswith("[bot]") or name.endswith("[bot]"):
            continue
        else:
            break
    return count


def main() -> None:
    repo, number, expected_ref = (os.environ[k] for k in ("REPO", "PR_NUMBER", "EXPECTED_HEAD_REF"))
    mention = os.environ.get("REQUEST_MODE") == "mention"
    pr = api(f"repos/{repo}/pulls/{number}")
    reason = pause_reason(pr)
    if reason:
        print(f"::notice::Claude PR writer paused by {reason}; nothing reserved or run.")
        emit("true")
        return
    head = pr.get("head") or {}
    # Select the live checkout only after acquiring the shared PR lock. A queued
    # review may have observed an older head while the preceding fixer pushed.
    sha, ref = str(head.get("sha") or ""), str(head.get("ref") or "")
    if (pr.get("state") != "open" or (not mention and pr.get("draft") is not False)
            or (head.get("repo") or {}).get("full_name") != repo
            or not SAFE_SHA_RE.fullmatch(sha) or not SAFE_REF_RE.fullmatch(ref)
            or ref in PROTECTED_REFS or ref != expected_ref):
        emit("true")
        return
    login = pr["user"]["login"]
    if api(f"repos/{repo}/collaborators/{login}/permission").get("permission") not in {"write", "maintain", "admin"}:
        emit("true")
        return
    if mention:
        actor = os.environ.get("MENTION_ACTOR", "")
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", actor)
                or api(f"repos/{repo}/collaborators/{actor}/permission").get("permission") not in {"write", "maintain", "admin"}):
            emit("true")
            return
        streak = claude_streak(repo, number)
        if streak >= LOOP_CAP:
            print(f"::notice::@claude skipped: {streak} Claude commits since the last human commit "
                  f"(cap {LOOP_CAP}). A human commit resets the streak.")
            emit("true")
            return
        emit("false", sha=sha, ref=ref)
        return
    review_id = os.environ.get("REVIEW_ID", "")
    if review_id:
        if not review_id.isdigit() or int(review_id) <= 0:
            emit("true")
            return
        # This PR-scoped endpoint proves the delayed review belongs to this PR.
        review = api(f"repos/{repo}/pulls/{number}/reviews/{review_id}")
        user = review.get("user") or {}
        if (review.get("id") != int(review_id)
                or user.get("login") != DEVIN + "[bot]"
                or user.get("type") != "Bot" or user.get("id") != DEVIN_ID
                or str(review.get("state") or "").lower() not in {"commented", "changes_requested"}
                or not SAFE_SHA_RE.fullmatch(str(review.get("commit_id") or ""))):
            emit("true")
            return
    owner, name = repo.split("/")
    threads, cursor = [], None
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"owner={owner}",
                "-f", f"name={name}", "-F", f"number={number}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        data = json.loads(subprocess.check_output(args, text=True))
        page = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        threads.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
        if not cursor:
            raise ValueError("missing thread cursor")
    ids = eligible_comment_ids(threads)
    emit("false" if ids else "true", ids, sha=sha, ref=ref)


def emit(skip: str, ids: list[int] | None = None, *, sha: str = "", ref: str = "") -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"skip={skip}\ncomment_ids={json.dumps(ids or [])}\nhead_sha={sha}\nhead_ref={ref}\n")


if __name__ == "__main__":
    main()
