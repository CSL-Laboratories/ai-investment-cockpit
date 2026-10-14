#!/usr/bin/env python3
"""Find same-repo open PRs with merge conflicts for the Claude conflict resolver.

Fail closed. Selects a PR only when GitHub reports it CONFLICTING (mergeable
false / mergeable_state dirty), its head is in this repository, its refs are
safe, its head is not a protected branch, it is not labelled needs-decision,
and its author has write/maintain/admin permission. Writes a JSON matrix to
GITHUB_OUTPUT. Import select() from tests.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Callable

WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})
SAFE_REF_RE = re.compile(r"^(?!.*\.\.)(?!/)(?!.*//)[A-Za-z0-9._/-]{1,200}$")
SAFE_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
SAFE_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
PROTECTED_REFS = frozenset({"master", "main", "HEAD"})
SKIP_LABEL = "needs-decision"
MAX_PRS = 3

PermissionLookup = Callable[[str], str]


def is_conflicted(pr: dict[str, Any]) -> bool:
    return pr.get("mergeable") is False and str(pr.get("mergeable_state") or "") == "dirty"


def _ref(value: Any) -> str:
    text = str(value or "")
    return text if SAFE_REF_RE.fullmatch(text) else ""


def select(
    prs: list[dict[str, Any]],
    repository: str,
    permission_lookup: PermissionLookup | None,
    max_prs: int = MAX_PRS,
) -> tuple[list[dict[str, str]], list[str]]:
    """Return (matrix entries, audit lines)."""
    chosen: list[dict[str, str]] = []
    audit: list[str] = []
    cache: dict[str, str] = {}
    for pr in prs:
        number = str(pr.get("number") or "")
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        head_ref = _ref(head.get("ref"))
        base_ref = _ref(base.get("ref"))
        head_sha = str(head.get("sha") or "").lower()
        same_repo = ((head.get("repo") or {}).get("full_name") or "") == repository
        labels = {str((lb or {}).get("name") or "") for lb in pr.get("labels") or []}
        author = str((pr.get("user") or {}).get("login") or "")
        reason = ""
        if str(pr.get("state") or "") != "open":
            reason = "not-open"
        elif not same_repo:
            reason = "fork"
        elif not is_conflicted(pr):
            reason = "not-conflicted"
        elif not head_ref or head_ref in PROTECTED_REFS or not base_ref:
            reason = "unsafe-ref"
        elif not SAFE_SHA_RE.fullmatch(head_sha):
            reason = "unsafe-sha"
        elif SKIP_LABEL in labels:
            reason = "needs-decision"
        elif not SAFE_LOGIN_RE.fullmatch(author):
            reason = "unsafe-login"
        if not reason:
            if author not in cache:
                try:
                    cache[author] = str((permission_lookup or (lambda _l: "none"))(author) or "none")
                except Exception:
                    cache[author] = "error"
            if cache[author] not in WRITE_PERMISSIONS:
                reason = f"author-permission-{cache[author]}"
        if not reason and len(chosen) >= max_prs:
            reason = "over-cap"
        if reason:
            audit.append(f"conflict-scan: pr={number} skip={reason}")
            continue
        audit.append(f"conflict-scan: pr={number} select author_permission={cache[author]}")
        chosen.append(
            {"number": number, "head_ref": head_ref, "head_sha": head_sha, "base_ref": base_ref}
        )
    return chosen, audit


def _gh_json(path: str) -> Any:
    raw = subprocess.check_output(["gh", "api", path], text=True, stderr=subprocess.DEVNULL)
    return json.loads(raw)


def fetch_prs(repository: str, only: str = "") -> list[dict[str, Any]]:
    """Fetch PR details (mergeable is only on the single-PR endpoint)."""
    if only:
        numbers = [only]
    else:
        listing = _gh_json(f"repos/{repository}/pulls?state=open&per_page=50")
        numbers = [str(p.get("number")) for p in listing if isinstance(p, dict)]
    details: list[dict[str, Any]] = []
    for number in numbers:
        if not number.isdigit():
            continue
        pr: dict[str, Any] = {}
        for _attempt in range(4):  # mergeable is computed lazily
            pr = _gh_json(f"repos/{repository}/pulls/{number}")
            if pr.get("mergeable") is not None:
                break
            time.sleep(5)
        details.append(pr)
    return details


def gh_permission(repository: str) -> PermissionLookup:
    def lookup(login: str) -> str:
        data = _gh_json(f"repos/{repository}/collaborators/{login}/permission")
        return str(data.get("permission") or "none") if isinstance(data, dict) else "none"

    return lookup


def emit(chosen: list[dict[str, str]], audit: list[str]) -> None:
    for line in audit:
        print(line)
    lines = [f"count={len(chosen)}", "prs=" + json.dumps(chosen, separators=(",", ":"))]
    for line in lines:
        print(line)
    dest = os.environ.get("GITHUB_OUTPUT")
    if dest:
        with open(dest, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    only = os.environ.get("PR_NUMBER", "").strip()
    if not repository or (only and not only.isdigit()):
        emit([], ["conflict-scan: bad-input"])
        return 0
    try:
        prs = fetch_prs(repository, only)
    except (OSError, ValueError, subprocess.CalledProcessError):
        emit([], ["conflict-scan: fetch-failed"])
        return 0
    chosen, audit = select(prs, repository, gh_permission(repository))
    emit(chosen, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
