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
        self.updated: list[dict] = []

    def _post(self, query: str, variables: dict) -> dict:
        if "issueCreate" in query:
            self.created.append(variables["input"])
            return {"issueCreate": {"success": True, "issue": {"url": "https://linear.app/i/CSL-99"}}}
        if "issueUpdate" in query:
            self.updated.append({"id": variables["id"], **(variables.get("input") or {})})
            return {
                "issueUpdate": {
                    "success": True,
                    "issue": {
                        "id": variables["id"],
                        "url": (self.existing or {}).get("url") or "https://linear.app/i/CSL-77",
                        "identifier": (self.existing or {}).get("identifier") or "CSL-77",
                    },
                }
            }
        if "issues(" in query:
            nodes: list[dict] = []
            if self.existing:
                desc = str(self.existing.get("description") or "")
                title = str(self.existing.get("title") or "")
                contains = variables.get("contains")
                eq_title = variables.get("title")
                matched = True
                if contains is not None and contains not in desc:
                    matched = False
                if eq_title is not None and eq_title != title:
                    matched = False
                if matched:
                    nodes = [self.existing]
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
    assert mod.pr_marker("CSL-Laboratories/ai-investment-cockpit", 9) == (
        "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#9 -->"
    )
    assert mod.pr_rollup_title("CSL-Laboratories/ai-investment-cockpit", 9) == (
        "Stuck PR threads: ai-investment-cockpit#9"
    )


def test_marker_rejects_unsafe_id():
    with pytest.raises(ValueError):
        mod.marker("bad id")


def test_needs_human_phrases():
    assert mod.claude_needs_human("I couldn't fix this; it needs a human.")
    assert mod.claude_needs_human("left the thread open for Lucas to confirm")
    assert not mod.claude_needs_human("could not verify the workspace setting")
    assert not mod.claude_needs_human("Fixed in abc123. Gates passed.")


def test_login_aliases_accept_graphql_and_rest():
    assert mod.is_devin("devin-ai-integration", DEVIN)
    assert mod.is_devin("devin-ai-integration[bot]", DEVIN)
    assert mod.is_claude("claude", ["claude[bot]", "claude"])
    assert mod.is_claude("claude[bot]", ["claude[bot]"])
    assert not mod.is_devin("alice", DEVIN)


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


def test_classify_stale_after_stuck_hours_without_fix_claim():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "Please rename this helper.", created=NOW - timedelta(hours=10)),
                comment("claude[bot]", "I still disagree with the rename.", created=NOW - timedelta(hours=7)),
            ]
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is not None
    assert reason == "stale-claude"


def test_classify_graphql_logins_without_bot_suffix():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment("devin-ai-integration", "finding"),
                comment("claude", "This needs a human."),
            ]
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is not None
    assert reason == "needs-human"


def test_classify_ignores_earlier_needs_human_after_fix():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "finding", created=NOW - timedelta(hours=10)),
                comment("claude[bot]", "needs a human", created=NOW - timedelta(hours=8)),
                comment("claude[bot]", "Fixed in abc123. Gates passed.", created=NOW - timedelta(hours=7)),
            ]
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is None
    assert reason == "claude-fixed"


def test_classify_aged_after_devin_replies_to_fix():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "finding", created=NOW - timedelta(hours=10)),
                comment("claude[bot]", "Fixed in abc.", created=NOW - timedelta(hours=8)),
                comment(DEVIN, "Still wrong.", created=NOW - timedelta(hours=7)),
            ]
        ),
        cfg=cfg(),
        now=NOW,
    )
    assert item is not None
    assert reason == "stale-devin-replied"


