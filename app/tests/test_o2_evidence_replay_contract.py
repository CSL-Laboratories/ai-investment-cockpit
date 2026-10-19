"""CSL-214 candidate O2 contract. Provider-free. No network, credentials, or live venue.

Exit 0 of the candidate command is not an empirical PASS. The warnings below are the
designed negative-control failures and must appear in CI output.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import warnings
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
O2 = ROOT / "docs" / "replacement" / "outcomes" / "O2"
VALIDATOR = O2 / "validate_o2.py"


class O2NegativeControlWarning(UserWarning):
    """A frozen negative control failed as designed."""


def _load_validator():
    spec = importlib.util.spec_from_file_location("o2_validate", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_o2_candidate_replays_and_negative_controls_fail_as_designed() -> None:
    module = _load_validator()
    lines = module.validate_candidate(O2)
    assert lines[0] == "O2 candidate corpus: ok"
    assert lines[1] == "O2 maturity: researched"
    failures = [line for line in lines if line.startswith("O2 NEGATIVE CONTROL FAIL AS DESIGNED:")]
    assert failures == [
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: future_data_leak codes=future_data_leak",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: raw_not_reconstructable codes=raw_not_reconstructable",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: lost_resolution_semantics codes=lost_resolution_semantics",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: lost_corporate_action_semantics codes=lost_corporate_action_semantics",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: no_evidence codes=no_evidence",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: dedup_collapses_correction codes=dedup_collapses_correction",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: stock_treated_as_event codes=stock_treated_as_event",
    ]
    for line in failures:
        warnings.warn(line, O2NegativeControlWarning, stacklevel=1)

    completed = subprocess.run(
        (sys.executable, "-X", "utf8", "-B", str(VALIDATOR)),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for line in failures:
        assert line in completed.stdout
    assert "contract_frozen" in completed.stdout
    assert "Not claimed" in completed.stdout


def test_swapped_event_identity_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    observations = json.loads((copy / "fixtures" / "observations.json").read_text(encoding="utf-8"))
    swapped = next(item for item in observations["observations"] if item["observation_id"] == "obs-ev-void")
    swapped["identity_id"] = "idn-ev-b1"
    (copy / "fixtures" / "observations.json").write_text(
        json.dumps(observations, indent=2) + "\n", encoding="utf-8"
    )
    manifest = json.loads((copy / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {
        rel: hashlib.sha256((copy / rel).read_bytes()).hexdigest() for rel in module.EXPECTED_FILES
    }
    (copy / "fixtures" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(module.ContractError, match="identity"):
        module.validate_candidate(copy)


def test_same_key_independent_splits_are_both_required_and_rejected() -> None:
    module = _load_validator()
    documents = {rel: json.loads((O2 / rel).read_text(encoding="utf-8")) for rel in module.SCHEMA_FOR}
    bundle = module.Bundle(documents)
    extra = json.loads(json.dumps(bundle.records["raw-eq-split"]))
    extra["raw_record_id"] = "raw-eq-split-other"
    extra["source_record_key"] = "split-other"
    extra["event_time"] = "2024-09-01T15:00:00Z"
    extra["accepted_at"] = "2024-09-01T15:00:00Z"
    extra["supersedes"] = None
    extra["payload"] = dict(extra["payload"])
    extra["payload"]["ratio_numerator"] = 4
    extra["payload"]["ex_date"] = "2024-09-03"
    extra["raw_sha256"] = module.payload_sha256(extra["payload"])
    bundle.records[extra["raw_record_id"]] = extra
    decision = module.parse_time("2024-09-02T00:00:00Z")
    required = {(item[1], item[4]) for item in module.required_corporate_actions(bundle, "idn-eq-v2", decision)}
    assert required == {(3, "raw-eq-split-corr"), (4, "raw-eq-split-other")}
    observation = {
        "market_family": "equity",
        "identity_id": "idn-eq-v2",
        "natural_key": "us-equity:listing:0000000001:common:corporate_action",
        "corporate_actions": [
            {
                "action_type": "split",
                "ratio_numerator": 4,
                "ratio_denominator": 1,
                "ex_date": "2024-09-03",
                "raw_record_id": "raw-eq-split-other",
            }
        ],
    }
    assert module.semantic_codes(bundle, observation, decision) == ["lost_corporate_action_semantics"]
    with pytest.raises(module.ContractError, match="independent actions"):
        module.validate_records(bundle)


def test_listing_change_drops_the_revoked_split() -> None:
    module = _load_validator()
    documents = {rel: json.loads((O2 / rel).read_text(encoding="utf-8")) for rel in module.SCHEMA_FOR}
    bundle = module.Bundle(documents)
    bundle.identities["idn-eq-other"] = {
        "identity_id": "idn-eq-other",
        "market_family": "equity",
        "identity_version": 1,
        "equity": {"listing_key": "us-equity:cik:0000000002:class:common"},
    }
    correction = bundle.records["raw-eq-split-corr"]
    correction["payload"] = dict(correction["payload"])
    correction["payload"]["listing_identity_before"] = "idn-eq-other"
    correction["payload"]["listing_identity_after"] = "idn-eq-other"
    correction["raw_sha256"] = module.payload_sha256(correction["payload"])
    decision = module.parse_time("2024-08-11T00:00:00Z")
    assert module.required_corporate_actions(bundle, "idn-eq-v1", decision) == set()
    assert module.required_corporate_actions(bundle, "idn-eq-other", decision) == set()
    observation = {
        "market_family": "equity",
        "identity_id": "idn-eq-v1",
        "natural_key": "sec:fact:0000000001:revenue:fy2023",
        "corporate_actions": [],
    }
    assert module.semantic_codes(bundle, observation, decision) == []
    new_obs = {"market_family": "equity", "identity_id": "idn-eq-other"}
    assert not module._input_identity_ok(bundle, new_obs, bundle.records["raw-eq-split-corr"], decision)
    assert not module._input_identity_ok(bundle, new_obs, bundle.records["raw-eq-split"], decision)
    assert not module._input_identity_ok(bundle, new_obs, bundle.records["raw-eq-10ka"], decision)
    with pytest.raises(module.ContractError, match="split endpoints are not one listing"):
        module.validate_records(bundle)


def test_correction_without_its_raw_parent_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    observations = json.loads((copy / "fixtures" / "observations.json").read_text(encoding="utf-8"))
    amended = next(item for item in observations["observations"] if item["observation_id"] == "obs-revenue-jul")
    amended["raw_record_ids"] = ["raw-eq-10ka"]
    (copy / "fixtures" / "observations.json").write_text(
        json.dumps(observations, indent=2) + "\n", encoding="utf-8"
    )
    lineage = json.loads((copy / "fixtures" / "lineage.json").read_text(encoding="utf-8"))
    edge = next(item for item in lineage["edges"] if item["observation_id"] == "obs-revenue-jul")
    edge["input_raw_record_ids"] = ["raw-eq-10ka"]
    (copy / "fixtures" / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads((copy / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {
        rel: hashlib.sha256((copy / rel).read_bytes()).hexdigest() for rel in module.EXPECTED_FILES
    }
    (copy / "fixtures" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(module.ContractError, match="raw parent"):
        module.validate_candidate(copy)


def _sync_coverage_pin(module, copy: Path) -> None:
    """Move the coverage pin to a candidate whose scenarios were intentionally extended."""
    checks = json.loads((copy / "fixtures" / "replay_checks.json").read_text(encoding="utf-8"))["checks"]
    observations = json.loads((copy / "fixtures" / "observations.json").read_text(encoding="utf-8"))["observations"]
    coverage = json.loads((copy / "fixtures" / "coverage.json").read_text(encoding="utf-8"))
    coverage["checks"] = [module.coverage_scenario(check) for check in checks]
    coverage["observation_ids"] = [item["observation_id"] for item in observations]
    text = json.dumps(coverage, indent=2) + "\n"
    (copy / "fixtures" / "coverage.json").write_text(text, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(text.encode()).hexdigest()
    contract_path = copy / "CONTRACT.md"
    contract = contract_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"Coverage manifest SHA-256: `[0-9a-f]{64}`",
        f"Coverage manifest SHA-256: `{digest}`",
        contract,
        count=1,
    )
    assert count == 1
    contract_path.write_text(updated, encoding="utf-8", newline="\n")
    _rewrite_manifest(module, copy)


def _rewrite_manifest(module, copy: Path) -> None:
    manifest = json.loads((copy / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {
        rel: hashlib.sha256((copy / rel).read_bytes()).hexdigest() for rel in module.EXPECTED_FILES
    }
    (copy / "fixtures" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def test_unparseable_record_timestamp_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    cases = [
        ("raw-eq-ooo", "event_time", "2024-02-31T21:30:05Z"),
        ("raw-eq-10k-dup", "accepted_at", "2024-02-30T21:30:05Z"),
    ]
    for raw_id, field, bad_value in cases:
        copy = tmp_path / ("O2-" + field)
        shutil.copytree(O2, copy)
        records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
        record = next(item for item in records["records"] if item["raw_record_id"] == raw_id)
        record[field] = bad_value
        (copy / "fixtures" / "raw_records.json").write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        _rewrite_manifest(module, copy)
        with pytest.raises(module.ContractError, match="timestamp"):
            module.validate_candidate(copy)


def test_incompatible_correction_payload_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    amendment = next(item for item in records["records"] if item["raw_record_id"] == "raw-eq-10ka")
    quote = next(item for item in records["records"] if item["raw_record_id"] == "raw-eq-quote")
    amendment["payload"] = quote["payload"]
    amendment["raw_sha256"] = module.payload_sha256(amendment["payload"])
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="source does not emit|payload kind|equity fact"):
        module.validate_candidate(copy)


def test_contradictory_event_result_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    for raw_id in ("raw-ev-determined", "raw-ev-settled"):
        copy = tmp_path / ("O2-" + raw_id)
        shutil.copytree(O2, copy)
        records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
        record = next(item for item in records["records"] if item["raw_record_id"] == raw_id)
        record["payload"]["result"] = "no"
        record["raw_sha256"] = module.payload_sha256(record["payload"])
        (copy / "fixtures" / "raw_records.json").write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        _rewrite_manifest(module, copy)
        with pytest.raises(module.ContractError, match="contradicts"):
            module.validate_candidate(copy)


def test_event_record_must_name_its_market_identity(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    record = next(item for item in records["records"] if item["raw_record_id"] == "raw-ev-close-ooo")
    record["identity_id"] = "idn-ev-void"
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="market identity"):
        module.validate_candidate(copy)


def test_unresolved_listing_reference_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    for bad_ref in ("idn-eq-ghost", "idn-ev-b1"):
        copy = tmp_path / ("O2-" + bad_ref)
        shutil.copytree(O2, copy)
        records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
        record = next(item for item in records["records"] if item["raw_record_id"] == "raw-eq-split")
        record["payload"]["listing_identity_before"] = bad_ref
        record["raw_sha256"] = module.payload_sha256(record["payload"])
        (copy / "fixtures" / "raw_records.json").write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        _rewrite_manifest(module, copy)
        with pytest.raises(module.ContractError, match="listing reference"):
            module.validate_candidate(copy)


def test_status_observed_must_be_a_rest_poll(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    record = next(item for item in records["records"] if item["raw_record_id"] == "raw-ev-d1-disputed")
    record["payload"]["channel"] = "market_lifecycle_v2"
    record["raw_sha256"] = module.payload_sha256(record["payload"])
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="pairs status_observed"):
        module.validate_candidate(copy)


def test_unreal_settlement_instant_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    record = next(item for item in records["records"] if item["raw_record_id"] == "raw-ev-d1-disputed")
    record["payload"]["settlement_ts"] = "2024-02-31T14:59:00Z"
    record["raw_sha256"] = module.payload_sha256(record["payload"])
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="timestamp"):
        module.validate_candidate(copy)


def test_event_identity_requires_settlement_sources(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    identities = json.loads((copy / "fixtures" / "identities.json").read_text(encoding="utf-8"))
    entry = next(item for item in identities["identities"] if item["identity_id"] == "idn-ev-d1")
    del entry["event"]["settlement_sources"]
    (copy / "fixtures" / "identities.json").write_text(
        json.dumps(identities, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="schema: fixtures/identities.json"):
        module.validate_candidate(copy)


def test_event_identity_rejects_empty_settlement_sources(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    identities = json.loads((copy / "fixtures" / "identities.json").read_text(encoding="utf-8"))
    entry = next(item for item in identities["identities"] if item["identity_id"] == "idn-ev-d1")
    entry["event"]["settlement_sources"] = []
    (copy / "fixtures" / "identities.json").write_text(
        json.dumps(identities, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="schema: fixtures/identities.json"):
        module.validate_candidate(copy)


def test_event_observation_on_non_resolution_key_fails() -> None:
    module = _load_validator()
    documents = {rel: json.loads((O2 / rel).read_text(encoding="utf-8")) for rel in module.SCHEMA_FOR}
    bundle = module.Bundle(documents)
    observation = {
        "market_family": "event",
        "identity_id": "idn-ev-b1",
        "natural_key": "kalshi_demo:market:kxsynth-26jan01-b1:close_date",
        "raw_record_ids": ["raw-ev-close-ooo"],
        "resolution": "yes",
        "lifecycle_status": "finalized",
    }
    assert module.semantic_codes(bundle, observation, module.parse_time("2024-09-02T00:00:00Z")) == [
        "lost_resolution_semantics"
    ]


def test_byte_identical_correction_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    by_id = {item["raw_record_id"]: item for item in records["records"]}
    record = by_id["raw-ev-d1-amended"]
    record["payload"] = by_id["raw-ev-d1-disputed"]["payload"]
    record["raw_sha256"] = module.payload_sha256(record["payload"])
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="repeats its predecessor"):
        module.validate_candidate(copy)


def test_duplicate_before_target_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    record = next(item for item in records["records"] if item["raw_record_id"] == "raw-eq-10k-dup")
    record["event_time"] = "2020-01-01T00:00:00Z"
    record["accepted_at"] = "2020-01-01T00:00:00Z"
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="precedes its target"):
        module.validate_candidate(copy)


def test_source_must_emit_its_payload_kind(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    record = next(item for item in records["records"] if item["raw_record_id"] == "raw-eq-quote")
    record["source"] = "synthetic_sec_submission"
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="source does not emit"):
        module.validate_candidate(copy)


def test_impossible_ex_date_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    records = json.loads((copy / "fixtures" / "raw_records.json").read_text(encoding="utf-8"))
    record = next(item for item in records["records"] if item["raw_record_id"] == "raw-eq-split")
    record["payload"]["ex_date"] = "2024-02-31"
    record["raw_sha256"] = module.payload_sha256(record["payload"])
    (copy / "fixtures" / "raw_records.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    _rewrite_manifest(module, copy)
    with pytest.raises(module.ContractError, match="calendar date"):
        module.validate_candidate(copy)


def _candidate_documents(module) -> dict:
    return {rel: json.loads((O2 / rel).read_text(encoding="utf-8")) for rel in module.SCHEMA_FOR}


def _write_modified_candidate(module, copy: Path, documents: dict) -> Path:
    shutil.copytree(O2, copy)
    for record in documents["fixtures/raw_records.json"]["records"]:
        record["raw_sha256"] = module.payload_sha256(record["payload"])
    for rel, document in documents.items():
        # Preserve the LF bytes hashed by the coverage pin on Windows as well.
        (copy / rel).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    _rewrite_manifest(module, copy)
    return copy


@pytest.mark.parametrize("raw_id", ["raw-eq-ooo", "raw-eq-10k-dup", "raw-ev-close-ooo"])
def test_record_event_time_after_acceptance_is_rejected(tmp_path: Path, raw_id: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(r for r in documents["fixtures/raw_records.json"]["records"] if r["raw_record_id"] == raw_id)
    future = module.parse_time(record["accepted_at"]) + timedelta(seconds=1)
    record["event_time"] = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="event_time is after accepted_at"):
        module.validate_candidate(copy)


def _set_event_contract_resolution(documents: dict, contract_family: str, resolution: str) -> None:
    identity = next(i for i in documents["fixtures/identities.json"]["identities"] if i["identity_id"] == "idn-ev-b1")
    identity["event"]["contract_family"] = contract_family
    # Change every resolution and observation consistently: copy checks alone
    # cannot detect the wrong contract family.
    for record in documents["fixtures/raw_records.json"]["records"]:
        if record["identity_id"] == identity["identity_id"] and record["payload"]["resolution"] is not None:
            record["payload"]["resolution"] = resolution
            record["payload"]["result"] = resolution
    for observation in documents["fixtures/observations.json"]["observations"]:
        if observation["identity_id"] == identity["identity_id"]:
            observation["resolution"] = resolution


@pytest.mark.parametrize("contract_family,resolution", [("binary", "scalar"), ("scalar", "yes"), ("scalar", "no")])
def test_event_resolution_must_match_contract_family(
    tmp_path: Path, contract_family: str, resolution: str
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _set_event_contract_resolution(documents, contract_family, resolution)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="contract family"):
        module.validate_candidate(copy)


def test_matching_scalar_event_resolution_is_accepted(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _set_event_contract_resolution(documents, "scalar", "scalar")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"


@pytest.mark.parametrize("raw_id", ["raw-ev-d1-disputed", "raw-ev-d1-amended"])
def test_rest_settlement_timestamp_requires_finalized(tmp_path: Path, raw_id: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(r for r in documents["fixtures/raw_records.json"]["records"] if r["raw_record_id"] == raw_id)
    record["payload"]["settlement_ts"] = record["event_time"]
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="settlement_ts before finalized"):
        module.validate_candidate(copy)


@pytest.mark.parametrize("session_date", ["2024-02-31", "2023-02-29", "0000-01-01"])
def test_impossible_equity_session_date_is_rejected(tmp_path: Path, session_date: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(r for r in documents["fixtures/raw_records.json"]["records"] if r["raw_record_id"] == "raw-eq-quote")
    record["calendar"]["session_date"] = session_date
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="calendar date"):
        module.validate_candidate(copy)


@pytest.mark.parametrize("field,value", [("issuer_cik", "0000000002"), ("share_class", "preferred")])
def test_equity_identity_fields_must_agree(tmp_path: Path, field: str, value: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    identity = next(i for i in documents["fixtures/identities.json"]["identities"] if i["identity_id"] == "idn-eq-v1")
    identity["equity"][field] = value
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="listing_key contradicts|natural key does not name its equity"):
        module.validate_candidate(copy)


@pytest.mark.parametrize("family", ["equity", "event"])
def test_prior_identity_must_refer_to_same_instrument(tmp_path: Path, family: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    identities = documents["fixtures/identities.json"]["identities"]
    if family == "equity":
        unrelated = json.loads(json.dumps(identities[0]))
        unrelated["identity_id"] = "idn-eq-other"
        unrelated["equity"]["issuer_cik"] = "0000000002"
        unrelated["equity"]["listing_key"] = "us-equity:cik:0000000002:class:common"
        identities.append(unrelated)
        identity = next(i for i in identities if i["identity_id"] == "idn-eq-v2")
        identity["prior_identity_id"] = unrelated["identity_id"]
    else:
        identity = next(i for i in identities if i["identity_id"] == "idn-ev-b1")
        identity["identity_version"] = 2
        identity["prior_identity_id"] = "idn-ev-void"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="prior identity changes instrument"):
        module.validate_candidate(copy)


def _ensure_probe_identity(documents: dict) -> None:
    identities = documents["fixtures/identities.json"]["identities"]
    if any(item["identity_id"] == "idn-ev-probe" for item in identities):
        return
    probe = json.loads(json.dumps(next(item for item in identities if item["identity_id"] == "idn-ev-b1")))
    probe["identity_id"] = "idn-ev-probe"
    probe["event"] = dict(probe["event"])
    probe["event"]["market_ticker"] = "KXSYNTH-26JAN01-PROBE"
    identities.append(probe)


def _add_lifecycle_state_probe(
    documents: dict, event_type: str, status: str, resolution: str | None, result: str | None
) -> None:
    _ensure_probe_identity(documents)
    records = documents["fixtures/raw_records.json"]["records"]
    record = json.loads(json.dumps(next(r for r in records if r["raw_record_id"] == "raw-ev-determined")))
    suffix = {"close_date_updated": "close_date", "created": "created"}.get(event_type, "resolution")
    record.update(
        raw_record_id="raw-ev-state-probe",
        source_record_key="state-probe",
        identity_id="idn-ev-probe",
        natural_key=f"kalshi_demo:market:kxsynth-26jan01-probe:{suffix}",
        supersedes=None,
    )
    records.append(record)
    payload = record["payload"]
    payload.update(
        event_type=event_type,
        channel="rest_get_markets" if event_type == "status_observed" else "market_lifecycle_v2",
        status_rest=status,
        resolution=resolution,
        result=result,
        official_lifecycle_status_observed=status != "voided",
        close_time="2024-09-05T20:00:00Z" if event_type == "close_date_updated" else None,
    )
    for field in ("determination_ts", "settled_ts", "settlement_value", "settlement_ts"):
        payload.pop(field, None)


@pytest.mark.parametrize(
    "event_type,status,resolution,result",
    [
        ("created", "finalized", "yes", "yes"),
        ("created", "active", None, None),
        ("created", "initialized", "yes", "yes"),
        ("created", "voided", "voided", None),
        ("close_date_updated", "finalized", "yes", "yes"),
        ("close_date_updated", "active", "yes", "yes"),
        ("determined", "finalized", "yes", "yes"),
        ("determined", "determined", None, None),
        ("determined", "determined", "yes", None),
        ("settled", "determined", "yes", "yes"),
        ("settled", "finalized", None, None),
        ("settled", "voided", "voided", None),
        ("status_observed", "initialized", "yes", "yes"),
        ("status_observed", "disputed", None, None),
        ("status_observed", "amended", "no", None),
        ("status_observed", "finalized", None, None),
        ("status_observed", "voided", "voided", None),
    ],
)
def test_lifecycle_event_state_contradictions_are_rejected(
    tmp_path: Path, event_type: str, status: str, resolution: str | None, result: str | None
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _add_lifecycle_state_probe(documents, event_type, status, resolution, result)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="lifecycle state"):
        module.validate_candidate(copy)


@pytest.mark.parametrize(
    "event_type,status",
    [("created", "initialized"), ("close_date_updated", "closed"), ("status_observed", "active")],
)
def test_unresolved_lifecycle_states_without_results_are_accepted(
    tmp_path: Path, event_type: str, status: str
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _add_lifecycle_state_probe(documents, event_type, status, None, None)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"


def test_resultless_settled_socket_without_predecessor_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _add_lifecycle_state_probe(documents, "settled", "finalized", "yes", None)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="settled resolution has no predecessor"):
        module.validate_candidate(copy)


def test_resultless_settled_socket_copies_predecessor_resolution(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _add_lifecycle_state_probe(documents, "determined", "determined", "yes", "yes")
    records = documents["fixtures/raw_records.json"]["records"]
    prior = next(item for item in records if item["raw_record_id"] == "raw-ev-state-probe")
    prior["raw_record_id"] = "raw-ev-probe-determined"
    prior["source_record_key"] = "probe-determined"
    settled = json.loads(json.dumps(prior))
    settled.update(
        raw_record_id="raw-ev-probe-settled",
        source_record_key="probe-settled",
        supersedes="raw-ev-probe-determined",
        event_time="2024-09-01T18:00:00Z",
        accepted_at="2024-09-01T18:00:02Z",
    )
    settled["payload"] = dict(settled["payload"])
    settled["payload"].update(
        event_type="settled",
        channel="market_lifecycle_v2",
        status_rest="finalized",
        resolution="yes",
        result=None,
        official_lifecycle_status_observed=True,
        determination_ts=None,
        settlement_value=None,
        settled_ts=1725213600,
        settlement_ts=None,
    )
    records.append(settled)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"


def test_resultless_settled_socket_rejects_contradictory_predecessor(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _add_lifecycle_state_probe(documents, "determined", "determined", "yes", "yes")
    records = documents["fixtures/raw_records.json"]["records"]
    prior = next(item for item in records if item["raw_record_id"] == "raw-ev-state-probe")
    prior["raw_record_id"] = "raw-ev-probe-determined"
    prior["source_record_key"] = "probe-determined"
    settled = json.loads(json.dumps(prior))
    settled.update(
        raw_record_id="raw-ev-probe-settled",
        source_record_key="probe-settled",
        supersedes="raw-ev-probe-determined",
        event_time="2024-09-01T18:00:00Z",
        accepted_at="2024-09-01T18:00:02Z",
    )
    settled["payload"] = dict(settled["payload"])
    settled["payload"].update(
        event_type="settled",
        channel="market_lifecycle_v2",
        status_rest="finalized",
        resolution="no",
        result=None,
        official_lifecycle_status_observed=True,
        determination_ts=None,
        settlement_value=None,
        settled_ts=1725213600,
        settlement_ts=None,
    )
    records.append(settled)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="settled resolution has no predecessor"):
        module.validate_candidate(copy)


def test_future_correction_does_not_authorize_lineage_input() -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    bundle = module.Bundle(documents)
    bundle.identities["idn-eq-moved"] = {
        "identity_id": "idn-eq-moved",
        "market_family": "equity",
        "identity_version": 1,
        "equity": {
            "issuer_cik": "0000000002",
            "listing_key": "us-equity:cik:0000000002:class:common",
            "share_class": "common",
        },
    }
    quote = json.loads(json.dumps(bundle.records["raw-eq-quote"]))
    quote.update(
        raw_record_id="raw-eq-quote-moved",
        identity_id="idn-eq-moved",
        source_record_key="quote-moved",
        natural_key="us-equity:listing:0000000002:common:quote",
    )
    bundle.records[quote["raw_record_id"]] = quote
    move = json.loads(json.dumps(bundle.records["raw-eq-split-corr"]))
    move.update(
        raw_record_id="raw-eq-split-move",
        source_record_key="split-move",
        event_time="2024-12-01T15:00:00Z",
        accepted_at="2024-12-01T15:00:00Z",
        supersedes="raw-eq-split-corr",
    )
    move["payload"] = dict(move["payload"])
    move["payload"]["listing_identity_before"] = "idn-eq-moved"
    move["payload"]["listing_identity_after"] = "idn-eq-moved"
    move["payload"]["ratio_numerator"] = 5
    move["raw_sha256"] = module.payload_sha256(move["payload"])
    bundle.records[move["raw_record_id"]] = move
    early = module.parse_time("2024-08-11T00:00:00Z")
    later = module.parse_time("2024-12-02T00:00:00Z")
    observation = {
        "observation_id": "obs-moved-quote",
        "decision_time": "2024-08-11T00:00:00Z",
        "market_family": "equity",
        "identity_id": "idn-eq-moved",
        "natural_key": quote["natural_key"],
        "effective_raw_record_id": quote["raw_record_id"],
        "raw_record_ids": [quote["raw_record_id"], "raw-eq-split"],
        "value": "50.00",
        "resolution": None,
        "lifecycle_status": None,
        "corporate_actions": [],
    }
    assert not module._input_identity_ok(bundle, observation, bundle.records["raw-eq-split"], early)
    assert not module._input_identity_ok(bundle, observation, bundle.records["raw-eq-split"], later)
    bundle.observations.append(observation)
    bundle.edges.append(
        {
            "lineage_id": "lin-moved-quote",
            "observation_id": "obs-moved-quote",
            "input_raw_record_ids": observation["raw_record_ids"],
            "transform_id": "o2.candidate.effective_version",
            "transform_version": "o2.candidate.v1",
        }
    )
    with pytest.raises(module.ContractError, match="uses another identity"):
        module.validate_observations(bundle)


def test_equity_natural_key_must_match_identity(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    old = "us-equity:listing:0000000001:common:quote"
    new = "us-equity:listing:0000000002:common:quote"
    for record in documents["fixtures/raw_records.json"]["records"]:
        if record["natural_key"] == old:
            record["natural_key"] = new
    for observation in documents["fixtures/observations.json"]["observations"]:
        if observation["natural_key"] == old:
            observation["natural_key"] = new
    for check in documents["fixtures/replay_checks.json"]["checks"]:
        if check["natural_key"] == old:
            check["natural_key"] = new
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="issuer CIK is not bound"):
        module.validate_candidate(copy)


def test_amendment_must_name_the_superseded_accession(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    amendment = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-10ka"
    )
    amendment["payload"]["amends_accession"] = "0000000001-24-000050"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="amends_accession does not name the superseded filing"):
        module.validate_candidate(copy)


def test_duplicate_observation_id_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observations = documents["fixtures/observations.json"]["observations"]
    observations[-1] = json.loads(json.dumps(observations[0]))
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate observation"):
        module.validate_candidate(copy)


def test_lifecycle_natural_key_suffix_must_match_event_type(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    old = "kalshi_demo:market:kxsynth-26jan01-b1:close_date"
    new = "kalshi_demo:market:kxsynth-26jan01-b1:arbitrary_fact"
    for record in documents["fixtures/raw_records.json"]["records"]:
        if record["natural_key"] == old:
            record["natural_key"] = new
    for check in documents["fixtures/replay_checks.json"]["checks"]:
        if check["natural_key"] == old:
            check["natural_key"] = new
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="natural key suffix does not match its event_type"):
        module.validate_candidate(copy)


def test_settlement_timestamp_after_acceptance_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(
        item
        for item in documents["fixtures/raw_records.json"]["records"]
        if item["raw_record_id"] == "raw-ev-d1-finalized"
    )
    record["payload"]["settlement_ts"] = "2099-01-01T00:00:00Z"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="settlement_ts is after accepted_at"):
        module.validate_candidate(copy)


def _other_listing(documents: dict) -> None:
    documents["fixtures/identities.json"]["identities"].append(
        {
            "identity_id": "idn-eq-other",
            "market_family": "equity",
            "identity_version": 1,
            "venue": "us_equity",
            "currency": "USD",
            "prior_identity_id": None,
            "equity": {
                "issuer_cik": "0000000002",
                "listing_key": "us-equity:cik:0000000002:class:common",
                "ticker": "OTHER",
                "share_class": "common",
            },
        }
    )


def test_listing_key_filing_without_issuer_binding_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    records = documents["fixtures/raw_records.json"]["records"]
    filing = json.loads(json.dumps(next(item for item in records if item["raw_record_id"] == "raw-eq-10k")))
    filing.update(
        raw_record_id="raw-eq-listing-filing",
        source_record_key="0000000099-24-000051",
        natural_key="us-equity:listing:0000000001:common:filing",
        duplicate_of=None,
        supersedes=None,
    )
    filing["payload"] = dict(filing["payload"])
    filing["payload"]["accession"] = "0000000099-24-000051"
    filing["payload"].pop("issuer_cik", None)
    records.append(filing)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="agent-filed accession lacks its issuer CIK"):
        module.validate_candidate(copy)


def test_listing_key_agent_filing_is_accepted_when_issuer_matches(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    records = documents["fixtures/raw_records.json"]["records"]
    filing = json.loads(json.dumps(next(item for item in records if item["raw_record_id"] == "raw-eq-10k")))
    filing.update(
        raw_record_id="raw-eq-listing-filing",
        source_record_key="0000000099-24-000051",
        natural_key="us-equity:listing:0000000001:common:filing",
        duplicate_of=None,
        supersedes=None,
    )
    filing["payload"] = dict(filing["payload"])
    filing["payload"]["accession"] = "0000000099-24-000051"
    filing["payload"]["issuer_cik"] = "0000000001"
    records.append(filing)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"


def test_split_record_must_share_endpoint_listing(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _other_listing(documents)
    record = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-split"
    )
    record["payload"]["listing_identity_before"] = "idn-eq-other"
    record["payload"]["listing_identity_after"] = "idn-eq-other"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="split endpoints are not one listing"):
        module.validate_candidate(copy)


def test_amendment_form_must_match_predecessor_family(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    for record in documents["fixtures/raw_records.json"]["records"]:
        if record["raw_record_id"] in {"raw-eq-10k", "raw-eq-10k-dup"}:
            record["payload"]["form"] = "8-K"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="form family does not match its predecessor"):
        module.validate_candidate(copy)


def test_8k_amendment_passes_schema_and_form_family(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    for record in documents["fixtures/raw_records.json"]["records"]:
        payload = record["payload"]
        if payload.get("form") == "10-K":
            payload["form"] = "8-K"
        elif payload.get("form") == "10-K/A":
            payload["form"] = "8-K/A"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"


def test_split_endpoints_must_share_one_listing(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    documents["fixtures/identities.json"]["identities"].append(
        {
            "identity_id": "idn-eq-other",
            "market_family": "equity",
            "identity_version": 1,
            "venue": "us_equity",
            "currency": "USD",
            "prior_identity_id": None,
            "equity": {
                "issuer_cik": "0000000002",
                "listing_key": "us-equity:cik:0000000002:class:common",
                "ticker": "OTHER",
                "share_class": "common",
            },
        }
    )
    record = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-split"
    )
    record["payload"]["listing_identity_after"] = "idn-eq-other"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="split endpoints are not one listing"):
        module.validate_candidate(copy)


@pytest.mark.parametrize(
    ("raw_id", "field"),
    [("raw-ev-determined", "determination_ts"), ("raw-ev-settled", "settled_ts")],
)
def test_socket_lifecycle_timestamp_after_acceptance_is_rejected(
    tmp_path: Path, raw_id: str, field: str
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == raw_id)
    record["payload"][field] = 4102444800
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match=f"{field} is after accepted_at"):
        module.validate_candidate(copy)


def _retarget_fact_accessions(documents: dict, issuer_cik: str | None) -> None:
    mapping = {
        "raw-eq-10k": ("0000000099-24-000001", None),
        "raw-eq-10k-dup": ("0000000099-24-000001", None),
        "raw-eq-10ka": ("0000000099-24-000099", "0000000099-24-000001"),
    }
    for record in documents["fixtures/raw_records.json"]["records"]:
        planned = mapping.get(record["raw_record_id"])
        if planned is None:
            continue
        accession, amends = planned
        record["payload"]["accession"] = accession
        if amends is not None:
            record["payload"]["amends_accession"] = amends
        if issuer_cik is not None:
            record["payload"]["issuer_cik"] = issuer_cik


def test_fact_accession_without_issuer_binding_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _retarget_fact_accessions(documents, None)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="agent-filed accession lacks its issuer CIK"):
        module.validate_candidate(copy)


def test_agent_filed_fact_is_accepted_when_issuer_matches(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _retarget_fact_accessions(documents, "0000000001")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"
    assert module.validate_candidate(copy)[1] == "O2 maturity: researched"


def test_agent_filed_accession_key_without_issuer_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-ooo"
    )
    record["payload"]["accession"] = "0000000099-24-000050"
    record["natural_key"] = "sec:accession:0000000099-24-000050"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="agent-filed accession lacks its issuer CIK"):
        module.validate_candidate(copy)


def test_agent_filed_accession_key_is_accepted_when_issuer_matches(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-ooo"
    )
    record["payload"]["accession"] = "0000000099-24-000050"
    record["payload"]["issuer_cik"] = "0000000001"
    record["natural_key"] = "sec:accession:0000000099-24-000050"
    old = "sec:accession:0000000001-24-000050"
    for observation in documents["fixtures/observations.json"]["observations"]:
        if observation["natural_key"] == old:
            observation["natural_key"] = record["natural_key"]
    for check in documents["fixtures/replay_checks.json"]["checks"]:
        if check["natural_key"] == old:
            check["natural_key"] = record["natural_key"]
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _sync_coverage_pin(module, copy)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"


def _hide_fact_record(documents: dict, raw_id: str) -> None:
    for check in documents["fixtures/replay_checks.json"]["checks"]:
        if check["natural_key"] == "sec:fact:0000000001:revenue:fy2023":
            check["hidden_raw_record_ids"].append(raw_id)


def _append_amendment(
    documents: dict, raw_id: str, supersedes: str, accession: str, amends: str, accepted_at: str, value: str
) -> None:
    records = documents["fixtures/raw_records.json"]["records"]
    base = json.loads(json.dumps(next(item for item in records if item["raw_record_id"] == "raw-eq-10ka")))
    base.update(
        raw_record_id=raw_id,
        source_record_key=accession,
        supersedes=supersedes,
        event_time=accepted_at,
        accepted_at=accepted_at,
    )
    base["calendar"] = dict(base["calendar"])
    base["calendar"]["session_date"] = accepted_at[:10]
    base["payload"] = dict(base["payload"])
    base["payload"].update(accession=accession, amends_accession=amends, value=value)
    records.append(base)
    _hide_fact_record(documents, raw_id)


def test_later_amendment_may_name_the_original_filing(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _append_amendment(
        documents,
        "raw-eq-10ka-mid",
        "raw-eq-10ka",
        "0000000001-24-000120",
        "0000000001-24-000099",
        "2024-09-01T16:00:05Z",
        "130",
    )
    _append_amendment(
        documents,
        "raw-eq-10ka-orig",
        "raw-eq-10ka-mid",
        "0000000001-24-000130",
        "0000000001-24-000001",
        "2024-10-01T16:00:05Z",
        "140",
    )
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _sync_coverage_pin(module, copy)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"


def test_amendment_accession_must_be_distinct(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    amendment = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-10ka"
    )
    amendment["payload"]["accession"] = "0000000001-24-000001"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="amendment reuses a predecessor accession"):
        module.validate_candidate(copy)


def _add_resultless_settled_pair(documents: dict) -> dict:
    _add_lifecycle_state_probe(documents, "determined", "determined", "yes", "yes")
    records = documents["fixtures/raw_records.json"]["records"]
    prior = next(item for item in records if item["raw_record_id"] == "raw-ev-state-probe")
    prior["raw_record_id"] = "raw-ev-probe-determined"
    prior["source_record_key"] = "probe-determined"
    settled = json.loads(json.dumps(prior))
    settled.update(
        raw_record_id="raw-ev-probe-settled",
        source_record_key="probe-settled",
        supersedes="raw-ev-probe-determined",
        event_time="2024-09-01T18:00:00Z",
        accepted_at="2024-09-01T18:00:02Z",
    )
    settled["payload"] = dict(settled["payload"])
    settled["payload"].update(
        event_type="settled",
        channel="market_lifecycle_v2",
        status_rest="finalized",
        resolution="yes",
        result=None,
        official_lifecycle_status_observed=True,
        determination_ts=None,
        settlement_value=None,
        settled_ts=1725213600,
        settlement_ts=None,
    )
    records.append(settled)
    return settled


def test_resultless_settled_duplicate_is_idempotent(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    settled = _add_resultless_settled_pair(documents)
    duplicate = json.loads(json.dumps(settled))
    duplicate.update(
        raw_record_id="raw-ev-probe-settled-dup",
        source_record_key="probe-settled-dup",
        duplicate_of="raw-ev-probe-settled",
        supersedes=None,
        accepted_at="2024-09-01T18:05:00Z",
    )
    documents["fixtures/raw_records.json"]["records"].append(duplicate)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"
    bundle = module.Bundle({rel: json.loads((copy / rel).read_text(encoding="utf-8")) for rel in module.SCHEMA_FOR})
    effective = module.effective_record(
        bundle.records,
        settled["natural_key"],
        module.parse_time("2024-09-01T19:00:00Z"),
    )
    assert effective is not None
    assert effective["raw_record_id"] == "raw-ev-probe-settled"


def test_resultless_settled_duplicate_with_different_bytes_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    settled = _add_resultless_settled_pair(documents)
    duplicate = json.loads(json.dumps(settled))
    duplicate.update(
        raw_record_id="raw-ev-probe-settled-dup",
        source_record_key="probe-settled-dup",
        duplicate_of="raw-ev-probe-settled",
        supersedes=None,
        accepted_at="2024-09-01T18:05:00Z",
    )
    duplicate["payload"] = dict(duplicate["payload"])
    duplicate["payload"]["resolution"] = "no"
    documents["fixtures/raw_records.json"]["records"].append(duplicate)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate does not match its target"):
        module.validate_candidate(copy)


def test_ex_date_correction_is_replayable(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    records = documents["fixtures/raw_records.json"]["records"]
    original = next(item for item in records if item["raw_record_id"] == "raw-eq-split-oct")
    correction = json.loads(json.dumps(original))
    correction.update(
        raw_record_id="raw-eq-split-oct-corr",
        source_record_key="split-2024-10-01#corr",
        supersedes="raw-eq-split-oct",
        event_time="2024-09-16T16:00:00Z",
        accepted_at="2024-09-16T16:00:00Z",
    )
    correction["payload"] = dict(correction["payload"])
    correction["payload"]["ex_date"] = "2024-10-02"
    records.append(correction)
    for check in documents["fixtures/replay_checks.json"]["checks"]:
        if check["check_id"] == "independent_corporate_action_hidden":
            check["hidden_raw_record_ids"].append("raw-eq-split-oct-corr")
        if check["check_id"] == "independent_corporate_action_retained":
            check["visible_raw_record_ids"].append("raw-eq-split-oct-corr")
            check["effective_raw_record_id"] = "raw-eq-split-oct-corr"
    observation = next(
        item
        for item in documents["fixtures/observations.json"]["observations"]
        if item["observation_id"] == "obs-splits-sep"
    )
    observation["effective_raw_record_id"] = "raw-eq-split-oct-corr"
    observation["raw_record_ids"].append("raw-eq-split-oct-corr")
    action = next(item for item in observation["corporate_actions"] if item["raw_record_id"] == "raw-eq-split-oct")
    action["raw_record_id"] = "raw-eq-split-oct-corr"
    action["ex_date"] = "2024-10-02"
    edge = next(
        item for item in documents["fixtures/lineage.json"]["edges"] if item["observation_id"] == "obs-splits-sep"
    )
    edge["input_raw_record_ids"] = list(observation["raw_record_ids"])
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _sync_coverage_pin(module, copy)
    assert module.validate_candidate(copy)[0] == "O2 candidate corpus: ok"
    bundle = module.Bundle({rel: json.loads((copy / rel).read_text(encoding="utf-8")) for rel in module.SCHEMA_FOR})
    effective = module.effective_record(
        bundle.records,
        original["natural_key"],
        module.parse_time("2024-09-17T00:00:00Z"),
    )
    assert effective is not None
    assert effective["raw_record_id"] == "raw-eq-split-oct-corr"
    assert effective["payload"]["ex_date"] == "2024-10-02"


def test_root_ex_date_suffix_must_match(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    record = next(
        item
        for item in documents["fixtures/raw_records.json"]["records"]
        if item["raw_record_id"] == "raw-eq-split-oct"
    )
    record["payload"]["ex_date"] = "2024-10-02"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="natural key does not name its ex-date"):
        module.validate_candidate(copy)


def test_duplicate_replay_check_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    checks = documents["fixtures/replay_checks.json"]["checks"]
    checks[-1]["check_id"] = checks[0]["check_id"]
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate replay check"):
        module.validate_candidate(copy)


def _rename_natural_key(documents: dict, old: str, new: str) -> None:
    for record in documents["fixtures/raw_records.json"]["records"]:
        if record["natural_key"] == old:
            record["natural_key"] = new
    for observation in documents["fixtures/observations.json"]["observations"]:
        if observation["natural_key"] == old:
            observation["natural_key"] = new
    for check in documents["fixtures/replay_checks.json"]["checks"]:
        if check["natural_key"] == old:
            check["natural_key"] = new


@pytest.mark.parametrize("repin_coverage", [False, True])
def test_fact_period_relabel_cannot_bypass_the_unchanged_contract(
    tmp_path: Path, repin_coverage: bool
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _rename_natural_key(
        documents, "sec:fact:0000000001:revenue:fy2023", "sec:fact:0000000001:revenue:fy2099"
    )
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    expected = "replay scenario does not match the coverage manifest"
    if repin_coverage:
        coverage = documents["fixtures/coverage.json"]
        coverage["checks"] = [module.coverage_scenario(check) for check in documents["fixtures/replay_checks.json"]["checks"]]
        (copy / "fixtures/coverage.json").write_text(
            json.dumps(coverage, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        _rewrite_manifest(module, copy)
        expected = "coverage manifest digest does not match the contract"
    else:
        assert (copy / "fixtures/coverage.json").read_bytes() == (O2 / "fixtures/coverage.json").read_bytes()
    assert (copy / "CONTRACT.md").read_bytes() == (O2 / "CONTRACT.md").read_bytes()
    # No payload reporting period exists: this protects the frozen corpus label,
    # not arbitrary SEC fiscal-period semantics or a newly repinned contract.
    with pytest.raises(module.ContractError, match=expected):
        module.validate_candidate(copy)


@pytest.mark.parametrize("form", ["10-K", "8-K"])
@pytest.mark.parametrize("representation", ["correction", "duplicate", "new_accession"])
def test_accession_key_preserves_raw_bytes(
    tmp_path: Path, form: str, representation: str
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    records = documents["fixtures/raw_records.json"]["records"]
    original = json.loads(json.dumps(next(record for record in records if record["raw_record_id"] == "raw-eq-ooo")))
    original.update(
        raw_record_id="raw-accession-original", source_record_key="0000000001-24-000051",
        natural_key="sec:accession:0000000001-24-000051",
    )
    original["payload"].update(form=form, accession="0000000001-24-000051")
    later = json.loads(json.dumps(original))
    later.update(raw_record_id="raw-accession-later", source_record_key="later", accepted_at="2024-05-03T15:00:00Z")
    if representation == "duplicate":
        later["duplicate_of"] = original["raw_record_id"]
    else:
        later["payload"]["value"] = "changed"
        if representation == "correction":
            later["supersedes"] = original["raw_record_id"]
        else:
            later["payload"]["accession"] = "0000000001-24-000052"
            later["source_record_key"] = "0000000001-24-000052"
            later["natural_key"] = "sec:accession:0000000001-24-000052"
    records.extend((original, later))
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    for retained in ("CONTRACT.md", "fixtures/coverage.json"):
        assert (copy / retained).read_bytes() == (O2 / retained).read_bytes()
    if representation == "correction":
        with pytest.raises(module.ContractError, match="filing correction reuses a predecessor accession"):
            module.validate_candidate(copy)
    else:
        assert module.validate_candidate(copy)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


def test_duplicate_corporate_action_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observation = next(
        item
        for item in documents["fixtures/observations.json"]["observations"]
        if item["observation_id"] == "obs-splits-sep"
    )
    observation["corporate_actions"].append(dict(observation["corporate_actions"][0]))
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="repeats corporate action"):
        module.validate_candidate(copy)


def test_unknown_corporate_action_suffix_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _rename_natural_key(
        documents,
        "us-equity:listing:0000000001:common:corporate_action:ex-2024-10-01",
        "us-equity:listing:0000000001:common:corporate_action:anything",
    )
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="natural key suffix is not a corporate action"):
        module.validate_candidate(copy)


def _copy_filing(documents: dict, raw_id: str, duplicate_of: str, accepted_at: str) -> dict:
    records = documents["fixtures/raw_records.json"]["records"]
    base = json.loads(json.dumps(next(item for item in records if item["raw_record_id"] == "raw-eq-10k")))
    base.update(
        raw_record_id=raw_id,
        source_record_key=raw_id,
        duplicate_of=duplicate_of,
        supersedes=None,
        event_time="2024-03-01T21:30:00Z",
        accepted_at=accepted_at,
    )
    records.append(base)
    return base


def test_duplicate_self_reference_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    duplicate = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-10k-dup"
    )
    duplicate["duplicate_of"] = "raw-eq-10k-dup"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate refers to itself"):
        module.validate_candidate(copy)


def test_duplicate_of_missing_record_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    duplicate = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-10k-dup"
    )
    duplicate["duplicate_of"] = "raw-eq-missing"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate names a missing record"):
        module.validate_candidate(copy)


def test_duplicate_of_later_record_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    duplicate = next(
        item for item in documents["fixtures/raw_records.json"]["records"] if item["raw_record_id"] == "raw-eq-10k-dup"
    )
    duplicate["accepted_at"] = "2024-03-01T21:30:01Z"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate precedes its target"):
        module.validate_candidate(copy)


def test_duplicate_chain_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _copy_filing(documents, "raw-eq-10k-dup-chain", "raw-eq-10k-dup", "2024-03-01T21:32:00Z")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate chains through another duplicate"):
        module.validate_candidate(copy)


def test_duplicate_cycle_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _copy_filing(documents, "raw-eq-10k-cycle-a", "raw-eq-10k-cycle-b", "2024-03-02T21:31:00Z")
    _copy_filing(documents, "raw-eq-10k-cycle-b", "raw-eq-10k-cycle-a", "2024-03-02T21:31:00Z")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="duplicate cycle"):
        module.validate_candidate(copy)


def test_listing_key_tail_must_be_the_payload_kind(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _rename_natural_key(
        documents,
        "us-equity:listing:0000000001:common:quote",
        "us-equity:listing:0000000001:common:quote:extra",
    )
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="natural key does not name its equity fact"):
        module.validate_candidate(copy)


def _base_filing_correction(documents: dict, form: str) -> dict:
    records = documents["fixtures/raw_records.json"]["records"]
    for record in records:
        if record["raw_record_id"] in {"raw-eq-10k", "raw-eq-10k-dup"}:
            record["payload"]["form"] = form
    correction = next(record for record in records if record["raw_record_id"] == "raw-eq-10ka")
    correction["payload"].update(kind="filing", form=form)
    correction["payload"].pop("amends_accession")
    return correction


@pytest.mark.parametrize("form", ["10-K", "8-K"])
def test_base_filing_correction_requires_distinct_accession(tmp_path: Path, form: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    correction = _base_filing_correction(documents, form)
    correction["payload"]["accession"] = "0000000001-24-000001"
    correction["source_record_key"] = "0000000001-24-000001"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="filing correction reuses a predecessor accession"):
        module.validate_candidate(copy)


@pytest.mark.parametrize("form", ["10-K", "8-K"])
def test_base_filing_correction_preserves_unit(tmp_path: Path, form: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    correction = _base_filing_correction(documents, form)
    correction["payload"]["unit"] = "text"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="filing correction changes stable fact field unit"):
        module.validate_candidate(copy)


@pytest.mark.parametrize("form", ["10-K", "8-K"])
def test_base_filing_correction_preserves_concept_on_listing_key(form: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    correction = _base_filing_correction(documents, form)
    records = documents["fixtures/raw_records.json"]["records"]
    for record in records:
        if record["raw_record_id"] in {"raw-eq-10k", "raw-eq-10k-dup", "raw-eq-10ka"}:
            record["natural_key"] = "us-equity:listing:0000000001:common:filing"
        record["raw_sha256"] = module.payload_sha256(record["payload"])
    # A listing key does not itself bind the concept, unlike a sec:fact key.
    module.validate_records(module.Bundle(documents))
    correction["payload"]["concept"] = "other_fact"
    correction["raw_sha256"] = module.payload_sha256(correction["payload"])
    with pytest.raises(module.ContractError, match="filing correction changes stable fact field concept"):
        module.validate_records(module.Bundle(documents))


@pytest.mark.parametrize("form", ["10-K", "8-K"])
def test_distinct_base_filing_correction_preserves_valid_replay(tmp_path: Path, form: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _base_filing_correction(documents, form)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


@pytest.mark.parametrize("repeat_in", ["observation", "lineage", "both"])
def test_observation_and_lineage_inputs_must_be_unique(tmp_path: Path, repeat_in: str) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observation = documents["fixtures/observations.json"]["observations"][0]
    edge = next(
        item for item in documents["fixtures/lineage.json"]["edges"]
        if item["observation_id"] == observation["observation_id"]
    )
    if repeat_in in {"observation", "both"}:
        observation["raw_record_ids"].append("raw-eq-10k")
    if repeat_in in {"lineage", "both"}:
        edge["input_raw_record_ids"].append("raw-eq-10k")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="repeats (observation|lineage) raw input"):
        module.validate_candidate(copy)


@pytest.mark.parametrize(
    ("observation_id", "raw_id"),
    [
        ("obs-revenue-jul", "raw-eq-ooo"),
        ("obs-revenue-apr", "raw-eq-10k-dup"),
        ("obs-ev-sep", "raw-ev-close-ooo"),
    ],
)
def test_observation_rejects_inputs_outside_effective_and_action_chains(
    tmp_path: Path, observation_id: str, raw_id: str
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observation = next(
        item for item in documents["fixtures/observations.json"]["observations"]
        if item["observation_id"] == observation_id
    )
    observation["raw_record_ids"].append(raw_id)
    edge = next(
        item for item in documents["fixtures/lineage.json"]["edges"]
        if item["observation_id"] == observation_id
    )
    edge["input_raw_record_ids"] = list(observation["raw_record_ids"])
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="inputs are not the effective and action chains"):
        module.validate_candidate(copy)


def test_valid_action_chain_inputs_keep_observation_and_edge_order(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observation = next(
        item for item in documents["fixtures/observations.json"]["observations"]
        if item["observation_id"] == "obs-splits-sep"
    )
    observation["raw_record_ids"].reverse()
    edge = next(
        item for item in documents["fixtures/lineage.json"]["edges"]
        if item["observation_id"] == observation["observation_id"]
    )
    edge["input_raw_record_ids"] = list(observation["raw_record_ids"])
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


def test_deleted_observation_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observations = documents["fixtures/observations.json"]["observations"]
    removed = observations.pop()
    edges = documents["fixtures/lineage.json"]["edges"]
    documents["fixtures/lineage.json"]["edges"] = [
        edge for edge in edges if edge["observation_id"] != removed["observation_id"]
    ]
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="observation set does not match the coverage manifest"):
        module.validate_candidate(copy)


def test_swapped_replay_scenario_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    checks = documents["fixtures/replay_checks.json"]["checks"]
    source = next(item for item in checks if item["check_id"] == "late_correction_before_amendment")
    target = next(item for item in checks if item["check_id"] == "out_of_order_acceptance_hidden")
    kept = target["check_id"]
    target.clear()
    target.update(json.loads(json.dumps(source)))
    target["check_id"] = kept
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="replay scenario does not match the coverage manifest"):
        module.validate_candidate(copy)


def _replace_observation_with_donor(documents: dict, target_id: str, donor_id: str) -> None:
    """Copy a valid observation over a required one, keep the target id, mirror lineage."""
    observations = documents["fixtures/observations.json"]["observations"]
    donor = next(item for item in observations if item["observation_id"] == donor_id)
    index = next(i for i, item in enumerate(observations) if item["observation_id"] == target_id)
    replacement = json.loads(json.dumps(donor))
    replacement["observation_id"] = target_id
    observations[index] = replacement
    edge = next(item for item in documents["fixtures/lineage.json"]["edges"] if item["observation_id"] == target_id)
    edge["input_raw_record_ids"] = list(replacement["raw_record_ids"])


def _assert_pins_unchanged(copy: Path) -> None:
    for retained in ("CONTRACT.md", "fixtures/coverage.json"):
        assert (copy / retained).read_bytes() == (O2 / retained).read_bytes(), retained


def test_required_observation_binding_table_is_the_approved_mapping() -> None:
    module = _load_validator()
    exact = {
        "obs-revenue-apr": "late_correction_before_amendment",
        "obs-revenue-jul": "late_correction_after_amendment",
        "obs-ooo-may": "out_of_order_acceptance_visible_after_accept",
        "obs-quote-aug": "unadjusted_quote_not_rewritten",
        "obs-split-aug": "corporate_action_retained",
        "obs-split-corr": "corporate_action_correction_is_effective",
        "obs-splits-sep": "independent_corporate_action_retained",
        "obs-ev-aug21": "event_resolution_before_late_message",
        "obs-ev-sep": "event_settled_keeps_determination_raw",
        "obs-ev-void": "voided_event_stays_distinct",
        "obs-ev-d1-dispute": "event_dispute_correction_is_effective",
        "obs-ev-d1-final": "event_finalized_keeps_dispute_chain",
    }
    expected = {
        observation_id: {
            "natural_key": check_id,
            "decision_time": check_id,
            "effective_raw_record_id": check_id,
        }
        for observation_id, check_id in exact.items()
    }
    # Disclosed composite: revenue key and effective 10-K/A, post-split decision time.
    expected["obs-revenue-aug"] = {
        "natural_key": "late_correction_after_amendment",
        "decision_time": "corporate_action_retained",
        "effective_raw_record_id": "late_correction_after_amendment",
    }
    assert module.REQUIRED_OBSERVATION_BINDINGS == expected
    coverage = json.loads((O2 / "fixtures" / "coverage.json").read_text(encoding="utf-8"))
    assert set(module.REQUIRED_OBSERVATION_BINDINGS) == set(coverage["observation_ids"])
    assert module.validate_candidate(O2)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


def test_revenue_copied_over_out_of_order_observation_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _replace_observation_with_donor(documents, "obs-ooo-may", "obs-revenue-apr")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _assert_pins_unchanged(copy)
    with pytest.raises(
        module.ContractError,
        match="obs-ooo-may natural_key does not match coverage scenario out_of_order_acceptance_visible_after_accept",
    ):
        module.validate_candidate(copy)


def test_earlier_event_copied_over_settled_observation_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _replace_observation_with_donor(documents, "obs-ev-sep", "obs-ev-aug21")
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _assert_pins_unchanged(copy)
    with pytest.raises(
        module.ContractError,
        match="obs-ev-sep decision_time does not match coverage scenario event_settled_keeps_determination_raw",
    ):
        module.validate_candidate(copy)


def test_composite_revenue_observation_cannot_move_its_decision_time(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observation = next(
        item
        for item in documents["fixtures/observations.json"]["observations"]
        if item["observation_id"] == "obs-revenue-aug"
    )
    observation["decision_time"] = "2024-08-03T00:00:00Z"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _assert_pins_unchanged(copy)
    with pytest.raises(
        module.ContractError,
        match="obs-revenue-aug decision_time does not match coverage scenario corporate_action_retained",
    ):
        module.validate_candidate(copy)


# Each donor is a valid positive observation. Nearby donors are chosen so that some
# replacements differ in only one bound field, which catches a partial table.
@pytest.mark.parametrize(
    ("target_id", "donor_id"),
    [
        ("obs-revenue-apr", "obs-revenue-jul"),
        ("obs-revenue-jul", "obs-revenue-aug"),
        ("obs-ooo-may", "obs-revenue-apr"),
        ("obs-revenue-aug", "obs-revenue-jul"),
        ("obs-quote-aug", "obs-revenue-aug"),
        ("obs-split-aug", "obs-split-corr"),
        ("obs-split-corr", "obs-split-aug"),
        ("obs-splits-sep", "obs-split-corr"),
        ("obs-ev-aug21", "obs-ev-sep"),
        ("obs-ev-sep", "obs-ev-aug21"),
        ("obs-ev-void", "obs-ev-sep"),
        ("obs-ev-d1-dispute", "obs-ev-d1-final"),
        ("obs-ev-d1-final", "obs-ev-d1-dispute"),
    ],
)
def test_required_observation_replaced_by_valid_donor_is_rejected(
    tmp_path: Path, target_id: str, donor_id: str
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _replace_observation_with_donor(documents, target_id, donor_id)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _assert_pins_unchanged(copy)
    with pytest.raises(
        module.ContractError,
        match=rf"{target_id} (natural_key|decision_time|effective_raw_record_id) does not match coverage scenario",
    ):
        module.validate_candidate(copy)


def test_effective_record_binding_is_checked() -> None:
    module = _load_validator()
    bundle = module.Bundle(_candidate_documents(module))
    coverage = json.loads((O2 / "fixtures" / "coverage.json").read_text(encoding="utf-8"))
    checks = {item["check_id"]: item for item in coverage["checks"]}
    module._require_observation_bindings(checks, bundle)
    observation = next(item for item in bundle.observations if item["observation_id"] == "obs-ooo-may")
    observation["effective_raw_record_id"] = "raw-eq-10k"
    with pytest.raises(
        module.ContractError,
        match="obs-ooo-may effective_raw_record_id does not match coverage scenario "
        "out_of_order_acceptance_visible_after_accept",
    ):
        module._require_observation_bindings(checks, bundle)


def test_required_observation_missing_under_repinned_coverage_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    documents["fixtures/observations.json"]["observations"] = [
        item for item in documents["fixtures/observations.json"]["observations"]
        if item["observation_id"] != "obs-revenue-aug"
    ]
    documents["fixtures/lineage.json"]["edges"] = [
        item for item in documents["fixtures/lineage.json"]["edges"] if item["observation_id"] != "obs-revenue-aug"
    ]
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _sync_coverage_pin(module, copy)
    with pytest.raises(module.ContractError, match="required observation obs-revenue-aug is missing"):
        module.validate_candidate(copy)


def test_binding_to_missing_coverage_scenario_fails_closed() -> None:
    module = _load_validator()
    bundle = module.Bundle(_candidate_documents(module))
    coverage = json.loads((O2 / "fixtures" / "coverage.json").read_text(encoding="utf-8"))
    checks = {
        item["check_id"]: item for item in coverage["checks"] if item["check_id"] != "corporate_action_retained"
    }
    with pytest.raises(
        module.ContractError,
        match="obs-revenue-aug binding names missing coverage scenario corporate_action_retained",
    ):
        module._require_observation_bindings(checks, bundle)


def test_incomplete_required_binding_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_validator()
    binding = dict(module.REQUIRED_OBSERVATION_BINDINGS["obs-revenue-aug"])
    del binding["decision_time"]
    monkeypatch.setitem(module.REQUIRED_OBSERVATION_BINDINGS, "obs-revenue-aug", binding)
    with pytest.raises(module.ContractError, match="obs-revenue-aug has no required decision_time binding"):
        module.validate_candidate(O2)


def test_extended_coverage_pin_may_add_an_unbound_observation(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    observations = documents["fixtures/observations.json"]["observations"]
    extra = json.loads(json.dumps(next(item for item in observations if item["observation_id"] == "obs-revenue-jul")))
    extra["observation_id"] = "obs-revenue-jul-extra"
    observations.append(extra)
    edges = documents["fixtures/lineage.json"]["edges"]
    edge = json.loads(json.dumps(next(item for item in edges if item["observation_id"] == "obs-revenue-jul")))
    edge.update(lineage_id="lin-revenue-jul-extra", observation_id="obs-revenue-jul-extra")
    edges.append(edge)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    _sync_coverage_pin(module, copy)
    assert module.validate_candidate(copy)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


def test_later_identity_without_prior_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    identity = next(
        item for item in documents["fixtures/identities.json"]["identities"] if item["identity_id"] == "idn-eq-v2"
    )
    identity["prior_identity_id"] = None
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="identity version does not name its prior"):
        module.validate_candidate(copy)


# settlement_sources is Event-level metadata copied onto each market identity.
# The candidate cannot order two collections for one (venue, event_ticker), so it
# refuses them; identity_version is per market_ticker and is not an Event version.
# This scope is settlement_sources only and makes no provider-immutability claim.
#
# Fiscal/reporting-period certification remains deferred. The coverage digest and
# contract pins protect the researched synthetic corpus, including its fy2023 key
# label (see test_fact_period_relabel_cannot_bypass_the_unchanged_contract). They
# do not certify general reporting-period truth, and no test here infers a period.
_EVENT_MARKETS = ("idn-ev-b1", "idn-ev-void", "idn-ev-d1")


def _event_identity(documents: dict, identity_id: str) -> dict:
    return next(
        item for item in documents["fixtures/identities.json"]["identities"] if item["identity_id"] == identity_id
    )


def test_unchanged_fixture_shares_one_settlement_source_collection_per_event() -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    events = {_event_identity(documents, identity_id)["event"]["event_ticker"] for identity_id in _EVENT_MARKETS}
    # The sibling-conflict cases below rely on these markets sharing one event.
    assert events == {"KXSYNTH-26JAN01"}
    assert module.validate_candidate(O2)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


@pytest.mark.parametrize(
    ("identity_id", "field", "value"),
    [
        ("idn-ev-void", "name", "Other determination source"),
        ("idn-ev-d1", "url", "https://example.invalid/other-determination"),
    ],
)
def test_sibling_market_settlement_source_conflict_is_rejected(
    tmp_path: Path, identity_id: str, field: str, value: str
) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    _event_identity(documents, identity_id)["event"]["settlement_sources"][0][field] = value
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(
        module.ContractError,
        match=f"{identity_id} settlement_sources conflict with idn-ev-b1 for event kalshi_demo:KXSYNTH-26JAN01",
    ):
        module.validate_candidate(copy)


def test_reordered_settlement_source_collection_is_consistent(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    backup = {"name": "Synthetic backup source", "url": "https://example.invalid/kxsynth-backup"}
    for identity_id in _EVENT_MARKETS:
        _event_identity(documents, identity_id)["event"]["settlement_sources"].append(dict(backup))
    # Array order is not a priority rule: the same collection in another order agrees.
    _event_identity(documents, "idn-ev-void")["event"]["settlement_sources"].reverse()
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


def test_separate_events_may_differ_in_settlement_sources(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    event = _event_identity(documents, "idn-ev-d1")["event"]
    event["event_ticker"] = "KXSYNTH-26JAN02"
    event["settlement_sources"] = [
        {"name": "Other determination source", "url": "https://example.invalid/other-determination"}
    ]
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    assert module.validate_candidate(copy)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]


@pytest.mark.parametrize("conflict", [False, True])
def test_later_market_version_settlement_source_conflict_is_ambiguous(tmp_path: Path, conflict: bool) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    later = json.loads(json.dumps(_event_identity(documents, "idn-ev-b1")))
    later.update(identity_id="idn-ev-b1-v2", identity_version=2, prior_identity_id="idn-ev-b1")
    if conflict:
        later["event"]["settlement_sources"][0]["url"] = "https://example.invalid/other-determination"
    documents["fixtures/identities.json"]["identities"].append(later)
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    if not conflict:
        # The version lineage itself is valid; only the source conflict is refused.
        assert module.validate_candidate(copy)[:2] == ["O2 candidate corpus: ok", "O2 maturity: researched"]
        return
    with pytest.raises(
        module.ContractError,
        match="idn-ev-b1-v2 settlement_sources conflict with idn-ev-b1 for event kalshi_demo:KXSYNTH-26JAN01",
    ):
        module.validate_candidate(copy)


def test_frames_qualification_decision_is_enforced(tmp_path: Path) -> None:
    module = _load_validator()
    documents = _candidate_documents(module)
    feed = next(
        item for item in documents["fixtures/qualification_matrix.json"]["feeds"] if item["feed_id"] == "sec_edgar_frames"
    )
    feed["qualification"] = "public_documented"
    copy = _write_modified_candidate(module, tmp_path / "O2", documents)
    with pytest.raises(module.ContractError, match="sec_edgar_frames qualification decision does not match"):
        module.validate_candidate(copy)


def test_o2_validator_does_not_import_providers() -> None:
    text = VALIDATOR.read_text(encoding="utf-8")
    for banned in ("urllib", "httpx", "requests", "socket", "websockets", "boto3", "keyring"):
        assert f"import {banned}" not in text
        assert f"from {banned}" not in text


def test_accepted_csl212_documents_and_o2_maturity_stay_researched() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    receipt = program["authority"]["acceptance_receipt"]["accepted_contracts"]
    for path, digest in receipt.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    outcome = next(item for item in program["outcomes"] if item["key"] == "O2")
    assert outcome["linear_issue"] == "CSL-214"
    assert outcome["evidence"]["claimed"] == "researched"
    assert "contract_frozen" in outcome["evidence"]["not_claimed"]
    assert outcome["completion_gate_status"] == "pending_outcome_artifact"
