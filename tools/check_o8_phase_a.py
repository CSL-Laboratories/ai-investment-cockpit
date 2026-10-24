"""Provider-free validator for the CSL-220 O8 phase A contract artifacts.

Checks the frozen fixture, candidate manifests, and negative controls.
It does not run benchmarks, open a network connection, store a credential,
or select a language, storage layout, or topology.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
O8 = ROOT / "docs" / "replacement" / "outcomes" / "O8"
PACKET_PATH = O8 / "fixtures" / "phase-a-packet.json"
NEGATIVE_DIR = O8 / "fixtures" / "negative"
SCHEMAS = O8 / "schemas"
BENCHMARK_SCHEMA = SCHEMAS / "benchmark-manifest.schema.json"
CANDIDATE_SCHEMA = SCHEMAS / "candidate-manifest.schema.json"
PACKET_SCHEMA = SCHEMAS / "phase-a-packet.schema.json"
PYTHON_RUNTIME_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+([a-z]+[0-9]+)?([ \t][^\r\n\u2028\u2029]+)?(?![\s\S])"

INSPECTION_COMMIT = "ebbaa34489f171e079834d02098135711e1d4755"
REGISTRY_BASELINE = "73ab78d9d69494c757cec875855121b1d29320a4"
SEARCH_IDS = (
    "search_8afa65003c515c3b57197de5f5cd25c4",
    "search_902e146c8a56d6e091750abdb2b9d763",
    "search_b6290caf1f31d7a8a710db6b71f76c7e",
    "search_86967d451c3ce6e3c60de7a6868def47",
    "search_4b647f1aa2547fad5b4062554a71c354",
    "search_41e4072e4bc070139371640730ea2eb3",
)
CITED_URLS = (
    "https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig",
    "https://pyperf.readthedocs.io/en/stable/run_benchmark.html",
    "https://pyperf.readthedocs.io/en/latest/analyze.html",
    "https://github.com/psf/pyperf",
    "https://enersys.co.th/en/insights/strangler-fig-migration-python-to-rust-2026",
    "https://adaptive-enforcement-lab.com/patterns/architecture/strangler-fig/migration-guide/",
    "https://martinfowler.com/bliki/ParallelChange.html",
    "https://asv.readthedocs.io/en/stable/writing_benchmarks.html",
    "https://asv.readthedocs.io/en/stable/using.html",
    "https://github.com/sharkdp/hyperfine",
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
    "invariant_1_single_order_path": [
        "app/tests/test_invariants.py::test_invariant_1_order_calls_only_in_brokers",
        "app/tests/brokers/test_gateway.py::test_b_mode_without_order_authority_refuses",
        "app/tests/brokers/test_gateway.py::test_d_toctou_risk_flip_to_fail_voids_and_records_bypass",
        "app/tests/brokers/test_gateway.py::test_e_consumed_approval_replay_refused",
        "app/tests/brokers/test_gateway.py::test_g_no_token_refuses_at_capability",
        "app/tests/system/test_forged_interaction.py::test_g9_forged_interaction_is_rejected"
    ],
    "invariant_2_decision_pinned_in_auto_modes": [
        "app/tests/orchestrator/test_g5_acceptance.py::test_g5_3_arm_refuses_non_claude_decision_stage_explicit",
        "app/tests/orchestrator/test_g5_acceptance.py::test_g5_3_sec08_execution_runtime_refused_even_after_valid_arm",
        "app/tests/brokers/test_gateway.py::test_f_auto_mode_non_claude_runtime_refused"
    ],
    "invariant_3_schema_validate_model_output": [
        "app/tests/agent_runner/test_runner.py::test_decision_job_requires_schema",
        "app/tests/agent_runner/test_runner.py::test_repair_success",
        "app/tests/workflows/test_agent_calls.py::test_still_malformed_output_quarantines_job"
    ],
    "invariant_4_secrets_os_credential_store": [
        "app/tests/test_wp0.py::test_secret_roundtrip_via_keyring",
        "app/tests/test_invariants.py::test_invariant_4_no_plaintext_secrets_in_app_or_operator_tools",
        "app/tests/workflows/test_agent_calls.py::test_provisioned_secret_in_the_prompt_is_refused",
        "app/tests/system/test_secrets_in_logs.py::test_g9_no_secrets_or_pii_in_logs"
    ],
    "invariant_5_risk_pure": [
        "app/tests/test_invariants.py::test_invariant_5_risk_engine_has_no_llm",
        "app/tests/test_invariants.py::test_the_risk_engine_itself_did_not_acquire_an_impure_import",
        "app/tests/test_invariants.py::test_the_execution_verifier_stays_pure_because_risk_imports_it"
    ],
    "invariant_6_human_gate_hash_chain_audit": [
        "app/tests/system/test_forged_interaction.py::test_g9_forged_interaction_is_rejected",
        "app/tests/storage/test_audit.py::test_append_and_verify",
        "app/tests/storage/test_audit.py::test_tamper_is_detected",
        "app/tests/storage/test_audit.py::test_truncation_detected_by_watermark",
        "app/tests/storage/test_audit.py::test_completeness_catches_unaudited_state"
    ],
    "invariant_7_fail_closed": [
        "app/tests/workflows/test_agent_calls.py::test_limit_hit_enters_watch_only_once_and_mode_unchanged",
        "app/tests/workflows/test_agent_calls.py::test_timeout_enters_watch_only",
        "app/tests/orchestrator/test_g5_acceptance.py::test_g5_4_stale_market_data_goes_watch_only_not_review_live",
        "app/tests/brokers/test_gateway.py::test_b_watch_only_refuses",
        "app/tests/orchestrator/test_oob_kill.py::test_out_of_band_cli_set_refuses_next_submit_before_broker"
    ],
    "invariant_8_raw_inputs_separate_from_summaries": [
        "app/tests/workflows/test_rw23_raw_news.py::test_long_original_news_is_recoverable_without_entering_prompt",
        "app/tests/ingest/test_characterization.py::test_evidence_writer_separates_raw_and_summary_and_hash_binds_them"
    ],
    "invariant_9_post_pdt": [
        "app/tests/test_o8_floor_regressions.py::test_post_pdt_reg_t_exact_equity_boundary",
        "app/tests/test_o8_floor_regressions.py::test_post_pdt_has_no_day_trade_counter_gate"
    ],
    "fail_closed": [
        "app/tests/workflows/test_agent_calls.py::test_limit_hit_enters_watch_only_once_and_mode_unchanged",
        "app/tests/workflows/test_agent_calls.py::test_timeout_enters_watch_only",
        "app/tests/orchestrator/test_g5_acceptance.py::test_g5_4_stale_market_data_goes_watch_only_not_review_live",
        "app/tests/brokers/test_gateway.py::test_b_watch_only_refuses",
        "app/tests/orchestrator/test_oob_kill.py::test_out_of_band_cli_set_refuses_next_submit_before_broker"
    ],
    "hash_chain_audit": [
        "app/tests/storage/test_audit.py::test_append_and_verify",
        "app/tests/storage/test_audit.py::test_tamper_is_detected",
        "app/tests/storage/test_audit.py::test_truncation_detected_by_watermark",
        "app/tests/storage/test_audit.py::test_completeness_catches_unaudited_state"
    ]
}
# Floor coverage the registered selectors do not yet provide. The agent-timeout and
# out-of-band HALT selectors do not prove automatic hung-orchestrator detection, so
# that requirement stays binding and unqualified (PHASE-A.md coverage limitation).
# No selector is registered until a reviewed detector test exists (CSL-333). A future
# registration names one resolvable selector that every listed floor must contain.
FLOOR_COVERAGE_REQUIREMENTS = {
    "hung_orchestrator_detection": {
        "floors": ("invariant_7_fail_closed", "fail_closed"),
        "selector": None,
    },
}
FLOOR_QUALIFICATION_INCOMPLETE = "tier-3 floor qualification incomplete"
OBSERVATION_NARRATIVES = ("correctness", "failure", "recovery", "growth")
WORKLOAD_IDS = (
    "ingest_to_raw_store",
    "point_in_time_replay",
    "frozen_evaluation_run",
    "paper_order_lifecycle",
    "reconciliation",
    "gui_cli_render",
)
WORKLOAD_HELPERS = {
    "ingest_to_raw_store": {"ingest-worker": "primary", "raw-store": "storage-helper"},
    "point_in_time_replay": {"replay-worker": "primary", "raw-store": "storage-helper"},
    "frozen_evaluation_run": {"evaluation-worker": "primary", "fixture-reader": "storage-helper"},
    "paper_order_lifecycle": {
        "paper-lifecycle": "primary",
        "sim-venue": "execution-helper",
        "audit-appender": "audit-helper",
    },
    "reconciliation": {
        "reconciliation-worker": "primary",
        "ledger-reader": "storage-helper",
        "audit-verifier": "audit-helper",
    },
    "gui_cli_render": {"render-process": "primary", "ledger-reader": "storage-helper"},
}
WORKLOAD_IO_KINDS = {
    "ingest_to_raw_store": {"disk"},
    "point_in_time_replay": {"disk"},
    "frozen_evaluation_run": {"disk"},
}
OBSERVATION_UNITS = {
    "latency": "ms",
    "throughput": "workload_completions_per_second",
    "rss_bytes": "bytes",
    "cpu": "cpu_seconds",
    "io": "bytes",
    "tokens": "tokens",
    "cost": "USD",
}
REPLAY_SOURCES = {"raw_inputs", "hash_chain_audit"}
SHARED_STORES = {"raw_bytes", "hash_chain_audit"}
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
    ("missing-measured-observation.json", "benchmark", "tier-3 evidence lacks measured observation"),
    ("impossible-observation.json", "benchmark", "impossible measured observation"),
    ("baseline-unbound-outputs.json", "benchmark", "tier-3 baseline lacks retained outputs"),
    ("attached-floors-invalid.json", "benchmark", "attached floors invalid"),
    ("experiment-seam-mismatch.json", "candidate", "candidate experiment does not exercise its seam"),
    ("non-finite-literal.json", "benchmark", "unreadable JSON"),
    ("overflow-literal.json", "benchmark", "unreadable JSON"),
    ("baseline-outputs-absolute.json", "benchmark", "baseline outputs path must be relative"),
    (
        "baseline-outputs-traversal.json",
        "benchmark",
        "baseline outputs path escapes the repository",
    ),
    ("baseline-metadata-only.json", "benchmark", "tier-3 baseline lacks retained outputs"),
    ("baseline-observation-untyped.json", "benchmark", "tier-3 baseline observation is not measured"),
    ("helper-set-mismatch.json", "benchmark", "helper set does not match the workload"),
    ("replay-from-duplicate.json", "packet", "replay sources must be distinct"),
    ("shared-stores-duplicate.json", "packet", "shared stores must be distinct"),
    ("baseline-outputs-backslash-root.json", "benchmark", "baseline outputs path must be relative"),
    ("baseline-outputs-unc-slash.json", "benchmark", "baseline outputs path must be relative"),
    ("baseline-outputs-drive-abs.json", "benchmark", "baseline outputs path must be relative"),
    ("baseline-outputs-drive-abs-slash.json", "benchmark", "baseline outputs path must be relative"),
    ("baseline-outputs-drive-rel.json", "benchmark", "baseline outputs path must be relative"),
    ("baseline-outputs-mid-backslash.json", "benchmark", "baseline outputs path must be relative"),
    ("helper-duplicate.json", "benchmark", "helper set does not match the workload"),
    ("tokens-negative.json", "benchmark", "tier-3 evidence lacks measured observation"),
    ("cost-negative.json", "benchmark", "tier-3 evidence lacks measured observation"),
    ("tokens-huge-int.json", "benchmark", "tier-3 evidence lacks measured observation"),
    ("fixture-sha-unbound.json", "benchmark", "fixture hash does not match retained fixture"),
    ("units-missing.json", "benchmark", "observation units missing or invalid"),
    ("units-mismatched.json", "benchmark", "observation units missing or invalid"),
    ("baseline-units-mismatched.json", "benchmark", "tier-3 baseline lacks retained outputs"),
    ("io-kind-memory-only.json", "benchmark", "I/O kinds omit required disk I/O for this workload"),
    ("candidate-id-unbound.json", "benchmark", "candidate does not match retained candidate manifest"),
    ("candidate-commit-mismatch.json", "benchmark", "candidate does not match retained candidate manifest"),
    ("candidate-config-mismatch.json", "benchmark", "candidate does not match retained candidate manifest"),
    ("baseline-host-mismatch.json", "benchmark", "tier-3 baseline host does not match the candidate"),
    ("baseline-runner-mismatch.json", "benchmark", "tier-3 baseline host does not match the candidate"),
    ("baseline-config-mismatch.json", "benchmark", "tier-3 baseline host does not match the candidate"),
    ("baseline-python-coarse.json", "benchmark", "tier-3 baseline host does not match the candidate"),
    ("floor-results-missing.json", "benchmark", "tier-3 floor results are not bound to the candidate"),
    ("floor-results-wrong-commit.json", "benchmark", "tier-3 floor results are not bound to the candidate"),
    ("floor-results-failed.json", "benchmark", "tier-3 floor results are not bound to the candidate"),
    ("blank-outcome-correctness.json", "benchmark", "measured observation narrative missing or blank: correctness"),
    ("blank-outcome-failure.json", "benchmark", "measured observation narrative missing or blank: failure"),
    ("blank-outcome-recovery.json", "benchmark", "measured observation narrative missing or blank: recovery"),
    ("blank-outcome-growth.json", "benchmark", "measured observation narrative missing or blank: growth"),
)


class O8Error(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _parse_finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_float,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
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


def _is_rooted(value: str) -> bool:
    """True for absolute or rooted paths on POSIX or Windows, and for any backslash form.

    Covers leading '/' or '\\', drive-absolute (C:\\x and C:/x), drive-relative
    (C:foo), UNC (\\\\server\\share and //server/share), and any backslash at all,
    so a packet-supplied path cannot smuggle a platform-specific escape.
    """
    if "\\" in value:
        return True
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return True
    return bool(PureWindowsPath(value).drive)


def _resolve_local_path(value: str) -> Path | None:
    """Resolve a packet-supplied local path under ROOT; None when absolute, escaping, or unresolvable."""
    if not value or "\x00" in value or _is_rooted(value):
        return None
    try:
        resolved = (ROOT / value).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not resolved.is_relative_to(ROOT):
        return None
    return resolved


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, ValueError, RuntimeError):
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, ValueError, RuntimeError):
        return False


def _local_path_exists(locator: str) -> bool:
    if "\x00" in locator:
        return False
    if locator.startswith(("https://", "http://")):
        return True
    resolved = _resolve_local_path(locator)
    return resolved is not None and _safe_exists(resolved)


def _test_locator_exists(locator: Any) -> bool:
    """Resolve a registered pytest function selector without importing test code.

    The bounded grammar selects a top-level test function and all of its
    parameter cases. Collection and execution are separate validation steps;
    source existence is never a passing test result.
    """
    if not isinstance(locator, str) or locator.count("::") != 1:
        return False
    relative, name = locator.split("::")
    path = PurePosixPath(relative)
    if (
        not relative.startswith("app/tests/")
        or path.as_posix() != relative
        or ".." in path.parts
        or not path.name.startswith("test_")
        or path.suffix != ".py"
        or not name.startswith("test_")
        or not name.isidentifier()
    ):
        return False
    resolved = _resolve_local_path(relative)
    # No symlink/junction aliases: a locator names the actual in-repository test.
    if resolved is None or resolved != ROOT / relative:
        return False
    raw = _read_regular_file(resolved)
    if raw is None:
        return False
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=relative)
    except (UnicodeError, SyntaxError, ValueError, RecursionError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        for node in tree.body
    )


def _floor_locators_valid(floor: dict[str, Any]) -> bool:
    floor_id = floor.get("id")
    if not isinstance(floor_id, str):
        return False
    expected = FLOOR_LOCATORS.get(floor_id)
    return (
        expected is not None
        and floor.get("test_locators") == expected
        and all(_test_locator_exists(locator) for locator in expected)
    )


def _is_finite_nonneg(value: Any) -> bool:
    """Require nonnegative finite metrics representable as a float, without overflowing."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= sys.float_info.max
    return isinstance(value, float) and math.isfinite(value) and value >= 0


def _read_regular_file(path: Path) -> bytes | None:
    """Open one regular file and read that handle.

    ``O_NOFOLLOW`` is set where the platform defines it, so a symlink that
    replaces the path between the check and the read is not followed. The
    opened inode is also compared with the inode observed before open.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        observed = os.lstat(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(observed.st_mode):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except (OSError, ValueError):
        return None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            return None
        if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
            return None
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def _retained_bytes(reference: Any) -> bytes | None:
    """Read only digest-matched, repository-contained regular files."""
    if not isinstance(reference, dict):
        return None
    path, digest = reference.get("path"), reference.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        return None
    if path.startswith(("http://", "https://")):
        return None
    retained = _resolve_local_path(path)
    if retained is None:
        return None
    raw = _read_regular_file(retained)
    if raw is None:
        return None
    return raw if hashlib.sha256(raw).hexdigest() == digest else None


def _retained_candidate_bound(document: dict[str, Any]) -> bool:
    raw = _retained_bytes(document.get("candidate"))
    if raw is None:
        return False
    try:
        retained = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_float,
        )
    except (UnicodeError, ValueError, RecursionError):
        return False
    return (
        isinstance(retained, dict)
        and not _schema_errors(retained, CANDIDATE_SCHEMA)
        and isinstance(retained.get("commit"), str)
        and isinstance(retained.get("configuration"), dict)
        and bool(retained["configuration"])
        and all(retained.get(key) == document.get(key) for key in ("candidate_id", "commit"))
        and json.dumps(retained["configuration"], sort_keys=True) == json.dumps(document.get("configuration"), sort_keys=True)
        and retained.get("seam") == document.get("workload_id")
        and not candidate_reasons(retained)
    )


def benchmark_reasons(document: Any) -> list[str]:
    reasons: list[str] = []
    if not isinstance(document, dict):
        return ["omits I/O", "omits helper processes", "omits variance", "omits provider latency"]
    io = document.get("io")
    if not isinstance(io, dict) or io.get("included") is not True or not io.get("kinds"):
        reasons.append("omits I/O")
    else:
        required_io = WORKLOAD_IO_KINDS.get(document.get("workload_id"), set())
        kinds = io.get("kinds")
        if required_io and (not isinstance(kinds, list) or not required_io.issubset(str(kind) for kind in kinds)):
            reasons.append("I/O kinds omit required disk I/O for this workload")
    helpers = document.get("helper_processes")
    helpers_ok = isinstance(helpers, list) and len(helpers) > 0 and all(
        isinstance(item, dict) and item.get("included") is True and item.get("name") and item.get("role")
        for item in helpers
    )
    if not helpers_ok:
        reasons.append("omits helper processes")
    else:
        expected_helpers = WORKLOAD_HELPERS.get(document.get("workload_id"))
        pairs = [(str(item.get("name")), str(item.get("role"))) for item in helpers]
        if expected_helpers is not None and (
            len(pairs) != len(expected_helpers) or dict(pairs) != expected_helpers
        ):
            reasons.append("helper set does not match the workload")
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
        fixture = document.get("fixture")
        raw_fixture = _retained_bytes(fixture)
        fixture_digest = hashlib.sha256(raw_fixture).hexdigest() if raw_fixture is not None else None
        if (
            fixture_digest is None
            or fixture_digest != document.get("fixture_sha256")
            or not isinstance(baseline, dict)
            or fixture_digest != baseline.get("fixture_sha256")
        ):
            reasons.append("fixture hash does not match retained fixture")
        if not _retained_candidate_bound(document):
            reasons.append("candidate does not match retained candidate manifest")
        if (
            not isinstance(baseline, dict)
            or baseline.get("commit") == document.get("commit")
            or baseline.get("workload_id") != document.get("workload_id")
            or baseline.get("fixture_sha256") != document.get("fixture_sha256")
        ):
            reasons.append("tier-3 evidence lacks a matched baseline")
        outputs = baseline.get("outputs") if isinstance(baseline, dict) else None
        outputs_ok = False
        outputs_rejected = False
        if isinstance(outputs, dict):
            outputs_path = outputs.get("path")
            outputs_digest = outputs.get("sha256")
            if isinstance(outputs_path, str):
                if _is_rooted(outputs_path):
                    reasons.append("baseline outputs path must be relative")
                    outputs_rejected = True
                elif outputs_path.startswith(("http://", "https://")):
                    pass
                else:
                    retained = _resolve_local_path(outputs_path)
                    if retained is None:
                        reasons.append("baseline outputs path escapes the repository")
                        outputs_rejected = True
                    elif isinstance(outputs_digest, str):
                        raw = _read_regular_file(retained)
                        if raw is not None and hashlib.sha256(raw).hexdigest() == outputs_digest:
                            identity_units = _baseline_identity_units(raw, baseline)
                            outputs_ok = identity_units and _baseline_observation_measured(raw)
                            if identity_units and not outputs_ok:
                                reasons.append("tier-3 baseline observation is not measured")
                                outputs_ok = True
                            elif outputs_ok and not _baseline_host_matches(raw, document):
                                reasons.append("tier-3 baseline host does not match the candidate")
        if not outputs_ok and not outputs_rejected:
            reasons.append("tier-3 baseline lacks retained outputs")
    if document.get("floors_attached") is True:
        _check_floors(document.get("floors"), reasons, "attached floors invalid")
    if (
        document.get("csl42_evidence") is True
        and "attached floors invalid" not in reasons
        and not _floor_results_bound(document)
    ):
        reasons.append("tier-3 floor results are not bound to the candidate")
    if document.get("csl42_evidence") is True:
        # A bound receipt is structural only; missing required coverage refuses qualification.
        reasons.extend(_floor_qualification_reasons(document))
    if isinstance(variance, dict) and variance.get("status") == "measured":
        if document.get("units") != OBSERVATION_UNITS:
            reasons.append("observation units missing or invalid")
        observation = document.get("observation")
        observation = observation if isinstance(observation, dict) else {}
        metrics_ok = all(
            _is_finite_nonneg(observation.get(key))
            for key in ("p50", "p95", "p99", "throughput", "cpu", "io")
        ) and all(
            observation.get(key) is None or _is_finite_nonneg(observation.get(key))
            for key in ("tokens", "cost")
        ) and isinstance(observation.get("rss_bytes"), int) and not isinstance(
            observation.get("rss_bytes"), bool
        ) and _is_finite_nonneg(observation.get("rss_bytes"))
        helpers_measured = isinstance(helpers, list) and all(
            isinstance(item, dict) and item.get("status") == "measured" for item in helpers
        )
        relative_iqr = variance.get("relative_iqr")
        iqr_finite = _is_finite_nonneg(relative_iqr)
        if observation.get("status") != "measured" or not metrics_ok or not helpers_measured or not iqr_finite:
            reasons.append("tier-3 evidence lacks measured observation")
        elif not (observation["p50"] <= observation["p95"] <= observation["p99"]):
            reasons.append("impossible measured observation")
        # Same rule as the retained baseline: a whitespace-only narrative is not a record.
        for key in OBSERVATION_NARRATIVES:
            value = observation.get(key)
            if not isinstance(value, str) or not value.strip():
                reasons.append(f"measured observation narrative missing or blank: {key}")
    reasons.extend(_schema_errors(document, BENCHMARK_SCHEMA))
    return reasons


def _execution_identity(host: Any, configuration: Any) -> tuple[str, str, str] | None:
    """Runner, Python runtime, and exact execution configuration, or None when incomplete."""
    if not isinstance(host, dict) or not isinstance(configuration, dict) or not configuration:
        return None
    runner = host.get("runner")
    python = host.get("python")
    if not isinstance(runner, str) or not runner.strip():
        return None
    if not isinstance(python, str) or not re.fullmatch(PYTHON_RUNTIME_PATTERN, python):
        return None
    return (runner, python, json.dumps(configuration, sort_keys=True, separators=(",", ":")))


def _baseline_host_matches(raw: bytes, document: dict[str, Any]) -> bool:
    """True when the retained baseline was measured on the candidate host and configuration."""
    try:
        retained = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_float,
        )
    except (UnicodeError, ValueError, RecursionError):
        return False
    if not isinstance(retained, dict):
        return False
    retained_identity = _execution_identity(retained.get("host"), retained.get("configuration"))
    candidate_identity = _execution_identity(document.get("host"), document.get("configuration"))
    return retained_identity is not None and retained_identity == candidate_identity


def _floor_results_bound(document: dict[str, Any]) -> bool:
    """True when retained floor results pass and name this candidate commit.

    The checker binds the receipt. It does not execute the floor tests. Binding is
    structural; ``floor_qualification_gaps`` decides whether coverage is complete.
    """
    return _bound_floor_receipt(document) is not None


def _bound_floor_receipt(document: dict[str, Any]) -> dict[str, list[str]] | None:
    """Passing selectors per floor from a bound receipt, or None when it does not bind."""
    raw = _retained_bytes(document.get("floor_results"))
    if raw is None:
        return None
    try:
        retained = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_float,
        )
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(retained, dict):
        return None
    commit = retained.get("commit")
    if not isinstance(commit, str) or commit != document.get("commit"):
        return None
    results = retained.get("results")
    if not isinstance(results, list) or len(results) != len(FLOOR_IDS):
        return None
    found: dict[str, list[str]] = {}
    for item in results:
        if not isinstance(item, dict):
            return None
        floor_id = item.get("id")
        if not isinstance(floor_id, str) or floor_id not in FLOOR_LOCATORS or floor_id in found:
            return None
        if item.get("outcome") != "pass" or not _floor_locators_valid(item):
            return None
        found[floor_id] = list(item["test_locators"])
    return found if set(found) == set(FLOOR_IDS) else None


def floor_qualification_gaps(document: Any) -> list[str]:
    """Required coverage that a structurally bound floor receipt still does not provide.

    A requirement is covered only when its registered selector resolves to a test
    function, appears in every listed floor's registry entry, and appears in the
    passing receipt row for each of those floors. Neither a receipt, a manifest
    field nor a synthetic positive control can add coverage the registry lacks.
    """
    receipt = _bound_floor_receipt(document) if isinstance(document, dict) else None
    gaps = []
    for requirement, spec in FLOOR_COVERAGE_REQUIREMENTS.items():
        selector = spec.get("selector")
        floors = spec.get("floors") or ()
        covered = (
            isinstance(selector, str)
            and len(floors) > 0
            and _test_locator_exists(selector)
            and receipt is not None
            and all(
                selector in FLOOR_LOCATORS.get(floor_id, ()) and selector in receipt.get(floor_id, ())
                for floor_id in floors
            )
        )
        if not covered:
            gaps.append(requirement)
    return gaps


def _floor_qualification_reasons(document: Any) -> list[str]:
    return [
        f"{FLOOR_QUALIFICATION_INCOMPLETE}: missing {requirement} selector coverage in "
        + " and ".join(FLOOR_COVERAGE_REQUIREMENTS[requirement]["floors"])
        for requirement in floor_qualification_gaps(document)
    ]


def _baseline_record(raw: bytes) -> dict[str, Any] | None:
    try:
        retained = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_float,
        )
    except (UnicodeError, ValueError, RecursionError):
        return None
    return retained if isinstance(retained, dict) else None


def _baseline_identity_units(raw: bytes, baseline: dict[str, Any]) -> bool:
    """True when the digest-bound file names this baseline and declares the fixed units."""
    retained = _baseline_record(raw)
    if retained is None or retained.get("units") != OBSERVATION_UNITS:
        return False
    keys = ("commit", "workload_id", "fixture_sha256")
    return all(
        isinstance(retained.get(key), str) and retained.get(key) and retained.get(key) == baseline.get(key)
        for key in keys
    )


def _baseline_observation_measured(raw: bytes) -> bool:
    """True when each retained metric is a finite number paired with its protocol unit.

    The bytes are the digest-matched baseline file. A nonempty object is not a measurement.
    """
    retained = _baseline_record(raw)
    if retained is None:
        return False
    units = retained.get("units")
    observation = retained.get("observation")
    if units != OBSERVATION_UNITS or not isinstance(observation, dict):
        return False
    if observation.get("status") != "measured":
        return False
    unit_for = {
        "p50": "latency",
        "p95": "latency",
        "p99": "latency",
        "throughput": "throughput",
        "cpu": "cpu",
        "io": "io",
    }
    for metric, unit_key in unit_for.items():
        if units.get(unit_key) != OBSERVATION_UNITS[unit_key] or not _is_finite_nonneg(observation.get(metric)):
            return False
    rss = observation.get("rss_bytes")
    if (
        units.get("rss_bytes") != OBSERVATION_UNITS["rss_bytes"]
        or isinstance(rss, bool)
        or not isinstance(rss, int)
        or not _is_finite_nonneg(rss)
    ):
        return False
    for metric in ("tokens", "cost"):
        value = observation.get(metric)
        if units.get(metric) != OBSERVATION_UNITS[metric] or (value is not None and not _is_finite_nonneg(value)):
            return False
    if not (observation["p50"] <= observation["p95"] <= observation["p99"]):
        return False
    for key in ("correctness", "failure", "recovery", "growth"):
        if not isinstance(observation.get(key), str) or not observation[key].strip():
            return False
    allowed = set(unit_for) | {"rss_bytes", "tokens", "cost", "correctness", "failure", "recovery", "growth", "status"}
    return set(observation) == allowed and set(units) == set(OBSERVATION_UNITS)


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
                not _floor_locators_valid(floor)
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


def _check_floors(floors: Any, reasons: list[str], label: str = "candidate missing floors") -> None:
    ids = _floor_ids(floors)
    if len(ids) != len(FLOOR_IDS) or set(ids) != set(FLOOR_IDS) or len(floors) != len(ids):
        reasons.append(label)
        return
    for floor in floors:
        if not isinstance(floor, dict):
            reasons.append(label)
            return
        if floor.get("enforced_by") != "tests" or floor.get("benchmark_eligible") is not False:
            reasons.append(label)
            return
        expected = FLOOR_LOCATORS.get(floor.get("id"))
        if floor.get("test_locators") != expected:
            reasons.append(f"floor locator drift: {floor.get('id')}")
        elif expected is not None:
            for locator in expected:
                if not _test_locator_exists(locator):
                    reasons.append(f"missing floor test case: {locator}")


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
    recovery = migration.get("mixed_version_recovery") if isinstance(migration.get("mixed_version_recovery"), dict) else {}
    replay_from = recovery.get("replay_from")
    if not (
        isinstance(replay_from, list)
        and len(replay_from) == 2
        and sorted(str(item) for item in replay_from) == sorted(REPLAY_SOURCES)
    ):
        reasons.append("replay sources must be distinct")
    shared_stores = migration.get("shared_stores")
    if not (
        isinstance(shared_stores, list)
        and len(shared_stores) == 2
        and sorted(str(item) for item in shared_stores) == sorted(SHARED_STORES)
    ):
        reasons.append("shared stores must be distinct")
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
        if isinstance(item, dict) and (item.get("owner") != "release owner" or item.get("status") != "unresolved"):
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
        try:
            document = _load_json(path)
            if kind == "packet":
                validate_packet(document)
                reasons = []
            else:
                reasons = benchmark_reasons(document) if kind == "benchmark" else candidate_reasons(document)
        except O8Error as exc:
            reasons = exc.reasons
        if not any(expected in reason for reason in reasons):
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
