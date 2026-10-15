"""Unit tests for the in-repo stuck-thread watcher / Codex mediator."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "stuck_thread_watch.py"
WORKFLOW = ROOT / ".github" / "workflows" / "stuck-thread-watch.yml"
DEVIN = "devin-ai-integration[bot]"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def load():
    spec = importlib.util.spec_from_file_location("stuck_thread_watch", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["stuck_thread_watch"] = module
    spec.loader.exec_module(module)
    return module


mod = load()


def cfg(**overrides) -> object:
    values = dict(
        repository="CSL-Laboratories/ai-investment-cockpit",
        owner="CSL-Laboratories",
        name="ai-investment-cockpit",
        pr_number="",
        stuck_hours=6,
        max_per_pr_per_day=3,
        quote_limit=80,
        devin_login=DEVIN,
        claude_logins=("claude[bot]", "claude"),
        file_linear=True,
        apply_pr_label=False,
        needs_decision_label="needs-decision",
        linear_team_key="CSL",
        linear_labels=("needs-decision", "auto-filed"),
        linear_project="ai-investment-cockpit",
        linear_repo_label="ai-investment-cockpit",
        codex_pat="pat",
        linear_api_key="lin",
        event_name="schedule",
        event_path="",
    )
    values.update(overrides)
    return mod.Config(**values)


def comment(login: str, body: str, created: datetime | None = None, url: str = "https://ex/r") -> dict:
    stamp = (created or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "author": {"login": login},
        "body": body,
        "createdAt": stamp,
        "url": url,
        "path": "AGENTS.md",
        "line": 12,
    }


def thread(
    thread_id: str = "PRRT_kwDOabc",
    comments: list[dict] | None = None,
    resolved: bool = False,
) -> dict:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": "AGENTS.md",
        "line": 12,
        "comments": {"nodes": comments or []},
    }


class FakeGitHub(mod.GitHub):
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.labels: list[tuple[str, dict]] = []
        self.prs: list[dict] = []
        self.pr_by_number: dict[int, dict] = {}
        self.issue_comments: dict[int, list[dict]] = {}

    def graphql(self, query: str, variables: dict, token: str | None = None) -> object:
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
        if method == "POST" and path.endswith("/comments"):
            self.posts.append((path, body or {}))
            return {"id": 1}
        if method == "POST" and path.endswith("/labels"):
            self.labels.append((path, body or {}))
            return []
        return None

    def paginate_rest(self, path: str, token: str | None = None) -> list:
        number = int(path.split("/issues/")[1].split("/")[0])
        return list(self.issue_comments.get(number, []))


class FakeLinear(mod.Linear):
    def __init__(self, existing: dict | None = None) -> None:
        super().__init__("lin", post=self._post)
        self.existing = existing
        self.created: list[dict] = []

    def _post(self, query: str, variables: dict) -> dict:
        if "issueCreate" in query:
            self.created.append(variables["input"])
            return {"issueCreate": {"success": True, "issue": {"url": "https://linear.app/i/CSL-99"}}}
        if "issues(" in query:
            nodes = [self.existing] if self.existing else []
            return {"issues": {"nodes": nodes}}
        if "teams(" in query:
            return {
                "teams": {
                    "nodes": [
                        {
                            "id": "team-1",
                            "states": {"nodes": [{"id": "triage-1", "type": "triage", "name": "Triage"}]},
                        }
                    ]
                }
            }
        if "issueLabels" in query:
            return {"issueLabels": {"nodes": [{"id": f"lab-{variables['name']}", "name": variables["name"]}]}}
        if "projects(" in query:
            return {"projects": {"nodes": [{"id": "proj-1", "name": "ai-investment-cockpit"}]}}
        return {}


def test_marker_round_trip():
    assert mod.marker("PRRT_kwDOabc") == "<!-- codex-mediation:PRRT_kwDOabc -->"
    assert "PRRT_kwDOabc" in mod.markers_in("hello <!-- codex-mediation:PRRT_kwDOabc -->")


def test_marker_rejects_unsafe_id():
    with pytest.raises(ValueError):
        mod.marker("bad id")


def test_needs_human_phrases():
    assert mod.claude_needs_human("I couldn't fix this; it needs a human.")
    assert mod.claude_needs_human("left the thread open for Lucas to confirm")
    assert mod.claude_needs_human("could not verify the workspace setting")
    assert not mod.claude_needs_human("Fixed in abc123. Gates passed.")


def test_classify_requires_devin_then_claude():
    item, reason = mod.classify_thread(
        thread(comments=[comment("alice", "looks wrong")]),
        cfg=cfg(),
        now=NOW,
    )
    assert item is None
    assert reason == "not-devin"


def test_classify_needs_human_immediately():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "Linear status automation needs clarification."),
                comment("claude[bot]", "Not changed. Couldn't verify; needs a human."),
            ]
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is not None
    assert reason == "needs-human"
    assert item.path == "AGENTS.md"


def test_classify_stale_after_stuck_hours():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "Please rename this helper.", created=NOW - timedelta(hours=10)),
                comment("claude[bot]", "Renamed in abc.", created=NOW - timedelta(hours=7)),
            ]
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is not None
    assert reason == "stale-claude"


def test_classify_fresh_claude_not_stuck():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "Please rename this helper.", created=NOW - timedelta(hours=1)),
                comment("claude[bot]", "Renamed in abc.", created=NOW - timedelta(minutes=10)),
            ]
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is None
    assert reason == "fresh-claude"


def test_classify_skips_resolved():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "finding"),
                comment("claude[bot]", "needs a human"),
            ],
            resolved=True,
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is None
    assert reason == "resolved"


def test_select_skips_already_mediated():
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 7,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://github.com/org/repo/pull/7",
        "title": "demo",
        "reviewThreads": {"nodes": [stuck]},
    }
    comments = [{"body": "<!-- codex-mediation:PRRT_kwDOabc -->", "created_at": "2026-10-15T00:00:00Z"}]
    chosen, audit = mod.select_stuck(pr, comments, cfg(), NOW)
    assert chosen == []
    assert any("already-mediated" in line for line in audit)


def test_daily_cap_and_missing_pat(tmp_path):
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 4,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://github.com/org/repo/pull/4",
        "title": "demo",
        "reviewThreads": {"nodes": [stuck]},
    }
    gh.pr_by_number[4] = pr
    gh.issue_comments[4] = [
        {"body": "<!-- codex-mediation:old1 -->", "created_at": "2026-10-15T10:00:00Z"},
        {"body": "<!-- codex-mediation:old2 -->", "created_at": "2026-10-15T11:00:00Z"},
        {"body": "<!-- codex-mediation:old3 -->", "created_at": "2026-10-15T11:30:00Z"},
    ]
    logs = mod.mediate_pr(pr, cfg(max_per_pr_per_day=3), gh, FakeLinear(), NOW)
    assert any("over-cap" in line for line in logs)
    assert gh.posts == []

    logs = mod.mediate_pr(pr, cfg(codex_pat="", max_per_pr_per_day=99), gh, FakeLinear(), NOW)
    assert any("skipped-no-secret" in line for line in logs)
    assert gh.posts == []


def test_run_posts_one_codex_comment_and_files_linear():
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "Linear status automation needs clarification."),
            comment("claude[bot]", "Couldn't verify; left the thread open."),
        ]
    )
    pr = {
        "number": 9,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/9",
        "title": "docs",
        "reviewThreads": {"nodes": [stuck]},
    }
    gh.prs = [pr]
    gh.pr_by_number[9] = pr
    linear = FakeLinear()
    code = mod.run(cfg=cfg(), gh=gh, linear=linear, now=NOW)
    assert code == 0
    assert len(gh.posts) == 1
    body = gh.posts[0][1]["body"]
    assert body.startswith("@codex review")
    assert "<!-- codex-mediation:PRRT_kwDOabc -->" in body
    assert "UNTRUSTED DATA" in body
    assert "do **not** push" in body.lower() or "Do **not** push" in body
    assert len(linear.created) == 1
    assert "<!-- codex-mediation:PRRT_kwDOabc -->" in linear.created[0]["description"]
    assert linear.created[0]["stateId"] == "triage-1"


def test_linear_dedupe_skips_create():
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 2,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://ex/2",
        "title": "t",
        "reviewThreads": {"nodes": [stuck]},
    }
    gh.pr_by_number[2] = pr
    existing = {
        "id": "x",
        "identifier": "CSL-77",
        "url": "https://linear.app/i/CSL-77",
        "description": "<!-- codex-mediation:PRRT_kwDOabc -->",
    }
    linear = FakeLinear(existing=existing)
    logs = mod.mediate_pr(pr, cfg(codex_pat=""), gh, linear, NOW)
    assert linear.created == []
    assert any("linear=https://linear.app/i/CSL-77" in line or "linear=existing" in line for line in logs)


def test_personal_repo_applies_label_not_linear():
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 3,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://ex/3",
        "title": "t",
        "reviewThreads": {"nodes": [stuck]},
    }
    logs = mod.mediate_pr(
        pr,
        cfg(file_linear=False, apply_pr_label=True, codex_pat=""),
        gh,
        None,
        NOW,
    )
    assert gh.labels
    assert gh.labels[0][1]["labels"] == ["needs-decision"]
    assert any("label=needs-decision" in line for line in logs)


def test_cheap_comment_filter():
    assert mod.cheap_comment_event(
        {"comment": {"user": {"login": "claude[bot]"}}, "issue": {"pull_request": {}}},
        "issue_comment",
        devin_login=DEVIN,
        claude_logins=["claude[bot]"],
    )
    assert not mod.cheap_comment_event(
        {"comment": {"user": {"login": "alice"}}, "issue": {"pull_request": {}}},
        "issue_comment",
        devin_login=DEVIN,
        claude_logins=["claude[bot]"],
    )
    assert not mod.cheap_comment_event(
        {"comment": {"user": {"login": "claude[bot]"}}, "issue": {}},
        "issue_comment",
        devin_login=DEVIN,
        claude_logins=["claude[bot]"],
    )


def test_quote_strips_fences_and_truncates():
    text = mod.quote_untrusted("```rm -rf```" + ("x" * 200), 20)
    assert "```" not in text
    assert "truncated" in text


def test_parse_paginated_json_concatenated_pages():
    raw = '[{"body": "a"}]\n[{"body": "b"}]'
    assert [c["body"] for c in mod.parse_paginated_json(raw)] == ["a", "b"]


def test_load_config_reads_env():
    loaded = mod.load_config(
        {
            "GITHUB_REPOSITORY": "CSL-Laboratories/ai-investment-cockpit",
            "STUCK_HOURS": "8",
            "FILE_LINEAR": "true",
            "APPLY_PR_LABEL": "false",
            "CODEX_TRIGGER_PAT": "  ",
        }
    )
    assert loaded.owner == "CSL-Laboratories"
    assert loaded.stuck_hours == 8
    assert loaded.file_linear is True
    assert loaded.codex_pat == ""


def test_script_never_resolves_or_pushes():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "never resolves" in text.lower()
    assert "git push" not in text
    assert "mergePullRequest" not in text
    assert "resolveReviewThread" not in text
    assert "pulls/" not in text or "issues/" in text


def test_workflow_structure():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: Stuck thread watch" in text
    assert 'cron: "23 */2 * * *"' in text
    assert "workflow_dispatch:" in text
    assert "pr_number:" in text
    assert "issue_comment:" in text
    assert "pull_request_review_comment:" in text
    assert "runs-on: ubuntu-latest" in text
    assert "blacksmith-" not in text
    assert "python -m pytest" not in text
    assert "CODEX_TRIGGER_PAT: ${{ secrets.CODEX_TRIGGER_PAT }}" in text
    assert "LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}" in text
    assert "STUCK_HOURS:" in text.split("jobs:")[0]
    assert "python3 .github/scripts/stuck_thread_watch.py" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
