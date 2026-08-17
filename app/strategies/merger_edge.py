"""W-23 deterministic cash-merger expected-edge provider.

This module may read immutable evidence and market data, but it never imports a broker and never
places an order.  Its only authority-bearing output is ``EdgeEstimate | None``; all exceptions,
missing evidence, tamper, stale observations, and scope mismatches collapse to ``None``.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select

from filings.raw_store import RawStore
from ingest.market_data import calculate_daily_metrics
from quant.events import to_ns
from quant.verify import EdgeEstimate
from schemas.models.audit_entry import AuditEntry
from schemas.models.company import Company
from schemas.models.filing import Filing
from schemas.models.merger_edge import (
    MergerCalibrationEvidence,
    MergerDealEvidence,
    MergerEdgeEvidence,
)
from orchestrator.state import EVENT_EDGAR_POLL_COMPLETED, EVENT_FILING_INGESTED
from storage.parquet_store import (
    SnapshotIntegrityError,
    read_verified_snapshot,
    write_snapshot,
)

DEAL_SCHEMA = "merger_deal.v1"
CALIBRATION_SCHEMA = "merger_calibration.v1"
EDGE_SCHEMA = "merger_edge_evidence.v1"
COHORT_ROW_SCHEMA = "merger_calibration_cohort_row.v2"
SOURCE_ID = "cash_merger_30d.v1"
TIME_HORIZON = "long"
EVENT_DEAL = "merger_deal_evidence"
EVENT_CALIBRATION = "merger_calibration_evidence"
EVENT_EDGE = "merger_edge_computed"
EVENT_COMPANY_ATTRIBUTION = "edgar-company-attribution"

MAX_RESOLUTION_DAYS = 30
MAX_DEAL_OBSERVATION_AGE = timedelta(hours=24)
MAX_EDGAR_POLL_AGE = timedelta(minutes=10)
MAX_CALIBRATION_AGE = timedelta(days=365)
MIN_PRICE = Decimal("5")
MIN_DOLLAR_ADV = Decimal("20000000")
MIN_METRIC_OBSERVATIONS = 20
MAX_METRIC_AGE_DAYS = 7
MAX_SPREAD_BPS = Decimal("50")
MIN_COHORT_SIZE = 30
MIN_TRAINING_COHORT_SIZE = 10
MIN_EVALUATION_COHORT_SIZE = 30
MIN_HELDOUT_FIELDS = 20
MIN_HELDOUT_PRECISION = Decimal("0.95")
ALLOWED_QUOTE_FEEDS = frozenset({"iex", "sip"})

_CIK = re.compile(r"^\d{10}$")
_ACCESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CAL_REF = re.compile(r"^strategy-calibration://(\d{4}-\d{2}-\d{2})/([A-Za-z0-9._-]+)$")
_ELIGIBILITY_FIELDS = frozenset({
    "cash_offer", "security_type", "listing_country", "listing_exchange",
    "agreement_status", "lifecycle_status", "has_cvr", "has_earnout",
    "has_stock", "uses_options_or_leverage", "is_rumor_or_competing_bid",
    "amendment_reviewed",
})
_SOURCE_REF_FIELDS = frozenset({"event_seq", "raw_ref", "content_hash"})
_COHORT_FIELDS = frozenset({
    "schema_version", "deal_id", "symbol", "target_cik", "source_accession",
    "split", "selected_at", "outcome_as_of", "outcome", "entry_ask",
    "outcome_price", "eligibility_prediction", "eligibility_label",
    "eligibility_source", "entry_price_source", "outcome_source",
    "outcome_price_source",
})

_DEAL_INPUT_FIELDS = frozenset({
    "schema_version", "symbol", "target_cik", "cash_offer",
    "security_type", "listing_country", "listing_exchange",
    "announcement_accession", "current_accession", "agreement_status",
    "lifecycle_status", "conditions", "announcement_at", "observed_at",
    "expected_resolution_date", "outside_date", "source_accessions", "filing_hashes", "has_cvr",
    "has_earnout", "has_stock", "uses_options_or_leverage",
    "is_rumor_or_competing_bid", "amendment_reviewed",
})
_DEAL_BINDING_FIELDS = frozenset({
    "filing_event_seq", "filing_set_hash",
    "edgar_poll_event_seq", "edgar_poll_completed_at",
})
_DEAL_FIELDS = _DEAL_INPUT_FIELDS | _DEAL_BINDING_FIELDS
_CALIBRATION_FIELDS = frozenset({
    "schema_version", "version", "method", "frozen", "frozen_at", "cohort_ref",
    "cohort_content_hash", "cohort_as_of", "cohort_size", "training_cutoff",
    "training_cohort_size", "evaluation_cohort_size", "heldout_field_count",
    "heldout_correct_fields",
    "p_close", "p_fail", "p_open", "break_price_ratio", "open_price_ratio",
})


class MergerEvidenceError(ValueError):
    """An immutable strategy record does not satisfy the W-23 contract."""


class EdgarFreshnessError(MergerEvidenceError):
    """The target's durable SEC polling authority is absent, degraded, or stale."""


class SemanticEvidenceUnavailable(MergerEvidenceError):
    """Independent semantic authority for labels/deal terms is not available."""


def _canonical_hash(document: dict) -> str:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc(value, *, field: str, allow_naive: bool = False) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MergerEvidenceError(f"{field} is not an ISO datetime") from exc
    else:
        raise MergerEvidenceError(f"{field} is not a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not allow_naive:
            raise MergerEvidenceError(f"{field} is timezone-naive")
        # SQLite returns a naive value for timezone=True. This flag is used only at that
        # database read boundary; operator evidence must always carry an explicit offset.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date(value, *, field: str) -> date:
    try:
        return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))
    except ValueError as exc:
        raise MergerEvidenceError(f"{field} is not an ISO date") from exc


def _decimal(value, *, field: str, positive: bool = False) -> Decimal:
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MergerEvidenceError(f"{field} is not numeric") from exc
    if not out.is_finite() or (positive and out <= 0):
        raise MergerEvidenceError(f"{field} is not a positive finite value")
    return out


def _exact_fields(document: dict, expected: frozenset[str], *, kind: str) -> None:
    if not isinstance(document, dict) or frozenset(document) != expected:
        missing = sorted(expected - frozenset(document or {})) if isinstance(document, dict) else sorted(expected)
        extra = sorted(frozenset(document or {}) - expected) if isinstance(document, dict) else []
        raise MergerEvidenceError(f"{kind} fields mismatch; missing={missing}, extra={extra}")


