"""RW-11 deterministic text-delta precision evidence freezer and scorer.

This tool is intentionally outside the runtime composition.  It accepts only a predeclared
case manifest and an explicitly marked, isolated RawStore.  It has no database, network, mode,
broker, audit, or order capability.  A valid score remains evidence for human review; it never
closes RW-11 or grants execution authority.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core.clock import FrozenClock  # noqa: E402
from filings.cite.builder import build_citations  # noqa: E402
from filings.cite.resolver import resolve_all  # noqa: E402
from filings.delta import engine as delta_engine  # noqa: E402
from filings.edgar.fetch import archive_document_url  # noqa: E402
from filings.raw_store import RawStore  # noqa: E402

SCHEMA_VERSION = 1
ISOLATION_MARKER = ".rw11-isolated-readonly"
ISOLATION_MARKER_TEXT = "RW-11 isolated raw store\n"
LABELS = frozenset({"CORRECT", "INCORRECT", "ABSTAIN"})
FORMS = frozenset({"8-K", "10-Q", "10-K"})
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
CIK_RE = re.compile(r"^\d{10}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
CASE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")

REVIEW_COLUMNS = (
    "population_hash", "row_id", "case_id", "cik", "form_type",
    "current_accession", "prior_accession", "source_accession", "source_url",
    "current_url", "prior_url", "byte_start", "byte_end", "snippet_sha256",
    "snippet_preview", "human_source_ref", "material_change_verdict", "reviewer_notes",
    "reviewer", "reviewed_at", "independence_procedure",
)
LABEL_COLUMNS = (
    "population_hash", "row_id", "human_source_ref", "material_change_verdict",
    "reviewer_notes", "reviewer", "reviewed_at", "independence_procedure",
)


class EvidenceError(RuntimeError):
    """A malformed, ambiguous, or tampered evidence input."""


@dataclass(frozen=True)
class FilingSpec:
    cik: str
    accession_no: str
    form_type: str
    filing_date: date
    primary_doc: str
    raw_sha256: str

    @property
    def raw_ref(self) -> str:
        return self.accession_no

    @property
    def url(self) -> str:
        return archive_document_url(self.cik, self.accession_no, self.primary_doc)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Any) -> bytes:
    payload = _canonical_bytes(value) + b"\n"
    path.write_bytes(payload)
    return payload


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EvidenceError(
            f"{label} keys mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be an RFC3339 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvidenceError(f"{label} must be timezone-qualified UTC")
    return parsed


def _filing(value: object, label: str) -> FilingSpec:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    _exact_keys(
        value,
        {"cik", "accession_no", "form_type", "filing_date", "primary_doc", "raw_sha256"},
        label,
    )
    cik = value["cik"]
    accession = value["accession_no"]
    form = value["form_type"]
    primary = value["primary_doc"]
    raw_sha256 = value["raw_sha256"]
    if not isinstance(cik, str) or not CIK_RE.fullmatch(cik):
        raise EvidenceError(f"{label}.cik must be a zero-padded 10-digit CIK")
    if not isinstance(accession, str) or not ACCESSION_RE.fullmatch(accession):
        raise EvidenceError(f"{label}.accession_no is not canonical")
    if accession[:10] != cik:
        raise EvidenceError(f"{label}.accession_no is not bound to its CIK")
    if not isinstance(form, str) or form not in FORMS:
        raise EvidenceError(f"{label}.form_type must be one of {sorted(FORMS)}")
    if not isinstance(primary, str) or not primary or Path(primary).name != primary:
        raise EvidenceError(f"{label}.primary_doc must be a base filename")
    if not isinstance(raw_sha256, str) or not SHA_RE.fullmatch(raw_sha256):
        raise EvidenceError(f"{label}.raw_sha256 must be lowercase SHA-256")
    try:
        filed = date.fromisoformat(value["filing_date"])
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"{label}.filing_date must be YYYY-MM-DD") from exc
    return FilingSpec(cik, accession, form, filed, primary, raw_sha256)


def _load_cases(path: Path, now: datetime) -> tuple[dict, list[tuple[str, FilingSpec, FilingSpec | None]], str]:
    payload = path.read_bytes()
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"invalid case manifest JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise EvidenceError("case manifest must be an object")
    _exact_keys(
        manifest,
        {"schema_version", "package", "source_commit", "authored_at", "selected_by",
         "cutoff_date", "selection_rule", "universe_ciks", "cases"},
        "case manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["package"] != "RW-11":
        raise EvidenceError("unsupported case manifest schema/package")
    if not isinstance(manifest["source_commit"], str) or not COMMIT_RE.fullmatch(
        manifest["source_commit"]
    ):
        raise EvidenceError("source_commit must be a lowercase 40-hex commit")
    if _parse_utc(manifest["authored_at"], "authored_at") > now:
        raise EvidenceError("authored_at cannot be after freeze time")
    selected_by = manifest["selected_by"]
    if not isinstance(selected_by, str) or not selected_by.strip():
        raise EvidenceError("selected_by must identify the case selector")
    try:
        cutoff = date.fromisoformat(manifest["cutoff_date"])
    except (TypeError, ValueError) as exc:
        raise EvidenceError("cutoff_date must be YYYY-MM-DD") from exc
    rule = manifest["selection_rule"]
    if not isinstance(rule, str) or len(rule.strip()) < 20:
        raise EvidenceError("selection_rule must predeclare a substantive population rule")
    universe = manifest["universe_ciks"]
    if not isinstance(universe, list) or not universe:
        raise EvidenceError("universe_ciks must be a non-empty list")
    if any(not isinstance(cik, str) or not CIK_RE.fullmatch(cik) for cik in universe):
        raise EvidenceError("universe_ciks contains a non-canonical CIK")
    if universe != sorted(set(universe)):
        raise EvidenceError("universe_ciks must be sorted and unique")
    raw_cases = manifest["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvidenceError("cases must be a non-empty list")
    cases = []
    seen_ids: set[str] = set()
    seen_current: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        label = f"cases[{index}]"
        if not isinstance(raw_case, dict):
            raise EvidenceError(f"{label} must be an object")
        _exact_keys(raw_case, {"case_id", "current", "prior"}, label)
        case_id = raw_case["case_id"]
        if not isinstance(case_id, str) or not CASE_RE.fullmatch(case_id):
            raise EvidenceError(f"{label}.case_id is not canonical")
        if case_id in seen_ids:
            raise EvidenceError(f"duplicate case_id {case_id!r}")
        current = _filing(raw_case["current"], f"{label}.current")
        prior = None if raw_case["prior"] is None else _filing(raw_case["prior"], f"{label}.prior")
        if current.cik not in universe:
            raise EvidenceError(f"{case_id}: current CIK is outside universe_ciks")
        if current.filing_date <= cutoff:
            raise EvidenceError(f"{case_id}: current filing is not strictly post-cutoff")
        if current.accession_no in seen_current:
            raise EvidenceError(f"duplicate current accession {current.accession_no!r}")
        if prior is None and current.form_type != "8-K":
            raise EvidenceError(f"{case_id}: periodic filing requires a prior same-form filing")
        if prior is not None:
            if (prior.cik, prior.form_type) != (current.cik, current.form_type):
                raise EvidenceError(f"{case_id}: prior must have the same CIK and form")
            if (prior.filing_date, prior.accession_no) >= (
                current.filing_date, current.accession_no
            ):
                raise EvidenceError(f"{case_id}: prior must be chronologically earlier")
        seen_ids.add(case_id)
        seen_current.add(current.accession_no)
        cases.append((case_id, current, prior))
    return manifest, sorted(cases, key=lambda item: item[0]), _sha256(_canonical_bytes(manifest))


def _isolated_store(root: Path, filings: list[FilingSpec]) -> tuple[RawStore, dict[str, str]]:
    if root.is_symlink():
        raise EvidenceError("raw root must not be a symlink")
    root = root.resolve(strict=True)
    marker = root / ISOLATION_MARKER
    if marker.is_symlink() or marker.read_text(encoding="utf-8") != ISOLATION_MARKER_TEXT:
        raise EvidenceError(f"raw root lacks exact {ISOLATION_MARKER} isolation marker")
    store = RawStore(root, min_free_bytes=0)
    hashes = {}
    for filing in filings:
        directory = root / filing.accession_no
        for target in (directory, directory / "raw.bin", directory / "sha256"):
            if target.is_symlink():
                raise EvidenceError(f"symlinked raw evidence refused: {target.name}")
            resolved = target.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise EvidenceError("raw evidence path escapes isolated root") from exc
        store.resolve(filing.raw_ref)
        digest = store.stored_hash(filing.raw_ref)
        if not SHA_RE.fullmatch(digest):
            raise EvidenceError(f"invalid stored raw hash for {filing.accession_no}")
        if digest != filing.raw_sha256:
            raise EvidenceError(f"raw hash differs from predeclared case {filing.accession_no}")
        hashes[filing.accession_no] = digest
    return store, hashes


def _implementation_hashes() -> dict[str, str]:
    files = {ROOT / "tools" / "rw11_precision.py", APP / "filings" / "raw_store.py"}
    for directory in (APP / "filings" / "delta", APP / "filings" / "parse", APP / "filings" / "cite"):
        files.update(directory.rglob("*.py"))
    files.update({APP / "schemas" / "models" / "delta.py", APP / "schemas" / "models" / "citation.py"})
    return {
        path.relative_to(ROOT).as_posix(): _sha256(path.read_bytes())
        for path in sorted(files, key=lambda item: item.as_posix())
    }


def _preview(value: str) -> str:
    rendered = "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char) in {"Cc", "Cf"} else char
        for char in value
    )
    return "'" + rendered if rendered[:1] in {"=", "+", "-", "@"} else rendered


def _csv_bytes(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    import io

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="raise", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _freeze_rows(
    cases: list[tuple[str, FilingSpec, FilingSpec | None]], store: RawStore, now: datetime
) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    clock = FrozenClock(now)
    for case_id, current, prior in cases:
        production = delta_engine.compute_deltas(current, prior, store, [], [], clock=clock)
        current_parsed = delta_engine._parse_filing(current, store.resolve(current.raw_ref))
        if prior is None:
            details = delta_engine.extract_filing(current_parsed)
        else:
            prior_parsed = delta_engine._parse_filing(prior, store.resolve(prior.raw_ref))
            details = delta_engine._paired_text_deltas(current_parsed, prior_parsed)
        produced = {
            (row.raw_ref, row.source_ref, row.snippet_start_offset,
             row.snippet_end_offset, row.snippet): row
            for row in production if row.kind == "text-section"
        }
        detail_keys = {
            (item.raw_ref, item.source_ref, item.byte_start, item.byte_end, item.snippet)
            for item in details
        }
        if set(produced) != detail_keys or len(production) != len(details):
            raise EvidenceError(f"{case_id}: evidence replay diverged from production delta output")
        filings = {current.accession_no: current}
        if prior is not None:
            filings[prior.accession_no] = prior
        citations = build_citations(production, filings, clock=clock)
        failures = [result.reason for result in resolve_all(citations, store) if not result.ok]
        if failures:
            raise EvidenceError(f"{case_id}: citation precheck failed: {failures}")
        for item in details:
            key = (item.raw_ref, item.source_ref, item.byte_start, item.byte_end, item.snippet)
            row = produced[key]
            if row.delta_id in seen:
                raise EvidenceError(f"duplicate emitted delta across cases: {row.delta_id}")
            seen.add(row.delta_id)
            rows.append({
                "row_id": row.delta_id,
                "case_id": case_id,
                "kind": row.kind,
                "cik": current.cik,
                "form_type": current.form_type,
                "current_accession": current.accession_no,
                "prior_accession": None if prior is None else prior.accession_no,
                "source_accession": item.raw_ref,
                "source_url": row.edgar_url,
                "current_url": current.url,
                "prior_url": None if prior is None else prior.url,
                "byte_start": item.byte_start,
                "byte_end": item.byte_end,
                "snippet": item.snippet,
                "snippet_sha256": _sha256(item.snippet.encode("utf-8")),
                "machine_source_ref": item.source_ref,
                "machine_change_type": item.change_type,
                "machine_prior_text": item.prior_text,
                "machine_current_text": item.current_text,
                "machine_materiality_score": str(row.materiality_score),
            })
    return sorted(rows, key=lambda row: (row["case_id"], row["row_id"]))


def freeze(cases_path: Path, raw_root: Path, output_dir: Path, *, now: datetime | None = None) -> dict:
    now = datetime.now(UTC) if now is None else now
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise EvidenceError("freeze time must be timezone-qualified UTC")
    case_manifest, cases, cases_hash = _load_cases(Path(cases_path), now)
    filings = [filing for _, current, prior in cases for filing in (current, prior) if filing]
    store, raw_hashes = _isolated_store(Path(raw_root), filings)
    machine_rows = _freeze_rows(cases, store, now)
    implementation_hashes = _implementation_hashes()
    population_payload = {
        "cases_sha256": cases_hash,
        "implementation_sha256": implementation_hashes,
        "raw_sha256": dict(sorted(raw_hashes.items())),
        "rows": machine_rows,
    }
    population_hash = _sha256(_canonical_bytes(population_payload))

    review_rows = []
    label_rows = []
    for row in machine_rows:
        review = {column: "" for column in REVIEW_COLUMNS}
        for column in REVIEW_COLUMNS:
            if column in row and column not in {"snippet_preview"}:
                review[column] = "" if row[column] is None else row[column]
        review["population_hash"] = population_hash
        review["snippet_preview"] = _preview(row["snippet"])
        review_rows.append(review)
        label_rows.append({column: review[column] for column in LABEL_COLUMNS})
    review_bytes = _csv_bytes(REVIEW_COLUMNS, review_rows)
    label_bytes = _csv_bytes(LABEL_COLUMNS, label_rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package": "RW-11",
        "scope": "text-delta-precision-only",
        "authority": "none",
        "rw11_gate": "OPEN",
        "gate_closed": False,
        "frozen_at": now.isoformat(),
        "source_commit": case_manifest["source_commit"],
        "cases_sha256": cases_hash,
        "case_manifest": case_manifest,
        "population_hash": population_hash,
        "selection_rule": case_manifest["selection_rule"],
        "cutoff_date": case_manifest["cutoff_date"],
        "universe_ciks": case_manifest["universe_ciks"],
        "raw_sha256": dict(sorted(raw_hashes.items())),
        "implementation_sha256": implementation_hashes,
        "rows": machine_rows,
        "artifacts": {
            "review_sheet.csv": _sha256(review_bytes),
            "label_template.csv": _sha256(label_bytes),
        },
        "known_blockers": [
            "numeric/companyfacts evidence is not captured by this text-only package",
            "independent human labels are not present at freeze time",
            "a cited live material delta and human sufficiency decision remain required",
        ],
    }
    output = Path(output_dir)
    if output.exists():
        raise EvidenceError("output directory already exists; frozen evidence is write-once")
    output.parent.mkdir(parents=True, exist_ok=True)
    # mkdir is atomically exclusive on Windows, macOS, and Linux. A crash can leave an invalid
    # partial directory, but verify_freeze will refuse it and the path can never be overwritten.
    output.mkdir()
    (output / "review_sheet.csv").write_bytes(review_bytes)
    (output / "label_template.csv").write_bytes(label_bytes)
    manifest_bytes = _write_json(output / "machine_manifest.json", manifest)
    (output / "machine_manifest.sha256").write_text(
        _sha256(manifest_bytes) + "\n", encoding="ascii"
    )
    return manifest


def verify_freeze(freeze_dir: Path) -> dict:
    root = Path(freeze_dir).resolve(strict=True)
    manifest_path = root / "machine_manifest.json"
    payload = manifest_path.read_bytes()
    expected = (root / "machine_manifest.sha256").read_text(encoding="ascii").strip()
    failures = []
    if not SHA_RE.fullmatch(expected) or _sha256(payload) != expected:
        failures.append("machine manifest hash mismatch")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "failures": failures + ["invalid machine manifest JSON"]}
    if not isinstance(manifest, dict):
        return {"ok": False, "failures": failures + ["machine manifest must be an object"]}
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("package") != "RW-11"
        or manifest.get("scope") != "text-delta-precision-only"
    ):
        failures.append("manifest schema/package/scope changed")
    if (
        manifest.get("authority") != "none"
        or manifest.get("rw11_gate") != "OPEN"
        or manifest.get("gate_closed") is not False
    ):
        failures.append("manifest authority/gate boundary changed")
    artifacts = manifest.get("artifacts")
    expected_artifacts = {"review_sheet.csv", "label_template.csv"}
    if not isinstance(artifacts, dict) or set(artifacts) != expected_artifacts:
        failures.append("manifest artifact set changed")
        artifacts = {}
    case_manifest = manifest.get("case_manifest")
    cases_hash = manifest.get("cases_sha256")
    if not isinstance(case_manifest, dict) or not isinstance(cases_hash, str):
        failures.append("case manifest binding missing")
    elif _sha256(_canonical_bytes(case_manifest)) != cases_hash:
        failures.append("case manifest hash mismatch")
    rows = manifest.get("rows")
    raw_hashes = manifest.get("raw_sha256")
    implementation = manifest.get("implementation_sha256")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        failures.append("machine rows malformed")
        rows = []
    row_ids = [row.get("row_id") for row in rows]
    if any(not isinstance(row_id, str) for row_id in row_ids) or len(row_ids) != len(set(row_ids)):
        failures.append("machine row identities malformed or duplicated")
    if not isinstance(raw_hashes, dict) or not isinstance(implementation, dict):
        failures.append("raw/implementation bindings missing")
    else:
        population = {
            "cases_sha256": cases_hash,
            "implementation_sha256": implementation,
            "raw_sha256": raw_hashes,
            "rows": rows,
        }
        if _sha256(_canonical_bytes(population)) != manifest.get("population_hash"):
            failures.append("population hash mismatch")
    for name, digest in artifacts.items():
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            failures.append(f"artifact escapes freeze directory: {name}")
            continue
        try:
            actual = _sha256(target.read_bytes())
        except OSError:
            failures.append(f"artifact unreadable: {name}")
            continue
        if not isinstance(digest, str) or actual != digest:
            failures.append(f"artifact hash mismatch: {name}")
    return {"ok": not failures, "failures": failures, "manifest": manifest}


def _read_labels(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LABEL_COLUMNS:
            raise EvidenceError("label file columns differ from the frozen template")
        return list(reader)


def _wilson_lower(correct: int, total: int) -> float | None:
    if total == 0:
        return None
    z = 1.959963984540054
    p = correct / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - margin) / denominator


def _axis_stats(counts: dict[str, int]) -> dict[str, Any]:
    correct = counts["CORRECT"]
    incorrect = counts["INCORRECT"]
    abstained = counts["ABSTAIN"]
    scored = correct + incorrect
    total = scored + abstained
    return {
        "correct": correct,
        "incorrect": incorrect,
        "abstained": abstained,
        "precision": None if not scored else correct / scored,
        "worst_case_precision": None if not total else correct / total,
        "wilson_95_lower_bound": _wilson_lower(correct, scored),
        "point_precision_at_least_95_percent": bool(scored) and correct * 100 >= scored * 95,
        "worst_case_precision_at_least_95_percent": bool(total)
        and correct * 100 >= total * 95,
    }


def score(freeze_dir: Path, labels_path: Path, output_path: Path) -> dict:
    verified = verify_freeze(freeze_dir)
    if not verified["ok"]:
        raise EvidenceError(f"frozen evidence verification failed: {verified['failures']}")
    manifest = verified["manifest"]
    machine = {row["row_id"]: row for row in manifest["rows"]}
    labels = _read_labels(labels_path)
    by_id: dict[str, dict[str, str]] = {}
    for row in labels:
        row_id = row["row_id"]
        if row_id in by_id:
            raise EvidenceError(f"duplicate label row {row_id!r}")
        if row["population_hash"] != manifest["population_hash"]:
            raise EvidenceError(f"population hash mismatch for {row_id!r}")
        by_id[row_id] = row
    if set(by_id) != set(machine):
        raise EvidenceError(
            f"label row set mismatch: missing={sorted(set(machine) - set(by_id))}, "
            f"extra={sorted(set(by_id) - set(machine))}"
        )
    reviewers = {row["reviewer"].strip() for row in labels}
    dates = {row["reviewed_at"].strip() for row in labels}
    procedures = {row["independence_procedure"].strip() for row in labels}
    if len(reviewers) != 1 or not next(iter(reviewers), ""):
        raise EvidenceError("all labels require one consistent non-empty reviewer")
    if len(dates) != 1:
        raise EvidenceError("all labels require one consistent review timestamp")
    reviewed_at = _parse_utc(next(iter(dates), ""), "reviewed_at")
    if reviewed_at < _parse_utc(manifest["frozen_at"], "frozen_at"):
        raise EvidenceError("reviewed_at cannot precede the frozen population")
    if len(procedures) != 1 or len(next(iter(procedures), "")) < 20:
        raise EvidenceError("independence_procedure must be consistent and substantive")
    reviewer = next(iter(reviewers))
    selector = manifest["case_manifest"].get("selected_by", "").strip()
    if reviewer.casefold() == selector.casefold():
        raise EvidenceError("independent reviewer must differ from the case selector")

    axes = {
        "source_ref": {label: 0 for label in LABELS},
        "material_change": {label: 0 for label in LABELS},
    }
    per_row = []
    for row_id in sorted(machine):
        label = by_id[row_id]
        human_ref = label["human_source_ref"].strip()
        material = label["material_change_verdict"].strip().upper()
        notes = label["reviewer_notes"].strip()
        if not human_ref or material not in LABELS:
            raise EvidenceError(f"{row_id}: labels must be complete and canonical")
        locator = "ABSTAIN" if human_ref.upper() == "ABSTAIN" else (
            "CORRECT" if human_ref == machine[row_id]["machine_source_ref"] else "INCORRECT"
        )
        if (locator in {"INCORRECT", "ABSTAIN"} or material in {"INCORRECT", "ABSTAIN"}) and not notes:
            raise EvidenceError(f"{row_id}: incorrect/abstain labels require reviewer notes")
        axes["source_ref"][locator] += 1
        axes["material_change"][material] += 1
        per_row.append({"row_id": row_id, "source_ref": locator, "material_change": material})
    axis_stats = {name: _axis_stats(counts) for name, counts in axes.items()}
    correct = sum(stats["correct"] for stats in axis_stats.values())
    incorrect = sum(stats["incorrect"] for stats in axis_stats.values())
    abstained = sum(stats["abstained"] for stats in axis_stats.values())
    denominator = correct + incorrect
    total_fields = denominator + abstained
    forms = sorted({row["form_type"] for row in machine.values()})
    ciks = sorted({row["cik"] for row in machine.values()})
    numeric_present = any(row.get("kind") == "numeric" for row in machine.values())
    preliminary = {
        "minimum_30_rows": len(machine) >= 30,
        "minimum_3_issuers": len(ciks) >= 3,
        "all_supported_forms": set(forms) == FORMS,
        "numeric_kind_present": numeric_present,
        "abstentions_at_most_10_percent": bool(total_fields) and abstained * 10 <= total_fields,
        "source_ref_worst_case_precision_at_least_95_percent": axis_stats["source_ref"]
        ["worst_case_precision_at_least_95_percent"],
        "material_change_worst_case_precision_at_least_95_percent": axis_stats
        ["material_change"]["worst_case_precision_at_least_95_percent"],
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "package": "RW-11",
        "scope": manifest["scope"],
        "authority": "none",
        "rw11_gate": "OPEN",
        "gate_closed": False,
        "population_hash": manifest["population_hash"],
        "reviewer": reviewer,
        "selected_by": selector,
        "reviewed_at": next(iter(dates)),
        "correct_fields": correct,
        "incorrect_fields": incorrect,
        "abstained_fields": abstained,
        "precision_fraction": f"{correct}/{denominator}",
        "precision": None if not denominator else correct / denominator,
        "worst_case_precision": None if not total_fields else correct / total_fields,
        "wilson_95_lower_bound": _wilson_lower(correct, denominator),
        "precision_by_field": axis_stats,
        "row_count": len(machine),
        "ciks": ciks,
        "forms": forms,
        "preliminary_sufficiency": preliminary,
        "preliminary_sufficiency_met": all(preliminary.values()),
        "rows": per_row,
        "remaining_blockers": manifest["known_blockers"],
    }
    output = Path(output_path)
    if output.exists():
        raise EvidenceError("score output already exists; evidence is write-once")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise EvidenceError("score output already exists; evidence is write-once") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(_canonical_bytes(report) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_cmd = commands.add_parser("freeze")
    freeze_cmd.add_argument("--cases", type=Path, required=True)
    freeze_cmd.add_argument("--raw-root", type=Path, required=True)
    freeze_cmd.add_argument("--output-dir", type=Path, required=True)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--freeze-dir", type=Path, required=True)
    score_cmd = commands.add_parser("score")
    score_cmd.add_argument("--freeze-dir", type=Path, required=True)
    score_cmd.add_argument("--labels", type=Path, required=True)
    score_cmd.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze(args.cases, args.raw_root, args.output_dir)
            print(json.dumps({"population_hash": result["population_hash"],
                              "rows": len(result["rows"]), "rw11_gate": "OPEN"}))
            return 0
        if args.command == "verify":
            result = verify_freeze(args.freeze_dir)
            print(json.dumps({key: value for key, value in result.items() if key != "manifest"}))
            return 0 if result["ok"] else 2
        result = score(args.freeze_dir, args.labels, args.output)
        print(json.dumps({
            "precision": result["precision"],
            "row_count": result["row_count"],
            "preliminary_sufficiency_met": result["preliminary_sufficiency_met"],
            "authority": "none",
            "rw11_gate": "OPEN_NOT_CLOSED",
        }))
        return 0
    except (EvidenceError, OSError, ValueError) as exc:
        print(f"RW-11 evidence refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
