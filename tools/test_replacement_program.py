"""Negative controls for the replacement-program contract."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("checker", ROOT / "tools" / "check_replacement_program.py")
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

MALFORMED_VALUES = (None, True, False, 0, 1, 1.0, 1.5, "", "bad", [], {}, [None], [[]], [{}], {"x": []})
MONTH_PROGRAM = "docs/replacement/MONTH-PROGRAM.md"
ISSUE_CONTRACT = "docs/replacement/ISSUE-CONTRACT.md"


def replacement_root(tmp) -> Path:
    """Copy the tree under review. Do not restore from origin/main or skip."""
    root = Path(tmp) / "repo"
    shutil.copytree(ROOT / "docs" / "replacement", root / "docs" / "replacement")
    return root


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git(root: Path) -> None:
    """Init a repo whose blobs are the working-tree bytes.

    GitHub Windows runners default ``core.autocrlf=true``. That cleans CRLF to
    LF on ``git add``. A receipt hashed from ``Path.read_bytes`` then disagrees
    with ``git show``, which is the check in ``_validate_github_founder_pr_approval``.
    """
    _git(root, "init")
    _git(root, "config", "user.name", "replacement-tests")
    _git(root, "config", "user.email", "replacement-tests@example.com")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.safecrlf", "false")
    _git(root, "config", "core.eol", "lf")


def _append_lf(path: Path, suffix: str) -> None:
    """Append LF bytes. ``Path.write_text`` would translate ``\\n`` to ``os.linesep``."""
    encoded = suffix.encode("utf-8")
    if b"\r" in encoded or not encoded.endswith(b"\n"):
        raise ValueError("governed suffix must be LF-only and end in a newline")
    path.write_bytes(path.read_bytes() + encoded)


def governed_git_repo(tmp, *, extra_commit=None) -> tuple[Path, str]:
    root = replacement_root(tmp)
    _init_git(root)
    _git(root, "add", "docs/replacement")
    _git(root, "commit", "-m", "approved governed bytes")
    approved = _git(root, "rev-parse", "HEAD").stdout.strip()
    if extra_commit is not None:
        extra_commit(root)
        _git(root, "add", "-A")
        _git(root, "commit", "-m", "descendant")
    return root, approved


@contextlib.contextmanager
def pinned_github_receipt(program):
    receipt = program["authority"]["acceptance_receipt"]
    previous = CHECKER.EXPECTED_GITHUB_ACCEPTED_RECEIPTS
    CHECKER.EXPECTED_GITHUB_ACCEPTED_RECEIPTS = (copy.deepcopy(receipt),)
    try:
        yield
    finally:
        CHECKER.EXPECTED_GITHUB_ACCEPTED_RECEIPTS = previous


def retarget_month_digest(program, digest: str) -> None:
    for contract in program["contracts"]:
        if contract["path"] == MONTH_PROGRAM:
            contract["sha256"] = digest
    program["authority"]["acceptance_receipt"]["accepted_contracts"][MONTH_PROGRAM] = digest
    for outcome in program["outcomes"]:
        for ref in outcome["dependency_contracts"]:
            ref["accepted_digest"] = digest


def github_acceptance_program(program, **overrides):
    candidate = copy.deepcopy(program)
    linear = program["authority"]["acceptance_receipt"]
    receipt = {
        "kind": CHECKER.RECEIPT_KIND_GITHUB_FOUNDER_PR_APPROVAL,
        "linear_issue": "CSL-523",
        "github_pr": 275,
        "github_repository": CHECKER.GITHUB_REPOSITORY,
        "pr_author_login": "CDanTheMan",
        "approving_founder_login": "samir-shah-ahmed",
        "approved_head_sha": "0123456789abcdef0123456789abcdef01234567",
        "accepted_at": "2026-10-25T07:00:00.000Z",
        "accepted_contracts": dict(CHECKER.EXPECTED_ACCEPTED_CONTRACTS),
        "authority_research_sha256": linear["authority_research_sha256"],
        "not_granted": list(linear["not_granted"]),
    }
    receipt.update(overrides)
    candidate["authority"]["acceptance_receipt"] = receipt
    return candidate


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
        payloads = (b"\xff", b"{")
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "program.json"
            for payload in payloads:
                candidate.write_bytes(payload)
                with self.subTest(prefix=payload[:20]), self.assertRaisesRegex(CHECKER.ContractError, "invalid program input"):
                    CHECKER.load(candidate)

    def test_loader_rejects_deep_array_root_on_any_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "program.json"
            candidate.write_bytes(b"[" * 10000 + b"0" + b"]" * 10000)
            # Some interpreters decode deeply nested arrays; root shape still rejects them.
            with self.assertRaisesRegex(CHECKER.ContractError, "invalid program input|program root must be an object"):
                CHECKER.load(candidate)

    def test_loader_rejects_duplicate_keys_at_every_object_depth(self) -> None:
        payloads = (
            b'{"x": true, "x": false}',
            b'{"x": false, "x": true}',
            b'{"x": false, "x": false}',
            b'{"nested": {"x": true, "x": false}}',
            b'{"items": [{"x": true, "x": false}]}',
            b'{"x": true, "\\u0078": false}',
        )
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "program.json"
            for payload in payloads:
                candidate.write_bytes(payload)
                with self.subTest(payload=payload), self.assertRaisesRegex(CHECKER.ContractError, "duplicate object key"):
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
            root = replacement_root(tmp)
            program = CHECKER.load(root / "docs" / "replacement" / "program.json")
            contract = root / "docs" / "replacement" / "SUCCESSOR-PRODUCT.md"
            _append_lf(contract, "\nUnaccepted mutation.\n")
            digest = hashlib.sha256(contract.read_bytes()).hexdigest()
            program["contracts"][0]["sha256"] = digest
            with self.assertRaises(CHECKER.ContractError):
                CHECKER.validate(program, root)

    def test_research_file_and_registry_hash_cannot_bypass_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = replacement_root(tmp)
            program = CHECKER.load(root / "docs" / "replacement" / "program.json")
            research = root / "docs" / "replacement" / "AUTHORITY-COMPLIANCE-RESEARCH.md"
            _append_lf(research, "\nUnaccepted mutation.\n")
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


class FounderPrApprovalReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = CHECKER.load(ROOT / "docs" / "replacement" / "program.json")

    def test_lucas_linear_receipt_still_valid_on_accepted_bytes(self) -> None:
        CHECKER.validate(self.program)
        receipt = self.program["authority"]["acceptance_receipt"]
        self.assertEqual(receipt["kind"], CHECKER.RECEIPT_KIND_LINEAR_DIGEST)
        self.assertEqual(receipt["accepted_by_linear_user_id"], "8709c8bb-e7d2-429e-89f9-89a48b7187fa")
        self.assertEqual(receipt["accepted_contracts"], CHECKER.EXPECTED_ACCEPTED_CONTRACTS)
        self.assertEqual(
            self.program["authority"]["founder_github_allowlist"],
            list(CHECKER.FOUNDER_GITHUB_ALLOWLIST),
        )
        self.assertEqual(CHECKER.EXPECTED_GITHUB_ACCEPTED_RECEIPTS, ())

    def test_program_json_alone_cannot_mint_github_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = governed_git_repo(tmp)
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            with self.assertRaisesRegex(CHECKER.ContractError, "github receipt is not checker-pinned"):
                CHECKER.validate(candidate, root)

    def test_founder_pr_approval_accepts_allowlisted_non_author(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = governed_git_repo(tmp)
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            with pinned_github_receipt(candidate):
                CHECKER.validate(candidate, root)

    def test_author_self_approval_rejected(self) -> None:
        candidate = github_acceptance_program(
            self.program,
            pr_author_login="samir-shah-ahmed",
            approving_founder_login="samir-shah-ahmed",
        )
        with self.assertRaisesRegex(CHECKER.ContractError, "author self-approval rejected"):
            CHECKER.validate(candidate)

    def test_author_self_approval_rejected_casefold(self) -> None:
        candidate = github_acceptance_program(
            self.program,
            pr_author_login="cdantheman",
            approving_founder_login="CDanTheMan",
        )
        with self.assertRaisesRegex(CHECKER.ContractError, "author self-approval rejected"):
            CHECKER.validate(candidate)

    def test_allowlist_matching_is_exact_case(self) -> None:
        candidate = github_acceptance_program(
            self.program,
            pr_author_login="CDanTheMan",
            approving_founder_login="Samir-Shah-Ahmed",
        )
        with self.assertRaisesRegex(CHECKER.ContractError, "approving founder not on allowlist"):
            CHECKER.validate(candidate)

    def test_non_founder_approval_rejected(self) -> None:
        candidate = github_acceptance_program(
            self.program,
            pr_author_login="CDanTheMan",
            approving_founder_login="octocat",
        )
        with self.assertRaisesRegex(CHECKER.ContractError, "approving founder not on allowlist"):
            CHECKER.validate(candidate)

    def test_any_non_author_approver_is_not_enough(self) -> None:
        candidate = github_acceptance_program(
            self.program,
            pr_author_login="CDanTheMan",
            approving_founder_login="dependabot[bot]",
        )
        with self.assertRaisesRegex(CHECKER.ContractError, "approving founder not on allowlist"):
            CHECKER.validate(candidate)

    def test_digest_mismatch_against_approved_head_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = governed_git_repo(tmp)
            contracts = dict(CHECKER.EXPECTED_ACCEPTED_CONTRACTS)
            contracts["docs/replacement/SUCCESSOR-PRODUCT.md"] = "f" * 64
            candidate = github_acceptance_program(
                self.program, approved_head_sha=sha, accepted_contracts=contracts
            )
            with pinned_github_receipt(candidate), self.assertRaisesRegex(
                CHECKER.ContractError, "github receipt digest does not match approved head"
            ):
                CHECKER.validate(candidate, root)

    def test_descendant_commit_changing_governed_bytes_rejected(self) -> None:
        def mutate(root: Path) -> None:
            _append_lf(root / ISSUE_CONTRACT, "\nchanged after approval\n")

        with tempfile.TemporaryDirectory() as tmp:
            root, sha = governed_git_repo(tmp, extra_commit=mutate)
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            with pinned_github_receipt(candidate), self.assertRaisesRegex(
                CHECKER.ContractError, "github receipt governed bytes changed after approval"
            ):
                CHECKER.validate(candidate, root)

    def test_descendant_commit_without_governed_diff_accepted(self) -> None:
        def mutate(root: Path) -> None:
            _append_lf(root / "docs" / "replacement" / "README.md", "\n# non-governing\n")

        with tempfile.TemporaryDirectory() as tmp:
            root, sha = governed_git_repo(tmp, extra_commit=mutate)
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            with pinned_github_receipt(candidate):
                CHECKER.validate(candidate, root)

    def test_github_receipt_fails_closed_when_git_cannot_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = replacement_root(tmp)
            candidate = github_acceptance_program(self.program)
            with pinned_github_receipt(candidate), self.assertRaisesRegex(
                CHECKER.ContractError, "github receipt head unavailable"
            ):
                CHECKER.validate(candidate, root)

    def test_github_receipt_may_move_issue_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = replacement_root(tmp)
            path = root / ISSUE_CONTRACT
            _append_lf(path, "\n<!-- github-receipt issue move -->\n")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            _init_git(root)
            _git(root, "add", "docs/replacement")
            _git(root, "commit", "-m", "approved issue-contract move")
            sha = _git(root, "rev-parse", "HEAD").stdout.strip()
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            candidate["authority"]["acceptance_receipt"]["accepted_contracts"][ISSUE_CONTRACT] = digest
            for contract in candidate["contracts"]:
                if contract["path"] == ISSUE_CONTRACT:
                    contract["sha256"] = digest
            with pinned_github_receipt(candidate):
                CHECKER.validate(candidate, root)

    def test_github_month_program_move_requires_outcome_baseline_amendment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = replacement_root(tmp)
            path = root / MONTH_PROGRAM
            _append_lf(path, "\n<!-- github-receipt month move -->\n")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            _init_git(root)
            _git(root, "add", "docs/replacement")
            _git(root, "commit", "-m", "approved month-program move")
            sha = _git(root, "rev-parse", "HEAD").stdout.strip()
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            retarget_month_digest(candidate, digest)
            with pinned_github_receipt(candidate):
                with self.assertRaisesRegex(
                    CHECKER.ContractError, "outcome integrity baseline changed"
                ):
                    CHECKER.validate(candidate, root)
                previous = CHECKER.OUTCOME_BASELINE_DIGESTS
                CHECKER.OUTCOME_BASELINE_DIGESTS = {
                    item["key"]: CHECKER._outcome_digest(item) for item in candidate["outcomes"]
                }
                try:
                    CHECKER.validate(candidate, root)
                finally:
                    CHECKER.OUTCOME_BASELINE_DIGESTS = previous

    def test_github_path_keeps_superseded_linear_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = governed_git_repo(tmp)
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            candidate["authority"]["superseded_acceptance_receipt"]["accepted_contracts"] = {
                **candidate["authority"]["superseded_acceptance_receipt"]["accepted_contracts"],
                MONTH_PROGRAM: "f" * 64,
            }
            with pinned_github_receipt(candidate), self.assertRaisesRegex(
                CHECKER.ContractError, "superseded contract digests changed"
            ):
                CHECKER.validate(candidate, root)

    def test_allowlist_cannot_be_widened_in_program_json(self) -> None:
        candidate = copy.deepcopy(self.program)
        candidate["authority"]["founder_github_allowlist"] = [
            *CHECKER.FOUNDER_GITHUB_ALLOWLIST,
            "octocat",
        ]
        with self.assertRaisesRegex(CHECKER.ContractError, "founder GitHub allowlist changed"):
            CHECKER.validate(candidate)

    def test_unknown_receipt_kind_rejected(self) -> None:
        candidate = copy.deepcopy(self.program)
        candidate["authority"]["acceptance_receipt"]["kind"] = "merge"
        with self.assertRaisesRegex(CHECKER.ContractError, "unsupported receipt kind"):
            CHECKER.validate(candidate)

    def test_lucas_github_path_requires_another_founder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = governed_git_repo(tmp)
            candidate = github_acceptance_program(
                self.program,
                pr_author_login="NotSkittlz",
                approving_founder_login="CDanTheMan",
                approved_head_sha=sha,
            )
            with pinned_github_receipt(candidate):
                CHECKER.validate(candidate, root)

    def test_governed_repo_stores_working_tree_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _sha = governed_git_repo(tmp)
            self.assertEqual(_git(root, "config", "--get", "core.autocrlf").stdout.strip(), "false")

    def test_windows_text_rewrite_splits_receipt_from_approved_blob(self) -> None:
        """Path.write_text on Windows emits CRLF. autocrlf then stores LF."""
        with tempfile.TemporaryDirectory() as tmp:
            root = replacement_root(tmp)
            path = root / ISSUE_CONTRACT
            rewritten = path.read_bytes().replace(b"\n", b"\r\n") + b"\r\n<!-- windows text rewrite -->\r\n"
            path.write_bytes(rewritten)
            working = hashlib.sha256(path.read_bytes()).hexdigest()
            _init_git(root)
            _git(root, "config", "core.autocrlf", "true")
            _git(root, "add", "docs/replacement")
            _git(root, "commit", "-m", "crlf rewrite")
            sha = _git(root, "rev-parse", "HEAD").stdout.strip()
            self.assertNotEqual(working, CHECKER._git_show_digest(root, sha, ISSUE_CONTRACT))
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            candidate["authority"]["acceptance_receipt"]["accepted_contracts"][ISSUE_CONTRACT] = working
            for contract in candidate["contracts"]:
                if contract["path"] == ISSUE_CONTRACT:
                    contract["sha256"] = working
            with pinned_github_receipt(candidate), self.assertRaisesRegex(
                CHECKER.ContractError, "github receipt digest does not match approved head"
            ):
                CHECKER.validate(candidate, root)

    def test_lf_append_matches_blob_when_host_autocrlf_is_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = replacement_root(tmp)
            path = root / ISSUE_CONTRACT
            _append_lf(path, "\n<!-- github-receipt issue move -->\n")
            working = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertNotIn(b"\r", path.read_bytes())
            _init_git(root)
            _git(root, "config", "core.autocrlf", "true")
            _git(root, "add", "docs/replacement")
            _git(root, "commit", "-m", "lf append under autocrlf")
            sha = _git(root, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(working, CHECKER._git_show_digest(root, sha, ISSUE_CONTRACT))
            candidate = github_acceptance_program(self.program, approved_head_sha=sha)
            candidate["authority"]["acceptance_receipt"]["accepted_contracts"][ISSUE_CONTRACT] = working
            for contract in candidate["contracts"]:
                if contract["path"] == ISSUE_CONTRACT:
                    contract["sha256"] = working
            with pinned_github_receipt(candidate):
                CHECKER.validate(candidate, root)


FLOOR_SPEC = importlib.util.spec_from_file_location(
    "checker_test_floor", ROOT / "tools" / "check_checker_test_floor.py"
)
assert FLOOR_SPEC and FLOOR_SPEC.loader
FLOOR = importlib.util.module_from_spec(FLOOR_SPEC)
FLOOR_SPEC.loader.exec_module(FLOOR)

DIRECT_CI_COMMANDS = (
    "python -X utf8 -B tools/check_replacement_program.py",
    "python -X utf8 -B tools/check_o7_operator_contract.py",
    "python -X utf8 -B -m unittest -q tools.test_replacement_program",
    "python -X utf8 -B tools/check_checker_test_floor.py",
)


POINTER_TARGET = "contracts/ai-investment-cockpit/PRODUCT.md"


def pointer_record(**overrides):
    record = {
        "pointer_version": 1,
        "repository": CHECKER.POINTER_REPOSITORY,
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "path": POINTER_TARGET,
        "sha256": "a" * 64,
        "acceptance": {
            "linear_issue": "CSL-577",
            "linear_comment_id": "57fc34c5-0000-4000-8000-000000000000",
        },
    }
    record.update(overrides)
    return record


def pointer_bytes(record, *, fences=1, prose="") -> bytes:
    block = "```json " + CHECKER.POINTER_FENCE + "\n" + json.dumps(record, indent=2) + "\n```\n"
    text = "# Successor product\n\nThe accepted contract lives in csl-research.\n" + prose + "\n" + block * fences
    return text.encode("utf-8")


def research_repo(tmp, content: bytes = b"# Product contract\n") -> tuple[Path, str, str]:
    root = Path(tmp) / "research"
    target = root / POINTER_TARGET
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    _init_git(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "contract")
    return root, _git(root, "rev-parse", "HEAD").stdout.strip(), hashlib.sha256(content).hexdigest()


class ContractPointerTests(unittest.TestCase):
    """CSL-577: SUCCESSOR-PRODUCT.md may point at the accepted csl-research contract."""

    def setUp(self) -> None:
        self.program = CHECKER.load(ROOT / "docs" / "replacement" / "program.json")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_pointer(self, data: bytes) -> Path:
        path = self.tmp / "SUCCESSOR-PRODUCT.md"
        path.write_bytes(data)
        return path

    def pointer_root(self, data: bytes):
        """A governed repo whose SUCCESSOR-PRODUCT.md is ``data``, accepted by a pinned founder receipt."""
        root = replacement_root(self.tmp)
        path = root / CHECKER.POINTER_CONTRACT
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        _init_git(root)
        _git(root, "add", "docs/replacement")
        _git(root, "commit", "-m", "approved pointer")
        sha = _git(root, "rev-parse", "HEAD").stdout.strip()
        candidate = github_acceptance_program(self.program, approved_head_sha=sha)
        candidate["authority"]["acceptance_receipt"]["accepted_contracts"][CHECKER.POINTER_CONTRACT] = digest
        for contract in candidate["contracts"]:
            if contract["path"] == CHECKER.POINTER_CONTRACT:
                contract["sha256"] = digest
        return root, candidate

    def test_current_in_place_contract_is_not_a_pointer(self) -> None:
        self.assertIsNone(CHECKER.read_pointer(ROOT / CHECKER.POINTER_CONTRACT))
        CHECKER.validate(self.program)

    def test_well_formed_pointer_parses(self) -> None:
        record = pointer_record()
        self.assertEqual(CHECKER.read_pointer(self.write_pointer(pointer_bytes(record))), record)

    def test_malformed_pointer_fields_fail_closed(self) -> None:
        bad = [
            {"repository": "CSL-Laboratories/recursion"},
            {"repository": "csl-laboratories/csl-research"},
            {"commit": "0" * 39},
            {"commit": "A" * 40},
            {"commit": 1},
            {"sha256": "f" * 63},
            {"sha256": "F" * 64},
            {"sha256": None},
            {"path": "/" + POINTER_TARGET},
            {"path": "../" + POINTER_TARGET},
            {"path": "contracts/../PRODUCT.md"},
            {"path": "contracts/./PRODUCT.md"},
            {"path": "contracts//PRODUCT.md"},
            {"path": "contracts\\PRODUCT.md"},
            {"path": ""},
            {"pointer_version": True},
            {"pointer_version": 2},
            {"pointer_version": "1"},
            {"acceptance": {"linear_issue": "CSL-577"}},
            {"acceptance": {"linear_issue": "csl-577", "linear_comment_id": "57fc34c5-0000-4000-8000-000000000000"}},
            {"acceptance": {"linear_issue": "CSL-577", "linear_comment_id": "57fc34c5"}},
            {"acceptance": {"linear_issue": "CSL-577", "linear_comment_id": "57fc34c5-0000-4000-8000-000000000000", "accepted": True}},
            {"acceptance": None},
            {"extra": True},
        ]
        for override in bad:
            with self.subTest(override=override):
                path = self.write_pointer(pointer_bytes(pointer_record(**override)))
                with self.assertRaises(CHECKER.ContractError):
                    CHECKER.read_pointer(path)
        record = pointer_record()
        del record["sha256"]
        with self.assertRaisesRegex(CHECKER.ContractError, "pointer fields changed"):
            CHECKER.read_pointer(self.write_pointer(pointer_bytes(record)))

    def test_fence_must_be_one_valid_json_block(self) -> None:
        cases = {
            "two blocks": pointer_bytes(pointer_record(), fences=2),
            "marker without block": ("Pointer: " + CHECKER.POINTER_FENCE + " pending.\n").encode("utf-8"),
            "marker in prose too": pointer_bytes(pointer_record(), prose="See " + CHECKER.POINTER_FENCE + ".\n"),
            "invalid json": ("```json " + CHECKER.POINTER_FENCE + "\n{nope\n```\n").encode("utf-8"),
            "duplicate key": (
                "```json " + CHECKER.POINTER_FENCE + '\n{"pointer_version": 1, "pointer_version": 1}\n```\n'
            ).encode("utf-8"),
            "not utf-8": b"\xff" + pointer_bytes(pointer_record()),
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(CHECKER.ContractError):
                CHECKER.read_pointer(self.write_pointer(data))

    def test_pointer_switch_passes_under_pinned_founder_receipt(self) -> None:
        root, candidate = self.pointer_root(pointer_bytes(pointer_record()))
        with pinned_github_receipt(candidate):
            CHECKER.validate(candidate, root)

    def test_pointer_bytes_still_need_the_receipt(self) -> None:
        root, candidate = self.pointer_root(pointer_bytes(pointer_record()))
        with self.assertRaisesRegex(CHECKER.ContractError, "github receipt is not checker-pinned"):
            CHECKER.validate(candidate, root)

    def test_malformed_pointer_fails_validate_without_research_root(self) -> None:
        root, candidate = self.pointer_root(pointer_bytes(pointer_record(commit="abc")))
        with pinned_github_receipt(candidate), self.assertRaisesRegex(CHECKER.ContractError, "40 lowercase hex"):
            CHECKER.validate(candidate, root)

    def test_research_root_checks_target_bytes(self) -> None:
        research, commit, digest = research_repo(self.tmp)
        good = pointer_record(commit=commit, sha256=digest)
        CHECKER.verify_pointer_target(good, research)
        root, candidate = self.pointer_root(pointer_bytes(good))
        with pinned_github_receipt(candidate):
            CHECKER.validate(candidate, root, research_root=research)
        with self.assertRaisesRegex(CHECKER.ContractError, "target digest mismatch"):
            CHECKER.verify_pointer_target(pointer_record(commit=commit, sha256="b" * 64), research)
        with self.assertRaisesRegex(CHECKER.ContractError, "target unavailable"):
            CHECKER.verify_pointer_target(pointer_record(commit="0" * 40, sha256=digest), research)
        with self.assertRaisesRegex(CHECKER.ContractError, "target unavailable"):
            CHECKER.verify_pointer_target(
                pointer_record(commit=commit, sha256=digest, path="contracts/missing.md"), research
            )

    def test_main_says_when_target_bytes_were_not_checked(self) -> None:
        research, commit, digest = research_repo(self.tmp)
        root, candidate = self.pointer_root(pointer_bytes(pointer_record(commit=commit, sha256=digest)))
        program_path = self.tmp / "program.json"
        program_path.write_text(json.dumps(candidate), encoding="utf-8")
        args = ["--program", str(program_path), "--root", str(root)]
        with pinned_github_receipt(candidate):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(CHECKER.main(args), 0)
            self.assertIn("target bytes not checked", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(CHECKER.main([*args, "--research-root", str(research)]), 0)
            self.assertEqual(out.getvalue(), "replacement-program: PASS\n")

    def test_pointer_adds_no_digest_constants(self) -> None:
        source = (ROOT / "tools" / "check_replacement_program.py").read_text(encoding="utf-8")
        start = source.index("# CSL-577:")
        end = source.index("RESEARCH_BUDGET_FIELDS = {")
        self.assertIsNone(re.search(r"[0-9a-f]{40}", source[start:end]))


def floor_counters(value):
    return {"pytest": lambda root, entry: value, "unittest": lambda root, entry: value}


class CheckerTestFloorTests(unittest.TestCase):
    """CSL-511 WP4: the floor fails closed when a pinned checker test goes away."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for rel in FLOOR.REQUIRED_FILES + tuple(entry.path for entry in FLOOR.FLOORS):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x = 1\n", encoding="utf-8")

    def run_floor(self, counters=None):
        return FLOOR.check(self.root, counters=counters or floor_counters(10_000))

    def test_complete_tree_at_or_above_floor_passes(self):
        self.assertEqual(self.run_floor(), [])

    def test_floors_pin_both_app_tests_and_this_tools_test(self):
        paths = {entry.path for entry in FLOOR.FLOORS}
        self.assertEqual(
            paths,
            {
                "app/tests/test_replacement_program_registry.py",
                "app/tests/test_o7_operator_contract.py",
                "tools/test_replacement_program.py",
            },
        )
        self.assertTrue(all(entry.minimum > 0 for entry in FLOOR.FLOORS))

    def test_deleting_any_pinned_file_fails_closed(self):
        for rel in tuple(entry.path for entry in FLOOR.FLOORS) + FLOOR.REQUIRED_FILES:
            with self.subTest(rel=rel):
                path = self.root / rel
                data = path.read_bytes()
                path.unlink()
                problems = self.run_floor()
                self.assertTrue(any(f"{rel} is missing" in problem for problem in problems), problems)
                path.write_bytes(data)

    def test_symlinked_test_file_fails_closed(self):
        path = self.root / "app/tests/test_o7_operator_contract.py"
        path.unlink()
        try:
            path.symlink_to(self.root / "tools/check_o7_operator_contract.py")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        self.assertTrue(any("is a symlink" in problem for problem in self.run_floor()))

    def test_thinned_test_file_fails_closed(self):
        for entry in FLOOR.FLOORS:
            with self.subTest(path=entry.path):
                def counter(root, found, entry=entry):
                    return found.minimum - 1 if found.path == entry.path else found.minimum

                problems = self.run_floor({"pytest": counter, "unittest": counter})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("below its floor", problems[0])

    def test_collection_error_and_unknown_runner_fail_closed(self):
        def boom(root, entry):
            raise RuntimeError("collection failed")

        self.assertEqual(len(self.run_floor({"pytest": boom, "unittest": boom})), len(FLOOR.FLOORS))
        problems = FLOOR.check(
            self.root, floors=(FLOOR.Floor("tools/check_replacement_program.py", "nope", 1),)
        )
        self.assertTrue(any("unknown runner" in problem for problem in problems))

    def test_ci_keeps_the_direct_commands_in_the_linux_job(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        start = text.index("\n  linux:\n")
        end = text.index("\n  linux-gates:\n")
        for command in DIRECT_CI_COMMANDS:
            self.assertIn(command, text[start:end], command)
        self.assertIn("\n    name: linux\n", text[start:end])
        self.assertIn("\n    name: linux-gates\n", text[end:])


if __name__ == "__main__":
    unittest.main()
