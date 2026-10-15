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
SHA = "a" * 40
SHA2 = "b" * 40
NOW = datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)
THREAD = "PRRT_kwDOabc123"


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


def comment(login: str, body: str, created: datetime | None = None) -> dict:
    stamp = (created or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "author": {"login": login},
        "user": {"login": login},
        "body": body,
        "createdAt": stamp,
        "created_at": stamp,
        "submittedAt": stamp,
        "submitted_at": stamp,
    }


def thread(
    thread_id: str = THREAD,
    comments: list[dict] | None = None,
    resolved: bool = False,
) -> dict:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": "AGENTS.md",
        "comments": {"nodes": comments or []},
    }


def verdict_body(kind: str = "RESOLVE", sha: str = SHA, tid: str = THREAD, extra: str = "") -> str:
    html = mod.verdict_marker(tid, sha, kind)
    line = mod.visible_verdict_line(tid, sha, kind, reason="needs-decision" if kind == "KEEP" else "")
    return f"The fix is in.\n\n{html}\n{line}\n{extra}"


class FakeGitHub(mod.GitHub):
    def __init__(self) -> None:
        super().__init__(run=self._unused)
        self.prs: list[dict] = []
        self.pr_by_number: dict[int, dict] = {}
        self.issue_comments: dict[int, list[dict]] = {}
        self.reviews: dict[int, list[dict]] = {}
        self.rollup_by_sha: dict[str, dict | None] = {SHA: {"state": "SUCCESS"}}
        self.head_by_number: dict[int, str] = {}
        self.head_queue: dict[int, list[str]] = {}
        self.resolves: list[str] = []
        self.replies: list[tuple[str, str]] = []
        self.fail_resolve = False
        self.fail_reply = False

    def _unused(self, *args, **kwargs):
        raise AssertionError("real gh invoked")

    def graphql(self, query: str, variables: dict, token: str | None = None) -> object:
        if "resolveReviewThread" in query:
            if token != "pat":
                raise AssertionError("resolve must use CODEX_TRIGGER_PAT")
            if self.fail_resolve:
                raise RuntimeError("github graphql errors")
            tid = str(variables["id"])
            self.resolves.append(tid)
            return {"resolveReviewThread": {"thread": {"id": tid, "isResolved": True}}}
        if "addPullRequestReviewThreadReply" in query:
            if token != "pat":
                raise AssertionError("reply must use CODEX_TRIGGER_PAT")
            if self.fail_reply:
                raise RuntimeError("github graphql errors")
            tid = str(variables["threadId"])
            body = str(variables["body"])
            self.replies.append((tid, body))
            return {"addPullRequestReviewThreadReply": {"comment": {"id": "C1", "url": "https://ex/c"}}}
        if "statusCheckRollup" in query:
            oid = str(variables.get("oid") or "")
            return {"repository": {"object": {"oid": oid, "statusCheckRollup": self.rollup_by_sha.get(oid)}}}
        if "headRefOid" in query and "reviewThreads" not in query:
            number = int(variables["number"])
            queued = self.head_queue.get(number) or []
            if queued:
                sha = queued.pop(0)
            else:
                sha = self.head_by_number.get(number) or SHA
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
        raise AssertionError(f"unexpected REST {method} {path}")

    def paginate_rest(self, path: str, token: str | None = None) -> list:
        if "/reviews" in path:
            number = int(path.split("/pulls/")[1].split("/")[0])
            return list(self.reviews.get(number, []))
        number = int(path.split("/issues/")[1].split("/")[0])
        return list(self.issue_comments.get(number, []))


def pr_payload(number: int = 12, *, sha: str = SHA, threads: list[dict] | None = None, draft: bool = False) -> dict:
    return {
        "number": number,
        "isDraft": draft,
        "state": "OPEN",
        "url": f"https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/{number}",
        "title": "CSL-179 demo",
        "headRefOid": sha,
        "reviewThreads": {"nodes": threads or []},
    }


def ready_gh(*, threads: list[dict], reviews: list[dict] | None = None, sha: str = SHA) -> FakeGitHub:
    gh = FakeGitHub()
    payload = pr_payload(threads=threads, sha=sha)
    gh.prs = [{"number": 12, "isDraft": False, "state": "OPEN"}]
    gh.pr_by_number[12] = payload
    gh.head_by_number[12] = sha
    gh.reviews[12] = reviews or []
    gh.rollup_by_sha[sha] = {"state": "SUCCESS"}
    return gh


def test_marker_round_trip():
    html = mod.verdict_marker(THREAD, SHA, "RESOLVE")
    assert html == f"<!-- codex-thread-verdict:{THREAD}:{SHA}:RESOLVE -->"
    line = mod.visible_verdict_line(THREAD, SHA, "KEEP", reason="needs-decision")
    assert "CODEX-THREAD: KEEP" in line
    assert f"thread={THREAD}" in line
    assert f"sha={SHA}" in line
    parsed = mod.parse_thread_verdicts(f"{html}\n{mod.visible_verdict_line(THREAD, SHA, 'RESOLVE')}")
    assert parsed == [mod.ThreadVerdict(THREAD, SHA, "RESOLVE", source="html")]


def test_marker_rejects_unsafe_id():
    with pytest.raises(ValueError):
        mod.verdict_marker("bad id", SHA, "RESOLVE")
    with pytest.raises(ValueError):
        mod.verdict_marker(THREAD, "notasha", "RESOLVE")
    with pytest.raises(ValueError):
        mod.resolved_marker(THREAD, "zzz")


