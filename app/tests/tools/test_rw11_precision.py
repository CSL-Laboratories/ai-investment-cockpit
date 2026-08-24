"""Hermetic tests for the RW-11 filing-precision evidence tool."""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = ROOT / "tools" / "rw11_precision.py"
SPEC = importlib.util.spec_from_file_location("rw11_precision_tool", TOOL_PATH)
rw11 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rw11
SPEC.loader.exec_module(rw11)

NOW = datetime(2026, 8, 19, 1, 0, tzinfo=UTC)
CIK = "0000000001"
ACCESSION = "0000000001-26-000001"
RAW = (
    b"<html><body>"
    b"<p>Item 1.01 Entry into a Material Definitive Agreement</p>"
    b"<p>On August 18, 2026, Example Corp entered a material agreement to acquire assets "
    b"for cash.</p>"
    b"<p>Item 9.01 Financial Statements and Exhibits</p>"
    b"<p>The agreement is filed as Exhibit 10.1 to this report.</p>"
    b"</body></html>"
)


def _filing(
    *, accession=ACCESSION, form="8-K", filed="2026-08-18", primary="example.htm",
    raw_sha256=None,
):
    return {
        "cik": CIK,
        "accession_no": accession,
        "form_type": form,
        "filing_date": filed,
        "primary_doc": primary,
        "raw_sha256": raw_sha256 or rw11._sha256(RAW),
    }


