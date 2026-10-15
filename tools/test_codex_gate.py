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

    def test_mediate_skips_approved_pr(self) -> None:
        posted: list[str] = []
        mediated: list[str] = []
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
            file_decision=lambda: (mediated.append("linear") or "https://linear.app/issue/CSL-1"),
            pr_approved=True,
        )
        self.assertEqual(mediated, [])
        self.assertEqual(len(posted), 1)
        self.assertIn("already approved", posted[0])
        self.assertIn("codex-gate-action:" + SHA + ":MEDIATE", posted[0])
        self.assertTrue(any("mediate=skip-approved" in line for line in logs))

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

    def test_mediate_run_skips_linear_when_pr_approved(self) -> None:
        gh = FakeGH()
        gh.reviews = [
            {
                "body": "",
                "user": {"login": "alice"},
                "state": "APPROVED",
                "commit_id": SHA,
                "submitted_at": "2026-10-15T15:00:00Z",
            }
        ]
        with patch.dict(os.environ, {"LINEAR_API_KEY": "lin_api_not_real"}, clear=False):
            rc = mod.run(
                event_name="pull_request_review",
                event={
                    "review": review("nits\nCODEX-GATE: MEDIATE"),
                    "pull_request": pr_payload(),
                },
                repository=REPO,
                pat="not-a-real-secret",
                gh=gh,
            )
        self.assertEqual(rc, 0)
        bodies = [str(body.get("body") or "") for _, body in gh.posts]
        self.assertTrue(any("already approved" in item for item in bodies))
        self.assertTrue(any("codex-gate-action:" + SHA + ":MEDIATE" in item for item in bodies))
        self.assertFalse(any("Linear:" in item for item in bodies))

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

    def test_create_needs_decision_issue_updates_rollup(self) -> None:
        updates: list[dict[str, Any]] = []
        existing = (
            f"{mod.stuck_pr_marker(REPO, '42')}\n"
            f"{mod.GATE_BEGIN}\n"
            f"**PR:** https://github.com/{REPO}/pull/42\n"
            f"**SHA:** `{SHA}`\n\n"
            "Codex gate MEDIATE: Devin and Claude disagree or a thread needs a "
            "human. Auto-filed by `.github/workflows/codex-gate.yml`. Humans decide.\n"
            f"{mod.GATE_END}\n"
            "Wait for Alice's risk decision\n"
        )

        def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            if "issues(" in query:
                return {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-1",
                                "url": "https://linear.app/csl-laboratories/issue/CSL-9",
                                "description": existing,
                            }
                        ]
                    }
                }
            if "issueUpdate" in query:
                updates.append(variables)
                return {
                    "issueUpdate": {
                        "success": True,
                        "issue": {"url": "https://linear.app/csl-laboratories/issue/CSL-9"},
                    }
                }
            raise AssertionError("should update, not create")

        url = mod.create_needs_decision_issue(
            graphql, repository=REPO, pr_number="42", sha="b" * 40
        )
        self.assertEqual(url, "https://linear.app/csl-laboratories/issue/CSL-9")
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["id"], "issue-1")
        self.assertEqual(updates[0]["input"]["title"], "Stuck PR threads: example#42")
        body = updates[0]["input"]["description"]
        self.assertIn(mod.stuck_pr_marker(REPO, "42"), body)
        self.assertIn("b" * 40, body)
        self.assertIn("Wait for Alice's risk decision", body)
        self.assertIn(mod.GATE_BEGIN, body)
        self.assertEqual(body.count(mod.stuck_pr_marker(REPO, "42")), 1)

    def test_create_needs_decision_issue_rollup_title(self) -> None:
        created: list[dict[str, Any]] = []

        def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            if "issues(" in query:
                return {"issues": {"nodes": []}}
            if "teams(" in query:
                return {
                    "teams": {
                        "nodes": [
                            {
                                "id": "t1",
                                "states": {"nodes": [{"id": "s1", "type": "triage"}]},
                            }
                        ]
                    }
                }
            if "issueLabels" in query:
                name = str(variables.get("name") or "")
                return {"issueLabels": {"nodes": [{"id": name, "name": name}]}}
            if "projects(" in query:
                return {
                    "projects": {
                        "nodes": [{"id": "p1", "name": "ai-investment-cockpit"}]
                    }
                }
            if "issueCreate" in query:
                created.append(variables)
                return {
                    "issueCreate": {
                        "success": True,
                        "issue": {"url": "https://linear.app/csl-laboratories/issue/CSL-10"},
                    }
                }
            raise AssertionError(query[:80])

        url = mod.create_needs_decision_issue(
            graphql, repository=REPO, pr_number="42", sha=SHA
        )
        self.assertEqual(url, "https://linear.app/csl-laboratories/issue/CSL-10")
        self.assertEqual(created[0]["input"]["title"], "Stuck PR threads: example#42")
        self.assertIn(mod.stuck_pr_marker(REPO, "42"), created[0]["input"]["description"])
        self.assertIn(mod.GATE_BEGIN, created[0]["input"]["description"])
        self.assertIn(mod.mediate_issue_end_marker(), created[0]["input"]["description"])
        self.assertNotIn(f"codex-gate-mediate:42:{SHA}", created[0]["input"]["description"])

    def test_create_needs_decision_issue_ignores_other_repo(self) -> None:
        created: list[dict[str, Any]] = []
        other = (
            f"{mod.stuck_pr_marker('acme/other', '42')}\n"
            f"{mod.WATCHER_BEGIN}\n"
            "**PR:** https://github.com/acme/other/pull/42\n"
            f"{mod.WATCHER_END}\n"
        )

        def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            if "issues(" in query:
                return {
                    "issues": {
                        "nodes": [
                            {
                                "id": "other-1",
                                "url": "https://linear.app/csl-laboratories/issue/CSL-8",
                                "description": other,
                            }
                        ]
                    }
                }
            if "teams(" in query:
                return {
                    "teams": {
                        "nodes": [
                            {
                                "id": "t1",
                                "states": {"nodes": [{"id": "s1", "type": "triage"}]},
                            }
                        ]
                    }
                }
            if "issueLabels" in query:
                name = str(variables.get("name") or "")
                return {"issueLabels": {"nodes": [{"id": name, "name": name}]}}
            if "projects(" in query:
                return {
                    "projects": {
                        "nodes": [{"id": "p1", "name": "ai-investment-cockpit"}]
                    }
                }
            if "issueCreate" in query:
                created.append(variables)
                return {
                    "issueCreate": {
                        "success": True,
                        "issue": {"url": "https://linear.app/csl-laboratories/issue/CSL-11"},
                    }
                }
            if "issueUpdate" in query:
                raise AssertionError("must not update another repository's issue")
            raise AssertionError(query[:80])

        url = mod.create_needs_decision_issue(
            graphql, repository=REPO, pr_number="42", sha=SHA
        )
        self.assertEqual(url, "https://linear.app/csl-laboratories/issue/CSL-11")
        self.assertEqual(len(created), 1)

    def test_create_needs_decision_issue_finds_renamed_legacy(self) -> None:
        found = mod.find_mediate_issue(
            lambda query, variables: (
                {
                    "issues": {
                        "nodes": [
                            {
                                "id": "legacy-1",
                                "url": "https://linear.app/csl-laboratories/issue/CSL-20",
                                "title": "Decision on risk limits",
                                "description": (
                                    f"{mod.mediate_issue_marker_legacy('42')}\n"
                                    f"**PR:** https://github.com/{REPO}/pull/42\n"
                                ),
                            }
                        ]
                    }
                }
                if "issues(" in query
                and str(variables.get("contains") or "")
                in {
                    "codex-gate-mediate:42 -->",
                    f"https://github.com/{REPO}/pull/42",
                }
                else {"issues": {"nodes": []}}
            ),
            REPO,
            "42",
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["id"], "legacy-1")
        self.assertFalse(
            mod.rollup_may_update(str(found["description"]), REPO, "42")
        )

    def test_create_needs_decision_issue_skips_unqualified_overwrite(self) -> None:
        created: list[dict[str, Any]] = []
        foreign = (
            f"{mod.mediate_issue_marker_legacy('42')}\n"
            f"**PR:** https://github.com/{REPO}/pull/42\n"
            "[discussion](https://github.com/other/project/pull/42)\n"
        )

        def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            if "title: { eq" in query:
                return {"issues": {"nodes": []}}
            if "issues(" in query:
                return {
                    "issues": {
                        "nodes": [
                            {
                                "id": "foreign-1",
                                "url": "https://linear.app/csl-laboratories/issue/CSL-99",
                                "description": foreign,
                            }
                        ]
                    }
                }
            if "teams(" in query:
                return {
                    "teams": {
                        "nodes": [
                            {
                                "id": "t1",
                                "states": {"nodes": [{"id": "s1", "type": "triage"}]},
                            }
                        ]
                    }
                }
            if "issueLabels" in query:
                name = str(variables.get("name") or "")
                return {"issueLabels": {"nodes": [{"id": name, "name": name}]}}
            if "projects(" in query:
                return {
                    "projects": {
                        "nodes": [{"id": "p1", "name": "ai-investment-cockpit"}]
                    }
                }
            if "issueCreate" in query:
                created.append(variables)
                return {
                    "issueCreate": {
                        "success": True,
                        "issue": {"url": "https://linear.app/csl-laboratories/issue/CSL-100"},
                    }
                }
            if "issueUpdate" in query:
                raise AssertionError("must not overwrite a roll-up without a repo-qualified marker")
            raise AssertionError(query[:80])

        url = mod.create_needs_decision_issue(
            graphql, repository=REPO, pr_number="42", sha=SHA
        )
        self.assertEqual(url, "https://linear.app/csl-laboratories/issue/CSL-100")
        self.assertEqual(len(created), 1)
        self.assertIn(mod.stuck_pr_marker(REPO, "42"), created[0]["input"]["description"])

    def test_create_needs_decision_issue_updates_watcher_marker(self) -> None:
        updates: list[dict[str, Any]] = []
        watcher = (
            f"{mod.stuck_pr_marker(REPO, '42')}\n"
            f"{mod.WATCHER_BEGIN}\n"
            "watcher threads\n"
            f"{mod.WATCHER_END}\n"
        )

        def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            if "issues(" in query:
                return {
                    "issues": {
                        "nodes": [
                            {
                                "id": "watch-1",
                                "url": "https://linear.app/csl-laboratories/issue/CSL-86",
                                "description": watcher,
                            }
                        ]
                    }
                }
            if "issueUpdate" in query:
                updates.append(variables)
                return {
                    "issueUpdate": {
                        "success": True,
                        "issue": {"url": "https://linear.app/csl-laboratories/issue/CSL-86"},
                    }
                }
            raise AssertionError("should update the #124 roll-up, not create")

        url = mod.create_needs_decision_issue(
            graphql, repository=REPO, pr_number="42", sha=SHA
        )
        self.assertEqual(url, "https://linear.app/csl-laboratories/issue/CSL-86")
        self.assertEqual(len(updates), 1)
        body = updates[0]["input"]["description"]
        self.assertIn(mod.stuck_pr_marker(REPO, "42"), body)
        self.assertIn(mod.WATCHER_BEGIN, body)
        self.assertIn("watcher threads", body)
        self.assertIn(mod.GATE_BEGIN, body)

    def test_create_needs_decision_issue_adopts_title_and_skips_done(self) -> None:
        updates: list[dict[str, Any]] = []
        created: list[dict[str, Any]] = []
        unmarked = {
            "id": "hand-1",
            "url": "https://linear.app/csl-laboratories/issue/CSL-167",
            "title": "Stuck PR threads: example#42",
            "description": "Hand-made roll-up",
            "state": {"name": "Triage", "type": "triage"},
        }
        done = {
            "id": "done-1",
            "url": "https://linear.app/csl-laboratories/issue/CSL-169",
            "title": "Stuck PR threads: example#7",
            "description": "",
            "state": {"name": "Done", "type": "completed"},
        }

        def graphql_open(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            if "title: { eq" in query:
                return {"issues": {"nodes": [unmarked]}}
            if "issues(" in query:
                return {"issues": {"nodes": []}}
            if "issueUpdate" in query:
                updates.append(variables)
                return {
                    "issueUpdate": {
                        "success": True,
                        "issue": {"url": unmarked["url"]},
                    }
                }
            raise AssertionError(query[:80])

        url = mod.create_needs_decision_issue(
            graphql_open, repository=REPO, pr_number="42", sha=SHA
        )
        self.assertEqual(url, unmarked["url"])
        self.assertEqual(len(updates), 1)
        self.assertIn("Hand-made roll-up", updates[0]["input"]["description"])
        self.assertIn(mod.stuck_pr_marker(REPO, "42"), updates[0]["input"]["description"])

        def graphql_done(query: str, variables: dict[str, Any]) -> dict[str, Any]:
            if "title: { eq" in query:
                return {"issues": {"nodes": [done]}}
            if "issues(" in query:
                return {"issues": {"nodes": []}}
            if "teams(" in query:
                return {
                    "teams": {
                        "nodes": [
                            {
                                "id": "t1",
                                "states": {"nodes": [{"id": "s1", "type": "triage"}]},
                            }
                        ]
                    }
                }
            if "issueLabels" in query:
                name = str(variables.get("name") or "")
                return {"issueLabels": {"nodes": [{"id": name, "name": name}]}}
            if "projects(" in query:
                return {
                    "projects": {
                        "nodes": [{"id": "p1", "name": "ai-investment-cockpit"}]
                    }
                }
            if "issueCreate" in query:
                created.append(variables)
                return {
                    "issueCreate": {
                        "success": True,
                        "issue": {"url": "https://linear.app/csl-laboratories/issue/CSL-12"},
                    }
                }
            if "issueUpdate" in query:
                raise AssertionError("must not update a Done issue")
            raise AssertionError(query[:80])

        url = mod.create_needs_decision_issue(
            graphql_done, repository=REPO, pr_number="7", sha=SHA
        )
        self.assertEqual(url, "https://linear.app/csl-laboratories/issue/CSL-12")
        self.assertEqual(len(created), 1)

    def test_merge_keeps_watcher_block(self) -> None:
        existing = (
            f"{mod.stuck_pr_marker(REPO, '42')}\n"
            f"{mod.WATCHER_BEGIN}\n"
            "watcher threads\n"
            f"{mod.WATCHER_END}\n"
            "Wait for Alice's risk decision\n"
        )
        merged = mod.merge_mediate_description(existing, REPO, "42", SHA)
        self.assertIn(mod.WATCHER_BEGIN, merged)
        self.assertIn("watcher threads", merged)
        self.assertIn(mod.WATCHER_END, merged)
        self.assertIn(mod.GATE_BEGIN, merged)
        self.assertIn("Wait for Alice's risk decision", merged)
        self.assertLess(merged.index(mod.WATCHER_END), merged.index(mod.GATE_BEGIN))

    def test_merge_mediate_description_keeps_notes_on_repeat(self) -> None:
        first = mod.merge_mediate_description(
            "Wait for Alice's risk decision", REPO, "42", SHA
        )
        self.assertIn("Wait for Alice's risk decision", first)
        second = mod.merge_mediate_description(first, REPO, "42", "b" * 40)
        self.assertEqual(second.count("Wait for Alice's risk decision"), 1)
        self.assertIn("b" * 40, second)
        self.assertNotIn(SHA, second.split(mod.mediate_issue_end_marker())[0])

    def test_mediate_description_does_not_match_other_pr(self) -> None:
        own = mod.stuck_pr_marker(REPO, "42")
        self.assertTrue(mod.mediate_description_matches(own, REPO, "42"))
        self.assertTrue(mod.has_repo_qualified_marker(own, REPO, "42"))
        self.assertTrue(mod.rollup_may_update(own, REPO, "42"))
        short = "<!-- stuck-pr-threads:example#42 -->"
        self.assertTrue(mod.mediate_description_matches(short, REPO, "42"))
        self.assertFalse(mod.has_repo_qualified_marker(short, REPO, "42"))
        self.assertFalse(mod.rollup_may_update(short, REPO, "42"))
        self.assertFalse(mod.has_repo_qualified_marker(own, "acme/other", "42"))
        legacy = (
            f"{mod.mediate_marker('42', SHA)}\n**PR:** https://github.com/{REPO}/pull/42\n"
        )
        self.assertTrue(mod.mediate_description_matches(legacy, REPO, "42"))
        self.assertFalse(mod.mediate_description_matches(own, REPO, "4"))
        other = (
            f"{mod.mediate_issue_marker_legacy('42')}\n"
            "**PR:** https://github.com/acme/other/pull/42\n"
        )
        self.assertFalse(mod.mediate_description_matches(other, REPO, "42"))
        self.assertFalse(
            mod.mediate_description_matches(mod.stuck_pr_marker("acme/other", "42"), REPO, "42")
        )
        markdown = (
            f"{mod.mediate_issue_marker_legacy('42')}\n"
            f"**PR:** [#42](https://github.com/{REPO}/pull/42)\n"
        )
        self.assertTrue(mod.mediate_description_matches(markdown, REPO, "42"))
        numbered = (
            f"{mod.mediate_issue_marker_legacy('42')}\n"
            f"**PR:** https://github.com/{REPO}/pull/420\n"
        )
        self.assertFalse(mod.mediate_description_matches(numbered, REPO, "42"))
        related = (
            f"{mod.mediate_issue_marker_legacy('42')}\n"
            "**PR:** [#42](https://github.com/other/project/pull/42)\n"
            f"[discussion](https://github.com/{REPO}/pull/42)\n"
        )
        self.assertFalse(mod.mediate_description_matches(related, REPO, "42"))

    def test_pr_is_approved_ignores_bots_and_changes(self) -> None:
        human_ok = {
            "user": {"login": "alice"},
            "state": "APPROVED",
            "submitted_at": "2026-10-15T15:00:00Z",
        }
        self.assertTrue(mod.pr_is_approved([human_ok]))
        self.assertFalse(
            mod.pr_is_approved(
                [
                    human_ok,
                    {
                        "user": {"login": "bob"},
                        "state": "CHANGES_REQUESTED",
                        "submitted_at": "2026-10-15T16:00:00Z",
                    },
                ]
            )
        )
        self.assertTrue(
            mod.pr_is_approved(
                [
                    human_ok,
                    {
                        "user": {"login": "devin-ai-integration[bot]"},
                        "state": "CHANGES_REQUESTED",
                        "submitted_at": "2026-10-15T16:00:00Z",
                    },
                ]
            )
        )
        self.assertFalse(
            mod.pr_is_approved(
                [
                    {
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                        "state": "APPROVED",
                        "submitted_at": "2026-10-15T15:00:00Z",
                    }
                ]
            )
        )
        self.assertFalse(
            mod.pr_is_approved(
                [
                    {
                        "user": {"login": "chatgpt-codex-connector", "type": "Bot"},
                        "state": "APPROVED",
                        "submitted_at": "2026-10-15T15:00:00Z",
                    }
                ]
            )
        )
        self.assertFalse(
            mod.pr_is_approved(
                [
                    {
                        "user": {"login": "chatgpt-codex-connector"},
                        "state": "APPROVED",
                        "submitted_at": "2026-10-15T15:00:00Z",
                    }
                ]
            )
        )
        self.assertFalse(mod.pr_is_approved([]))

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
        self.assertIn("Stuck PR threads:", self.agents)
        self.assertIn("stuck-pr-threads:", self.agents)
        self.assertIn("already approved", self.agents)
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