def test_classify_fresh_claude_not_stuck():
    item, reason = mod.classify_thread(
        thread(
            comments=[
                comment(DEVIN, "Please rename this helper.", created=NOW - timedelta(hours=1)),
                comment("claude[bot]", "Still looking at this.", created=NOW - timedelta(minutes=10)),
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
    assert len(chosen) == 1
    assert chosen[0].already_mediated is True
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
    linear = FakeLinear()
    logs = mod.mediate_pr(pr, cfg(max_per_pr_per_day=3), gh, linear, NOW)
    assert any("over-cap" in line for line in logs)
    assert any("linear=skipped-no-actionable" in line for line in logs)
    assert gh.posts == []
    assert linear.created == []
    assert linear.updated == []

    linear = FakeLinear()
    logs = mod.mediate_pr(pr, cfg(codex_pat="", max_per_pr_per_day=99), gh, linear, NOW)
    assert any("skipped-no-secret" in line for line in logs)
    assert any("linear=skipped-no-actionable" in line for line in logs)
    assert gh.posts == []
    assert linear.created == []
    assert linear.updated == []


def test_linear_writes_when_one_cap_slot_remains():
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
    ]
    linear = FakeLinear()
    logs = mod.mediate_pr(pr, cfg(max_per_pr_per_day=3), gh, linear, NOW)
    assert any("mediate=posted" in line for line in logs)
    assert not any("over-cap" in line for line in logs)
    assert not any("linear=skipped-no-actionable" in line for line in logs)
    assert len(gh.posts) == 1
    assert len(linear.created) == 1
    assert linear.created[0]["title"] == "Stuck PR threads: ai-investment-cockpit#4"


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
    assert "Linear-first (CSL-88)" in body
    assert "any deep-research source the team has" in body
    assert "web research (Parallel)" not in body
    assert "CSL-Laboratories/csl-research as a submissions record through a PR" in body
    assert "Never commit research content to this repo" in body
    assert "<!-- codex-mediation:PRRT_kwDOabc -->" in body
    assert "UNTRUSTED DATA" in body
    assert "do **not** push" in body.lower() or "Do **not** push" in body
    assert len(linear.created) == 1
    assert linear.created[0]["title"] == "Stuck PR threads: ai-investment-cockpit#9"
    assert "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#9 -->" in linear.created[0]["description"]
    assert (
        "<!-- codex-gate-mediate:CSL-Laboratories/ai-investment-cockpit:9 -->"
        in linear.created[0]["description"]
    )
    assert "<!-- /codex-gate-mediate -->" in linear.created[0]["description"]
    assert "<!-- stuck-pr-threads-begin -->" in linear.created[0]["description"]
    assert "<!-- stuck-pr-threads-end -->" in linear.created[0]["description"]
    assert linear.created[0]["description"].index("<!-- stuck-pr-threads-end -->") < linear.created[0][
        "description"
    ].index("<!-- /codex-gate-mediate -->")
    assert "<!-- codex-mediation:PRRT_kwDOabc -->" not in linear.created[0]["description"]
    assert "do not file a new Linear issue per thread" in body
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
        "title": "Stuck PR threads: ai-investment-cockpit#2",
        "description": "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#2 -->",
    }
    linear = FakeLinear(existing=existing)
    logs = mod.mediate_pr(pr, cfg(codex_pat="pat"), gh, linear, NOW)
    assert linear.created == []
    assert len(linear.updated) == 1
    assert linear.updated[0]["title"] == "Stuck PR threads: ai-investment-cockpit#2"
    assert "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#2 -->" in linear.updated[0]["description"]
    assert any("linear=https://linear.app/i/CSL-77" in line or "linear=updated" in line for line in logs)


def test_linear_resolve_ids_requires_needs_decision_and_project():
    class MissingLinear(FakeLinear):
        def _post(self, query: str, variables: dict) -> dict:
            if "issueLabels" in query and variables.get("name") == "needs-decision":
                return {"issueLabels": {"nodes": []}}
            return super()._post(query, variables)

    with pytest.raises(RuntimeError, match="needs-decision"):
        mod.linear_resolve_ids(MissingLinear(), cfg())

    class NoProjectLinear(FakeLinear):
        def _post(self, query: str, variables: dict) -> dict:
            if "projects(" in query:
                return {"projects": {"nodes": []}}
            return super()._post(query, variables)

    with pytest.raises(RuntimeError, match="project"):
        mod.linear_resolve_ids(NoProjectLinear(), cfg())


def test_apply_pr_label_requires_write_token():
    with pytest.raises(RuntimeError, match="no write token"):
        mod.apply_pr_label(FakeGitHub(), cfg(codex_pat=""), 3)


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
        cfg(file_linear=False, apply_pr_label=True, codex_pat="pat"),
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


def test_linear_find_rejects_foreign_marker():
    existing = {
        "id": "wrong",
        "title": "Stuck PR threads: ai-investment-cockpit#13",
        "description": (
            "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#13 -->\n"
            "quoted stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12"
        ),
    }
    linear = FakeLinear(existing=existing)
    assert mod.linear_find_existing(linear, cfg(), 12) is None


def test_linear_find_short_form_renamed_title():
    existing = {
        "id": "legacy",
        "identifier": "CSL-77",
        "url": "https://linear.app/i/CSL-77",
        "title": "Decision needed: PR #12",
        "description": "<!-- stuck-pr-threads:ai-investment-cockpit#12 -->\nolder roll-up",
    }
    linear = FakeLinear(existing=existing)
    found = mod.linear_find_existing(linear, cfg(), 12)
    assert found is not None
    assert found["id"] == "legacy"
    item = mod.StuckThread(
        pr_number=12,
        pr_url="https://ex/12",
        pr_title="t",
        thread_id="PRRT_kwDOabc",
        path="AGENTS.md",
        line="12",
        thread_url="https://ex/r",
        devin_body="finding",
        claude_body="needs a human",
        reason="needs-human",
    )
    url = mod.upsert_linear_issue(linear, [item], cfg())
    assert url == "https://linear.app/i/CSL-77"
    assert linear.created == []
    assert linear.updated[0]["title"] == "Stuck PR threads: ai-investment-cockpit#12"
    assert (
        "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->"
        in linear.updated[0]["description"]
    )
    assert "older roll-up" in linear.updated[0]["description"]


def test_linear_find_ignores_quoted_marker_in_suffix():
    existing = {
        "id": "real",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": (
            "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->\n"
            "<!-- stuck-pr-threads-begin -->\n"
            "generated\n"
            "<!-- stuck-pr-threads-end -->\n\n"
            "See also <!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#13 -->\n"
        ),
    }
    linear = FakeLinear(existing=existing)
    found = mod.linear_find_existing(linear, cfg(), 12)
    assert found is not None
    assert found["id"] == "real"
    assert mod.linear_find_existing(linear, cfg(), 13) is None
    assert mod.description_is_for_pr(existing["description"], cfg().repository, 12)
    assert not mod.description_is_for_pr(existing["description"], cfg().repository, 13)


