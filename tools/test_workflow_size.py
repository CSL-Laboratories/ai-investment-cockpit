#!/usr/bin/env python3
"""Fail CI if a GitHub workflow file can exceed GitHub's start-run size limit.

GitHub documents a 500 KB workflow-file limit (512,000 bytes). Files above it
create a run named after the path that fails with zero jobs. claude.yml on
main at 1f9e5113 was 746,382 bytes after E3 #264 and every Claude PR Fixer
run since then has been that failure (e.g. 40234128658).

    python -X utf8 -B tools/test_workflow_size.py
"""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
# GitHub's documented hard limit: 500 KB == 512,000 bytes.
GITHUB_WORKFLOW_LIMIT = 512_000
# Fail well below that so a later inline cannot silently disable the fixer.
SAFE_WORKFLOW_LIMIT = 480_000

PINNED = {
    "scripts/ci/sandbox_filter.c": "c7d218317d057037e4c3712af5e79ea38b5763f67a6bfe1e6bcb55d31f29eb45",
    "scripts/ci/sandbox_c.pin": "5bde63914f3c8a41123418c6f31e911370d6cb21d6598cf62f6fee87b55bb079",
    "scripts/ci/claude_fixer_publish.py": "cd1ab4234edb098748fc24758f12422a3243f74d827a699a16496b5e67d0f452",
    "scripts/ci/sandbox_e3_cli_pin.sh": None,  # pin string is inside the file
    "scripts/ci/sandbox_e3_record.py": None,
    "scripts/ci/sandbox_e3_bind.py": None,
    "scripts/ci/sandbox_e3_bootstrap.sh": None,
    "scripts/ci/sandbox_e3_action_bun.sh": None,
}

CLI_SHA = "520bc6c1f40af466bd03a278b5ae1af1a2938bc7de5b9e5cb15526da20d71cfe"
SDK_SHA = "48480d49d4493e2aec28227d8db8f8432743c7aa32facbdb9a7ce0a0363f7503"
ACTION_PIN = "a5a423f75c251dcbff8a74e99f83b2cb03c72bfa"

BANNED_CLAUDE_HEREDOCS = (
    "CSL_C_COMMON_H",
    "CSL_C_COMMON_C",
    "CSL_C_FILTER_H",
    "CSL_C_FILTER_C",
    "CSL_C_ARGV_H",
    "CSL_C_ARGV_C",
    "CSL_C_LAUNCH",
    "CSL_C_CAPTURE",
    "CSL_C_RELAY",
    "CSL_C_PIN",
    "CSL_E3_ENVIRON",
    "CSL_E3_RECORD",
    "CSL_E3_BIND",
    "CSL_E3_BOOTSTRAP",
    "CSL_E3_ACTION_BUN",
    "CSL_E3_CLI_PIN",
    "CLAUDE_FIXER_OUTBOX",
    "CLAUDE_FIXER_RECEIPTS",
    "CLAUDE_FIXER_PUBLISH",
    "CLAUDE_FIXER_PREFLIGHT",
    "CLAUDE_PR_FIXER_GATE",
    "CLAUDE_DENIALS_REPORT",
)