def _normalized_deal(document: dict, *, require_authority_binding: bool = True) -> dict:
    expected = _DEAL_FIELDS if require_authority_binding else _DEAL_INPUT_FIELDS
    _exact_fields(document, expected, kind="deal")
    if document["schema_version"] != DEAL_SCHEMA:
        raise MergerEvidenceError("unsupported deal schema")
    symbol = str(document["symbol"]).strip().upper()
    cik = str(document["target_cik"]).strip()
    if not symbol or len(symbol) > 10 or not re.fullmatch(r"[A-Z][A-Z0-9.-]*", symbol):
        raise MergerEvidenceError("invalid target symbol")
    if not _CIK.fullmatch(cik):
        raise MergerEvidenceError("target CIK must be ten digits")
    accessions = [str(value).strip() for value in document["source_accessions"]]
    if not accessions or len(accessions) != len(set(accessions)):
        raise MergerEvidenceError("source accessions must be non-empty and unique")
    if any(not _ACCESSION.fullmatch(value) for value in accessions):
        raise MergerEvidenceError("invalid source accession")
    filing_hashes = document["filing_hashes"]
    if (
        not isinstance(filing_hashes, dict)
        or set(filing_hashes) != set(accessions)
        or any(not isinstance(value, str) or _HASH.fullmatch(value) is None
               for value in filing_hashes.values())
    ):
        raise MergerEvidenceError("filing hashes must bind every source accession")
    announcement = str(document["announcement_accession"]).strip()
    current = str(document["current_accession"]).strip()
    if announcement not in accessions or current not in accessions:
        raise MergerEvidenceError("announcement/current accessions must be source accessions")
    if document["agreement_status"] != "definitive":
        raise MergerEvidenceError("initial strategy requires a signed definitive agreement")
    if (
        document["security_type"] != "common_equity"
        or document["listing_country"] != "US"
        or document["listing_exchange"] not in {"nasdaq", "nyse", "nyse_american"}
    ):
        raise MergerEvidenceError("target is not eligible US-listed common equity")
    if document["lifecycle_status"] not in {"active", "terminated"}:
        raise MergerEvidenceError("invalid lifecycle status")
    flags = (
        "has_cvr", "has_earnout", "has_stock", "uses_options_or_leverage",
        "is_rumor_or_competing_bid", "amendment_reviewed",
    )
    if any(type(document[name]) is not bool for name in flags):
        raise MergerEvidenceError("deal flags must be booleans")
    conditions = document["conditions"]
    if (
        not isinstance(conditions, list) or not conditions or len(conditions) > 50
        or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in conditions)
    ):
        raise MergerEvidenceError("deal conditions must be a bounded non-empty string list")
    announced = _utc(document["announcement_at"], field="announcement_at")
    observed = _utc(document["observed_at"], field="observed_at")
    resolution = _date(document["expected_resolution_date"], field="expected_resolution_date")
    outside = _date(document["outside_date"], field="outside_date")
    if announced > observed or outside < resolution:
        raise MergerEvidenceError("deal chronology is inconsistent")
    normalized = dict(document)
    normalized.update({
        "symbol": symbol,
        "target_cik": cik,
        "security_type": "common_equity",
        "listing_country": "US",
        "listing_exchange": document["listing_exchange"],
        "cash_offer": str(_decimal(document["cash_offer"], field="cash_offer", positive=True)),
        "announcement_accession": announcement,
        "current_accession": current,
        "conditions": [item.strip() for item in conditions],
        "announcement_at": announced.isoformat(),
        "observed_at": observed.isoformat(),
        "expected_resolution_date": resolution.isoformat(),
        "outside_date": outside.isoformat(),
        "source_accessions": accessions,
        "filing_hashes": {accession: filing_hashes[accession] for accession in accessions},
    })
    if require_authority_binding:
        for name in ("filing_event_seq", "edgar_poll_event_seq"):
            if type(document[name]) is not int or document[name] <= 0:
                raise MergerEvidenceError(f"{name} must be a positive audit sequence")
            normalized[name] = document[name]
        if not isinstance(document["filing_set_hash"], str) or _HASH.fullmatch(
            document["filing_set_hash"]
        ) is None:
            raise MergerEvidenceError("filing_set_hash is invalid")
        normalized["edgar_poll_completed_at"] = _utc(
            document["edgar_poll_completed_at"], field="edgar_poll_completed_at"
        ).isoformat()
    return normalized


def _normalized_calibration(document: dict) -> dict:
    _exact_fields(document, _CALIBRATION_FIELDS, kind="calibration")
    if document["schema_version"] != CALIBRATION_SCHEMA or document["version"] != SOURCE_ID:
        raise MergerEvidenceError("unsupported calibration schema/version")
    if (
        document["method"] != "wilson95_heldout_worst_case_prices_v2"
        or document["frozen"] is not True
    ):
        raise MergerEvidenceError("calibration method is not the pinned conservative method")
    frozen = _utc(document["frozen_at"], field="frozen_at")
    cohort_as_of = _date(document["cohort_as_of"], field="cohort_as_of")
    if frozen.date() < cohort_as_of:
        raise MergerEvidenceError("calibration was frozen before its cohort as-of date")
    cohort_size = int(document["cohort_size"])
    training_size = int(document["training_cohort_size"])
    evaluation_size = int(document["evaluation_cohort_size"])
    heldout = int(document["heldout_field_count"])
    correct = int(document["heldout_correct_fields"])
    training_cutoff = _date(document["training_cutoff"], field="training_cutoff")
    if (
        cohort_size < MIN_COHORT_SIZE
        or training_size < MIN_TRAINING_COHORT_SIZE
        or evaluation_size < MIN_EVALUATION_COHORT_SIZE
        or training_size + evaluation_size != cohort_size
        or heldout < MIN_HELDOUT_FIELDS
        or not 0 <= correct <= heldout
    ):
        raise MergerEvidenceError("calibration cohort/held-out sample is too small or inconsistent")
    if Decimal(correct) / Decimal(heldout) < MIN_HELDOUT_PRECISION:
        raise MergerEvidenceError("held-out deal-field precision is below 95%")
    ref = str(document["cohort_ref"])
    digest = str(document["cohort_content_hash"])
    if _CAL_REF.fullmatch(ref) is None or _HASH.fullmatch(digest) is None:
        raise MergerEvidenceError("calibration cohort reference/hash is invalid")
    p_close = _decimal(document["p_close"], field="p_close")
    p_fail = _decimal(document["p_fail"], field="p_fail")
    p_open = _decimal(document["p_open"], field="p_open")
    if any(probability <= 0 or probability >= 1 for probability in (p_close, p_fail, p_open)):
        raise MergerEvidenceError("each calibration outcome must have empirical support")
    if p_close + p_fail + p_open != Decimal("1"):
        raise MergerEvidenceError("calibration probabilities must sum exactly to one")
    break_ratio = _decimal(document["break_price_ratio"], field="break_price_ratio", positive=True)
    open_ratio = _decimal(document["open_price_ratio"], field="open_price_ratio", positive=True)
    if break_ratio > Decimal("1.5") or open_ratio > Decimal("1.5"):
        raise MergerEvidenceError("calibrated price ratio is outside the bounded contract")
    normalized = dict(document)
    normalized.update({
        "frozen_at": frozen.isoformat(),
        "cohort_as_of": cohort_as_of.isoformat(),
        "training_cutoff": training_cutoff.isoformat(),
        "cohort_size": cohort_size,
        "training_cohort_size": training_size,
        "evaluation_cohort_size": evaluation_size,
        "heldout_field_count": heldout,
        "heldout_correct_fields": correct,
        "p_close": str(p_close),
        "p_fail": str(p_fail),
        "p_open": str(p_open),
        "break_price_ratio": str(break_ratio),
        "open_price_ratio": str(open_ratio),
    })
    return normalized


def write_calibration_cohort(raw_root, day: str, key: str, records: list[dict]) -> tuple[str, str]:
    """Persist a raw point-in-time cohort once and return its reference and bound hash."""
    parsed_day = date.fromisoformat(day).isoformat()
    if not _ACCESSION.fullmatch(key) or not records:
        raise MergerEvidenceError("invalid or empty calibration cohort")
    _, digest = write_snapshot(Path(raw_root) / "strategy-calibration", parsed_day, key, records)
    return f"strategy-calibration://{parsed_day}/{key}", digest


def _calibration_records(raw_root: Path, document: dict) -> list[dict]:
    match = _CAL_REF.fullmatch(document["cohort_ref"])
    if match is None:  # defensive; normalization already checked it
        raise MergerEvidenceError("calibration cohort reference is invalid")
    try:
        return read_verified_snapshot(
            raw_root / "strategy-calibration",
            match.group(1),
            match.group(2),
            document["cohort_content_hash"],
        )
    except SnapshotIntegrityError as exc:
        raise MergerEvidenceError("calibration cohort failed integrity verification") from exc


