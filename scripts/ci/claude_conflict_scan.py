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
MAX_RETRY_REQUESTS = 100
MAX_DETAIL_PRS = 200  # per scan; the window rotates every scan so no PR starves
ROTATION_SECONDS = 1800
MAX_RUN_PAGES = 5
FIXER_EVENTS = frozenset(
    {"issue_comment", "pull_request_review", "pull_request_review_comment"}
)
WORKFLOW_FILE = "claude.yml"

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


def rotate_window(numbers: list[str], epoch: float, size: int = MAX_DETAIL_PRS) -> list[str]:
    """Bounded slice of numbers whose start advances by `size` every ROTATION_SECONDS."""
    if len(numbers) <= size:
        return numbers
    start = (int(epoch // ROTATION_SECONDS) * size) % len(numbers)
    return (numbers[start:] + numbers[:start])[:size]


def fetch_prs(
    repository: str,
    only: str = "",
    get: Callable[[str], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], float] = time.time,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Return (PR details, bases of all open PRs).

    The open listing is always fetched so stacked-branch protection also holds
    for a single-PR dispatch. mergeable is only on the single-PR endpoint and is
    computed lazily: retries share one time budget, never sleep after the last
    attempt, and stop after the deadline or once MAX_RETRY_REQUESTS retries are
    spent. At most MAX_DETAIL_PRS PRs are fetched per scan, from a window that
    rotates with wall-clock time (one step per 30-minute schedule), so the scan
    stays bounded and every PR is reached within a few scans. Pending PRs are
    skipped.
    """
    get = get or _gh_json
    listing = list_open_prs(repository, get)
    bases = open_bases(listing)
    numbers = [only] if only else rotate_window([str(p.get("number")) for p in listing], now())
    deadline = clock() + MERGEABLE_BUDGET_SECONDS
    retries = 0
    details: list[dict[str, Any]] = []
    for number in numbers:
        if not number.isdigit():
            continue
        pr: dict[str, Any] = {}
        for attempt in range(MERGEABLE_ATTEMPTS):
            if attempt:
                retries += 1
            pr = get(f"repos/{repository}/pulls/{number}")
            if not isinstance(pr, dict) or pr.get("mergeable") is not None:
                break
            if (
                attempt == MERGEABLE_ATTEMPTS - 1
                or retries >= MAX_RETRY_REQUESTS
                or clock() + 5 > deadline
            ):
                break
            sleep(5)
        if isinstance(pr, dict):
            details.append(pr)
    return details, bases


def fixer_busy(
    repository: str, current_run_id: str = "", get: Callable[[str], Any] | None = None
) -> bool:
    """True if an event-driven Claude fixer run (comment/review/PR) is queued or running.

    The resolver shares the per-PR claude-<N> concurrency group with the fixers, and
    GitHub keeps only one pending job per group. Not queueing resolvers while any
    fixer run is active means a scheduled resolver can never displace a pending,
    one-shot fixer. Fails closed: a lookup error counts as busy.
    """
    get = get or _gh_json
    try:
        for status in ("queued", "in_progress", "waiting", "pending"):
            for page in range(1, MAX_RUN_PAGES + 1):
                data = get(
                    f"repos/{repository}/actions/workflows/{WORKFLOW_FILE}/runs"
                    f"?status={status}&per_page=100&page={page}"
                )
                runs = data.get("workflow_runs") if isinstance(data, dict) else None
                if not isinstance(runs, list):
                    return True
                for run in runs:
                    if not isinstance(run, dict):
                        continue
                    if str(run.get("id") or "") == current_run_id:
                        continue
                    if str(run.get("event") or "") in FIXER_EVENTS:
                        return True
                if len(runs) < 100:
                    break
            else:
                return True  # more pages than we inspect: fail closed
    except (OSError, ValueError, subprocess.CalledProcessError):
        return True
    return False


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
        if fixer_busy(repository, os.environ.get("GITHUB_RUN_ID", "")):
            emit([], ["conflict-scan: skip=fixer-busy (an event-driven Claude run is active; next scan retries)"])
            return 0
        prs, bases = fetch_prs(repository, only)
    except (OSError, ValueError, subprocess.CalledProcessError):
        emit([], ["conflict-scan: fetch-failed"])
        return 0
    chosen, audit = select(prs, repository, gh_permission(repository), review_bases=bases)
    emit(chosen, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