def test_linear_find_rejects_conflicting_preamble_markers():
    existing = {
        "id": "ambiguous",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": (
            "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->\n"
            "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#13 -->\n"
            "<!-- stuck-pr-threads-begin -->\n"
            "generated\n"
            "<!-- stuck-pr-threads-end -->\n"
        ),
    }
    linear = FakeLinear(existing=existing)
    assert mod.linear_find_existing(linear, cfg(), 12) is None
    assert mod.linear_find_existing(linear, cfg(), 13) is None
    assert not mod.description_is_for_pr(existing["description"], cfg().repository, 12)
    assert not mod.description_is_for_pr(existing["description"], cfg().repository, 13)


def test_pr_identity_rejects_longer_pr_number():
    repo = cfg().repository
    only_123 = (
        "<!-- stuck-pr-threads:ai-investment-cockpit#12 -->\n"
        f"https://github.com/{repo.lower()}/pull/123\n"
    )
    only_12 = (
        "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->\n"
        f"https://github.com/{repo.lower()}/pull/12\n"
    )
    assert not mod._pr_identity_agrees(only_123, repo, 12)
    assert not mod.description_is_for_pr(only_123, repo, 12)
    assert mod._pr_identity_agrees(only_12, repo, 12)
    assert not mod._pr_identity_agrees(only_12, repo, 123)


def test_linear_update_preserves_legacy_short_form_notes():
    item = mod.StuckThread(
        pr_number=12,
        pr_url="https://ex/12",
        pr_title="t",
        thread_id="PRRT_kwDOabc",
        path="AGENTS.md",
        line="12",
        thread_url="https://ex/r",
        devin_body="finding",
        claude_body="needs a human",
        reason="needs-human",
    )
    existing = {
        "id": "legacy",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-77",
        "title": "Decision needed: PR #12",
        "description": "<!-- stuck-pr-threads:ai-investment-cockpit#12 -->\nDecision: wait for Lucas\n",
    }
    linear = FakeLinear(existing=existing)
    url = mod.upsert_linear_issue(linear, [item], cfg())
    assert url == "https://linear.app/i/CSL-77"
    assert linear.created == []
    assert "Decision: wait for Lucas" in linear.updated[0]["description"]
    assert linear.updated[0]["description"].index("<!-- stuck-pr-threads-end -->") < linear.updated[0][
        "description"
    ].index("Decision: wait for Lucas")


def _rollup_item(**overrides) -> object:
    values = dict(
        pr_number=12,
        pr_url="https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/12",
        pr_title="t",
        thread_id="PRRT_kwDOabc",
        path="AGENTS.md",
        line="12",
        thread_url="https://ex/r",
        devin_body="finding",
        claude_body="needs a human",
        reason="needs-human",
    )
    values.update(overrides)
    return mod.StuckThread(**values)