def _normalized_eligibility(values: dict, *, kind: str) -> dict:
    _exact_fields(values, _ELIGIBILITY_FIELDS, kind=kind)
    normalized = dict(values)
    normalized["cash_offer"] = str(
        _decimal(values["cash_offer"], field=f"{kind} cash_offer", positive=True)
    )
    for name in (
        "security_type", "listing_country", "listing_exchange",
        "agreement_status", "lifecycle_status",
    ):
        if not isinstance(values[name], str) or not values[name].strip():
            raise MergerEvidenceError(f"{kind} {name} is invalid")
        normalized[name] = values[name].strip()
    for name in _ELIGIBILITY_FIELDS - {
        "cash_offer", "security_type", "listing_country", "listing_exchange",
        "agreement_status", "lifecycle_status",
    }:
        if type(values[name]) is not bool:
            raise MergerEvidenceError(f"{kind} {name} must be boolean")
    return normalized


def _normalized_source_ref(value: dict, *, field: str) -> dict:
    _exact_fields(value, _SOURCE_REF_FIELDS, kind=field)
    if type(value["event_seq"]) is not int or value["event_seq"] <= 0:
        raise MergerEvidenceError(f"{field} event_seq must be positive")
    raw_ref = str(value["raw_ref"]).strip()
    digest = str(value["content_hash"]).strip()
    if not raw_ref or _HASH.fullmatch(digest) is None:
        raise MergerEvidenceError(f"{field} raw reference/hash is invalid")
    return {"event_seq": value["event_seq"], "raw_ref": raw_ref, "content_hash": digest}


def _cohort_statistics(records: list[dict]) -> dict:
    if len(records) < MIN_COHORT_SIZE:
        raise MergerEvidenceError("calibration cohort is too small")
    outcomes = {"closed": [], "failed": [], "open": []}
    seen_ids: set[str] = set()
    heldout = 0
    correct = 0
    as_of: date | None = None
    training_dates: list[date] = []
    evaluation_dates: list[date] = []
    for record in records:
        _exact_fields(record, _COHORT_FIELDS, kind="calibration cohort row")
        if record["schema_version"] != COHORT_ROW_SCHEMA:
            raise MergerEvidenceError("unsupported calibration cohort row schema")
        deal_id = str(record["deal_id"]).strip()
        if not deal_id or deal_id in seen_ids:
            raise MergerEvidenceError("calibration cohort deal ids must be non-empty and unique")
        seen_ids.add(deal_id)
        symbol = str(record["symbol"]).strip().upper()
        cik = str(record["target_cik"]).strip()
        accession = str(record["source_accession"]).strip()
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol)
            or _CIK.fullmatch(cik) is None
            or _ACCESSION.fullmatch(accession) is None
        ):
            raise MergerEvidenceError("calibration cohort source identity is invalid")
        split = str(record["split"])
        if split not in {"training", "evaluation"}:
            raise MergerEvidenceError("calibration cohort split is invalid")
        selected = _date(record["selected_at"], field="selected_at")
        outcome_as_of = _date(record["outcome_as_of"], field="outcome_as_of")
        if selected >= outcome_as_of:
            raise MergerEvidenceError("calibration outcomes must be strictly after selection")
        outcome = str(record["outcome"])
        if outcome not in outcomes:
            raise MergerEvidenceError("calibration cohort outcome is invalid")
        horizon = (outcome_as_of - selected).days
        if outcome == "open" and horizon != MAX_RESOLUTION_DAYS:
            raise MergerEvidenceError("open calibration outcomes require the exact 30-day horizon")
        if outcome != "open" and horizon > MAX_RESOLUTION_DAYS:
            raise MergerEvidenceError("resolved calibration outcomes exceed the 30-day horizon")
        prediction = _normalized_eligibility(
            record["eligibility_prediction"], kind="eligibility prediction"
        )
        label = _normalized_eligibility(
            record["eligibility_label"], kind="eligibility label"
        )
        for name in (
            "eligibility_source", "entry_price_source", "outcome_source",
            "outcome_price_source",
        ):
            _normalized_source_ref(record[name], field=name)
        entry = _decimal(record["entry_ask"], field="cohort entry_ask", positive=True)
        outcome_price = _decimal(
            record["outcome_price"], field="cohort outcome_price", positive=True
        )
        if split == "training":
            training_dates.append(selected)
            continue
        evaluation_dates.append(selected)
        as_of = outcome_as_of if as_of is None else max(as_of, outcome_as_of)
        heldout += len(_ELIGIBILITY_FIELDS)
        correct += sum(prediction[name] == label[name] for name in _ELIGIBILITY_FIELDS)
        outcomes[outcome].append(outcome_price / entry)
    if len(training_dates) < MIN_TRAINING_COHORT_SIZE:
        raise MergerEvidenceError("calibration training split is too small")
    if len(evaluation_dates) < MIN_EVALUATION_COHORT_SIZE:
        raise MergerEvidenceError("calibration evaluation split is too small")
    training_cutoff = max(training_dates)
    if min(evaluation_dates) <= training_cutoff:
        raise MergerEvidenceError("evaluation deals do not occur strictly after the training cutoff")
    if Decimal(correct) / Decimal(heldout) < MIN_HELDOUT_PRECISION:
        raise MergerEvidenceError("held-out deal-field precision is below 95%")
    if any(not rows for rows in outcomes.values()):
        raise MergerEvidenceError("each calibration outcome needs held-out raw support")
    # Pinned conservative method: use the 95% Wilson LOWER bound for completion, allocate
    # the resulting non-close probability between failure/open in the observed non-close
    # proportions, and use the worst observed failure/open price ratios. This is deliberately
    # adverse and replayable; it cannot be loosened by editing a calibration JSON document.
    count = Decimal(len(evaluation_dates))
    close_hat = Decimal(len(outcomes["closed"])) / count
    z = Decimal("1.96")
    z2 = z * z
    lower_close = (
        close_hat + z2 / (Decimal("2") * count)
        - z * ((close_hat * (1 - close_hat) + z2 / (Decimal("4") * count)) / count).sqrt()
    ) / (1 + z2 / count)
    nonclose_count = Decimal(len(outcomes["failed"]) + len(outcomes["open"]))
    p_fail = (1 - lower_close) * Decimal(len(outcomes["failed"])) / nonclose_count
    p_open = 1 - lower_close - p_fail
    return {
        "cohort_as_of": as_of.isoformat() if as_of is not None else "",
        "cohort_size": len(records),
        "training_cutoff": training_cutoff.isoformat(),
        "training_cohort_size": len(training_dates),
        "evaluation_cohort_size": len(evaluation_dates),
        "heldout_field_count": heldout,
        "heldout_correct_fields": correct,
        "p_close": lower_close,
        "p_fail": p_fail,
        "p_open": p_open,
        "break_price_ratio": min(outcomes["failed"]),
        "open_price_ratio": min(outcomes["open"]),
    }


def calibration_statistics(records: list[dict]) -> dict:
    """Return document-ready statistics from the pinned raw-cohort method."""
    statistics = _cohort_statistics(records)
    return {
        name: str(value) if isinstance(value, Decimal) else value
        for name, value in statistics.items()
    }


def _source_event_in(session, reference: dict, *, event_type: str) -> AuditEntry:
    normalized = _normalized_source_ref(reference, field=f"{event_type} source")
    event = session.execute(
        select(AuditEntry).where(AuditEntry.seq == normalized["event_seq"])
    ).scalar_one_or_none()
    if event is None or event.event_type != event_type:
        raise MergerEvidenceError(f"signed {event_type} source is missing")
    hash_field = "raw_content_hash" if event_type == EVENT_FILING_INGESTED else "content_hash"
    if (
        (event.refs or {}).get("raw_ref") != normalized["raw_ref"]
        or (event.payload or {}).get(hash_field) != normalized["content_hash"]
    ):
        raise MergerEvidenceError(f"signed {event_type} source binding is inconsistent")
    return event


