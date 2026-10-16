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


def main() -> None:
    repo, number, expected_ref = (os.environ[k] for k in ("REPO", "PR_NUMBER", "EXPECTED_HEAD_REF"))
    pr = api(f"repos/{repo}/pulls/{number}")
    head = pr.get("head") or {}
    # Select the live checkout only after acquiring the shared PR lock. A queued
    # review may have observed an older head while the preceding fixer pushed.
    sha, ref = str(head.get("sha") or ""), str(head.get("ref") or "")
    if (pr.get("state") != "open" or pr.get("draft") is not False
            or (head.get("repo") or {}).get("full_name") != repo
            or not SAFE_SHA_RE.fullmatch(sha) or not SAFE_REF_RE.fullmatch(ref)
            or ref in PROTECTED_REFS or ref != expected_ref):
        emit("true")
        return
    login = pr["user"]["login"]
    if api(f"repos/{repo}/collaborators/{login}/permission").get("permission") not in {"write", "maintain", "admin"}:
        emit("true")
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
