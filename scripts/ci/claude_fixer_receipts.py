#!/usr/bin/env python3
"""Locked fixer eligibility, explicit receipts, and per-head attempt budget."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess

DEVIN_ID = 158243242
CLAUDE_ID = 209825114
ACTIONS_ID = 41898282
MAX_ATTEMPTS = 3
SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
METADATA_RE = re.compile(r"<!-- devin-review-comment (\{.*?\}) -->")
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


def digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def marker(kind: str, data: dict) -> str:
    return f"<!-- claude-fixer-{kind}:" + json.dumps(data, sort_keys=True, separators=(",", ":")) + " -->"


def records(body: str, kind: str) -> list[dict]:
    found = []
    prefix = f"<!-- claude-fixer-{kind}:"
    for line in body.splitlines():
        if not line.startswith(prefix) or not line.endswith(" -->"):
            continue
        try:
            value = json.loads(line[len(prefix):-4])
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def bot(user: dict, login: str, identity: int) -> bool:
    return user.get("login") == login and user.get("type") == "Bot" and user.get("id") == identity


def findings(threads: list[dict]) -> list[dict]:
    found = []
    for thread in threads:
        if thread.get("isResolved") is not False or thread.get("isOutdated") is not False:
            continue
        roots = (thread.get("comments") or {}).get("nodes") or []
        if not roots:
            continue
        root = roots[0]
        author = root.get("author") or {}
        if author.get("__typename") != "Bot" or author.get("login") not in {"devin-ai-integration", "devin-ai-integration[bot]"}:
            continue
        body = str(root.get("body") or "")
        matches = METADATA_RE.findall(body)
        if len(matches) != 1:
            continue
        try:
            metadata = json.loads(matches[0])
        except ValueError:
            continue
        if not isinstance(metadata, dict) or metadata.get("kind") != "bug":
            continue
        root_id = root.get("databaseId")
        if type(root_id) is int and root_id > 0:
            found.append({"root": root_id, "fingerprint": digest(body), "thread": str(thread.get("id") or "")})
    return sorted(found, key=lambda item: item["root"])


def substantive_followup(comment: dict, permission) -> bool:
    body = str(comment.get("body") or "").strip()
    if not body or records(body, "result"):
        return False
    user = comment.get("user") or {}
    if bot(user, "devin-ai-integration[bot]", DEVIN_ID):
        # Exclude known analysis metadata; a new bug report or nonempty direct
        # Devin follow-up can reopen an explicit handoff, never mark success.
        matches = METADATA_RE.findall(body)
        if matches:
            try:
                return any(json.loads(value).get("kind") == "bug" for value in matches)
            except (ValueError, AttributeError):
                return False
        return not body.startswith("<!--")
    if user.get("type") != "User":
        return False
    login = str(user.get("login") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
        return False
    return permission(login) in {"write", "maintain", "admin"}


def pending_findings(items: list[dict], comments: list[dict], head: str, compare, permission, manual: bool = False) -> list[dict]:
    pending = []
    for item in items:
        replies = [c for c in comments if c.get("in_reply_to_id") == item["root"]]
        terminal = False
        generations = set()
        for reply in replies:
            if not bot(reply.get("user") or {}, "claude[bot]", CLAUDE_ID):
                continue
            for receipt in records(str(reply.get("body") or ""), "result"):
                if type(receipt.get("root")) is not int or receipt.get("root") != item["root"] or receipt.get("fingerprint") != item["fingerprint"]:
                    continue
                base = str(receipt.get("base") or "")
                if not SHA_RE.fullmatch(base):
                    continue
                stamp = str(reply.get("created_at") or "")
                if not stamp:
                    continue
                status = receipt.get("status")
                valid = False
                if status == "addressed":
                    commit = str(receipt.get("commit") or "")
                    valid = bool(SHA_RE.fullmatch(commit)) and compare(base, commit) == "ahead" and compare(commit, head) in {"ahead", "identical"}
                elif status == "needs-human":
                    valid = compare(base, head) in {"ahead", "identical"}
                if not valid:
                    continue
                followups = [c for c in replies if str(c.get("updated_at") or c.get("created_at") or "") > stamp and substantive_followup(c, permission)]
                if followups:
                    generations.update(digest(str((c.get("user") or {}).get("login")) + "\n" + str(c.get("body") or "")) for c in followups)
                    continue
                if status == "addressed" or not manual:
                    terminal = True
        if not terminal:
            pending.append({**item, **({"generation": digest(json.dumps(sorted(generations)))} if generations else {})})
    return pending


def attempt_key(head: str, items: list[dict]) -> dict:
    canonical = json.dumps(sorted(items, key=lambda item: item["root"]), sort_keys=True, separators=(",", ":"))
    return {"head": head, "fingerprint": digest(canonical)}


def attempt_count(comments: list[dict], key: dict) -> int:
    seen = set()
    for comment in comments:
        if not bot(comment.get("user") or {}, "github-actions[bot]", ACTIONS_ID):
            continue
        for record in records(str(comment.get("body") or ""), "attempt"):
            if any(record.get(k) != value for k, value in key.items()):
                continue
            run, attempt = str(record.get("run") or ""), str(record.get("attempt") or "")
            if run.isdigit() and int(run) > 0 and attempt.isdigit() and int(attempt) > 0:
                seen.add((run, attempt))
    return len(seen)


def api(path: str):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def pages(path: str) -> list[dict]:
    result, page = [], 1
    while True:
        batch = api(f"{path}?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("invalid GitHub listing")
        result.extend(batch)
        if len(batch) < 100:
            return result
        page += 1


def post(repo: str, number: str, body: str) -> None:
    subprocess.check_call(["gh", "api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}"], stdout=subprocess.DEVNULL)


def emit(items: list[dict], head: str, reason: str = "") -> None:
    context = []
    for item in items:
        context.append({**item, "base": head})
    ids = [item["root"] for item in items]
    thread_ids = [item["thread"] for item in items]
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"skip={'false' if items else 'true'}\ncomment_ids={json.dumps(ids)}\nthread_ids={json.dumps(thread_ids)}\nreceipt_context={json.dumps(context, separators=(',', ':'))}\nreason={reason}\n")
    print(json.dumps(ids))


def main() -> None:
    repo, number, head = (os.environ[k] for k in ("REPO", "PR_NUMBER", "FIXER_HEAD_SHA"))
    if not SHA_RE.fullmatch(head):
        raise ValueError("unsafe head")
    manual = os.environ.get("MANUAL_RETRY") == "true"
    owner, name = repo.split("/")
    threads, cursor = [], None
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        data = json.loads(subprocess.check_output(args, text=True))
        page = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        threads.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        next_cursor = page["pageInfo"]["endCursor"]
        if not next_cursor or next_cursor == cursor:
            raise ValueError("invalid thread cursor")
        cursor = next_cursor
    comparisons, permissions = {}, {}

    def compare(base, tip):
        key = (base, tip)
        if key not in comparisons:
            comparisons[key] = api(f"repos/{repo}/compare/{base}...{tip}")["status"]
        return comparisons[key]

    def permission(login):
        if login not in permissions:
            permissions[login] = api(f"repos/{repo}/collaborators/{login}/permission")["permission"]
        return permissions[login]

    items = pending_findings(findings(threads), pages(f"repos/{repo}/pulls/{number}/comments"), head, compare, permission, manual)
    if not items:
        emit([], head, "no-pending-findings")
        return
    key = attempt_key(head, items)
    comments = pages(f"repos/{repo}/issues/{number}/comments")
    if not manual and attempt_count(comments, key) >= MAX_ATTEMPTS:
        pause = marker("paused", key)
        if not any(bot(c.get("user") or {}, "github-actions[bot]", ACTIONS_ID) and pause in str(c.get("body") or "") for c in comments):
            post(repo, number, pause + "\nClaude PR Fixer paused after three automatic attempts on this head and unchanged findings. Attempts include runs stopped by workflow validation before model execution. After activation, an authorized manual devin-review-fix workflow dispatch can retry; a new head or changed findings resets this budget. The eight-commit guard still applies.")
        emit([], head, "attempt-limit")
        return
    if not manual:
        run, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
        if not run.isdigit() or not attempt.isdigit():
            raise ValueError("invalid run identity")
        # This is an attempt reservation, not success. Under the shared PR lock,
        # a failed post aborts before any model work and no later step can run.
        post(repo, number, marker("attempt", {**key, "run": run, "attempt": attempt}))
    emit(items, head)


if __name__ == "__main__":
    main()