def _verify_price_source_in(
    session, raw_root: Path, reference: dict, *, symbol: str,
    observed_on: date, price: Decimal, entry: bool,
) -> None:
    from orchestrator.state import EVENT_MARKET_DATA_RAW

    event = _source_event_in(session, reference, event_type=EVENT_MARKET_DATA_RAW)
    payload = event.payload or {}
    kind = payload.get("kind")
    if kind not in {"quote", "daily_bars"} or (entry and kind != "quote"):
        raise MergerEvidenceError("calibration price source kind is not admissible")
    raw_ref = str((event.refs or {}).get("raw_ref") or "")
    prefix = "market-data://"
    try:
        day, key = raw_ref.removeprefix(prefix).split("/", 1)
    except ValueError as exc:
        raise MergerEvidenceError("calibration price raw_ref is malformed") from exc
    if (
        not raw_ref.startswith(prefix)
        or payload.get("date") != day
        or payload.get("raw_key") != key
    ):
        raise MergerEvidenceError("calibration price source identity is inconsistent")
    root = raw_root / "market-data" / ("quotes" if kind == "quote" else "daily-bars")
    digest = str(payload.get("content_hash") or "")
    try:
        records = read_verified_snapshot(root, day, key, digest)
    except SnapshotIntegrityError as exc:
        raise MergerEvidenceError(
            "calibration price raw snapshot is missing or corrupt"
        ) from exc
    if len(records) != int(payload.get("count") or 0):
        raise MergerEvidenceError("calibration price snapshot count is inconsistent")
    matches = []
    for raw in records:
        try:
            if kind == "quote":
                raw_symbol = str(raw.get("symbol") or "").upper()
                raw_day = _utc(raw.get("exchange_ts"), field="exchange_ts").date()
                raw_price = _decimal(
                    raw.get("ask") if entry else raw.get("price"),
                    field="calibration quote price", positive=True,
                )
            else:
                raw_symbol = str(key).upper()
                raw_day = _utc(raw.get("t"), field="bar timestamp").date()
                raw_price = _decimal(
                    raw.get("c"), field="calibration close price", positive=True
                )
        except MergerEvidenceError:
            continue
        if raw_symbol == symbol and raw_day == observed_on and raw_price == price:
            matches.append(raw)
    if len(matches) != 1:
        raise MergerEvidenceError("calibration price is not uniquely present in signed raw data")


def _verify_calibration_sources_in(
    session, raw_root: Path, records: list[dict], *, frozen_at: datetime
) -> None:
    raw_store = RawStore(raw_root / "sec", min_free_bytes=0)
    seen_sequences: set[int] = set()
    for record in records:
        cik = str(record["target_cik"])
        accession = str(record["source_accession"])
        eligibility_ref = _normalized_source_ref(
            record["eligibility_source"], field="eligibility_source"
        )
        event = _source_event_in(
            session, eligibility_ref, event_type=EVENT_FILING_INGESTED
        )
        if _utc(event.created_at, field="source event time", allow_naive=True) > frozen_at:
            raise MergerEvidenceError("calibration source was signed after the frozen cutoff")
        payload = event.payload or {}
        if (
            payload.get("cik") != cik
            or payload.get("accession_no") != accession
            or (event.refs or {}).get("raw_ref") != accession
            or not raw_store.verify(accession)
            or raw_store.stored_hash(accession) != eligibility_ref["content_hash"]
            or _date(payload.get("filing_date"), field="eligibility source filing_date")
            > _date(record["selected_at"], field="selected_at")
        ):
            raise MergerEvidenceError("calibration filing source binding is inconsistent")
        entry_ref = _normalized_source_ref(
            record["entry_price_source"], field="entry_price_source"
        )
        outcome_evidence_ref = _normalized_source_ref(
            record["outcome_source"], field="outcome_source"
        )
        outcome_event = _source_event_in(
            session, outcome_evidence_ref, event_type=EVENT_FILING_INGESTED
        )
        outcome_payload = outcome_event.payload or {}
        outcome_accession = str(outcome_payload.get("accession_no") or "")
        outcome_filed_on = _date(
            outcome_payload.get("filing_date"), field="outcome source filing_date"
        )
        selected_on = _date(record["selected_at"], field="selected_at")
        outcome_as_of = _date(record["outcome_as_of"], field="outcome_as_of")
        if (
            outcome_payload.get("cik") != cik
            or outcome_accession == accession
            or (outcome_event.refs or {}).get("raw_ref") != outcome_accession
            or not raw_store.verify(outcome_accession)
            or raw_store.stored_hash(outcome_accession)
            != outcome_evidence_ref["content_hash"]
            or not selected_on < outcome_filed_on <= outcome_as_of
        ):
            raise MergerEvidenceError("calibration outcome source binding is inconsistent")
        outcome_ref = _normalized_source_ref(
            record["outcome_price_source"], field="outcome_price_source"
        )
        row_sequences = {
            eligibility_ref["event_seq"], entry_ref["event_seq"],
            outcome_evidence_ref["event_seq"], outcome_ref["event_seq"],
        }
        if len(row_sequences) != 4 or row_sequences & seen_sequences:
            raise MergerEvidenceError("calibration source events must be unique per observation")
        seen_sequences.update(row_sequences)
        symbol = str(record["symbol"]).upper()
        _verify_price_source_in(
            session, raw_root, entry_ref, symbol=symbol,
            observed_on=_date(record["selected_at"], field="selected_at"),
            price=_decimal(record["entry_ask"], field="entry_ask", positive=True),
            entry=True,
        )
        _verify_price_source_in(
            session, raw_root, outcome_ref, symbol=symbol,
            observed_on=_date(record["outcome_as_of"], field="outcome_as_of"),
            price=_decimal(record["outcome_price"], field="outcome_price", positive=True),
            entry=False,
        )
        for reference in (outcome_evidence_ref, entry_ref, outcome_ref):
            source_event = session.execute(
                select(AuditEntry).where(AuditEntry.seq == reference["event_seq"])
            ).scalar_one()
            if _utc(
                source_event.created_at, field="source event time", allow_naive=True
            ) > frozen_at:
                raise MergerEvidenceError("calibration source was signed after the frozen cutoff")


def _verify_calibration_statistics(
    raw_root: Path, document: dict, *, session=None
) -> None:
    records = _calibration_records(raw_root, document)
    expected = _cohort_statistics(records)
    if (
        document["cohort_as_of"] != expected["cohort_as_of"]
        or document["cohort_size"] != expected["cohort_size"]
        or document["training_cutoff"] != expected["training_cutoff"]
        or document["training_cohort_size"] != expected["training_cohort_size"]
        or document["evaluation_cohort_size"] != expected["evaluation_cohort_size"]
        or document["heldout_field_count"] != expected["heldout_field_count"]
        or document["heldout_correct_fields"] != expected["heldout_correct_fields"]
        or any(_decimal(document[name], field=name) != expected[name]
               for name in ("p_close", "p_fail", "p_open",
                            "break_price_ratio", "open_price_ratio"))
    ):
        raise MergerEvidenceError("calibration statistics do not match the raw cohort")
    if session is not None:
        _verify_calibration_sources_in(
            session,
            raw_root,
            records,
            frozen_at=_utc(document["frozen_at"], field="frozen_at"),
        )


@contextmanager
def _authority_session(state):
    """Yield one DB snapshot containing both a verified ledger and its covered rows."""
    anchor = getattr(state, "anchor_store", None)
    latest = anchor.latest() if anchor is not None else None
    with state.session_factory() as session, session.begin():
        result = (
            state.audit.verify_session_against_anchor(session, *latest)
            if latest is not None
            else state.audit.verify_session(session)
        )
        if not result.ok:
            raise MergerEvidenceError(f"audit chain is invalid: {result.error}")
        yield session


def _latest_event_in(session, event_type: str, field: str, value: str) -> AuditEntry | None:
    rows = session.execute(
        select(AuditEntry)
        .where(AuditEntry.event_type == event_type)
        .order_by(AuditEntry.seq.desc())
    ).scalars().all()
    return next((item for item in rows if (item.payload or {}).get(field) == value), None)


