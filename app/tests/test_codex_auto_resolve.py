"""Unit tests for Codex per-thread auto-resolve (CSL-179)."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "codex_auto_resolve.py"
WORKFLOW = ROOT / ".github" / "workflows" / "codex-auto-resolve.yml"
CODEX = "chatgpt-codex-connector[bot]"
DEVIN = "devin-ai-integration[bot]"
PAT_LOGIN = "github-actions[bot]"
SHA = "a" * 40
SHA2 = "b" * 40
NOW = datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)
THREAD = "PRRT_kwDOabc123"
THREAD2 = "PRRT_kwDOabc456"


def load():
    spec = importlib.util.spec_from_file_location("codex_auto_resolve", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["codex_auto_resolve"] = module
    spec.loader.exec_module(module)
    return module


mod = load()


def cfg(**overrides) -> object:
    values = dict(
        repository="CSL-Laboratories/ai-investment-cockpit",
        owner="CSL-Laboratories",
        name="ai-investment-cockpit",
        pr_number="12",
        max_per_pr_per_day=20,
        max_per_run=10,
        quote_limit=80,
        github_token="gh",
        codex_pat="pat",
        event_name="schedule",
        event_path="",
    )
    values.update(overrides)
    return mod.Config(**values)


def comment(
    login: str,
    body: str,
    created: datetime | None = None,
    association: str | None = None,
) -> dict:
    stamp = (created or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    if association is None:
        association = "NONE" if login.lower().endswith("[bot]") else "OWNER"
    return {
        "author": {"login": login},
        "user": {"login": login},
        "body": body,
        "createdAt": stamp,
        "created_at": stamp,
        "submittedAt": stamp,
        "submitted_at": stamp,
        "authorAssociation": association,
        "author_association": association,
    }


def thread(
    thread_id: str = THREAD,
    comments: list[dict] | None = None,
    resolved: bool = False,
    truncated: bool = False,
    resolved_by: str = "",
) -> dict:
    if comments is None:
        comments = [comment(DEVIN, "please fix", NOW - timedelta(seconds=60))]
    payload = {
        "id": thread_id,
        "isResolved": resolved,
        "path": "AGENTS.md",
        "comments": {
            "nodes": comments,
            "pageInfo": {"hasNextPage": truncated, "endCursor": None},
        },
    }
    if resolved_by:
        payload["resolvedBy"] = {"login": resolved_by}
    return payload


def verdict_body(kind: str = "RESOLVE", sha: str = SHA, tid: str = THREAD, extra: str = "") -> str:
    return (
        f"The fix is in.\n\n{mod.verdict_marker(tid)}\n"
        f"CODEX-THREAD: {kind}\n"
        f"HEAD: {sha}\n{extra}"
    )


def passing_rollup() -> dict:
    return {
        "state": "SUCCESS",
        "contexts": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "linux-gates",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "isRequired": True,
                }
            ],
        },
    }


class FakeGitHub(mod.GitHub):
    def __init__(self) -> None:
        super().__init__(run=self._unused)
        self.prs: list[dict] = []
        self.pr_by_number: dict[int, dict] = {}
        self.issue_comments: dict[int, list[dict]] = {}
        self.reviews: dict[int, list[dict]] = {}
        self.rollup_by_sha: dict[str, dict | None] = {SHA: passing_rollup()}
        self.head_by_number: dict[int, str] = {}
        self.head_queue: dict[int, list[str]] = {}
        self.resolves: list[str] = []
        self.replies: list[tuple[str, str]] = []
        self.issue_posts: list[tuple[int, str]] = []
        self.fail_resolve = False
        self.resolve_errors_left = 0
        self.resolve_leave_open = False
        self.resolve_echo_id = ""
        self.fail_reply = False
        self.fail_issue_post = False
        self.fail_viewer = False
        self.viewer_login = PAT_LOGIN
        self.resolve_by_login: str | None = PAT_LOGIN
        self.resolve_by_queue: list[str | None] = []
        self.thread_tails: dict[str, list[dict]] = {}
        self.thread_tail_queue: dict[str, list[list[dict]]] = {}
        self.thread_tail_has_previous: dict[str, bool] = {}
        self.fail_thread_tail = False
        self.thread_tail_calls = 0

    def _unused(self, *args, **kwargs):
        raise AssertionError("real gh invoked")

    def _next_sha(self, number: int) -> str:
        queued = self.head_queue.get(number) or []
        if queued:
            return queued.pop(0)
        return self.head_by_number.get(number) or SHA

    def _comments_for_thread(self, tid: str) -> list[dict]:
        for pr in self.pr_by_number.values():
            for node in ((pr.get("reviewThreads") or {}).get("nodes") or []):
                if isinstance(node, dict) and str(node.get("id") or "") == tid:
                    return [
                        item
                        for item in ((node.get("comments") or {}).get("nodes") or [])
                        if isinstance(item, dict)
                    ]
        return []

    def graphql(self, query: str, variables: dict, token: str | None = None) -> object:
        if "viewer { login }" in " ".join(query.split()):
            if token != "pat":
                raise AssertionError("viewer login must use CODEX_TRIGGER_PAT")
            if self.fail_viewer:
                raise RuntimeError("viewer lookup failed")
            return {"viewer": {"login": self.viewer_login}}
        if "resolveReviewThread" in query:
            if token != "pat":
                raise AssertionError("resolve must use CODEX_TRIGGER_PAT")
            if self.fail_resolve or self.resolve_errors_left:
                if self.resolve_errors_left:
                    self.resolve_errors_left -= 1
                raise RuntimeError("github graphql errors")
            tid = str(variables["id"])
            self.resolves.append(tid)
            resolver = (
                self.resolve_by_queue.pop(0)
                if self.resolve_by_queue
                else self.resolve_by_login
            )
            thread_payload: dict[str, object] = {
                "id": self.resolve_echo_id or tid,
                "isResolved": not self.resolve_leave_open,
            }
            if resolver:
                thread_payload["resolvedBy"] = {"login": resolver}
            else:
                thread_payload["resolvedBy"] = None
            return {"resolveReviewThread": {"thread": thread_payload}}
        if "comments(last: 40)" in query:
            self.thread_tail_calls += 1
            if self.fail_thread_tail:
                raise RuntimeError("github graphql errors")
            tid = str(variables["id"])
            queued = self.thread_tail_queue.get(tid)
            if queued:
                nodes = list(queued.pop(0))
            elif tid in self.thread_tails:
                nodes = list(self.thread_tails[tid])
            else:
                nodes = self._comments_for_thread(tid)
            has_previous = self.thread_tail_has_previous.get(tid, len(nodes) >= 40)
            return {
                "node": {
                    "comments": {
                        "nodes": nodes,
                        "pageInfo": {"hasPreviousPage": has_previous},
                    }
                }
            }
        if "addPullRequestReviewThreadReply" in query:
            if token != "pat":
                raise AssertionError("reply must use CODEX_TRIGGER_PAT")
            body = str(variables["body"])
            if self.fail_reply or (self.fail_issue_post and mod.RESOLVED_PREFIX in body):
                raise RuntimeError("github graphql errors")
            tid = str(variables["threadId"])
            self.replies.append((tid, body))
            return {
                "addPullRequestReviewThreadReply": {
                    "comment": {"id": "C1", "url": "https://ex/c"}
                }
            }
        if "statusCheckRollup" in query:
            number = int(variables["number"])
            sha = self._next_sha(number)
            pr = self.pr_by_number.get(number) or {}
            return {
                "repository": {
                    "pullRequest": {
                        "number": number,
                        "isDraft": bool(pr.get("isDraft")),
                        "state": str(pr.get("state") or "OPEN"),
                        "isCrossRepository": bool(pr.get("isCrossRepository")),
                        "headRefOid": sha,
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "oid": sha,
                                        "statusCheckRollup": self.rollup_by_sha.get(sha),
                                    }
                                }
                            ]
                        },
                    }
                }
            }
        if "headRefOid" in query and "reviewThreads" not in query:
            number = int(variables["number"])
            sha = self._next_sha(number)
            pr = self.pr_by_number.get(number) or {}
            return {
                "repository": {
                    "pullRequest": {
                        "number": number,
                        "isDraft": bool(pr.get("isDraft")),
                        "state": str(pr.get("state") or "OPEN"),
                        "headRefOid": sha,
                    }
                }
            }
        if "reviewThreads" in query:
            number = int(variables["number"])
            return {"repository": {"pullRequest": self.pr_by_number[number]}}
        return {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": self.prs,
                }
            }
        }

    def rest(self, path: str, method: str = "GET", body: dict | None = None, token: str | None = None):
        if method == "POST" and "/comments" in path:
            if token != "pat":
                raise AssertionError("success comment must use CODEX_TRIGGER_PAT")
            if self.fail_issue_post:
                raise RuntimeError("rest failed")
            number = int(path.split("/issues/")[1].split("/")[0])
            text = str((body or {}).get("body") or "")
            self.issue_posts.append((number, text))
            return {"id": 1}
        raise AssertionError(f"unexpected REST {method} {path}")

    def paginate_rest(self, path: str, token: str | None = None) -> list:
        if "/reviews" in path:
            number = int(path.split("/pulls/")[1].split("/")[0])
            return list(self.reviews.get(number, []))
        number = int(path.split("/issues/")[1].split("/")[0])
        return list(self.issue_comments.get(number, []))


def pr_payload(
    number: int = 12,
    *,
    sha: str = SHA,
    threads: list[dict] | None = None,
    draft: bool = False,
) -> dict:
    return {
        "number": number,
        "isDraft": draft,
        "state": "OPEN",
        "isCrossRepository": False,
        "url": f"https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/{number}",
        "title": "CSL-179 demo",
        "headRefOid": sha,
        "reviewThreads": {"nodes": threads or []},
    }


def ready_gh(
    *,
    threads: list[dict],
    reviews: list[dict] | None = None,
    sha: str = SHA,
    number: int = 12,
) -> FakeGitHub:
    gh = FakeGitHub()
    payload = pr_payload(number, threads=threads, sha=sha)
    gh.prs = [{"number": number, "isDraft": False, "state": "OPEN"}]
    gh.pr_by_number[number] = payload
    gh.head_by_number[number] = sha
    gh.reviews[number] = reviews or []
    gh.rollup_by_sha[sha] = passing_rollup()
    return gh


def test_marker_round_trip():
    html = mod.verdict_marker(THREAD)
    assert html == f"<!-- codex-thread-verdict:{THREAD} -->"
    parsed = mod.parse_thread_verdicts(verdict_body())
    assert parsed == [mod.ThreadVerdict(THREAD, SHA, "RESOLVE", source="block")]
    success = mod.build_success_comment(THREAD, SHA, NOW)
    assert mod.resolved_marker(THREAD, SHA) in success
    assert f"RESOLVED_AT: {NOW.strftime('%Y-%m-%dT%H:%M:%SZ')}" in success
    assert mod.parse_resolved_at(success) == NOW


def test_marker_rejects_unsafe_id():
    with pytest.raises(ValueError):
        mod.verdict_marker("bad id")
    with pytest.raises(ValueError):
        mod.resolved_marker(THREAD, "zzz")
    with pytest.raises(ValueError):
        mod.cite_marker(THREAD, "notasha")


def test_line_only_resolve_is_not_actionable():
    body = f"CODEX-THREAD: RESOLVE thread={THREAD} sha={SHA}\nHEAD: {SHA}\n"
    assert mod.parse_thread_verdicts(body) == []


def test_html_without_action_is_ambiguous():
    body = f"{mod.verdict_marker(THREAD)}\nHEAD: {SHA}\n"
    parsed = mod.parse_thread_verdicts(body)
    assert len(parsed) == 1
    assert parsed[0].verdict == "AMBIGUOUS"
    assert parsed[0].reason == "missing-action"


def test_malformed_keep_companion_is_ambiguous():
    body = (
        f"{mod.verdict_marker(THREAD)}\n"
        f"CODEX-THREAD: RESOLVE\n"
        f"CODEX-THREAD: KEEP reason=unresolved\n"
        f"HEAD: {SHA}\n"
    )
    parsed = mod.parse_thread_verdicts(body)
    assert len(parsed) == 1
    assert parsed[0].verdict == "AMBIGUOUS"
    assert parsed[0].reason == "malformed"


def test_mixed_actions_in_block_are_ambiguous():
    body = (
        f"{mod.verdict_marker(THREAD)}\n"
        "CODEX-THREAD: RESOLVE\n"
        "CODEX-THREAD: DECIDE\n"
        f"HEAD: {SHA}\n"
    )
    parsed = mod.parse_thread_verdicts(body)
    assert parsed[0].verdict == "AMBIGUOUS"


def test_quoted_and_fenced_verdicts_cannot_authorize():
    quoted = "\n".join(f"> {line}" for line in verdict_body().splitlines())
    fenced = f"```\n{verdict_body()}\n```\n"
    tilde = f"~~~\n{verdict_body()}\n~~~\n"
    assert mod.parse_thread_verdicts(quoted) == []
    assert mod.parse_thread_verdicts(fenced) == []
    assert mod.parse_thread_verdicts(tilde) == []
    mixed_backtick = f"```\n~~~\n{verdict_body()}\n~~~\n```\n"
    mixed_tilde = f"~~~\n```\n{verdict_body()}\n```\n~~~\n"
    nested_longer = f"````\n```\n{verdict_body()}\n```\n````\n"
    assert mod.parse_thread_verdicts(mixed_backtick) == []
    assert mod.parse_thread_verdicts(mixed_tilde) == []
    assert mod.parse_thread_verdicts(nested_longer) == []
    false_close = f"```\nstuff\n    ```\n{verdict_body()}\n```\n"
    assert mod.parse_thread_verdicts(false_close) == []
    three_space_closer = f"```\n   ```\n{verdict_body()}\n"
    assert mod.parse_thread_verdicts(three_space_closer) == [
        mod.ThreadVerdict(THREAD, SHA, "RESOLVE", source="block")
    ]


def test_login_aliases_accept_graphql_and_rest():
    assert mod.is_codex("chatgpt-codex-connector[bot]")
    assert mod.is_codex("chatgpt-codex-connector")
    assert not mod.is_codex("claude[bot]")
    assert not mod.is_codex("devin-ai-integration[bot]")


def test_gate_ready_is_not_a_thread_resolve():
    body = "Looks good.\n\nCODEX-GATE: READY\n"
    assert mod.parse_thread_verdicts(body) == []
    assert mod.has_gate_verdict_only(body)
    assert not mod.has_gate_verdict_only(verdict_body())


def test_latest_codex_comment_wins():
    older = comment(CODEX, verdict_body("MISSING"), NOW - timedelta(hours=1))
    newer = comment(CODEX, verdict_body("DECIDE"), NOW)
    latest = mod.latest_verdicts([older, newer])
    assert latest[(THREAD, SHA)].verdict.verdict == "DECIDE"


def test_decide_is_sticky_against_later_resolve():
    older = comment(CODEX, verdict_body("DECIDE"), NOW - timedelta(hours=1))
    newer = comment(CODEX, verdict_body("RESOLVE"), NOW)
    latest = mod.latest_verdicts([older, newer])
    assert latest[(THREAD, SHA)].verdict.verdict == "DECIDE"


def test_non_codex_markers_are_ignored():
    fake = comment("alice", verdict_body("RESOLVE"))
    latest = mod.latest_verdicts([fake])
    assert latest == {}


def test_required_checks_fail_closed():
    assert mod.required_checks_green(passing_rollup()) == (True, "checks=green")
    assert mod.required_checks_green({"state": "SUCCESS"})[0] is False
    assert mod.required_checks_green({"state": "PENDING"})[0] is False
    assert mod.required_checks_green({"state": "FAILURE"})[0] is False
    assert mod.required_checks_green(None)[0] is False
    assert mod.required_checks_green({})[0] is False


def test_required_checks_ignore_own_pending_job():
    rollup = {
        "state": "PENDING",
        "contexts": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "linux-gates",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "isRequired": True,
                },
                {
                    "__typename": "CheckRun",
                    "name": "codex auto-resolve",
                    "conclusion": None,
                    "status": "IN_PROGRESS",
                    "isRequired": False,
                },
            ],
        },
    }
    assert mod.required_checks_green(rollup) == (True, "checks=green")


def test_required_checks_truncated_fail_closed():
    rollup = {
        "state": "SUCCESS",
        "contexts": {"pageInfo": {"hasNextPage": True}, "nodes": []},
    }
    assert mod.required_checks_green(rollup) == (False, "checks=truncated")


def test_classify_decide_does_not_resolve():
    t = thread(comments=[comment(CODEX, verdict_body("DECIDE"))])
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, verdict = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "decide"
    assert verdict is not None and verdict.verdict == "DECIDE"


def test_classify_stale_sha():
    t = thread(comments=[comment(CODEX, verdict_body("RESOLVE", sha=SHA2))])
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "stale-sha"


def test_classify_already_resolved_and_marked():
    t = thread(resolved=True, comments=[comment(CODEX, verdict_body())])
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "already-resolved"

    marked = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA)),
        ]
    )
    latest = mod.latest_verdicts(mod.thread_comments(marked))
    reason, _ = mod.classify_thread(
        marked, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "already-marked"

    forged = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment("alice", mod.resolved_marker(THREAD, SHA), NOW - timedelta(seconds=1)),
        ]
    )
    latest = mod.latest_verdicts(mod.thread_comments(forged))
    reason, _ = mod.classify_thread(
        forged, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "resolve"


def test_forged_resolved_marker_does_not_already_mark():
    t = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment("alice", mod.resolved_marker(THREAD, SHA), NOW - timedelta(seconds=1)),
        ]
    )
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "resolve"


def test_classify_retry_mark_when_resolved_without_audit_marker():
    t = thread(
        resolved=True,
        resolved_by=PAT_LOGIN,
        comments=[
            comment(CODEX, verdict_body()),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
        ],
    )
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, verdict = mod.classify_thread(
        t, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "retry-mark"
    assert verdict is not None and verdict.sha == SHA


def test_retry_mark_latest_cite_when_old_marker_exists():
    t = thread(
        resolved=True,
        resolved_by=PAT_LOGIN,
        comments=[
            comment(CODEX, verdict_body()),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
            comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA)),
            comment(CODEX, verdict_body(sha=SHA2)),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2)),
        ],
    )
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, verdict = mod.classify_thread(
        t, head_sha=SHA2, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "retry-mark"
    assert verdict is not None and verdict.sha == SHA2


def test_cite_sha_prefers_newer_timestamp_across_surfaces():
    older_issue = comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), NOW - timedelta(hours=2))
    newer_thread = comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2), NOW)
    assert mod.cite_sha_for_thread([newer_thread, older_issue], THREAD, PAT_LOGIN) == SHA2
    assert mod.cite_sha_for_thread([older_issue, newer_thread], THREAD, PAT_LOGIN) == SHA2


def test_retry_mark_uses_newer_thread_cite_over_older_issue_cite():
    t = thread(
        resolved=True,
        resolved_by=PAT_LOGIN,
        comments=[comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2), NOW)],
    )
    reason, verdict = mod.classify_thread(
        t,
        head_sha=SHA2,
        latest={},
        issue_comments=[
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), NOW - timedelta(hours=2))
        ],
        pat_login=PAT_LOGIN,
    )
    assert reason == "retry-mark"
    assert verdict is not None and verdict.sha == SHA2


def test_retry_mark_newer_issue_cite_beats_older_thread_cite():
    t = thread(
        resolved=True,
        resolved_by=PAT_LOGIN,
        comments=[
            comment(CODEX, verdict_body(sha=SHA2)),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), NOW - timedelta(hours=1)),
            comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA), NOW - timedelta(hours=1)),
        ],
    )
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, verdict = mod.classify_thread(
        t,
        head_sha=SHA2,
        latest=latest,
        issue_comments=[comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2), NOW)],
        pat_login=PAT_LOGIN,
    )
    assert reason == "retry-mark"
    assert verdict is not None and verdict.sha == SHA2


def test_human_resolve_after_cite_does_not_retry_mark():
    t = thread(
        resolved=True,
        resolved_by="alice",
        comments=[
            comment(CODEX, verdict_body()),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
        ],
    )
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "already-resolved"


def test_forged_cite_does_not_retry_mark():
    t = thread(
        resolved=True,
        resolved_by=PAT_LOGIN,
        comments=[
            comment(CODEX, verdict_body()),
            comment("alice", mod.cite_marker(THREAD, SHA)),
        ],
    )
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "already-resolved"

    forged_receipt = thread(
        resolved=True,
        resolved_by=PAT_LOGIN,
        comments=[
            comment(CODEX, verdict_body()),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
            comment("alice", mod.resolved_marker(THREAD, SHA)),
        ],
    )
    latest = mod.latest_verdicts(mod.thread_comments(forged_receipt))
    reason, verdict = mod.classify_thread(
        forged_receipt, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "retry-mark"
    assert verdict is not None and verdict.sha == SHA

    forged_issue = thread(resolved=True, resolved_by=PAT_LOGIN, comments=[comment(CODEX, verdict_body())])
    latest = mod.latest_verdicts(mod.thread_comments(forged_issue))
    reason, _ = mod.classify_thread(
        forged_issue,
        head_sha=SHA,
        latest=latest,
        issue_comments=[comment("alice", mod.cite_marker(THREAD, SHA))],
        pat_login=PAT_LOGIN,
    )
    assert reason == "already-resolved"


def test_classify_retry_resolve_when_already_cited():
    t = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
        ]
    )
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, verdict = mod.classify_thread(
        t, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "retry-resolve"
    assert verdict is not None and verdict.verdict == "RESOLVE"


def test_scan_resolves_when_safe():
    gh = ready_gh(
        threads=[thread(comments=[comment("devin-ai-integration[bot]", "please fix")])],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == [THREAD]
    assert len(gh.replies) == 1
    assert THREAD in gh.replies[0][0]
    assert SHA[:12] in gh.replies[0][1]
    assert mod.cite_marker(THREAD, SHA) in gh.replies[0][1]
    assert mod.resolved_marker(THREAD, SHA) not in gh.replies[0][1]
    assert len(gh.issue_posts) == 1
    assert mod.resolved_marker(THREAD, SHA) in gh.issue_posts[0][1]
    assert any("resolve=posted" in line for line in logs)


def test_scan_skips_pending_required_checks():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.rollup_by_sha[SHA] = {
        "state": "PENDING",
        "contexts": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "linux-gates",
                    "conclusion": None,
                    "status": "IN_PROGRESS",
                    "isRequired": True,
                }
            ],
        },
    }
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=checks=linux-gates-not-green" in line for line in logs)


def test_scan_ignores_own_pending_check():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.rollup_by_sha[SHA] = {
        "state": "PENDING",
        "contexts": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "linux-gates",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "isRequired": True,
                },
                {
                    "__typename": "CheckRun",
                    "name": "resolve",
                    "conclusion": None,
                    "status": "IN_PROGRESS",
                    "isRequired": False,
                },
            ],
        },
    }
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == [THREAD]
    assert any("resolve=posted" in line for line in logs)


def test_scan_skips_decide_and_leaves_needs_decision_path():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body("DECIDE"))])
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=decide" in line for line in logs)


def test_scan_skips_gate_ready_without_thread_marker():
    gh = ready_gh(
        threads=[thread()],
        reviews=[comment(CODEX, "Settled.\n\nCODEX-GATE: READY\n")],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=no-verdict" in line for line in logs)


def test_scan_skips_truncated_thread_comments():
    gh = ready_gh(
        threads=[thread(truncated=True)],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=truncated-comments" in line for line in logs)


def test_scan_never_resolves_on_stale_head_during_mutation():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.head_queue[12] = [SHA, SHA, SHA2]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("stale-sha" in line for line in logs)


def test_reply_failure_does_not_resolve():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.fail_reply = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=reply-error" in line for line in logs)


def test_posted_unmarked_counts_against_run_cap():
    remaining = [1]
    first = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    first.fail_issue_post = True
    logs = mod.scan_pr(first.pr_by_number[12], cfg(), first, NOW, remaining_run=remaining)
    assert first.resolves == [THREAD]
    assert remaining == [0]
    assert any("resolve=posted-unmarked" in line for line in logs)

    second = ready_gh(
        number=13,
        threads=[thread(thread_id=THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))])],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    logs = mod.scan_pr(second.pr_by_number[13], cfg(pr_number="13"), second, NOW, remaining_run=remaining)
    assert second.resolves == []
    assert any("skip=over-cap" in line for line in logs)


def test_human_resolve_race_does_not_post_receipt_or_consume_cap():
    gh = ready_gh(
        threads=[
            thread(),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body()),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    gh.resolve_by_queue = ["human-reviewer"]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert gh.resolves == [THREAD, THREAD2]
    assert any(f"thread={THREAD} skip=already-resolved" in line for line in logs)
    assert any(f"thread={THREAD2} resolve=posted" in line for line in logs)
    receipts = [body for _n, body in gh.issue_posts if mod.RESOLVED_PREFIX in body]
    assert len(receipts) == 1
    assert THREAD2 in receipts[0]
    assert THREAD not in receipts[0]


def test_retry_mark_posts_missing_audit_marker_without_unresolve():
    gh = ready_gh(
        threads=[
            thread(
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(CODEX, verdict_body()),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                ],
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.issue_posts and THREAD in gh.issue_posts[0][1]
    assert f"RESOLVED_AT: {NOW.strftime('%Y-%m-%dT%H:%M:%SZ')}" in gh.issue_posts[0][1]
    assert any("retry-mark=posted" in line for line in logs)


def test_retry_mark_counts_toward_daily_cap():
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(CODEX, verdict_body()),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body()),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert gh.resolves == []
    assert any("retry-mark=posted" in line for line in logs)
    assert any("skip=over-cap" in line for line in logs)


def test_old_cite_retry_mark_does_not_consume_today_cap():
    old = NOW - timedelta(hours=48)
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body(), old),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert THREAD2 in gh.resolves
    assert any("retry-mark=posted" in line for line in logs)
    assert any("resolve=posted" in line for line in logs)
    assert f"RESOLVED_AT: {old.strftime('%Y-%m-%dT%H:%M:%SZ')}" in gh.issue_posts[0][1]


def test_old_cite_retry_mark_marker_does_not_count_today():
    old = NOW - timedelta(hours=48)
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body(), old),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    gh.issue_comments[12] = [comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA), NOW)]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert THREAD2 in gh.resolves
    assert any("resolve=posted" in line for line in logs)


def test_later_cite_after_skip_counts():
    earlier = NOW - timedelta(hours=1)
    threads = [
        thread(
            THREAD,
            resolved=True,
            resolved_by=PAT_LOGIN,
            comments=[
                comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), earlier),
                comment(PAT_LOGIN, mod.skip_marker(THREAD, SHA), earlier),
                comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
            ],
        )
    ]
    assert mod.resolved_count_today(threads, [], NOW, PAT_LOGIN) == 1


def test_cite_before_skip_does_not_count():
    earlier = NOW - timedelta(hours=1)
    threads = [
        thread(
            THREAD,
            resolved=True,
            resolved_by="alice",
            comments=[
                comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), earlier),
                comment(PAT_LOGIN, mod.skip_marker(THREAD, SHA)),
            ],
        )
    ]
    assert mod.resolved_count_today(threads, [], NOW, PAT_LOGIN) == 0


def test_human_resolved_skipped_cite_does_not_count():
    threads = [
        thread(
            THREAD,
            resolved=True,
            resolved_by="alice",
            comments=[
                comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                comment(PAT_LOGIN, mod.skip_marker(THREAD, SHA)),
            ],
        )
    ]
    assert mod.resolved_count_today(threads, [], NOW, PAT_LOGIN) == 0


def test_human_resolved_unmarked_cite_still_counts():
    threads = [
        thread(
            THREAD,
            resolved=True,
            resolved_by="alice",
            comments=[comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA))],
        )
    ]
    assert mod.resolved_count_today(threads, [], NOW, PAT_LOGIN) == 1


def test_human_resolved_skipped_cite_does_not_consume_cap():
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by="alice",
                comments=[
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                    comment(PAT_LOGIN, mod.skip_marker(THREAD, SHA)),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert THREAD2 in gh.resolves
    assert any("resolve=posted" in line for line in logs)
    assert not any("skip=over-cap" in line for line in logs)


def test_later_human_resolve_cannot_erase_unmarked_pat_success():
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by="alice",
                comments=[comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA))],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert THREAD2 not in gh.resolves
    assert any("skip=over-cap" in line for line in logs)


def test_unmarked_prior_head_cite_plus_later_marker_fail_closed():
    gh = ready_gh(
        threads=[thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))])],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    gh.issue_comments[12] = [
        comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
        comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2)),
        comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA2)),
    ]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=2), gh, NOW)
    assert THREAD2 not in gh.resolves
    assert any("skip=over-cap" in line for line in logs)


def test_unmarked_reopen_on_new_head_counts_separately_from_old_marker():
    old = NOW - timedelta(hours=48)
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                    comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2)),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert THREAD2 not in gh.resolves
    assert any("skip=over-cap" in line for line in logs)


def test_unmarked_cite_after_in_window_receipt_counts_separately():
    earlier = NOW - timedelta(hours=1)
    threads = [
        thread(
            THREAD,
            resolved=True,
            resolved_by=PAT_LOGIN,
            comments=[
                comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), earlier),
                comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA), earlier),
                comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2)),
            ],
        )
    ]
    assert mod.resolved_count_today(threads, [], NOW, PAT_LOGIN) == 2


def test_unmarked_success_after_receipt_consumes_cap():
    earlier = NOW - timedelta(hours=1)
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), earlier),
                    comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA), earlier),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2)),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=2), gh, NOW)
    assert THREAD2 not in gh.resolves
    assert any("skip=over-cap" in line for line in logs)


def test_two_unmarked_heads_count_separately():
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2)),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=2), gh, NOW)
    assert THREAD2 not in gh.resolves
    assert any("skip=over-cap" in line for line in logs)


def test_old_cite_retry_resolve_consumes_in_scan_cap():
    old = NOW - timedelta(hours=48)
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body(), old),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert gh.resolves == [THREAD]
    assert any("skip=over-cap" in line for line in logs)


def test_unresolved_cites_do_not_burn_daily_cap():
    stuck = [
        thread(
            f"PRRT_old{i:02d}",
            comments=[comment(PAT_LOGIN, mod.cite_marker(f"PRRT_old{i:02d}", SHA))],
        )
        for i in range(20)
    ]
    gh = ready_gh(
        threads=[*stuck, thread()],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=20), gh, NOW)
    assert THREAD in gh.resolves
    assert any("resolve=posted" in line for line in logs)


def test_same_day_retry_resolve_does_not_double_charge():
    gh = ready_gh(
        threads=[
            thread(
                THREAD,
                comments=[
                    comment(CODEX, verdict_body()),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body()),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=2), gh, NOW)
    assert THREAD in gh.resolves
    assert THREAD2 in gh.resolves
    assert not any("skip=over-cap" in line for line in logs)


def test_reopen_resolve_on_new_head_counts_separately():
    gh = ready_gh(
        threads=[thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))])],
        reviews=[comment(CODEX, verdict_body(tid=THREAD2))],
    )
    gh.issue_comments[12] = [
        comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
        comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA)),
        comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA2)),
        comment(PAT_LOGIN, mod.resolved_marker(THREAD, SHA2)),
    ]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=2), gh, NOW)
    assert THREAD2 not in gh.resolves
    assert any("skip=over-cap" in line for line in logs)


def test_mutation_error_aborts_remaining_scan():
    gh = ready_gh(
        threads=[
            thread(),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body()),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    gh.resolve_errors_left = 1
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=2), gh, NOW)
    assert gh.resolves == []
    assert any("skip=resolve-error" in line for line in logs)
    assert any("skip=abort-scan" in line for line in logs)


def test_human_resolve_race_does_not_publish_receipt():
    remaining = [2]
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.resolve_by_login = "alice"
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW, remaining_run=remaining)
    assert gh.resolves == [THREAD]
    assert remaining == [2]
    assert any("skip=already-resolved" in line for line in logs)
    assert not any("resolve=posted" in line for line in logs)
    assert not any(mod.RESOLVED_PREFIX in body for _n, body in gh.issue_posts)
    assert any(mod.SKIP_PREFIX in body for _n, body in gh.issue_posts)


def test_missing_resolved_by_after_mutation_does_not_publish_receipt():
    remaining = [1]
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.resolve_by_login = ""
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW, remaining_run=remaining)
    assert gh.resolves == [THREAD]
    assert remaining == [0]
    assert not any(mod.RESOLVED_PREFIX in body for _n, body in gh.issue_posts)
    assert not any(mod.SKIP_PREFIX in body for _n, body in gh.issue_posts)
    assert any(mod.CITE_PREFIX in body for _n, body in gh.issue_posts)
    assert any("resolve=posted-unmarked" in line for line in logs)


def test_scan_skips_retry_mark_when_human_resolved():
    gh = ready_gh(
        threads=[
            thread(
                resolved=True,
                resolved_by="alice",
                comments=[
                    comment(CODEX, verdict_body()),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                ],
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.issue_posts == []
    assert any("already-resolved" in line for line in logs)


def test_scan_skips_retry_mark_for_forged_cite():
    gh = ready_gh(
        threads=[
            thread(
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[comment(CODEX, verdict_body())],
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    gh.issue_comments[12] = [comment("alice", mod.cite_marker(THREAD, SHA))]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.issue_posts == []
    assert any("already-resolved" in line for line in logs)


def test_retry_resolve_skips_second_cite():
    gh = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body()),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == [THREAD]
    assert gh.replies == []
    assert any("resolve=posted" in line for line in logs)


def test_retry_resolve_human_race_does_not_publish_receipt():
    gh = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body()),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    gh.resolve_by_login = "alice"
    remaining = [3]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW, remaining_run=remaining)
    assert gh.resolves == [THREAD]
    assert remaining == [3]
    assert any("skip=already-resolved" in line for line in logs)
    assert not any(mod.RESOLVED_PREFIX in body for _n, body in gh.issue_posts)
    assert any(mod.SKIP_PREFIX in body for _n, body in gh.issue_posts)


def test_idempotent_second_run():
    first = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    mod.scan_pr(first.pr_by_number[12], cfg(), first, NOW)
    assert first.resolves == [THREAD]
    cite_body = first.replies[0][1]
    success_body = first.issue_posts[0][1]
    second = ready_gh(
        threads=[
            thread(
                resolved=True,
                comments=[comment("github-actions[bot]", cite_body)],
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    second.issue_comments[12] = [comment("github-actions[bot]", success_body)]
    logs = mod.scan_pr(second.pr_by_number[12], cfg(), second, NOW)
    assert second.resolves == []
    assert any("already-resolved" in line for line in logs)


def test_daily_cap():
    prior = [
        comment(
            PAT_LOGIN,
            mod.build_success_comment(f"PRRT_old{i:02d}", SHA, NOW - timedelta(minutes=i)),
            NOW - timedelta(minutes=i),
        )
        for i in range(20)
    ]
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.issue_comments[12] = prior
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=20), gh, NOW)
    assert gh.resolves == []
    assert any("skip=over-cap" in line for line in logs)


def test_retry_resolve_error_refreshes_cite():
    old = NOW - timedelta(hours=48)
    gh = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body(), old)],
    )
    gh.fail_resolve = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=resolve-error" in line for line in logs)
    assert any(mod.CITE_PREFIX in body for _n, body in gh.issue_posts)


def test_fresh_cite_retry_resolve_error_does_not_refresh_cite():
    gh = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body()),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    gh.fail_resolve = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=resolve-error" in line for line in logs)
    assert not any(mod.CITE_PREFIX in body for _n, body in gh.issue_posts)


def test_resolved_false_does_not_refresh_cite():
    old = NOW - timedelta(hours=48)
    gh = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body(), old)],
    )
    gh.resolve_leave_open = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == [THREAD]
    assert any("skip=resolve-error" in line for line in logs)
    assert not any(mod.CITE_PREFIX in body for _n, body in gh.issue_posts)


def test_echo_mismatch_does_not_refresh_cite():
    old = NOW - timedelta(hours=48)
    gh = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body(), old)],
    )
    gh.resolve_echo_id = THREAD2
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == [THREAD]
    assert any("skip=resolve-error" in line for line in logs)
    assert not any(mod.CITE_PREFIX in body for _n, body in gh.issue_posts)


def test_retry_resolve_error_fresh_cite_keeps_next_scan_cap():
    old = NOW - timedelta(hours=48)
    first = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body(), old)],
    )
    first.fail_resolve = True
    mod.scan_pr(first.pr_by_number[12], cfg(max_per_pr_per_day=1), first, NOW)
    fresh_cites = [body for _n, body in first.issue_posts if mod.CITE_PREFIX in body]
    assert fresh_cites
    second = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body(), old),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    second.issue_comments[12] = [
        comment(PAT_LOGIN, fresh_cites[0], NOW),
    ]
    logs = mod.scan_pr(second.pr_by_number[12], cfg(max_per_pr_per_day=1), second, NOW)
    assert THREAD2 not in second.resolves
    assert any("skip=over-cap" in line for line in logs)


def test_indeterminate_resolve_consumes_same_scan_cap():
    gh = ready_gh(
        threads=[
            thread(comments=[comment(CODEX, verdict_body())]),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body()),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    gh.fail_resolve = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=1), gh, NOW)
    assert gh.resolves == []
    assert any("skip=resolve-error" in line for line in logs)
    assert any("skip=abort-scan" in line for line in logs)


def test_old_cite_retry_resolve_persists_in_next_scan_cap():
    old = NOW - timedelta(hours=48)
    first = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body(), old)],
    )
    mod.scan_pr(first.pr_by_number[12], cfg(max_per_pr_per_day=1), first, NOW)
    assert first.resolves == [THREAD]
    success_body = first.issue_posts[0][1]
    second = ready_gh(
        threads=[
            thread(
                THREAD,
                resolved=True,
                resolved_by=PAT_LOGIN,
                comments=[
                    comment(CODEX, verdict_body(), old),
                    comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA), old),
                    comment(PAT_LOGIN, success_body),
                ],
            ),
            thread(THREAD2, comments=[comment(CODEX, verdict_body(tid=THREAD2))]),
        ],
        reviews=[
            comment(CODEX, verdict_body(), old),
            comment(CODEX, verdict_body(tid=THREAD2)),
        ],
    )
    logs = mod.scan_pr(second.pr_by_number[12], cfg(max_per_pr_per_day=1), second, NOW)
    assert second.resolves == []
    assert any("skip=over-cap" in line for line in logs)


def test_forged_resolved_markers_do_not_burn_daily_cap():
    prior = [
        comment(
            "alice",
            mod.resolved_marker(f"PRRT_old{i:02d}", SHA),
            NOW - timedelta(minutes=i),
        )
        for i in range(20)
    ]
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.issue_comments[12] = prior
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=20), gh, NOW)
    assert gh.resolves == [THREAD]
    assert any("resolve=posted" in line for line in logs)


def test_run_wide_cap_is_shared_across_prs():
    gh = FakeGitHub()
    for number, tid in ((12, THREAD), (13, THREAD2)):
        payload = pr_payload(number, threads=[thread(tid)], sha=SHA)
        gh.pr_by_number[number] = payload
        gh.head_by_number[number] = SHA
        gh.reviews[number] = [comment(CODEX, verdict_body(tid=tid))]
        gh.rollup_by_sha[SHA] = passing_rollup()
    gh.prs = [
        {"number": 12, "isDraft": False, "state": "OPEN"},
        {"number": 13, "isDraft": False, "state": "OPEN"},
    ]
    code = mod.run(cfg=cfg(pr_number="", max_per_run=1), gh=gh, now=NOW)
    assert code == 0
    assert gh.resolves == [THREAD]
    assert len(gh.resolves) == 1


def test_missing_pat_login_skips_without_resolving():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.fail_viewer = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.issue_posts == []
    assert any("skip=no-pat-login" in line for line in logs)


def test_run_skips_when_viewer_lookup_fails(capsys):
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.fail_viewer = True
    code = mod.run(cfg=cfg(pr_number=""), gh=gh, now=NOW)
    assert code == 0
    assert gh.resolves == []
    out = capsys.readouterr().out
    assert "viewer login unavailable" in out


def test_missing_pat_is_notice_not_failure(capsys):
    code = mod.run(cfg=cfg(codex_pat="", event_name="schedule"), gh=FakeGitHub(), now=NOW)
    assert code == 0
    out = capsys.readouterr().out
    assert "CODEX_TRIGGER_PAT" in out
    assert "skipping auto-resolve" in out


def test_draft_pr_skipped():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.pr_by_number[12]["isDraft"] = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("closed-or-draft" in line for line in logs)


def test_cheap_event_filter_only_codex():
    event = {
        "review": {"user": {"login": CODEX}},
        "pull_request": {"head": {"repo": {"full_name": "CSL-Laboratories/ai-investment-cockpit"}}},
    }
    assert mod.cheap_codex_event(event, "pull_request_review")
    event["review"]["user"]["login"] = "claude[bot]"
    assert not mod.cheap_codex_event(event, "pull_request_review")


def test_fork_event_is_rejected():
    event = {
        "review": {"user": {"login": CODEX}},
        "pull_request": {"head": {"repo": {"full_name": "other/fork"}}},
    }
    conf = cfg(event_name="pull_request_review", event_path="event.json")
    assert not mod.should_scan_event(conf, load_event=lambda _p: event)


def test_issue_comment_event_is_rejected():
    event = {
        "comment": {"user": {"login": CODEX}},
        "issue": {"pull_request": {"url": "https://example/pr"}},
    }
    conf = cfg(event_name="issue_comment", event_path="event.json")
    assert not mod.should_scan_event(conf, load_event=lambda _p: event)
    assert not mod.same_repo_pull(event, "CSL-Laboratories/ai-investment-cockpit")


def test_reply_cites_ruling_and_sha():
    body = mod.build_resolve_reply(THREAD, SHA, "Fix already present on head.")
    assert SHA[:12] in body
    assert "no owner/human decision" in body
    assert "Fix already present" in body
    assert mod.cite_marker(THREAD, SHA) in body
    assert mod.resolved_marker(THREAD, SHA) not in body


def test_ruling_excerpt_strips_receipt_markers():
    planted = (
        verdict_body()
        + "\n"
        + mod.resolved_marker(THREAD, SHA)
        + "\nRESOLVED_AT: 2026-10-15T19:00:00Z\n"
        + mod.cite_marker(THREAD2, SHA)
        + "\n"
    )
    text = mod.ruling_excerpt([comment(CODEX, planted)], THREAD, SHA, 400)
    assert "<!--" not in text
    assert "RESOLVED_AT:" not in text
    assert THREAD not in text
    assert "The fix is in." in text


def test_reply_strips_receipt_markers_from_excerpt():
    dirty = (
        f"Looks good {mod.resolved_marker(THREAD, SHA)} "
        f"{mod.cite_marker(THREAD2, SHA2)}"
    )
    body = mod.build_resolve_reply(THREAD, SHA, dirty)
    assert mod.parse_resolved_markers(body) == []
    assert mod.parse_cite_markers(body) == [(THREAD, SHA)]
    assert THREAD2 not in body


def test_mediation_instructions_forbid_self_resolve():
    text = mod.mediation_verdict_instructions(THREAD)
    assert "do **not** call resolvereviewthread" in text.lower()
    assert "codex-thread-verdict" in text
    assert "CODEX-THREAD:" in text
    assert "DECIDE" in text
    assert "MISSING" in text
    assert "AMBIGUOUS" in text


def test_script_never_posts_codex_review_or_pushes():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "@codex review" not in text or "never posts" in text.lower()
    assert "git push" not in text
    assert "mergePullRequest" not in text
    assert "resolveReviewThread" in text
    assert "resolvedBy { login }" in text
    assert "CODEX_TRIGGER_PAT" in text


def test_workflow_structure():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: Codex auto-resolve" in text
    assert "pull_request_review:" not in text
    assert "pull_request_review_comment:" not in text
    assert "issue_comment:" not in text
    assert "workflow_dispatch:" not in text
    assert "runs-on: ubuntu-latest" in text
    assert "blacksmith-" not in text
    assert "python -m pytest" not in text
    assert "CODEX_TRIGGER_PAT: ${{ secrets.CODEX_TRIGGER_PAT }}" in text
    assert "GH_TOKEN: ${{ github.token }}" in text
    assert "persist-credentials: false" in text
    assert "github.event.repository.default_branch" in text
    assert "python3 -X utf8 -B .github/scripts/codex_auto_resolve.py" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
    assert "checks: read" in text
    assert "statuses: read" in text
    assert "11bd71901bbe5b1630ceea73d27597364c9af683" in text
    assert "resolveReviewThread" in text
    assert "Never run the PR head with the PAT" in text
    assert "CODEX-THREAD: RESOLVE|DECIDE|MISSING|AMBIGUOUS" in text


def test_sanitize_summary_strips_markup():
    dirty = "thread=<!--x--> ```injected``` <img>"
    cleaned = mod.sanitize_summary_line(dirty)
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "```" not in cleaned
    assert "`" not in cleaned


def test_run_end_to_end(tmp_path):
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    summary = tmp_path / "summary.md"
    import os

    os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
    try:
        code = mod.run(cfg=cfg(), gh=gh, now=NOW)
    finally:
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
    assert code == 0
    assert gh.resolves == [THREAD]
    written = summary.read_text(encoding="utf-8")
    assert "Codex auto-resolve" in written
    assert "```text" in written


def test_parse_paginated_concatenated_arrays():
    raw = json.dumps([{"id": 1}]) + json.dumps([{"id": 2}])
    items = mod.parse_paginated_json(raw)
    assert [i["id"] for i in items] == [1, 2]


def test_conflicting_blocks_in_one_body_are_ambiguous():
    body = verdict_body("DECIDE") + "\n" + verdict_body("RESOLVE")
    parsed = mod.parse_thread_verdicts(body)
    assert len(parsed) == 1
    assert parsed[0].verdict == "AMBIGUOUS"
    assert parsed[0].reason == "conflict"


def test_duplicate_head_lines_are_ambiguous():
    body = (
        f"{mod.verdict_marker(THREAD)}\n"
        "CODEX-THREAD: RESOLVE\n"
        f"HEAD: {SHA2}\n"
        f"HEAD: {SHA}\n"
    )
    parsed = mod.parse_thread_verdicts(body)
    assert parsed[0].verdict == "AMBIGUOUS"
    assert parsed[0].reason == "malformed-head"


def test_malformed_decide_line_is_ambiguous():
    body = (
        f"{mod.verdict_marker(THREAD)}\n"
        "CODEX-THREAD: RESOLVE\n"
        "CODEX-THREAD: DECIDE reason=needs-decision\n"
        f"HEAD: {SHA}\n"
    )
    parsed = mod.parse_thread_verdicts(body)
    assert parsed[0].verdict == "AMBIGUOUS"
    assert parsed[0].reason == "malformed"


def test_linux_gates_skipped_is_not_green():
    rollup = {
        "state": "SUCCESS",
        "contexts": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "linux-gates",
                    "conclusion": "SKIPPED",
                    "status": "COMPLETED",
                    "isRequired": False,
                }
            ],
        },
    }
    assert mod.required_checks_green(rollup) == (False, "checks=linux-gates-not-green")


def test_required_check_named_resolve_is_not_ignored():
    rollup = {
        "state": "FAILURE",
        "contexts": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "linux-gates",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "isRequired": True,
                },
                {
                    "__typename": "CheckRun",
                    "name": "resolve",
                    "conclusion": "FAILURE",
                    "status": "COMPLETED",
                    "isRequired": True,
                },
            ],
        },
    }
    assert mod.required_checks_green(rollup) == (False, "checks=required-not-green")


def test_same_second_current_head_beats_old_head():
    old_head = comment(CODEX, verdict_body("RESOLVE", sha=SHA2), NOW)
    current = comment(CODEX, verdict_body("RESOLVE", sha=SHA), NOW)
    latest = mod.latest_verdicts([old_head, current])
    t = thread(comments=[old_head, current])
    reason, verdict = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "resolve"
    assert verdict is not None and verdict.sha == SHA


def test_later_old_head_still_stale():
    current = comment(CODEX, verdict_body("RESOLVE", sha=SHA), NOW - timedelta(seconds=1))
    later_old = comment(CODEX, verdict_body("RESOLVE", sha=SHA2), NOW)
    latest = mod.latest_verdicts([current, later_old])
    t = thread(comments=[current, later_old])
    reason, _ = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "stale-sha"


def test_same_second_headless_still_vetoes():
    current = comment(CODEX, verdict_body("RESOLVE"), NOW)
    headless = comment(
        CODEX,
        f"{mod.verdict_marker(THREAD)}\nCODEX-THREAD: DECIDE\n",
        NOW,
    )
    latest = mod.latest_verdicts([current, headless])
    t = thread(comments=[current, headless])
    reason, verdict = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "ambiguous"
    assert verdict is not None and verdict.verdict == "AMBIGUOUS"


def test_newer_headless_verdict_cancels_older_resolve():
    older = comment(CODEX, verdict_body("RESOLVE"), NOW - timedelta(hours=1))
    newer = comment(
        CODEX,
        f"{mod.verdict_marker(THREAD)}\nCODEX-THREAD: DECIDE\n",
        NOW,
    )
    latest = mod.latest_verdicts([older, newer])
    t = thread(comments=[older, newer])
    reason, verdict = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "ambiguous"
    assert verdict is not None and verdict.verdict == "AMBIGUOUS"


def test_path_is_protected_over_protects_order_audit_surfaces():
    assert mod.path_is_protected("app/quant/execution/sim_venue.py")
    assert mod.path_is_protected("app/storage/parquet_store.py")
    assert mod.path_is_protected("app/schemas/models/order.py")
    assert mod.path_is_protected("app/schemas/models/audit_entry.py")
    assert not mod.path_is_protected("app/quant/costs.py")
    assert not mod.path_is_protected("AGENTS.md")
    assert not mod.path_is_protected("")


@pytest.mark.parametrize(
    "path",
    [
        "app/risk/limits.py",
        "app/brokers/gateway.py",
        "app/orchestrator/app.py",
        "app/storage/repositories.py",
        "app/storage/engine.py",
        "app/storage/audit.py",
        "app/quant/execution/autoloop.py",
        "app/schemas/models/order.py",
        "app/schemas/models/audit_entry.py",
        "order.py",
        "audit_entry.py",
    ],
)
def test_protected_path_never_resolves(path: str):
    t = thread(comments=[comment(CODEX, verdict_body())])
    t["path"] = path
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "protected-path"
    gh = ready_gh(threads=[t], reviews=[comment(CODEX, verdict_body())])
    gh.pr_by_number[12]["reviewThreads"]["nodes"][0]["path"] = path
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=protected-path" in line for line in logs)


def test_unprotected_docs_path_can_resolve():
    t = thread(comments=[comment(CODEX, verdict_body())])
    t["path"] = "AGENTS.md"
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "resolve"


def test_resolved_thread_stays_resolved_on_new_head():
    """A thread closed on SHA is not reopened when the head moves to SHA2."""
    t = thread(resolved=True, comments=[comment(CODEX, verdict_body())])
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, _ = mod.classify_thread(t, head_sha=SHA2, latest=latest, issue_comments=[])
    assert reason == "already-resolved"


def test_fork_pr_skipped():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.pr_by_number[12]["isCrossRepository"] = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=fork" in line for line in logs)


def test_closed_pr_during_head_refresh_does_not_resolve():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.pr_by_number[12]["state"] = "OPEN"
    original = gh.graphql

    def close_after_first(query, variables, token=None):
        result = original(query, variables, token)
        if "statusCheckRollup" in query:
            gh.pr_by_number[12]["state"] = "CLOSED"
        return result

    gh.graphql = close_after_first  # type: ignore[method-assign]
    logs = mod.scan_pr(pr_payload(threads=[thread()]), cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=closed-or-draft" in line or "stale-sha" in line for line in logs)


def _latest_from_thread(t: dict, extra: list[dict] | None = None):
    items = mod.thread_comments(t) + (extra or [])
    return mod.latest_verdicts(items)


def test_classify_human_root_does_not_resolve():
    t = thread(
        comments=[
            comment("alice", "REQUEST_CHANGES: this still needs a human decision"),
            comment(CODEX, verdict_body()),
        ]
    )
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "human-root"


def test_classify_empty_thread_is_human_root():
    t = thread(comments=[])
    latest = mod.latest_verdicts([comment(CODEX, verdict_body())])
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=latest, issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "human-root"


def test_classify_human_pat_owner_root_is_human_root():
    t = thread(
        comments=[
            comment("NotSkittlz", "REQUEST_CHANGES: still needs a human decision"),
            comment(CODEX, verdict_body()),
        ]
    )
    reason, _ = mod.classify_thread(
        t,
        head_sha=SHA,
        latest=_latest_from_thread(t),
        issue_comments=[],
        pat_login="NotSkittlz",
    )
    assert reason == "human-root"


def test_classify_devin_root_can_resolve():
    t = thread(
        comments=[
            comment(DEVIN, "please fix", NOW - timedelta(seconds=60)),
            comment(CODEX, verdict_body()),
        ]
    )
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "resolve"


def test_scan_skips_human_opened_thread():
    gh = ready_gh(
        threads=[thread(comments=[comment("alice", "REQUEST_CHANGES major")])],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.replies == []
    assert any("skip=human-root" in line for line in logs)


def test_classify_later_owner_reply_is_human_veto():
    later = NOW + timedelta(seconds=5)
    t = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment("alice", "this still needs a human decision", later),
        ]
    )
    reason, verdict = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "human-veto"
    assert verdict is not None and verdict.verdict == "RESOLVE"


def test_classify_same_second_human_reply_is_human_veto():
    t = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment("alice", "leave this open"),
        ]
    )
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "human-veto"


def test_classify_earlier_human_reply_on_bot_thread_does_not_veto():
    t = thread(
        comments=[
            comment(DEVIN, "please fix", NOW - timedelta(seconds=60)),
            comment("alice", "please also fix tests", NOW - timedelta(seconds=30)),
            comment(CODEX, verdict_body()),
        ]
    )
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "resolve"


def test_classify_later_bot_reply_does_not_veto():
    t = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment("devin-ai-integration[bot]", "still looking", NOW + timedelta(seconds=5)),
        ]
    )
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "resolve"


def test_classify_untrusted_association_does_not_veto():
    t = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment(
                "newbie",
                "please keep this open",
                NOW + timedelta(seconds=5),
                association="FIRST_TIME_CONTRIBUTOR",
            ),
        ]
    )
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "resolve"


def test_classify_missing_timestamp_human_is_veto():
    human = comment("alice", "hold", NOW + timedelta(seconds=5))
    human["createdAt"] = ""
    human["created_at"] = ""
    t = thread(comments=[comment(CODEX, verdict_body()), human])
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "human-veto"


def test_classify_retry_resolve_later_human_is_veto():
    t = thread(
        comments=[
            comment(CODEX, verdict_body()),
            comment(PAT_LOGIN, mod.cite_marker(THREAD, SHA)),
            comment("alice", "leave this open", NOW + timedelta(seconds=5)),
        ]
    )
    reason, _ = mod.classify_thread(
        t, head_sha=SHA, latest=_latest_from_thread(t), issue_comments=[], pat_login=PAT_LOGIN
    )
    assert reason == "human-veto"


def test_scan_skips_later_owner_reply():
    gh = ready_gh(
        threads=[
            thread(
                comments=[
                    comment(CODEX, verdict_body()),
                    comment("alice", "this still needs a human decision", NOW + timedelta(seconds=5)),
                ]
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.replies == []
    assert any("skip=human-veto" in line for line in logs)


def test_scan_rechecks_thread_tail_before_cite():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.thread_tails[THREAD] = [
        comment("alice", "this still needs a human decision", NOW + timedelta(seconds=2)),
    ]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.replies == []
    assert gh.thread_tail_calls >= 1
    assert any("skip=human-veto" in line for line in logs)


def test_scan_rechecks_thread_tail_after_cite_before_resolve():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.thread_tail_queue[THREAD] = [
        [],
        [comment("alice", "stop", NOW + timedelta(seconds=2))],
    ]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.replies  # cite already posted
    assert gh.thread_tail_calls >= 2
    assert any("skip=human-veto" in line for line in logs)


def test_scan_tail_error_is_human_veto():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.fail_thread_tail = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=human-veto" in line for line in logs)


def test_reply_and_resolve_without_stamp_is_human_veto():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    result = mod.reply_and_resolve(
        gh,
        cfg(),
        number=12,
        thread_id=THREAD,
        sha=SHA,
        excerpt="x",
        pat_login=PAT_LOGIN,
        after=None,
    )
    assert result == "human-veto"
    assert gh.resolves == []


def test_scan_resolves_still_revalidates_empty_tail():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == [THREAD]
    assert gh.thread_tail_calls >= 2
    assert any("resolve=posted" in line for line in logs)


def test_scan_incomplete_tail_window_is_human_veto():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    later = NOW + timedelta(seconds=1)
    gh.thread_tails[THREAD] = [
        comment("devin-ai-integration[bot]", f"bot {i}", later) for i in range(40)
    ]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=human-veto" in line for line in logs)


def test_scan_complete_full_tail_window_can_resolve():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.thread_tails[THREAD] = [
        comment(CODEX, "prior", NOW - timedelta(seconds=1)),
        *[
            comment("devin-ai-integration[bot]", f"bot {i}", NOW + timedelta(seconds=1))
            for i in range(39)
        ],
    ]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == [THREAD]
    assert any("resolve=posted" in line for line in logs)


def test_scan_rechecks_same_second_human_on_tail():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.thread_tails[THREAD] = [comment("alice", "leave this open")]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert gh.replies == []
    assert gh.thread_tail_calls >= 1
    assert any("skip=human-veto" in line for line in logs)


def test_scan_full_tail_same_second_as_stamp_is_human_veto():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.thread_tails[THREAD] = [
        comment("devin-ai-integration[bot]", f"bot {i}") for i in range(40)
    ]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=human-veto" in line for line in logs)


def test_scan_has_previous_page_without_reaching_stamp_is_human_veto():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.thread_tails[THREAD] = [
        comment("devin-ai-integration[bot]", "bot", NOW + timedelta(seconds=1))
    ]
    gh.thread_tail_has_previous[THREAD] = True
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=human-veto" in line for line in logs)
