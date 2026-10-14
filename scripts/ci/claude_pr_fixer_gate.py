#!/usr/bin/env python3
"""Evaluate Claude PR Fixer event paths. Fail closed. Provider-free.

Reads the GitHub event JSON and optional PR payload. Writes GitHub Actions
outputs when GITHUB_OUTPUT is set. Import evaluate() from tests.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

MEMBER_ASSOC = frozenset({"OWNER", "MEMBER"})
DEVIN_BOT = "devin-ai-integration[bot]"
DEVIN_STATES = frozenset({"commented", "changes_requested"})
MENTION_EVENTS = frozenset({"issue_comment", "pull_request_review_comment"})
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@claude(?![A-Za-z0-9_])")
SAFE_REF_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._/-]+$")
SAFE_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def is_whole_word_mention(text: str | None) -> bool:
    return bool(text) and MENTION_RE.search(text) is not None


def is_member(association: str | None) -> bool:
    return (association or "") in MEMBER_ASSOC


def is_human(user_type: str | None) -> bool:
    return (user_type or "User") != "Bot"


def safe_head_ref(ref: str | None) -> str:
    value = ref or ""
    if not SAFE_REF_RE.fullmatch(value):
        return ""
    return value


def safe_head_sha(sha: str | None) -> str:
    value = (sha or "").lower()
    if not SAFE_SHA_RE.fullmatch(value):
        return ""
    return value


def _head_repo(pr: dict[str, Any] | None) -> str:
    if not pr:
        return ""
    return str(((pr.get("head") or {}).get("repo") or {}).get("full_name") or "")


def evaluate(
    event_name: str,
    event: dict[str, Any],
    repository: str,
    pr: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return mention/devin/same_repo/head_ref/pr_number/reason as strings."""
    payload = pr if pr is not None else (event.get("pull_request") or None)
    issue = event.get("issue") or {}
    if payload is None and issue.get("pull_request"):
        return {
            "mention": "false",
            "devin": "false",
            "same_repo": "false",
            "head_ref": "",
            "head_sha": "",
            "pr_number": str(issue.get("number") or ""),
            "reason": "missing-pr-payload",
        }
    if payload is None:
        return _skip("not-a-pr")

    head = payload.get("head") or {}
    head_ref = safe_head_ref(head.get("ref"))
    head_sha = safe_head_sha(head.get("sha"))
    if not head_ref or not head_sha:
        return _skip("unsafe-or-empty-ref")

    number = str(payload.get("number") or issue.get("number") or "")
    same_repo = _head_repo(payload) == repository
    state = str(payload.get("state") or issue.get("state") or "")
    author_member = is_member(payload.get("author_association"))
    open_same_member = same_repo and state == "open" and author_member

    mention = "false"
    if event_name in MENTION_EVENTS and open_same_member:
        comment = event.get("comment") or {}
        if (
            is_whole_word_mention(comment.get("body"))
            and is_member(comment.get("author_association"))
            and is_human((comment.get("user") or {}).get("type"))
        ):
            mention = "true"

    review = event.get("review") or {}
    devin = "false"
    if (
        event_name == "pull_request_review"
        and open_same_member
        and (review.get("user") or {}).get("login") == DEVIN_BOT
        and str(review.get("state") or "").lower() in DEVIN_STATES
    ):
        devin = "true"

    return {
        "mention": mention,
        "devin": devin,
        "same_repo": "true" if same_repo else "false",
        "head_ref": head_ref,
        "head_sha": head_sha,
        "pr_number": number,
        "reason": "ok" if (mention == "true" or devin == "true" or same_repo) else "fork-or-skip",
    }


def _skip(reason: str) -> dict[str, str]:
    return {
        "mention": "false",
        "devin": "false",
        "same_repo": "false",
        "head_ref": "",
        "head_sha": "",
        "pr_number": "",
        "reason": reason,
    }


def fetch_pr(repository: str, number: int) -> dict[str, Any] | None:
    try:
        raw = subprocess.check_output(
            ["gh", "api", f"repos/{repository}/pulls/{number}"],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_event(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("event JSON must be an object")
    return data


def emit(decision: dict[str, str]) -> None:
    dest = os.environ.get("GITHUB_OUTPUT")
    for key, value in decision.items():
        line = f"{key}={value}"
        print(line)
        if dest:
            with open(dest, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_name or not repository or not event_path:
        emit(_skip("missing-github-env"))
        return 0
    try:
        event = load_event(event_path)
    except (OSError, ValueError, json.JSONDecodeError):
        emit(_skip("bad-event-json"))
        return 0
    pr = event.get("pull_request") if isinstance(event.get("pull_request"), dict) else None
    issue = event.get("issue") if isinstance(event.get("issue"), dict) else None
    if pr is None and issue and issue.get("pull_request"):
        try:
            number = int(issue.get("number"))
        except (TypeError, ValueError):
            emit(_skip("bad-pr-number"))
            return 0
        pr = fetch_pr(repository, number)
        if pr is None:
            emit(_skip("pr-fetch-failed"))
            return 0
    emit(evaluate(event_name, event, repository, pr=pr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
