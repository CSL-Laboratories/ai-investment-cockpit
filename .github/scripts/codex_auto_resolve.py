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
* no owner/human decision is needed (``KEEP`` / missing / malformed / mixed
  markers fail closed and leave the needs-decision path)
* required checks on that head are green (``statusCheckRollup.state == SUCCESS``)

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
MARKER_PREFIX = "codex-thread-verdict:"
RESOLVED_PREFIX = "codex-thread-resolved:"
HTML_VERDICT_RE = re.compile(
    r"<!--\s*codex-thread-verdict:([A-Za-z0-9_=-]{1,128}):"
    r"([0-9a-fA-F]{40}|[0-9a-fA-F]{64}):(RESOLVE|KEEP)\s*-->"
)
LINE_VERDICT_RE = re.compile(
    r"^CODEX-THREAD:\s*(RESOLVE|KEEP)\s+"
    r"thread=([A-Za-z0-9_=-]{1,128})\s+"
    r"sha=([0-9a-fA-F]{40}|[0-9a-fA-F]{64})"
    r"(?:\s+reason=(needs-decision|fix-missing|ambiguous))?\s*$"
)
HTML_RESOLVED_RE = re.compile(
    r"<!--\s*codex-thread-resolved:([A-Za-z0-9_=-]{1,128}):"
    r"([0-9a-fA-F]{40}|[0-9a-fA-F]{64})\s*-->"
)
SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_=-]{1,128}$")
SAFE_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
SAFE_PR_RE = re.compile(r"^[1-9][0-9]{0,8}$")
SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,39}/[A-Za-z0-9._-]{1,80}$")
GATE_LINE_RE = re.compile(r"^CODEX-GATE:\s*(FIX|READY|MEDIATE)$")
PASSING_ROLLUP = frozenset({"SUCCESS"})
BLOCKING_ROLLUP = frozenset({"FAILURE", "ERROR", "PENDING", "EXPECTED", "CANCELLED"})

