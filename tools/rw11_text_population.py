"""RW-11 prospective SEC text-population evidence collector.

This operator-only command executes a previously witnessed, outcome-independent population
plan. It uses only the existing fair-access ``SecFetcher`` GET path, preserves exact submissions
and filing bytes outside the operational database, and emits a case manifest for the separate
offline ``rw11_precision`` freezer. It never grants strategy, paper, live, or autonomous authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from filings.edgar.fetch import SecFetchError, SecFetcher, archive_document_url, submissions_url  # noqa: E402
from filings.raw_store import RawStore, RawStoreError  # noqa: E402

SCHEMA_VERSION = 1
PACKAGE = "RW-11"
SCOPE = "prospective-text-delta-population-capture-only"
PLAN_SCOPE = "prospective-text-delta-population-plan-only"
WITNESS_SCOPE = "prospective-text-delta-population-plan-chronology-witness"
PREFLIGHT_SCOPE = "prospective-text-delta-population-safety-preflight"
PLAN_NAME = "population_plan.json"
WITNESS_NAME = "witness_receipt.json"
PREFLIGHT_NAME = "safety_preflight.json"
CASES_NAME = "case_manifest.json"
MANIFEST_NAME = "capture_manifest.json"
MANIFEST_HASH_NAME = "capture_manifest.sha256"
ISOLATION_MARKER = ".rw11-isolated-readonly"
ISOLATION_MARKER_TEXT = "RW-11 isolated raw store\n"
FORMS = ("8-K", "10-K", "10-Q")
PERIODIC_FORMS = frozenset({"10-K", "10-Q"})
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
CIK_RE = re.compile(r"^\d{10}$")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
SNOWFLAKE_RE = re.compile(r"^\d{15,22}$")
PREFLIGHT_MAX_AGE = timedelta(minutes=15)
KNOWN_BLOCKERS = (
    "independent blind human labels at or above 95 percent remain required",
    "a meaningful emitted population of at least 30 rows across at least 3 issuers remains required",
    "a separately bounded numeric Company Facts replay and citation package remains required",
    "a cited prospective material delta remains required",
)
IMPLEMENTATION_PATHS = (
    "app/filings/edgar/fetch.py",
    "app/filings/edgar/submissions_poll.py",
    "app/filings/raw_store.py",
    "tools/rw11_precision.py",
    "tools/rw11_text_population.py",
)


class EvidenceError(RuntimeError):
    """A malformed, incomplete, ambiguous, or tampered evidence input."""


@dataclass(frozen=True)
class FilingRow:
    cik: str
    accession: str
    form: str
    filing_date: date
    primary_document: str

    @property
    def url(self) -> str:
        return archive_document_url(self.cik, self.accession, self.primary_document)

    @property
    def chronology(self) -> tuple[date, str]:
        return self.filing_date, self.accession


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Any) -> bytes:
    payload = _canonical_bytes(value) + b"\n"
    path.write_bytes(payload)
    return payload


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    if set(value) != expected:
        raise EvidenceError(
            f"{label} keys mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )
    return value


def _strict_json(payload: bytes, label: str) -> Any:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value):
        raise EvidenceError(f"{label} contains non-finite JSON number {value!r}")

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"{label} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label} is invalid JSON: {exc}") from exc


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvidenceError(f"{label} must be timezone-qualified UTC")
    return parsed


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EvidenceError(f"{label} must be a positive integer")
    return value


def _load_plan(path: Path, *, now: datetime, expected_sha256: str) -> tuple[dict, bytes]:
    if SHA_RE.fullmatch(str(expected_sha256)) is None:
        raise EvidenceError("expected plan hash must be lowercase SHA-256")
    payload = Path(path).read_bytes()
    if _sha256(payload) != expected_sha256:
        raise EvidenceError("population plan hash differs from the witnessed hash")
    plan = _strict_json(payload, "population plan")
    plan = _exact_keys(
        plan,
        {
            "schema_version", "package", "scope", "authority", "rw11_gate", "gate_closed",
            "source_commit", "authored_at", "selected_by", "cutoff_date",
            "observation_window", "selection_rule", "universe_ciks", "eligible_forms",
            "discovery_contract", "case_construction", "resource_limits", "output_paths",
            "safety_preflight", "evaluation_contract", "numeric_replay_trigger",
            "known_blockers",
        },
        "population plan",
    )
    if (
        plan["schema_version"] != SCHEMA_VERSION
        or plan["package"] != PACKAGE
        or plan["scope"] != PLAN_SCOPE
        or plan["authority"] != "none"
        or plan["rw11_gate"] != "OPEN_NOT_CLOSED"
        or plan["gate_closed"] is not False
    ):
        raise EvidenceError("population plan scope or safety boundary changed")
    if not isinstance(plan["source_commit"], str) or COMMIT_RE.fullmatch(plan["source_commit"]) is None:
        raise EvidenceError("population plan source_commit is not canonical")
    authored = _parse_utc(plan["authored_at"], "authored_at")
    if authored > now:
        raise EvidenceError("population plan authored_at is after collection start")
    if not isinstance(plan["selected_by"], str) or len(plan["selected_by"].strip()) < 20:
        raise EvidenceError("population plan selector identity is missing")
    if not isinstance(plan["selection_rule"], str) or len(plan["selection_rule"].strip()) < 200:
        raise EvidenceError("population plan selection rule is not substantive")
    try:
        cutoff = date.fromisoformat(plan["cutoff_date"])
    except (TypeError, ValueError) as exc:
        raise EvidenceError("population plan cutoff_date must be YYYY-MM-DD") from exc
    window = _exact_keys(
        plan["observation_window"],
        {"first_eligible_filing_date", "last_eligible_filing_date", "finalize_on_or_after"},
        "observation window",
    )
    try:
        first = date.fromisoformat(window["first_eligible_filing_date"])
        last = date.fromisoformat(window["last_eligible_filing_date"])
    except (TypeError, ValueError) as exc:
        raise EvidenceError("population plan observation dates must be YYYY-MM-DD") from exc
    finalize = _parse_utc(window["finalize_on_or_after"], "finalize_on_or_after")
    if not cutoff < first <= last or finalize.date() <= last:
        raise EvidenceError("population plan observation chronology is invalid")
    if now < finalize:
        raise EvidenceError("prospective observation window has not reached its finalize time")
    ciks = plan["universe_ciks"]
    if not isinstance(ciks, list) or not ciks or len(ciks) > 50:
        raise EvidenceError("population plan universe must contain 1..50 CIKs")
    if ciks != sorted(set(ciks)) or any(
        not isinstance(cik, str) or CIK_RE.fullmatch(cik) is None for cik in ciks
    ):
        raise EvidenceError("population plan CIKs must be canonical, sorted, and unique")
    if plan["eligible_forms"] != list(FORMS):
        raise EvidenceError(f"eligible forms must be exactly {list(FORMS)!r}")
    limits = _exact_keys(
        plan["resource_limits"],
        {
            "maximum_current_filings", "maximum_unique_filing_documents",
            "maximum_sec_requests", "maximum_response_bytes", "maximum_total_bytes",
        },
        "resource limits",
    )
    for key in limits:
        _positive_int(limits[key], key)
    if limits["maximum_unique_filing_documents"] < limits["maximum_current_filings"]:
        raise EvidenceError("filing-document limit cannot be below current-filing limit")
    if limits["maximum_sec_requests"] < len(ciks) + limits["maximum_unique_filing_documents"]:
        raise EvidenceError("SEC request cap cannot cover the declared census and document caps")
    if limits["maximum_total_bytes"] < limits["maximum_response_bytes"]:
        raise EvidenceError("total byte cap cannot be below per-response cap")
    outputs = _exact_keys(
        plan["output_paths"],
        {"capture_directory", "freeze_directory", "publication_rule"},
        "output paths",
    )
    if not all(isinstance(outputs[key], str) and outputs[key].strip() for key in outputs):
        raise EvidenceError("population plan output paths are incomplete")
    return plan, payload


def _witness_message(plan: dict, plan_sha256: str) -> str:
    declaration = {
        "authored_at": plan["authored_at"],
        "authority": "none",
        "plan_sha256": plan_sha256,
        "rw11_gate": "OPEN_NOT_CLOSED",
        "source_commit": plan["source_commit"],
    }
    return "rw11-population-plan-commitment " + _canonical_bytes(declaration).decode("utf-8")


def _load_witness(
    path: Path, *, expected_sha256: str, plan: dict, plan_sha256: str
) -> tuple[dict, bytes]:
    if SHA_RE.fullmatch(str(expected_sha256)) is None:
        raise EvidenceError("expected witness receipt hash must be lowercase SHA-256")
    payload = Path(path).read_bytes()
    if _sha256(payload) != expected_sha256:
        raise EvidenceError("witness receipt hash mismatch")
    receipt = _strict_json(payload, "witness receipt")
    receipt = _exact_keys(
        receipt,
        {
            "schema_version", "package", "scope", "authority", "rw11_gate", "provider",
            "population_plan_sha256", "population_plan_package_manifest_sha256",
            "source_commit", "plan_authored_at", "exact_message", "channel_id",
            "message_id", "bot_author_id", "server_timestamp", "verified_at", "post_count",
            "exact_message_get_count", "post_and_get_timestamps_match", "edited_timestamp",
            "webhook_id", "credential_values_recorded", "trust_boundary",
        },
        "witness receipt",
    )
    if (
        receipt["schema_version"] != SCHEMA_VERSION
        or receipt["package"] != PACKAGE
        or receipt["scope"] != WITNESS_SCOPE
        or receipt["authority"] != "none"
        or receipt["rw11_gate"] != "OPEN_NOT_CLOSED"
        or receipt["provider"] != "Discord API v10 exact-message chronology"
    ):
        raise EvidenceError("witness receipt scope or authority changed")
    if receipt["population_plan_sha256"] != plan_sha256:
        raise EvidenceError("witness receipt does not bind the population plan")
    if SHA_RE.fullmatch(str(receipt["population_plan_package_manifest_sha256"])) is None:
        raise EvidenceError("witness receipt plan-package hash is malformed")
    if receipt["source_commit"] != plan["source_commit"] or receipt["plan_authored_at"] != plan["authored_at"]:
        raise EvidenceError("witness receipt does not bind plan provenance")
    if receipt["exact_message"] != _witness_message(plan, plan_sha256):
        raise EvidenceError("witness receipt exact message mismatch")
    for key in ("channel_id", "message_id", "bot_author_id"):
        if not isinstance(receipt[key], str) or SNOWFLAKE_RE.fullmatch(receipt[key]) is None:
            raise EvidenceError(f"witness receipt {key} is invalid")
    server_time = _parse_utc(receipt["server_timestamp"], "witness server_timestamp")
    verified_at = _parse_utc(receipt["verified_at"], "witness verified_at")
    first = date.fromisoformat(plan["observation_window"]["first_eligible_filing_date"])
    if not _parse_utc(plan["authored_at"], "authored_at") <= server_time <= verified_at:
        raise EvidenceError("witness chronology is invalid")
    if server_time.date() >= first:
        raise EvidenceError("witness is not strictly before the prospective observation window")
    if (
        receipt["post_count"] != 1
        or receipt["exact_message_get_count"] != 1
        or receipt["post_and_get_timestamps_match"] is not True
        or receipt["edited_timestamp"] is not None
        or receipt["webhook_id"] is not None
        or receipt["credential_values_recorded"] is not False
    ):
        raise EvidenceError("witness receipt exact-message controls changed")
    return receipt, payload


def _load_preflight(
    path: Path, *, now: datetime, collector_source_commit: str
) -> tuple[dict, bytes]:
    payload = Path(path).read_bytes()
    preflight = _strict_json(payload, "safety preflight")
    preflight = _exact_keys(
        preflight,
        {
            "schema_version", "package", "scope", "checked_at", "source_commit",
            "configured_mode", "effective_mode", "persisted_mode", "expected_edge_available",
            "merger_execution_watchlist_count", "merger_edge_rows", "merger_selection_rows",
            "proposal_rows", "risk_decision_rows", "approval_rows", "order_rows",
            "audit_chain_valid", "halt_absent", "sec_user_agent_configured",
            "cockpit_service_processes", "api_port_listeners", "worktree_clean",
            "main_equals_origin_main",
        },
        "safety preflight",
    )
    if (
        preflight["schema_version"] != SCHEMA_VERSION
        or preflight["package"] != PACKAGE
        or preflight["scope"] != PREFLIGHT_SCOPE
        or preflight["source_commit"] != collector_source_commit
    ):
        raise EvidenceError("safety preflight package or source commit mismatch")
    checked_at = _parse_utc(preflight["checked_at"], "safety preflight checked_at")
    if checked_at > now or now - checked_at > PREFLIGHT_MAX_AGE:
        raise EvidenceError("safety preflight is future-dated or older than 15 minutes")
    if preflight["configured_mode"] != "read_only" or preflight["effective_mode"] != "read_only":
        raise EvidenceError("safety preflight is not read_only")
    if preflight["persisted_mode"] not in (None, "read_only"):
        raise EvidenceError("persisted mode exceeds read_only")
    zeroes = (
        "merger_execution_watchlist_count", "merger_edge_rows", "merger_selection_rows",
        "proposal_rows", "risk_decision_rows", "approval_rows", "order_rows",
        "cockpit_service_processes", "api_port_listeners",
    )
    if any(preflight[key] != 0 for key in zeroes):
        raise EvidenceError("safety preflight has a nonzero execution-state count")
    trues = (
        "audit_chain_valid", "halt_absent", "sec_user_agent_configured", "worktree_clean",
        "main_equals_origin_main",
    )
    if preflight["expected_edge_available"] is not False or any(
        preflight[key] is not True for key in trues
    ):
        raise EvidenceError("safety preflight fail-closed conditions are not satisfied")
    return preflight, payload


def _source_state() -> dict[str, Any]:
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        )
        return completed.stdout.strip()

    head = git("rev-parse", "HEAD")
    origin = git("rev-parse", "origin/main")
    dirty = git("status", "--porcelain")
    return {"head": head, "origin_main": origin, "worktree_clean": not dirty}


def _validate_source_state(value: dict[str, Any]) -> str:
    source = _exact_keys(value, {"head", "origin_main", "worktree_clean"}, "source state")
    if not isinstance(source["head"], str) or COMMIT_RE.fullmatch(source["head"]) is None:
        raise EvidenceError("collector HEAD is not canonical")
    if source["head"] != source["origin_main"] or source["worktree_clean"] is not True:
        raise EvidenceError("collector requires a clean HEAD equal to origin/main")
    return source["head"]


def _recent_rows(payload: bytes, expected_cik: str, cutoff: date) -> list[FilingRow]:
    document = _strict_json(payload, f"SEC submissions response for {expected_cik}")
    if not isinstance(document, dict):
        raise EvidenceError(f"SEC submissions response for {expected_cik} must be an object")
    raw_cik = document.get("cik")
    if isinstance(raw_cik, bool) or not isinstance(raw_cik, (int, str)):
        raise EvidenceError(f"SEC submissions response for {expected_cik} has malformed CIK")
    try:
        if isinstance(raw_cik, str) and (not raw_cik.isdigit() or not raw_cik):
            raise ValueError
        response_cik = f"{int(raw_cik):010d}"
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"SEC submissions response for {expected_cik} has malformed CIK") from exc
    if response_cik != expected_cik:
        raise EvidenceError(f"SEC submissions response CIK mismatches request {expected_cik}")
    filings = document.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise EvidenceError(f"SEC submissions response for {expected_cik} has no recent filings")
    names = ("accessionNumber", "form", "filingDate", "primaryDocument")
    arrays = [recent.get(name) for name in names]
    if any(not isinstance(value, list) for value in arrays):
        raise EvidenceError(f"SEC submissions arrays are malformed for {expected_cik}")
    lengths = {len(value) for value in arrays}
    if lengths == {0} or len(lengths) != 1:
        raise EvidenceError(f"SEC submissions arrays are empty or unequal for {expected_cik}")
    rows = []
    seen = set()
    for accession, form, filing_date_value, primary in zip(*arrays, strict=True):
        if not isinstance(accession, str) or ACCESSION_RE.fullmatch(accession) is None:
            raise EvidenceError(f"SEC submissions accession is malformed for {expected_cik}")
        if accession[:10] != expected_cik or accession in seen:
            raise EvidenceError(f"SEC submissions accession identity is ambiguous for {expected_cik}")
        if not isinstance(form, str) or not form:
            raise EvidenceError(f"SEC submissions form is malformed for {accession}")
        if (
            not isinstance(primary, str)
            or not primary
            or Path(primary).name != primary
            or "\\" in primary
        ):
            raise EvidenceError(f"SEC submissions primary document is malformed for {accession}")
        try:
            filed = date.fromisoformat(filing_date_value)
        except (TypeError, ValueError) as exc:
            raise EvidenceError(f"SEC submissions filing date is malformed for {accession}") from exc
        seen.add(accession)
        rows.append(FilingRow(expected_cik, accession, form, filed, primary))
    if any(left.chronology <= right.chronology for left, right in zip(rows, rows[1:])):
        raise EvidenceError(f"SEC submissions chronology is not strictly newest-first for {expected_cik}")
    if min(row.filing_date for row in rows) > cutoff:
        raise EvidenceError(f"SEC submissions history does not reach the cutoff for {expected_cik}")
    return rows


def _select_cases(plan: dict, by_cik: dict[str, list[FilingRow]]) -> tuple[list[dict], list[FilingRow]]:
    cutoff = date.fromisoformat(plan["cutoff_date"])
    first = date.fromisoformat(plan["observation_window"]["first_eligible_filing_date"])
    last = date.fromisoformat(plan["observation_window"]["last_eligible_filing_date"])
    eligible = [
        row
        for cik in plan["universe_ciks"]
        for row in by_cik[cik]
        if row.form in FORMS and cutoff < row.filing_date <= last
    ]
    if any(row.filing_date < first for row in eligible):
        raise EvidenceError("selected filing precedes the fixed observation start")
    eligible.sort(key=lambda row: (row.filing_date, row.accession, row.cik, row.form))
    maximum = plan["resource_limits"]["maximum_current_filings"]
    selected = eligible[:maximum]
    cases = []
    documents: dict[str, FilingRow] = {}
    for current in selected:
        prior = None
        if current.form in PERIODIC_FORMS:
            candidates = [
                row
                for row in by_cik[current.cik]
                if row.form == current.form and row.chronology < current.chronology
            ]
            if not candidates:
                raise EvidenceError(f"no prior same-form filing for {current.accession}")
            prior = max(candidates, key=lambda row: row.chronology)
        documents[current.accession] = current
        if prior is not None:
            documents[prior.accession] = prior
        cases.append({"case_id": _case_id(current), "current": current, "prior": prior})
    ordered_documents = sorted(
        documents.values(), key=lambda row: (row.cik, row.filing_date, row.accession, row.form)
    )
    if len(ordered_documents) > plan["resource_limits"]["maximum_unique_filing_documents"]:
        raise EvidenceError("unique filing-document cap exceeded")
    return cases, ordered_documents


def _case_id(row: FilingRow) -> str:
    return f"{row.cik}-{row.form.lower()}-{row.accession}"


def _filing_spec(row: FilingRow, hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "cik": row.cik,
        "accession_no": row.accession,
        "form_type": row.form,
        "filing_date": row.filing_date.isoformat(),
        "primary_doc": row.primary_document,
        "raw_sha256": hashes[row.accession],
    }


def _case_manifest(plan: dict, cases: list[dict], hashes: dict[str, str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "package": PACKAGE,
        "source_commit": plan["source_commit"],
        "authored_at": plan["authored_at"],
        "selected_by": plan["selected_by"],
        "cutoff_date": plan["cutoff_date"],
        "selection_rule": plan["selection_rule"],
        "universe_ciks": plan["universe_ciks"],
        "cases": [
            {
                "case_id": case["case_id"],
                "current": _filing_spec(case["current"], hashes),
                "prior": None if case["prior"] is None else _filing_spec(case["prior"], hashes),
            }
            for case in cases
        ],
    }


def _implementation_hashes() -> dict[str, str]:
    return {path: _sha256((ROOT / path).read_bytes()) for path in IMPLEMENTATION_PATHS}


def _safe_output(path: Path, expected: str) -> Path:
    candidate = Path(path)
    if candidate.name in {"", ".", ".."}:
        raise EvidenceError("output directory must name one bounded package")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve(strict=False)
    if resolved != Path(expected).resolve(strict=False):
        raise EvidenceError("output directory differs from the witnessed plan")
    if candidate.exists() or candidate.is_symlink() or resolved.exists():
        raise EvidenceError("output directory already exists; evidence is write-once")
    return resolved


def _is_linklike(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"cannot inspect capture artifact {path}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(
        reparse_flag and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _sample_clock(clock: Callable[[], datetime], previous: datetime | None, label: str) -> datetime:
    sampled = clock()
    if sampled.tzinfo is None or sampled.utcoffset() != timedelta(0):
        raise EvidenceError(f"{label} must be timezone-qualified UTC")
    if previous is not None and sampled < previous:
        raise EvidenceError("capture clock moved backwards")
    return sampled


def _fetch(
    fetcher: Any,
    url: str,
    *,
    request_state: dict[str, int],
    limits: dict[str, int],
) -> bytes:
    if request_state["requests"] >= limits["maximum_sec_requests"]:
        raise EvidenceError("SEC request cap exceeded")
    request_state["requests"] += 1
    payload = fetcher.get_bytes(url)
    if not isinstance(payload, bytes):
        raise EvidenceError("SEC fetcher returned non-byte payload")
    if len(payload) > limits["maximum_response_bytes"]:
        raise EvidenceError("SEC response byte cap exceeded")
    request_state["bytes"] += len(payload)
    if request_state["bytes"] > limits["maximum_total_bytes"]:
        raise EvidenceError("SEC total byte cap exceeded")
    return payload


def _collection_hash_value(manifest: dict) -> dict:
    return {key: manifest[key] for key in (
        "plan_sha256", "witness_receipt_sha256", "safety_preflight_sha256",
        "source_commit", "collector_source_commit", "capture_started_at", "captured_at",
        "implementation_hashes", "submissions", "raw_documents", "case_manifest_sha256",
        "sec_request_count", "fetch_bytes_received",
    )}


def collect(
    plan_path: Path,
    witness_receipt_path: Path,
    safety_preflight_path: Path,
    output_dir: Path,
    *,
    expected_plan_sha256: str,
    expected_witness_receipt_sha256: str,
    fetcher: Any,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    source_state: dict[str, Any] | None = None,
) -> dict:
    """Execute and atomically publish one complete witnessed prospective census."""
    capture_started_at = _sample_clock(clock, None, "capture start")
    plan, plan_bytes = _load_plan(
        plan_path, now=capture_started_at, expected_sha256=expected_plan_sha256
    )
    _receipt, receipt_bytes = _load_witness(
        witness_receipt_path,
        expected_sha256=expected_witness_receipt_sha256,
        plan=plan,
        plan_sha256=expected_plan_sha256,
    )
    collector_commit = _validate_source_state(source_state or _source_state())
    _preflight, preflight_bytes = _load_preflight(
        safety_preflight_path, now=capture_started_at, collector_source_commit=collector_commit
    )
    target = _safe_output(output_dir, plan["output_paths"]["capture_directory"])
    staging = Path(tempfile.mkdtemp(prefix=".rw11-text-population.", dir=target.parent))
    request_state = {"requests": 0, "bytes": 0}
    previous = capture_started_at
    try:
        (staging / PLAN_NAME).write_bytes(plan_bytes)
        (staging / WITNESS_NAME).write_bytes(receipt_bytes)
        (staging / PREFLIGHT_NAME).write_bytes(preflight_bytes)
        submissions_dir = staging / "submissions"
        submissions_dir.mkdir()
        by_cik: dict[str, list[FilingRow]] = {}
        submissions_records = []
        cutoff = date.fromisoformat(plan["cutoff_date"])
        limits = plan["resource_limits"]
        for cik in plan["universe_ciks"]:
            url = submissions_url(cik)
            payload = _fetch(fetcher, url, request_state=request_state, limits=limits)
            previous = _sample_clock(clock, previous, f"submissions fetch time for {cik}")
            rows = _recent_rows(payload, cik, cutoff)
            filename = f"CIK{cik}.json"
            (submissions_dir / filename).write_bytes(payload)
            (submissions_dir / f"{filename}.sha256").write_text(
                _sha256(payload) + "\n", encoding="ascii"
            )
            by_cik[cik] = rows
            submissions_records.append({
                "cik": cik,
                "url": url,
                "filename": filename,
                "payload_sha256": _sha256(payload),
                "payload_bytes": len(payload),
                "fetched_at": previous.isoformat(),
                "row_count": len(rows),
                "newest_filing_date": rows[0].filing_date.isoformat(),
                "oldest_filing_date": rows[-1].filing_date.isoformat(),
            })
        cases, documents = _select_cases(plan, by_cik)
        raw_root = staging / "raw"
        raw_root.mkdir()
        (raw_root / ISOLATION_MARKER).write_text(ISOLATION_MARKER_TEXT, encoding="utf-8")
        store = RawStore(raw_root, min_free_bytes=0)
        hashes = {}
        raw_records = []
        for filing in documents:
            payload = _fetch(fetcher, filing.url, request_state=request_state, limits=limits)
            previous = _sample_clock(clock, previous, f"filing fetch time for {filing.accession}")
            ref = store.write(filing.accession, payload)
            hashes[filing.accession] = ref.sha256
            raw_records.append({
                **_filing_spec(filing, hashes),
                "url": filing.url,
                "payload_bytes": len(payload),
                "fetched_at": previous.isoformat(),
            })
        case_manifest = _case_manifest(plan, cases, hashes)
        case_bytes = _write_json(staging / CASES_NAME, case_manifest)
        captured_at = _sample_clock(clock, previous, "capture completion")
        periodic = [
            case["current"].accession
            for case in cases
            if case["current"].form in PERIODIC_FORMS
        ]
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "package": PACKAGE,
            "scope": SCOPE,
            "authority": "none",
            "rw11_gate": "OPEN_NOT_CLOSED",
            "gate_closed": False,
            "source_commit": plan["source_commit"],
            "collector_source_commit": collector_commit,
            "capture_started_at": capture_started_at.isoformat(),
            "captured_at": captured_at.isoformat(),
            "plan_sha256": expected_plan_sha256,
            "witness_receipt_sha256": expected_witness_receipt_sha256,
            "safety_preflight_sha256": _sha256(preflight_bytes),
            "selected_by": plan["selected_by"],
            "cutoff_date": plan["cutoff_date"],
            "observation_window": plan["observation_window"],
            "selection_rule": plan["selection_rule"],
            "universe_ciks": plan["universe_ciks"],
            "eligible_forms": plan["eligible_forms"],
            "implementation_hashes": _implementation_hashes(),
            "submissions": submissions_records,
            "selected_current_filing_count": len(cases),
            "periodic_current_accessions": periodic,
            "raw_documents": raw_records,
            "case_manifest_sha256": _sha256(case_bytes),
            "sec_request_count": request_state["requests"],
            "fetch_bytes_received": request_state["bytes"],
            "numeric_replay_trigger": (
                "READY_FOR_SEPARATE_BOUNDED_PLAN"
                if periodic
                else "AWAITING_SUPPORTED_POST_CUTOFF_PERIODIC_ACCESSION"
            ),
            "known_blockers": list(KNOWN_BLOCKERS),
        }
        manifest["collection_hash"] = _sha256(_canonical_bytes(_collection_hash_value(manifest)))
        manifest_bytes = _write_json(staging / MANIFEST_NAME, manifest)
        (staging / MANIFEST_HASH_NAME).write_text(
            _sha256(manifest_bytes) + "\n", encoding="ascii"
        )
        try:
            target.mkdir()
        except FileExistsError as exc:
            raise EvidenceError("output directory already exists; evidence is write-once") from exc
        for child in staging.iterdir():
            child.rename(target / child.name)
        result = verify(
            target,
            expected_plan_sha256=expected_plan_sha256,
            expected_witness_receipt_sha256=expected_witness_receipt_sha256,
        )
        if not result["ok"]:
            raise EvidenceError("published capture failed immediate verification")
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _expected_paths(manifest: dict) -> set[str]:
    paths = {
        PLAN_NAME, WITNESS_NAME, PREFLIGHT_NAME, CASES_NAME, MANIFEST_NAME,
        MANIFEST_HASH_NAME, "submissions", "raw", f"raw/{ISOLATION_MARKER}",
    }
    for row in manifest["submissions"]:
        prefix = f"submissions/{row['filename']}"
        paths.update({prefix, f"{prefix}.sha256"})
    for row in manifest["raw_documents"]:
        prefix = f"raw/{row['accession_no']}"
        paths.update({prefix, f"{prefix}/raw.bin", f"{prefix}/sha256"})
    return paths


def verify(
    package_dir: Path, *, expected_plan_sha256: str, expected_witness_receipt_sha256: str
) -> dict:
    """Recompute the complete census, case, raw-byte, and provenance bindings."""
    requested = Path(package_dir)
    if _is_linklike(requested):
        raise EvidenceError("capture package root cannot be a link or reparse point")
    root = requested.resolve(strict=True)
    if not root.is_dir():
        raise EvidenceError("capture package must be a directory")
    present = set()
    for path in root.rglob("*"):
        if _is_linklike(path):
            raise EvidenceError(
                f"capture package contains link or reparse point: {path.relative_to(root)}"
            )
        present.add(path.relative_to(root).as_posix())
    manifest_bytes = (root / MANIFEST_NAME).read_bytes()
    manifest_hash = (root / MANIFEST_HASH_NAME).read_text(encoding="ascii").strip()
    if SHA_RE.fullmatch(manifest_hash) is None or _sha256(manifest_bytes) != manifest_hash:
        raise EvidenceError("capture manifest hash mismatch")
    manifest = _strict_json(manifest_bytes, "capture manifest")
    manifest = _exact_keys(
        manifest,
        {
            "schema_version", "package", "scope", "authority", "rw11_gate", "gate_closed",
            "source_commit", "collector_source_commit", "capture_started_at", "captured_at",
            "plan_sha256", "witness_receipt_sha256", "safety_preflight_sha256",
            "selected_by", "cutoff_date", "observation_window", "selection_rule",
            "universe_ciks", "eligible_forms", "implementation_hashes", "submissions",
            "selected_current_filing_count", "periodic_current_accessions", "raw_documents",
            "case_manifest_sha256", "sec_request_count", "fetch_bytes_received",
            "numeric_replay_trigger", "known_blockers", "collection_hash",
        },
        "capture manifest",
    )
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["package"] != PACKAGE
        or manifest["scope"] != SCOPE
        or manifest["authority"] != "none"
        or manifest["rw11_gate"] != "OPEN_NOT_CLOSED"
        or manifest["gate_closed"] is not False
        or manifest["known_blockers"] != list(KNOWN_BLOCKERS)
    ):
        raise EvidenceError("capture manifest scope or safety boundary changed")
    if not isinstance(manifest["collector_source_commit"], str) or COMMIT_RE.fullmatch(
        manifest["collector_source_commit"]
    ) is None:
        raise EvidenceError("collector source commit is not canonical")
    if not isinstance(manifest["submissions"], list) or not isinstance(
        manifest["raw_documents"], list
    ):
        raise EvidenceError("capture manifest submissions/raw_documents must be lists")
    if not isinstance(manifest["periodic_current_accessions"], list):
        raise EvidenceError("capture manifest periodic_current_accessions must be a list")
    started = _parse_utc(manifest["capture_started_at"], "capture_started_at")
    completed = _parse_utc(manifest["captured_at"], "captured_at")
    if started > completed:
        raise EvidenceError("capture completion precedes start")
    plan, plan_bytes = _load_plan(
        root / PLAN_NAME, now=started, expected_sha256=expected_plan_sha256
    )
    if manifest["plan_sha256"] != _sha256(plan_bytes):
        raise EvidenceError("capture manifest plan hash mismatch")
    for key in (
        "source_commit", "selected_by", "cutoff_date", "observation_window",
        "selection_rule", "universe_ciks", "eligible_forms",
    ):
        if manifest[key] != plan[key]:
            raise EvidenceError(f"capture manifest diverges from population plan: {key}")
    _receipt, receipt_bytes = _load_witness(
        root / WITNESS_NAME,
        expected_sha256=expected_witness_receipt_sha256,
        plan=plan,
        plan_sha256=expected_plan_sha256,
    )
    if manifest["witness_receipt_sha256"] != _sha256(receipt_bytes):
        raise EvidenceError("capture manifest witness hash mismatch")
    if root != Path(plan["output_paths"]["capture_directory"]).resolve(strict=True):
        raise EvidenceError("capture package moved from the witnessed output path")
    if manifest["implementation_hashes"] != _implementation_hashes():
        raise EvidenceError("capture implementation hashes changed")
    if present != _expected_paths(manifest):
        raise EvidenceError(
            f"capture artifact set changed: missing={sorted(_expected_paths(manifest) - present)}, "
            f"extra={sorted(present - _expected_paths(manifest))}"
        )
    preflight_bytes = (root / PREFLIGHT_NAME).read_bytes()
    if manifest["safety_preflight_sha256"] != _sha256(preflight_bytes):
        raise EvidenceError("safety preflight hash mismatch")
    _load_preflight(
        root / PREFLIGHT_NAME,
        now=started,
        collector_source_commit=manifest["collector_source_commit"],
    )
    cutoff = date.fromisoformat(plan["cutoff_date"])
    by_cik = {}
    recomputed_submissions = []
    observed_fetch_times = []
    for recorded in manifest["submissions"]:
        filename = recorded.get("filename")
        if filename != f"CIK{recorded.get('cik')}.json":
            raise EvidenceError("submissions filename/CIK binding changed")
        payload = (root / "submissions" / filename).read_bytes()
        sidecar = (root / "submissions" / f"{filename}.sha256").read_text(
            encoding="ascii"
        ).strip()
        if sidecar != _sha256(payload):
            raise EvidenceError(f"submissions hash mismatch for {filename}")
        rows = _recent_rows(payload, recorded["cik"], cutoff)
        fetched_at = _parse_utc(recorded["fetched_at"], "submissions fetched_at")
        if not started <= fetched_at <= completed:
            raise EvidenceError("submissions fetch timestamp is outside capture window")
        observed_fetch_times.append(fetched_at)
        by_cik[recorded["cik"]] = rows
        recomputed_submissions.append({
            "cik": recorded["cik"],
            "url": submissions_url(recorded["cik"]),
            "filename": filename,
            "payload_sha256": _sha256(payload),
            "payload_bytes": len(payload),
            "fetched_at": recorded["fetched_at"],
            "row_count": len(rows),
            "newest_filing_date": rows[0].filing_date.isoformat(),
            "oldest_filing_date": rows[-1].filing_date.isoformat(),
        })
    if [row["cik"] for row in recomputed_submissions] != plan["universe_ciks"]:
        raise EvidenceError("submissions census set/order differs from the plan")
    if recomputed_submissions != manifest["submissions"]:
        raise EvidenceError("submissions summaries changed")
    cases, documents = _select_cases(plan, by_cik)
    store = RawStore(root / "raw", min_free_bytes=0)
    if (root / "raw" / ISOLATION_MARKER).read_text(encoding="utf-8") != ISOLATION_MARKER_TEXT:
        raise EvidenceError("isolated raw-store marker changed")
    hashes = {}
    recomputed_raw = []
    recorded_by_accession = {row["accession_no"]: row for row in manifest["raw_documents"]}
    if len(recorded_by_accession) != len(manifest["raw_documents"]):
        raise EvidenceError("duplicate raw-document accession")
    for filing in documents:
        recorded = recorded_by_accession.get(filing.accession)
        if recorded is None:
            raise EvidenceError(f"missing raw document for {filing.accession}")
        try:
            payload = store.resolve(filing.accession)
        except (FileNotFoundError, RawStoreError) as exc:
            raise EvidenceError(f"raw filing integrity failure for {filing.accession}") from exc
        hashes[filing.accession] = _sha256(payload)
        fetched_at = _parse_utc(recorded["fetched_at"], "filing fetched_at")
        if not started <= fetched_at <= completed:
            raise EvidenceError("filing fetch timestamp is outside capture window")
        observed_fetch_times.append(fetched_at)
        recomputed_raw.append({
            **_filing_spec(filing, hashes),
            "url": filing.url,
            "payload_bytes": len(payload),
            "fetched_at": recorded["fetched_at"],
        })
    if recomputed_raw != manifest["raw_documents"]:
        raise EvidenceError("raw-document summaries changed")
    if any(left > right for left, right in zip(observed_fetch_times, observed_fetch_times[1:])):
        raise EvidenceError("recorded fetch timestamps moved backwards")
    case_bytes = (root / CASES_NAME).read_bytes()
    expected_case_bytes = _canonical_bytes(_case_manifest(plan, cases, hashes)) + b"\n"
    if case_bytes != expected_case_bytes or manifest["case_manifest_sha256"] != _sha256(case_bytes):
        raise EvidenceError("case manifest differs from the complete deterministic census")
    if manifest["selected_current_filing_count"] != len(cases):
        raise EvidenceError("selected current-filing count mismatch")
    periodic = [
        case["current"].accession
        for case in cases
        if case["current"].form in PERIODIC_FORMS
    ]
    if manifest["periodic_current_accessions"] != periodic:
        raise EvidenceError("periodic current-accession set changed")
    expected_trigger = (
        "READY_FOR_SEPARATE_BOUNDED_PLAN"
        if periodic
        else "AWAITING_SUPPORTED_POST_CUTOFF_PERIODIC_ACCESSION"
    )
    if manifest["numeric_replay_trigger"] != expected_trigger:
        raise EvidenceError("numeric replay trigger changed")
    request_count = len(recomputed_submissions) + len(recomputed_raw)
    total_bytes = sum(row["payload_bytes"] for row in recomputed_submissions + recomputed_raw)
    if manifest["sec_request_count"] != request_count or manifest["fetch_bytes_received"] != total_bytes:
        raise EvidenceError("SEC request or byte accounting mismatch")
    limits = plan["resource_limits"]
    if (
        len(cases) > limits["maximum_current_filings"]
        or len(documents) > limits["maximum_unique_filing_documents"]
        or request_count > limits["maximum_sec_requests"]
        or total_bytes > limits["maximum_total_bytes"]
        or any(
            row["payload_bytes"] > limits["maximum_response_bytes"]
            for row in recomputed_submissions + recomputed_raw
        )
    ):
        raise EvidenceError("verified package exceeds a witnessed resource limit")
    if manifest["collection_hash"] != _sha256(_canonical_bytes(_collection_hash_value(manifest))):
        raise EvidenceError("collection hash mismatch")
    return {
        "ok": True,
        "collection_hash": manifest["collection_hash"],
        "current_filings": len(cases),
        "raw_documents": len(documents),
        "periodic_current_accessions": len(periodic),
        "numeric_replay_trigger": expected_trigger,
        "authority": "none",
        "rw11_gate": "OPEN_NOT_CLOSED",
    }


def _live_fetcher(plan: dict, rate_per_second: float) -> SecFetcher:
    user_agent = os.environ.get("COCKPIT_SEC_USER_AGENT", "")
    if not user_agent.strip():
        raise EvidenceError(
            "COCKPIT_SEC_USER_AGENT is required for SEC fair access; its value is never printed"
        )
    limits = plan["resource_limits"]
    return SecFetcher(
        user_agent=user_agent,
        rate_per_sec=rate_per_second,
        max_retries=0,
        max_response_bytes=limits["maximum_response_bytes"],
        max_total_bytes=limits["maximum_total_bytes"],
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect_cmd = commands.add_parser("collect")
    collect_cmd.add_argument("--plan", type=Path, required=True)
    collect_cmd.add_argument("--expected-plan-sha256", required=True)
    collect_cmd.add_argument("--witness-receipt", type=Path, required=True)
    collect_cmd.add_argument("--expected-witness-receipt-sha256", required=True)
    collect_cmd.add_argument("--safety-preflight", type=Path, required=True)
    collect_cmd.add_argument("--output-dir", type=Path, required=True)
    collect_cmd.add_argument("--rate-per-second", type=float, default=2.0)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--package-dir", type=Path, required=True)
    verify_cmd.add_argument("--expected-plan-sha256", required=True)
    verify_cmd.add_argument("--expected-witness-receipt-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify":
            print(json.dumps(verify(
                args.package_dir,
                expected_plan_sha256=args.expected_plan_sha256,
                expected_witness_receipt_sha256=args.expected_witness_receipt_sha256,
            )))
            return 0
        start = datetime.now(UTC)
        plan, _payload = _load_plan(
            args.plan, now=start, expected_sha256=args.expected_plan_sha256
        )
        manifest = collect(
            args.plan,
            args.witness_receipt,
            args.safety_preflight,
            args.output_dir,
            expected_plan_sha256=args.expected_plan_sha256,
            expected_witness_receipt_sha256=args.expected_witness_receipt_sha256,
            fetcher=_live_fetcher(plan, args.rate_per_second),
        )
        print(json.dumps({
            "collection_hash": manifest["collection_hash"],
            "current_filings": manifest["selected_current_filing_count"],
            "periodic_current_accessions": len(manifest["periodic_current_accessions"]),
            "authority": "none",
            "rw11_gate": "OPEN_NOT_CLOSED",
        }))
        return 0
    except (EvidenceError, SecFetchError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"RW-11 text-population capture refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
