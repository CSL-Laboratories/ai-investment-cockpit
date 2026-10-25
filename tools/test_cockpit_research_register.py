"""Negative controls for the Cockpit living research register."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cockpit_research_register",
    ROOT / "tools" / "check_cockpit_research_register.py",
)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def _with_table(rows: str) -> str:
    return (
        "preamble\n"
        f"{CHECKER.BEGIN}\n"
        "| parent | linear | research_record | status |\n"
        "| --- | --- | --- | --- |\n"
        f"{rows}"
        f"{CHECKER.END}\n"
    )


def _valid_row(n: int, status: str = "open", record: str = "none") -> str:
    parent = f"CSL-{n}"
    return (
        f"| {parent} | [{parent}](https://linear.app/csl-laboratories/issue/{parent}) "
        f"| {record} | {status} |\n"
    )


class CockpitResearchRegisterTests(unittest.TestCase):
    def test_checked_in_register_passes(self) -> None:
        CHECKER.validate(
            (ROOT / "docs/replacement/amendments/RESEARCH-REGISTER.md").read_text(
                encoding="utf-8"
            )
        )

    def test_missing_parent_fails(self) -> None:
        rows = "".join(_valid_row(n) for n in range(511, 522))
        with self.assertRaisesRegex(CHECKER.RegisterError, "parents must be"):
            CHECKER.validate(_with_table(rows))

    def test_bad_status_fails(self) -> None:
        rows = "".join(
            _valid_row(n, status="done" if n == 511 else "open")
            for n in range(511, 523)
        )
        with self.assertRaisesRegex(CHECKER.RegisterError, "status"):
            CHECKER.validate(_with_table(rows))

    def test_findings_published_requires_record(self) -> None:
        rows = "".join(
            _valid_row(n, status="findings-published" if n == 512 else "open")
            for n in range(511, 523)
        )
        with self.assertRaisesRegex(CHECKER.RegisterError, "findings-published"):
            CHECKER.validate(_with_table(rows))

    def test_cli_pass_and_fail(self) -> None:
        self.assertEqual(CHECKER.main([]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.md"
            path.write_text("no markers\n", encoding="utf-8")
            self.assertEqual(CHECKER.main(["--register", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
