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

    def test_checked_in_registry_passes(self) -> None:
        CHECKER.validate(self.program)

    def test_outcome_heading_must_preserve_exact_fragment(self) -> None:
        self.assertTrue(CHECKER._has_exact_outcome_heading("### O1\n", "O1"))
        self.assertTrue(CHECKER._has_exact_outcome_heading("### O1 ###\n", "O1"))
        self.assertFalse(CHECKER._has_exact_outcome_heading("### O1 retired\n", "O1"))

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
            lambda p: p["authority"].update(bounded_direct_authorized=True),
            lambda p: p["authority"]["superseded_acceptance_receipt"]["accepted_contracts"].update(
                {"docs/replacement/SUCCESSOR-PRODUCT.md": "f" * 64}
            ),
            lambda p: p["authority"]["superseded_acceptance_receipt"].update(extra_receipt_claim=True),
            lambda p: p["outcomes"][0]["falsifiers"].append(""),
            lambda p: p["outcomes"][0]["deliverables"].append("   "),
            lambda p: p["outcomes"][0]["non_goals"].append(""),
            lambda p: p["outcomes"][0]["inherited_decisions"].append(""),
            lambda p: p["outcomes"][0]["research_budget"]["source_classes"].append(""),
            lambda p: p["outcomes"][0]["research_budget"].update(stop_condition="nonstop unbounded"),
            lambda p: p["outcomes"][0]["independent_verification"].update(verifier_role="not_independent"),
            lambda p: p["outcomes"][0]["independent_verification"]["required_evidence"].append(""),
            lambda p: p["outcomes"][5].update(discretion_class="bounded_autonomy"),
            lambda p: p["research_evidence"]["authority_compliance"].update(status="running"),
            lambda p: p["research_evidence"]["authority_compliance"].update(claim="live_qualified"),
            lambda p: p["research_evidence"]["authority_compliance"].update(owner_direction="Live authority is granted now."),
            lambda p: p["research_evidence"]["authority_compliance"].update(not_claimed=[]),
            lambda p: p["research_evidence"]["authority_compliance"].update(sha256="0" * 64),
            lambda p: p["outcomes"][0].update(authoritative_clause="missing.md#nope"),
            lambda p: p["outcomes"][0]["evidence"].update(
                claimed="human_decided", not_claimed=list(CHECKER.MATURITY[3:])
            ),
            lambda p: p["outcomes"][0]["evidence"].update(
                claimed="limited_live_qualified", not_claimed=["release_qualified"]
            ),
        ]
        for mutation in mutations:
            candidate = copy.deepcopy(self.program)
            mutation(candidate)
            with self.subTest(mutation=mutation), self.assertRaises(CHECKER.ContractError):
                CHECKER.validate(candidate)

if __name__ == "__main__":
    unittest.main()