def test_keep_wins_when_body_mixes_verdicts():
    body = (
        f"{mod.verdict_marker(THREAD, SHA, 'RESOLVE')}\n"
        f"{mod.visible_verdict_line(THREAD, SHA, 'KEEP', reason='ambiguous')}\n"
    )
    parsed = mod.parse_thread_verdicts(body)
    assert len(parsed) == 1
    assert parsed[0].verdict == "KEEP"
    assert parsed[0].reason == "ambiguous"


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
    older = comment(CODEX, verdict_body("KEEP"), NOW - timedelta(hours=1))
    newer = comment(CODEX, verdict_body("RESOLVE"), NOW)
    latest = mod.latest_verdicts([older, newer])
    assert latest[(THREAD, SHA)].verdict.verdict == "RESOLVE"


def test_non_codex_markers_are_ignored():
    fake = comment("alice", verdict_body("RESOLVE"))
    latest = mod.latest_verdicts([fake])
    assert latest == {}


def test_required_checks_fail_closed():
    assert mod.required_checks_green({"state": "SUCCESS"}) == (True, "checks=green")
    assert mod.required_checks_green({"state": "PENDING"})[0] is False
    assert mod.required_checks_green({"state": "FAILURE"})[0] is False
    assert mod.required_checks_green(None)[0] is False
    assert mod.required_checks_green({})[0] is False


def test_classify_keep_does_not_resolve():
    t = thread(comments=[comment(CODEX, verdict_body("KEEP"))])
    latest = mod.latest_verdicts(mod.thread_comments(t))
    reason, verdict = mod.classify_thread(t, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason.startswith("keep:")
    assert verdict is not None and verdict.verdict == "KEEP"


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
            comment("github-actions[bot]", mod.resolved_marker(THREAD, SHA)),
        ]
    )
    latest = mod.latest_verdicts(mod.thread_comments(marked))
    reason, _ = mod.classify_thread(marked, head_sha=SHA, latest=latest, issue_comments=[])
    assert reason == "already-marked"


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
    assert mod.resolved_marker(THREAD, SHA) in gh.replies[0][1]
    assert any("resolve=posted" in line for line in logs)


def test_scan_skips_pending_checks():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.rollup_by_sha[SHA] = {"state": "PENDING"}
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=checks=pending" in line for line in logs)


def test_scan_skips_keep_and_leaves_needs_decision_path():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body("KEEP"))])
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=keep:" in line for line in logs)


def test_scan_skips_gate_ready_without_thread_marker():
    gh = ready_gh(
        threads=[thread()],
        reviews=[comment(CODEX, "Settled.\n\nCODEX-GATE: READY\n")],
    )
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("skip=no-verdict" in line for line in logs)


def test_scan_never_resolves_on_stale_head_during_mutation():
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.head_queue[12] = [SHA, SHA, SHA2]
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(), gh, NOW)
    assert gh.resolves == []
    assert any("stale-sha" in line for line in logs)


def test_idempotent_second_run():
    first = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    mod.scan_pr(first.pr_by_number[12], cfg(), first, NOW)
    assert first.resolves == [THREAD]
    reply_body = first.replies[0][1]
    second = ready_gh(
        threads=[
            thread(
                resolved=True,
                comments=[comment("github-actions[bot]", reply_body)],
            )
        ],
        reviews=[comment(CODEX, verdict_body())],
    )
    logs = mod.scan_pr(second.pr_by_number[12], cfg(), second, NOW)
    assert second.resolves == []
    assert any("already-resolved" in line for line in logs)


def test_daily_cap():
    prior = [
        comment(
            "github-actions[bot]",
            mod.resolved_marker(f"PRRT_old{i}", SHA),
            NOW - timedelta(minutes=i),
        )
        for i in range(20)
    ]
    gh = ready_gh(threads=[thread()], reviews=[comment(CODEX, verdict_body())])
    gh.issue_comments[12] = prior
    logs = mod.scan_pr(gh.pr_by_number[12], cfg(max_per_pr_per_day=20), gh, NOW)
    assert gh.resolves == []
    assert any("skip=over-cap" in line for line in logs)


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


def test_reply_cites_ruling_and_sha():
    body = mod.build_resolve_reply(THREAD, SHA, "Fix already present on head.")
    assert SHA[:12] in body
    assert "no owner/human decision" in body
    assert "Fix already present" in body
    assert mod.resolved_marker(THREAD, SHA) in body


def test_mediation_instructions_forbid_self_resolve():
    text = mod.mediation_verdict_instructions(THREAD, SHA)
    assert "do **not** call resolveReviewThread" in text
    assert "codex-thread-verdict" in text
    assert "CODEX-THREAD:" in text


def test_script_never_posts_codex_review_or_pushes():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "@codex review" not in text or "never posts" in text.lower()
    assert "git push" not in text
    assert "mergePullRequest" not in text
    assert "resolveReviewThread" in text
    assert "CODEX_TRIGGER_PAT" in text


def test_workflow_structure():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: Codex auto-resolve" in text
    assert "pull_request_review:" in text
    assert "chatgpt-codex-connector[bot]" in text
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
    assert "resolveReviewThread" in text
    assert "Never run the PR head with the PAT" in text


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
    assert "Codex auto-resolve" in summary.read_text(encoding="utf-8")


def test_parse_paginated_concatenated_arrays():
    raw = json.dumps([{"id": 1}]) + json.dumps([{"id": 2}])
    items = mod.parse_paginated_json(raw)
    assert [i["id"] for i in items] == [1, 2]