def _load_signed_in(session, model, event_type: str, field: str, value: str):
    event = _latest_event_in(session, event_type, field, value)
    if event is None:
        raise MergerEvidenceError(f"signed {event_type} is missing")
    row = session.get(model, (event.refs or {}).get("row_id"))
    if row is None:
        raise MergerEvidenceError(f"signed {event_type} row is missing")
    digest = _canonical_hash(row.document)
    if (
        digest != row.content_hash
        or (event.payload or {}).get("content_hash") != digest
        or (event.refs or {}).get("model") != row.__tablename__
    ):
        raise MergerEvidenceError(f"{event_type} hash binding failed")
    return row


def _verify_company_attribution(session, *, symbol: str, cik: str) -> Company:
    company = session.execute(select(Company).where(
        Company.ticker == symbol,
        Company.cik == cik,
        Company.active.is_(True),
    )).scalar_one_or_none()
    if company is None:
        raise MergerEvidenceError("signed target symbol/CIK provenance is incomplete")
    events = session.execute(select(AuditEntry).where(
        AuditEntry.event_type == EVENT_COMPANY_ATTRIBUTION
    )).scalars().all()
    matches = [event for event in events if (
        (event.refs or {}).get("row_id") == company.id
        and (event.refs or {}).get("cik") == cik
        and (event.payload or {}).get("ticker") == symbol
        and (event.payload or {}).get("cik") == cik
        and (event.payload or {}).get("action") in {"created", "reactivated"}
    )]
    if not matches:
        raise MergerEvidenceError("signed target symbol/CIK attribution is missing")
    return company


def _filing_event_map(session, *, cik: str, raw_store: RawStore) -> dict[str, Filing]:
    events = session.execute(select(AuditEntry).where(
        AuditEntry.event_type == EVENT_FILING_INGESTED
    ).order_by(AuditEntry.seq)).scalars().all()
    signed: dict[str, Filing] = {}
    for event in events:
        payload = event.payload or {}
        if payload.get("cik") != cik:
            continue
        refs = event.refs or {}
        filing = session.get(Filing, refs.get("row_id"))
        accession = str(payload.get("accession_no") or "")
        if (
            filing is None
            or refs.get("model") != Filing.__tablename__
            or filing.cik != cik
            or filing.accession_no != accession
            or filing.raw_ref != refs.get("raw_ref")
            or filing.raw_ref != accession
            or filing.form_type.value != payload.get("form_type")
            or (
                payload.get("filing_date") is not None
                and filing.filing_date.isoformat() != payload.get("filing_date")
            )
            or _utc(filing.detection_ts, field="detection_ts", allow_naive=True)
            != _utc(payload.get("detection_ts"), field="detection_ts")
            or not raw_store.verify(accession)
            or raw_store.stored_hash(accession) != payload.get("raw_content_hash")
        ):
            raise MergerEvidenceError("signed target filing row/raw binding failed")
        prior = signed.get(accession)
        if prior is not None and prior.id != filing.id:
            raise MergerEvidenceError("target filing has conflicting signed bindings")
        signed[accession] = filing
    return signed


def _filing_set_hash_in(session, *, cik: str) -> str:
    """Hash the complete domain-row set so unsigned insertions are also detectable."""
    filings = session.execute(
        select(Filing).where(Filing.cik == cik).order_by(Filing.accession_no, Filing.id)
    ).scalars().all()
    return _canonical_hash({
        "cik": cik,
        "filings": [
            {
                "id": filing.id,
                "accession_no": filing.accession_no,
                "form_type": filing.form_type.value,
                "filing_date": filing.filing_date.isoformat(),
                "detection_ts": _utc(
                    filing.detection_ts, field="detection_ts", allow_naive=True
                ).isoformat(),
                "raw_ref": filing.raw_ref,
                "primary_doc": filing.primary_doc,
            }
            for filing in filings
        ],
    })


def _latest_filing_event_seq_in(session, *, cik: str) -> int:
    events = session.execute(
        select(AuditEntry)
        .where(AuditEntry.event_type == EVENT_FILING_INGESTED)
        .order_by(AuditEntry.seq.desc())
    ).scalars().all()
    return next(
        (event.seq for event in events if (event.payload or {}).get("cik") == cik),
        0,
    )


def _current_edgar_binding_in(
    session, *, cik: str, now: datetime, process_epoch: str
) -> dict:
    events = session.execute(
        select(AuditEntry)
        .where(AuditEntry.event_type == EVENT_EDGAR_POLL_COMPLETED)
        .order_by(AuditEntry.seq.desc())
    ).scalars().all()
    event = next(
        (candidate for candidate in events
         if (candidate.payload or {}).get("cik") == cik),
        None,
    )
    if event is None:
        raise EdgarFreshnessError("target has no audited successful EDGAR poll")
    payload = event.payload or {}
    if (event.refs or {}).get("cik") != cik:
        raise EdgarFreshnessError("EDGAR poll CIK binding is inconsistent")
    if not process_epoch or payload.get("process_epoch") != process_epoch:
        raise EdgarFreshnessError("target has no successful EDGAR poll in this process")
    completed = _utc(payload.get("completed_at"), field="EDGAR completed_at")
    age = now - completed
    if age < timedelta(0) or age > MAX_EDGAR_POLL_AGE:
        raise EdgarFreshnessError("target EDGAR poll is stale or future-dated")
    filing_event_seq = _latest_filing_event_seq_in(session, cik=cik)
    if filing_event_seq <= 0:
        raise EdgarFreshnessError("target has no signed filing event")
    if payload.get("filing_event_seq") != filing_event_seq:
        raise EdgarFreshnessError(
            "latest target filing is not covered by a completed EDGAR poll"
        )
    return {
        "filing_event_seq": filing_event_seq,
        "filing_set_hash": _filing_set_hash_in(session, cik=cik),
        "edgar_poll_event_seq": event.seq,
        "edgar_poll_completed_at": completed.isoformat(),
    }


def _require_same_authority_binding(document: dict, current: dict) -> None:
    for name in _DEAL_BINDING_FIELDS:
        if document.get(name) != current[name]:
            raise MergerEvidenceError(
                "target filing/EDGAR authority changed after the reviewed snapshot"
            )


def _require_current_deal_filing_set(document: dict, current: dict) -> None:
    """A newer healthy poll may refresh liveness, but it may not change reviewed filings."""
    for name in ("filing_event_seq", "filing_set_hash"):
        if document.get(name) != current[name]:
            raise MergerEvidenceError(
                "target filing set changed after the reviewed deal snapshot"
            )
    if current["edgar_poll_event_seq"] < document.get("edgar_poll_event_seq", 0):
        raise MergerEvidenceError("EDGAR poll authority rolled back")


def _verify_deal_provenance_in(
    session, *, document: dict, raw_store: RawStore, require_new_review: bool
) -> None:
    symbol = document["symbol"]
    cik = document["target_cik"]
    announced = _utc(document["announcement_at"], field="announcement_at")
    observed = _utc(document["observed_at"], field="observed_at")
    _verify_company_attribution(session, symbol=symbol, cik=cik)
    filings = session.execute(select(Filing).where(
        Filing.cik == cik
    ).order_by(Filing.detection_ts.desc())).scalars().all()
    signed = _filing_event_map(session, cik=cik, raw_store=raw_store)
    reviewed = set(document["source_accessions"])
    if any(
        announced <= _utc(filing.detection_ts, field="detection_ts", allow_naive=True) <= observed
        and filing.accession_no not in reviewed
        for filing in filings
    ):
        raise MergerEvidenceError(
            "target filing in the announcement-to-review window was not reviewed"
        )
    for accession in reviewed:
        filing = signed.get(accession)
        if (
            filing is None
            or _utc(filing.detection_ts, field="detection_ts", allow_naive=True) > observed
            or raw_store.stored_hash(accession) != document["filing_hashes"][accession]
        ):
            raise MergerEvidenceError("deal filing provenance lacks an exact signed binding")
    if require_new_review and any(
        filing.accession_no not in reviewed
        and _utc(filing.detection_ts, field="detection_ts", allow_naive=True) > observed
        for filing in filings
    ):
        raise MergerEvidenceError("new target filing requires deal review")