def test_linear_outage_does_not_block_mediation():
    class BoomLinear(FakeLinear):
        def graphql(self, query: str, variables: dict | None = None) -> dict:
            raise RuntimeError("linear down")

    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 12,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://ex/12",
        "title": "t",
        "reviewThreads": {"nodes": [stuck]},
    }
    logs = mod.mediate_pr(pr, cfg(), gh, BoomLinear(), NOW)
    assert any("linear=error" in line for line in logs)
    assert any("mediate=posted" in line for line in logs)
    assert len(gh.posts) == 1


def test_gate_marker_rejects_longer_pr_number():
    repo = cfg().repository
    desc_123 = f"<!-- codex-gate-mediate:{repo}:123 -->\nno pull url\n"
    assert not mod.description_has_gate_marker(desc_123, repo, 12)
    assert not mod.issue_is_for_pr(
        {"title": "other", "description": desc_123},
        repo,
        12,
    )
    assert mod.description_has_gate_marker(f"<!-- codex-gate-mediate:{repo}:12 -->", repo, 12)


def test_issue_is_for_pr_requires_stuck_pr_threads_marker():
    repo = cfg().repository
    marked = {
        "title": "Renamed on purpose",
        "description": "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->\n",
        "state": {"type": "unstarted"},
    }
    title_only = {
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": "hand-made CSL-167 notes with no marker\n",
        "state": {"type": "unstarted"},
    }
    gate_only = {
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": f"<!-- codex-gate-mediate:{repo}:12 -->\n",
        "state": {"type": "unstarted"},
    }
    assert mod.issue_is_for_pr(marked, repo, 12)
    assert not mod.issue_is_for_pr(title_only, repo, 12)
    assert not mod.issue_is_for_pr(gate_only, repo, 12)
    assert mod.linear_find_existing(FakeLinear(existing=title_only | {"id": "handmade"}), cfg(), 12) is None
    assert mod.linear_find_existing(FakeLinear(existing=gate_only | {"id": "gate"}), cfg(), 12) is None
    assert mod.linear_find_existing(FakeLinear(existing=marked | {"id": "real"}), cfg(), 12)["id"] == "real"


def test_failed_post_does_not_consume_cap():
    class FailFirst(FakeGitHub):
        def __init__(self) -> None:
            super().__init__()
            self.fail_left = 1

        def rest(self, path: str, method: str = "GET", body: dict | None = None, token: str | None = None):
            if method == "POST" and path.endswith("/comments") and self.fail_left:
                self.fail_left -= 1
                raise RuntimeError("post failed")
            return super().rest(path, method, body, token)

    gh = FailFirst()
    first = thread(
        thread_id="PRRT_kwDOone",
        comments=[
            comment(DEVIN, "first finding"),
            comment("claude[bot]", "needs a human"),
        ],
    )
    second = thread(
        thread_id="PRRT_kwDOtwo",
        comments=[
            comment(DEVIN, "second finding"),
            comment("claude[bot]", "couldn't fix this"),
        ],
    )
    pr = {
        "number": 11,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://ex/11",
        "title": "t",
        "reviewThreads": {"nodes": [first, second]},
    }
    logs = mod.mediate_pr(pr, cfg(max_per_pr_per_day=1, file_linear=False), gh, FakeLinear(), NOW)
    assert any("mediate=error" in line for line in logs)
    assert any("mediate=posted" in line and "PRRT_kwDOtwo" in line for line in logs)
    assert not any("over-cap" in line for line in logs)
    assert len(gh.posts) == 1


def test_linear_restores_renamed_title():
    item = _rollup_item()
    generated = mod.build_linear_description([item], cfg())
    existing = {
        "id": "x",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-77",
        "title": "Decision for Lucas",
        "description": generated,
    }
    linear = FakeLinear(existing=existing)
    url = mod.upsert_linear_issue(linear, [item], cfg())
    assert url == "https://linear.app/i/CSL-77"
    assert linear.created == []
    assert linear.updated[0]["title"] == "Stuck PR threads: ai-investment-cockpit#12"


def test_linear_skips_unchanged_generated_block():
    item = _rollup_item()
    generated = mod.build_linear_description([item], cfg())
    existing = {
        "id": "x",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-77",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": generated,
    }
    linear = FakeLinear(existing=existing)
    assert mod.upsert_linear_issue(linear, [item], cfg()) == "unchanged"
    assert linear.updated == []
    assert linear.created == []


