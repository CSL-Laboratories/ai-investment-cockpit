"""Provider-free checks for the CSL-220 O8 phase A contract artifacts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = "tools/check_o8_phase_a.py"

_SPEC = importlib.util.spec_from_file_location("check_o8_phase_a", ROOT / VALIDATOR)
assert _SPEC and _SPEC.loader
CHECK_O8 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CHECK_O8)
EXPECTED_NEGATIVE_LINES = (
    "O8 negative control failed as designed: omit-io: omits I/O",
    "O8 negative control failed as designed: omit-helpers: omits helper processes",
    "O8 negative control failed as designed: omit-variance: omits variance",
    "O8 negative control failed as designed: provider-latency-unlabeled: omits provider latency",
    "O8 negative control failed as designed: candidate-missing-floors: candidate missing floors",
    "O8 negative control failed as designed: floor-enforced-by-benchmark: candidate missing floors",
    "O8 negative control failed as designed: seam-missing-rollback: migration cannot roll back",
    "O8 negative control failed as designed: candidate-missing-smallest-experiment: candidate missing smallest experiment",
    "O8 negative control failed as designed: missing-measured-observation: tier-3 evidence lacks measured observation",
    "O8 negative control failed as designed: impossible-observation: impossible measured observation",
    "O8 negative control failed as designed: baseline-unbound-outputs: tier-3 baseline lacks retained outputs",
    "O8 negative control failed as designed: attached-floors-invalid: attached floors invalid",
    "O8 negative control failed as designed: experiment-seam-mismatch: candidate experiment does not exercise its seam",
    "O8 negative control failed as designed: non-finite-literal: unreadable JSON",
    "O8 negative control failed as designed: overflow-literal: unreadable JSON",
    "O8 negative control failed as designed: baseline-outputs-absolute: baseline outputs path must be relative",
    "O8 negative control failed as designed: baseline-outputs-traversal: baseline outputs path escapes the repository",
    "O8 negative control failed as designed: baseline-metadata-only: tier-3 baseline lacks retained outputs",
    "O8 negative control failed as designed: baseline-observation-untyped: tier-3 baseline observation is not measured",
    "O8 negative control failed as designed: helper-set-mismatch: helper set does not match the workload",
    "O8 negative control failed as designed: replay-from-duplicate: replay sources must be distinct",
    "O8 negative control failed as designed: shared-stores-duplicate: shared stores must be distinct",
    "O8 negative control failed as designed: baseline-outputs-backslash-root: baseline outputs path must be relative",
    "O8 negative control failed as designed: baseline-outputs-unc-slash: baseline outputs path must be relative",
    "O8 negative control failed as designed: baseline-outputs-drive-abs: baseline outputs path must be relative",
    "O8 negative control failed as designed: baseline-outputs-drive-abs-slash: baseline outputs path must be relative",
    "O8 negative control failed as designed: baseline-outputs-drive-rel: baseline outputs path must be relative",
    "O8 negative control failed as designed: baseline-outputs-mid-backslash: baseline outputs path must be relative",
    "O8 negative control failed as designed: helper-duplicate: helper set does not match the workload",
    "O8 negative control failed as designed: tokens-negative: tier-3 evidence lacks measured observation",
    "O8 negative control failed as designed: cost-negative: tier-3 evidence lacks measured observation",
    "O8 negative control failed as designed: tokens-huge-int: tier-3 evidence lacks measured observation",
    "O8 negative control failed as designed: fixture-sha-unbound: fixture hash does not match retained fixture",
    "O8 negative control failed as designed: units-missing: observation units missing or invalid",
    "O8 negative control failed as designed: units-mismatched: observation units missing or invalid",
    "O8 negative control failed as designed: baseline-units-mismatched: tier-3 baseline lacks retained outputs",
    "O8 negative control failed as designed: io-kind-memory-only: I/O kinds omit required disk I/O for this workload",
    "O8 negative control failed as designed: candidate-id-unbound: candidate does not match retained candidate manifest",
    "O8 negative control failed as designed: candidate-commit-mismatch: candidate does not match retained candidate manifest",
    "O8 negative control failed as designed: candidate-config-mismatch: candidate does not match retained candidate manifest",
    "O8 negative control failed as designed: baseline-host-mismatch: tier-3 baseline host does not match the candidate",
    "O8 negative control failed as designed: baseline-runner-mismatch: tier-3 baseline host does not match the candidate",
    "O8 negative control failed as designed: baseline-config-mismatch: tier-3 baseline host does not match the candidate",
    "O8 negative control failed as designed: baseline-python-coarse: tier-3 baseline host does not match the candidate",
    "O8 negative control failed as designed: floor-results-missing: tier-3 floor results are not bound to the candidate",
    "O8 negative control failed as designed: floor-results-wrong-commit: tier-3 floor results are not bound to the candidate",
    "O8 negative control failed as designed: floor-results-failed: tier-3 floor results are not bound to the candidate",
    "O8 negative control failed as designed: blank-outcome-correctness: measured observation narrative missing or blank: correctness",
    "O8 negative control failed as designed: blank-outcome-failure: measured observation narrative missing or blank: failure",
    "O8 negative control failed as designed: blank-outcome-recovery: measured observation narrative missing or blank: recovery",
    "O8 negative control failed as designed: blank-outcome-growth: measured observation narrative missing or blank: growth",
    "O8 negative controls: 51 failed as designed",
)
# No automatic hung-orchestrator detector selector is registered, so no receipt qualifies.
HUNG_QUALIFICATION_REASON = (
    "tier-3 floor qualification incomplete: missing hung_orchestrator_detection selector "
    "coverage in invariant_7_fail_closed and fail_closed"
)
NARRATIVES = ("correctness", "failure", "recovery", "growth")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, "-X", "utf8", "-B", VALIDATOR, *args),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_o8_phase_a_fixture_stays_researched_and_negative_controls_fail(capsys) -> None:
    positive = _run()
    assert positive.returncode == 0, positive.stderr
    assert "maturity=researched" in positive.stdout
    assert "completion_gate_status=pending_outcome_artifact" in positive.stdout
    assert "per_pr_benchmark_gating=false" in positive.stdout
    assert "new_ci_cadence=false" in positive.stdout
    assert "PASS" not in positive.stdout
    assert "contract_frozen" not in positive.stdout
    assert "qualification" not in positive.stdout

    negative = _run("--negative-controls")
    assert negative.returncode == 0, negative.stderr
    for line in EXPECTED_NEGATIVE_LINES:
        assert line in negative.stdout
    # Pytest captures the subprocess. Write the designed failures to the CI log.
    with capsys.disabled():
        sys.stdout.write(negative.stdout)
        if not negative.stdout.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()


def test_o8_phase_a_rejects_a_promoted_maturity(tmp_path: Path) -> None:
    packet = json.loads((ROOT / "docs/replacement/outcomes/O8/fixtures/phase-a-packet.json").read_text(encoding="utf-8"))
    packet["maturity"] = "contract_frozen"
    candidate = tmp_path / "packet.json"
    candidate.write_text(json.dumps(packet), encoding="utf-8")
    result = _run("--manifest", str(candidate), "--kind", "packet")
    assert result.returncode == 1
    assert "maturity is researched only" in result.stderr
    assert "fixture accepted" not in result.stdout


def test_o8_contract_doc_keeps_researched_maturity_and_owner_role_decisions() -> None:
    text = (ROOT / "docs/replacement/outcomes/O8/PHASE-A.md").read_text(encoding="utf-8")
    assert "Maturity claimed: `researched`." in text
    assert "## Unresolved decisions (owner: release owner)" in text
    assert "## Research (Parallel)" in text
    for url in (
        "https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig",
        "https://pyperf.readthedocs.io/en/stable/run_benchmark.html",
        "https://pyperf.readthedocs.io/en/latest/analyze.html",
        "https://github.com/psf/pyperf",
        "https://enersys.co.th/en/insights/strangler-fig-migration-python-to-rust-2026",
        "https://martinfowler.com/bliki/ParallelChange.html",
    ):
        assert url in text


def test_o8_validator_does_not_import_network_or_credentials() -> None:
    source = (ROOT / "tools" / "check_o8_phase_a.py").read_text(encoding="utf-8")
    imported = "\n".join(
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    )
    for banned in ("keyring", "httpx", "urllib", "socket", "discord", "alpaca", "boto3"):
        assert banned not in imported


def _packet_with(tmp_path: Path, mutate) -> subprocess.CompletedProcess[str]:
    packet = json.loads((ROOT / "docs/replacement/outcomes/O8/fixtures/phase-a-packet.json").read_text(encoding="utf-8"))
    mutate(packet)
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return _run("--manifest", str(path), "--kind", "packet")


def test_o8_rejects_experiment_that_does_not_exercise_the_seam(tmp_path: Path) -> None:
    def mutate(packet: dict) -> None:
        packet["candidate"]["smallest_experiment"]["workload_id"] = "ingest_to_raw_store"
        packet["candidate"]["seam"] = "paper_order_lifecycle"

    result = _packet_with(tmp_path, mutate)
    assert result.returncode == 1
    assert "candidate experiment does not exercise its seam" in result.stderr


def test_o8_rejects_numeric_latency_on_provider_free_workload(tmp_path: Path) -> None:
    def mutate(packet: dict) -> None:
        packet["workloads"][0]["provider_latency"] = 42.0

    result = _packet_with(tmp_path, mutate)
    assert result.returncode == 1
    assert "omits provider latency" in result.stderr


def test_resolve_local_path_rejects_nul_byte_without_raising() -> None:
    assert CHECK_O8._resolve_local_path("docs/replacement\x00/outcomes") is None
    assert CHECK_O8._resolve_local_path("\x00") is None


def test_resolve_local_path_rejects_nul_byte_via_local_path_exists() -> None:
    assert CHECK_O8._local_path_exists("docs/replacement\x00/outcomes/O8/PHASE-A.md") is False


def test_resolve_local_path_survives_resolve_raising_oserror(monkeypatch) -> None:
    def _boom(self):
        raise OSError("simulated resolve failure")

    monkeypatch.setattr(CHECK_O8.Path, "resolve", _boom)
    assert CHECK_O8._resolve_local_path("docs/replacement/outcomes/O8/PHASE-A.md") is None


def test_resolve_local_path_survives_resolve_raising_valueerror(monkeypatch) -> None:
    def _boom(self):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(CHECK_O8.Path, "resolve", _boom)
    assert CHECK_O8._resolve_local_path("docs/replacement/outcomes/O8/PHASE-A.md") is None


def test_o8_rejects_resolve_runtimeerror_without_crashing(monkeypatch) -> None:
    packet = CHECK_O8._load_json(CHECK_O8.PACKET_PATH)

    def _boom(self):
        raise RuntimeError("simulated symlink loop")

    monkeypatch.setattr(CHECK_O8.Path, "resolve", _boom)
    assert CHECK_O8._resolve_local_path("docs/replacement/outcomes/O8/PHASE-A.md") is None
    assert CHECK_O8._local_path_exists("docs/replacement/outcomes/O8/PHASE-A.md") is False
    with pytest.raises(CHECK_O8.O8Error, match="missing disposition path"):
        CHECK_O8.validate_packet(packet)


def test_safe_exists_survives_exists_raising_oserror(monkeypatch) -> None:
    def _boom(self):
        raise OSError("simulated exists failure")

    monkeypatch.setattr(CHECK_O8.Path, "exists", _boom)
    assert CHECK_O8._safe_exists(ROOT / "docs/replacement/outcomes/O8/PHASE-A.md") is False


def test_safe_is_file_survives_is_file_raising_valueerror(monkeypatch) -> None:
    def _boom(self):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(CHECK_O8.Path, "is_file", _boom)
    assert CHECK_O8._safe_is_file(ROOT / "docs/replacement/outcomes/O8/PHASE-A.md") is False


@pytest.mark.parametrize(("method", "helper"), [
    ("exists", "_safe_exists"), ("is_file", "_safe_is_file"),
])
def test_safe_path_checks_survive_runtimeerror(monkeypatch, method: str, helper: str) -> None:
    def _boom(self):
        raise RuntimeError("simulated symlink loop")

    monkeypatch.setattr(CHECK_O8.Path, method, _boom)
    assert getattr(CHECK_O8, helper)(ROOT / "docs/replacement/outcomes/O8/PHASE-A.md") is False


@pytest.mark.parametrize("scheme", ["https", "http"])
def test_o8_rejects_url_locator_with_nul_byte_without_crashing(tmp_path: Path, scheme: str) -> None:
    locator = f"{scheme}://x\x00y"
    assert CHECK_O8._local_path_exists(locator) is False

    def mutate(packet: dict) -> None:
        packet["disposition"][0]["paths"] = [locator]

    result = _packet_with(tmp_path, mutate)
    assert result.returncode == 1
    assert "missing disposition path" in result.stderr
    assert "Traceback" not in result.stderr


def test_o8_rejects_disposition_path_with_nul_byte_without_crashing(tmp_path: Path) -> None:
    def mutate(packet: dict) -> None:
        packet["disposition"][0]["paths"] = ["app/risk/\x00evil"]

    result = _packet_with(tmp_path, mutate)
    assert result.returncode == 1
    assert "missing disposition path" in result.stderr
    assert "Traceback" not in result.stderr


def _synthetic_comparison() -> dict:
    return CHECK_O8._load_json(CHECK_O8.O8 / "fixtures/validator-only/benchmark.json")


def test_o8_synthetic_comparison_binds_structurally_but_cannot_qualify() -> None:
    # These observations are invented controls. Structural binding is not qualification:
    # the only remaining reason is the missing hung-orchestrator selector coverage.
    document = _synthetic_comparison()
    assert CHECK_O8._floor_results_bound(document)
    assert CHECK_O8.floor_qualification_gaps(document) == ["hung_orchestrator_detection"]
    assert CHECK_O8.benchmark_reasons(document) == [HUNG_QUALIFICATION_REASON]


def test_o8_cli_rejects_the_synthetic_comparison_for_missing_qualification() -> None:
    result = _run(
        "--manifest", "docs/replacement/outcomes/O8/fixtures/validator-only/benchmark.json",
        "--kind", "benchmark",
    )
    assert result.returncode == 1
    assert HUNG_QUALIFICATION_REASON in result.stderr
    assert "fixture accepted" not in result.stdout


def test_o8_qualification_gap_ignores_caller_claims() -> None:
    document = _synthetic_comparison()
    document["floor_qualification"] = "pass"
    document["hung_orchestrator_detection"] = True
    reasons = CHECK_O8.benchmark_reasons(document)
    assert HUNG_QUALIFICATION_REASON in reasons
    assert any(reason.startswith("schema ") for reason in reasons)


def test_o8_tier2_measurement_without_csl42_claim_stays_structurally_usable() -> None:
    document = _synthetic_comparison()
    document["tier"] = 2
    document["csl42_evidence"] = False
    assert CHECK_O8.benchmark_reasons(document) == []
    document["observation"]["growth"] = "  "
    assert CHECK_O8.benchmark_reasons(document) == [
        "measured observation narrative missing or blank: growth"
    ]


_MISSING = object()


@pytest.mark.parametrize("key", NARRATIVES)
@pytest.mark.parametrize("value", [_MISSING, None, "", " ", "\t\n", " "], ids=[
    "missing", "null", "empty", "space", "tab-newline", "nbsp",
])
def test_o8_measured_observation_rejects_blank_narratives(key: str, value) -> None:
    document = _synthetic_comparison()
    if value is _MISSING:
        del document["observation"][key]
    else:
        document["observation"][key] = value
    reasons = CHECK_O8.benchmark_reasons(document)
    semantic = [reason for reason in reasons if not reason.startswith("schema ")]
    assert semantic == [
        HUNG_QUALIFICATION_REASON,
        f"measured observation narrative missing or blank: {key}",
    ]
    # Schema checks stay intact: only whitespace-only strings pass the schema's minLength.
    schema_rejects = value is _MISSING or value is None or value == ""
    assert any(reason.startswith("schema ") for reason in reasons) is schema_rejects


@pytest.mark.parametrize("key", NARRATIVES)
@pytest.mark.parametrize("value", ["x", "  padded narrative  ", "\trecorded outcome\n"])
def test_o8_measured_observation_accepts_nonblank_narratives(key: str, value: str) -> None:
    document = _synthetic_comparison()
    document["observation"][key] = value
    assert CHECK_O8.benchmark_reasons(document) == [HUNG_QUALIFICATION_REASON]


def test_o8_candidate_configuration_distinguishes_boolean_from_integer() -> None:
    document = _synthetic_comparison()
    document["configuration"]["batch_size"] = True
    assert "candidate does not match retained candidate manifest" in CHECK_O8.benchmark_reasons(document)


@pytest.mark.parametrize(("python_runtime", "expected_valid"), [
    ("3.11.9", True),
    ("3.12.3", True),
    ("3.13.0rc1", True),
    ("3.12.3 (64bit)", True),
    ("3", False),
    ("3.12", False),
    ("cpython", False),
    ("3.12.3\n", False),
    ("3.12.3 \n", False),
    ("", False),
])
def test_o8_checker_python_runtime_pattern(python_runtime: str, expected_valid: bool) -> None:
    identity = CHECK_O8._execution_identity(
        {"runner": "test-runner", "python": python_runtime},
        {"batch_size": 1},
    )
    assert (identity is not None) is expected_valid


@pytest.mark.parametrize(("python_runtime", "expected_valid"), [
    ("3.11.9", True),
    ("3.12.3", True),
    ("3.13.0rc1", True),
    ("3.12.3 (64bit)", True),
    ("3", False),
    ("3.12", False),
    ("cpython", False),
    ("3.12.3\n", False),
    ("3.12.3 \n", False),
    ("", False),
])
def test_o8_benchmark_schema_python_runtime_pattern(python_runtime: str, expected_valid: bool) -> None:
    schema = json.loads(
        (ROOT / "docs/replacement/outcomes/O8/schemas/benchmark-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    python_schema = schema["properties"]["host"]["properties"]["python"]
    assert python_schema["pattern"] == CHECK_O8.PYTHON_RUNTIME_PATTERN
    validator = jsonschema.Draft202012Validator(python_schema)
    assert validator.is_valid(python_runtime) is expected_valid


def test_o8_runner_negative_differs_from_candidate_only_in_runner() -> None:
    candidate = CHECK_O8._load_json(CHECK_O8.NEGATIVE_DIR / "baseline-runner-mismatch.json")
    retained_path = ROOT / candidate["baseline"]["outputs"]["path"]
    retained = CHECK_O8._load_json(retained_path)

    assert retained["host"]["python"] == candidate["host"]["python"]
    assert retained["host"]["runner"] != candidate["host"]["runner"]
    assert retained["configuration"] == candidate["configuration"]


def test_o8_candidate_cannot_be_reused_for_a_different_workload_seam() -> None:
    document = _synthetic_comparison()
    document["workload_id"] = "point_in_time_replay"
    assert CHECK_O8._retained_candidate_bound(document) is False


@pytest.mark.parametrize(("name", "expected"), [
    ("tokens-huge-int", "tier-3 evidence lacks measured observation"),
    ("fixture-sha-unbound", "fixture hash does not match retained fixture"),
    ("units-missing", "observation units missing or invalid"),
    ("units-mismatched", "observation units missing or invalid"),
    ("baseline-units-mismatched", "tier-3 baseline lacks retained outputs"),
    ("baseline-observation-untyped", "tier-3 baseline observation is not measured"),
    ("io-kind-memory-only", "I/O kinds omit required disk I/O for this workload"),
    ("candidate-id-unbound", "candidate does not match retained candidate manifest"),
    ("candidate-commit-mismatch", "candidate does not match retained candidate manifest"),
    ("candidate-config-mismatch", "candidate does not match retained candidate manifest"),
    ("baseline-host-mismatch", "tier-3 baseline host does not match the candidate"),
    ("baseline-runner-mismatch", "tier-3 baseline host does not match the candidate"),
    ("baseline-config-mismatch", "tier-3 baseline host does not match the candidate"),
    ("baseline-python-coarse", "tier-3 baseline host does not match the candidate"),
    ("floor-results-missing", "tier-3 floor results are not bound to the candidate"),
    ("floor-results-wrong-commit", "tier-3 floor results are not bound to the candidate"),
    ("floor-results-failed", "tier-3 floor results are not bound to the candidate"),
    ("blank-outcome-correctness", "measured observation narrative missing or blank: correctness"),
    ("blank-outcome-failure", "measured observation narrative missing or blank: failure"),
    ("blank-outcome-recovery", "measured observation narrative missing or blank: recovery"),
    ("blank-outcome-growth", "measured observation narrative missing or blank: growth"),
])
def test_o8_review_negative_controls_isolate_the_intended_failure(name: str, expected: str) -> None:
    document = CHECK_O8._load_json(CHECK_O8.NEGATIVE_DIR / f"{name}.json")
    reasons = CHECK_O8.benchmark_reasons(document)
    semantic = [reason for reason in reasons if not reason.startswith("schema ")]
    # Every CSL-42 claim also carries the explicit qualification gap, and only CSL-42
    # claims do. It is filtered by exact text, so it cannot hide the intended failure.
    assert (HUNG_QUALIFICATION_REASON in semantic) is (document["csl42_evidence"] is True)
    # An unrelated semantic failure must not mask a broken control (especially huge tokens).
    assert [reason for reason in semantic if reason != HUNG_QUALIFICATION_REASON] == [expected]


@pytest.mark.parametrize("name", [
    "blank-outcome-correctness", "blank-outcome-failure", "blank-outcome-recovery", "blank-outcome-growth",
])
def test_o8_blank_outcome_controls_differ_from_the_synthetic_comparison_in_one_narrative(name: str) -> None:
    control = CHECK_O8._load_json(CHECK_O8.NEGATIVE_DIR / f"{name}.json")
    original = _synthetic_comparison()
    key = name.removeprefix("blank-outcome-")
    assert isinstance(control["observation"][key], str)
    assert control["observation"][key].strip() == ""
    control["observation"][key] = original["observation"][key]
    assert control == original


@pytest.mark.parametrize("workload_id", [
    "ingest_to_raw_store", "point_in_time_replay", "frozen_evaluation_run",
])
def test_o8_disk_workloads_reject_memory_only_io(workload_id: str) -> None:
    packet = CHECK_O8._load_json(CHECK_O8.PACKET_PATH)
    workload = next(item for item in packet["workloads"] if item["workload_id"] == workload_id)
    assert CHECK_O8.benchmark_reasons(workload) == []
    workload["io"]["kinds"] = ["memory_copy"]
    assert "I/O kinds omit required disk I/O for this workload" in CHECK_O8.benchmark_reasons(workload)
    workload["io"]["kinds"] = ["memory_copy", "disk"]
    assert CHECK_O8.benchmark_reasons(workload) == []


@pytest.mark.parametrize("metric", [
    "p50", "p95", "p99", "throughput", "rss_bytes", "cpu", "io", "tokens", "cost", "relative_iqr",
])
def test_o8_rejects_huge_integers_in_every_measured_metric(metric: str) -> None:
    document = _synthetic_comparison()
    container = document["variance"] if metric == "relative_iqr" else document["observation"]
    container[metric] = 10 ** 399
    reasons = CHECK_O8.benchmark_reasons(document)
    assert "tier-3 evidence lacks measured observation" in reasons
    assert "tier-3 baseline lacks retained outputs" not in reasons


@pytest.mark.parametrize("field", ["candidate", "fixture"])
@pytest.mark.parametrize("path", [
    "", "/outside.json", "../outside.json", "C:/outside.json", "C:outside.json",
    "\\\\server\\share\\outside.json", "https://example.test/outside.json", "nul\x00.json",
    "docs/replacement/outcomes/O8/fixtures/validator-only/missing.json",
])
def test_o8_evidence_references_fail_closed(field: str, path: str) -> None:
    document = _synthetic_comparison()
    document[field]["path"] = path
    expected = (
        "candidate does not match retained candidate manifest" if field == "candidate"
        else "fixture hash does not match retained fixture"
    )
    assert expected in CHECK_O8.benchmark_reasons(document)


@pytest.mark.parametrize("field", ["candidate", "fixture"])
def test_o8_evidence_rejects_missing_reference_or_wrong_digest(field: str) -> None:
    document = _synthetic_comparison()
    document[field]["sha256"] = "0" * 64
    expected = (
        "candidate does not match retained candidate manifest" if field == "candidate"
        else "fixture hash does not match retained fixture"
    )
    assert expected in CHECK_O8.benchmark_reasons(document)
    del document[field]
    assert expected in CHECK_O8.benchmark_reasons(document)


def test_o8_evidence_rejects_unreadable_files(monkeypatch) -> None:
    def unreadable(*_args, **_kwargs):
        raise OSError("simulated read failure")

    document = _synthetic_comparison()
    monkeypatch.setattr(CHECK_O8.os, "open", unreadable)
    assert CHECK_O8._retained_bytes(document["fixture"]) is None
    assert CHECK_O8._retained_candidate_bound(document) is False


def test_o8_evidence_rejects_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = repository / "input.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    monkeypatch.setattr(CHECK_O8, "ROOT", repository)
    assert CHECK_O8._resolve_local_path("input.json") is None
    assert CHECK_O8._read_regular_file(link) is None
    assert outside.read_bytes() not in (CHECK_O8._read_regular_file(link) or b"")


def test_o8_retained_read_rejects_a_symlink_swap(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    inside = repository / "inside.json"
    inside.write_bytes(b'{"measured": true}\n')
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"secret": true}\n')
    monkeypatch.setattr(CHECK_O8, "ROOT", repository)
    probe = repository / "probe"
    try:
        probe.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    probe.unlink()
    real_lstat = CHECK_O8.os.lstat

    def swap_after_lstat(path):
        observed = real_lstat(path)
        if Path(path) == inside:
            inside.unlink()
            inside.symlink_to(outside)
        return observed

    monkeypatch.setattr(CHECK_O8.os, "lstat", swap_after_lstat)
    assert CHECK_O8._read_regular_file(inside) is None
