#!/usr/bin/env python3
"""Upsert one Linear comment per PR so Grok Bots can see shared-file risk.

Missing LINEAR_API_KEY is a notice, not a failure. Never prints secrets.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

LINEAR_URL = "https://api.linear.app/graphql"
DEVIN_LOGIN = "devin-ai-integration[bot]"
CSL_KEY_RE = re.compile(r"CSL-([0-9]+)", re.IGNORECASE)
MARKER_RE = re.compile(r"<!--\s*pr-sync:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s*-->")
SAFE_PR_RE = re.compile(r"^[1-9][0-9]{0,8}$")
PURPOSE_LINES = 8
MAX_FILES = 12
MERGEABLE_ATTEMPTS = 4
MERGEABLE_SLEEP_SECONDS = 2.0
LINEAR_ISSUE = """
query($id: String!) {
  issue(id: $id) {
    id
    identifier
    comments(first: 50) { nodes { id body } }
  }
}
"""
LINEAR_COMMENT_CREATE = """
mutation($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment { id }
  }
}
"""
LINEAR_COMMENT_UPDATE = """
mutation($id: String!, $body: String!) {
  commentUpdate(id: $id, input: { body: $body }) {
    success
    comment { id }
  }
}
"""


def marker(repo: str, number: int) -> str:
    return f"<!-- pr-sync:{repo}#{number} -->"


def csl_key(*texts: str) -> str:
    for text in texts:
        match = CSL_KEY_RE.search(text or "")
        if match:
            return f"CSL-{match.group(1)}"
    return ""


def purpose_from_body(body: str, limit: int = PURPOSE_LINES) -> str:
    lines: list[str] = []
    for raw in (body or "").splitlines():
        text = raw.strip()
        if not text or text.startswith("<!--"):
            continue
        if text.startswith("#"):
            text = text.lstrip("#").strip()
        if text.lower() in {"summary", "checklist", "test plan"}:
            continue
        lines.append(text)
        if len(lines) >= limit:
            break
    return "\n".join(lines) or "(no PR description)"


def pr_status(pr: dict[str, Any]) -> str:
    if pr.get("merged") or pr.get("merged_at"):
        return "merged"
    if str(pr.get("state") or "").lower() == "closed":
        return "closed"
    if pr.get("draft"):
        return "draft"
    return "open"


def merge_note(pr: dict[str, Any], *, vs_default: bool = False) -> str:
    state = str(pr.get("mergeable_state") or pr.get("mergeableState") or "")
    mergeable = pr.get("mergeable")
    if state == "dirty" or mergeable is False:
        return "conflicts with main" if vs_default else "conflicts"
    if state == "behind":
        return "out of date with main" if vs_default else "behind"
    if vs_default and state == "clean":
        return "up to date with main"
    return state or ("unknown" if mergeable is None else "mergeable")


def devin_reviewed_head(reviews: list[dict[str, Any]], head_sha: str) -> bool:
    head = (head_sha or "").lower()
    if not head:
        return False
    for review in reviews:
        login = str(((review.get("user") or {}).get("login")) or "")
        sha = str(review.get("commit_id") or review.get("commitId") or "").lower()
        state = str(review.get("state") or "").upper()
        aliases = {DEVIN_LOGIN, DEVIN_LOGIN.removesuffix("[bot]")}
        if login in aliases and sha == head and state in {"COMMENTED", "CHANGES_REQUESTED", "APPROVED"}:
            return True
    return False


def top_files(files: list[Any], limit: int = MAX_FILES) -> list[str]:
    names: list[str] = []
    for item in files:
        if isinstance(item, dict):
            name = str(item.get("filename") or item.get("path") or "")
        else:
            name = str(item)
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def render_comment(
    pr: dict[str, Any],
    *,
    repo: str,
    reviews: list[dict[str, Any]],
    files: list[Any],
    vs_default: bool = False,
) -> str:
    number = int(pr.get("number") or 0)
    html = str(pr.get("html_url") or pr.get("url") or f"https://github.com/{repo}/pull/{number}")
    author = str(((pr.get("user") or {}).get("login")) or "")
    head = str(((pr.get("head") or {}).get("sha")) or pr.get("head_sha") or "")
    status = pr_status(pr)
    merge = merge_note(pr, vs_default=vs_default)
    reviewed = "yes" if devin_reviewed_head(reviews, head) else "no"
    file_lines = "\n".join(f"- `{name}`" for name in top_files(files)) or "- (none listed)"
    purpose = purpose_from_body(str(pr.get("body") or ""))
    return (
        f"{marker(repo, number)}\n\n"
        f"Linear-first (CSL-88): work from the CSL Linear issue; check it and its "
        f"PR-sync comment for overlap first; post status and questions there; file "
        f"new findings to Triage; research may use any deep-research source "
        f"the team has, such as Parallel, Exa, Claude Deep Research, Open Deep "
        f"Research, ChatGPT Deep Research or the web. Each run records its "
        f"tool, its run id and its cost (or 'unknown + reason') in its "
        f"csl-research record. All research output goes to "
        f"CSL-Laboratories/csl-research as a submission record, never to this "
        f"repo; post a digest on the Linear issue.\n\n"
        f"**PR:** {html}\n"
        f"**Author:** {author}\n"
        f"**Status:** {status}\n"
        f"**Mergeable:** {merge}\n"
        f"**Head:** `{head}`\n"
        f"**Devin review on head:** {reviewed}\n\n"
        f"**Purpose**\n\n{purpose}\n\n"
        f"**Top changed files**\n\n{file_lines}\n"
    )


class GitHub:
    def __init__(self, run: Callable[..., str] | None = None) -> None:
        self._run = run or _run_gh

    def rest(self, path: str, method: str = "GET") -> Any:
        raw = self._run(["gh", "api", path, "--method", method])
        return json.loads(raw) if raw else None

    def paginate(self, path: str) -> list[Any]:
        raw = self._run(["gh", "api", "--paginate", path])
        text = (raw or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except ValueError:
            items: list[Any] = []
            decoder = json.JSONDecoder()
            idx = 0
            while idx < len(text):
                while idx < len(text) and text[idx].isspace():
                    idx += 1
                if idx >= len(text):
                    break
                obj, end = decoder.raw_decode(text, idx)
                if isinstance(obj, list):
                    items.extend(obj)
                idx = end
            return items


def _run_gh(cmd: list[str], input_text: str | None = None) -> str:
    return subprocess.check_output(cmd, input=input_text, text=True, stderr=subprocess.DEVNULL)


class Linear:
    def __init__(self, api_key: str, post: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.api_key = api_key
        self._post = post or self._http_post

    def _http_post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            LINEAR_URL,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            headers={"Authorization": self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError("linear request failed") from exc
        if payload.get("errors"):
            raise RuntimeError("linear graphql errors")
        return payload.get("data") or {}

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._post(query, variables or {})


def find_sync_comment(comments: list[dict[str, Any]], repo: str, number: int) -> dict[str, Any] | None:
    needle = marker(repo, number)
    for item in comments:
        if needle in str(item.get("body") or ""):
            return item
    return None


def upsert_linear_comment(linear: Linear, issue_key: str, body: str, repo: str, number: int) -> str:
    data = linear.graphql(LINEAR_ISSUE, {"id": issue_key})
    issue = data.get("issue") or {}
    issue_id = str(issue.get("id") or "")
    if not issue_id:
        raise RuntimeError("linear issue missing")
    comments = ((issue.get("comments") or {}).get("nodes")) or []
    existing = find_sync_comment(comments, repo, number)
    if existing:
        linear.graphql(LINEAR_COMMENT_UPDATE, {"id": existing.get("id"), "body": body})
        return "updated"
    linear.graphql(LINEAR_COMMENT_CREATE, {"input": {"issueId": issue_id, "body": body}})
    return "created"


@dataclass
class Config:
    repository: str
    event_name: str
    default_branch: str = "main"
    linear_api_key: str = ""
    pr_number: str = ""


def load_config(env: dict[str, str] | None = None) -> Config:
    src = env if env is not None else os.environ
    return Config(
        repository=src.get("GITHUB_REPOSITORY") or "",
        event_name=src.get("GITHUB_EVENT_NAME") or "",
        default_branch=src.get("DEFAULT_BRANCH") or "main",
        linear_api_key=(src.get("LINEAR_API_KEY") or "").strip(),
        pr_number=(src.get("PR_NUMBER") or "").strip(),
    )


def load_pr(gh: GitHub, repo: str, number: int) -> dict[str, Any]:
    pr = gh.rest(f"repos/{repo}/pulls/{number}")
    return pr if isinstance(pr, dict) else {}


def load_pr_until_mergeable(
    gh: GitHub,
    repo: str,
    number: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = MERGEABLE_ATTEMPTS,
) -> dict[str, Any]:
    pr: dict[str, Any] = {}
    for index in range(max(1, attempts)):
        pr = load_pr(gh, repo, number)
        if pr.get("mergeable") is not None:
            return pr
        if index + 1 < attempts:
            sleep(MERGEABLE_SLEEP_SECONDS)
    return pr


def load_reviews(gh: GitHub, repo: str, number: int) -> list[dict[str, Any]]:
    items = gh.paginate(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    return [i for i in items if isinstance(i, dict)]


def load_files(gh: GitHub, repo: str, number: int) -> list[Any]:
    items = gh.paginate(f"repos/{repo}/pulls/{number}/files?per_page=100")
    return items[:MAX_FILES]


def list_open_pr_numbers(gh: GitHub, repo: str) -> list[int]:
    items = gh.paginate(f"repos/{repo}/pulls?state=open&per_page=100")
    numbers: list[int] = []
    for item in items:
        if isinstance(item, dict) and item.get("number"):
            numbers.append(int(item["number"]))
    return numbers


def sync_one(gh: GitHub, linear: Linear, repo: str, number: int, *, vs_default: bool) -> str:
    pr = load_pr_until_mergeable(gh, repo, number)
    if not pr:
        return f"pr={number} skip=missing"
    head_ref = str(((pr.get("head") or {}).get("ref")) or "")
    key = csl_key(str(pr.get("title") or ""), head_ref)
    if not key:
        return f"pr={number} skip=no-linear-key"
    reviews = load_reviews(gh, repo, number)
    files = load_files(gh, repo, number)
    body = render_comment(pr, repo=repo, reviews=reviews, files=files, vs_default=vs_default)
    action = upsert_linear_comment(linear, key, body, repo, number)
    return f"pr={number} issue={key} {action}"


def run(
    cfg: Config | None = None,
    gh: GitHub | None = None,
    linear: Linear | None = None,
) -> int:
    cfg = cfg or load_config()
    if not cfg.repository:
        print("linear-pr-sync: GITHUB_REPOSITORY missing")
        return 1
    if not cfg.linear_api_key:
        print("linear-pr-sync: notice LINEAR_API_KEY missing; skip")
        return 0
    gh = gh or GitHub()
    linear = linear or Linear(cfg.linear_api_key)
    vs_default = cfg.event_name == "push"
    numbers: list[int] = []
    if cfg.pr_number:
        if not SAFE_PR_RE.fullmatch(cfg.pr_number):
            print("linear-pr-sync: skip unsafe PR_NUMBER")
            return 0
        numbers = [int(cfg.pr_number)]
    elif vs_default:
        try:
            numbers = list_open_pr_numbers(gh, cfg.repository)
        except Exception:
            print("linear-pr-sync: failed to list open PRs")
            return 1
    else:
        print("linear-pr-sync: no PR_NUMBER")
        return 0
    logs: list[str] = []
    for number in numbers:
        try:
            logs.append("linear-pr-sync: " + sync_one(gh, linear, cfg.repository, number, vs_default=vs_default))
        except Exception:
            logs.append(f"linear-pr-sync: pr={number} error")
    for line in logs:
        print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Linear PR sync\n\n```text\n")
            handle.write("\n".join(logs) or "no-op")
            handle.write("\n```\n")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