def _record_immutable(
    state, row, event_type: str, *, actor: str, payload: dict,
    raw_store: RawStore | None = None, now: datetime | None = None,
    calibration_raw_root: Path | None = None,
):
    model = type(row)
    with _authority_session(state) as session:
        if event_type == EVENT_DEAL:
            if raw_store is None or now is None:
                raise MergerEvidenceError("deal persistence lacks its authority verifier")
            document = _normalized_deal(row.document)
            _verify_deal_provenance_in(
                session, document=document, raw_store=raw_store, require_new_review=True
            )
            _require_same_authority_binding(
                document,
                _current_edgar_binding_in(
                    session, cik=document["target_cik"], now=now,
                    process_epoch=state.process_epoch,
                ),
            )
        elif event_type == EVENT_CALIBRATION:
            if calibration_raw_root is None:
                raise MergerEvidenceError("calibration persistence lacks source verification")
            calibration_document = _normalized_calibration(row.document)
            _verify_calibration_statistics(
                calibration_raw_root, calibration_document, session=session
            )
        elif event_type == EVENT_EDGE:
            document = row.document
            deal = _load_signed_in(
                session, MergerDealEvidence, EVENT_DEAL, "symbol", row.symbol
            )
            calibration = _load_signed_in(
                session, MergerCalibrationEvidence, EVENT_CALIBRATION, "version", SOURCE_ID
            )
            if (
                document.get("deal_evidence_id") != deal.id
                or document.get("deal_content_hash") != deal.content_hash
                or document.get("calibration_evidence_id") != calibration.id
                or document.get("calibration_content_hash") != calibration.content_hash
            ):
                raise MergerEvidenceError(
                    "edge authority inputs were superseded before persistence"
                )
            if calibration_raw_root is None:
                raise MergerEvidenceError("edge persistence lacks calibration verification")
            _verify_calibration_statistics(
                calibration_raw_root,
                _normalized_calibration(calibration.document),
                session=session,
            )
            deal_document = _normalized_deal(deal.document)
            if raw_store is None or now is None:
                raise MergerEvidenceError("edge persistence lacks its authority verifier")
            _verify_deal_provenance_in(
                session,
                document=deal_document,
                raw_store=raw_store,
                require_new_review=True,
            )
            current = _current_edgar_binding_in(
                session, cik=deal_document["target_cik"], now=now,
                process_epoch=state.process_epoch,
            )
            _require_current_deal_filing_set(deal_document, current)
            _require_same_authority_binding(document, current)
        existing = session.get(model, row.id)
        if existing is not None:
            if existing.content_hash != row.content_hash:
                raise MergerEvidenceError("immutable evidence id collision")
            event = _latest_event_in(
                session,
                event_type,
                "symbol" if event_type != EVENT_CALIBRATION else "version",
                row.symbol if event_type != EVENT_CALIBRATION else row.version,
            )
            if event is None or (event.refs or {}).get("row_id") != row.id:
                raise MergerEvidenceError("existing evidence row is not the latest signed record")
            session.expunge(existing)
            return existing
        session.add(row)
        session.flush()
        state.audit.append_in(
            session,
            event_type,
            actor=actor,
            refs={"model": row.__tablename__, "row_id": row.id},
            payload=payload,
        )
        session.expunge(row)
    return row


def record_deal_evidence(state, document: dict, *, raw_root, actor: str = "operator"):
    """Validate SEC provenance and append one immutable operator-reviewed deal version."""
    normalized = _normalized_deal(document, require_authority_binding=False)
    now = _utc(state.clock.now(), field="clock")
    observed = _utc(normalized["observed_at"], field="observed_at")
    if observed > now or now - observed > MAX_DEAL_OBSERVATION_AGE:
        raise MergerEvidenceError("deal review timestamp is future or stale")
    raw_store = RawStore(Path(raw_root) / "sec", min_free_bytes=0)
    with _authority_session(state) as session:
        _verify_deal_provenance_in(
            session, document=normalized, raw_store=raw_store, require_new_review=False
        )
        normalized.update(_current_edgar_binding_in(
            session, cik=normalized["target_cik"], now=now,
            process_epoch=state.process_epoch,
        ))
    normalized = _normalized_deal(normalized)
    digest = _canonical_hash(normalized)
    row = MergerDealEvidence(
        id=f"mdeal_{digest}", symbol=normalized["symbol"], target_cik=normalized["target_cik"],
        document=normalized, content_hash=digest,
        observed_at=observed, created_at=now,
    )
    return _record_immutable(
        state, row, EVENT_DEAL, actor=actor,
        payload={"symbol": row.symbol, "content_hash": digest, "schema_version": DEAL_SCHEMA},
        raw_store=raw_store, now=now,
    )


def record_calibration_evidence(state, document: dict, *, raw_root, actor: str = "operator"):
    """Append a frozen calibration only after its raw cohort hash verifies."""
    normalized = _normalized_calibration(document)
    raw_root = Path(raw_root)
    with _authority_session(state) as session:
        _verify_calibration_statistics(raw_root, normalized, session=session)
    digest = _canonical_hash(normalized)
    now = state.clock.now()
    row = MergerCalibrationEvidence(
        id=f"mcal_{digest}", version=SOURCE_ID, document=normalized, content_hash=digest,
        frozen_at=_utc(normalized["frozen_at"], field="frozen_at"), created_at=now,
    )
    return _record_immutable(
        state, row, EVENT_CALIBRATION, actor=actor,
        payload={"version": SOURCE_ID, "content_hash": digest,
                  "schema_version": CALIBRATION_SCHEMA},
        calibration_raw_root=raw_root,
    )


