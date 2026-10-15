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
PROTECTED_PREFIXES = ("release/", "releases/", "hotfix/")
SKIP_LABEL = "needs-decision"
MAX_PRS = 3
MAX_PAGES = 10
MERGEABLE_ATTEMPTS = 4
MERGEABLE_BUDGET_SECONDS = 120.0
MAX_DETAIL_REQUESTS = 150

PermissionLookup = Callable[[str], str]


def is_conflicted(pr: dict[str, Any]) -> bool:
    return pr.get("mergeable") is False and str(pr.get("mergeable_state") or "") == "dirty"


def open_bases(prs: list[dict[str, Any]]) -> set[str]:
    """Base branches targeted by any open PR (stacked review branches)."""
    return {
        str(((pr or {}).get("base") or {}).get("ref") or "")
        for pr in prs
        if isinstance(pr, dict) and str(pr.get("state") or "open") == "open"
    }


def is_protected_head(head_ref: str, review_bases: set[str]) -> bool:
    """Default/release branches and any branch another open PR targets are off limits."""
    return (
        head_ref in PROTECTED_REFS
        or head_ref.startswith(PROTECTED_PREFIXES)
        or head_ref in review_bases
    )


def _ref(value: Any) -> str:
    text = str(value or "")
    return text if SAFE_REF_RE.fullmatch(text) else ""


def select(
    prs: list[dict[str, Any]],
    repository: str,
    permission_lookup: PermissionLookup | None,
    max_prs: int = MAX_PRS,
    review_bases: set[str] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Return (matrix entries, audit lines)."""
    chosen: list[dict[str, str]] = []
    audit: list[str] = []
    cache: dict[str, str] = {}
    if review_bases is None:
        review_bases = open_bases(prs)
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
        elif not head_ref or not base_ref:
            reason = "unsafe-ref"
        elif is_protected_head(head_ref, review_bases):
            reason = "protected-head"
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


def list_open_prs(repository: str, get: Callable[[str], Any] | None = None) -> list[dict[str, Any]]:
    """All open PR listing entries, following pagination (bounded by MAX_PAGES x 100)."""
    get = get or _gh_json
    entries: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        listing = get(f"repos/{repository}/pulls?state=open&per_page=100&page={page}")
        if not isinstance(listing, list) or not listing:
            break
        entries.extend(p for p in listing if isinstance(p, dict))
        if len(listing) < 100:
            break
    return entries


def list_open_numbers(repository: str, get: Callable[[str], Any] | None = None) -> list[str]:
    return [str(p.get("number")) for p in list_open_prs(repository, get)]


def fetch_prs(
    repository: str,
    only: str = "",
    get: Callable[[str], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Return (PR details, bases of all open PRs).

    The open listing is always fetched so stacked-branch protection also holds
    for a single-PR dispatch. mergeable is only on the single-PR endpoint and is
    computed lazily: retries share one time budget, never sleep after the last
    attempt, stop after the deadline, and total detail requests are capped so the
    scan always finishes well inside its job timeout. Pending PRs are skipped.
    """
    get = get or _gh_json
    listing = list_open_prs(repository, get)
    bases = open_bases(listing)
    numbers = [only] if only else [str(p.get("number")) for p in listing]
    deadline = clock() + MERGEABLE_BUDGET_SECONDS
    requests = 0
    details: list[dict[str, Any]] = []
    for number in numbers:
        if not number.isdigit():
            continue
        if requests >= MAX_DETAIL_REQUESTS:
            break
        pr: dict[str, Any] = {}
        for attempt in range(MERGEABLE_ATTEMPTS):
            pr = get(f"repos/{repository}/pulls/{number}")
            requests += 1
            if not isinstance(pr, dict) or pr.get("mergeable") is not None:
                break
            if (
                attempt == MERGEABLE_ATTEMPTS - 1
                or requests >= MAX_DETAIL_REQUESTS
                or clock() + 5 > deadline
            ):
                break
            sleep(5)
        if isinstance(pr, dict):
            details.append(pr)
    return details, bases


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
        prs, bases = fetch_prs(repository, only)
    except (OSError, ValueError, subprocess.CalledProcessError):
        emit([], ["conflict-scan: fetch-failed"])
        return 0
    chosen, audit = select(prs, repository, gh_permission(repository), review_bases=bases)
    emit(chosen, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