def _write_cases(path: Path, *, current=None, prior=None, cutoff="2026-08-17") -> Path:
    value = {
        "schema_version": 1,
        "package": "RW-11",
        "source_commit": "a" * 40,
        "authored_at": "2026-08-18T23:00:00+00:00",
        "selected_by": "Sample Coordinator",
        "cutoff_date": cutoff,
        "selection_rule": "Census every supported filing in the frozen one-CIK fixture universe.",
        "universe_ciks": [CIK],
        "cases": [{"case_id": "example-8k", "current": current or _filing(), "prior": prior}],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _write_raw(root: Path, *, raw=RAW) -> Path:
    root.mkdir()
    (root / rw11.ISOLATION_MARKER).write_text(rw11.ISOLATION_MARKER_TEXT, encoding="utf-8")
    rw11.RawStore(root, min_free_bytes=0).write(ACCESSION, raw)
    return root


def _freeze(tmp_path: Path, name="freeze"):
    cases = _write_cases(tmp_path / f"{name}-cases.json")
    raw = _write_raw(tmp_path / f"{name}-raw")
    output = tmp_path / name
    manifest = rw11.freeze(cases, raw, output, now=NOW)
    return output, manifest


def _write_labels(freeze_dir: Path, path: Path, mutate=None) -> Path:
    with (freeze_dir / "label_template.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    manifest = json.loads((freeze_dir / "machine_manifest.json").read_text(encoding="utf-8"))
    machine = {row["row_id"]: row for row in manifest["rows"]}
    for row in rows:
        row["human_source_ref"] = machine[row["row_id"]]["machine_source_ref"]
        row["material_change_verdict"] = "CORRECT"
        row["reviewer"] = "Independent Reviewer"
        row["reviewed_at"] = "2026-08-19T02:00:00+00:00"
        row["independence_procedure"] = "Machine answers remained sealed until labels were final."
    if mutate is not None:
        mutate(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rw11.LABEL_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_freeze_is_blind_hash_bound_and_explicitly_non_authoritative(tmp_path):
    output, manifest = _freeze(tmp_path)

    assert len(manifest["rows"]) == 2
    assert manifest["authority"] == "none"
    assert manifest["rw11_gate"] == "OPEN"
    assert manifest["gate_closed"] is False
    assert manifest["scope"] == "text-delta-precision-only"
    assert not any(
        platform in blocker
        for blocker in manifest["known_blockers"]
        for platform in ("Ubuntu", "macOS", "GitHub")
    )
    assert rw11.verify_freeze(output)["ok"] is True

    header = (output / "review_sheet.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "machine_source_ref" not in header
    assert "machine_change_type" not in header
    assert "machine_materiality_score" not in header
    assert "human_source_ref" in header
    assert all(row["source_accession"] == ACCESSION for row in manifest["rows"])


def test_freeze_is_byte_deterministic_with_a_frozen_clock(tmp_path):
    first, first_manifest = _freeze(tmp_path, "first")
    second_cases = _write_cases(tmp_path / "second-cases.json")
    second_raw = _write_raw(tmp_path / "second-raw")
    second = tmp_path / "second"
    second_manifest = rw11.freeze(second_cases, second_raw, second, now=NOW)

    assert first_manifest == second_manifest
    for name in ("machine_manifest.json", "machine_manifest.sha256", "review_sheet.csv",
                 "label_template.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_freeze_refuses_raw_tamper_and_missing_isolation_marker(tmp_path):
    cases = _write_cases(tmp_path / "cases.json")
    raw = _write_raw(tmp_path / "raw")
    (raw / ACCESSION / "raw.bin").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="integrity check FAILED"):
        rw11.freeze(cases, raw, tmp_path / "tampered-freeze", now=NOW)

    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    rw11.RawStore(unmarked, min_free_bytes=0).write(ACCESSION, RAW)
    with pytest.raises((rw11.EvidenceError, FileNotFoundError)):
        rw11.freeze(cases, unmarked, tmp_path / "unmarked-freeze", now=NOW)

    wrong_hash = _write_cases(
        tmp_path / "wrong-hash.json", current=_filing(raw_sha256="0" * 64)
    )
    marked = _write_raw(tmp_path / "marked")
    with pytest.raises(rw11.EvidenceError, match="predeclared case"):
        rw11.freeze(wrong_hash, marked, tmp_path / "wrong-hash-freeze", now=NOW)


def test_freeze_refuses_overwrite_post_cutoff_violation_and_unpaired_periodic(tmp_path):
    output, _ = _freeze(tmp_path)
    cases = tmp_path / "freeze-cases.json"
    raw = tmp_path / "freeze-raw"
    with pytest.raises(rw11.EvidenceError, match="already exists"):
        rw11.freeze(cases, raw, output, now=NOW)

    old = _write_cases(tmp_path / "old.json", cutoff="2026-08-18")
    with pytest.raises(rw11.EvidenceError, match="post-cutoff"):
        rw11.freeze(old, raw, tmp_path / "old-freeze", now=NOW)

    periodic = _write_cases(
        tmp_path / "periodic.json", current=_filing(form="10-Q"), prior=None
    )
    with pytest.raises(rw11.EvidenceError, match="requires a prior"):
        rw11.freeze(periodic, raw, tmp_path / "periodic-freeze", now=NOW)


def test_manifest_or_review_sheet_tamper_fails_verification(tmp_path):
    output, _ = _freeze(tmp_path)
    sheet = output / "review_sheet.csv"
    sheet.write_bytes(sheet.read_bytes() + b"tampered")
    result = rw11.verify_freeze(output)
    assert result["ok"] is False
    assert "artifact hash mismatch: review_sheet.csv" in result["failures"]


def test_resealed_missing_artifacts_and_population_tamper_fail_verification(tmp_path):
    output, _ = _freeze(tmp_path)
    manifest_path = output / "machine_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = {}
    manifest["rows"][0]["machine_source_ref"] = "laundered"
    payload = rw11._canonical_bytes(manifest) + b"\n"
    manifest_path.write_bytes(payload)
    (output / "machine_manifest.sha256").write_text(
        rw11._sha256(payload) + "\n", encoding="ascii"
    )
    result = rw11.verify_freeze(output)
    assert result["ok"] is False
    assert "manifest artifact set changed" in result["failures"]
    assert "population hash mismatch" in result["failures"]


def test_score_counts_fields_exactly_but_never_closes_rw11(tmp_path):
    output, _ = _freeze(tmp_path)
    labels = _write_labels(output, tmp_path / "labels.csv")
    report = rw11.score(output, labels, tmp_path / "score.json")

    assert report["correct_fields"] == 4
    assert report["incorrect_fields"] == 0
    assert report["precision_fraction"] == "4/4"
    assert report["precision"] == 1.0
    assert report["precision_by_field"]["source_ref"]["precision"] == 1.0
    assert report["precision_by_field"]["material_change"]["precision"] == 1.0
    assert report["worst_case_precision"] == 1.0
    assert report["preliminary_sufficiency"]["minimum_30_rows"] is False
    assert report["preliminary_sufficiency"]["numeric_kind_present"] is False
    assert report["preliminary_sufficiency_met"] is False
    assert report["authority"] == "none"
    assert report["rw11_gate"] == "OPEN"
    assert report["gate_closed"] is False
    assert report["scope"] == "text-delta-precision-only"


def test_score_includes_false_positive_and_wrong_locator_in_denominator(tmp_path):
    output, _ = _freeze(tmp_path)

    def wrong(rows):
        rows[0]["human_source_ref"] = "item_2.02"
        rows[0]["material_change_verdict"] = "INCORRECT"
        rows[0]["reviewer_notes"] = "The emitted row is boilerplate under a different item."

    labels = _write_labels(output, tmp_path / "labels.csv", wrong)
    report = rw11.score(output, labels, tmp_path / "score.json")
    assert report["correct_fields"] == 2
    assert report["incorrect_fields"] == 2
    assert report["precision_fraction"] == "2/4"
    assert report["precision_by_field"]["source_ref"]["precision"] == 0.5
    assert report["precision_by_field"]["material_change"]["precision"] == 0.5


@pytest.mark.parametrize("attack", ["missing", "duplicate", "population"])
def test_score_refuses_row_set_and_population_attacks(tmp_path, attack):
    output, _ = _freeze(tmp_path)

    def mutate(rows):
        if attack == "missing":
            rows.pop()
        elif attack == "duplicate":
            rows.append(dict(rows[0]))
        else:
            rows[0]["population_hash"] = "0" * 64

    labels = _write_labels(output, tmp_path / "labels.csv", mutate)
    with pytest.raises(rw11.EvidenceError):
        rw11.score(output, labels, tmp_path / "score.json")


def test_score_requires_notes_for_abstentions_and_does_not_overwrite(tmp_path):
    output, _ = _freeze(tmp_path)

    def abstain_without_notes(rows):
        rows[0]["human_source_ref"] = "ABSTAIN"

    labels = _write_labels(output, tmp_path / "labels.csv", abstain_without_notes)
    with pytest.raises(rw11.EvidenceError, match="require reviewer notes"):
        rw11.score(output, labels, tmp_path / "score.json")

    valid = _write_labels(output, tmp_path / "valid.csv")
    score_path = tmp_path / "score.json"
    rw11.score(output, valid, score_path)
    with pytest.raises(rw11.EvidenceError, match="already exists"):
        rw11.score(output, valid, score_path)


def test_score_abstention_is_a_failure_in_the_conservative_floor(tmp_path):
    output, _ = _freeze(tmp_path)

    def abstain(rows):
        rows[0]["human_source_ref"] = "ABSTAIN"
        rows[0]["material_change_verdict"] = "ABSTAIN"
        rows[0]["reviewer_notes"] = "The source context does not support an independent verdict."

    labels = _write_labels(output, tmp_path / "labels.csv", abstain)
    report = rw11.score(output, labels, tmp_path / "score.json")
    assert report["precision"] == 1.0
    assert report["worst_case_precision"] == 0.5
    assert report["precision_by_field"]["source_ref"]["worst_case_precision"] == 0.5
    assert report["preliminary_sufficiency"]["abstentions_at_most_10_percent"] is False


def test_score_requires_reviewer_independence_and_post_freeze_timestamp(tmp_path):
    output, _ = _freeze(tmp_path)

    def same_person(rows):
        for row in rows:
            row["reviewer"] = "sample coordinator"

    labels = _write_labels(output, tmp_path / "same.csv", same_person)
    with pytest.raises(rw11.EvidenceError, match="differ from the case selector"):
        rw11.score(output, labels, tmp_path / "same-score.json")

    def too_early(rows):
        for row in rows:
            row["reviewed_at"] = "2026-08-18T23:59:00+00:00"

    labels = _write_labels(output, tmp_path / "early.csv", too_early)
    with pytest.raises(rw11.EvidenceError, match="cannot precede"):
        rw11.score(output, labels, tmp_path / "early-score.json")


def test_preview_neutralizes_spreadsheet_and_unicode_controls():
    assert rw11._preview("=2+2").startswith("'")
    assert rw11._preview("safe\u202efile").endswith("\\u202efile")
    assert "\\u000a" in rw11._preview("line1\nline2")


def test_tool_has_no_direct_database_engine_or_order_path_imports():
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = ("storage", "orchestrator", "brokers", "risk", "sqlalchemy", "config")
    assert not any(module.startswith(forbidden) for module in imports)


def test_cli_reports_open_gate(tmp_path, capsys):
    output, _ = _freeze(tmp_path)
    assert rw11.main(["verify", "--freeze-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    labels = _write_labels(output, tmp_path / "labels.csv")
    assert rw11.main([
        "score", "--freeze-dir", str(output), "--labels", str(labels),
        "--output", str(tmp_path / "score.json"),
    ]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["preliminary_sufficiency_met"] is False
    assert summary["rw11_gate"] == "OPEN_NOT_CLOSED"
    assert summary["authority"] == "none"