def test_linear_does_not_adopt_codex_gate_only():
    existing = {
        "id": "gate",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-77",
        "title": "Renamed gate roll-up",
        "description": (
            "<!-- codex-gate-mediate:CSL-Laboratories/ai-investment-cockpit:12 -->\n\n"
            "**PR:** https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/12\n"
            "**SHA:** `abc`\n\n"
            "Codex gate MEDIATE: Devin and Claude disagree.\n"
            "<!-- /codex-gate-mediate -->\n"
        ),
    }
    linear = FakeLinear(existing=existing)
    assert mod.linear_find_existing(linear, cfg(), 12) is None
    url = mod.upsert_linear_issue(linear, [_rollup_item()], cfg())
    assert url == "https://linear.app/i/CSL-99"
    assert linear.updated == []
    assert len(linear.created) == 1
    created = linear.created[0]["description"]
    assert "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->" in created
    assert "<!-- stuck-pr-threads-begin -->" in created
    assert "<!-- codex-gate-mediate:CSL-Laboratories/ai-investment-cockpit:12 -->" in created


def test_linear_does_not_adopt_title_only():
    existing = {
        "id": "handmade",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-167",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": "Operator notes for CSL-167. Do not clobber.\n",
    }
    linear = FakeLinear(existing=existing)
    assert mod.linear_find_existing(linear, cfg(), 12) is None
    repo = cfg().repository
    assert not mod.issue_is_for_pr(existing, repo, 12)
    url = mod.upsert_linear_issue(linear, [_rollup_item()], cfg())
    assert url == "https://linear.app/i/CSL-99"
    assert linear.updated == []
    assert len(linear.created) == 1
    created = linear.created[0]["description"]
    assert "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->" in created
    assert "Operator notes for CSL-167. Do not clobber." not in created


def test_linear_find_skips_completed():
    existing = {
        "id": "done",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->",
        "state": {"type": "completed"},
    }
    linear = FakeLinear(existing=existing)
    assert mod.linear_find_existing(linear, cfg(), 12) is None


def test_rescan_skips_linear_when_unchanged():
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 12,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/12",
        "title": "t",
        "reviewThreads": {"nodes": [stuck]},
    }
    gh.pr_by_number[12] = pr
    gh.issue_comments[12] = [
        {"body": "<!-- codex-mediation:PRRT_kwDOabc -->", "created_at": "2026-10-15T10:00:00Z"}
    ]
    item = _rollup_item()
    existing = {
        "id": "x",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-77",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": mod.build_linear_description([item], cfg()),
    }
    linear = FakeLinear(existing=existing)
    logs = mod.mediate_pr(pr, cfg(), gh, linear, NOW)
    assert linear.created == []
    assert linear.updated == []
    assert any("linear=skipped-no-actionable" in line for line in logs)
    assert not any("linear=unchanged" in line for line in logs)
    assert gh.posts == []


def test_linear_recovers_missing_rollup_after_already_mediated():
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 12,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/12",
        "title": "t",
        "reviewThreads": {"nodes": [stuck]},
    }
    gh.pr_by_number[12] = pr
    gh.issue_comments[12] = [
        {"body": "<!-- codex-mediation:PRRT_kwDOabc -->", "created_at": "2026-10-15T10:00:00Z"}
    ]
    linear = FakeLinear()
    logs = mod.mediate_pr(pr, cfg(), gh, linear, NOW)
    assert len(linear.created) == 1
    assert linear.updated == []
    assert any("linear=https://linear.app/i/CSL-99" in line for line in logs)
    assert not any("linear=skipped-no-actionable" in line for line in logs)
    assert gh.posts == []


def test_actionable_thread_skips_unchanged_linear_then_mediates():
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 12,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/12",
        "title": "t",
        "reviewThreads": {"nodes": [stuck]},
    }
    gh.pr_by_number[12] = pr
    item = _rollup_item()
    existing = {
        "id": "x",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-77",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": mod.build_linear_description([item], cfg()),
    }
    linear = FakeLinear(existing=existing)
    logs = mod.mediate_pr(pr, cfg(), gh, linear, NOW)
    assert linear.created == []
    assert linear.updated == []
    assert any("linear=unchanged" in line for line in logs)
    assert any("mediate=posted" in line for line in logs)
    assert len(gh.posts) == 1


