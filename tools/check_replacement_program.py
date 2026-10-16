"""Offline validator for the CSL-212 replacement-program candidate."""

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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(program: dict[str, Any], root: Path = ROOT) -> None:
    _require(program.get("schema_version") == 1, "unsupported schema_version")
    _require(program.get("program_id") == "cockpit.replacement.2026-10", "wrong program_id")
    _require(program.get("status") == "candidate", "program must remain candidate")
    _require(program.get("controlling_issue") == "CSL-212", "missing CSL-212 lineage")
    _require(program.get("source_commit") == "73ab78d9d69494c757cec875855121b1d29320a4", "stale source commit")
    authority = program.get("authority", {})
    _require(authority.get("current_runtime_invariants_preserved") is True, "runtime invariants not preserved")
    _require(authority.get("live_authority_granted") is False, "candidate grants live authority")
    _require(authority.get("contract_acceptance") == "pending_human_digest_receipt", "contract acceptance inferred")

    research = program.get("research_evidence", {}).get("authority_compliance", {})
    _require(research.get("run_id") == "trun_db705696fec649d8ad991d8f8f7b7813", "authority research run missing")
    _require(research.get("status") == "completed", "authority research incomplete")
    _require(research.get("research_cutoff") == "2026-10-16", "authority research cutoff missing")
    _require(research.get("claim") == "research_input_only", "authority research over-promoted")
    research_path = research.get("reconciliation_path")
    _require(research_path == "docs/replacement/AUTHORITY-COMPLIANCE-RESEARCH.md", "authority reconciliation path mismatch")
    research_file = root / research_path
    _require(research_file.is_file(), "authority reconciliation file missing")
    _require(re.fullmatch(r"[0-9a-f]{64}", research.get("sha256", "")) is not None, "authority reconciliation digest invalid")
    _require(_digest(research_file) == research["sha256"], "authority reconciliation digest mismatch")

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

    outcomes = program.get("outcomes")
    _require(isinstance(outcomes, list) and len(outcomes) == 9, "exactly nine outcomes required")
    expected_keys = [f"O{i}" for i in range(1, 10)]
    keys = [item.get("key") for item in outcomes]
    _require(keys == expected_keys, "outcomes must be ordered O1 through O9")
    issues: set[str] = set()
    seen: set[str] = set()
    for item in outcomes:
        key = item["key"]
        missing = REQUIRED_OUTCOME_FIELDS - set(item)
        _require(not missing, f"{key} missing fields: {sorted(missing)}")
        _require(item["parent_issue"] == "CSL-212", f"{key} missing parent lineage")
        issue = item["linear_issue"]
        _require(isinstance(issue, str) and re.fullmatch(r"CSL-[0-9]+", issue) is not None, f"{key} invalid Linear issue")
        _require(issue not in issues, f"duplicate Linear issue: {issue}")
        issues.add(issue)
        for field in REQUIRED_OUTCOME_FIELDS - {"dependencies", "dependency_contracts", "evidence", "falsifiers", "deliverables", "non_goals", "inherited_decisions"}:
            _require(_nonempty(item[field]), f"{key} empty {field}")
        _require(item["discretion_class"] in {"bounded_autonomy", "collaborative_judgment", "human_gated"}, f"{key} invalid discretion class")
        _require(isinstance(item["research_budget"], str) and "stop" in item["research_budget"].lower(), f"{key} research budget lacks stop condition")
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
            _require(isinstance(item[field], list) and item[field], f"{key} empty {field}")
        verification = item["independent_verification"]
        _require(_nonempty(verification) and "independent" in verification.lower(), f"{key} missing independent verification")
        _require(item["completion_gate_status"] == "pending_outcome_artifact", f"{key} completion gate status is unsupported")
        _require("must be checked in" in item["completion_gate"].lower(), f"{key} completion gate falsely claims execution")
        evidence = item["evidence"]
        _require(isinstance(evidence, dict), f"{key} evidence must be an object")
        claimed = evidence.get("claimed")
        not_claimed = evidence.get("not_claimed")
        _require(claimed in MATURITY, f"{key} invalid claimed maturity")
        _require(isinstance(not_claimed, list) and not_claimed, f"{key} missing unclaimed levels")
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