GITHUB_GRAPHQL_PRS = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $after, states: OPEN) {
      pageInfo { hasNextPage endCursor }
      nodes { number isDraft state url title }
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
      headRefOid
      reviewThreads(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          path
          comments(first: 80) {
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
      headRefOid
    }
  }
}
"""
GITHUB_GRAPHQL_CHECKS = """
query($owner: String!, $name: String!, $oid: GitObjectID!) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) {
      ... on Commit {
        oid
        statusCheckRollup { state }
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


def verdict_marker(thread_id: str, sha: str, verdict: str) -> str:
    tid = safe_thread_id(thread_id)
    digest = safe_sha(sha)
    kind = str(verdict or "").strip().upper()
    if not tid:
        raise ValueError("unsafe thread id")
    if not digest:
        raise ValueError("unsafe sha")
    if kind not in {"RESOLVE", "KEEP"}:
        raise ValueError("unsafe verdict")
    return f"<!-- {MARKER_PREFIX}{tid}:{digest}:{kind} -->"


def resolved_marker(thread_id: str, sha: str) -> str:
    tid = safe_thread_id(thread_id)
    digest = safe_sha(sha)
    if not tid:
        raise ValueError("unsafe thread id")
    if not digest:
        raise ValueError("unsafe sha")
    return f"<!-- {RESOLVED_PREFIX}{tid}:{digest} -->"


def visible_verdict_line(
    thread_id: str, sha: str, verdict: str, reason: str = ""
) -> str:
    tid = safe_thread_id(thread_id)
    digest = safe_sha(sha)
    kind = str(verdict or "").strip().upper()
    if not tid:
        raise ValueError("unsafe thread id")
    if not digest:
        raise ValueError("unsafe sha")
    if kind not in {"RESOLVE", "KEEP"}:
        raise ValueError("unsafe verdict")
    line = f"CODEX-THREAD: {kind} thread={tid} sha={digest}"
    extra = str(reason or "").strip().lower()
    if kind == "KEEP" and extra in {"needs-decision", "fix-missing", "ambiguous"}:
        line = f"{line} reason={extra}"
    return line


def mediation_verdict_instructions(thread_id: str, sha: str = "<pr-head-sha>") -> str:
    """Snippet for #124's ``@codex review`` prompt. Codex must not resolve itself."""
    tid = safe_thread_id(thread_id) or "<thread-id>"
    digest = safe_sha(sha) or "<pr-head-sha>"
    return (
        "After ruling, emit exactly one structured per-thread marker (HTML + line) "
        "for this thread. The auto-resolve workflow parses it; do **not** call "
        "resolveReviewThread, push, merge, or open a fix PR.\n\n"
        f"{verdict_marker(tid, digest, 'RESOLVE') if safe_sha(sha) and safe_thread_id(thread_id) else '<!-- codex-thread-verdict:<thread_id>:<pr-head-sha>:RESOLVE|KEEP -->'}\n"
        f"CODEX-THREAD: RESOLVE thread={tid} sha={digest}\n"
        f"CODEX-THREAD: KEEP thread={tid} sha={digest} reason=needs-decision\n\n"
        "RESOLVE = the fix is already on this exact head SHA and no owner/human "
        "decision is needed. KEEP = a decision is needed, the fix is missing, or "
        "it is ambiguous. If unsure, KEEP."
    )


@dataclass(frozen=True)
class ThreadVerdict:
    thread_id: str
    sha: str
    verdict: str
    reason: str = ""
    source: str = ""


def _collapse_body_verdicts(found: list[ThreadVerdict]) -> dict[tuple[str, str], ThreadVerdict]:
    collapsed: dict[tuple[str, str], ThreadVerdict] = {}
    for item in found:
        key = (item.thread_id, item.sha)
        prev = collapsed.get(key)
        if prev is None:
            collapsed[key] = item
            continue
        if prev.verdict != item.verdict or (item.verdict == "KEEP" and item.reason != prev.reason):
            reason = item.reason or prev.reason or "ambiguous"
            collapsed[key] = ThreadVerdict(
                thread_id=item.thread_id,
                sha=item.sha,
                verdict="KEEP",
                reason=reason if reason != "ambiguous" and prev.verdict != item.verdict else "ambiguous",
                source="mixed",
            )
    return collapsed


def parse_thread_verdicts(text: str | None) -> list[ThreadVerdict]:
    """Parse HTML markers and visible ``CODEX-THREAD:`` lines. KEEP wins in-body."""
    body = text or ""
    found: list[ThreadVerdict] = []
    for match in HTML_VERDICT_RE.finditer(body):
        tid = safe_thread_id(match.group(1))
        digest = safe_sha(match.group(2))
        kind = match.group(3)
        if tid and digest and kind in {"RESOLVE", "KEEP"}:
            found.append(ThreadVerdict(tid, digest, kind, source="html"))
    for line in body.splitlines():
        match = LINE_VERDICT_RE.fullmatch(line.strip())
        if not match:
            continue
        kind, tid_raw, sha_raw, reason = match.group(1), match.group(2), match.group(3), match.group(4) or ""
        tid = safe_thread_id(tid_raw)
        digest = safe_sha(sha_raw)
        if tid and digest:
            found.append(ThreadVerdict(tid, digest, kind, reason=reason, source="line"))
    return list(_collapse_body_verdicts(found).values())


def parse_resolved_markers(text: str | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in HTML_RESOLVED_RE.finditer(text or ""):
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
    latest: dict[tuple[str, str], StampedVerdict] = {}
    for item in items:
        for stamped in stamped_verdicts_from(item):
            key = (stamped.verdict.thread_id, stamped.verdict.sha)
            prev = latest.get(key)
            if prev is None or stamped.when >= prev.when:
                latest[key] = stamped
    return latest


def required_checks_green(rollup: dict[str, Any] | None) -> tuple[bool, str]:
    """True only when the commit rollup is SUCCESS. Missing/pending fail closed."""
    if not isinstance(rollup, dict):
        return False, "checks=missing"
    state = str(rollup.get("state") or "").upper()
    if state in PASSING_ROLLUP:
        return True, "checks=green"
    if not state:
        return False, "checks=missing"
    if state in BLOCKING_ROLLUP or state == "PENDING":
        return False, f"checks={state.lower()}"
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
        f"Auto-resolved: Codex ruled the fix is already on head `{short}` and no "
        f"owner/human decision is needed. Required checks on that head are green.",
        "",
    ]
    if excerpt:
        lines.extend([f"Codex ruling: {excerpt}", ""])
    lines.append(resolved_marker(tid, digest))
    return "\n".join(lines) + "\n"


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
    if event_name == "issue_comment":
        if not (event.get("issue") or {}).get("pull_request"):
            return False
        return is_codex(login_of((event.get("comment") or {})))
    return False


def same_repo_pull(event: dict[str, Any], repository: str) -> bool:
    pr = event.get("pull_request") if isinstance(event.get("pull_request"), dict) else {}
    if not pr:
        issue = event.get("issue") if isinstance(event.get("issue"), dict) else {}
        if not issue.get("pull_request"):
            return True
        return True
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    full = str(repo.get("full_name") or "")
    if not full:
        return True
    return full == repository


def should_scan_event(cfg: Config, load_event: Callable[[str], dict[str, Any]] | None = None) -> bool:
    if cfg.event_name in {"", "schedule", "workflow_dispatch"}:
        return True
    if cfg.event_name not in {
        "pull_request_review",
        "pull_request_review_comment",
        "issue_comment",
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
    pr_meta = dict(pr_meta)
    pr_meta["reviewThreads"] = {"nodes": threads}
    return pr_meta


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


def fetch_rollup(gh: GitHub, cfg: Config, sha: str) -> dict[str, Any] | None:
    digest = safe_sha(sha)
    if not digest:
        return None
    data = gh.graphql(
        GITHUB_GRAPHQL_CHECKS,
        {"owner": cfg.owner, "name": cfg.name, "oid": digest},
        token=cfg.github_token,
    )
    obj = ((data.get("repository") or {}).get("object")) or {}
    rollup = obj.get("statusCheckRollup")
    return rollup if isinstance(rollup, dict) else None


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
    stamped = latest.get((thread_id, head_sha))
    if stamped is None:
        any_for_thread = [s for (tid, _sha), s in latest.items() if tid == thread_id]
        if any_for_thread:
            return "stale-sha", None
        return "no-verdict", None
    if stamped.verdict.verdict != "RESOLVE":
        return f"keep:{stamped.verdict.reason or 'keep'}", stamped.verdict
    return "resolve", stamped.verdict


def reply_and_resolve(
    gh: GitHub,
    cfg: Config,
    *,
    number: int,
    thread_id: str,
    sha: str,
    excerpt: str,
) -> str:
    live = fetch_head_sha(gh, cfg, number)
    if not live or live != sha:
        return "stale-sha"
    rollup = fetch_rollup(gh, cfg, sha)
    ok, _reason = required_checks_green(rollup)
    if not ok:
        return _reason
    live = fetch_head_sha(gh, cfg, number)
    if live != sha:
        return "stale-sha"
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
    live = fetch_head_sha(gh, cfg, number)
    if live != sha:
        return "stale-after-resolve"
    try:
        gh.graphql(
            REPLY_THREAD,
            {"threadId": thread_id, "body": build_resolve_reply(thread_id, sha, excerpt)},
            token=cfg.codex_pat,
        )
    except Exception:
        return "reply-error"
    return "posted"


def scan_pr(
    pr: dict[str, Any],
    cfg: Config,
    gh: GitHub,
    now: datetime,
) -> list[str]:
    logs: list[str] = []
    number = int(pr.get("number") or 0)
    if not number:
        return logs
    if "reviewThreads" not in pr or "headRefOid" not in pr:
        pr = list_pr(gh, cfg, number)
    if pr.get("isDraft") or str(pr.get("state") or "OPEN") not in {"OPEN", "open"}:
        logs.append(f"codex-auto-resolve: pr={number} skip=closed-or-draft")
        return logs
    head_sha = safe_sha(pr.get("headRefOid"))
    if not head_sha:
        logs.append(f"codex-auto-resolve: pr={number} skip=no-head")
        return logs
    live = fetch_head_sha(gh, cfg, number)
    if live != head_sha:
        logs.append(f"codex-auto-resolve: pr={number} skip=stale-sha")
        return logs
    rollup = fetch_rollup(gh, cfg, head_sha)
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
    posted_run = 0
    if not cfg.codex_pat:
        logs.append(f"codex-auto-resolve: pr={number} skip=no-secret")
        return logs
    for thread in threads:
        tid = str(thread.get("id") or "?")
        reason, verdict = classify_thread(
            thread, head_sha=head_sha, latest=latest, issue_comments=issue_comments
        )
        if reason != "resolve":
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={reason}")
            continue
        if not checks_ok:
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip={checks_reason}")
            continue
        if posted_today >= cfg.max_per_pr_per_day or posted_run >= cfg.max_per_run:
            logs.append(f"codex-auto-resolve: pr={number} thread={tid} skip=over-cap")
            continue
        excerpt = ruling_excerpt(items, verdict.thread_id if verdict else tid, head_sha, cfg.quote_limit)
        try:
            result = reply_and_resolve(
                gh,
                cfg,
                number=number,
                thread_id=tid,
                sha=head_sha,
                excerpt=excerpt,
            )
        except Exception:
            result = "error"
        if result == "posted":
            posted_today += 1
            posted_run += 1
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
    for pr in prs:
        try:
            logs.extend(scan_pr(pr, cfg, gh, now))
        except Exception:
            logs.append(f"codex-auto-resolve: pr={pr.get('number')} error")
    for line in logs:
        print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Codex auto-resolve\n\n```text\n")
            handle.write("\n".join(logs) or "no-op")
            handle.write("\n```\n")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
