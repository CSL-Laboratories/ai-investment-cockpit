#!/usr/bin/env python3
"""Auto-resolve PR review threads from a structured Codex per-thread verdict.

Codex (gate #131 / stuck-thread mediator #124) only rules. This workflow
parses the shared marker contract (also used by recursion CSL-178) and, when
safe, resolves the thread. It never posts ``@codex review``, never pushes,
and never merges.

Resolve when every condition holds:

* an API-typed Bot with a trusted Codex login emitted an unedited ``RESOLVE``
  for that GraphQL thread id (missing edit metadata fails closed)
* the marker SHA equals the PR's current head (re-fetched immediately before
  the mutation — never resolve on a stale SHA)
* no owner/human decision is needed (``DECIDE`` / ``MISSING`` / ``AMBIGUOUS``
  / malformed / mixed markers fail closed and leave the needs-decision path)
* the thread root comment has GitHub's Bot actor type (including app logins
  without ``[bot]``). Human-opened threads stay on the needs-decision path.
* no trusted human thread reply at or after the ``RESOLVE`` stamp (the
  thread tail is re-fetched before cite and before ``resolveReviewThread``;
  an incomplete last-40 window fails closed)
* required checks on that head are green (``isRequired`` contexts, ignoring
  this workflow's own in-progress check)

Writes (thread reply + ``resolveReviewThread``) use repo secret
``CODEX_TRIGGER_PAT`` — the same PAT that posts ``@codex review``. GITHUB_TOKEN
is read-only. The PAT identity is what GitHub records as resolving the thread.
A success receipt is posted only when the mutation's ``resolvedBy`` matches
that identity, so a concurrent human resolve is not attributed to the bot.

Missing ``CODEX_TRIGGER_PAT`` is a notice, not a failure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

CODEX_BOTS = frozenset({"chatgpt-codex-connector[bot]", "chatgpt-codex-connector"})
VERDICT_PREFIX = "codex-thread-verdict"
CITE_PREFIX = "codex-thread-cited"
RESOLVED_PREFIX = "codex-thread-resolved"
SKIP_PREFIX = "codex-thread-skipped"
VERDICT_ACTIONS = ("RESOLVE", "DECIDE", "MISSING", "AMBIGUOUS")
SELF_CHECK_NAMES = frozenset({"codex auto-resolve", "resolve"})
LINUX_GATES_NAMES = frozenset({"linux-gates"})
# On retry-resolve GraphQL exceptions, refresh the PAT cite only when it is
# missing or older than 20h. That still lands before the 24h cap window
# closes, without posting a comment every 20-minute cron. The 4h margin
# holds only if cron actually runs in that gap; a stall longer than ~4h
# can still let a retry cite age out of the 24h count.
CITE_REFRESH_AGE = timedelta(hours=20)
# Extra GraphQL pages after the first 80 comments. 100 pages of 50 still
# fail closed if the connection remains truncated.
THREAD_COMMENT_EXTRA_PAGES = 100
# Review-thread connection pages (50 threads each). Remaining truncation
# aborts the PR scan; omitted threads are global verdict sources.
REVIEW_THREAD_PAGES = 100
# Open-PR listing pages (50 PRs each). Remaining truncation logs and
# continues with the PRs already fetched; omitted PRs stay unresolved.
OPEN_PR_PAGES = 10
# Live evidence refresh before writes. These skip reasons abort the rest of
# the PR: an intervening Codex edit or truncated page can hide a veto.
EVIDENCE_ABORT_SKIPS = frozenset({
    "edited-or-unknown-codex-evidence",
    "truncated-comments",
    "truncated-threads",
})
# Over-protect order/risk/audit surfaces. Prefixes cover trees; names
# match any basename (``app/schemas/models/order.py``, ``audit_entry.py``).
PROTECTED_PATH_PREFIXES = (
    "app/risk/",
    "app/brokers/",
    "app/orchestrator/",
    "app/storage/",
    "app/quant/execution/",
)
PROTECTED_BASENAMES = frozenset({"order.py", "audit_entry.py"})
HUMAN_ASSOCIATIONS = frozenset(
    {"OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"}
)
VERDICT_BLOCK_RE = re.compile(
    r"<!--\s*" + VERDICT_PREFIX + r":(?P<tid>[A-Za-z0-9_=-]{1,128})\s*-->"
    r"(?P<body>.*?)(?=<!--\s*" + VERDICT_PREFIX + r":|\Z)",
    re.IGNORECASE | re.DOTALL,
)
VERDICT_ACTION_RE = re.compile(
    r"^CODEX-THREAD:\s*(RESOLVE|DECIDE|MISSING|AMBIGUOUS)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CODEX_THREAD_LINE_RE = re.compile(r"^CODEX-THREAD:\s*(.*)$", re.IGNORECASE)
HEAD_LINE_RE = re.compile(r"^HEAD:\s*(.*)$", re.IGNORECASE)
HTML_RESOLVED_RE = re.compile(
    r"<!--\s*codex-thread-resolved:([A-Za-z0-9_=-]{1,128}):"
    r"([0-9a-fA-F]{40}|[0-9a-fA-F]{64})\s*-->"
)
HTML_CITE_RE = re.compile(
    r"<!--\s*codex-thread-cited:([A-Za-z0-9_=-]{1,128}):"
    r"([0-9a-fA-F]{40}|[0-9a-fA-F]{64})\s*-->"
)
HTML_SKIP_RE = re.compile(
    r"<!--\s*codex-thread-skipped:([A-Za-z0-9_=-]{1,128}):"
    r"([0-9a-fA-F]{40}|[0-9a-fA-F]{64})\s*-->"
)
RESOLVED_AT_RE = re.compile(r"^RESOLVED_AT:\s*(\S+)\s*$", re.MULTILINE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_=-]{1,128}$")
SAFE_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
SAFE_PR_RE = re.compile(r"^[1-9][0-9]{0,8}$")
SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,39}/[A-Za-z0-9._-]{1,80}$")
GATE_LINE_RE = re.compile(r"^CODEX-GATE:\s*(FIX|READY|MEDIATE)$")
PASSING_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})

GITHUB_GRAPHQL_PRS = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $after, states: OPEN) {
      pageInfo { hasNextPage endCursor }
      nodes { number isDraft state url title isCrossRepository }
    }
  }
}
"""
GITHUB_GRAPHQL_PR = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      isDraft
      state
      url
      title
      isCrossRepository
      headRefOid
      reviewThreads(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          resolvedBy { login }
          path
          comments(first: 80) {
            pageInfo { hasNextPage endCursor }
            nodes {
              databaseId
              url
              body
              createdAt
              authorAssociation
              lastEditedAt
              includesCreatedEdit
              author { __typename login }
            }
          }
        }
      }
    }
  }
}
"""
GITHUB_GRAPHQL_VIEWER = """
query {
  viewer { login }
}
"""
GITHUB_GRAPHQL_HEAD = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      isDraft
      state
      isCrossRepository
      headRefOid
    }
  }
}
"""
GITHUB_GRAPHQL_CHECKS = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      isDraft
      state
      isCrossRepository
      headRefOid
      commits(last: 1) {
        nodes {
          commit {
            oid
            statusCheckRollup {
              state
              contexts(first: 80) {
                pageInfo { hasNextPage }
                nodes {
                  __typename
                  ... on CheckRun {
                    name
                    conclusion
                    status
                    isRequired(pullRequestNumber: $number)
                  }
                  ... on StatusContext {
                    context
                    state
                    isRequired(pullRequestNumber: $number)
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""
GITHUB_GRAPHQL_THREAD_COMMENTS = """
query($id: ID!, $cursor: String!) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      comments(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          databaseId
          url
          body
          createdAt
          authorAssociation
          lastEditedAt
          includesCreatedEdit
          author { __typename login }
        }
      }
    }
  }
}
"""
GITHUB_GRAPHQL_THREAD_TAIL = """
query($id: ID!) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      comments(last: 40) {
        pageInfo { hasPreviousPage }
        nodes {
          createdAt
          authorAssociation
          author { __typename login }
        }
      }
    }
  }
}
"""
RESOLVE_THREAD = """
mutation($id: ID!) {
  resolveReviewThread(input: {threadId: $id}) {
    thread { id isResolved resolvedBy { login } }
  }
}
"""
REPLY_THREAD = """
mutation($threadId: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {
    pullRequestReviewThreadId: $threadId
    body: $body
  }) {
    comment { id url }
  }
}
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def format_resolved_at(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_resolved_at(text: str | None) -> datetime | None:
    match = RESOLVED_AT_RE.search(text or "")
    if not match:
        return None
    return parse_dt(match.group(1))


def safe_sha(value: str | None) -> str:
    text = (value or "").strip().lower()
    return text if SAFE_SHA_RE.fullmatch(text) else ""


def safe_thread_id(value: str | None) -> str:
    text = str(value or "").strip()
    return text if SAFE_THREAD_ID_RE.fullmatch(text) else ""


def safe_pr(value: Any) -> str:
    text = str(value or "").strip()
    return text if SAFE_PR_RE.fullmatch(text) else ""


def login_of(node: Any) -> str:
    if isinstance(node, dict):
        author = node.get("author") or node.get("user") or {}
        if isinstance(author, dict):
            return str(author.get("login") or "")
        return str(node.get("login") or "")
    return ""


def body_of(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("body") or "")
    return ""


def login_aliases(login: str) -> set[str]:
    name = (login or "").strip()
    if not name:
        return set()
    aliases = {name, name.lower()}
    if name.endswith("[bot]"):
        aliases.add(name[: -len("[bot]")])
    else:
        aliases.add(f"{name}[bot]")
    return {item.lower() for item in aliases if item}


def is_codex(login: str) -> bool:
    wanted: set[str] = set()
    for item in CODEX_BOTS:
        wanted |= login_aliases(item)
    return bool(login_aliases(login) & wanted)


def is_bot_author(item: dict[str, Any]) -> bool:
    """Require API actor typing; a bot-like login alone is not authority."""
    author = item.get("author")
    if isinstance(author, dict):
        return author.get("__typename") == "Bot" and bool(author.get("login"))
    user = item.get("user")
    return isinstance(user, dict) and user.get("type") == "Bot" and bool(user.get("login"))


def thread_root_is_human(thread: dict[str, Any]) -> bool:
    """True when the thread was opened by a human, or the root cannot be read.

    GraphQL ``comments(first: ...)`` is chronological, so node 0 is the root.
    An empty thread or a root with no login fails closed (do not auto-resolve).
    The PAT login is not treated as a bot here: ``CODEX_TRIGGER_PAT`` may be a
    human founder's account, and that person's own review threads must stay
    on the needs-decision path.
    """
    comments = thread_comments(thread)
    if not comments:
        return True
    login = login_of(comments[0])
    if not login:
        return True
    return not is_bot_author(comments[0])


def is_trusted_human(comment: dict[str, Any], pat_login: str = "") -> bool:
    login = login_of(comment)
    if not login or is_bot_author(comment) or login_matches(login, pat_login):
        return False
    assoc = str(
        comment.get("authorAssociation") or comment.get("author_association") or "OWNER"
    ).upper()
    return assoc in HUMAN_ASSOCIATIONS


def later_human_veto(
    comments: list[dict[str, Any]],
    after: datetime,
    pat_login: str = "",
) -> bool:
    """True when a trusted human commented at or after the Codex RESOLVE stamp."""
    for comment in comments:
        if not is_trusted_human(comment, pat_login):
            continue
        when = parse_dt(comment.get("createdAt") or comment.get("created_at"))
        if when is None or when >= after:
            return True
    return False


def notice(message: str) -> None:
    print(f"::notice::{message}")
    print(f"codex-auto-resolve: {message}")


def log_thread_id(value: str) -> str:
    return safe_thread_id(value) or "unsafe-id"


def sanitize_summary_line(text: str) -> str:
    """Strip characters that can break out of the step-summary fence."""
    cleaned = (text or "").replace("\r", " ").replace("\n", " ")
    return cleaned.replace("```", "").replace("<", "").replace(">", "").replace("`", "")[:400]


def verdict_marker(thread_id: str) -> str:
    tid = safe_thread_id(thread_id)
    if not tid:
        raise ValueError("unsafe thread id")
    return f"<!-- {VERDICT_PREFIX}:{tid} -->"


def resolved_marker(thread_id: str, sha: str) -> str:
    tid = safe_thread_id(thread_id)
    digest = safe_sha(sha)
    if not tid:
        raise ValueError("unsafe thread id")
    if not digest:
        raise ValueError("unsafe sha")
    return f"<!-- {RESOLVED_PREFIX}:{tid}:{digest} -->"


def cite_marker(thread_id: str, sha: str) -> str:
    tid = safe_thread_id(thread_id)
    digest = safe_sha(sha)
    if not tid:
        raise ValueError("unsafe thread id")
    if not digest:
        raise ValueError("unsafe sha")
    return f"<!-- {CITE_PREFIX}:{tid}:{digest} -->"


def skip_marker(thread_id: str, sha: str) -> str:
    tid = safe_thread_id(thread_id)
    digest = safe_sha(sha)
    if not tid:
        raise ValueError("unsafe thread id")
    if not digest:
        raise ValueError("unsafe sha")
    return f"<!-- {SKIP_PREFIX}:{tid}:{digest} -->"


def mediation_verdict_instructions(thread_id: str) -> str:
    """Snippet for #124's ``@codex review`` prompt. Shared with recursion CSL-178."""
    tid = safe_thread_id(thread_id) or "<thread-id>"
    return (
        "End this review with one machine-readable block per thread (not in a "
        "code fence). Copy the thread id exactly. Do **not** call "
        "resolveReviewThread, push, merge, or open a fix PR.\n\n"
        f"{verdict_marker(tid) if safe_thread_id(thread_id) else '<!-- codex-thread-verdict:<thread-id> -->'}\n"
        "CODEX-THREAD: RESOLVE|DECIDE|MISSING|AMBIGUOUS\n"
        "HEAD: <full 40-character commit SHA of the PR head you inspected>\n\n"
        "RESOLVE = the fix is already on that HEAD and no owner/human decision "
        "is needed. DECIDE = an owner/human decision is needed. MISSING = the "
        "fix is not on that HEAD. AMBIGUOUS = you cannot tell. If unsure, AMBIGUOUS."
    )


@dataclass(frozen=True)
class ThreadVerdict:
    thread_id: str
    sha: str
    verdict: str
    reason: str = ""
    source: str = ""


def _fence_indent(line: str) -> tuple[int, str]:
    """Leading spaces before fence content. A tab is indent 4 (not a fence)."""
    n = 0
    for index, char in enumerate(line):
        if char == " ":
            n += 1
            continue
        if char == "\t":
            return 4, line[index + 1 :]
        return n, line[index:]
    return n, ""


def _fence_marker(stripped: str) -> tuple[str, int, str]:
    """Return (character, run length, rest) for a fence line, else empty."""
    if not stripped or stripped[0] not in "`~":
        return "", 0, ""
    char = stripped[0]
    run = 0
    for item in stripped:
        if item != char:
            break
        run += 1
    if run < 3:
        return "", 0, ""
    return char, run, stripped[run:]


def strip_quoted_and_fenced(text: str) -> str:
    """Drop quoted lines and fenced code so echoed verdicts cannot authorize."""
    kept: list[str] = []
    fence_char = ""
    fence_len = 0
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        indent, rest = _fence_indent(line)
        char, run, after = _fence_marker(rest) if indent <= 3 else ("", 0, "")
        if char:
            if not fence_char:
                fence_char = char
                fence_len = run
                continue
            if char == fence_char and run >= fence_len and not after.strip():
                fence_char = ""
                fence_len = 0
                continue
        stripped = line.lstrip()
        if fence_char or stripped.startswith(">") or indent >= 4:
            continue
        kept.append(line)
    return "\n".join(kept)


def parse_thread_verdicts(text: str | None) -> list[ThreadVerdict]:
    """Parse shared CSL-178/CSL-179 blocks. Incomplete companions are AMBIGUOUS."""
    source = strip_quoted_and_fenced(text or "")
    found: list[ThreadVerdict] = []
    for match in VERDICT_BLOCK_RE.finditer(source):
        tid = safe_thread_id(match.group("tid"))
        if not tid:
            continue
        block = match.group("body") or ""
        thread_lines = 0
        head_values: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if CODEX_THREAD_LINE_RE.match(stripped):
                thread_lines += 1
            head_match = HEAD_LINE_RE.match(stripped)
            if head_match:
                head_values.append(head_match.group(1).strip())
        actions = [str(item).upper() for item in VERDICT_ACTION_RE.findall(block)]
        unique_sha = _unique_valid_sha(head_values)
        if not thread_lines:
            found.append(
                ThreadVerdict(tid, unique_sha, "AMBIGUOUS", reason="missing-action", source="block")
            )
            continue
        if thread_lines != len(actions) or len(set(actions)) != 1:
            found.append(
                ThreadVerdict(tid, unique_sha, "AMBIGUOUS", reason="malformed", source="block")
            )
            continue
        if len(head_values) != 1:
            reason = "missing-head" if not head_values else "malformed-head"
            found.append(ThreadVerdict(tid, "", "AMBIGUOUS", reason=reason, source="block"))
            continue
        digest = safe_sha(head_values[0])
        if not digest:
            found.append(ThreadVerdict(tid, "", "AMBIGUOUS", reason="malformed-head", source="block"))
            continue
        found.append(ThreadVerdict(tid, digest, actions[-1], source="block"))
    collapsed: dict[tuple[str, str], ThreadVerdict] = {}
    order: list[tuple[str, str]] = []
    for verdict in found:
        key = (verdict.thread_id, verdict.sha)
        prev = collapsed.get(key)
        if prev is None:
            collapsed[key] = verdict
            order.append(key)
            continue
        if prev.verdict != verdict.verdict:
            collapsed[key] = ThreadVerdict(
                verdict.thread_id, verdict.sha, "AMBIGUOUS", reason="conflict", source="block"
            )
    return [collapsed[key] for key in order]


def _unique_valid_sha(head_values: list[str]) -> str:
    """Preserve a uniquely valid HEAD so malformed AMBIGUOUS sticks to that SHA."""
    found: list[str] = []
    for raw in head_values:
        digest = safe_sha(raw)
        if digest and digest not in found:
            found.append(digest)
    return found[0] if len(found) == 1 else ""


def parse_resolved_markers(text: str | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in HTML_RESOLVED_RE.finditer(text or ""):
        tid = safe_thread_id(match.group(1))
        digest = safe_sha(match.group(2))
        if tid and digest:
            found.append((tid, digest))
    return found


def parse_cite_markers(text: str | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in HTML_CITE_RE.finditer(text or ""):
        tid = safe_thread_id(match.group(1))
        digest = safe_sha(match.group(2))
        if tid and digest:
            found.append((tid, digest))
    return found


def parse_skip_markers(text: str | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in HTML_SKIP_RE.finditer(text or ""):
        tid = safe_thread_id(match.group(1))
        digest = safe_sha(match.group(2))
        if tid and digest:
            found.append((tid, digest))
    return found


def has_gate_verdict_only(text: str | None) -> bool:
    """``CODEX-GATE: READY`` alone is not a per-thread resolve."""
    if parse_thread_verdicts(text):
        return False
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return bool(lines) and GATE_LINE_RE.fullmatch(lines[-1]) is not None


@dataclass(frozen=True)
class StampedVerdict:
    verdict: ThreadVerdict
    when: datetime


def is_unedited(item: dict[str, Any]) -> bool:
    return (
        "lastEditedAt" in item
        and item["lastEditedAt"] is None
        and item.get("includesCreatedEdit") is False
    )


def stamped_verdicts_from(item: dict[str, Any]) -> list[StampedVerdict]:
    if not is_bot_author(item) or not is_codex(login_of(item)):
        return []
    when = parse_dt(item.get("createdAt") or item.get("created_at") or item.get("submittedAt") or item.get("submitted_at"))
    stamp = when or datetime.min.replace(tzinfo=timezone.utc)
    verdicts = parse_thread_verdicts(body_of(item))
    if not is_unedited(item):
        # Do not expose an older RESOLVE by discarding an edited DECIDE.
        # Keep identifiable pairs vetoed, without trusting edited text to act.
        verdicts = [replace(v, verdict="AMBIGUOUS", reason="edited-or-unknown") for v in verdicts]
    return [StampedVerdict(verdict=v, when=stamp) for v in verdicts]


def latest_verdicts(
    items: list[dict[str, Any]],
) -> dict[tuple[str, str], StampedVerdict]:
    """DECIDE / MISSING / AMBIGUOUS stick for that head; a later RESOLVE cannot override."""
    latest: dict[tuple[str, str], StampedVerdict] = {}
    for item in items:
        for stamped in stamped_verdicts_from(item):
            key = (stamped.verdict.thread_id, stamped.verdict.sha)
            prev = latest.get(key)
            if prev is None:
                latest[key] = stamped
                continue
            prev_kind = prev.verdict.verdict
            new_kind = stamped.verdict.verdict
            if prev_kind != "RESOLVE" and new_kind == "RESOLVE":
                continue
            if prev_kind == "RESOLVE" and new_kind != "RESOLVE":
                latest[key] = stamped
                continue
            if stamped.when >= prev.when:
                latest[key] = stamped
    return latest


def check_context_name(node: dict[str, Any]) -> str:
    return str(node.get("name") or node.get("context") or "").strip().lower()


def is_self_check(node: dict[str, Any]) -> bool:
    """Only ignore this job while it is in progress. Never drop a required context."""
    if node.get("isRequired"):
        return False
    if check_context_name(node) not in SELF_CHECK_NAMES:
        return False
    status = str(node.get("status") or node.get("state") or "").upper()
    return status in {"", "IN_PROGRESS", "QUEUED", "WAITING", "PENDING"}


def path_is_protected(path: str) -> bool:
    text = (path or "").replace("\\", "/").lstrip("./")
    if not text:
        return False
    if text.rsplit("/", 1)[-1].lower() in PROTECTED_BASENAMES:
        return True
    for prefix in PROTECTED_PATH_PREFIXES:
        if prefix.endswith("/"):
            if text == prefix[:-1] or text.startswith(prefix):
                return True
        elif text == prefix or text.startswith(prefix + "/"):
            return True
    return False


def linux_gates_success(nodes: list[dict[str, Any]]) -> bool:
    gates = [n for n in nodes if check_context_name(n) in LINUX_GATES_NAMES]
    if not gates:
        return False
    return all(check_run_strict_success(n) for n in gates)


def check_run_strict_success(node: dict[str, Any]) -> bool:
    if "conclusion" not in node and "status" not in node:
        return str(node.get("state") or "").upper() == "SUCCESS"
    status = str(node.get("status") or "").upper()
    conclusion = str(node.get("conclusion") or "").upper()
    if status and status != "COMPLETED":
        return False
    return conclusion == "SUCCESS"


def check_run_ok(node: dict[str, Any]) -> bool:
    if "conclusion" not in node and "status" not in node:
        return str(node.get("state") or "").upper() == "SUCCESS"
    status = str(node.get("status") or "").upper()
    conclusion = str(node.get("conclusion") or "").upper()
    if status and status != "COMPLETED":
        return False
    return conclusion in PASSING_CONCLUSIONS


def required_checks_green(rollup: dict[str, Any] | None) -> tuple[bool, str]:
    """Required contexts must pass. This workflow's own check is ignored."""
    if not isinstance(rollup, dict):
        return False, "checks=missing"
    raw = rollup.get("contexts")
    nodes: list[dict[str, Any]] = []
    has_next = False
    if isinstance(raw, dict):
        nodes = [n for n in (raw.get("nodes") or []) if isinstance(n, dict)]
        has_next = bool((raw.get("pageInfo") or {}).get("hasNextPage"))
    elif isinstance(raw, list):
        nodes = [n for n in raw if isinstance(n, dict)]
    if has_next:
        return False, "checks=truncated"
    nodes = [n for n in nodes if not is_self_check(n)]
    if not linux_gates_success(nodes):
        return False, "checks=linux-gates-not-green"
    required = [n for n in nodes if n.get("isRequired")]
    if required:
        if all(check_run_ok(n) for n in required):
            return True, "checks=green"
        return False, "checks=required-not-green"
    if nodes:
        if all(check_run_ok(n) for n in nodes):
            return True, "checks=green"
        return False, "checks=not-green"
    state = str(rollup.get("state") or "").upper()
    if state == "SUCCESS":
        return True, "checks=green"
    if not state:
        return False, "checks=missing"
    return False, f"checks={state.lower()}"


def sanitize_ruling_excerpt(text: str, limit: int = 240) -> str:
    """Drop HTML comments and receipt prefixes so a copied ruling cannot mint markers."""
    cleaned = HTML_COMMENT_RE.sub("", text or "")
    cleaned = RESOLVED_AT_RE.sub("", cleaned)
    for prefix in (CITE_PREFIX, RESOLVED_PREFIX, SKIP_PREFIX, VERDICT_PREFIX):
        cleaned = cleaned.replace(prefix, "")
    excerpt = " ".join(cleaned.split())
    if limit > 0 and len(excerpt) > limit:
        excerpt = excerpt[: max(limit - 3, 0)] + "..."
    return excerpt


def build_resolve_reply(thread_id: str, sha: str, ruling_excerpt: str = "") -> str:
    digest = safe_sha(sha)
    tid = safe_thread_id(thread_id)
    if not digest or not tid:
        raise ValueError("unsafe reply ids")
    short = digest[:12]
    excerpt = sanitize_ruling_excerpt(ruling_excerpt, 240)
    lines = [
        cite_marker(tid, digest),
        f"Codex ruled the fix is already present on head `{short}` and no "
        f"owner/human decision is needed. Required checks on that head are "
        f"green. Resolving this thread.",
        "",
    ]
    if excerpt:
        lines.extend([f"Ruling excerpt: {excerpt}", ""])
    return "\n".join(lines) + "\n"


def build_success_comment(
    thread_id: str,
    sha: str,
    resolved_at: datetime | None = None,
) -> str:
    digest = safe_sha(sha)
    tid = safe_thread_id(thread_id)
    if not digest or not tid:
        raise ValueError("unsafe success ids")
    short = digest[:12]
    when = resolved_at or utcnow()
    return (
        f"{resolved_marker(tid, digest)}\n"
        f"RESOLVED_AT: {format_resolved_at(when)}\n"
        f"Auto-resolved thread `{tid}` on head `{short}` after an on-thread "
        f"Codex citation.\n"
    )


@dataclass
class Config:
    repository: str
    owner: str
    name: str
    pr_number: str
    max_per_pr_per_day: int = 20
    max_per_run: int = 10
    quote_limit: int = 240
    github_token: str = ""
    codex_pat: str = ""
    pat_login: str = ""
    event_name: str = ""
    event_path: str = ""


def load_config(env: dict[str, str] | None = None) -> Config:
    src = env if env is not None else os.environ

    def get(name: str, default: str = "") -> str:
        value = src.get(name)
        return default if value is None else str(value)

    def as_int(name: str, default: int) -> int:
        raw = get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    repository = get("GITHUB_REPOSITORY")
    owner, name = (repository.split("/", 1) + [""])[:2] if "/" in repository else ("", repository)
    return Config(
        repository=repository,
        owner=owner,
        name=name,
        pr_number=get("PR_NUMBER").strip(),
        max_per_pr_per_day=as_int("MAX_RESOLVES_PER_PR_PER_DAY", 20),
        max_per_run=as_int("MAX_RESOLVES_PER_RUN", 10),
        quote_limit=as_int("QUOTE_LIMIT", 240),
        github_token=get("GH_TOKEN") or get("GITHUB_TOKEN"),
        codex_pat=get("CODEX_TRIGGER_PAT").strip(),
        event_name=get("GITHUB_EVENT_NAME"),
        event_path=get("GITHUB_EVENT_PATH"),
    )


def _run_gh(cmd: list[str], input_text: str | None = None, token: str | None = None) -> str:
    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token
        env.pop("GITHUB_TOKEN", None)
    return subprocess.check_output(cmd, input=input_text, text=True, env=env, stderr=subprocess.DEVNULL)


class GitHub:
    """Thin ``gh`` wrapper. Writes use ``CODEX_TRIGGER_PAT``; reads use GH_TOKEN."""

    def __init__(self, run: Callable[..., str] | None = None) -> None:
        self._run = run or _run_gh

    def graphql(self, query: str, variables: dict[str, Any], token: str | None = None) -> Any:
        raw = self._run(
            ["gh", "api", "graphql", "--input", "-"],
            input_text=json.dumps({"query": query, "variables": variables}),
            token=token,
        )
        payload = json.loads(raw or "{}")
        if payload.get("errors"):
            raise RuntimeError("github graphql errors")
        return payload.get("data") or {}

    def rest(
        self,
        path: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        cmd = ["gh", "api", path, "--method", method]
        raw = self._run(
            cmd + (["--input", "-"] if body is not None else []),
            input_text=json.dumps(body) if body is not None else None,
            token=token,
        )
        if not raw:
            return None
        return json.loads(raw)

    def paginate_rest(self, path: str, token: str | None = None) -> list[Any]:
        raw = self._run(["gh", "api", "--paginate", path], token=token)
        return parse_paginated_json(raw)


def parse_paginated_json(raw: str) -> list[Any]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except ValueError:
        pass
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
            items.extend(item for item in obj if isinstance(item, dict))
        idx = end
    return items


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def cheap_codex_event(event: dict[str, Any], event_name: str) -> bool:
    if event_name == "pull_request_review":
        return is_codex(login_of((event.get("review") or {})))
    if event_name == "pull_request_review_comment":
        return is_codex(login_of((event.get("comment") or {})))
    return False


def same_repo_pull(event: dict[str, Any], repository: str) -> bool:
    """Fail closed when the head repo cannot be verified (no issue_comment path)."""
    pr = event.get("pull_request") if isinstance(event.get("pull_request"), dict) else {}
    if not pr:
        return False
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    full = str(repo.get("full_name") or "")
    if not full:
        return False
    return full == repository


def should_scan_event(cfg: Config, load_event: Callable[[str], dict[str, Any]] | None = None) -> bool:
    if cfg.event_name in {"", "schedule", "workflow_dispatch"}:
        return True
    if cfg.event_name not in {
        "pull_request_review",
        "pull_request_review_comment",
    }:
        return False
    if not cfg.event_path:
        return True
    loader = load_event or _load_json
    try:
        event = loader(cfg.event_path)
    except (OSError, ValueError):
        return False
    if not cheap_codex_event(event, cfg.event_name):
        return False
    return same_repo_pull(event, cfg.repository)


def list_open_prs(gh: GitHub, cfg: Config) -> list[dict[str, Any]]:
    if cfg.pr_number:
        if not SAFE_PR_RE.fullmatch(cfg.pr_number):
            print("codex-auto-resolve: skip unsafe PR_NUMBER")
            return []
        return [{"number": int(cfg.pr_number)}]
    found: list[dict[str, Any]] = []
    after = None
    cursors: set[str] = set()
    truncated = True
    for _ in range(OPEN_PR_PAGES):
        data = gh.graphql(
            GITHUB_GRAPHQL_PRS,
            {"owner": cfg.owner, "name": cfg.name, "after": after},
            token=cfg.github_token,
        )
        conn = ((data.get("repository") or {}).get("pullRequests")) or {}
        nodes = conn.get("nodes")
        if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
            break
        found.extend(nodes)
        page = conn.get("pageInfo") or {}
        if page.get("hasNextPage") is False:
            truncated = False
            break
        after = page.get("endCursor")
        if page.get("hasNextPage") is not True or not isinstance(after, str) or not after or after in cursors:
            break
        cursors.add(after)
    if truncated:
        print("codex-auto-resolve: skip=truncated-open-prs")
    return found


def list_pr(gh: GitHub, cfg: Config, number: int) -> dict[str, Any]:
    threads: list[dict[str, Any]] = []
    after = None
    pr_meta: dict[str, Any] = {}
    cursors: set[str] = set()
    truncated = True
    for _ in range(REVIEW_THREAD_PAGES):
        data = gh.graphql(
            GITHUB_GRAPHQL_PR,
            {"owner": cfg.owner, "name": cfg.name, "number": number, "after": after},
            token=cfg.github_token,
        )
        pr = ((data.get("repository") or {}).get("pullRequest")) or {}
        if not pr_meta:
            pr_meta = pr
        conn = pr.get("reviewThreads") or {}
        nodes = conn.get("nodes")
        if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
            break
        threads.extend(nodes)
        page = conn.get("pageInfo") or {}
        if page.get("hasNextPage") is False:
            truncated = False
            break
        after = page.get("endCursor")
        if page.get("hasNextPage") is not True or not isinstance(after, str) or not after or after in cursors:
            break
        cursors.add(after)
    filled: list[dict[str, Any]] = []
    if not truncated:
        for node in threads:
            filled.append(expand_thread_comments(gh, cfg, node))
    else:
        filled = threads
    pr_meta = dict(pr_meta)
    pr_meta["reviewThreads"] = {
        "nodes": filled,
        "pageInfo": {"hasNextPage": truncated},
    }
    return pr_meta


def expand_thread_comments(gh: GitHub, cfg: Config, node: dict[str, Any]) -> dict[str, Any]:
    conn = node.get("comments") if isinstance(node.get("comments"), dict) else {}
    page = conn.get("pageInfo") or {}
    thread_id = str(node.get("id") or "")
    if not page.get("hasNextPage") or not thread_id:
        return node
    nodes = list(conn.get("nodes") or [])
    cursor = page.get("endCursor")
    cursors: set[str] = set()
    for _ in range(THREAD_COMMENT_EXTRA_PAGES):
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            break
        cursors.add(cursor)
        data = gh.graphql(
            GITHUB_GRAPHQL_THREAD_COMMENTS,
            {"id": thread_id, "cursor": cursor},
            token=cfg.github_token,
        )
        nxt = ((data.get("node") or {}).get("comments")) or {}
        page_nodes = nxt.get("nodes")
        if not isinstance(page_nodes, list) or not all(isinstance(n, dict) for n in page_nodes):
            break
        nodes.extend(page_nodes)
        info = nxt.get("pageInfo") or {}
        if info.get("hasNextPage") is False:
            return {**node, "comments": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        if info.get("hasNextPage") is not True:
            break
        cursor = info.get("endCursor")
    return {**node, "comments": {"nodes": nodes, "pageInfo": {"hasNextPage": True}}}


def fetch_head_sha(gh: GitHub, cfg: Config, number: int) -> str:
    data = gh.graphql(
        GITHUB_GRAPHQL_HEAD,
        {"owner": cfg.owner, "name": cfg.name, "number": number},
        token=cfg.github_token,
    )
    pr = ((data.get("repository") or {}).get("pullRequest")) or {}
    if pr.get("isDraft") or str(pr.get("state") or "OPEN") not in {"OPEN", "open"}:
        return ""
    return safe_sha(pr.get("headRefOid"))


def fetch_head_state(gh: GitHub, cfg: Config, number: int) -> tuple[str, dict[str, Any] | None]:
    data = gh.graphql(
        GITHUB_GRAPHQL_CHECKS,
        {"owner": cfg.owner, "name": cfg.name, "number": number},
        token=cfg.github_token,
    )
    pr = ((data.get("repository") or {}).get("pullRequest")) or {}
    if pr.get("isDraft") or str(pr.get("state") or "OPEN") not in {"OPEN", "open"}:
        return "", None
    if pr.get("isCrossRepository"):
        return "", None
    live = safe_sha(pr.get("headRefOid"))
    commits = ((pr.get("commits") or {}).get("nodes")) or []
    if not commits:
        return live, None
    commit = (commits[-1] or {}).get("commit") or {}
    commit_oid = safe_sha(commit.get("oid"))
    if commit_oid and live and commit_oid != live:
        return live, None
    rollup = commit.get("statusCheckRollup")
    return live, rollup if isinstance(rollup, dict) else None


def list_issue_comments(gh: GitHub, cfg: Config, number: int) -> list[dict[str, Any]]:
    return list_verdict_source(gh, cfg, number, "comments")


def list_reviews(gh: GitHub, cfg: Config, number: int) -> list[dict[str, Any]]:
    return list_verdict_source(gh, cfg, number, "reviews")


def list_verdict_source(
    gh: GitHub, cfg: Config, number: int, source: str,
) -> list[dict[str, Any]]:
    """Read body, author and edit history together, including every page."""
    if source not in {"comments", "reviews"}:
        raise ValueError("invalid verdict source")
    timestamp = "createdAt: submittedAt" if source == "reviews" else "createdAt"
    query = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      SOURCE(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id url body TIMESTAMP lastEditedAt includesCreatedEdit
          author { __typename login }
        }
      }
    }
  }
}
""".replace("SOURCE", source).replace("TIMESTAMP", timestamp)
    items: list[dict[str, Any]] = []
    after = None
    cursors: set[str] = set()
    for _ in range(100):
        data = gh.graphql(query, {
            "owner": cfg.owner, "name": cfg.name, "number": number, "after": after,
        }, token=cfg.github_token)
        connection = (((data.get("repository") or {}).get("pullRequest") or {}).get(source)) or {}
        page = connection.get("pageInfo") or {}
        nodes = connection.get("nodes")
        if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
            raise RuntimeError("incomplete verdict source")
        items.extend(nodes)
        if page.get("hasNextPage") is False:
            return items
        after = page.get("endCursor")
        if page.get("hasNextPage") is not True or not isinstance(after, str) or not after or after in cursors:
            raise RuntimeError("incomplete verdict pagination")
        cursors.add(after)
    raise RuntimeError("verdict pagination limit exceeded")


def thread_comments(thread: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in ((thread.get("comments") or {}).get("nodes") or []) if isinstance(c, dict)]


def thread_comments_truncated(thread: dict[str, Any]) -> bool:
    page = (thread.get("comments") or {}).get("pageInfo") or {}
    return bool(page.get("hasNextPage"))


def review_threads_truncated(pr: dict[str, Any]) -> bool:
    page = (pr.get("reviewThreads") or {}).get("pageInfo") or {}
    return bool(page.get("hasNextPage"))


def all_codex_items(
    threads: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    items.extend(reviews)
    items.extend(issue_comments)
    for thread in threads:
        items.extend(thread_comments(thread))
    return items


def login_matches(left: str, right: str) -> bool:
    return bool(login_aliases(left) & login_aliases(right))


def resolved_by_login(thread: dict[str, Any]) -> str:
    node = thread.get("resolvedBy")
    if isinstance(node, dict):
        return str(node.get("login") or "")
    return ""


def fetch_pat_login(gh: GitHub, cfg: Config) -> str:
    try:
        data = gh.graphql(GITHUB_GRAPHQL_VIEWER, {}, token=cfg.codex_pat)
    except Exception:
        return ""
    viewer = data.get("viewer") if isinstance(data, dict) else None
    if not isinstance(viewer, dict):
        return ""
    return str(viewer.get("login") or "").strip()


def already_resolved_on_sha(
    comments: list[dict[str, Any]],
    thread_id: str,
    sha: str,
    actor_login: str = "",
) -> bool:
    if not actor_login:
        return False
    needle = (thread_id, sha)
    for comment in comments:
        if not login_matches(login_of(comment), actor_login):
            continue
        if needle in parse_resolved_markers(body_of(comment)):
            return True
    return False


def cite_sha_for_thread(
    comments: list[dict[str, Any]],
    thread_id: str,
    actor_login: str = "",
) -> str:
    """Latest PAT cite SHA for ``thread_id`` by ``createdAt``, not list order.

    ``classify_thread`` concatenates thread replies then issue comments, so
    iteration order is not recency. A missing timestamp fails closed as
    newest (cannot prove it is older).
    """
    if not actor_login:
        return ""
    digest = ""
    chosen_when: datetime | None = None
    seen = False
    for comment in comments:
        if not login_matches(login_of(comment), actor_login):
            continue
        stamped = parse_dt(comment.get("createdAt") or comment.get("created_at"))
        for tid, sha in parse_cite_markers(body_of(comment)):
            if tid != thread_id or not sha:
                continue
            if not seen:
                digest = sha
                chosen_when = stamped
                seen = True
                continue
            if chosen_when is None:
                continue
            if stamped is None or stamped >= chosen_when:
                digest = sha
                chosen_when = stamped
    return digest


def already_cited_on_sha(
    comments: list[dict[str, Any]],
    thread_id: str,
    sha: str,
    actor_login: str = "",
) -> bool:
    if not actor_login:
        return False
    needle = (thread_id, sha)
    for comment in comments:
        if not login_matches(login_of(comment), actor_login):
            continue
        if needle in parse_cite_markers(body_of(comment)):
            return True
    return False


def cite_time_for_thread(
    comments: list[dict[str, Any]],
    thread_id: str,
    actor_login: str = "",
    sha: str = "",
) -> datetime | None:
    """Latest PAT cite time for ``thread_id``, optionally pinned to ``sha``.

    Refresh and retry-mark decisions are pair-specific. A cite for another
    head must not hide a near-expired cite for the current SHA.
    """
    tid = safe_thread_id(thread_id)
    digest = safe_sha(sha) if sha else ""
    if not actor_login or not tid:
        return None
    latest: datetime | None = None
    for comment in comments:
        if not login_matches(login_of(comment), actor_login):
            continue
        markers = parse_cite_markers(body_of(comment))
        if not any(
            marker_tid == tid and (not digest or marker_sha == digest)
            for marker_tid, marker_sha in markers
        ):
            continue
        stamped = parse_dt(comment.get("createdAt") or comment.get("created_at"))
        if stamped is None:
            continue
        if latest is None or stamped > latest:
            latest = stamped
    return latest


def unresolved_thread_ids(threads: list[dict[str, Any]]) -> frozenset[str]:
    found: set[str] = set()
    for thread in threads:
        if thread.get("isResolved"):
            continue
        tid = safe_thread_id(str(thread.get("id") or ""))
        if tid:
            found.add(tid)
    return frozenset(found)


def resolved_count_today(
    threads: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    now: datetime,
    actor_login: str = "",
    head_sha: str = "",
) -> int:
    """Count unique PAT-attributed successful resolves in the last 24h.

    Counts:

    * success marker whose ``RESOLVED_AT`` falls in the window (new resolve
      or delayed ``retry-resolve``; marker retries copy the original cite
      time so they do not consume a later day)
    * legacy marker whose comment time and matching PAT cite both fall in
      the window
    * unmarked PAT cite with no receipt when the thread is closed
    * open-thread cite that itself carries ``RESOLVED_AT``
    * receipt-failed same-SHA reopen: PAT ``resolvedBy`` and the cite is
      the unique in-window cite for the current head

    Does not count:

    * failed cites on still-open threads
    * an earlier SHA's unmarked cite inferred from leftover ``resolvedBy``
      (that would count a failed first attempt beside a later receipt)
    * a PAT skip unless a later PAT cite is strictly newer (skip markers
      are posted only for a confirmed non-PAT ``resolvedBy``)

    A missing resolver does not skip (fail closed). A failed skip post
    over-counts. Missing timestamps fail closed.
    """
    if not actor_login:
        return 0
    start = now - timedelta(hours=24)
    pending = unresolved_thread_ids(threads)
    current = safe_sha(head_sha)
    pat_resolved_open: set[str] = set()
    for thread in threads:
        tid = safe_thread_id(str(thread.get("id") or ""))
        if (
            tid
            and tid in pending
            and login_matches(resolved_by_login(thread), actor_login)
        ):
            pat_resolved_open.add(tid)
    all_comments = issue_comments + [c for thread in threads for c in thread_comments(thread)]
    cite_times: dict[tuple[str, str], datetime | None] = {}
    skip_times: dict[tuple[str, str], datetime | None] = {}
    all_marker_pairs: set[tuple[str, str]] = set()
    durable_cites: set[tuple[str, str]] = set()
    markers: list[tuple[str, str, datetime | None, bool]] = []
    for comment in all_comments:
        if not login_matches(login_of(comment), actor_login):
            continue
        stamped = parse_dt(comment.get("createdAt") or comment.get("created_at"))
        body = body_of(comment)
        in_window = stamped is None or stamped >= start
        resolved_at = parse_resolved_at(body)
        for tid, digest in parse_cite_markers(body):
            if not tid or not digest:
                continue
            if in_window:
                _stamp_latest(cite_times, (tid, digest), stamped)
            if resolved_at is not None and (stamped is None or resolved_at >= start):
                durable_cites.add((tid, digest))
        for tid, digest in parse_skip_markers(body):
            if tid and digest and in_window:
                _stamp_latest(skip_times, (tid, digest), stamped)
        for tid, digest in parse_resolved_markers(body):
            if not tid or not digest:
                continue
            all_marker_pairs.add((tid, digest))
            effective = resolved_at or stamped
            markers.append((tid, digest, effective, resolved_at is not None))
    counted: set[tuple[str, str]] = set()
    cite_window = set(cite_times)
    for tid, digest, effective, has_resolved_at in markers:
        if effective is not None and effective < start:
            continue
        if has_resolved_at or (tid, digest) in cite_window:
            counted.add((tid, digest))
    counted |= durable_cites
    cites_by_thread: dict[str, set[str]] = {}
    for tid, digest in cite_times:
        cites_by_thread.setdefault(tid, set()).add(digest)
    unmarked: set[tuple[str, str]] = set()
    for pair, cite_when in cite_times.items():
        tid, digest = pair
        if pair in counted or pair in all_marker_pairs:
            continue
        if tid in pending:
            others = cites_by_thread.get(tid, set()) - {digest}
            same_sha_reopen = current and digest == current and not others
            if tid not in pat_resolved_open or not same_sha_reopen:
                continue
        if pair in skip_times:
            skip_when = skip_times[pair]
            if skip_when is None or cite_when is None or cite_when <= skip_when:
                continue
        unmarked.add(pair)
    return len(counted) + len(unmarked)


def _stamp_latest(
    store: dict[tuple[str, str], datetime | None],
    pair: tuple[str, str],
    stamped: datetime | None,
) -> None:
    if pair not in store:
        store[pair] = stamped
        return
    known = store[pair]
    if known is None:
        return
    if stamped is None or stamped > known:
        store[pair] = stamped


def ruling_excerpt(items: list[dict[str, Any]], thread_id: str, sha: str, limit: int) -> str:
    latest: tuple[datetime, str] | None = None
    for item in items:
        if not is_bot_author(item) or not is_codex(login_of(item)):
            continue
        verdicts = parse_thread_verdicts(body_of(item))
        if not any(v.thread_id == thread_id and v.sha == sha for v in verdicts):
            continue
        when = parse_dt(item.get("createdAt") or item.get("created_at") or item.get("submittedAt") or item.get("submitted_at"))
        stamp = when or datetime.min.replace(tzinfo=timezone.utc)
        if latest is None or stamp >= latest[0]:
            latest = (stamp, body_of(item))
    if latest is None:
        return ""
    return sanitize_ruling_text(latest[1])[:limit]


def sanitize_ruling_text(text: str | None) -> str:
    """Drop HTML comments and ``RESOLVED_AT`` lines before citing a ruling.

    The citation is PAT-authored. Copying a ``codex-thread-resolved`` (or cite)
    marker from Codex would make the next scan trust a forged receipt.
    """
    cleaned = HTML_COMMENT_RE.sub(" ", text or "")
    cleaned = RESOLVED_AT_RE.sub(" ", cleaned)
    return " ".join(cleaned.split())


def newest_for_thread(verdicts: list[StampedVerdict], head_sha: str) -> StampedVerdict:
    """Pick the newest ruling. On a timestamp tie, empty SHA vetoes; else prefer current head."""

    def rank(item: StampedVerdict) -> tuple[datetime, int]:
        sha = item.verdict.sha
        if not sha:
            tie = 2
        elif sha == head_sha:
            tie = 1
        else:
            tie = 0
        return (item.when, tie)

    return max(verdicts, key=rank)


def classify_thread(
    thread: dict[str, Any],
    *,
    head_sha: str,
    latest: dict[tuple[str, str], StampedVerdict],
    issue_comments: list[dict[str, Any]],
    pat_login: str = "",
) -> tuple[str, ThreadVerdict | None]:
    thread_id = safe_thread_id(str(thread.get("id") or ""))
    if not thread_id:
        return "bad-thread-id", None
    comments = thread_comments(thread) + issue_comments
    # Intentional limit: a thread resolved on one head is never reopened
    # when the head moves, even if the cited fix later regresses. A human
    # (or a new review comment) has to reopen it. If we already cited and
    # resolved but the audit marker never landed, retry the marker only
    # when GitHub attributes the resolve to the PAT identity.
    if thread.get("isResolved"):
        cite_sha = cite_sha_for_thread(comments, thread_id, actor_login=pat_login)
        if cite_sha and already_resolved_on_sha(
            comments, thread_id, cite_sha, actor_login=pat_login
        ):
            return "already-resolved", None
        if pat_login and login_matches(resolved_by_login(thread), pat_login) and cite_sha:
            return "retry-mark", ThreadVerdict(thread_id, cite_sha, "RESOLVE")
        return "already-resolved", None
    if already_resolved_on_sha(comments, thread_id, head_sha, actor_login=pat_login):
        return "already-marked", None
    if path_is_protected(str(thread.get("path") or "")):
        return "protected-path", None
    any_for_thread = [s for (tid, _sha), s in latest.items() if tid == thread_id]
    if not any_for_thread:
        return "no-verdict", None
    newest = newest_for_thread(any_for_thread, head_sha)
    stamped = latest.get((thread_id, head_sha))
    if newest.verdict.sha != head_sha:
        if not newest.verdict.sha:
            return "ambiguous", newest.verdict
        return "stale-sha", None
    if stamped is None:
        return "no-verdict", None
    if stamped.verdict.verdict != "RESOLVE":
        return stamped.verdict.verdict.lower(), stamped.verdict
    if thread_root_is_human(thread):
        return "human-root", stamped.verdict
    comments = thread_comments(thread)
    if later_human_veto(comments, stamped.when, pat_login):
        return "human-veto", stamped.verdict
    if already_cited_on_sha(comments, thread_id, head_sha, actor_login=pat_login):
        return "retry-resolve", stamped.verdict
    return "resolve", stamped.verdict


def thread_tail_vetoes(
    gh: GitHub,
    cfg: Config,
    *,
    thread_id: str,
    after: datetime | None,
    pat_login: str,
) -> bool:
    """True when the live tail has a later trusted human reply, or the recheck cannot run."""
    if after is None:
        return True
    try:
        tail = gh.graphql(
            GITHUB_GRAPHQL_THREAD_TAIL,
            {"id": thread_id},
            token=cfg.github_token or cfg.codex_pat,
        )
    except Exception:
        return True
    node = tail.get("node") if isinstance(tail, dict) else None
    comments: list[dict[str, Any]] = []
    page_info: dict[str, Any] | None = None
    if isinstance(node, dict):
        conn = node.get("comments") or {}
        if isinstance(conn, dict):
            raw_page = conn.get("pageInfo")
            if isinstance(raw_page, dict):
                page_info = raw_page
            comments = [
                item for item in (conn.get("nodes") or []) if isinstance(item, dict)
            ]
    if later_human_veto(comments, after, pat_login):
        return True
    # last:40 is a suffix. hasPreviousPage (or a missing pageInfo on a
    # full 40-comment page) means older comments are unseen. Fail closed
    # unless a visible stamp is strictly before the verdict, so the
    # contiguous suffix covers the whole at-or-after window — including
    # same-second replies GitHub would order before this page.
    if page_info is None or "hasPreviousPage" not in page_info:
        has_previous = None
    else:
        has_previous = bool(page_info.get("hasPreviousPage"))
    truncated = has_previous is True or (has_previous is None and len(comments) >= 40)
    if not truncated:
        return False
    oldest: datetime | None = None
    for item in comments:
        when = parse_dt(item.get("createdAt") or item.get("created_at"))
        if when is None:
            return True
        if oldest is None or when < oldest:
            oldest = when
    return oldest is None or oldest >= after


def live_evidence_state(
    gh: GitHub,
    cfg: Config,
    number: int,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Refetch threads and verdict sources. Skip is empty when evidence is complete and unedited."""
    pr = list_pr(gh, cfg, number)
    if review_threads_truncated(pr):
        return "truncated-threads", {}, [], [], []
    threads = [
        n for n in ((pr.get("reviewThreads") or {}).get("nodes") or []) if isinstance(n, dict)
    ]
    if any(thread_comments_truncated(thread) for thread in threads):
        return "truncated-comments", {}, [], [], []
    issue_comments = list_issue_comments(gh, cfg, number)
    reviews = list_reviews(gh, cfg, number)
    items = all_codex_items(threads, issue_comments, reviews)
    if any(
        is_bot_author(item) and is_codex(login_of(item)) and not is_unedited(item)
        for item in items
    ):
        return "edited-or-unknown-codex-evidence", {}, [], [], []
    return "", pr, threads, issue_comments, reviews


def live_resolve_skip(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    sha: str,
    pat_login: str,
) -> str:
    """Empty if live evidence still authorizes this resolve; otherwise a skip."""
    skip, pr, threads, issue_comments, reviews = live_evidence_state(gh, cfg, number)
    if skip:
        return skip
    live_head = safe_sha(pr.get("headRefOid"))
    if not live_head or live_head != sha:
        return "stale-sha"
    if pr.get("isDraft") or str(pr.get("state") or "OPEN") not in {"OPEN", "open"}:
        return "closed-or-draft"
    if pr.get("isCrossRepository"):
        return "fork"
    items = all_codex_items(threads, issue_comments, reviews)
    latest = latest_verdicts(items)
    live_thread = next((item for item in threads if str(item.get("id") or "") == thread_id), None)
    if live_thread is None:
        return "no-verdict"
    reason, _verdict = classify_thread(
        live_thread,
        head_sha=sha,
        latest=latest,
        issue_comments=issue_comments,
        pat_login=pat_login,
    )
    if reason not in {"resolve", "retry-resolve"}:
        return reason
    return ""


def reply_and_resolve(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    sha: str,
    excerpt: str,
    retry: bool = False,
    now: datetime | None = None,
    pat_login: str = "",
    after: datetime | None = None,
    cite_when: datetime | None = None,
) -> str:
    live, rollup = fetch_head_state(gh, cfg, number)
    if not live or live != sha:
        return "stale-sha"
    ok, _reason = required_checks_green(rollup)
    if not ok:
        return _reason
    actor = pat_login or cfg.pat_login
    if thread_tail_vetoes(
        gh, cfg, thread_id=thread_id, after=after, pat_login=actor
    ):
        return "human-veto"
    blocked = live_resolve_skip(
        gh, cfg, number=number, thread_id=thread_id, sha=sha, pat_login=actor or "",
    )
    if blocked:
        return blocked
    if not retry:
        try:
            gh.graphql(
                REPLY_THREAD,
                {
                    "threadId": thread_id,
                    "body": build_resolve_reply(thread_id, sha, excerpt),
                },
                token=cfg.codex_pat,
            )
        except Exception:
            return "reply-error"
        live, rollup = fetch_head_state(gh, cfg, number)
        if live != sha:
            return "stale-sha"
        ok, _reason = required_checks_green(rollup)
        if not ok:
            return _reason
        if thread_tail_vetoes(
            gh, cfg, thread_id=thread_id, after=after, pat_login=actor
        ):
            return "human-veto"
        blocked = live_resolve_skip(
            gh, cfg, number=number, thread_id=thread_id, sha=sha, pat_login=actor or "",
        )
        if blocked:
            return blocked
    live, rollup = fetch_head_state(gh, cfg, number)
    if not live or live != sha:
        return "stale-sha"
    ok, _reason = required_checks_green(rollup)
    if not ok:
        return _reason
    try:
        resolved = gh.graphql(
            RESOLVE_THREAD,
            {"id": thread_id},
            token=cfg.codex_pat,
        )
    except Exception:
        # GitHub may have applied the mutation even though the client
        # saw an error. Refresh a retry-resolve cite when it is missing
        # or older than 20h so it still counts after the next scan.
        # A younger cite is left alone — posting every cron would spam
        # an open thread. First-time resolve already cited this scan.
        # Determinate failures (mismatched id / still-open) do not refresh.
        stamp = now or utcnow()
        still_counts = cite_when is not None and cite_when >= stamp - CITE_REFRESH_AGE
        if retry and not still_counts:
            publish_cite_marker(gh, cfg, number=number, thread_id=thread_id, sha=sha)
        return "resolve-error"
    thread = ((resolved.get("resolveReviewThread") or {}).get("thread")) or {}
    echoed = str(thread.get("id") or "")
    if echoed != thread_id:
        return "resolve-error"
    if thread.get("isResolved") is False:
        return "resolve-error"
    # resolveReviewThread is a successful no-op on an already-resolved
    # thread. A human who won the race would otherwise get a PAT success
    # receipt (and a cap charge). Fail closed unless GitHub attributes
    # the resolve to the PAT identity.
    actor = pat_login or cfg.pat_login
    resolver = resolved_by_login(thread)
    if resolver and actor and not login_matches(resolver, actor):
        publish_skip_marker(gh, cfg, number=number, thread_id=thread_id, sha=sha)
        return "already-resolved"
    if not actor or not login_matches(resolver, actor):
        # Missing resolvedBy cannot be distinguished from a PAT success.
        # Do not post a skip (that would exempt the pair from the cap).
        # Refresh the cite so a retry older than 24h still lands in today's
        # unmarked window instead of disappearing from the cap.
        publish_cite_marker(
            gh,
            cfg,
            number=number,
            thread_id=thread_id,
            sha=sha,
            resolved_at=now or utcnow(),
        )
        return "posted-unmarked"
    stamp = now or utcnow()
    if publish_resolved_marker(
        gh,
        cfg,
        number=number,
        thread_id=thread_id,
        sha=sha,
        resolved_at=stamp,
    ):
        return "posted"
    publish_cite_marker(
        gh, cfg, number=number, thread_id=thread_id, sha=sha, resolved_at=stamp
    )
    return "posted-unmarked"


def publish_cite_marker(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    sha: str,
    resolved_at: datetime | None = None,
) -> bool:
    """Refresh the PAT cite so it outranks an earlier skip on this pair."""
    try:
        body = cite_marker(thread_id, sha)
    except ValueError:
        return False
    if resolved_at is not None:
        body = f"{body}\nRESOLVED_AT: {format_resolved_at(resolved_at)}\n"
    return _post_audit_comment(gh, cfg, number=number, thread_id=thread_id, body=body)


def publish_skip_marker(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    sha: str,
) -> bool:
    """Post ``codex-thread-skipped`` so a human-raced cite does not fill the cap."""
    try:
        body = skip_marker(thread_id, sha)
    except ValueError:
        return False
    return _post_audit_comment(gh, cfg, number=number, thread_id=thread_id, body=body)


def _post_audit_comment(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    body: str,
) -> bool:
    try:
        gh.rest(
            f"repos/{cfg.repository}/issues/{number}/comments",
            method="POST",
            body={"body": body},
            token=cfg.codex_pat,
        )
        return True
    except Exception:
        pass
    try:
        gh.graphql(
            REPLY_THREAD,
            {"threadId": thread_id, "body": body},
            token=cfg.codex_pat,
        )
        return True
    except Exception:
        return False


def publish_resolved_marker(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    sha: str,
    resolved_at: datetime | None = None,
) -> bool:
    """Post ``codex-thread-resolved``. False if both issue and thread paths fail."""
    body = build_success_comment(thread_id, sha, resolved_at)
    return _post_audit_comment(gh, cfg, number=number, thread_id=thread_id, body=body)


def scan_pr(
    pr: dict[str, Any],
    cfg: Config,
    gh: GitHub,
    now: datetime,
    remaining_run: list[int] | None = None,
) -> list[str]:
    logs: list[str] = []
    number = int(pr.get("number") or 0)
    if not number:
        return logs
    remaining = remaining_run if remaining_run is not None else [cfg.max_per_run]
    if "reviewThreads" not in pr or "headRefOid" not in pr:
        pr = list_pr(gh, cfg, number)
    if pr.get("isDraft") or str(pr.get("state") or "OPEN") not in {"OPEN", "open"}:
        logs.append(f"codex-auto-resolve: pr={number} skip=closed-or-draft")
        return logs
    if pr.get("isCrossRepository"):
        logs.append(f"codex-auto-resolve: pr={number} skip=fork")
        return logs
    head_sha = safe_sha(pr.get("headRefOid"))
    if not head_sha:
        logs.append(f"codex-auto-resolve: pr={number} skip=no-head")
        return logs
    live, rollup = fetch_head_state(gh, cfg, number)
    if not live:
        logs.append(f"codex-auto-resolve: pr={number} skip=closed-or-draft")
        return logs
    if live != head_sha:
        logs.append(f"codex-auto-resolve: pr={number} skip=stale-sha")
        return logs
    checks_ok, checks_reason = required_checks_green(rollup)
    # Omitted review threads are global verdict sources. A truncated
    # connection can hide a DECIDE/AMBIGUOUS/edited Codex item, so do not
    # resolve visible threads from a partial thread list.
    if review_threads_truncated(pr):
        logs.append(f"codex-auto-resolve: pr={number} skip=truncated-threads")
        return logs
    threads = [n for n in ((pr.get("reviewThreads") or {}).get("nodes") or []) if isinstance(n, dict)]
    # Thread comments are global verdict sources. A truncated connection can
    # hide a DECIDE/AMBIGUOUS/edited Codex item for another thread, so do not
    # resolve later complete threads from a partial evidence set.
    if any(thread_comments_truncated(thread) for thread in threads):
        logs.append(f"codex-auto-resolve: pr={number} skip=truncated-comments")
        return logs
    issue_comments = list_issue_comments(gh, cfg, number)
    reviews = list_reviews(gh, cfg, number)
    items = all_codex_items(threads, issue_comments, reviews)
    # An edit can erase or redirect an earlier DECIDE's thread/head marker.
    # Current text cannot prove the original veto scope, so pause the entire
    # PR before any writes, including receipt retries. Human mediation remains.
    if any(
        is_bot_author(item) and is_codex(login_of(item)) and not is_unedited(item)
        for item in items
    ):
        logs.append(f"codex-auto-resolve: pr={number} skip=edited-or-unknown-codex-evidence")
        return logs
    latest = latest_verdicts(items)
    if not cfg.codex_pat:
        logs.append(f"codex-auto-resolve: pr={number} skip=no-secret")
        return logs
    pat_login = cfg.pat_login or fetch_pat_login(gh, cfg)
    if not pat_login:
        logs.append(f"codex-auto-resolve: pr={number} skip=no-pat-login")
        return logs
    if not cfg.pat_login:
        cfg = replace(cfg, pat_login=pat_login)
    posted_today = resolved_count_today(
        threads,
        issue_comments,
        now,
        actor_login=pat_login,
        head_sha=head_sha,
    )
    abort_scan = False
    for thread in threads:
        tid = log_thread_id(str(thread.get("id") or ""))
        if abort_scan:
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip=abort-scan")
            continue
        reason, verdict = classify_thread(
            thread,
            head_sha=head_sha,
            latest=latest,
            issue_comments=issue_comments,
            pat_login=pat_login,
        )
        if reason == "retry-mark":
            blocked, _pr, live_threads, live_issue, live_reviews = live_evidence_state(
                gh, cfg, number
            )
            if blocked:
                if blocked in EVIDENCE_ABORT_SKIPS:
                    abort_scan = True
                logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={blocked}")
                continue
            live_id = str(thread.get("id") or "")
            live_thread = next(
                (item for item in live_threads if str(item.get("id") or "") == live_id),
                None,
            )
            if live_thread is None:
                logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip=no-verdict")
                continue
            live_latest = latest_verdicts(all_codex_items(live_threads, live_issue, live_reviews))
            live_reason, live_verdict = classify_thread(
                live_thread,
                head_sha=head_sha,
                latest=live_latest,
                issue_comments=live_issue,
                pat_login=pat_login,
            )
            if live_reason != "retry-mark":
                logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={live_reason}")
                continue
            mark_sha = (live_verdict.sha if live_verdict else "") or head_sha
            comments = thread_comments(live_thread) + live_issue
            cite_when = cite_time_for_thread(
                comments,
                live_id,
                actor_login=pat_login,
                sha=mark_sha,
            )
            try:
                marked = publish_resolved_marker(
                    gh,
                    cfg,
                    number=number,
                    thread_id=str(thread.get("id") or ""),
                    sha=mark_sha,
                    resolved_at=cite_when,
                )
            except Exception:
                marked = False
            if marked:
                logs.append(
                    f"codex-auto-resolve: pr={number} thread={tid} retry-mark=posted sha={mark_sha}"
                )
            else:
                logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip=posted-unmarked")
            continue
        if reason not in {"resolve", "retry-resolve"}:
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={reason}")
            continue
        if not checks_ok:
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={checks_reason}")
            continue
        if posted_today >= cfg.max_per_pr_per_day or remaining[0] <= 0:
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip=over-cap")
            continue
        excerpt = ruling_excerpt(items, verdict.thread_id if verdict else tid, head_sha, cfg.quote_limit)
        stamped = latest.get((str(thread.get("id") or ""), head_sha))
        try:
            result = reply_and_resolve(
                gh,
                cfg,
                number=number,
                thread_id=str(thread.get("id") or ""),
                sha=head_sha,
                excerpt=excerpt,
                retry=reason == "retry-resolve",
                now=now,
                pat_login=pat_login,
                after=stamped.when if stamped is not None else None,
                cite_when=cite_time_for_thread(
                    thread_comments(thread) + issue_comments,
                    str(thread.get("id") or ""),
                    actor_login=pat_login,
                    sha=head_sha,
                ),
            )
        except Exception:
            result = "error"
        if result == "error":
            abort_scan = True
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip=error")
            continue
        if result in EVIDENCE_ABORT_SKIPS:
            abort_scan = True
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={result}")
            continue
        if result in {"posted", "posted-unmarked", "resolve-error"}:
            posted_today += 1
            remaining[0] -= 1
            if result == "resolve-error":
                abort_scan = True
                logs.append(
                    f"codex-auto-resolve: pr={number} thread={tid} skip=resolve-error sha={head_sha}"
                )
            else:
                logs.append(
                    f"codex-auto-resolve: pr={number} thread={tid} resolve={result} sha={head_sha}"
                )
        else:
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={result}")
    return logs


def run(
    cfg: Config | None = None,
    gh: GitHub | None = None,
    now: datetime | None = None,
    load_event: Callable[[str], dict[str, Any]] | None = None,
) -> int:
    cfg = cfg or load_config()
    now = now or utcnow()
    if not cfg.repository or not cfg.owner or not cfg.name:
        print("codex-auto-resolve: GITHUB_REPOSITORY missing")
        return 1
    if not SAFE_REPO_RE.fullmatch(cfg.repository):
        print("codex-auto-resolve: unsafe GITHUB_REPOSITORY")
        return 1
    if not should_scan_event(cfg, load_event=load_event):
        print("codex-auto-resolve: skip event filter")
        return 0
    if not cfg.codex_pat:
        notice("CODEX_TRIGGER_PAT is not configured; skipping auto-resolve.")
        return 0
    gh = gh or GitHub()
    if not cfg.pat_login:
        cfg = replace(cfg, pat_login=fetch_pat_login(gh, cfg))
    if not cfg.pat_login:
        notice("CODEX_TRIGGER_PAT viewer login unavailable; skipping auto-resolve.")
        return 0
    try:
        prs = list_open_prs(gh, cfg)
    except Exception:
        print("codex-auto-resolve: failed to list pull requests")
        return 1
    logs: list[str] = []
    remaining_run = [cfg.max_per_run]
    for pr in prs:
        try:
            logs.extend(scan_pr(pr, cfg, gh, now, remaining_run=remaining_run))
        except Exception:
            logs.append(f"codex-auto-resolve: pr={pr.get('number')} error")
    for line in logs:
        print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        safe_logs = [sanitize_summary_line(line) for line in logs]
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Codex auto-resolve\n\n```text\n")
            handle.write("\n".join(safe_logs) or "no-op")
            handle.write("\n```\n")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
