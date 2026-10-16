#!/usr/bin/env python3
"""Offline push races and terminal handback; no live/provider qualification."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/ci/claude_fixer_guard.py"
spec = importlib.util.spec_from_file_location("guard", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
A, B, C = "a" * 40, "b" * 40, "c" * 40
CONFIG = {"repo": "acme/example", "number": "7", "base": A, "ref": "fix"}


def pr(sha=A):
    return {"state": "open", "head": {"sha": sha, "ref": "fix", "repo": {"full_name": "acme/example"}}}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.state = self.path / "attempt.json"
        self.execution = self.path / "execution.json"
        self.execution.write_text(json.dumps([{"type": "result", "subtype": "success", "num_turns": 12}]))

    def push(self, read=lambda _: pr(), updates=None, config=CONFIG, remote="origin"):
        return m.guard(config, updates or f"HEAD {B} refs/heads/fix {A}\n", remote, self.state,
                       read=read, git=lambda args: B if args[1] == "rev-parse" else A)

    def test_live_head_movement_stops_and_consumes_attempt(self):
        with self.assertRaisesRegex(ValueError, "remote moved"):
            self.push(lambda _: pr(C))
        with self.assertRaises(FileExistsError):
            self.push()
        self.assertEqual(json.loads(self.state.read_text()), {})

    def test_api_denial_stops_without_retry(self):
        def denied(_):
            raise subprocess.CalledProcessError(1, "gh")
        with self.assertRaises(subprocess.CalledProcessError):
            self.push(denied)
        with self.assertRaises(FileExistsError):
            self.push()

    def test_advertised_remote_movement_stops_even_if_api_is_stale(self):
        with self.assertRaises(ValueError):
            self.push(updates=f"HEAD {B} refs/heads/fix {C}\n")

    def test_valid_push_check_records_candidate_not_success(self):
        self.push()
        self.assertEqual(json.loads(self.state.read_text()), {"candidate": B})
        with self.assertRaises(FileExistsError):
            self.push()

    def test_wrong_target_closed_fork_and_missing_identity_are_rejected(self):
        for bad in [pr() | {"state": "closed"}, {"state": "open", "head": {}},
                    pr() | {"head": pr()["head"] | {"ref": "other"}},
                    pr() | {"head": pr()["head"] | {"repo": {"full_name": "fork/example"}}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.current_pr(CONFIG, lambda _, bad=bad: bad)
        for ref in ("main", "master", "HEAD", "../bad", "fix\nother"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                m.validate(CONFIG | {"ref": ref})

    def test_delete_multiple_refs_no_change_and_non_origin_rejected(self):
        for rows, remote in [(f"HEAD {'0'*40} refs/heads/fix {A}", "origin"),
                             (f"HEAD {A} refs/heads/fix {A}", "origin"),
                             (f"HEAD {B} refs/heads/fix {A}\nHEAD {B} refs/heads/other {A}", "origin"),
                             (f"HEAD {B} refs/heads/fix {A}", "other")]:
            with self.subTest(rows=rows, remote=remote):
                self.state.unlink(missing_ok=True)
                with self.assertRaises(ValueError):
                    self.push(updates=rows, remote=remote)

    def test_local_rewrite_rejected(self):
        with self.assertRaises(ValueError):
            m.guard(CONFIG, f"HEAD {B} refs/heads/fix {A}", "origin", self.state,
                    read=lambda _: pr(), git=lambda args: B if args[1] == "rev-parse" else C)

    def test_no_false_handled_receipt_for_unpushed_or_unknown_candidate(self):
        self.push()
        for compare in ("behind", "diverged", None):
            body = m.terminal(CONFIG, self.state, str(self.execution), "success",
                              read=lambda path, compare=compare: pr(C) if "/pulls/" in path else {"status": compare})
            self.assertIn("`unverified`", body)
            self.assertNotIn("claude-fixer-result:", body)
            self.assertNotIn('"status":"addressed"', body)

    def test_reachable_candidate_audit_does_not_claim_findings_handled(self):
        self.push()
        body = m.terminal(CONFIG, self.state, str(self.execution), "success",
                          read=lambda path: pr(B) if "/pulls/" in path else {"status": "ahead"})
        self.assertIn(f"`{B}`", body)
        self.assertIn("not an addressed", body)
        self.assertNotIn("claude-fixer-result:", body)

    def test_denied_command_turn_exhaustion_and_missing_output_have_terminal_audit(self):
        for record, expected in [({"permission_denials": [{"tool_input": "PRIVATE"}]}, "command-denied"),
                                 ({"num_turns": 38}, "turn-limit"),
                                 ({"subtype": "error_max_turns"}, "turn-limit"),
                                 ({"is_error": True}, "model-error")]:
            data = {"type": "result", "subtype": "success", "num_turns": 12} | record
            self.execution.write_text(json.dumps([data]))
            body = m.terminal(CONFIG, self.state, str(self.execution), "success")
            self.assertIn(expected, body)
            self.assertNotIn("PRIVATE", body)
            self.assertIn("three-attempt", body)
        self.assertEqual(m.execution_reason("", "failure"), "model-step-failed-or-timed-out")
        self.assertEqual(m.execution_reason("", "success"), "execution-result-unavailable")

    def test_comment_denial_keeps_summary_and_returns_failure(self):
        (self.path / "context.json").write_text(json.dumps(CONFIG))
        summary = self.path / "summary"
        with patch.object(m, "__file__", str(self.path / "pre-push")), \
                patch.object(sys, "argv", ["pre-push", "terminal"]), \
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary), "MODEL_OUTCOME": "failure"}), \
                patch.object(m, "command", side_effect=subprocess.CalledProcessError(1, "gh")), \
                patch("builtins.print"):
            self.assertEqual(m.main(), 1)
        self.assertIn("model-step-failed", summary.read_text())


class RealGitRaceTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX runner hook launcher")
    def test_installed_hook_race_after_advertisement_keeps_server_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local, remote, hooks, binaries = [root / name for name in ("local", "remote.git", "hooks", "bin")]
            hooks.mkdir()
            binaries.mkdir()
            def git(*args, cwd=local, check=True, env=None):
                return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check, env=env)
            git("init", "--bare", str(remote), cwd=root)
            git("init", str(local), cwd=root)
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("checkout", "-b", "fix")
            (local / "file").write_text("base")
            git("add", "file")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD").stdout.strip()
            git("remote", "add", "origin", str(remote))
            git("push", "origin", "HEAD:refs/heads/fix")
            tree = git("rev-parse", "HEAD^{tree}").stdout.strip()
            other = git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit-tree", tree, "-p", base, "-m", "external", cwd=remote).stdout.strip()
            # The hook receives Git's already-advertised base. During its final
            # API read, a separate writer advances the server. Return the old
            # observation so only the server's ordinary ref comparison can stop it.
            fake_api = binaries / "gh"
            fake_api.write_text("#!/usr/bin/env python3\nimport subprocess\nsubprocess.run(" +
                                repr(["git", "--git-dir", str(remote), "update-ref", "refs/heads/fix", other, base]) +
                                ", check=True)\nprint(" + repr(json.dumps(pr(base))) + ")\n")
            fake_api.chmod(0o700)
            hook = hooks / "pre-push"
            hook.write_text(SOURCE.read_text(encoding="utf-8"))
            env = os.environ | {"REPO": CONFIG["repo"], "PR_NUMBER": "7", "HEAD_SHA": base,
                                "HEAD_REF": "fix", "PATH": str(binaries) + os.pathsep + os.environ["PATH"]}
            subprocess.run([sys.executable, str(hook), "install"], cwd=local, env=env, check=True)
            (local / "file").write_text("fix")
            git("commit", "-am", "fix")
            candidate = git("rev-parse", "HEAD").stdout.strip()
            result = git("push", "origin", "HEAD:refs/heads/fix", check=False, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("remote rejected", result.stderr)
            self.assertEqual(git("rev-parse", "refs/heads/fix", cwd=remote).stdout.strip(), other)
            self.assertEqual(json.loads((hooks / "attempt.json").read_text()), {"candidate": candidate})
            retry = git("push", "origin", "HEAD:refs/heads/fix", check=False, env=env)
            self.assertNotEqual(retry.returncode, 0)
            self.assertIn("Stop this run", retry.stderr)

    def test_post_check_race_rejected_by_ordinary_push_then_no_second_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, local = root / "remote.git", root / "local"
            def git(*args, cwd=root, check=True):
                return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)
            git("init", "--bare", str(remote))
            git("init", str(local))
            git("config", "user.name", "Fixture", cwd=local)
            git("config", "user.email", "fixture@example.invalid", cwd=local)
            git("checkout", "-b", "fix", cwd=local)
            (local / "file").write_text("base")
            git("add", "file", cwd=local)
            git("commit", "-m", "base", cwd=local)
            base = git("rev-parse", "HEAD", cwd=local).stdout.strip()
            git("remote", "add", "origin", str(remote), cwd=local)
            git("push", "origin", "HEAD:refs/heads/fix", cwd=local)
            (local / "file").write_text("fix")
            git("commit", "-am", "fix", cwd=local)
            candidate = git("rev-parse", "HEAD", cwd=local).stdout.strip()
            config = CONFIG | {"base": base}
            state = root / "attempt.json"
            # Actual candidate, real ancestry, API fixture for the first check.
            m.guard(config, f"HEAD {candidate} refs/heads/fix {base}", "origin", state,
                    read=lambda _: pr(base), git=lambda args: git(*args[1:], cwd=local).stdout.strip())
            # External writer advances after the check. Copying candidate objects
            # into the fixture remote allows creating a divergent commit without fetch.
            git("push", "origin", "HEAD:refs/heads/fixture-objects", cwd=local)
            tree = git("rev-parse", f"{base}^{{tree}}", cwd=local).stdout.strip()
            other = subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                    "commit-tree", tree, "-p", base, "-m", "external"],
                                   cwd=remote, text=True, capture_output=True, check=True).stdout.strip()
            git("update-ref", "refs/heads/fix", other, base, cwd=remote)
            result = git("push", "origin", "HEAD:refs/heads/fix", cwd=local, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("rejected", result.stderr)
            self.assertEqual(git("rev-parse", "refs/heads/fix", cwd=remote).stdout.strip(), other)
            with self.assertRaises(FileExistsError):
                m.guard(config, f"HEAD {candidate} refs/heads/fix {other}", "origin", state)

    @unittest.skipIf(os.name == "nt", "POSIX runner hook launcher")
    def test_installed_hook_is_invoked_by_git_and_rejects_second_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local, remote, hooks, binaries = [root / name for name in ("local", "remote.git", "hooks", "bin")]
            hooks.mkdir()
            binaries.mkdir()
            def git(*args, cwd=root, check=True, env=None):
                return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check, env=env)
            git("init", "--bare", str(remote))
            git("init", str(local))
            git("config", "user.name", "Fixture", cwd=local)
            git("config", "user.email", "fixture@example.invalid", cwd=local)
            git("checkout", "-b", "fix", cwd=local)
            (local / "file").write_text("base")
            git("add", "file", cwd=local)
            git("commit", "-m", "base", cwd=local)
            base = git("rev-parse", "HEAD", cwd=local).stdout.strip()
            git("remote", "add", "origin", str(remote), cwd=local)
            git("push", "origin", "HEAD:refs/heads/fix", cwd=local)
            hook = hooks / "pre-push"
            hook.write_text(SOURCE.read_text(encoding="utf-8"))
            fake_api = binaries / "gh"
            fake_api.write_text("#!/usr/bin/env python3\nprint(" + repr(json.dumps(pr(base))) + ")\n")
            fake_api.chmod(0o700)
            env = os.environ | {"REPO": CONFIG["repo"], "PR_NUMBER": "7", "HEAD_SHA": base,
                                "HEAD_REF": "fix", "PATH": str(binaries) + os.pathsep + os.environ["PATH"]}
            subprocess.run([sys.executable, str(hook), "install"], cwd=local, env=env, check=True)
            (local / "file").write_text("fix")
            git("commit", "-am", "fix", cwd=local)
            candidate = git("rev-parse", "HEAD", cwd=local).stdout.strip()
            git("push", "origin", "HEAD:refs/heads/fix", cwd=local, env=env)
            self.assertEqual(json.loads((hooks / "attempt.json").read_text()), {"candidate": candidate})
            (local / "file").write_text("second")
            git("commit", "-am", "second", cwd=local)
            rejected = git("push", "origin", "HEAD:refs/heads/fix", cwd=local, env=env, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Stop this run", rejected.stderr)
            self.assertEqual(git("rev-parse", "refs/heads/fix", cwd=remote).stdout.strip(), candidate)


class WorkflowTests(unittest.TestCase):
    def test_workflow_embeds_trusted_guard_and_always_terminal_after_model(self):
        text = (ROOT / ".github/workflows/claude.yml").read_text(encoding="utf-8")
        chunks = text.split("<<'CLAUDE_FIXER_GUARD'\n")[1:]
        self.assertEqual(len(chunks), 2)
        for chunk in chunks:
            embedded = textwrap.dedent(chunk.split("          CLAUDE_FIXER_GUARD", 1)[0]).rstrip()
            self.assertEqual(embedded, SOURCE.read_text(encoding="utf-8").rstrip())
        self.assertEqual(text.count("if: always() && steps.push_guard.outcome == 'success'"), 2)
        self.assertEqual(text.count("timeout-minutes: 20"), 2)
        self.assertEqual(text.count("--max-turns 30"), 2)
        for body in text.split("      - id: claude\n")[1:]:
            allow = body.split("--allowedTools", 1)[1].split("--disallowedTools", 1)[0]
            for command in ("fetch", "pull", "rebase", "merge", "config"):
                self.assertNotIn(f"Bash(git {command}", allow)
            self.assertIn("If push is rejected or uncertain, STOP", body)
            self.assertIn("before emitting any addressed receipt", body)
            self.assertIn("has no Linear, Context7 or Parallel tools", body)


if __name__ == "__main__":
    unittest.main()
