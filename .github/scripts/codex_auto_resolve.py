#!/usr/bin/env python3
"""Auto-resolve PR review threads from a structured Codex per-thread verdict.

Codex (gate #131 / stuck-thread mediator #124) only rules. This workflow
parses the shared marker contract (also used by recursion CSL-178) and, when
safe, resolves the thread. It never posts ``@codex review``, never pushes,
and never merges.

Resolve when every condition holds:

* a trusted Codex login emitted ``RESOLVE`` for that GraphQL thread id
* the marker SHA equals the PR's current head (re-fetched immediately before
  the mutation — never resolve on a stale SHA)
* no owner/human decision is needed (``DECIDE`` / ``MISSING`` / ``AMBIGUOUS``
  / malformed / mixed markers fail closed and leave the needs-decision path)
* required checks on that head are green (``isRequired`` contexts, ignoring
  this workflow's own in-progress check)

Writes (thread reply + ``resolveReviewThread``) use repo secret
``CODEX_TRIGGER_PAT`` — the same PAT that posts ``@codex review``. GITHUB_TOKEN
is read-only. The PAT identity is what GitHub records as resolving the thread.

Missing ``CODEX_TRIGGER_PAT`` is a notice, not a failure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

CODEX_BOTS = frozenset({"chatgpt-codex-connector[bot]", "chatgpt-codex-connector"})
VERDICT_PREFIX = "codex-thread-verdict"
CITE_PREFIX = "codex-thread-cited"
RESOLVED_PREFIX = "codex-thread-resolved"
VERDICT_ACTIONS = ("RESOLVE", "DECIDE", "MISSING", "AMBIGUOUS")
SELF_CHECK_NAMES = frozenset({"codex auto-resolve", "resolve"})
LINUX_GATES_NAMES = frozenset({"linux-gates"})
PROTECTED_PATH_PREFIXES = (
    "app/risk/",
    "app/brokers/",
    "app/orchestrator/",
    "app/storage/audit.py",
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
          path
          comments(first: 80) {
            pageInfo { hasNextPage endCursor }
            nodes {
              databaseId
              url
              body
              createdAt
              author { login }
            }
          }
        }
      }
    }
  }
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
          author { login }
        }
      }
    }
  }
}
"""
RESOLVE_THREAD = """
mutation($id: ID!) {
  resolveReviewThread(input: {threadId: $id}) {
    thread { id isResolved }
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


def strip_quoted_and_fenced(text: str) -> str:
    """Drop quoted lines and fenced code so echoed verdicts cannot authorize."""
    kept: list[str] = []
    in_fence = False
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or stripped.startswith(">"):
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
        if not thread_lines:
            found.append(ThreadVerdict(tid, "", "AMBIGUOUS", reason="missing-action", source="block"))
            continue
        if thread_lines != len(actions) or len(set(actions)) != 1:
            found.append(ThreadVerdict(tid, "", "AMBIGUOUS", reason="malformed", source="block"))
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


def stamped_verdicts_from(item: dict[str, Any]) -> list[StampedVerdict]:
    if not is_codex(login_of(item)):
        return []
    when = parse_dt(item.get("createdAt") or item.get("created_at") or item.get("submittedAt") or item.get("submitted_at"))
    stamp = when or datetime.min.replace(tzinfo=timezone.utc)
    return [StampedVerdict(verdict=v, when=stamp) for v in parse_thread_verdicts(body_of(item))]


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


def build_resolve_reply(thread_id: str, sha: str, ruling_excerpt: str = "") -> str:
    digest = safe_sha(sha)
    tid = safe_thread_id(thread_id)
    if not digest or not tid:
        raise ValueError("unsafe reply ids")
    short = digest[:12]
    excerpt = " ".join((ruling_excerpt or "").split())
    if len(excerpt) > 240:
        excerpt = excerpt[:237] + "..."
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


def build_success_comment(thread_id: str, sha: str) -> str:
    digest = safe_sha(sha)
    tid = safe_thread_id(thread_id)
    if not digest or not tid:
        raise ValueError("unsafe success ids")
    short = digest[:12]
    return (
        f"{resolved_marker(tid, digest)}\n"
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
    for _ in range(10):
        data = gh.graphql(
            GITHUB_GRAPHQL_PRS,
            {"owner": cfg.owner, "name": cfg.name, "after": after},
            token=cfg.github_token,
        )
        conn = ((data.get("repository") or {}).get("pullRequests")) or {}
        for node in conn.get("nodes") or []:
            if isinstance(node, dict):
                found.append(node)
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    return found


def list_pr(gh: GitHub, cfg: Config, number: int) -> dict[str, Any]:
    threads: list[dict[str, Any]] = []
    after = None
    pr_meta: dict[str, Any] = {}
    for _ in range(10):
        data = gh.graphql(
            GITHUB_GRAPHQL_PR,
            {"owner": cfg.owner, "name": cfg.name, "number": number, "after": after},
            token=cfg.github_token,
        )
        pr = ((data.get("repository") or {}).get("pullRequest")) or {}
        if not pr_meta:
            pr_meta = pr
        conn = pr.get("reviewThreads") or {}
        threads.extend(n for n in (conn.get("nodes") or []) if isinstance(n, dict))
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    filled: list[dict[str, Any]] = []
    for node in threads:
        filled.append(expand_thread_comments(gh, cfg, node))
    pr_meta = dict(pr_meta)
    pr_meta["reviewThreads"] = {"nodes": filled}
    return pr_meta


def expand_thread_comments(gh: GitHub, cfg: Config, node: dict[str, Any]) -> dict[str, Any]:
    conn = node.get("comments") if isinstance(node.get("comments"), dict) else {}
    page = conn.get("pageInfo") or {}
    thread_id = str(node.get("id") or "")
    if not page.get("hasNextPage") or not thread_id:
        return node
    nodes = list(conn.get("nodes") or [])
    cursor = page.get("endCursor")
    for _ in range(20):
        if not cursor:
            break
        data = gh.graphql(
            GITHUB_GRAPHQL_THREAD_COMMENTS,
            {"id": thread_id, "cursor": cursor},
            token=cfg.github_token,
        )
        nxt = ((data.get("node") or {}).get("comments")) or {}
        nodes.extend(n for n in (nxt.get("nodes") or []) if isinstance(n, dict))
        info = nxt.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return {**node, "comments": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
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
    items = gh.paginate_rest(
        f"repos/{cfg.repository}/issues/{number}/comments?per_page=100",
        token=cfg.github_token,
    )
    return [c for c in items if isinstance(c, dict)]


def list_reviews(gh: GitHub, cfg: Config, number: int) -> list[dict[str, Any]]:
    items = gh.paginate_rest(
        f"repos/{cfg.repository}/pulls/{number}/reviews?per_page=100",
        token=cfg.github_token,
    )
    return [c for c in items if isinstance(c, dict)]


def thread_comments(thread: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in ((thread.get("comments") or {}).get("nodes") or []) if isinstance(c, dict)]


def thread_comments_truncated(thread: dict[str, Any]) -> bool:
    page = (thread.get("comments") or {}).get("pageInfo") or {}
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


def already_resolved_on_sha(comments: list[dict[str, Any]], thread_id: str, sha: str) -> bool:
    needle = (thread_id, sha)
    for comment in comments:
        if needle in parse_resolved_markers(body_of(comment)):
            return True
    return False


def already_cited_on_sha(comments: list[dict[str, Any]], thread_id: str, sha: str) -> bool:
    needle = (thread_id, sha)
    for comment in comments:
        if needle in parse_cite_markers(body_of(comment)):
            return True
    return False


def resolved_count_today(comments: list[dict[str, Any]], now: datetime) -> int:
    start = now - timedelta(hours=24)
    count = 0
    for comment in comments:
        if not parse_resolved_markers(body_of(comment)):
            continue
        stamped = parse_dt(comment.get("createdAt") or comment.get("created_at"))
        if stamped is None or stamped >= start:
            count += 1
    return count


def ruling_excerpt(items: list[dict[str, Any]], thread_id: str, sha: str, limit: int) -> str:
    latest: tuple[datetime, str] | None = None
    for item in items:
        if not is_codex(login_of(item)):
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
    text = " ".join(latest[1].split())
    return text[:limit]


def classify_thread(
    thread: dict[str, Any],
    *,
    head_sha: str,
    latest: dict[tuple[str, str], StampedVerdict],
    issue_comments: list[dict[str, Any]],
) -> tuple[str, ThreadVerdict | None]:
    thread_id = safe_thread_id(str(thread.get("id") or ""))
    if not thread_id:
        return "bad-thread-id", None
    if thread.get("isResolved"):
        return "already-resolved", None
    comments = thread_comments(thread) + issue_comments
    if already_resolved_on_sha(comments, thread_id, head_sha):
        return "already-marked", None
    if path_is_protected(str(thread.get("path") or "")):
        return "protected-path", None
    any_for_thread = [s for (tid, _sha), s in latest.items() if tid == thread_id]
    if not any_for_thread:
        return "no-verdict", None
    newest = max(any_for_thread, key=lambda item: item.when)
    stamped = latest.get((thread_id, head_sha))
    if newest.verdict.sha != head_sha:
        if not newest.verdict.sha:
            return "ambiguous", newest.verdict
        return "stale-sha", None
    if stamped is None:
        return "no-verdict", None
    if stamped.verdict.verdict != "RESOLVE":
        return stamped.verdict.verdict.lower(), stamped.verdict
    comments = thread_comments(thread)
    if already_cited_on_sha(comments, thread_id, head_sha):
        return "retry-resolve", stamped.verdict
    return "resolve", stamped.verdict


def reply_and_resolve(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    sha: str,
    excerpt: str,
    retry: bool = False,
) -> str:
    live, rollup = fetch_head_state(gh, cfg, number)
    if not live or live != sha:
        return "stale-sha"
    ok, _reason = required_checks_green(rollup)
    if not ok:
        return _reason
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
    resolved = gh.graphql(
        RESOLVE_THREAD,
        {"id": thread_id},
        token=cfg.codex_pat,
    )
    thread = ((resolved.get("resolveReviewThread") or {}).get("thread")) or {}
    echoed = str(thread.get("id") or "")
    if echoed != thread_id:
        return "resolve-error"
    if thread.get("isResolved") is False:
        return "resolve-error"
    try:
        gh.rest(
            f"repos/{cfg.repository}/issues/{number}/comments",
            method="POST",
            body={"body": build_success_comment(thread_id, sha)},
            token=cfg.codex_pat,
        )
    except Exception:
        try:
            gh.graphql(
                REPLY_THREAD,
                {"threadId": thread_id, "body": build_success_comment(thread_id, sha)},
                token=cfg.codex_pat,
            )
        except Exception:
            return "posted-unmarked"
    return "posted"


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
    threads = [n for n in ((pr.get("reviewThreads") or {}).get("nodes") or []) if isinstance(n, dict)]
    issue_comments = list_issue_comments(gh, cfg, number)
    reviews = list_reviews(gh, cfg, number)
    items = all_codex_items(threads, issue_comments, reviews)
    latest = latest_verdicts(items)
    posted_today = resolved_count_today(
        issue_comments + [c for t in threads for c in thread_comments(t)],
        now,
    )
    if not cfg.codex_pat:
        logs.append(f"codex-auto-resolve: pr={number} skip=no-secret")
        return logs
    for thread in threads:
        tid = log_thread_id(str(thread.get("id") or ""))
        if thread_comments_truncated(thread):
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip=truncated-comments")
            continue
        reason, verdict = classify_thread(
            thread, head_sha=head_sha, latest=latest, issue_comments=issue_comments
        )
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
        try:
            result = reply_and_resolve(
                gh,
                cfg,
                number=number,
                thread_id=str(thread.get("id") or ""),
                sha=head_sha,
                excerpt=excerpt,
                retry=reason == "retry-resolve",
            )
        except Exception:
            result = "error"
        if result == "posted":
            posted_today += 1
            remaining[0] -= 1
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} resolve=posted sha={head_sha}")
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
