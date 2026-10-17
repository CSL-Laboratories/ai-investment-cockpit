"""Provider-free validator for the CSL-220 O8 phase A contract artifacts.

Checks the frozen fixture, candidate manifests, and negative controls.
It does not run benchmarks, open a network connection, store a credential,
or select a language, storage layout, or topology.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
O8 = ROOT / "docs" / "replacement" / "outcomes" / "O8"
PACKET_PATH = O8 / "fixtures" / "phase-a-packet.json"
NEGATIVE_DIR = O8 / "fixtures" / "negative"
SCHEMAS = O8 / "schemas"
BENCHMARK_SCHEMA = SCHEMAS / "benchmark-manifest.schema.json"
CANDIDATE_SCHEMA = SCHEMAS / "candidate-manifest.schema.json"
PACKET_SCHEMA = SCHEMAS / "phase-a-packet.schema.json"

INSPECTION_COMMIT = "ebbaa34489f171e079834d02098135711e1d4755"
REGISTRY_BASELINE = "73ab78d9d69494c757cec875855121b1d29320a4"
SEARCH_IDS = (
    "search_8afa65003c515c3b57197de5f5cd25c4",
    "search_902e146c8a56d6e091750abdb2b9d763",
)
CITED_URLS = (
    "https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig",
    "https://pyperf.readthedocs.io/en/stable/run_benchmark.html",
    "https://pyperf.readthedocs.io/en/latest/analyze.html",
    "https://github.com/psf/pyperf",
    "https://enersys.co.th/en/insights/strangler-fig-migration-python-to-rust-2026",
)
ACCEPTED_DIGESTS = {
    "docs/replacement/SUCCESSOR-PRODUCT.md": "6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e",
    "docs/replacement/ISSUE-CONTRACT.md": "f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094",
    "docs/replacement/MONTH-PROGRAM.md": "f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001",
    "docs/replacement/SCOPE-AUDIT.md": "d23dcc769e743d199ae4142f0e20ad5feebfe310c71d9f57fdd9d647c230db8c",
}
FLOOR_IDS = (
    "invariant_1_single_order_path",
    "invariant_2_decision_pinned_in_auto_modes",
    "invariant_3_schema_validate_model_output",
    "invariant_4_secrets_os_credential_store",
    "invariant_5_risk_pure",
    "invariant_6_human_gate_hash_chain_audit",
    "invariant_7_fail_closed",
    "invariant_8_raw_inputs_separate_from_summaries",
    "invariant_9_post_pdt",
    "fail_closed",
    "hash_chain_audit",
)
FLOOR_LOCATORS = {
    "invariant_1_single_order_path": "app/tests/test_invariants.py",
    "invariant_2_decision_pinned_in_auto_modes": "app/orchestrator/mode_ladder.py",
    "invariant_3_schema_validate_model_output": "app/workflows/schema_loader.py",
    "invariant_4_secrets_os_credential_store": "app/core/secrets.py",
    "invariant_5_risk_pure": "app/risk/engine.py",
    "invariant_6_human_gate_hash_chain_audit": "app/storage/audit.py",
    "invariant_7_fail_closed": "app/orchestrator/breakers.py",
    "invariant_8_raw_inputs_separate_from_summaries": "app/filings/raw_store.py",
    "invariant_9_post_pdt": "app/risk/engine.py",
    "fail_closed": "app/orchestrator/breakers.py",
    "hash_chain_audit": "app/storage/audit.py",
}
WORKLOAD_IDS = (
    "ingest_to_raw_store",
    "point_in_time_replay",
    "frozen_evaluation_run",
    "paper_order_lifecycle",
    "reconciliation",
    "gui_cli_render",
)
TIER1_CASES = (
    "risk_evaluate",
    "order_tuple_hash",
    "schema_validate_proposal",
    "outbound_scrub",
    "audit_append_verify",
)
DECISION_CLASSES = ("spend", "credentials", "tax_legal", "architecture", "release", "live")
EXPECTED_DISPOSITION = {
    "risk-engine": "retain",
    "order-gateway": "retain",
    "audit-ledger": "retain",
    "fail-closed-breakers": "retain",
    "secrets-store": "retain",
    "raw-versus-summary-split": "retain",
    "tier1-hot-path-tripwire": "retain",
    "raw-store-implementation": "measure",
    "equity-ingest": "measure",
    "point-in-time-replay": "measure",
    "paper-order-lifecycle": "measure",
    "reconciliation": "measure",
    "frozen-evaluation": "measure",
    "quant-book-and-optional-rust": "measure",
    "operator-discord-and-loopback": "rework",
    "llm-workflows": "undecided",
    "merger-strategy-experiment": "undecided",
    "tax-and-prediction-markets": "undecided",
    "wholesale-rewrite-csl-42": "undecided",
}
NEGATIVES = (
    ("omit-io.json", "benchmark", "omits I/O"),
    ("omit-helpers.json", "benchmark", "omits helper processes"),
    ("omit-variance.json", "benchmark", "omits variance"),
    ("provider-latency-unlabeled.json", "benchmark", "omits provider latency"),
    ("candidate-missing-floors.json", "candidate", "candidate missing floors"),
    ("floor-enforced-by-benchmark.json", "candidate", "candidate missing floors"),
    ("seam-missing-rollback.json", "candidate", "migration cannot roll back"),
    (
        "candidate-missing-smallest-experiment.json",
        "candidate",
        "candidate missing smallest experiment",
    ),
)


class O8Error(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise O8Error([f"unreadable JSON {path}: {exc}"]) from exc


def _schema_errors(instance: Any, schema_path: Path) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError as exc:
        raise O8Error(["jsonschema is required for the provider-free validator"]) from exc
    schema = _load_json(schema_path)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise O8Error([f"schema {schema_path.name} is invalid: {exc.message}"]) from exc
    validator = Draft202012Validator(schema)
    found = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        found.append(f"schema {schema_path.name} {location}: {error.message}")
    return found


def _local_path_exists(locator: str) -> bool:
    if locator.startswith("https://") or locator.startswith("http://"):
        return True
    return (ROOT / locator).exists()


def benchmark_reasons(document: Any) -> list[str]:
    reasons: list[str] = []
    if not isinstance(document, dict):
        return ["omits I/O", "omits helper processes", "omits variance", "omits provider latency"]
    io = document.get("io")
    if not isinstance(io, dict) or io.get("included") is not True or not io.get("kinds"):
        reasons.append("omits I/O")
    helpers = document.get("helper_processes")
    helpers_ok = isinstance(helpers, list) and len(helpers) > 0 and all(
        isinstance(item, dict) and item.get("included") is True and item.get("name") and item.get("role")
        for item in helpers
    )
    if not helpers_ok:
        reasons.append("omits helper processes")
    variance = document.get("variance")
    variance_ok = (
        isinstance(variance, dict)
        and variance.get("statistic") == "relative_iqr"
        and "repetitions" in variance
        and "relative_iqr" in variance
        and "status" in variance
    )
    if not variance_ok:
        reasons.append("omits variance")
    provider_latency = document.get("provider_latency")
    # Benchmarks are provider-free (network_calls == 0), so a numeric latency would be fictitious.
    provider_latency_ok = (
        isinstance(provider_latency, dict)
        and provider_latency.get("status") == "excluded_with_reason"
        and isinstance(provider_latency.get("reason"), str)
        and provider_latency.get("reason").strip() != ""
    )
    if not provider_latency_ok:
        reasons.append("omits provider latency")
    if document.get("tier") in (2, 3) and document.get("csl42_evidence") is True:
        measured = isinstance(variance, dict) and variance.get("status") == "measured"
        if document.get("tier") != 3 or document.get("floors_attached") is not True or not measured:
            reasons.append("tier-3 evidence claimed before measurement")
    if document.get("csl42_evidence") is True:
        baseline = document.get("baseline")
        if (
            not isinstance(baseline, dict)
            or baseline.get("commit") == document.get("commit")
            or baseline.get("workload_id") != document.get("workload_id")
            or baseline.get("fixture_sha256") != document.get("fixture_sha256")
        ):
            reasons.append("tier-3 evidence lacks a matched baseline")
    reasons.extend(_schema_errors(document, BENCHMARK_SCHEMA))
    return reasons


def _floor_ids(floors: Any) -> list[str]:
    if not isinstance(floors, list):
        return []
    found = []
    for floor in floors:
        if isinstance(floor, dict) and isinstance(floor.get("id"), str):
            found.append(floor["id"])
    return found


def candidate_reasons(document: Any) -> list[str]:
    reasons: list[str] = []
    floors = document.get("floors") if isinstance(document, dict) else None
    if not isinstance(floors, list):
        reasons.append("candidate missing floors")
    else:
        for floor in floors:
            if not isinstance(floor, dict) or floor.get("enforced_by") != "tests" or floor.get("benchmark_eligible") is not False:
                reasons.append("candidate missing floors")
                break
        for floor in floors:
            if isinstance(floor, dict) and (
                floor.get("test_locator") != FLOOR_LOCATORS.get(floor.get("id"))
                or not _local_path_exists(str(floor.get("test_locator")))
            ):
                reasons.append("candidate missing floors")
                break
        missing = [floor_id for floor_id in FLOOR_IDS if floor_id not in _floor_ids(floors)]
        if missing:
            reasons.append("candidate missing floors")
    experiment = document.get("smallest_experiment") if isinstance(document, dict) else None
    if not (
        isinstance(experiment, dict)
        and experiment.get("workload_id") in WORKLOAD_IDS
        and isinstance(experiment.get("statement"), str)
        and experiment.get("statement").strip() != ""
    ):
        reasons.append("candidate missing smallest experiment")
    if not isinstance(document, dict) or document.get("seam") not in WORKLOAD_IDS:
        reasons.append("candidate missing seam")
    elif isinstance(experiment, dict) and experiment.get("workload_id") != document.get("seam"):
        reasons.append("candidate experiment does not exercise its seam")
    rollback = document.get("rollback") if isinstance(document, dict) else None
    if not (
        isinstance(rollback, dict)
        and rollback.get("route_back_to_legacy") is True
        and rollback.get("system_of_record") == "legacy_raw_store_and_hash_chain"
    ):
        reasons.append("migration cannot roll back")
    if isinstance(document, dict) and document.get("maturity") not in (None, "researched"):
        reasons.append("maturity is researched only")
    reasons.extend(_schema_errors(document, CANDIDATE_SCHEMA))
    return list(dict.fromkeys(reasons))


def _require_local(reasons: list[str], locator: str, label: str) -> None:
    if not _local_path_exists(locator):
        reasons.append(f"missing {label}: {locator}")


def _check_floors(floors: Any, reasons: list[str]) -> None:
    if _floor_ids(floors) != list(FLOOR_IDS):
        reasons.append("candidate missing floors")
        return
    for floor in floors:
        if not isinstance(floor, dict):
            reasons.append("candidate missing floors")
            return
        if floor.get("enforced_by") != "tests" or floor.get("benchmark_eligible") is not False:
            reasons.append("candidate missing floors")
            return
        expected = FLOOR_LOCATORS.get(floor.get("id"))
        if floor.get("test_locator") != expected:
            reasons.append(f"floor locator drift: {floor.get('id')}")
        elif expected is not None:
            _require_local(reasons, expected, "floor test")


def validate_packet(document: Any) -> None:
    reasons: list[str] = []
    if not isinstance(document, dict):
        raise O8Error(["packet root must be an object"])
    reasons.extend(_schema_errors(document, PACKET_SCHEMA))
    for path, digest in ACCEPTED_DIGESTS.items():
        file_path = ROOT / path
        actual = hashlib.sha256(file_path.read_bytes()).hexdigest() if file_path.is_file() else ""
        if actual != digest:
            reasons.append(f"accepted CSL-212 document changed: {path}")
    research = document.get("research") if isinstance(document.get("research"), dict) else {}
    if tuple(research.get("parallel_search_ids", ())) != SEARCH_IDS:
        reasons.append("parallel search ids drifted")
    if tuple(research.get("cited_urls", ())) != CITED_URLS:
        reasons.append("cited research URLs drifted")
    rows = document.get("disposition") if isinstance(document.get("disposition"), list) else []
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            reasons.append("disposition row is not an object")
            continue
        component_id = row.get("component_id")
        found[component_id] = row.get("disposition")
        if row.get("disposition") == "replace":
            reasons.append("replace without tier-3 evidence")
        for locator in row.get("paths") or []:
            _require_local(reasons, str(locator), "disposition path")
        for item in row.get("evidence") or []:
            if isinstance(item, dict):
                _require_local(reasons, str(item.get("locator", "")), "evidence locator")
    if found != EXPECTED_DISPOSITION:
        reasons.append("disposition matrix drifted")
    _check_floors(document.get("hard_floors"), reasons)
    candidate = document.get("candidate")
    reasons.extend(candidate_reasons(candidate))
    if isinstance(candidate, dict) and candidate.get("floors") != document.get("hard_floors"):
        reasons.append("candidate floors differ from the hard-floor registry")
    protocol = document.get("protocol") if isinstance(document.get("protocol"), dict) else {}
    tier1 = protocol.get("tier1") if isinstance(protocol.get("tier1"), dict) else {}
    if tuple(tier1.get("cases", ())) != TIER1_CASES:
        reasons.append("tier-1 tripwire cases drifted")
    workloads = document.get("workloads") if isinstance(document.get("workloads"), list) else []
    workload_ids = [item.get("workload_id") for item in workloads if isinstance(item, dict)]
    if tuple(workload_ids) != WORKLOAD_IDS:
        reasons.append("tier-2 workload set drifted")
    tier2 = protocol.get("tier2") if isinstance(protocol.get("tier2"), dict) else {}
    if tuple(tier2.get("workloads", ())) != WORKLOAD_IDS:
        reasons.append("tier-2 protocol workload list drifted")
    migration = document.get("migration") if isinstance(document.get("migration"), dict) else {}
    if tuple(migration.get("seams", ())) != WORKLOAD_IDS:
        reasons.append("migration seams drifted")
    for workload in workloads:
        workload_problems = benchmark_reasons(workload)
        if workload_problems:
            name = workload.get("workload_id") if isinstance(workload, dict) else "<workload>"
            reasons.append(f"workload {name}: {workload_problems[0]}")
        if isinstance(workload, dict):
            if workload.get("csl42_evidence") is not False:
                reasons.append("tier-3 evidence claimed before measurement")
            if workload.get("commit") != INSPECTION_COMMIT:
                reasons.append("workload commit is not the inspection commit")
            variance = workload.get("variance")
            if not isinstance(variance, dict) or variance.get("status") != "required_on_run":
                reasons.append("phase A workload records a measurement")
    decisions = document.get("unresolved_decisions") if isinstance(document.get("unresolved_decisions"), list) else []
    classes = [item.get("class") for item in decisions if isinstance(item, dict)]
    if tuple(classes) != DECISION_CLASSES:
        reasons.append("unresolved decision classes drifted")
    for item in decisions:
        if isinstance(item, dict) and (item.get("owner") != "Lucas" or item.get("status") != "unresolved"):
            reasons.append("a human decision was resolved in the packet")
    if document.get("source_commit") != INSPECTION_COMMIT or document.get("registry_baseline") != REGISTRY_BASELINE:
        reasons.append("source identity drifted")
    if document.get("maturity") != "researched":
        reasons.append("maturity is researched only")
    if reasons:
        raise O8Error(list(dict.fromkeys(reasons)))


def run_negative_controls() -> list[str]:
    lines = []
    for name, kind, expected in NEGATIVES:
        path = NEGATIVE_DIR / name
        document = _load_json(path)
        reasons = benchmark_reasons(document) if kind == "benchmark" else candidate_reasons(document)
        if expected not in reasons:
            detail = ", ".join(reasons) if reasons else "accepted"
            raise O8Error([f"negative control {name} did not fail as designed ({detail})"])
        lines.append(f"O8 negative control failed as designed: {path.stem}: {expected}")
    lines.append(f"O8 negative controls: {len(lines)} failed as designed")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the O8 phase A contract fixture.")
    parser.add_argument("--manifest", type=Path, help="Validate one JSON manifest instead of the frozen packet.")
    parser.add_argument("--kind", choices=("packet", "benchmark", "candidate"), default="packet")
    parser.add_argument("--negative-controls", action="store_true", help="Require the checked-in negative fixtures to fail.")
    args = parser.parse_args(argv)
    try:
        if args.negative_controls:
            for line in run_negative_controls():
                print(line)
            return 0
        path = args.manifest if args.manifest is not None else PACKET_PATH
        document = _load_json(path)
        kind = args.kind if args.manifest is not None else "packet"
        if kind == "packet":
            validate_packet(document)
        elif kind == "benchmark":
            reasons = benchmark_reasons(document)
            if reasons:
                raise O8Error(reasons)
        elif kind == "candidate":
            reasons = candidate_reasons(document)
            if reasons:
                raise O8Error(reasons)
        else:
            raise O8Error([f"unknown manifest kind {kind}"])
    except O8Error as exc:
        print(f"O8-phase-A: rejected: {exc}", file=sys.stderr)
        return 1
    print(
        "O8-phase-A: fixture accepted maturity=researched "
        "completion_gate_status=pending_outcome_artifact "
        "per_pr_benchmark_gating=false new_ci_cadence=false"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
