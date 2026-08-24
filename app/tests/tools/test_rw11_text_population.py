"""Hermetic tests for the witnessed RW-11 prospective text-population collector."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = ROOT / "tools" / "rw11_text_population.py"
SPEC = importlib.util.spec_from_file_location("rw11_text_population_tool", TOOL_PATH)
rw11 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rw11
SPEC.loader.exec_module(rw11)

NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
PLAN_COMMIT = "a" * 40
COLLECTOR_COMMIT = "b" * 40
CIK = "0000000001"
CIK2 = "0000000002"
PRIOR_Q = "0000000001-26-000001"
CURRENT_8K = "0000000001-26-000002"
CURRENT_Q = "0000000001-26-000003"


def _write_json(path: Path, value: dict) -> bytes:
    payload = json.dumps(value, indent=2).encode("utf-8") + b"\n"
    path.write_bytes(payload)
    return payload


def _plan(tmp_path: Path, *, ciks=None, current_cap=60, response_cap=1_000_000) -> tuple[Path, dict, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "capture"
    value = {
        "schema_version": 1,
        "package": "RW-11",
        "scope": "prospective-text-delta-population-plan-only",
        "authority": "none",
        "rw11_gate": "OPEN_NOT_CLOSED",
        "gate_closed": False,
        "source_commit": PLAN_COMMIT,
        "authored_at": "2026-08-24T18:34:38+00:00",
        "selected_by": "Prospective RW-11 Population Coordinator",
        "cutoff_date": "2026-08-24",
        "observation_window": {
            "first_eligible_filing_date": "2026-08-25",
            "last_eligible_filing_date": "2026-11-24",
            "finalize_on_or_after": "2026-11-25T00:00:00+00:00",
        },
        "selection_rule": (
            "Census every eligible filing across the complete frozen universe, sort by filing "
            "date and canonical accession, retain only the earliest fixed-cap current filings, "
            "include every zero-delta case, bind periodic prior context, and never filter by "
            "issuer, text, delta count, numeric availability, materiality, parser success, "
            "anticipated reviewer label, or outcome; refuse an incomplete population."
        ),
        "universe_ciks": ciks or [CIK],
        "eligible_forms": ["8-K", "10-K", "10-Q"],
        "discovery_contract": {"rule": "strict complete SEC submissions census"},
        "case_construction": {"rule": "mechanical complete cases"},
        "resource_limits": {
            "maximum_current_filings": current_cap,
            "maximum_unique_filing_documents": 120,
            "maximum_sec_requests": 131,
            "maximum_response_bytes": response_cap,
            "maximum_total_bytes": 4_000_000,
        },
        "output_paths": {
            "capture_directory": str(output),
            "freeze_directory": str(tmp_path / "freeze"),
            "publication_rule": "isolated write-once publication",
        },
        "safety_preflight": {"rule": "read_only stopped system"},
        "evaluation_contract": {"rule": "blind independent labels"},
        "numeric_replay_trigger": {"automatic_authority": "none"},
        "known_blockers": ["RW-11 remains open"],
    }
    path = tmp_path / "plan.json"
    payload = _write_json(path, value)
    return path, value, rw11._sha256(payload)


def _witness(tmp_path: Path, plan: dict, plan_hash: str) -> tuple[Path, str]:
    value = {
        "schema_version": 1,
        "package": "RW-11",
        "scope": "prospective-text-delta-population-plan-chronology-witness",
        "authority": "none",
        "rw11_gate": "OPEN_NOT_CLOSED",
        "provider": "Discord API v10 exact-message chronology",
        "population_plan_sha256": plan_hash,
        "population_plan_package_manifest_sha256": "c" * 64,
        "source_commit": plan["source_commit"],
        "plan_authored_at": plan["authored_at"],
        "exact_message": rw11._witness_message(plan, plan_hash),
        "channel_id": "123456789012345678",
        "message_id": "223456789012345678",
        "bot_author_id": "323456789012345678",
        "server_timestamp": "2026-08-24T18:47:14+00:00",
        "verified_at": "2026-08-24T18:47:15+00:00",
        "post_count": 1,
        "exact_message_get_count": 1,
        "post_and_get_timestamps_match": True,
        "edited_timestamp": None,
        "webhook_id": None,
        "credential_values_recorded": False,
        "trust_boundary": "chronology only, no execution authority",
    }
    path = tmp_path / "witness.json"
    payload = _write_json(path, value)
    return path, rw11._sha256(payload)


def _preflight(tmp_path: Path, **changes) -> Path:
    value = {
        "schema_version": 1,
        "package": "RW-11",
        "scope": "prospective-text-delta-population-safety-preflight",
        "checked_at": (NOW - timedelta(minutes=1)).isoformat(),
        "source_commit": COLLECTOR_COMMIT,
        "configured_mode": "read_only",
        "effective_mode": "read_only",
        "persisted_mode": None,
        "expected_edge_available": False,
        "merger_execution_watchlist_count": 0,
        "merger_edge_rows": 0,
        "merger_selection_rows": 0,
        "proposal_rows": 0,
        "risk_decision_rows": 0,
        "approval_rows": 0,
        "order_rows": 0,
        "audit_chain_valid": True,
        "halt_absent": True,
        "sec_user_agent_configured": True,
        "cockpit_service_processes": 0,
        "api_port_listeners": 0,
        "worktree_clean": True,
        "main_equals_origin_main": True,
    }
    value.update(changes)
    path = tmp_path / "preflight.json"
    _write_json(path, value)
    return path


def _submissions(cik=CIK, *, rows=None) -> bytes:
    rows = rows or [
        (CURRENT_Q, "10-Q", "2026-11-01", "current-q.htm"),
        (CURRENT_8K, "8-K", "2026-09-01", "current-8k.htm"),
        (PRIOR_Q, "10-Q", "2026-08-01", "prior-q.htm"),
    ]
    return json.dumps({
        "cik": int(cik),
        "filings": {"recent": {
            "accessionNumber": [row[0] for row in rows],
            "form": [row[1] for row in rows],
            "filingDate": [row[2] for row in rows],
            "primaryDocument": [row[3] for row in rows],
        }},
    }, separators=(",", ":")).encode("utf-8")


class Fetcher:
    def __init__(self, payloads, *, fail_url=None):
        self.payloads = payloads
        self.fail_url = fail_url
        self.requests = []

    def get_bytes(self, url):
        self.requests.append(url)
        if url == self.fail_url:
            raise RuntimeError("fixture network stopped")
        return self.payloads[url]


def _payloads(rows=None):
    submissions = _submissions(rows=rows)
    payloads = {rw11.submissions_url(CIK): submissions}
    parsed = rw11._recent_rows(submissions, CIK, datetime(2026, 8, 24).date())
    for row in parsed:
        payloads[row.url] = f"raw:{row.accession}".encode()
    return payloads


def _collect(tmp_path: Path, *, rows=None, current_cap=60, response_cap=1_000_000):
    plan_path, plan, plan_hash = _plan(
        tmp_path, current_cap=current_cap, response_cap=response_cap
    )
    witness_path, witness_hash = _witness(tmp_path, plan, plan_hash)
    preflight = _preflight(tmp_path)
    fetcher = Fetcher(_payloads(rows))
    manifest = rw11.collect(
        plan_path,
        witness_path,
        preflight,
        Path(plan["output_paths"]["capture_directory"]),
        expected_plan_sha256=plan_hash,
        expected_witness_receipt_sha256=witness_hash,
        fetcher=fetcher,
        clock=lambda: NOW,
        source_state={
            "head": COLLECTOR_COMMIT,
            "origin_main": COLLECTOR_COMMIT,
            "worktree_clean": True,
        },
    )
    return Path(plan["output_paths"]["capture_directory"]), manifest, fetcher, plan_hash, witness_hash


def test_complete_capture_binds_witness_census_priors_and_exact_bytes(tmp_path):
    output, manifest, fetcher, plan_hash, witness_hash = _collect(tmp_path)

    assert manifest["authority"] == "none"
    assert manifest["rw11_gate"] == "OPEN_NOT_CLOSED"
    assert manifest["selected_current_filing_count"] == 2
    assert manifest["periodic_current_accessions"] == [CURRENT_Q]
    assert manifest["numeric_replay_trigger"] == "READY_FOR_SEPARATE_BOUNDED_PLAN"
    assert manifest["sec_request_count"] == 4
    assert len(fetcher.requests) == 4
    assert rw11.verify(
        output,
        expected_plan_sha256=plan_hash,
        expected_witness_receipt_sha256=witness_hash,
    )["ok"] is True
    cases = json.loads((output / rw11.CASES_NAME).read_text(encoding="utf-8"))["cases"]
    by_current = {case["current"]["accession_no"]: case for case in cases}
    assert by_current[CURRENT_8K]["prior"] is None
    assert by_current[CURRENT_Q]["prior"]["accession_no"] == PRIOR_Q
    assert (output / "raw" / rw11.ISOLATION_MARKER).read_text() == rw11.ISOLATION_MARKER_TEXT


def test_quiet_complete_census_is_preserved_without_claiming_precision(tmp_path):
    rows = [(PRIOR_Q, "10-Q", "2026-08-01", "prior-q.htm")]
    output, manifest, _fetcher, plan_hash, witness_hash = _collect(tmp_path, rows=rows)

    assert manifest["selected_current_filing_count"] == 0
    assert manifest["raw_documents"] == []
    assert manifest["numeric_replay_trigger"] == "AWAITING_SUPPORTED_POST_CUTOFF_PERIODIC_ACCESSION"
    assert json.loads((output / rw11.CASES_NAME).read_text())["cases"] == []
    assert rw11.verify(
        output,
        expected_plan_sha256=plan_hash,
        expected_witness_receipt_sha256=witness_hash,
    )["current_filings"] == 0


def test_fixed_cap_selects_earliest_current_filing_without_outcome_filter(tmp_path):
    output, manifest, _fetcher, _plan_hash, _witness_hash = _collect(tmp_path, current_cap=1)

    assert manifest["selected_current_filing_count"] == 1
    cases = json.loads((output / rw11.CASES_NAME).read_text())["cases"]
    assert cases[0]["current"]["accession_no"] == CURRENT_8K


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"authority": "paper"}, "scope or authority"),
        ({"edited_timestamp": "2026-08-24T18:48:00+00:00"}, "controls changed"),
        ({"post_count": 2}, "controls changed"),
        ({"server_timestamp": "2026-08-25T00:00:00+00:00"}, "chronology"),
    ],
)
def test_witness_tampering_refuses_before_network(tmp_path, change, message):
    plan_path, plan, plan_hash = _plan(tmp_path)
    witness_path, _witness_hash = _witness(tmp_path, plan, plan_hash)
    witness = json.loads(witness_path.read_text())
    witness.update(change)
    witness_bytes = _write_json(witness_path, witness)
    fetcher = Fetcher(_payloads())
    with pytest.raises(rw11.EvidenceError, match=message):
        rw11.collect(
            plan_path,
            witness_path,
            _preflight(tmp_path),
            Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash,
            expected_witness_receipt_sha256=rw11._sha256(witness_bytes),
            fetcher=fetcher,
            clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert fetcher.requests == []


@pytest.mark.parametrize(
    ("preflight_change", "message"),
    [
        ({"effective_mode": "paper"}, "not read_only"),
        ({"order_rows": 1}, "nonzero"),
        ({"audit_chain_valid": False}, "not satisfied"),
        ({"checked_at": (NOW - timedelta(minutes=16)).isoformat()}, "older than"),
    ],
)
def test_failed_safety_preflight_refuses_before_network(tmp_path, preflight_change, message):
    plan_path, plan, plan_hash = _plan(tmp_path)
    witness_path, witness_hash = _witness(tmp_path, plan, plan_hash)
    fetcher = Fetcher(_payloads())
    with pytest.raises(rw11.EvidenceError, match=message):
        rw11.collect(
            plan_path,
            witness_path,
            _preflight(tmp_path, **preflight_change),
            Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash,
            expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher,
            clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert fetcher.requests == []


def test_dirty_or_unmerged_source_refuses_before_network(tmp_path):
    plan_path, plan, plan_hash = _plan(tmp_path)
    witness_path, witness_hash = _witness(tmp_path, plan, plan_hash)
    fetcher = Fetcher(_payloads())
    with pytest.raises(rw11.EvidenceError, match="clean HEAD"):
        rw11.collect(
            plan_path,
            witness_path,
            _preflight(tmp_path),
            Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash,
            expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher,
            clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": "c" * 40, "worktree_clean": True},
        )
    assert fetcher.requests == []


@pytest.mark.parametrize("attack", ["wrong-cik", "unequal", "chronology", "coverage", "duplicate-key"])
def test_malformed_or_incomplete_submissions_never_publish(tmp_path, attack):
    plan_path, plan, plan_hash = _plan(tmp_path)
    witness_path, witness_hash = _witness(tmp_path, plan, plan_hash)
    preflight = _preflight(tmp_path)
    payload = _submissions()
    if attack == "wrong-cik":
        payload = _submissions(CIK2)
    elif attack == "unequal":
        document = json.loads(payload)
        document["filings"]["recent"]["form"].pop()
        payload = json.dumps(document).encode()
    elif attack == "chronology":
        payload = _submissions(rows=[
            (CURRENT_8K, "8-K", "2026-09-01", "current-8k.htm"),
            (CURRENT_Q, "10-Q", "2026-11-01", "current-q.htm"),
            (PRIOR_Q, "10-Q", "2026-08-01", "prior-q.htm"),
        ])
    elif attack == "coverage":
        payload = _submissions(rows=[(CURRENT_Q, "10-Q", "2026-11-01", "current-q.htm")])
    else:
        payload = b'{"cik":1,"cik":1,"filings":{"recent":{}}}'
    fetcher = Fetcher({rw11.submissions_url(CIK): payload})
    with pytest.raises(rw11.EvidenceError):
        rw11.collect(
            plan_path,
            witness_path,
            preflight,
            Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash,
            expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher,
            clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert not Path(plan["output_paths"]["capture_directory"]).exists()


def test_missing_periodic_prior_and_partial_document_fetch_never_publish(tmp_path):
    rows = [(CURRENT_Q, "10-Q", "2026-11-01", "current-q.htm"), (PRIOR_Q, "8-K", "2026-08-01", "old.htm")]
    plan_path, plan, plan_hash = _plan(tmp_path)
    witness_path, witness_hash = _witness(tmp_path, plan, plan_hash)
    fetcher = Fetcher(_payloads(rows))
    with pytest.raises(rw11.EvidenceError, match="no prior"):
        rw11.collect(
            plan_path, witness_path, _preflight(tmp_path), Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash, expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher, clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert not Path(plan["output_paths"]["capture_directory"]).exists()

    other = tmp_path / "partial"
    plan_path, plan, plan_hash = _plan(other)
    witness_path, witness_hash = _witness(other, plan, plan_hash)
    payloads = _payloads()
    failed_url = next(url for url in payloads if "/Archives/" in url)
    fetcher = Fetcher(payloads, fail_url=failed_url)
    with pytest.raises(RuntimeError, match="network stopped"):
        rw11.collect(
            plan_path, witness_path, _preflight(other), Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash, expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher, clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert not Path(plan["output_paths"]["capture_directory"]).exists()


def test_byte_cap_and_existing_output_refuse_without_partial_publish(tmp_path):
    plan_path, plan, plan_hash = _plan(tmp_path, response_cap=5)
    witness_path, witness_hash = _witness(tmp_path, plan, plan_hash)
    fetcher = Fetcher(_payloads())
    with pytest.raises(rw11.EvidenceError, match="response byte cap"):
        rw11.collect(
            plan_path, witness_path, _preflight(tmp_path), Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash, expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher, clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert not Path(plan["output_paths"]["capture_directory"]).exists()

    other = tmp_path / "exists"
    plan_path, plan, plan_hash = _plan(other)
    witness_path, witness_hash = _witness(other, plan, plan_hash)
    Path(plan["output_paths"]["capture_directory"]).mkdir(parents=True)
    fetcher = Fetcher(_payloads())
    with pytest.raises(rw11.EvidenceError, match="already exists"):
        rw11.collect(
            plan_path, witness_path, _preflight(other), Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash, expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher, clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert fetcher.requests == []


def test_raw_tamper_and_extra_artifact_fail_verification(tmp_path):
    output, manifest, _fetcher, plan_hash, witness_hash = _collect(tmp_path)
    accession = manifest["raw_documents"][0]["accession_no"]
    (output / "raw" / accession / "raw.bin").write_bytes(b"tampered")
    with pytest.raises(rw11.EvidenceError, match="raw filing integrity"):
        rw11.verify(output, expected_plan_sha256=plan_hash, expected_witness_receipt_sha256=witness_hash)

    other = tmp_path / "extra"
    output, _manifest, _fetcher, plan_hash, witness_hash = _collect(other)
    (output / "unexpected.txt").write_text("extra")
    with pytest.raises(rw11.EvidenceError, match="artifact set changed"):
        rw11.verify(output, expected_plan_sha256=plan_hash, expected_witness_receipt_sha256=witness_hash)


def test_future_window_and_changed_plan_hash_refuse_before_network(tmp_path):
    plan_path, plan, plan_hash = _plan(tmp_path)
    witness_path, witness_hash = _witness(tmp_path, plan, plan_hash)
    fetcher = Fetcher(_payloads())
    with pytest.raises(rw11.EvidenceError, match="finalize time"):
        rw11.collect(
            plan_path, witness_path, _preflight(tmp_path), Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256=plan_hash, expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher, clock=lambda: datetime(2026, 11, 24, 23, 59, tzinfo=UTC),
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert fetcher.requests == []
    with pytest.raises(rw11.EvidenceError, match="witnessed hash"):
        rw11.collect(
            plan_path, witness_path, _preflight(tmp_path), Path(plan["output_paths"]["capture_directory"]),
            expected_plan_sha256="d" * 64, expected_witness_receipt_sha256=witness_hash,
            fetcher=fetcher, clock=lambda: NOW,
            source_state={"head": COLLECTOR_COMMIT, "origin_main": COLLECTOR_COMMIT, "worktree_clean": True},
        )
    assert fetcher.requests == []


def test_live_fetcher_uses_plan_caps_zero_retries_and_requires_user_agent(tmp_path, monkeypatch):
    _path, plan, _hash = _plan(tmp_path)
    monkeypatch.delenv("COCKPIT_SEC_USER_AGENT", raising=False)
    with pytest.raises(rw11.EvidenceError, match="required"):
        rw11._live_fetcher(plan, 2.0)

    captured = {}

    class FakeSecFetcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("COCKPIT_SEC_USER_AGENT", "configured-but-never-printed")
    monkeypatch.setattr(rw11, "SecFetcher", FakeSecFetcher)
    rw11._live_fetcher(plan, 2.0)
    assert captured["max_retries"] == 0
    assert captured["max_response_bytes"] == plan["resource_limits"]["maximum_response_bytes"]
    assert captured["max_total_bytes"] == plan["resource_limits"]["maximum_total_bytes"]


def test_tool_has_no_database_runtime_risk_broker_or_order_imports():
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = ("storage", "orchestrator", "brokers", "risk", "sqlalchemy", "config", "main")
    assert not any(module.startswith(forbidden) for module in imports)
