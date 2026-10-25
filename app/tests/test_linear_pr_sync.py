"""Unit tests for Linear PR-sync comments."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "linear_pr_sync.py"
WORKFLOW = ROOT / ".github" / "workflows" / "linear-pr-sync.yml"


def load():
    spec = importlib.util.spec_from_file_location("linear_pr_sync", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["linear_pr_sync"] = module
    spec.loader.exec_module(module)
    return module


mod = load()


class FakeGitHub(mod.GitHub):
    def __init__(self, prs: dict[int, dict], reviews=None, files=None) -> None:
        self.prs = prs
        self.reviews = reviews or {}
        self.files = files or {}

    def rest(self, path: str, method: str = "GET"):
        number = int(path.rsplit("/", 1)[-1])
        return self.prs[number]

    def paginate(self, path: str):
        if "/reviews" in path:
            number = int(path.split("/pulls/")[1].split("/")[0])
            return self.reviews.get(number, [])
        if "/files" in path:
            number = int(path.split("/pulls/")[1].split("/")[0])
            return self.files.get(number, [])
        if path.startswith("repos/") and "/pulls?" in path:
            return list(self.prs.values())
        return []


class FakeLinear(mod.Linear):
    def __init__(self, comments=None) -> None:
        super().__init__("lin", post=self._post)
        self.comments = list(comments or [])
        self.created: list[str] = []
        self.updated: list[tuple[str, str]] = []

    def _post(self, query: str, variables: dict) -> dict:
        if "commentUpdate" in query:
            self.updated.append((str(variables["id"]), str(variables["body"])))
            return {"commentUpdate": {"success": True, "comment": {"id": variables["id"]}}}
        if "commentCreate" in query:
            body = variables["input"]["body"]
            self.created.append(body)
            self.comments.append({"id": "new", "body": body})
            return {"commentCreate": {"success": True, "comment": {"id": "new"}}}
        return {
            "issue": {
                "id": "issue-uuid",
                "identifier": "CSL-86",
                "comments": {"nodes": list(self.comments)},
            }
        }


def sample_pr(**overrides) -> dict:
    pr = {
        "number": 124,
        "title": "CSL-86: add stuck-thread watch",
        "html_url": "https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/124",
        "state": "open",
        "draft": True,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "body": "## Summary\n\nReplace the out-of-repo watcher.\n\n## Checklist\n- [ ] Devin",
        "user": {"login": "cursor"},
        "head": {"sha": "abc123"},
    }
    pr.update(overrides)
    return pr


def test_csl_key_from_title():
    assert mod.csl_key("CSL-86: add watcher") == "CSL-86"
    assert mod.csl_key("csl-86: add watcher") == "CSL-86"
    assert mod.csl_key("no key here") == ""
    assert mod.csl_key("no key", "cursor/csl-86-codex-mediator-2478") == "CSL-86"


def test_purpose_skips_headings_and_html():
    body = "<!-- template -->\n## Summary\n\nAdd the watcher.\nKeep it small.\n"
    assert "Add the watcher." in mod.purpose_from_body(body)
    assert "Summary" not in mod.purpose_from_body(body)


def test_devin_reviewed_head():
    reviews = [{"user": {"login": "devin-ai-integration[bot]"}, "commit_id": "abc123", "state": "COMMENTED"}]
    assert mod.devin_reviewed_head(reviews, "abc123") is True
    assert mod.devin_reviewed_head(reviews, "def456") is False
    graphql = [{"user": {"login": "devin-ai-integration"}, "commit_id": "abc123", "state": "APPROVED"}]
    assert mod.devin_reviewed_head(graphql, "abc123") is True


def test_render_contains_marker_and_fields():
    text = mod.render_comment(
        sample_pr(),
        repo="CSL-Laboratories/ai-investment-cockpit",
        reviews=[],
        files=[{"filename": ".github/workflows/stuck-thread-watch.yml"}],
    )
    assert "<!-- pr-sync:CSL-Laboratories/ai-investment-cockpit#124 -->" in text
    assert "Linear-first (CSL-88)" in text
    assert "CSL-Laboratories/csl-research as a submission record" in text
    assert "never to this repo" in text
    assert "digest on the Linear issue" in text
    assert "**Status:** draft" in text
    assert "**Devin review on head:** no" in text
    assert "`abc123`" in text
    assert "stuck-thread-watch.yml" in text


def test_main_push_flags_conflicts():
    text = mod.render_comment(
        sample_pr(mergeable=False, mergeable_state="dirty"),
        repo="CSL-Laboratories/ai-investment-cockpit",
        reviews=[],
        files=[],
        vs_default=True,
    )
    assert "conflicts with main" in text
    assert "out of date / conflicts with main" not in text


def test_main_push_flags_behind_separately():
    text = mod.render_comment(
        sample_pr(mergeable=True, mergeable_state="behind"),
        repo="CSL-Laboratories/ai-investment-cockpit",
        reviews=[],
        files=[],
        vs_default=True,
    )
    assert "out of date with main" in text
    assert "conflicts" not in text


def test_upsert_edits_in_place():
    repo = "CSL-Laboratories/ai-investment-cockpit"
    existing = {"id": "cmt-1", "body": mod.marker(repo, 124) + "\nold"}
    linear = FakeLinear(comments=[existing])
    gh = FakeGitHub(
        {124: sample_pr()},
        reviews={124: []},
        files={124: [{"filename": "AGENTS.md"}]},
    )
    log = mod.sync_one(gh, linear, repo, 124, vs_default=False)
    assert "updated" in log
    assert linear.created == []
    assert linear.updated
    assert "<!-- pr-sync:CSL-Laboratories/ai-investment-cockpit#124 -->" in linear.updated[0][1]


def test_upsert_creates_when_missing():
    repo = "CSL-Laboratories/ai-investment-cockpit"
    linear = FakeLinear()
    gh = FakeGitHub({124: sample_pr()}, reviews={124: []}, files={124: []})
    log = mod.sync_one(gh, linear, repo, 124, vs_default=False)
    assert "created" in log
    assert linear.created


def test_missing_secret_is_success():
    code = mod.run(cfg=mod.Config(repository="o/r", event_name="pull_request", linear_api_key=""))
    assert code == 0


def test_skips_title_without_key():
    repo = "CSL-Laboratories/ai-investment-cockpit"
    gh = FakeGitHub({9: sample_pr(number=9, title="no key", head={"sha": "abc", "ref": "feature"})})
    log = mod.sync_one(gh, FakeLinear(), repo, 9, vs_default=False)
    assert "no-linear-key" in log


def test_uses_branch_linear_key():
    repo = "CSL-Laboratories/ai-investment-cockpit"
    linear = FakeLinear()
    gh = FakeGitHub(
        {9: sample_pr(number=9, title="docs only", head={"sha": "abc", "ref": "cursor/csl-86-x"})},
        reviews={9: []},
        files={9: []},
    )
    log = mod.sync_one(gh, linear, repo, 9, vs_default=False)
    assert "issue=CSL-86" in log
    assert linear.created


def test_retries_until_mergeable_is_known():
    repo = "CSL-Laboratories/ai-investment-cockpit"
    calls = {"n": 0}

    class RetryGitHub(FakeGitHub):
        def rest(self, path: str, method: str = "GET"):
            calls["n"] += 1
            pr = dict(self.prs[9])
            if calls["n"] < 3:
                pr["mergeable"] = None
            return pr

    slept: list[float] = []
    gh = RetryGitHub({9: sample_pr(number=9)}, reviews={9: []}, files={9: []})
    pr = mod.load_pr_until_mergeable(gh, repo, 9, sleep=slept.append, attempts=4)
    assert pr.get("mergeable") is True
    assert calls["n"] == 3
    assert slept == [2.0, 2.0]


def test_workflow_structure():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target:" in text
    assert "opened" in text
    assert "synchronize" in text
    assert "ready_for_review" in text
    assert "closed" in text
    assert "branches: [main]" in text
    assert "runs-on: ubuntu-latest" in text
    assert "timeout-minutes: 5" in text
    assert "cancel-in-progress: true" in text
    assert "LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}" in text
    assert "ref: ${{ github.event.repository.default_branch }}" in text
    assert "persist-credentials: false" in text
    assert "head.sha" not in text
    assert "blacksmith-" not in text
    assert "python -m pytest" not in text
