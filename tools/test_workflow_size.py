#!/usr/bin/env python3
"""Fail CI if a GitHub workflow file can exceed GitHub's start-run size limit.

GitHub documents a 500 KB workflow-file limit (512,000 bytes). Files above it
create a run named after the path that fails with zero jobs. claude.yml on
main at 1f9e5113 was 746,382 bytes after E3 #264 and every Claude PR Fixer
run since then has been that failure (e.g. 40234128658).

The Claude PR Fixer was retired in CSL-238; the limit still applies to every workflow.

    python -X utf8 -B tools/test_workflow_size.py
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
# GitHub's documented hard limit: 500 KB == 512,000 bytes.
GITHUB_WORKFLOW_LIMIT = 512_000
# Fail well below that so a later inline cannot silently disable a workflow.
SAFE_WORKFLOW_LIMIT = 480_000

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

    def test_workflow_checks_still_loads(self) -> None:
        import yaml

        for name in ("workflow-checks.yml",):
            path = WORKFLOWS / name
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIn("jobs", data, name)
            self.assertTrue(data["jobs"], name)
            self.assertLessEqual(path.stat().st_size, SAFE_WORKFLOW_LIMIT, name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
