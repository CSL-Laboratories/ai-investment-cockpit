#!/usr/bin/env python3
"""Tests for the Codex CI/fixer gate.

    python -X utf8 -B tools/test_codex_gate.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "scripts" / "ci" / "codex_gate.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "codex-gate.yml"
CLAUDE_WORKFLOW = ROOT / ".github" / "workflows" / "claude.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ACTIONLINT = ROOT / ".github" / "actionlint.yaml"
AGENTS = ROOT / "AGENTS.md"
REPO = "acme/example"
SHA = "a" * 40


def load_gate():
    spec = importlib.util.spec_from_file_location("codex_gate", GATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_gate()


def pr_payload(
    *,
    number: int = 42,
    state: str = "open",
    draft: bool = False,
    head_repo: str = REPO,
    head_ref: str = "feature/fix",
    head_sha: str = SHA,
) -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "head": {"ref": head_ref, "sha": head_sha, "repo": {"full_name": head_repo}},
    }


def comment(
    body: str,
    created: str | None = None,
    login: str = "github-actions[bot]",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "body": body,
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
    }
    if created:
        item["created_at"] = created
    return item


def review(body: str, *, login: str = "chatgpt-codex-connector[bot]", sha: str = SHA) -> dict:
    return {"body": body, "user": {"login": login}, "commit_id": sha}


def label_event(
    login: str,
    *,
    unlabeled: bool = False,
    created: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "event": "unlabeled" if unlabeled else "labeled",
        "actor": {"login": login},
        "label": {"name": "ci:full"},
    }
    if created:
        item["created_at"] = created
    return item


class FakeGH:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.dispatches: list[tuple[str, dict[str, Any], str]] = []
        self.workflows: list[tuple[str, dict[str, str]]] = []
        self.deletes: list[str] = []
        self.comments: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.prs: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.fail_workflow = False

    def get(self, path: str) -> Any:
        if path.rstrip("/") in {"user", "/user"} or path.endswith("/user"):
            return {"login": "github-actions[bot]"}
        if "/events" in path:
            return list(self.events)
        if "/comments" in path:
            return list(self.comments)
        if "/reviews" in path:
            return list(self.reviews)
        if path.startswith(f"repos/{REPO}/pulls?") or path.startswith(f"repos/{REPO}/pulls&"):
            return list(self.prs)
        if "/pulls/" in path and path.rstrip("0123456789").endswith("/pulls/"):
            number = path.rsplit("/", 1)[-1].split("?")[0]
            for item in self.prs:
                if str(item.get("number")) == number:
                    return item
            return pr_payload(number=int(number) if number.isdigit() else 42)
        return []

    def post(self, path: str, body: dict[str, Any]) -> Any:
        self.posts.append((path, body))
        return {"id": len(self.posts)}

    def delete(self, path: str) -> None:
        self.deletes.append(path)

    def dispatch(self, event_type: str, payload: dict[str, Any], repository: str) -> None:
        self.dispatches.append((event_type, payload, repository))

    def workflow_run(self, workflow: str, fields: dict[str, str]) -> None:
        if self.fail_workflow:
            raise OSError("workflow missing")
        self.workflows.append((workflow, fields))


class ParseVerdictTests(unittest.TestCase):
    def test_standalone_last_line_only(self) -> None:
        self.assertEqual(mod.parse_verdict("nits\nCODEX-GATE: MEDIATE\n"), "MEDIATE")
        self.assertEqual(mod.parse_verdict("CODEX-GATE: FIX"), "FIX")
        self.assertEqual(mod.parse_verdict(""), "")
        self.assertEqual(mod.parse_verdict(None), "")

    def test_quoted_or_multiple_lines_are_rejected(self) -> None:
        self.assertEqual(mod.parse_verdict("CODEX-GATE: FIX\nCODEX-GATE: READY"), "")
        self.assertEqual(mod.parse_verdict("see CODEX-GATE: MEDIATE please"), "")
        self.assertEqual(mod.parse_verdict("CODEX-GATE: NOPE"), "")
        self.assertEqual(
            mod.parse_verdict(
                "Options were CODEX-GATE: FIX or CODEX-GATE: READY; more investigation needed."
            ),
            "",
        )
        self.assertEqual(
            mod.parse_verdict("CODEX-GATE: FIX\nmore investigation needed."),
            "",
        )


class RequestDecisionTests(unittest.TestCase):
    def test_posts_once_per_sha(self) -> None:
        pr = pr_payload()
        self.assertEqual(mod.decide_request(pr, REPO, []), "post")
        existing = [comment(mod.gate_marker(SHA) + "\n@codex review")]
        self.assertEqual(mod.decide_request(pr, REPO, existing), "exists")

    def test_skips_fork_draft_protected(self) -> None:
        self.assertEqual(mod.decide_request(pr_payload(head_repo="evil/fork"), REPO, []), "skip-ineligible-pr")
        self.assertEqual(mod.decide_request(pr_payload(draft=True), REPO, []), "skip-ineligible-pr")
        self.assertEqual(mod.decide_request(pr_payload(head_ref="main"), REPO, []), "skip-ineligible-pr")
        self.assertEqual(mod.decide_request(pr_payload(state="closed"), REPO, []), "skip-ineligible-pr")


class VerdictDecisionTests(unittest.TestCase):
    def test_fix_ready_mediate(self) -> None:
        self.assertEqual(
            mod.decide_verdict(sha=SHA, review_body="CODEX-GATE: FIX", reviews=[], comments=[], day="2026-10-15"),
            "FIX",
        )
        self.assertEqual(
            mod.decide_verdict(sha=SHA, review_body="CODEX-GATE: READY", reviews=[], comments=[], day="2026-10-15"),
            "READY",
        )
        self.assertEqual(
            mod.decide_verdict(sha=SHA, review_body="nits\nCODEX-GATE: MEDIATE\n", reviews=[], comments=[], day="2026-10-15"),
            "MEDIATE",
        )

    def test_already_acted_and_cap(self) -> None:
        acted = [comment(mod.action_marker(SHA, "FIX"))]
        self.assertEqual(
            mod.decide_verdict(sha=SHA, review_body="CODEX-GATE: FIX", reviews=[], comments=acted, day="2026-10-15"),
            "skip-already-fix",
        )
        cap = [
            comment(mod.fixer_run_marker("2026-10-15", f"{i:040x}"))
            for i in range(3)
        ]
        self.assertEqual(
            mod.decide_verdict(sha=SHA, review_body="CODEX-GATE: FIX", reviews=[], comments=cap, day="2026-10-15"),
            "skip-cap",
        )
        self.assertEqual(
            mod.decide_verdict(sha=SHA, review_body="CODEX-GATE: READY", reviews=[], comments=cap, day="2026-10-15"),
            "READY",
        )

    def test_mediation_review_without_gate_line_is_skipped(self) -> None:
        comments = [
            comment("<!-- codex-mediation:abc -->\n@codex review\nmediate", created="2026-10-15T12:00:00Z"),
        ]
        self.assertEqual(
            mod.decide_verdict(
                sha=SHA,
                review_body="advisory notes, no gate line",
                reviews=[],
                comments=comments,
                day="2026-10-15",
            ),
            "skip-mediation-review",
        )


class TimeoutDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    def _request(self, age_min: int, sha: str = SHA) -> dict[str, Any]:
        created = (self.now - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return comment(mod.gate_marker(sha) + "\n@codex review", created)

    def test_waiting_under_20_minutes(self) -> None:
        self.assertEqual(
            mod.decide_timeout(
                sha=SHA, comments=[self._request(19)], reviews=[], now=self.now, day="2026-10-15"
            ),
            "skip-waiting",
        )

    def test_timeout_without_verdict_is_fix(self) -> None:
        self.assertEqual(
            mod.decide_timeout(
                sha=SHA, comments=[self._request(20)], reviews=[], now=self.now, day="2026-10-15"
            ),
            "FIX",
        )

    def test_timeout_respects_codex_verdict(self) -> None:
        reviews = [review("looks good\nCODEX-GATE: READY")]
        self.assertEqual(
            mod.decide_timeout(
                sha=SHA, comments=[self._request(30)], reviews=reviews, now=self.now, day="2026-10-15"
            ),
            "READY",
        )

    def test_timeout_cap(self) -> None:
        comments = [self._request(30)] + [
            comment(mod.fixer_run_marker("2026-10-15", f"{i:040x}")) for i in range(3)
        ]
        self.assertEqual(
            mod.decide_timeout(sha=SHA, comments=comments, reviews=[], now=self.now, day="2026-10-15"),
            "skip-cap",
        )

    def test_timeout_still_fires_when_mediation_prompt_is_newest(self) -> None:
        comments = [
            self._request(30),
            comment(
                "<!-- codex-mediation:abc -->\n@codex review\nmediate this thread",
                created=(self.now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        ]
        self.assertEqual(
            mod.decide_timeout(
                sha=SHA, comments=comments, reviews=[], now=self.now, day="2026-10-15"
            ),
            "FIX",
        )


class ApplyVerdictTests(unittest.TestCase):
    def _apply(self, verdict: str) -> tuple[list[str], list[str], list[str], list[str]]:
        posted: list[str] = []
        labels: list[str] = []
        dispatched: list[str] = []
        mediated: list[str] = []
        logs = mod.apply_verdict(
            verdict,
            repository=REPO,
            pr_number="42",
            sha=SHA,
            comments=[],
            day="2026-10-15",
            post_comment=posted.append,
            add_label=labels.append,
            dispatch_fixer=lambda: dispatched.append("fixer"),
            file_decision=lambda: (mediated.append("linear") or "https://linear.app/issue/CSL-1"),
        )
        return logs, posted, labels, dispatched, mediated  # type: ignore[return-value]

    def test_fix_dispatches(self) -> None:
        logs, posted, labels, dispatched, mediated = self._apply("FIX")
        self.assertTrue(any("dispatched-fixer" in line for line in logs))
        self.assertEqual(dispatched, ["fixer"])
        self.assertEqual(labels, [])
        self.assertEqual(mediated, [])
        self.assertIn("codex-gate-action:" + SHA + ":FIX", posted[0])
        self.assertIn("codex-fixer-run:2026-10-15", posted[0])

    def test_ready_labels(self) -> None:
        _, posted, labels, dispatched, mediated = self._apply("READY")
        self.assertEqual(labels, ["ci:full"])
        self.assertEqual(dispatched, [])
        self.assertEqual(mediated, [])
        self.assertIn("READY", posted[0])

    def test_mediate_handoff(self) -> None:
        _, posted, labels, dispatched, mediated = self._apply("MEDIATE")
        self.assertEqual(mediated, ["linear"])
        self.assertEqual(dispatched, [])
        self.assertEqual(labels, [])
        self.assertIn("codex-gate-mediate:42:" + SHA, posted[0])
        self.assertIn("needs a human", posted[0])
        self.assertIn("https://linear.app/issue/CSL-1", posted[0])

    def test_mediate_without_linear_url_does_not_mark(self) -> None:
        posted: list[str] = []
        logs = mod.apply_verdict(
            "MEDIATE",
            repository=REPO,
            pr_number="42",
            sha=SHA,
            comments=[],
            day="2026-10-15",
            post_comment=posted.append,
            add_label=lambda _n: None,
            dispatch_fixer=lambda: None,
            file_decision=lambda: "",
        )
        self.assertEqual(len(posted), 1)
        self.assertIn("codex-gate-mediate-pending:42:" + SHA, posted[0])
        self.assertNotIn("codex-gate-action:", posted[0])
        self.assertTrue(any("mediate=retry" in line for line in logs))

        posted2: list[str] = []
        mod.apply_verdict(
            "MEDIATE",
            repository=REPO,
            pr_number="42",
            sha=SHA,
            comments=[comment(posted[0])],
            day="2026-10-15",
            post_comment=posted2.append,
            add_label=lambda _n: None,
            dispatch_fixer=lambda: None,
            file_decision=lambda: "",
        )
        self.assertEqual(posted2, [])

    def test_skip_is_noop(self) -> None:
        logs, posted, labels, dispatched, mediated = self._apply("skip-waiting")
        self.assertEqual(posted, [])
        self.assertEqual(labels, [])
        self.assertEqual(dispatched, [])
        self.assertEqual(mediated, [])
        self.assertTrue(logs)

    def test_failed_side_effect_does_not_mark(self) -> None:
        posted: list[str] = []

        def boom() -> None:
            raise OSError("dispatch failed")

        with self.assertRaises(OSError):
            mod.apply_verdict(
                "FIX",
                repository=REPO,
                pr_number="42",
                sha=SHA,
                comments=[],
                day="2026-10-15",
                post_comment=posted.append,
                add_label=lambda _n: None,
                dispatch_fixer=boom,
                file_decision=lambda: "",
            )
        self.assertEqual(posted, [])


class RunTests(unittest.TestCase):
    def test_missing_secret_notices_and_skips(self) -> None:
        gh = FakeGH()
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = mod.run(
                event_name="pull_request",
                event={"pull_request": pr_payload()},
                repository=REPO,
                pat="",
                gh=gh,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.posts, [])
        out = buf.getvalue()
        self.assertIn("::notice::", out)
        self.assertIn("CODEX_TRIGGER_PAT", out)
        self.assertNotIn("ghp_", out)

    def test_request_posts_once(self) -> None:
        gh = FakeGH()
        rc = mod.run(
            event_name="pull_request",
            event={"pull_request": pr_payload()},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(gh.posts), 1)
        body = gh.posts[0][1]["body"]
        self.assertIn(mod.gate_marker(SHA), body)
        self.assertIn("@codex review", body)
        self.assertIn("CODEX-GATE: FIX", body)
        self.assertIn("Linear needs-decision", body)
        self.assertNotIn("stuck-thread-watch", body)

        gh.comments.append(comment(body))
        rc = mod.run(
            event_name="pull_request",
            event={"pull_request": pr_payload()},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(gh.posts), 1)

    def test_codex_review_fix_dispatches(self) -> None:
        gh = FakeGH()
        event = {
            "review": review("findings remain\nCODEX-GATE: FIX"),
            "pull_request": pr_payload(),
        }
        rc = mod.run(
            event_name="pull_request_review",
            event=event,
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.dispatches, [("devin-review-fix", {"pr_number": "42"}, REPO)])
        self.assertFalse(any("/labels" in path for path, _ in gh.posts))

    def test_non_codex_review_is_ignored(self) -> None:
        gh = FakeGH()
        event = {
            "review": review("CODEX-GATE: FIX", login="devin-ai-integration[bot]"),
            "pull_request": pr_payload(),
        }
        rc = mod.run(
            event_name="pull_request_review",
            event=event,
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.dispatches, [])

    def test_mediate_missing_linear_notices(self) -> None:
        gh = FakeGH()
        buf = io.StringIO()
        with patch("sys.stdout", buf), patch.dict(os.environ, {"LINEAR_API_KEY": ""}, clear=False):
            rc = mod.run(
                event_name="pull_request_review",
                event={
                    "review": review("CODEX-GATE: MEDIATE"),
                    "pull_request": pr_payload(),
                },
                repository=REPO,
                pat="not-a-real-secret",
                gh=gh,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.dispatches, [])
        self.assertIn("::notice::", buf.getvalue())
        self.assertIn("LINEAR_API_KEY", buf.getvalue())
        self.assertFalse(any("codex-gate-action:" in (body.get("body") or "") for _, body in gh.posts))
        self.assertTrue(any("codex-gate-mediate-pending:" in (body.get("body") or "") for _, body in gh.posts))

    def test_review_skips_fork_and_draft(self) -> None:
        gh = FakeGH()
        for pr in (
            pr_payload(head_repo="evil/fork"),
            pr_payload(draft=True),
            pr_payload(state="closed"),
        ):
            gh.dispatches.clear()
            rc = mod.run(
                event_name="pull_request_review",
                event={"review": review("CODEX-GATE: READY"), "pull_request": pr},
                repository=REPO,
                pat="not-a-real-secret",
                gh=gh,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(gh.dispatches, [])
            self.assertFalse(any("/labels" in path for path, _ in gh.posts))

    def test_forged_markers_do_not_count(self) -> None:
        gh = FakeGH()
        gh.comments = [
            comment(mod.action_marker(SHA, "FIX"), login="mallory"),
            comment(mod.fixer_run_marker("2026-10-15", SHA), login="mallory"),
        ]
        rc = mod.run(
            event_name="pull_request_review",
            event={
                "review": review("looks unfinished\nCODEX-GATE: FIX"),
                "pull_request": pr_payload(),
            },
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.dispatches, [("devin-review-fix", {"pr_number": "42"}, REPO)])

    def test_new_sha_clears_prior_ready_label(self) -> None:
        gh = FakeGH()
        gh.comments = [comment(mod.action_marker("c" * 40, "READY"))]
        rc = mod.run(
            event_name="pull_request",
            event={"pull_request": pr_payload()},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertTrue(any("labels/ci:full" in path for path in gh.deletes))

    def test_current_ready_keeps_label_when_older_sha_also_ready(self) -> None:
        gh = FakeGH()
        gh.comments = [
            comment(mod.action_marker("c" * 40, "READY")),
            comment(mod.action_marker(SHA, "READY")),
            comment(mod.gate_marker(SHA) + "\n@codex review"),
        ]
        rc = mod.run(
            event_name="pull_request",
            event={"pull_request": pr_payload()},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.deletes, [])
        self.assertFalse(any("/labels" in path for path, _ in gh.posts))

    def test_schedule_posts_missing_request(self) -> None:
        gh = FakeGH()
        gh.prs = [pr_payload()]
        rc = mod.run(
            event_name="schedule",
            event={},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(gh.posts), 1)
        self.assertIn(mod.gate_marker(SHA), gh.posts[0][1]["body"])
        self.assertEqual(gh.dispatches, [])

    def test_schedule_does_not_strip_manual_ci_full(self) -> None:
        gh = FakeGH()
        gh.prs = [pr_payload()]
        gh.comments = [
            comment(mod.action_marker("c" * 40, "READY"), created="2026-10-15T15:00:00Z"),
            comment(mod.gate_marker(SHA) + "\n@codex review", created="2026-10-15T15:10:00Z"),
        ]
        gh.events = [
            label_event("github-actions[bot]", created="2026-10-15T15:00:00Z"),
            label_event("lucas", created="2026-10-15T15:12:00Z"),
        ]
        rc = mod.run(
            event_name="schedule",
            event={},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.deletes, [])

    def test_schedule_keeps_pat_owner_manual_label(self) -> None:
        gh = FakeGH()
        gh.prs = [pr_payload()]
        gh.comments = [
            comment(mod.action_marker("c" * 40, "READY"), created="2026-10-15T15:00:00Z"),
            comment(mod.gate_marker(SHA) + "\n@codex review", created="2026-10-15T15:10:00Z"),
        ]
        # Same actor as the gate PAT owner; later timestamp is the override.
        gh.events = [
            label_event("github-actions[bot]", created="2026-10-15T15:00:00Z"),
            label_event("github-actions[bot]", created="2026-10-15T15:12:00Z"),
        ]
        rc = mod.run(
            event_name="schedule",
            event={},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(gh.deletes, [])

    def test_schedule_clears_gate_owned_stale_label(self) -> None:
        gh = FakeGH()
        gh.prs = [pr_payload()]
        gh.comments = [
            comment(mod.action_marker("c" * 40, "READY"), created="2026-10-15T15:00:00Z"),
            comment(mod.gate_marker(SHA) + "\n@codex review", created="2026-10-15T15:10:00Z"),
        ]
        gh.events = [label_event("github-actions[bot]", created="2026-10-15T15:00:00Z")]
        rc = mod.run(
            event_name="schedule",
            event={},
            repository=REPO,
            pat="not-a-real-secret",
            gh=gh,
        )
        self.assertEqual(rc, 0)
        self.assertTrue(any("labels/ci:full" in path for path in gh.deletes))

    def test_gate_ready_helper_requires_current_sha_not_ready(self) -> None:
        older = [comment(mod.action_marker("c" * 40, "READY"))]
        both = older + [comment(mod.action_marker(SHA, "READY"))]
        self.assertTrue(mod.gate_ready_on_other_sha(older, SHA))
        self.assertFalse(mod.gate_ready_on_other_sha(both, SHA))
        self.assertFalse(mod.gate_ready_on_other_sha([], SHA))

    def test_create_needs_decision_issue_dedupes(self) -> None:
        calls: list[str] = []

        def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            calls.append(query)
            if "issues(" in query:
                return {
                    "issues": {
                        "nodes": [
                            {
                                "url": "https://linear.app/csl-laboratories/issue/CSL-9",
                                "description": mod.mediate_marker("42", SHA) + "\nexisting",
                            }
                        ]
                    }
                }
            raise AssertionError("should not create")

        url = mod.create_needs_decision_issue(
            graphql, repository=REPO, pr_number="42", sha=SHA
        )
        self.assertEqual(url, "https://linear.app/csl-laboratories/issue/CSL-9")
        self.assertEqual(len(calls), 1)

    def test_label_predates_last_ready_ignores_actor(self) -> None:
        ready = [comment(mod.action_marker("c" * 40, "READY"), created="2026-10-15T15:00:00Z")]
        self.assertTrue(
            mod.label_predates_last_ready(
                ready,
                [label_event("github-actions[bot]", created="2026-10-15T15:00:00Z")],
            )
        )
        self.assertFalse(
            mod.label_predates_last_ready(
                ready,
                [label_event("github-actions[bot]", created="2026-10-15T15:12:00Z")],
            )
        )
        self.assertFalse(
            mod.label_predates_last_ready(
                ready,
                [
                    label_event("github-actions[bot]", created="2026-10-15T15:00:00Z"),
                    label_event("github-actions[bot]", unlabeled=True, created="2026-10-15T15:01:00Z"),
                ],
            )
        )


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.claude = CLAUDE_WORKFLOW.read_text(encoding="utf-8")
        cls.ci = CI_WORKFLOW.read_text(encoding="utf-8")
        cls.actionlint = ACTIONLINT.read_text(encoding="utf-8")
        cls.agents = AGENTS.read_text(encoding="utf-8")

    def test_runs_on_ubuntu_latest_not_blacksmith(self) -> None:
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertNotIn("blacksmith", self.text)

    def test_notice_on_missing_secret(self) -> None:
        self.assertIn("::notice::CODEX_TRIGGER_PAT is not configured", self.text)
        self.assertIn("secrets.CODEX_TRIGGER_PAT", self.text)
        self.assertIn("scripts/ci/codex_gate.py", self.text)
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("<!-- codex-gate:", source)
        self.assertIn("CODEX-GATE:", source)

    def test_review_job_if_is_codex_only(self) -> None:
        self.assertIn("chatgpt-codex-connector[bot]", self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("group: codex-gate", self.text)
        self.assertNotIn("github.run_id", self.text)
        self.assertIn("github.event.repository.default_branch", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("codex_gate.py is not on the default branch yet", self.text)
        self.assertIn("event-paths", self.text)
        self.assertIn("never checks out PR or fork code", self.text)
        # Tests may check out same-repo PR code; they never receive the PAT.
        event_paths_job = self.text.split("\n  event-paths:")[1]
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            event_paths_job.split("runs-on:")[0],
        )

    def test_claude_dispatch_triggers(self) -> None:
        self.assertIn("repository_dispatch:", self.claude)
        self.assertIn("types: [devin-review-fix]", self.claude)
        self.assertIn("job:", self.claude)
        self.assertIn("devin-review-fix", self.claude)

    def test_windows_runner_is_2vcpu(self) -> None:
        self.assertIn("blacksmith-2vcpu-windows-2025", self.ci)
        self.assertNotIn("blacksmith-4vcpu-windows-2025", self.ci)
        self.assertIn("blacksmith-2vcpu-windows-2025", self.actionlint)
        self.assertNotIn("blacksmith-4vcpu-windows-2025", self.actionlint)

    def test_agents_code_review_rules(self) -> None:
        self.assertIn("## Code Review Rules", self.agents)
        self.assertIn("CODEX-GATE: FIX", self.agents)
        self.assertIn("CODEX-GATE: READY", self.agents)
        self.assertIn("CODEX-GATE: MEDIATE", self.agents)
        self.assertIn("When you are the assignee", self.agents)
        self.assertIn("Parallel Task API deep research", self.agents)
        self.assertIn("in addition to Search/Extract", self.agents)
        self.assertIn("Grok Bots do not research or resolve decision issues", self.agents)
        self.assertIn("neither a Devin substitute", self.agents)
        self.assertIn("Human-only findings need a human `@claude` mention", self.agents)
        self.assertIn("A founder may have their own bot", self.agents)
        self.assertIn("Stuck-thread watch", self.agents)
        self.assertIn("codex-mediation:", self.agents)
        self.assertIn("needs-decision", self.agents)
        self.assertIn("do **not** add a `CODEX-GATE:` line", self.agents)
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("AGENTS.md", claude)
        self.assertIn("Grok Bots", claude)

    def test_pat_job_token_is_read_only_and_actionlint_is_checksummed(self) -> None:
        self.assertIn("contents: read", self.text)
        self.assertIn("pull-requests: read", self.text)
        self.assertIn("issues: read", self.text)
        self.assertNotIn("actions: write", self.text)
        self.assertNotIn("issues: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertIn("ACTIONLINT_SHA256", self.text)
        self.assertIn("sha256sum -c", self.text)
        self.assertNotIn("download-actionlint.bash", self.text)
        self.assertIn("LINEAR_API_KEY", self.text)

    def test_does_not_print_secret_names_as_values(self) -> None:
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("print(pat)", source)
        self.assertNotIn("print(os.environ", source)


class ActionlintTests(unittest.TestCase):
    def test_actionlint_when_available(self) -> None:
        import shutil

        exe = shutil.which("actionlint")
        if not exe:
            self.skipTest("actionlint not on PATH")
        result = subprocess.run(
            [exe, "-color", str(WORKFLOW_PATH), str(CLAUDE_WORKFLOW), str(CI_WORKFLOW)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CliSecretTests(unittest.TestCase):
    def test_cli_missing_event_path_does_not_fail(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CODEX_TRIGGER_PAT"}
        env.update(
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_EVENT_PATH": "/nonexistent/event.json",
            }
        )
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(GATE_PATH)],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice::", result.stdout)
        self.assertIn("CODEX_TRIGGER_PAT", result.stdout)


if __name__ == "__main__":
    unittest.main()
