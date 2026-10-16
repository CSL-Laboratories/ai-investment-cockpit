"""Negative controls for the replacement-program contract."""
from __future__ import annotations
import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("checker", ROOT / "tools" / "check_replacement_program.py")
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

class ReplacementProgramTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = CHECKER.load(ROOT / "docs" / "replacement" / "program.json")

    def test_checked_in_candidate_passes(self) -> None:
        CHECKER.validate(self.program)

    def test_required_fail_closed_controls(self) -> None:
        mutations = [
            lambda p: p["outcomes"][0].pop("parent_issue"),
            lambda p: p["outcomes"][0].pop("research_budget"),
            lambda p: p["outcomes"][0].pop("discretion_class"),
            lambda p: p["contracts"][0].update(sha256="0" * 64),
            lambda p: p["contracts"].pop(),
            lambda p: p["outcomes"][0]["dependencies"].append("O9"),
            lambda p: p["outcomes"][0]["evidence"].update(claimed="release_qualified"),
            lambda p: p["outcomes"][0]["evidence"].update(not_claimed=["release_qualified"]),
            lambda p: p["outcomes"][0].pop("independent_verification"),
            lambda p: p["authority"].update(live_authority_granted=True),
        ]
        for mutation in mutations:
            candidate = copy.deepcopy(self.program)
            mutation(candidate)
            with self.subTest(mutation=mutation):
                with self.assertRaises(CHECKER.ContractError):
                    CHECKER.validate(candidate)

if __name__ == "__main__":
    unittest.main()