class CashMergerEdgeProvider:
    """Return a verified 30-day cash-merger edge or fail closed to ``None``."""

    def __init__(self, state, *, raw_root, strategy_watchlist) -> None:
        self.state = state
        self.raw_root = Path(raw_root)
        self.strategy_watchlist = frozenset(str(s).strip().upper() for s in strategy_watchlist)
        self._reason = (
            "operator strategy watchlist is empty"
            if not self.strategy_watchlist
            else "not evaluated"
        )
        self._lock = threading.RLock()

    def _fail(self, reason: str) -> None:
        with self._lock:
            self._reason = reason

    def _verify_semantic_authority(
        self, *, deal_document: dict, calibration_document: dict
    ) -> None:
        """Production-closed pending a separately scoped independent attestor.

        Hashing operator-authored labels or deal terms cannot make those claims order
        authority. There is intentionally no constructor/configuration bypass for this gate.
        """
        del deal_document, calibration_document
        raise SemanticEvidenceUnavailable(
            "independent deal-term and calibration semantic verifier is not provisioned"
        )

    def status_text(self) -> str:
        with self._lock:
            last_evaluation = self._reason
        if not self.strategy_watchlist:
            return f"unavailable — {SOURCE_ID}: operator strategy watchlist is empty"
        try:
            now = _utc(self.state.clock.now(), field="clock")
            calibration = self._verify_calibration(now)
            market_data = getattr(self.state, "market_data", None)
            if market_data is None:
                raise MergerEvidenceError("market data is unavailable")
            for symbol in sorted(self.strategy_watchlist):
                deal, _binding = self._verify_deal(symbol, now)
                self._verify_semantic_authority(
                    deal_document=deal.document,
                    calibration_document=calibration.document,
                )
                quote = market_data.quote(symbol)
                self._verify_quote(symbol, quote, now)
                self._verify_metrics(symbol, now)
                if _utc(deal.document["observed_at"], field="observed_at") > datetime.fromtimestamp(
                    int(quote["ts_ns"]) / 1_000_000_000, tz=timezone.utc
                ):
                    raise MergerEvidenceError("quote predates the reviewed deal evidence")
        except Exception as exc:  # noqa: BLE001 - status must expose current fail-closed posture
            return f"unavailable — {SOURCE_ID}: {type(exc).__name__}: {exc}"
        return (
            f"available — {SOURCE_ID}: {len(self.strategy_watchlist)} target(s) verified; "
            f"last_evaluation={last_evaluation}"
        )

    def _load_signed(self, model, event_type: str, field: str, value: str):
        with _authority_session(self.state) as session:
            row = _load_signed_in(session, model, event_type, field, value)
            session.expunge(row)
            return row

    def _verify_deal(self, symbol: str, now: datetime) -> tuple[MergerDealEvidence, dict]:
        raw_store = RawStore(self.raw_root / "sec", min_free_bytes=0)
        with _authority_session(self.state) as session:
            row = _load_signed_in(
                session, MergerDealEvidence, EVENT_DEAL, "symbol", symbol
            )
            document = _normalized_deal(row.document)
            if row.symbol != symbol or row.target_cik != document["target_cik"]:
                raise MergerEvidenceError("deal row identity does not match signed evidence")
            observed = _utc(document["observed_at"], field="observed_at")
            if observed > now or now - observed > MAX_DEAL_OBSERVATION_AGE:
                raise MergerEvidenceError("deal review is stale")
            if document["lifecycle_status"] != "active":
                raise MergerEvidenceError("deal is terminated")
            if any(document[name] for name in (
                "has_cvr", "has_earnout", "has_stock", "uses_options_or_leverage",
                "is_rumor_or_competing_bid",
            )):
                raise MergerEvidenceError("deal consideration/scope is ineligible")
            if (
                document["current_accession"] != document["announcement_accession"]
                and not document["amendment_reviewed"]
            ):
                raise MergerEvidenceError("deal amendment has not been reviewed")
            resolution = _date(
                document["expected_resolution_date"], field="expected_resolution_date"
            )
            if (
                resolution < now.date()
                or resolution > now.date() + timedelta(days=MAX_RESOLUTION_DAYS)
            ):
                raise MergerEvidenceError("deal is outside the 30-day strategy horizon")
            _verify_deal_provenance_in(
                session, document=document, raw_store=raw_store, require_new_review=True
            )
            current = _current_edgar_binding_in(
                session, cik=document["target_cik"], now=now,
                process_epoch=self.state.process_epoch,
            )
            _require_current_deal_filing_set(document, current)
            session.expunge(row)
            return row, current

    def _verify_calibration(self, now: datetime) -> MergerCalibrationEvidence:
        with _authority_session(self.state) as session:
            row = _load_signed_in(
                session, MergerCalibrationEvidence, EVENT_CALIBRATION, "version", SOURCE_ID
            )
            document = _normalized_calibration(row.document)
            if row.version != SOURCE_ID:
                raise MergerEvidenceError("calibration row version does not match signed evidence")
            frozen = _utc(document["frozen_at"], field="frozen_at")
            if frozen > now or now - frozen > MAX_CALIBRATION_AGE:
                raise MergerEvidenceError("calibration is stale")
            _verify_calibration_statistics(self.raw_root, document, session=session)
            session.expunge(row)
            return row

    def _verify_quote(self, symbol: str, quote: dict, now: datetime) -> tuple[Decimal, str, str]:
        if not isinstance(quote, dict) or str(quote.get("symbol", "")).upper() != symbol:
            raise MergerEvidenceError("quote symbol mismatch")
        bid = _decimal(quote.get("bid"), field="bid", positive=True)
        ask = _decimal(quote.get("ask"), field="ask", positive=True)
        if ask < bid or ask < MIN_PRICE:
            raise MergerEvidenceError("quote is crossed or below the price floor")
        price = _decimal(quote.get("price"), field="price", positive=True)
        spread_pct = _decimal(quote.get("spread_pct"), field="spread_pct")
        expected_price = (bid + ask) / Decimal("2")
        expected_spread_pct = Decimal("100") * (ask - bid) / expected_price
        if price != expected_price or spread_pct != expected_spread_pct:
            raise MergerEvidenceError("quote derived fields diverge from bid/ask")
        spread_bps = Decimal("10000") * (ask - bid) / ask
        if spread_bps > MAX_SPREAD_BPS:
            raise MergerEvidenceError("quote spread is too wide")
        feed = str(quote.get("feed") or "").lower()
        if feed not in ALLOWED_QUOTE_FEEDS:
            raise MergerEvidenceError("quote feed is not approved")
        ts_ns = int(quote.get("ts_ns"))
        stamp = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        age = (now - stamp).total_seconds()
        market_data = getattr(self.state, "market_data", None)
        max_age = float(getattr(market_data, "max_age_s", 120.0))
        if age < 0 or age > max_age:
            raise MergerEvidenceError("quote is stale")
        raw_ref = str(quote.get("raw_ref") or "")
        prefix = "market-data://"
        if not raw_ref.startswith(prefix):
            raise MergerEvidenceError("quote raw snapshot identity is missing")
        try:
            day, key = raw_ref[len(prefix):].split("/", 1)
        except ValueError as exc:
            raise MergerEvidenceError("quote raw snapshot identity is malformed") from exc
        if day != stamp.date().isoformat() or not key.startswith(f"{symbol}-quote-{ts_ns}-"):
            raise MergerEvidenceError("quote raw snapshot identity does not match the quote")
        with _authority_session(self.state) as session:
            events = session.execute(select(AuditEntry).where(
                AuditEntry.event_type == "market_data_raw_snapshot"
            ).order_by(AuditEntry.seq.desc())).scalars().all()
        matches = [row for row in events if (row.refs or {}).get("raw_ref") == raw_ref]
        if not matches:
            raise MergerEvidenceError("quote has no signed raw snapshot")
        hashes = {(row.payload or {}).get("content_hash") for row in matches}
        if len(hashes) != 1 or None in hashes:
            raise MergerEvidenceError("quote raw snapshot has conflicting hash bindings")
        digest = next(iter(hashes))
        if quote.get("content_hash") != digest:
            raise MergerEvidenceError("quote raw hash identity does not match its signed binding")
        quote_root = self.raw_root / "market-data" / "quotes"
        try:
            records = read_verified_snapshot(quote_root, day, key, digest)
        except SnapshotIntegrityError as exc:
            raise MergerEvidenceError("quote raw snapshot is missing or corrupt") from exc
        if len(records) != 1:
            raise MergerEvidenceError("quote raw snapshot shape is invalid")
        raw = records[0]
        if (
            str(raw.get("symbol", "")).upper() != symbol
            or int(raw.get("exchange_ts_ns", -1)) != ts_ns
            or _decimal(raw.get("price"), field="raw price", positive=True) != price
            or _decimal(raw.get("spread_pct"), field="raw spread_pct") != spread_pct
            or _decimal(raw.get("bid"), field="raw bid", positive=True) != bid
            or _decimal(raw.get("ask"), field="raw ask", positive=True) != ask
            or str(raw.get("feed", "")).lower() != feed
        ):
            raise MergerEvidenceError("quote does not match its signed raw snapshot")
        return ask, raw_ref, digest

    def _verify_metrics(self, symbol: str, now: datetime, metric=None) -> dict:
        market_data = getattr(self.state, "market_data", None)
        if metric is None:
            metric = market_data.metrics(symbol) if market_data is not None else None
        if metric is None or str(metric.symbol).upper() != symbol:
            raise MergerEvidenceError("market metrics are unavailable")
        with _authority_session(self.state) as session:
            events = session.execute(select(AuditEntry).where(
                AuditEntry.event_type == "market_data_raw_snapshot"
            ).order_by(AuditEntry.seq.desc())).scalars().all()
        matches = [row for row in events if (
            (row.payload or {}).get("kind") == "daily_bars"
            and (row.payload or {}).get("raw_key") == symbol
        )]
        if not matches:
            raise MergerEvidenceError("daily-bar metrics have no signed raw snapshot")
        latest = matches[0]
        day = str((latest.payload or {}).get("date") or "")
        digest = str((latest.payload or {}).get("content_hash") or "")
        if _HASH.fullmatch(digest) is None:
            raise MergerEvidenceError("daily-bar snapshot hash is invalid")
        conflicts = {
            (row.payload or {}).get("content_hash")
            for row in matches
            if (row.payload or {}).get("date") == day
        }
        if len(conflicts) != 1:
            raise MergerEvidenceError("daily-bar snapshot has conflicting hash bindings")
        bars_root = self.raw_root / "market-data" / "daily-bars"
        try:
            bars = read_verified_snapshot(bars_root, day, symbol, digest)
        except SnapshotIntegrityError as exc:
            raise MergerEvidenceError("daily-bar snapshot is missing or corrupt") from exc
        derived = calculate_daily_metrics(symbol, bars)
        adv = _decimal(derived.median_daily_dollar_volume, field="ADV", positive=True)
        volatility = _decimal(
            derived.realized_volatility, field="realized volatility", positive=True
        )
        if (
            metric.as_of != derived.as_of
            or _decimal(metric.median_daily_dollar_volume, field="cached ADV", positive=True) != adv
            or _decimal(
                metric.realized_volatility,
                field="cached realized volatility",
                positive=True,
            ) != volatility
            or int(metric.observations) != int(derived.observations)
        ):
            raise MergerEvidenceError("cached market metrics diverge from signed raw bars")
        age_days = (now.date() - metric.as_of).days
        if (
            adv < MIN_DOLLAR_ADV or int(metric.observations) < MIN_METRIC_OBSERVATIONS
            or age_days < 0 or age_days > MAX_METRIC_AGE_DAYS
        ):
            raise MergerEvidenceError("market liquidity metrics fail the strategy gate")
        return {
            "as_of": metric.as_of.isoformat(),
            "median_daily_dollar_volume": str(adv),
            "realized_volatility": str(volatility),
            "observations": int(metric.observations),
            "raw_ref": (latest.refs or {}).get("raw_ref"),
            "content_hash": digest,
        }

    def _estimate(self, proposal: dict, quote: dict, market_metric=None) -> EdgeEstimate:
        now = _utc(self.state.clock.now(), field="clock")
        symbol = str(proposal.get("symbol", "")).strip().upper()
        if symbol not in self.strategy_watchlist:
            raise MergerEvidenceError("symbol is not operator-approved for the merger strategy")
        if str(proposal.get("side", "")).lower() != "buy":
            raise MergerEvidenceError("strategy permits long-target buys only")
        if proposal.get("asset_type") != "equity":
            raise MergerEvidenceError("strategy permits common-equity proposals only")
        if proposal.get("time_horizon") != TIME_HORIZON:
            raise MergerEvidenceError("proposal horizon is not long")
        if set(proposal) & {
            "expected_edge", "expected_return", "expected_return_bps",
            "expected_gross_return_bps", "p_close", "p_fail", "p_open",
        }:
            raise MergerEvidenceError("proposal contains model-authored return fields")
        if proposal.get("quantity") in (None, ""):
            raise MergerEvidenceError("resolved order quantity is missing")
        if not str(proposal.get("proposal_id") or "").strip():
            raise MergerEvidenceError("proposal id is missing")

        deal, filing_binding = self._verify_deal(symbol, now)
        calibration = self._verify_calibration(now)
        self._verify_semantic_authority(
            deal_document=deal.document,
            calibration_document=calibration.document,
        )
        ask, quote_ref, quote_hash = self._verify_quote(symbol, quote, now)
        metrics = self._verify_metrics(symbol, now, market_metric)
        deal_doc = deal.document
        cal_doc = calibration.document
        if _utc(deal_doc["observed_at"], field="observed_at") > datetime.fromtimestamp(
            int(quote["ts_ns"]) / 1_000_000_000, tz=timezone.utc
        ):
            raise MergerEvidenceError("quote predates the reviewed deal evidence")

        cash_offer = _decimal(deal_doc["cash_offer"], field="cash_offer", positive=True)
        p_close = _decimal(cal_doc["p_close"], field="p_close")
        p_fail = _decimal(cal_doc["p_fail"], field="p_fail")
        p_open = _decimal(cal_doc["p_open"], field="p_open")
        break_price = ask * _decimal(
            cal_doc["break_price_ratio"], field="break_price_ratio", positive=True
        )
        horizon_price = ask * _decimal(
            cal_doc["open_price_ratio"], field="open_price_ratio", positive=True
        )
        gross_bps = Decimal("10000") * (
            p_close * (cash_offer / ask - 1)
            + p_fail * (break_price / ask - 1)
            + p_open * (horizon_price / ask - 1)
        )
        if not gross_bps.is_finite():
            raise MergerEvidenceError("computed edge is non-finite")

        document = {
            "schema_version": EDGE_SCHEMA,
            "source_id": SOURCE_ID,
            "proposal_id": str(proposal.get("proposal_id") or ""),
            "symbol": symbol,
            "side": "buy",
            "quantity": str(proposal["quantity"]),
            "time_horizon": TIME_HORIZON,
            "deal_evidence_id": deal.id,
            "deal_content_hash": deal.content_hash,
            "calibration_evidence_id": calibration.id,
            "calibration_content_hash": calibration.content_hash,
            "filing_event_seq": filing_binding["filing_event_seq"],
            "filing_set_hash": filing_binding["filing_set_hash"],
            "edgar_poll_event_seq": filing_binding["edgar_poll_event_seq"],
            "edgar_poll_completed_at": filing_binding["edgar_poll_completed_at"],
            "quote_ref": quote_ref,
            "quote_content_hash": quote_hash,
            "quote_ts_ns": int(quote["ts_ns"]),
            "ask": str(ask),
            "cash_offer": str(cash_offer),
            "p_close": str(p_close),
            "p_fail": str(p_fail),
            "p_open": str(p_open),
            "break_price": str(break_price),
            "horizon_price": str(horizon_price),
            "metrics": metrics,
            "expected_gross_return_bps": str(gross_bps),
            "computed_at": now.isoformat(),
        }
        digest = _canonical_hash(document)
        row = MergerEdgeEvidence(
            id=f"medge_{digest}", symbol=symbol, document=document, content_hash=digest,
            computed_at=now, created_at=now,
        )
        _record_immutable(
            self.state, row, EVENT_EDGE, actor="cash_merger_edge",
            payload={"symbol": symbol, "content_hash": digest,
                     "schema_version": EDGE_SCHEMA, "source_id": SOURCE_ID},
            raw_store=RawStore(self.raw_root / "sec", min_free_bytes=0), now=now,
            calibration_raw_root=self.raw_root,
        )
        return EdgeEstimate(
            expected_gross_return_bps=gross_bps,
            time_horizon=TIME_HORIZON,
            source_id=SOURCE_ID,
            evidence_ref=f"merger-edge://{row.id}",
            computed_at_ns=to_ns(now),
            verified=True,
        )

    def estimate(self, proposal: dict, *, quote: dict, market_metric=None) -> EdgeEstimate | None:
        try:
            estimate = self._estimate(dict(proposal), dict(quote), market_metric)
        except Exception as exc:  # noqa: BLE001 - any evidence/data fault means no edge authority
            self._fail(f"{type(exc).__name__}: {exc}")
            if isinstance(exc, EdgarFreshnessError):
                from orchestrator.breakers import BreakerEngine

                engine = BreakerEngine(self.state)
                if not engine.is_watch_only("sec:edgar"):
                    engine.enter_watch_only(
                        "sec:edgar", str(exc), actor="cash_merger_edge"
                    )
            return None
        with self._lock:
            self._reason = "latest evidence verified"
        return estimate
