#!/usr/bin/env python3
"""Unit tests for D10 E1 wiring: static C launch/relay, denies, no dead Python.

Run: python3 -X utf8 -B tools/test_sandbox_e1.py
"""
# R4-I3: ordinary-commit record of c6d9a437's wire-static-launcher edits
# so a later linear rebase (P8) cannot drop merge-only content.
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "ci"
WORKFLOW = ROOT / ".github" / "workflows" / "claude.yml"

DEAD_PYTHON = (
    "sandbox_launcher.py",
    "sandbox_launcher.pin",
    "sandbox_relay.py",
    "sandbox_relay.pin",
    "sandbox_e1_hooks.py",
    "sandbox_e1_install.sh",
)


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
        self.assertIn("--strict-mcp-config", mention)
        self.assertIn("--mcp-config /opt/csl-claude/mcp.empty.json", mention)


class DeadPythonGoneTests(unittest.TestCase):
    def test_python_launcher_relay_hooks_and_install_are_deleted(self) -> None:
        for name in DEAD_PYTHON:
            self.assertFalse((SCRIPTS / name).exists(), name)
        text = WORKFLOW.read_text(encoding="utf-8")
        mention = text.split("  claude-mention:", 1)[1].split("  mention-publish:", 1)[0]
        for name in ("sandbox_launch.c", "sandbox_relay.c", "sandbox_capture.c"):
            self.assertIn(f'install -m 0444 "$TRUSTED/{name}"', mention, name)
        for tag in ("CSL_C_LAUNCH", "CSL_C_RELAY", "CSL_C_CAPTURE",
                    "CSL_E1_LAUNCH", "CSL_E1_RELAY", "CSL_E1_INSTALL"):
            self.assertNotIn(f"<<'{tag}'\n", mention, tag)
        self.assertIn('"$GCC" -static', (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8"))
        paths = text.split("paths:", 1)[1].split("jobs:", 1)[0]
        for name in DEAD_PYTHON:
            self.assertNotIn(name, paths)


class U16U18ContractTests(unittest.TestCase):
    def test_bootstrap_records_both_sudo_shapes_and_not_unproven(self) -> None:
        boot = (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("accepted_shapes=sudo_fork,sudo_exec", boot)
        self.assertIn("u18_status=[A]", boot)
        relay = (SCRIPTS / "sandbox_relay.c").read_text(encoding="utf-8")
        self.assertIn("sudo_fork", relay)
        self.assertIn("sudo_exec", relay)
        self.assertNotIn('return "unproven"', relay)
        self.assertNotIn('not_relayed("unproven")', relay)


if __name__ == "__main__":
    unittest.main()
