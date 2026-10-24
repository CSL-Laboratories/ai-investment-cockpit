#!/usr/bin/env python3
"""Unit tests for D10 E1 relay print, launcher U-15 refuse, and U-16/U-18 hooks.

Run: python3 -X utf8 -B tools/test_sandbox_e1.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "ci"
WORKFLOW = ROOT / ".github" / "workflows" / "claude.yml"


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


filt = load("sandbox_debug_filter")
relay = load("sandbox_relay")
launcher = load("sandbox_launcher")
hooks = load("sandbox_e1_hooks")

MATCH = filt.MATCH_PREFIX + "network, env" + filt.MATCH_SUFFIX


def capture(*lines: str) -> bytes:
    return "".join(line + "\n" for line in lines).encode("utf-8")


class RelayPrintTests(unittest.TestCase):
    def test_stop_commands_wraps_constant_alphabet_and_never_starts_body_with_colon(self) -> None:
        token = "ab" * 16
        text = relay.relay_print(capture(MATCH), min_wall_seconds=0.0, token=token)
        lines = text.splitlines()
        self.assertEqual(lines[0], f"::stop-commands::{token}")
        self.assertEqual(lines[-1], f"::{token}::")
        body = lines[1:-1]
        self.assertTrue(body[0].startswith("[Sandbox Linux] Wrapped command with bwrap ("))
        self.assertFalse(any(line.startswith("::") for line in body))
        self.assertIn("channel=S", body)
        self.assertIn("origin=unattributed", body)
        self.assertIn("first_bash=missing", body)

    def test_first_bash_bound_is_not_clause_three(self) -> None:
        text = relay.relay_print(
            capture(MATCH), first_bash="bound", min_wall_seconds=0.0, token="11" * 16
        )
        self.assertIn("first_bash=bound", text)
        self.assertIn("origin=unattributed", text)
        with self.assertRaises(ValueError):
            relay.format_public_body(
                filt.filter_capture(b""), None, first_bash="missing", origin="cli"
            )

    def test_ignores_argv_when_invoked_as_a_process(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SCRIPTS)
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-B", str(SCRIPTS / "sandbox_relay.py"), "/tmp/not-opened"],
            cwd=str(SCRIPTS),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NOT RELAYED reason=no_end", result.stdout)
        self.assertNotIn("/tmp/not-opened", result.stdout)


class LauncherEnvTests(unittest.TestCase):
    def test_refuses_u15_names_including_inherited_srt_debug(self) -> None:
        for name in launcher.U15_NAMES:
            with self.subTest(name=name):
                self.assertEqual(launcher.refused_debug({name: "true"}), name)
                self.assertEqual(launcher.refused_debug({name: "1"}), name)
                self.assertEqual(launcher.refused_debug({name: "false"}), name)
                self.assertEqual(launcher.refused_debug({name: ""}), name)
        self.assertIsNone(launcher.refused_debug({}))
        self.assertIsNone(launcher.refused_debug({"RUNNER_DEBUG": ""}))
        self.assertEqual(launcher.refused_debug({"RUNNER_DEBUG": "true"}), "RUNNER_DEBUG")
        self.assertTrue(launcher.parent_must_not_forward_stderr())

    def test_srt_debug_only_in_child_and_not_copied_from_parent(self) -> None:
        child = launcher.child_env(
            {
                "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                "HOME": "/tmp/evil",
            },
            home="/home/runner",
            user="runner",
        )
        self.assertEqual(child["SRT_DEBUG"], "1")
        self.assertEqual(child["HOME"], "/home/runner")
        self.assertNotIn("DEBUG_CLAUDE_AGENT_SDK", child)
        self.assertNotIn("ACTIONS_STEP_DEBUG", child)
        with self.assertRaises(RuntimeError):
            launcher.child_env(
                {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "SRT_DEBUG": "true"},
                home="/home/runner",
                user="runner",
            )
        with self.assertRaises(RuntimeError):
            launcher.child_env(
                {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "ACTIONS_STEP_DEBUG": "true"},
                home="/home/runner",
                user="runner",
            )


class U16U18HookTests(unittest.TestCase):
    def test_both_sudo_shapes_are_accepted_and_unproven_is_recorded(self) -> None:
        self.assertEqual(
            hooks.classify_parent(
                uid=0, euid=0, ppid_exe="/usr/bin/sudo", self_exe="/opt/csl-relay/bin/relay"
            ),
            "sudo_fork",
        )
        self.assertEqual(
            hooks.classify_parent(
                uid=0, euid=0, ppid_exe="/usr/bin/Runner.Worker", self_exe="/opt/csl-relay/bin/relay"
            ),
            "sudo_exec",
        )
        self.assertTrue(hooks.accept_parent_shape("sudo_fork"))
        self.assertTrue(hooks.accept_parent_shape("sudo_exec"))
        self.assertFalse(hooks.accept_parent_shape("unproven"))
        self.assertFalse(hooks.accept_parent_shape("bad_parent"))
        self.assertEqual(
            hooks.classify_parent(uid=1000, euid=1000, ppid_exe="/bin/bash", self_exe="/usr/bin/python3"),
            "bad_parent",
        )

    def test_u16_record_stays_unproven_and_names_org_residual(self) -> None:
        inv = hooks.u16_inventory()
        self.assertEqual(inv["u16_status"], "[A]")
        self.assertEqual(inv["image_match"], "unproven")
        self.assertEqual(inv["org_variable_names"], "unreadable")
        self.assertEqual(inv["repo_variable_names"], "empty")
        self.assertEqual(inv["see"], "f-u16.md")
        self.assertIn(inv["run0"], {"absent", "present_unrun"})
        with tempfile.TemporaryDirectory() as tmp:
            hooks.write_records(tmp, shape="unproven")
            u18 = (Path(tmp) / "u18.record").read_text(encoding="ascii")
            self.assertIn("hosted_pick=unproven", u18)
            self.assertIn("accepted_shapes=sudo_fork,sudo_exec", u18)


class MentionDenySpellingTests(unittest.TestCase):
    def test_file_command_denies_use_edit_double_slash_form(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        mention = text.split("  claude-mention:", 1)[1].split("  mention-publish:", 1)[0]
        settings = json.loads(mention.split("settings: |", 1)[1].split("prompt:", 1)[0])
        deny = settings["permissions"]["deny"]
        self.assertIn("Read(/${{ runner.temp }}/_runner_file_commands/**)", deny)
        self.assertIn("Edit(/${{ runner.temp }}/_runner_file_commands/**)", deny)
        self.assertIn("Edit(/${{ runner.tool_cache }}/**)", deny)
        self.assertNotIn("Write(/${{ runner.temp }}/_runner_file_commands/**)", deny)
        self.assertNotIn("Write(//${{ runner.temp }}/_runner_file_commands/**)", deny)
        self.assertNotIn("Write(/${{ runner.tool_cache }}/**)", deny)
        for rule in (
            "Read(//opt/csl-claude/**)",
            "Edit(//opt/csl-claude/**)",
            "Read(//opt/csl-relay/**)",
            "Edit(//opt/csl-relay/**)",
            "Read(//var/lib/csl-relay/**)",
            "Edit(//var/lib/csl-relay/**)",
            "Read(//home/runner/work/_actions/**)",
            "Edit(//home/runner/work/_actions/**)",
            "Edit(//opt/hostedtoolcache/**)",
            "Edit(//proc/**)",
            "Edit(//dev/fd/**)",
            "Edit(//dev/stderr)",
            "Read(//dev/stdout)",
            "Edit(//dev/stdout)",
        ):
            self.assertIn(rule, deny)
        # Never flatten // to a single slash on host paths; never triple-slash the temp var.
        self.assertNotIn("Edit(/opt/csl-relay/**)", deny)
        self.assertNotIn("Edit(/opt/csl-claude/**)", deny)
        self.assertNotIn("Edit(/var/lib/csl-relay/**)", deny)
        self.assertNotIn("Edit(//${{ runner.temp }}/_runner_file_commands/**)", deny)
        sandbox = settings["sandbox"]
        self.assertTrue(sandbox["enabled"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertFalse(sandbox["autoAllowBashIfSandboxed"])
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertIn("${{ runner.temp }}/_runner_file_commands", sandbox["filesystem"]["denyWrite"])

    def test_srt_debug_is_not_on_the_job_or_action_env(self) -> None:
        mention = WORKFLOW.read_text(encoding="utf-8").split("  claude-mention:", 1)[1].split(
            "  mention-publish:", 1
        )[0]
        self.assertNotIn("SRT_DEBUG:", mention)
        self.assertNotIn("show_full_output:", mention)
        self.assertNotIn("DEBUG_CLAUDE_AGENT_SDK:", mention)
        self.assertNotIn("ACTIONS_STEP_DEBUG:", mention)
        self.assertIn("path_to_claude_code_executable: /opt/csl-claude/launch", mention)
        self.assertIn("shell: /usr/bin/sudo -n /opt/csl-relay/bin/relay {0}", mention)
        after_claude = mention.split("      - id: claude\n", 1)[1]
        next_step = after_claude.split("\n      - name: ", 1)[1].split("\n", 1)[0]
        self.assertEqual(next_step, "Relay sandbox fd-2 (E1)")


class EmbeddedAndPinTests(unittest.TestCase):
    def test_production_launcher_and_relay_are_static_c_not_python_embeds(self) -> None:
        mention = WORKFLOW.read_text(encoding="utf-8").split("  claude-mention:", 1)[1].split(
            "  mention-publish:", 1
        )[0]
        for tag in ("CSL_C_LAUNCH", "CSL_C_RELAY", "CSL_C_CAPTURE"):
            self.assertIn(f"<<'{tag}'\n", mention, tag)
        for tag in ("CSL_E1_LAUNCH", "CSL_E1_RELAY", "CSL_E1_INSTALL"):
            self.assertNotIn(f"<<'{tag}'\n", mention, tag)
        self.assertIn('"$GCC" -static', mention)

    def test_relay_and_launcher_sha256_match_pin_files(self) -> None:
        for name in ("sandbox_relay", "sandbox_launcher"):
            expected = (SCRIPTS / f"{name}.pin").read_text(encoding="ascii").splitlines()[-1].strip()
            got = hashlib.sha256((SCRIPTS / f"{name}.py").read_bytes()).hexdigest()
            self.assertEqual(got, expected, name)

    def test_shred_overwrites_then_unlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "capture")
            Path(path).write_bytes(b"secret-wrap-line\n")
            relay.shred_capture(path)
            self.assertFalse(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
