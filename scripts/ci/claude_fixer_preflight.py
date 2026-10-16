#!/usr/bin/env python3
"""Recheck current PR/findings under the shared fixer lock. No PR code executed."""
from __future__ import annotations

import json
import os
import re
import subprocess

DEVIN = "devin-ai-integration"
QUERY = """query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$name) { pullRequest(number:$number) {
    reviewThreads(first:100, after:$cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { id isResolved isOutdated comments(first:1) { nodes {
        databaseId author { login __typename } body commit { oid }
      } } }
    }
  } }
}"""


def eligible_comment_ids(threads: list[dict], sha: str) -> list[int]:
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
                or author.get("__typename") != "Bot"
                or (root.get("commit") or {}).get("oid") != sha):
            continue
        match = re.search(r"<!-- devin-review-comment (\{.*?\}) -->", root.get("body", ""))
        if match:
            try:
                if json.loads(match.group(1)).get("kind") != "bug":
                    continue
            except (ValueError, AttributeError):
                continue
        elif "<!-- devin-review-comment" in root.get("body", ""):
            continue  # Malformed metadata is not an actionable finding.
        comment_id = root.get("databaseId")
        if isinstance(comment_id, int) and not isinstance(comment_id, bool) and comment_id > 0:
            findings.append(comment_id)
    return sorted(set(findings))


def api(path: str):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def main() -> None:
    repo, number, sha = (os.environ[k] for k in ("REPO", "PR_NUMBER", "HEAD_SHA"))
    pr = api(f"repos/{repo}/pulls/{number}")
    head = pr.get("head") or {}
    if (pr.get("state") != "open" or pr.get("draft") is not False or head.get("sha") != sha
            or (head.get("repo") or {}).get("full_name") != repo):
        emit("true")
        return
    login = pr["user"]["login"]
    if api(f"repos/{repo}/collaborators/{login}/permission").get("permission") not in {"write", "maintain", "admin"}:
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
    ids = eligible_comment_ids(threads, sha)
    emit("false" if ids else "true", ids)


def emit(skip: str, ids: list[int] | None = None) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"skip={skip}\ncomment_ids={json.dumps(ids or [])}\n")


if __name__ == "__main__":
    main()
