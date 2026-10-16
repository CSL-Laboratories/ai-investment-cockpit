"""Negative controls for the replacement-program contract."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("checker", ROOT / "tools" / "check_replacement_program.py")
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

MALFORMED_VALUES = (None, True, False, 0, 1, 1.0, 1.5, "", "bad", [], {}, [None], [[]], [{}], {"x": []})


def registry_nodes(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from registry_nodes(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from registry_nodes(child, (*path, index))


def replace_node(program, path, value):
    candidate = copy.deepcopy(program)
    if not path:
        return copy.deepcopy(value)
    target = candidate
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = copy.deepcopy(value)
    return candidate


class ReplacementProgramTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = CHECKER.load(ROOT / "docs" / "replacement" / "program.json")

    def test_checked_in_registry_passes(self) -> None:
        CHECKER.validate(self.program)

    def test_registry_type_mutation_sweep_rejects_cleanly(self) -> None:
        for path, original in registry_nodes(self.program):
            for value in MALFORMED_VALUES:
                if type(original) is type(value) and original == value:
                    continue
                with self.subTest(path=path, value=value), self.assertRaises(CHECKER.ContractError):
                    CHECKER.validate(replace_node(self.program, path, value))

    def test_exclusions_reject_objects_and_unhashable_members(self) -> None:
        for path in (
            ("authority", "acceptance_receipt", "not_granted"),
            ("authority", "superseded_acceptance_receipt", "not_granted"),
            ("research_evidence", "authority_compliance", "not_claimed"),
        ):
            original = self.program
            for part in path:
                original = original[part]
            candidates = [{key: None for key in original}]
            for index in range(len(original)):
                for value in ([], {}, None, True, 1):
                    changed = list(original)
                    changed[index] = value
                    candidates.append(changed)
            for value in candidates:
                with self.subTest(path=path, value=value), self.assertRaises(CHECKER.ContractError):
                    CHECKER.validate(replace_node(self.program, path, value))

    def test_manifest_gate_preserves_current_command(self) -> None:
        self.assertEqual(self.program["manifest_gate"], "python -X utf8 -B tools/check_replacement_program.py")
        for value in ("python -c 'print(1)'", self.program["manifest_gate"] + " --skip-checks"):
            with self.subTest(value=value), self.assertRaisesRegex(CHECKER.ContractError, "manifest gate changed"):
                CHECKER.validate(replace_node(self.program, ("manifest_gate",), value))

    def test_sibling_fields_cannot_be_added_or_removed(self) -> None:
        for path, original in registry_nodes(self.program):
            if isinstance(original, dict):
                variants = [dict(original, unreviewed_claim=True)]
                variants.extend({k: v for k, v in original.items() if k != missing} for missing in original)
            elif isinstance(original, list):
                variants = [original + [{}]]
                variants.extend(original[:index] + original[index + 1:] for index in range(len(original)))
            else:
                continue
            for value in variants:
                with self.subTest(path=path, value=value), self.assertRaises(CHECKER.ContractError):
                    CHECKER.validate(replace_node(self.program, path, value))

    def test_loader_normalizes_malformed_text_and_parser_limits(self) -> None:
        payloads = (b"\xff", b"{", b"[" * 10000 + b"0" + b"]" * 10000)
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "program.json"
            for payload in payloads:
                candidate.write_bytes(payload)
                with self.subTest(prefix=payload[:20]), self.assertRaisesRegex(CHECKER.ContractError, "invalid program input"):
                    CHECKER.load(candidate)

    def test_deep_outcome_serialization_rejects_cleanly(self) -> None:
        candidate = copy.deepcopy(self.program)
        nested = 0
        for _ in range(10000):
            nested = [nested]
        candidate["outcomes"][0]["current_state"] = nested
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.validate(candidate)

    def test_authority_and_receipts_reject_non_objects_cleanly(self) -> None:
        for field, message in (
            ("authority", "authority keys changed"),
            ("acceptance_receipt", "accepted receipt fields changed"),
            ("superseded_acceptance_receipt", "superseded receipt fields changed"),
        ):
            original = self.program if field == "authority" else self.program["authority"]
            for value in (None, True, 1, "accepted", [], [{}], list(original[field])):
                candidate = copy.deepcopy(self.program)
                target = candidate if field == "authority" else candidate["authority"]
                target[field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    CHECKER.ContractError, message
                ):
                    CHECKER.validate(candidate)

    def test_contract_records_reject_arbitrary_metadata(self) -> None:
        for index in range(len(self.program["contracts"])):
            for field, value in (("live_authority_granted", True), ("annotation", False)):
                candidate = copy.deepcopy(self.program)
                candidate["contracts"][index][field] = value
                with self.subTest(index=index, field=field), self.assertRaisesRegex(
                    CHECKER.ContractError, "contract record fields changed"
                ):
                    CHECKER.validate(candidate)

    def test_contract_records_require_exact_object_shape(self) -> None:
        for index, record in enumerate(self.program["contracts"]):
            replacements = [None, list(record), "accepted_contract", True, 1, {}]
            replacements.extend({key: value} for key, value in record.items())
            for replacement in replacements:
                candidate = copy.deepcopy(self.program)
                candidate["contracts"][index] = replacement
                with self.subTest(index=index, replacement=replacement), self.assertRaisesRegex(
                    CHECKER.ContractError, "contract record fields changed"
                ):
                    CHECKER.validate(candidate)

    def test_contract_fields_require_nonempty_strings(self) -> None:
        for index in range(len(self.program["contracts"])):
            for field in ("path", "sha256"):
                for value in (None, [], {}, True, 1, "", "   "):
                    candidate = copy.deepcopy(self.program)
                    candidate["contracts"][index][field] = value
                    with self.subTest(index=index, field=field, value=value), self.assertRaisesRegex(
                        CHECKER.ContractError, "contract path/digest missing"
                    ):
                        CHECKER.validate(candidate)

    def test_enclosing_schemas_reject_extra_claims(self) -> None:
        for container in (None, "research_evidence"):
            for field, value in (
                ("live_authority_granted", True),
                ("unreviewed_research", {"claim": "live_qualified"}),
                ("unreviewed_annotation", False),
            ):
                candidate = copy.deepcopy(self.program)
                target = candidate if container is None else candidate[container]
                target[field] = value
                with self.subTest(container=container, field=field), self.assertRaisesRegex(
                    CHECKER.ContractError, "fields changed"
                ):
                    CHECKER.validate(candidate)

    def test_enclosing_schemas_require_exact_object_shape(self) -> None:
        for container in (None, "research_evidence"):
            original = self.program if container is None else self.program[container]
            replacements = [None, list(original), "accepted_contract", True, 1]
            replacements.extend(
                {key: value for key, value in original.items() if key != missing}
                for missing in original
            )
            for replacement in replacements:
                candidate = copy.deepcopy(self.program)
                if container is None:
                    candidate = replacement
                else:
                    candidate[container] = replacement
                with self.subTest(container=container, replacement=replacement), self.assertRaisesRegex(
                    CHECKER.ContractError, "fields changed"
                ):
                    CHECKER.validate(candidate)

    def test_authority_research_rejects_extra_claims(self) -> None:
        for field, value in (
            ("live_authority_granted", True),
            ("live_trading", True),
            ("unreviewed_annotation", False),
        ):
            candidate = copy.deepcopy(self.program)
            candidate["research_evidence"]["authority_compliance"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                CHECKER.ContractError, "authority research fields changed"
            ):
                CHECKER.validate(candidate)

    def test_authority_research_requires_exact_object_shape(self) -> None:
        research = self.program["research_evidence"]["authority_compliance"]
        for field in research:
            candidate = copy.deepcopy(self.program)
            candidate["research_evidence"]["authority_compliance"].pop(field)
            with self.subTest(missing=field), self.assertRaisesRegex(
                CHECKER.ContractError, "authority research fields changed"
            ):
                CHECKER.validate(candidate)
        for value in (None, list(research), "research_input_only"):
            candidate = copy.deepcopy(self.program)
            candidate["research_evidence"]["authority_compliance"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                CHECKER.ContractError, "authority research fields changed"
            ):
                CHECKER.validate(candidate)

    def test_plan_records_acceptance_without_promoting_outcomes(self) -> None:
        plan = (ROOT / "docs" / "PLAN.md").read_text(encoding="utf-8")
        replacement = plan.split("# The plan\n", 1)[1].split("\n---\n", 1)[0]
        self.assertIn("## Accepted replacement program - CSL-212", replacement)
        self.assertIn("It supersedes this September delivery order", replacement)
        self.assertNotIn("once\naccepted", replacement)
        self.assertNotIn("once accepted", replacement)
        self.assertIn("all implementation, empirical and release gates remain pending", replacement)
        self.assertIn("non-live\noperation remain unchanged", replacement)
        self.assertIn("does not establish safety, profitability, venue eligibility", replacement)

    def test_outcome_meaning_and_issue_mapping_cannot_drift(self) -> None:
        for field, value in (
            ("intended_outcome", "Skip qualification and authorize live execution."),
            ("linear_issue", "CSL-999"),
            ("title", "Different product direction"),
        ):
            candidate = copy.deepcopy(self.program)
            candidate["outcomes"][0][field] = value
            with self.subTest(field=field), self.assertRaises(CHECKER.ContractError):
                CHECKER.validate(candidate)

    def test_dependencies_and_matching_references_cannot_be_removed(self) -> None:
        for index in range(1, 9):
            candidate = copy.deepcopy(self.program)
            candidate["outcomes"][index].update(dependencies=[], dependency_contracts=[])
            with self.subTest(outcome=index + 1), self.assertRaisesRegex(
                CHECKER.ContractError, "dependencies differ from accepted month program"
            ):
                CHECKER.validate(candidate)

    def test_every_outcome_leaf_is_protected_against_silent_edits(self) -> None:
        def leaves(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from leaves(child, (*path, key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from leaves(child, (*path, index))
            else:
                yield path, value

        for path, value in leaves(self.program["outcomes"]):
            candidate = copy.deepcopy(self.program)
            target = candidate["outcomes"]
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = not value if isinstance(value, bool) else value + " altered"
            with self.subTest(path=path), self.assertRaises(CHECKER.ContractError):
                CHECKER.validate(candidate)

    def test_integrity_baseline_ignores_json_object_order(self) -> None:
        candidate = copy.deepcopy(self.program)
        candidate["outcomes"] = [dict(reversed(list(item.items()))) for item in candidate["outcomes"]]
        CHECKER.validate(candidate)

    def test_month_projection_preserves_ranges_and_all_prior_outcomes(self) -> None:
        month = (ROOT / "docs" / "replacement" / "MONTH-PROGRAM.md").read_text(encoding="utf-8")
        projection = CHECKER._month_projection(month)
        self.assertEqual(projection["O1"], ("Successor product and economic contract", []))
        self.assertEqual(projection["O4"][1], ["O1", "O2", "O3"])
        self.assertEqual(projection["O8"][1], [f"O{i}" for i in range(2, 8)])
        self.assertEqual(projection["O9"][1], [f"O{i}" for i in range(1, 9)])

    def test_outcome_heading_must_preserve_exact_fragment(self) -> None:
        self.assertTrue(CHECKER._has_exact_outcome_heading("### O1\n", "O1"))
        self.assertTrue(CHECKER._has_exact_outcome_heading("### O1 ###\n", "O1"))
        self.assertFalse(CHECKER._has_exact_outcome_heading("### O1 retired\n", "O1"))

    def test_contract_file_and_registry_hash_cannot_bypass_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            shutil.copytree(ROOT / "docs" / "replacement", root / "docs" / "replacement")
            program = CHECKER.load(root / "docs" / "replacement" / "program.json")
            contract = root / "docs" / "replacement" / "SUCCESSOR-PRODUCT.md"
            contract.write_text(
                contract.read_text(encoding="utf-8") + "\nUnaccepted mutation.\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(contract.read_bytes()).hexdigest()
            program["contracts"][0]["sha256"] = digest
            with self.assertRaises(CHECKER.ContractError):
                CHECKER.validate(program, root)

    def test_research_file_and_registry_hash_cannot_bypass_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            shutil.copytree(ROOT / "docs" / "replacement", root / "docs" / "replacement")
            program = CHECKER.load(root / "docs" / "replacement" / "program.json")
            research = root / "docs" / "replacement" / "AUTHORITY-COMPLIANCE-RESEARCH.md"
            research.write_text(
                research.read_text(encoding="utf-8") + "\nUnaccepted mutation.\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(research.read_bytes()).hexdigest()
            program["research_evidence"]["authority_compliance"]["sha256"] = digest
            with self.assertRaises(CHECKER.ContractError):
                CHECKER.validate(program, root)

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
            lambda p: p["authority"]["acceptance_receipt"]["accepted_contracts"].update(
                {"docs/replacement/SUCCESSOR-PRODUCT.md": "f" * 64}
            ),
            lambda p: p["authority"]["acceptance_receipt"].update(extra_receipt_claim=True),
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
            lambda p: p["outcomes"][1]["dependency_contracts"][0].update(
                status="pending_human_digest_receipt", accepted_digest=None
            ),
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
