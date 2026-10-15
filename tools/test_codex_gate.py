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


def comment(body: str, created: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"body": body}
    if created:
        item["created_at"] = created
    return item


def review(body: str, *, login: str = "chatgpt-codex-connector[bot]", sha: str = SHA) -> dict:
    return {"body": body, "user": {"login": login}, "commit_id": sha}


class FakeGH:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.dispatches: list[tuple[str, dict[str, Any], str]] = []
        self.workflows: list[tuple[str, dict[str, str]]] = []
        self.comments: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.prs: list[dict[str, Any]] = []
        self.fail_workflow = False

    def get(self, path: str) -> Any:
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

    def dispatch(self, event_type: str, payload: dict[str, Any], repository: str) -> None:
        self.dispatches.append((event_type, payload, repository))

    def workflow_run(self, workflow: str, fields: dict[str, str]) -> None:
        if self.fail_workflow:
            raise OSError("workflow missing")
        self.workflows.append((workflow, fields))


class ParseVerdictTests(unittest.TestCase):
    def test_last_verdict_wins(self) -> None:
        self.assertEqual(mod.parse_verdict("CODEX-GATE: FIX\nCODEX-GATE: READY"), "READY")
        self.assertEqual(mod.parse_verdict("see CODEX-GATE: MEDIATE please"), "MEDIATE")
        self.assertEqual(mod.parse_verdict("CODEX-GATE: NOPE"), "")
        self.assertEqual(mod.parse_verdict(""), "")
        self.assertEqual(mod.parse_verdict(None), "")


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
            run_mediator=lambda: mediated.append("watch"),
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
        _, _, labels, dispatched, mediated = self._apply("MEDIATE")
        self.assertEqual(mediated, ["watch"])
        self.assertEqual(dispatched, [])
        self.assertEqual(labels, [])

    def test_skip_is_noop(self) -> None:
        logs, posted, labels, dispatched, mediated = self._apply("skip-waiting")
        self.assertEqual(posted, [])
        self.assertEqual(labels, [])
        self.assertEqual(dispatched, [])
        self.assertEqual(mediated, [])
        self.assertTrue(logs)


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

    def test_mediate_missing_workflow_notices(self) -> None:
        gh = FakeGH()
        gh.fail_workflow = True
        buf = io.StringIO()
        with patch("sys.stdout", buf):
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
        self.assertIn("stuck-thread-watch", buf.getvalue())


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
        self.assertIn("github.event_name == 'pull_request'", self.text)

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
        self.assertIn("Parallel **deep research**", self.agents)
        self.assertIn("Task API", self.agents)
        self.assertIn("Deep research brief", self.agents)
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("deep-research brief", claude)
        self.assertIn("AGENTS.md", claude)

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