class WorkflowSizeTests(unittest.TestCase):
    def test_every_workflow_file_stays_under_the_safe_limit(self) -> None:
        over = []
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            size = path.stat().st_size
            self.assertLessEqual(
                size,
                SAFE_WORKFLOW_LIMIT,
                f"{path.relative_to(ROOT)}: {size} bytes. "
                f"Workflow files must stay at or below {SAFE_WORKFLOW_LIMIT} bytes. "
                f"GitHub's hard limit is {GITHUB_WORKFLOW_LIMIT} bytes (500 KB); "
                "above it, GitHub creates a run named after the file that fails "
                "immediately with no jobs.",
            )
            if size > GITHUB_WORKFLOW_LIMIT:
                over.append(f"{path.name}: {size}")
        self.assertEqual(over, [])

    def test_safe_limit_is_below_githubs_hard_limit(self) -> None:
        self.assertLess(SAFE_WORKFLOW_LIMIT, GITHUB_WORKFLOW_LIMIT)
        self.assertEqual(GITHUB_WORKFLOW_LIMIT, 512_000)

    def test_claude_yml_copies_trusted_sources_from_workflow_sha(self) -> None:
        text = (WORKFLOWS / "claude.yml").read_text(encoding="utf-8")
        self.assertLess(len(text.encode("utf-8")), SAFE_WORKFLOW_LIMIT)
        self.assertIn("ref: ${{ github.workflow_sha }}", text)
        self.assertIn("path: .csl-trusted-workflow", text)
        self.assertIn("persist-credentials: false", text)
        self.assertGreaterEqual(text.count("Checkout trusted workflow commit"), 7)
        self.assertGreaterEqual(text.count("github.workflow_sha"), 7)
        self.assertIn('install -m 0444 "$TRUSTED/sandbox_filter.c"', text)
        self.assertIn('install -m 0444 "$TRUSTED/claude_fixer_publish.py"', text)
        self.assertIn('install -m 0755 "$TRUSTED/claude_fixer_outbox.py"', text)
        self.assertIn('python3 -I "$TRUSTED/claude_fixer_preflight.py"', text)
        self.assertIn('python3 -I "$TRUSTED/claude_pr_fixer_gate.py"', text)
        self.assertIn(
            'install -m 0444 "$TRUSTED/claude_denials.py" /opt/csl-claude/lib/claude_denials.py',
            text,
        )
        self.assertIn('"$interpreter" -I "$DENIALS" "$EXECUTION_FILE"', text)
        self.assertNotIn(
            '"$interpreter" -I "$TRUSTED/claude_denials.py" "$EXECUTION_FILE"',
            text,
        )
        self.assertNotIn("ref: ${{ github.event.pull_request.head.sha }}", text)
        for tag in BANNED_CLAUDE_HEREDOCS:
            self.assertNotIn(f"<<'{tag}'", text, tag)

    def test_pinned_hashes_are_unchanged(self) -> None:
        for rel, digest in PINNED.items():
            data = (ROOT / rel).read_bytes()
            got = hashlib.sha256(data).hexdigest()
            if digest is not None:
                self.assertEqual(got, digest, rel)
        pin = (ROOT / "scripts/ci/sandbox_c.pin").read_text(encoding="utf-8")
        self.assertIn(
            "c7d218317d057037e4c3712af5e79ea38b5763f67a6bfe1e6bcb55d31f29eb45  sandbox_filter.c",
            pin,
        )
        for rel in (
            "scripts/ci/sandbox_e3_cli_pin.sh",
            "scripts/ci/sandbox_e3_record.py",
            "scripts/ci/install_pinned_claude_code.sh",
            "scripts/ci/claude-code-2.1.306-linux-x64.pin",
            ".github/workflows/claude-conflict-writer.yml",
        ):
            self.assertIn(CLI_SHA, (ROOT / rel).read_text(encoding="utf-8"), rel)
        for rel in (
            "scripts/ci/sandbox_e3_record.py",
            "scripts/ci/sandbox_e3_bind.py",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(SDK_SHA, text, rel)
            self.assertIn(ACTION_PIN, text, rel)
        claude = (WORKFLOWS / "claude.yml").read_text(encoding="utf-8")
        self.assertIn(ACTION_PIN, claude)
        self.assertEqual(claude.count(ACTION_PIN), 2)

    def test_conflict_writer_and_codex_gate_still_load(self) -> None:
        import yaml

        for name in ("claude-conflict-writer.yml", "codex-gate.yml", "claude.yml"):
            path = WORKFLOWS / name
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIn("jobs", data, name)
            self.assertTrue(data["jobs"], name)
            self.assertLessEqual(path.stat().st_size, SAFE_WORKFLOW_LIMIT, name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
