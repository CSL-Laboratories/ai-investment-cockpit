"""Hermetic tests for the RW-11 prospective Company Facts capture."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = ROOT / "tools" / "rw11_companyfacts.py"
SPEC = importlib.util.spec_from_file_location("rw11_companyfacts_tool", TOOL_PATH)
rw11 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rw11
SPEC.loader.exec_module(rw11)

NOW = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)
CIK = "0000000001"
CIK2 = "0000000002"
ACCESSION = "0000000001-26-000001"


def _payload(cik=CIK, *, accession=ACCESSION, filed="2026-08-19", form="10-Q") -> bytes:
    return json.dumps({
        "cik": int(cik),
        "entityName": "Example Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [{
                            "start": "2026-04-01", "end": "2026-06-30", "val": 123,
                            "accn": accession, "fy": 2026, "fp": "Q2", "form": form,
                            "filed": filed, "frame": "CY2026Q2",
                        }]
                    },
                }
            }
        },
    }, separators=(",", ":")).encode()


def _plan(path: Path, *, ciks=None, cutoff="2026-08-18") -> Path:
    value = {
        "schema_version": 1,
        "package": "RW-11",
        "source_commit": "a" * 40,
        "authored_at": "2026-08-19T13:00:00+00:00",
        "selected_by": "Prospective Census Coordinator",
        "cutoff_date": cutoff,
        "selection_rule": "Capture every CIK in the frozen universe without outcome filtering.",
        "universe_ciks": ciks or [CIK],
        "eligible_forms": ["10-K", "10-Q"],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class Fetcher:
    def __init__(self, payloads, fail_at=None):
        self.payloads = payloads
        self.fail_at = fail_at
        self.requests = []
        self.bytes_received = 0

    def get_bytes(self, url):
        self.requests.append(url)
        if self.fail_at == url:
            raise RuntimeError("network stopped")
        payload = self.payloads[url]
        self.bytes_received += len(payload)
        return payload


def _capture(tmp_path: Path, name="capture"):
    plan = _plan(tmp_path / f"{name}.json")
    fetcher = Fetcher({rw11.companyfacts_url(CIK): _payload()})
    output = tmp_path / name
    manifest = rw11.collect(plan, output, fetcher=fetcher, now=NOW)
    return output, manifest, fetcher


def test_capture_is_exact_hash_bound_and_explicitly_non_authoritative(tmp_path):
    output, manifest, fetcher = _capture(tmp_path)

    assert fetcher.requests == [rw11.companyfacts_url(CIK)]
    assert manifest["authority"] == "none"
    assert manifest["rw11_gate"] == "OPEN"
    assert manifest["gate_closed"] is False
    assert manifest["scope"] == rw11.SCOPE
    assert manifest["capture_started_at"] == NOW.isoformat()
    assert manifest["captures"][0]["fetched_at"] == NOW.isoformat()
    assert manifest["captured_at"] == NOW.isoformat()
    assert manifest["eligible_accession_count"] == 1
    assert rw11.verify(output)["ok"] is True
    stored = rw11.RawStore(output / "raw", min_free_bytes=0).resolve(f"companyfacts-{CIK}")
    assert stored == _payload()
    assert manifest["captures"][0]["payload_sha256"] == rw11._sha256(stored)


def test_capture_is_byte_deterministic_for_frozen_inputs_and_time(tmp_path):
    first, first_manifest, _ = _capture(tmp_path, "first")
    second, second_manifest, _ = _capture(tmp_path, "second")

    assert first_manifest == second_manifest
    for name in (rw11.PLAN_NAME, rw11.MANIFEST_NAME, rw11.MANIFEST_HASH_NAME):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_capture_refuses_response_identity_mismatch_and_duplicate_json_keys(tmp_path):
    plan = _plan(tmp_path / "plan.json")
    wrong = Fetcher({rw11.companyfacts_url(CIK): _payload(CIK2)})
    with pytest.raises(rw11.EvidenceError, match="does not match"):
        rw11.collect(plan, tmp_path / "wrong", fetcher=wrong, now=NOW)
    assert not (tmp_path / "wrong").exists()

    duplicate = b'{"cik":1,"cik":1,"facts":{}}'
    fetcher = Fetcher({rw11.companyfacts_url(CIK): duplicate})
    with pytest.raises(rw11.EvidenceError, match="duplicate JSON key"):
        rw11.collect(plan, tmp_path / "duplicate", fetcher=fetcher, now=NOW)


def test_capture_refuses_partial_publish_and_overwrite(tmp_path):
    ciks = [CIK, CIK2]
    plan = _plan(tmp_path / "plan.json", ciks=ciks)
    urls = {rw11.companyfacts_url(cik): _payload(cik) for cik in ciks}
    failing = Fetcher(urls, fail_at=rw11.companyfacts_url(CIK2))
    with pytest.raises(RuntimeError, match="network stopped"):
        rw11.collect(plan, tmp_path / "partial", fetcher=failing, now=NOW)
    assert not (tmp_path / "partial").exists()

    output, _, fetcher = _capture(tmp_path, "complete")
    with pytest.raises(rw11.EvidenceError, match="already exists"):
        rw11.collect(plan, output, fetcher=fetcher, now=NOW)


def test_plan_is_predeclared_canonical_and_strictly_before_capture(tmp_path):
    unsorted = _plan(tmp_path / "unsorted.json", ciks=[CIK2, CIK])
    fetcher = Fetcher({})
    with pytest.raises(rw11.EvidenceError, match="sorted and unique"):
        rw11.collect(unsorted, tmp_path / "bad", fetcher=fetcher, now=NOW)

    same_day = _plan(tmp_path / "same-day.json", cutoff="2026-08-19")
    with pytest.raises(rw11.EvidenceError, match="must precede"):
        rw11.collect(same_day, tmp_path / "same", fetcher=fetcher, now=NOW)


def test_quiet_capture_is_reachability_only_not_a_precision_pass(tmp_path):
    plan = _plan(tmp_path / "plan.json", cutoff="2026-08-17")
    prior = _payload(filed="2026-08-17")
    fetcher = Fetcher({rw11.companyfacts_url(CIK): prior})
    output = tmp_path / "quiet"
    manifest = rw11.collect(plan, output, fetcher=fetcher, now=NOW)

    assert manifest["eligible_accession_count"] == 0
    verified = rw11.verify(output)
    assert verified["eligible_accessions"] == 0
    assert verified["rw11_gate"] == "OPEN_NOT_CLOSED"


def test_verify_rejects_raw_tamper_manifest_reseal_and_extra_artifact(tmp_path):
    output, _, _ = _capture(tmp_path, "raw-tamper")
    blob = output / "raw" / f"companyfacts-{CIK}" / "raw.bin"
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(rw11.EvidenceError, match="integrity failure"):
        rw11.verify(output)

    output, _, _ = _capture(tmp_path, "manifest-tamper")
    manifest_path = output / rw11.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["authority"] = "paper"
    payload = rw11._canonical_bytes(manifest) + b"\n"
    manifest_path.write_bytes(payload)
    (output / rw11.MANIFEST_HASH_NAME).write_text(rw11._sha256(payload) + "\n")
    with pytest.raises(rw11.EvidenceError, match="safety boundary changed"):
        rw11.verify(output)

    output, _, _ = _capture(tmp_path, "extra")
    (output / "unexpected.txt").write_text("not declared")
    with pytest.raises(rw11.EvidenceError, match="artifact set changed"):
        rw11.verify(output)


def test_verify_rejects_collection_and_summary_tamper_even_when_resealed(tmp_path):
    output, _, _ = _capture(tmp_path)
    manifest_path = output / rw11.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["captures"][0]["eligible_accessions"] = []
    manifest["eligible_accession_count"] = 0
    manifest["collection_hash"] = rw11._sha256(
        rw11._canonical_bytes(rw11._collection_hash_value(manifest))
    )
    payload = rw11._canonical_bytes(manifest) + b"\n"
    manifest_path.write_bytes(payload)
    (output / rw11.MANIFEST_HASH_NAME).write_text(rw11._sha256(payload) + "\n")
    with pytest.raises(rw11.EvidenceError, match="capture summary mismatch"):
        rw11.verify(output)


def test_verify_rejects_blocker_or_fetch_accounting_reseal(tmp_path):
    for field, value, match in (
        ("known_blockers", [], "blocker set changed"),
        ("fetch_bytes_received", 0, "byte accounting is incomplete"),
    ):
        output, _, _ = _capture(tmp_path, field)
        manifest_path = output / rw11.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[field] = value
        payload = rw11._canonical_bytes(manifest) + b"\n"
        manifest_path.write_bytes(payload)
        (output / rw11.MANIFEST_HASH_NAME).write_text(rw11._sha256(payload) + "\n")
        with pytest.raises(rw11.EvidenceError, match=match):
            rw11.verify(output)


def test_verify_rejects_linked_package_root_and_inner_artifact(tmp_path):
    output, _, _ = _capture(tmp_path, "real")
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(output, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this host cannot create a directory symlink: {exc}")
    assert rw11._is_linklike(linked_root) is True
    with pytest.raises(rw11.EvidenceError, match="root cannot be a link"):
        rw11.verify(linked_root)

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    inner = output / "linked-artifact"
    inner.symlink_to(outside)
    with pytest.raises(rw11.EvidenceError, match="contains link or reparse point"):
        rw11.verify(output)


def test_live_fetcher_requires_fixed_environment_variable(monkeypatch):
    monkeypatch.delenv("COCKPIT_SEC_USER_AGENT", raising=False)
    args = type("Args", (), {
        "rate_per_second": 2.0,
        "max_response_bytes": 100,
        "max_total_bytes": 200,
    })()
    with pytest.raises(rw11.EvidenceError, match="required for SEC fair access"):
        rw11._live_fetcher(args)


def test_tool_has_no_database_mode_risk_broker_or_order_imports():
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = ("storage", "sqlalchemy", "orchestrator", "risk", "brokers", "config")
    assert not any(module.startswith(forbidden) for module in imports)


def test_cli_verify_reports_open_gate(tmp_path, capsys):
    output, _, _ = _capture(tmp_path)
    assert rw11.main(["verify", "--package-dir", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["authority"] == "none"
    assert summary["rw11_gate"] == "OPEN_NOT_CLOSED"