def test_linear_update_failure_raises():
    class FailUpdate(FakeLinear):
        def _post(self, query: str, variables: dict) -> dict:
            if "issueUpdate" in query:
                return {"issueUpdate": {"success": False, "issue": None}}
            return super()._post(query, variables)

    item = mod.StuckThread(
        pr_number=12,
        pr_url="https://ex/12",
        pr_title="t",
        thread_id="PRRT_kwDOabc",
        path="AGENTS.md",
        line="12",
        thread_url="https://ex/r",
        devin_body="finding",
        claude_body="needs a human",
        reason="needs-human",
    )
    existing = {
        "id": "x",
        "url": "https://linear.app/i/CSL-77",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->",
    }
    with pytest.raises(RuntimeError, match="linear update failed"):
        mod.upsert_linear_issue(FailUpdate(existing=existing), [item], cfg())


def test_linear_update_preserves_human_notes():
    item = mod.StuckThread(
        pr_number=12,
        pr_url="https://ex/12",
        pr_title="t",
        thread_id="PRRT_kwDOabc",
        path="AGENTS.md",
        line="12",
        thread_url="https://ex/r",
        devin_body="finding",
        claude_body="needs a human",
        reason="needs-human",
    )
    existing = {
        "id": "x",
        "url": "https://linear.app/i/CSL-77",
        "identifier": "CSL-77",
        "title": "Stuck PR threads: ai-investment-cockpit#12",
        "description": (
            "<!-- stuck-pr-threads:CSL-Laboratories/ai-investment-cockpit#12 -->\n"
            "<!-- stuck-pr-threads-begin -->\n"
            "old generated\n"
            "<!-- stuck-pr-threads-end -->\n\n"
            "Decision: keep the current implementation\n"
        ),
    }
    linear = FakeLinear(existing=existing)
    url = mod.upsert_linear_issue(linear, [item], cfg())
    assert url == "https://linear.app/i/CSL-77"
    assert "Decision: keep the current implementation" in linear.updated[0]["description"]
    assert "<!-- stuck-pr-threads-begin -->" in linear.updated[0]["description"]
    assert linear.updated[0]["description"].index("<!-- stuck-pr-threads-end -->") < linear.updated[0][
        "description"
    ].index("Decision: keep the current implementation")


def test_skips_approved_prs():
    gh = FakeGitHub()
    stuck = thread(
        comments=[
            comment(DEVIN, "finding"),
            comment("claude[bot]", "needs a human"),
        ]
    )
    pr = {
        "number": 124,
        "isDraft": False,
        "state": "OPEN",
        "reviewDecision": "APPROVED",
        "url": "https://ex/124",
        "title": "t",
        "reviewThreads": {"nodes": [stuck]},
    }
    linear = FakeLinear()
    logs = mod.mediate_pr(pr, cfg(), gh, linear, NOW)
    assert any("skip=approved" in line for line in logs)
    assert gh.posts == []
    assert linear.created == []
    assert linear.updated == []


def test_two_stuck_threads_one_linear_issue():
    gh = FakeGitHub()
    first = thread(
        thread_id="PRRT_kwDOone",
        comments=[
            comment(DEVIN, "first finding"),
            comment("claude[bot]", "needs a human"),
        ],
    )
    second = thread(
        thread_id="PRRT_kwDOtwo",
        comments=[
            comment(DEVIN, "second finding"),
            comment("claude[bot]", "couldn't fix this"),
        ],
    )
    pr = {
        "number": 11,
        "isDraft": False,
        "state": "OPEN",
        "url": "https://ex/11",
        "title": "t",
        "reviewThreads": {"nodes": [first, second]},
    }
    linear = FakeLinear()
    logs = mod.mediate_pr(pr, cfg(), gh, linear, NOW)
    assert len(gh.posts) == 2
    assert len(linear.created) == 1
    assert linear.created[0]["title"] == "Stuck PR threads: ai-investment-cockpit#11"
    assert "Stuck threads:** 2" in linear.created[0]["description"]
    assert (
        "<!-- codex-gate-mediate:CSL-Laboratories/ai-investment-cockpit:11 -->"
        in linear.created[0]["description"]
    )
    assert any("linear=https://linear.app/i/CSL-99" in line for line in logs)


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
    assert "Stuck PR threads: <repo>#<PR>" in text
    assert "codex-gate-mediate:owner/repo:N" in text
    assert "Skip already-approved PRs" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
