"""Offline validator for the CSL-212 replacement-program registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROGRAM = ROOT / "docs" / "replacement" / "program.json"
MATURITY = (
    "unresearched",
    "researched",
    "human_decided",
    "contract_frozen",
    "prototyped",
    "implemented",
    "provider_free_verified",
    "demo_or_paper_qualified",
    "shadow_qualified",
    "limited_live_qualified",
    "release_qualified",
)
EXPECTED_CONTRACTS = {
    "docs/replacement/SUCCESSOR-PRODUCT.md",
    "docs/replacement/ISSUE-CONTRACT.md",
    "docs/replacement/MONTH-PROGRAM.md",
    "docs/replacement/SCOPE-AUDIT.md",
}
EXPECTED_AUTHORITY_KEYS = {
    "current_runtime_invariants_preserved",
    "live_authority_granted",
    "contract_acceptance",
    "superseded_acceptance_receipt",
}
EXPECTED_DISCRETION = {
    "O1": "human_gated",
    "O2": "collaborative_judgment",
    "O3": "human_gated",
    "O4": "collaborative_judgment",
    "O5": "collaborative_judgment",
    "O6": "human_gated",
    "O7": "collaborative_judgment",
    "O8": "human_gated",
    "O9": "human_gated",
}
RESEARCH_BUDGET_FIELDS = {
    "question",
    "source_classes",
    "quota_class",
    "artifact",
    "stop_condition",
}
VERIFICATION_FIELDS = {
    "verifier_role",
    "method",
    "required_evidence",
    "self_verification_sufficient",
}
REQUIRED_STOP_CONDITION = (
    "Stop after the named artifact records alternatives, falsifiers, unresolved "
    "decisions and the smallest next experiments; continuation requires a human "
    "Linear receipt."
)
REQUIRED_RESEARCH_DIRECTION = (
    "A is the qualification reference; C bounded direct authorization is a "
    "candidate future target pending a human-authored final-digest receipt and "
    "separate accepted gates."
)
REQUIRED_RESEARCH_EXCLUSIONS = {
    "legal_advice",
    "account_or_venue_eligibility",
    "provider_permission",
    "live_authority",
    "economic_qualification",
    "release_qualification",
}
REQUIRED_OUTCOME_FIELDS = {
    "key",
    "linear_issue",
    "title",
    "parent_issue",
    "authoritative_clause",
    "current_state",
    "intended_outcome",
    "non_goals",
    "inherited_decisions",
    "research_basis",
    "research_budget",
    "dependencies",
    "dependency_contracts",
    "authority_boundary",
    "discretion_class",
    "falsifiers",
    "deliverables",
    "completion_gate",
    "completion_gate_status",
    "independent_verification",
    "evidence",
}


class ContractError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(item) for item in value)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_exact_outcome_heading(document: str, key: str) -> bool:
    return re.search(rf"^### {re.escape(key)}[ \t]*\r?$", document, re.MULTILINE) is not None


def validate(program: dict[str, Any], root: Path = ROOT) -> None:
    _require(program.get("schema_version") == 1, "unsupported schema_version")
    _require(program.get("program_id") == "cockpit.replacement.2026-10", "wrong program_id")
    _require(program.get("status") == "candidate", "updated program must remain candidate")
    _require(program.get("controlling_issue") == "CSL-212", "missing CSL-212 lineage")
    _require(program.get("source_commit") == "73ab78d9d69494c757cec875855121b1d29320a4", "stale source commit")
    authority = program.get("authority", {})
    _require(set(authority) == EXPECTED_AUTHORITY_KEYS, "authority keys changed")
    _require(authority.get("current_runtime_invariants_preserved") is True, "runtime invariants not preserved")
    _require(authority.get("live_authority_granted") is False, "program grants live authority")
    _require(authority.get("contract_acceptance") == "pending_updated_human_digest_receipt", "updated digest acceptance inferred")
    receipt = authority.get("superseded_acceptance_receipt", {})
    _require(receipt.get("linear_issue") == "CSL-212", "superseded receipt issue mismatch")
    _require(receipt.get("human_linear_comment_id") == "736a6b55-2b20-463c-86e1-9553a5692996", "superseded human comment mismatch")
    _require(receipt.get("mirror_comment_id") == "15c6f540-3686-49eb-a9e7-d93083313e85", "superseded mirror mismatch")
    _require(receipt.get("accepted_by") == "Lucas Guerra", "superseded acceptance owner mismatch")
    _require(receipt.get("accepted_by_linear_user_id") == "8709c8bb-e7d2-429e-89f9-89a48b7187fa", "superseded owner identity mismatch")
    _require(receipt.get("accepted_at") == "2026-10-16T05:21:02.659Z", "superseded acceptance timestamp mismatch")
    _require(receipt.get("reviewed_head") == "f5f4cefaa9bfcac8a87654428d96c921196e53d2", "superseded reviewed head mismatch")
    _require(_nonempty(receipt.get("superseded_reason")), "superseded receipt reason missing")
    _require(
        set(receipt.get("not_granted", []))
        == {
            "implementation_completion",
            "empirical_pass",
            "legal_account_or_venue_eligibility",
            "provider_permission",
            "release_qualification",
            "live_authority",
            "credentials",
            "capital_movement",
            "merge_permission",
        },
        "acceptance exclusions changed",
    )

    research = program.get("research_evidence", {}).get("authority_compliance", {})
    _require(research.get("run_id") == "trun_db705696fec649d8ad991d8f8f7b7813", "authority research run missing")
    _require(research.get("status") == "completed", "authority research incomplete")
    _require(research.get("research_cutoff") == "2026-10-16", "authority research cutoff missing")
    _require(research.get("claim") == "research_input_only", "authority research over-promoted")
    _require(research.get("owner_direction") == REQUIRED_RESEARCH_DIRECTION, "authority research direction changed")
    _require(set(research.get("not_claimed", [])) == REQUIRED_RESEARCH_EXCLUSIONS, "authority research exclusions changed")
    research_path = research.get("reconciliation_path")
    _require(research_path == "docs/replacement/AUTHORITY-COMPLIANCE-RESEARCH.md", "authority reconciliation path mismatch")
    research_file = root / research_path
    _require(research_file.is_file(), "authority reconciliation file missing")
    _require(re.fullmatch(r"[0-9a-f]{64}", research.get("sha256", "")) is not None, "authority reconciliation digest invalid")
    _require(_digest(research_file) == research["sha256"], "authority reconciliation digest mismatch")
    _require(
        receipt.get("authority_research_sha256")
        == "0124cece646fc081de8db4e9b8a47cc7db3fea98ac4db3218a415c04da982938",
        "superseded research digest changed",
    )

    contracts = program.get("contracts")
    _require(isinstance(contracts, list) and contracts, "contracts missing")
    contract_paths = [item.get("path") for item in contracts]
    _require(len(contract_paths) == len(set(contract_paths)), "duplicate contract path")
    _require(set(contract_paths) == EXPECTED_CONTRACTS, "missing or unexpected contract path")
    for contract in contracts:
        rel = contract.get("path")
        expected = contract.get("sha256")
        _require(_nonempty(rel) and _nonempty(expected), "contract path/digest missing")
        path = root / rel
        _require(path.is_file(), f"contract path missing: {rel}")
        _require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, f"invalid digest: {rel}")
        _require(_digest(path) == expected, f"contract digest mismatch: {rel}")
    superseded_contracts = receipt.get("accepted_contracts")
    _require(isinstance(superseded_contracts, dict), "superseded contract digest set missing")
    _require(set(superseded_contracts) == EXPECTED_CONTRACTS, "superseded contract paths changed")
    _require(
        all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in superseded_contracts.values()),
        "superseded contract digest invalid",
    )

    outcomes = program.get("outcomes")
    _require(isinstance(outcomes, list) and len(outcomes) == 9, "exactly nine outcomes required")
    expected_keys = [f"O{i}" for i in range(1, 10)]
    keys = [item.get("key") for item in outcomes]
    _require(keys == expected_keys, "outcomes must be ordered O1 through O9")
    issues: set[str] = set()
    seen: set[str] = set()
    month_program = (root / "docs" / "replacement" / "MONTH-PROGRAM.md").read_text(encoding="utf-8")
    for item in outcomes:
        key = item["key"]
        _require(set(item) == REQUIRED_OUTCOME_FIELDS, f"{key} outcome fields changed")
        _require(item["parent_issue"] == "CSL-212", f"{key} missing parent lineage")
        issue = item["linear_issue"]
        _require(isinstance(issue, str) and re.fullmatch(r"CSL-[0-9]+", issue) is not None, f"{key} invalid Linear issue")
        _require(issue not in issues, f"duplicate Linear issue: {issue}")
        issues.add(issue)
        expected_clause = f"docs/replacement/MONTH-PROGRAM.md#{key.lower()}"
        _require(item["authoritative_clause"] == expected_clause, f"{key} authoritative clause changed")
        _require(_has_exact_outcome_heading(month_program, key), f"{key} authoritative heading missing")
        for field in REQUIRED_OUTCOME_FIELDS - {"dependencies", "dependency_contracts", "evidence", "falsifiers", "deliverables", "non_goals", "inherited_decisions", "research_budget", "independent_verification"}:
            _require(_nonempty(item[field]), f"{key} empty {field}")
        _require(item["discretion_class"] == EXPECTED_DISCRETION[key], f"{key} discretion class changed")
        budget = item["research_budget"]
        _require(isinstance(budget, dict) and set(budget) == RESEARCH_BUDGET_FIELDS, f"{key} research budget fields changed")
        for field in RESEARCH_BUDGET_FIELDS - {"source_classes"}:
            _require(_nonempty(budget[field]), f"{key} empty research budget {field}")
        _require(_nonempty_list(budget["source_classes"]), f"{key} empty research source class")
        _require(budget["quota_class"] == "one_bounded_reconciliation_pass", f"{key} research quota changed")
        _require(budget["stop_condition"] == REQUIRED_STOP_CONDITION, f"{key} research stop condition changed")
        deps = item["dependencies"]
        _require(isinstance(deps, list), f"{key} dependencies must be a list")
        for dep in deps:
            _require(dep in seen, f"{key} unresolved or cyclic dependency: {dep}")
        refs = item["dependency_contracts"]
        _require(isinstance(refs, list) and [ref.get("outcome") for ref in refs] == deps, f"{key} dependency fragments mismatch")
        for ref in refs:
            _require(ref.get("fragment") == f"docs/replacement/MONTH-PROGRAM.md#{ref['outcome'].lower()}", f"{key} invalid dependency fragment")
            _require(ref.get("status") == "pending_human_digest_receipt" and ref.get("accepted_digest") is None, f"{key} inferred dependency acceptance")
        seen.add(key)
        for field in ("non_goals", "inherited_decisions", "falsifiers", "deliverables"):
            _require(_nonempty_list(item[field]), f"{key} empty {field}")
        verification = item["independent_verification"]
        _require(isinstance(verification, dict) and set(verification) == VERIFICATION_FIELDS, f"{key} verification fields changed")
        _require(verification["verifier_role"] == "independent_non_implementer", f"{key} verifier is not independent")
        _require(verification["method"] == "rerun_exact_candidate_and_negative_controls", f"{key} verification method changed")
        _require(_nonempty_list(verification["required_evidence"]), f"{key} verification evidence missing")
        _require(verification["self_verification_sufficient"] is False, f"{key} self-verification accepted")
        _require(item["completion_gate_status"] == "pending_outcome_artifact", f"{key} completion gate status is unsupported")
        _require("must be checked in" in item["completion_gate"].lower(), f"{key} completion gate falsely claims execution")
        evidence = item["evidence"]
        _require(isinstance(evidence, dict) and set(evidence) == {"claimed", "not_claimed"}, f"{key} evidence fields changed")
        claimed = evidence.get("claimed")
        not_claimed = evidence.get("not_claimed")
        _require(claimed in MATURITY, f"{key} invalid claimed maturity")
        _require(claimed == "researched", f"{key} unsupported evidence maturity")
        _require(_nonempty_list(not_claimed), f"{key} missing unclaimed levels")
        claimed_index = MATURITY.index(claimed)
        _require(not_claimed == list(MATURITY[claimed_index + 1 :]), f"{key} invalid evidence promotion")


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    _require(isinstance(data, dict), "program root must be an object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    args = parser.parse_args(argv)
    try:
        validate(load(args.program))
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        print(f"replacement-program: FAIL: {exc}", file=sys.stderr)
        return 1
    print("replacement-program: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
