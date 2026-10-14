#!/usr/bin/env python3
"""Event-matrix and structure tests for the Claude PR Fixer workflow.

Acceptance gate for .github/workflows/claude.yml:

    python -X utf8 -B tools/test_claude_pr_fixer.py

Optionally runs actionlint when it is on PATH.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "scripts" / "ci" / "claude_pr_fixer_gate.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "claude.yml"
HEREDOC = "CLAUDE_PR_FIXER_GATE"
REPO = "acme/example"


def load_gate():
    spec = importlib.util.spec_from_file_location("claude_pr_fixer_gate", GATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_gate()


def pr_payload(
    *,
    number: int = 42,
    state: str = "open",
    head_repo: str = REPO,
    head_ref: str = "feature/fix",
    head_sha: str = "a" * 40,
    author_association: str = "CONTRIBUTOR",
    author: str = "lucas",
) -> dict:
    return {
        "number": number,
        "state": state,
        "author_association": author_association,
        "user": {"login": author, "type": "User"},
        "head": {"ref": head_ref, "sha": head_sha, "repo": {"full_name": head_repo}},
    }


def comment(
    *,
    body: str,
    association: str = "CONTRIBUTOR",
    user_type: str = "User",
    login: str = "alice",
) -> dict:
    return {
        "body": body,
        "author_association": association,
        "user": {"type": user_type, "login": login},
    }


# Repository permissions as the collaborators API would report them.
PERMS = {
    "lucas": "admin",
    "alice": "write",
    "maint": "maintain",
    "reader": "read",
    "mallory": "none",
}


def lookup(login: str) -> str:
    if login == "boom":
        raise RuntimeError("api down")
    return PERMS.get(login, "none")


def decide(
    event_name: str,
    event: dict,
    repository: str = REPO,
    pr: dict | None = None,
    permission_lookup=lookup,
) -> dict:
    return gate.evaluate(event_name, event, repository, pr=pr, permission_lookup=permission_lookup)


class MentionMatrixTests(unittest.TestCase):
    def test_member_whole_word_on_same_repo_pr_comment(self) -> None:
        event = {
            "comment": comment(body="please @claude fix the lint"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload())
        self.assertEqual(got["mention"], "true")
        self.assertEqual(got["devin"], "false")
        self.assertEqual(got["same_repo"], "true")
        self.assertEqual(got["head_ref"], "feature/fix")
        self.assertEqual(got["head_sha"], "a" * 40)

    def test_member_mention_on_outsider_pr_is_skipped(self) -> None:
        event = {
            "comment": comment(body="@claude please"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide(
            "issue_comment",
            event,
            pr=pr_payload(author="reader", author_association="MEMBER"),
        )
        self.assertEqual(got["mention"], "false")
        self.assertEqual(got["same_repo"], "true")

    def test_contributor_commenter_with_write_is_allowed(self) -> None:
        event = {
            "comment": comment(body="@claude fix", association="CONTRIBUTOR", login="alice"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload())
        self.assertEqual(got["mention"], "true")
        self.assertEqual(got["commenter_association"], "CONTRIBUTOR")
        self.assertEqual(got["commenter_permission"], "write")

    def test_read_only_commenter_is_rejected(self) -> None:
        event = {
            "comment": comment(body="@claude fix", association="MEMBER", login="reader"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload())
        self.assertEqual(got["mention"], "false")
        self.assertEqual(got["commenter_permission"], "read")

    def test_review_comment_mention(self) -> None:
        event = {
            "comment": comment(body="@claude"),
            "pull_request": pr_payload(),
        }
        got = decide("pull_request_review_comment", event)
        self.assertEqual(got["mention"], "true")
        self.assertEqual(got["head_ref"], "feature/fix")

    def test_plain_issue_is_skipped(self) -> None:
        event = {
            "comment": comment(body="@claude fix this"),
            "issue": {"number": 7, "state": "open"},
        }
        got = decide("issue_comment", event)
        self.assertEqual(got["mention"], "false")
        self.assertEqual(got["reason"], "not-a-pr")

    def test_fork_pr_is_skipped(self) -> None:
        event = {
            "comment": comment(body="@claude please"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload(head_repo="evil/fork"))
        self.assertEqual(got["mention"], "false")
        self.assertEqual(got["same_repo"], "false")

    def test_substring_mention_is_skipped(self) -> None:
        event = {
            "comment": comment(body="see @claude123 and claude"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload())
        self.assertEqual(got["mention"], "false")

    def test_email_like_mention_is_skipped(self) -> None:
        event = {
            "comment": comment(body="mail@claude.com"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload())
        self.assertEqual(got["mention"], "false")

    def test_bot_mention_is_skipped(self) -> None:
        event = {
            "comment": comment(body="@claude", user_type="Bot"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload())
        self.assertEqual(got["mention"], "false")

    def test_outsider_mention_is_skipped(self) -> None:
        event = {
            "comment": comment(body="@claude", association="MEMBER", login="mallory"),
            "issue": {"number": 42, "state": "open", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload())
        self.assertEqual(got["mention"], "false")

    def test_closed_pr_mention_is_skipped(self) -> None:
        event = {
            "comment": comment(body="@claude"),
            "issue": {"number": 42, "state": "closed", "pull_request": {"url": "x"}},
        }
        got = decide("issue_comment", event, pr=pr_payload(state="closed"))
        self.assertEqual(got["mention"], "false")


class DevinMatrixTests(unittest.TestCase):
    def _review(self, **pr_kw) -> dict:
        return {
            "review": {
                "user": {"login": "devin-ai-integration[bot]"},
                "state": "commented",
                "id": 1,
            },
            "pull_request": pr_payload(**pr_kw),
        }

    def test_devin_on_member_same_repo_pr(self) -> None:
        got = decide("pull_request_review", self._review())
        self.assertEqual(got["devin"], "true")
        self.assertEqual(got["mention"], "false")
        self.assertEqual(got["head_ref"], "feature/fix")

    def test_devin_changes_requested(self) -> None:
        event = self._review()
        event["review"]["state"] = "changes_requested"
        self.assertEqual(decide("pull_request_review", event)["devin"], "true")

    def test_devin_on_fork_is_skipped(self) -> None:
        got = decide("pull_request_review", self._review(head_repo="evil/fork"))
        self.assertEqual(got["devin"], "false")
        self.assertEqual(got["same_repo"], "false")

    def test_devin_on_non_member_pr_is_skipped(self) -> None:
        got = decide(
            "pull_request_review",
            self._review(author="reader", author_association="MEMBER"),
        )
        self.assertEqual(got["devin"], "false")
        self.assertEqual(got["pr_author_permission"], "read")

    def test_private_member_contributor_with_write_is_allowed(self) -> None:
        got = decide("pull_request_review", self._review(author_association="CONTRIBUTOR"))
        self.assertEqual(got["devin"], "true")
        self.assertEqual(got["pr_author_association"], "CONTRIBUTOR")
        self.assertEqual(got["pr_author_permission"], "admin")

    def test_maintain_author_is_allowed(self) -> None:
        got = decide("pull_request_review", self._review(author="maint"))
        self.assertEqual(got["devin"], "true")

    def test_contributor_without_permission_is_rejected(self) -> None:
        got = decide("pull_request_review", self._review(author="mallory"))
        self.assertEqual(got["devin"], "false")

    def test_permission_lookup_error_fails_closed(self) -> None:
        got = decide("pull_request_review", self._review(author="boom"))
        self.assertEqual(got["devin"], "false")
        self.assertEqual(got["pr_author_permission"], "none")

    def test_no_lookup_fails_closed(self) -> None:
        got = decide("pull_request_review", self._review(), permission_lookup=None)
        self.assertEqual(got["devin"], "false")

    def test_unsafe_login_is_not_looked_up(self) -> None:
        seen: list[str] = []

        def spy(login: str) -> str:
            seen.append(login)
            return "admin"

        got = decide(
            "pull_request_review",
            self._review(author="../../evil"),
            permission_lookup=spy,
        )
        self.assertEqual(got["devin"], "false")
        self.assertEqual(seen, [])

    def test_other_bot_review_is_skipped(self) -> None:
        event = self._review()
        event["review"]["user"]["login"] = "other-bot[bot]"
        self.assertEqual(decide("pull_request_review", event)["devin"], "false")

    def test_human_review_is_skipped(self) -> None:
        event = self._review()
        event["review"]["user"]["login"] = "alice"
        self.assertEqual(decide("pull_request_review", event)["devin"], "false")

    def test_unsafe_ref_is_rejected(self) -> None:
        got = decide("pull_request_review", self._review(head_ref="feat/../main"))
        self.assertEqual(got["head_ref"], "")
        self.assertEqual(got["head_sha"], "")
        self.assertEqual(got["devin"], "false")
        self.assertEqual(got["reason"], "unsafe-or-empty-ref")

    def test_missing_sha_is_rejected(self) -> None:
        got = decide("pull_request_review", self._review(head_sha="not-a-sha"))
        self.assertEqual(got["head_sha"], "")
        self.assertEqual(got["devin"], "false")

    def test_default_branch_ref_is_rejected(self) -> None:
        for name in ("master", "main", "HEAD"):
            got = decide("pull_request_review", self._review(head_ref=name))
            self.assertEqual(got["head_ref"], "", name)
            self.assertEqual(got["devin"], "false", name)


class WholeWordTests(unittest.TestCase):
    def test_patterns(self) -> None:
        self.assertTrue(gate.is_whole_word_mention("@claude"))
        self.assertTrue(gate.is_whole_word_mention("hey @claude!"))
        self.assertTrue(gate.is_whole_word_mention("`@claude`"))
        self.assertFalse(gate.is_whole_word_mention("@claude123"))
        self.assertFalse(gate.is_whole_word_mention("claude"))
        self.assertFalse(gate.is_whole_word_mention(""))
        self.assertFalse(gate.is_whole_word_mention(None))


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_no_workflow_level_concurrency(self) -> None:
        head = self.text.split("jobs:", 1)[0]
        self.assertNotIn("\nconcurrency:", head)
        self.assertFalse(head.lstrip().startswith("concurrency:"))

    def test_job_level_concurrency_does_not_cancel(self) -> None:
        self.assertIn("group: claude-${{ needs.resolve-pr.outputs.pr_number }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        mention = self._job("claude-mention")
        devin = self._job("devin-review-fix")
        self.assertIn("concurrency:", mention)
        self.assertIn("concurrency:", devin)

    def test_privileged_jobs_require_gate_outputs(self) -> None:
        mention = self._job("claude-mention")
        devin = self._job("devin-review-fix")
        self.assertIn("needs.resolve-pr.outputs.mention == 'true'", mention)
        self.assertIn("needs.resolve-pr.outputs.same_repo == 'true'", mention)
        self.assertIn("needs.resolve-pr.outputs.devin == 'true'", devin)
        self.assertIn("needs.resolve-pr.outputs.same_repo == 'true'", devin)

    def test_every_privileged_checkout_uses_pr_head_sha(self) -> None:
        mention = self._job("claude-mention")
        devin = self._job("devin-review-fix")
        needle = "ref: ${{ needs.resolve-pr.outputs.head_sha }}"
        self.assertIn(needle, mention)
        self.assertIn(needle, devin)
        self.assertNotIn("ref: ${{ needs.resolve-pr.outputs.head_ref }}", mention)
        self.assertNotIn("ref: ${{ github.event.pull_request.head.ref }}", mention)

    def test_privileged_jobs_attach_branch_before_push(self) -> None:
        mention = self._job("claude-mention")
        devin = self._job("devin-review-fix")
        attach = 'git checkout -B "$HEAD_REF" "$HEAD_SHA"'
        self.assertIn(attach, mention)
        self.assertIn(attach, devin)
        push = (
            "Bash(git push origin HEAD:refs/heads/"
            "${{ needs.resolve-pr.outputs.head_ref }})"
        )
        self.assertIn(push, mention)
        self.assertIn(push, devin)
        self.assertNotIn("HEAD:refs/heads/*", mention)
        self.assertNotIn("HEAD:refs/heads/*", devin)

    def test_setup_python_cache_uses_app_manifest(self) -> None:
        devin = self._job("devin-review-fix")
        self.assertIn("cache-dependency-path:", devin)
        self.assertIn("app/pyproject.toml", devin)

    def test_prompt_treats_review_and_files_as_untrusted(self) -> None:
        self.assertIn("UNTRUSTED DATA", self.text)
        self.assertIn("untrusted", self.text.lower())

    def test_tool_allowlist_and_denials(self) -> None:
        self.assertIn("--allowedTools", self.text)
        self.assertIn("--disallowedTools", self.text)
        self.assertIn("WebSearch", self.text)
        self.assertIn("WebFetch", self.text)
        self.assertIn(".github/workflows", self.text)
        self.assertIn("include_comments_by_actor: devin-ai-integration[bot]", self.text)

    def test_devin_auto_trigger_stays_but_is_permission_gated(self) -> None:
        self.assertIn("devin-ai-integration[bot]", self.text)
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("collaborators/{login}/permission", source)
        self.assertNotIn("MEMBER_ASSOC", source)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self._job("resolve-pr"))

    def test_acceptance_gates_before_push(self) -> None:
        devin = self._job("devin-review-fix")
        self.assertIn("acceptance gate", devin.lower())
        self.assertIn("ruff check .", devin)
        self.assertIn("python -m pytest -q", devin)
        self.assertNotIn("tools/ci_gate.py", devin)

    def test_embedded_gate_matches_source_file(self) -> None:
        start = f"python3 - <<'{HEREDOC}'\n"
        self.assertIn(start, self.text)
        after = self.text.split(start, 1)[1]
        closer = after.find(HEREDOC)
        self.assertGreater(closer, 0)
        embedded = textwrap.dedent(after[:closer]).rstrip("\n")
        source = GATE_PATH.read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(embedded, source)

    def test_plain_issues_are_not_resolved(self) -> None:
        resolve = self._job("resolve-pr")
        self.assertIn("github.event.issue.pull_request", resolve)
        self.assertIn("head_sha: ${{ steps.pr.outputs.head_sha }}", resolve)

    def _job(self, name: str) -> str:
        marker = f"  {name}:"
        start = self.text.find(marker)
        self.assertGreaterEqual(start, 0, name)
        nxt = self.text.find("\n  ", start + len(marker))
        while nxt != -1 and self.text[nxt + 3 : nxt + 4] in (" ", "\t"):
            nxt = self.text.find("\n  ", nxt + 1)
        return self.text[start:] if nxt == -1 else self.text[start:nxt]


class GateCliTests(unittest.TestCase):
    def test_cli_writes_outputs_for_member_mention(self) -> None:
        event = {
            "comment": comment(body="@claude please"),
            "issue": {"number": 9, "state": "open", "pull_request": {"url": "x"}},
            "pull_request": pr_payload(number=9),
        }
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            output_path = Path(tmp) / "out.txt"
            event_path.write_text(
                __import__("json").dumps(event),
                encoding="utf-8",
            )
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                'case "$*" in *collaborators/*/permission*) '
                'echo \'{"permission": "write"}\' ;; *) exit 1 ;; esac\n',
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            env = {
                **{k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")},
                "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
                "GITHUB_EVENT_NAME": "issue_comment",
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_OUTPUT": str(output_path),
            }
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-B", str(GATE_PATH)],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            body = output_path.read_text(encoding="utf-8")
            self.assertIn("mention=true", body)
            self.assertIn("head_ref=feature/fix", body)
            self.assertIn("head_sha=" + "a" * 40, body)
            self.assertIn("pr_author_association=CONTRIBUTOR", body)
            self.assertIn("commenter_permission=write", body)


class ActionlintTests(unittest.TestCase):
    def test_actionlint_when_available(self) -> None:
        exe = shutil.which("actionlint")
        if not exe:
            self.skipTest("actionlint not on PATH")
        result = subprocess.run(
            [exe, "-color", str(WORKFLOW_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
