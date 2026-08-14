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
from orchestrator.state import EVENT_FILING_INGESTED
from storage.parquet_store import read_snapshot, verify_snapshot, write_snapshot

DEAL_SCHEMA = "merger_deal.v1"
CALIBRATION_SCHEMA = "merger_calibration.v1"
EDGE_SCHEMA = "merger_edge_evidence.v1"
COHORT_ROW_SCHEMA = "merger_calibration_cohort_row.v1"
SOURCE_ID = "cash_merger_30d.v1"
TIME_HORIZON = "long"
EVENT_DEAL = "merger_deal_evidence"
EVENT_CALIBRATION = "merger_calibration_evidence"
EVENT_EDGE = "merger_edge_computed"

MAX_RESOLUTION_DAYS = 30
MAX_DEAL_OBSERVATION_AGE = timedelta(hours=24)
MAX_CALIBRATION_AGE = timedelta(days=365)
MIN_PRICE = Decimal("5")
MIN_DOLLAR_ADV = Decimal("20000000")
MIN_METRIC_OBSERVATIONS = 20
MAX_METRIC_AGE_DAYS = 7
MAX_SPREAD_BPS = Decimal("50")
MIN_COHORT_SIZE = 30
MIN_HELDOUT_FIELDS = 20
MIN_HELDOUT_PRECISION = Decimal("0.95")
ALLOWED_QUOTE_FEEDS = frozenset({"iex", "sip"})

_CIK = re.compile(r"^\d{10}$")
_ACCESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CAL_REF = re.compile(r"^strategy-calibration://(\d{4}-\d{2}-\d{2})/([A-Za-z0-9._-]+)$")
_COHORT_FIELDS = frozenset({
    "schema_version", "deal_id", "selected_at", "outcome_as_of", "outcome",
    "entry_ask", "outcome_price", "heldout_fields", "heldout_correct_fields",
    "included_point_in_time",
    "failure_or_delisting_captured",
})

_DEAL_FIELDS = frozenset({
    "schema_version", "symbol", "target_cik", "cash_offer",
    "security_type", "listing_country", "listing_exchange",
    "announcement_accession", "current_accession", "agreement_status",
    "lifecycle_status", "conditions", "announcement_at", "observed_at",
    "expected_resolution_date", "outside_date", "source_accessions", "filing_hashes", "has_cvr",
    "has_earnout", "has_stock", "uses_options_or_leverage",
    "is_rumor_or_competing_bid", "amendment_reviewed",
})
_CALIBRATION_FIELDS = frozenset({
    "schema_version", "version", "method", "frozen", "frozen_at", "cohort_ref",
    "cohort_content_hash", "cohort_as_of", "cohort_size", "heldout_field_count",
    "heldout_correct_fields", "survivorship_safe", "includes_failed_delisted",
    "p_close", "p_fail", "p_open", "break_price_ratio", "open_price_ratio",
})


class MergerEvidenceError(ValueError):
    """An immutable strategy record does not satisfy the W-23 contract."""


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


def _normalized_deal(document: dict) -> dict:
    _exact_fields(document, _DEAL_FIELDS, kind="deal")
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
    return normalized


def _normalized_calibration(document: dict) -> dict:
    _exact_fields(document, _CALIBRATION_FIELDS, kind="calibration")
    if document["schema_version"] != CALIBRATION_SCHEMA or document["version"] != SOURCE_ID:
        raise MergerEvidenceError("unsupported calibration schema/version")
    if (
        document["method"] != "wilson95_worst_case_prices_v1"
        or document["frozen"] is not True
    ):
        raise MergerEvidenceError("calibration method is not the pinned conservative method")
    if document["survivorship_safe"] is not True or document["includes_failed_delisted"] is not True:
        raise MergerEvidenceError("calibration cohort is not survivorship-safe")
    frozen = _utc(document["frozen_at"], field="frozen_at")
    cohort_as_of = _date(document["cohort_as_of"], field="cohort_as_of")
    if frozen.date() < cohort_as_of:
        raise MergerEvidenceError("calibration was frozen before its cohort as-of date")
    cohort_size = int(document["cohort_size"])
    heldout = int(document["heldout_field_count"])
    correct = int(document["heldout_correct_fields"])
    if cohort_size < MIN_COHORT_SIZE or heldout < MIN_HELDOUT_FIELDS or not 0 <= correct <= heldout:
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
        "cohort_size": cohort_size,
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


def _verify_calibration_raw(raw_root: Path, document: dict) -> bool:
    match = _CAL_REF.fullmatch(document["cohort_ref"])
    return bool(match) and verify_snapshot(
        raw_root / "strategy-calibration",
        match.group(1),
        match.group(2),
        document["cohort_content_hash"],
    )


def _calibration_records(raw_root: Path, document: dict) -> list[dict]:
    if not _verify_calibration_raw(raw_root, document):
        raise MergerEvidenceError("calibration cohort failed integrity verification")
    match = _CAL_REF.fullmatch(document["cohort_ref"])
    if match is None:  # defensive; normalization already checked it
        raise MergerEvidenceError("calibration cohort reference is invalid")
    return read_snapshot(raw_root / "strategy-calibration", match.group(1), match.group(2))


def _cohort_statistics(records: list[dict]) -> dict:
    if len(records) < MIN_COHORT_SIZE:
        raise MergerEvidenceError("calibration cohort is too small")
    outcomes = {"closed": [], "failed": [], "open": []}
    seen_ids: set[str] = set()
    heldout = 0
    correct = 0
    as_of: date | None = None
    for record in records:
        _exact_fields(record, _COHORT_FIELDS, kind="calibration cohort row")
        if record["schema_version"] != COHORT_ROW_SCHEMA:
            raise MergerEvidenceError("unsupported calibration cohort row schema")
        deal_id = str(record["deal_id"]).strip()
        if not deal_id or deal_id in seen_ids:
            raise MergerEvidenceError("calibration cohort deal ids must be non-empty and unique")
        seen_ids.add(deal_id)
        selected = _date(record["selected_at"], field="selected_at")
        outcome_as_of = _date(record["outcome_as_of"], field="outcome_as_of")
        if selected > outcome_as_of:
            raise MergerEvidenceError("calibration cohort chronology is inconsistent")
        as_of = outcome_as_of if as_of is None else max(as_of, outcome_as_of)
        outcome = str(record["outcome"])
        if outcome not in outcomes:
            raise MergerEvidenceError("calibration cohort outcome is invalid")
        if type(record["heldout_fields"]) is not int or type(record["heldout_correct_fields"]) is not int:
            raise MergerEvidenceError("held-out field counts must be integers")
        row_heldout = record["heldout_fields"]
        row_correct = record["heldout_correct_fields"]
        if row_heldout <= 0 or not 0 <= row_correct <= row_heldout:
            raise MergerEvidenceError("held-out field labels are inconsistent")
        heldout += row_heldout
        correct += row_correct
        if record["included_point_in_time"] is not True:
            raise MergerEvidenceError("cohort contains a hindsight-selected deal")
        if type(record["failure_or_delisting_captured"]) is not bool:
            raise MergerEvidenceError("failure/delisting capture flag must be boolean")
        if outcome == "failed" and record["failure_or_delisting_captured"] is not True:
            raise MergerEvidenceError("failed/delisted outcome is not explicitly captured")
        entry = _decimal(record["entry_ask"], field="cohort entry_ask", positive=True)
        outcome_price = _decimal(
            record["outcome_price"], field="cohort outcome_price", positive=True
        )
        outcomes[outcome].append(outcome_price / entry)
    if any(not rows for rows in outcomes.values()):
        raise MergerEvidenceError("each calibration outcome needs raw cohort support")
    # Pinned conservative method: use the 95% Wilson LOWER bound for completion, allocate
    # the resulting non-close probability between failure/open in the observed non-close
    # proportions, and use the worst observed failure/open price ratios. This is deliberately
    # adverse and replayable; it cannot be loosened by editing a calibration JSON document.
    count = Decimal(len(records))
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


def _verify_calibration_statistics(raw_root: Path, document: dict) -> None:
    records = _calibration_records(raw_root, document)
    expected = _cohort_statistics(records)
    if (
        document["cohort_as_of"] != expected["cohort_as_of"]
        or document["cohort_size"] != expected["cohort_size"]
        or document["heldout_field_count"] != expected["heldout_field_count"]
        or document["heldout_correct_fields"] != expected["heldout_correct_fields"]
        or any(_decimal(document[name], field=name) != expected[name]
               for name in ("p_close", "p_fail", "p_open",
                            "break_price_ratio", "open_price_ratio"))
    ):
        raise MergerEvidenceError("calibration statistics do not match the raw cohort")


def _verified_chain(state) -> bool:
    anchor = getattr(state, "anchor_store", None)
    if anchor is not None:
        latest = anchor.latest()
        if latest is not None:
            return state.audit.verify_against_anchor(*latest).ok
    return state.audit.verify_chain().ok


def _latest_event(state, event_type: str, field: str, value: str) -> AuditEntry | None:
    if not _verified_chain(state):
        return None
    with state.session_factory() as session:
        rows = session.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == event_type)
            .order_by(AuditEntry.seq.desc())
        ).scalars().all()
        row = next((item for item in rows if (item.payload or {}).get(field) == value), None)
        if row is not None:
            session.expunge(row)
    return row


def _record_immutable(state, row, event_type: str, *, actor: str, payload: dict):
    model = type(row)
    with state.session_factory() as session:
        existing = session.get(model, row.id)
        if existing is not None:
            session.expunge(existing)
    if existing is not None:
        if existing.content_hash != row.content_hash:
            raise MergerEvidenceError("immutable evidence id collision")
        event = _latest_event(
            state,
            event_type,
            "symbol" if event_type != EVENT_CALIBRATION else "version",
            row.symbol if event_type != EVENT_CALIBRATION else row.version,
        )
        if event is None or (event.refs or {}).get("row_id") != row.id:
            raise MergerEvidenceError("existing evidence row is not the latest signed record")
        return existing
    state.audit.record_state_change(row, event_type, actor=actor, payload=payload)
    return row


def record_deal_evidence(state, document: dict, *, raw_root, actor: str = "operator"):
    """Validate SEC provenance and append one immutable operator-reviewed deal version."""
    normalized = _normalized_deal(document)
    now = _utc(state.clock.now(), field="clock")
    observed = _utc(normalized["observed_at"], field="observed_at")
    if observed > now or now - observed > MAX_DEAL_OBSERVATION_AGE:
        raise MergerEvidenceError("deal review timestamp is future or stale")
    raw_store = RawStore(Path(raw_root) / "sec", min_free_bytes=0)
    with state.session_factory() as session:
        company = session.execute(
            select(Company).where(
                Company.ticker == normalized["symbol"],
                Company.cik == normalized["target_cik"],
                Company.active.is_(True),
            )
        ).scalar_one_or_none()
        filings = session.execute(
            select(Filing).where(Filing.accession_no.in_(normalized["source_accessions"]))
        ).scalars().all()
    if company is None or len(filings) != len(normalized["source_accessions"]):
        raise MergerEvidenceError("deal symbol/CIK/filing provenance is incomplete")
    by_accession = {row.accession_no: row for row in filings}
    for accession in normalized["source_accessions"]:
        filing = by_accession[accession]
        if (
            filing.cik != normalized["target_cik"]
            or filing.raw_ref != accession
            or _utc(filing.detection_ts, field="detection_ts", allow_naive=True) > observed
            or not raw_store.verify(accession)
            or raw_store.stored_hash(accession) != normalized["filing_hashes"][accession]
        ):
            raise MergerEvidenceError("deal filing provenance failed integrity verification")
    digest = _canonical_hash(normalized)
    row = MergerDealEvidence(
        id=f"mdeal_{digest}", symbol=normalized["symbol"], target_cik=normalized["target_cik"],
        document=normalized, content_hash=digest,
        observed_at=observed, created_at=now,
    )
    return _record_immutable(
        state, row, EVENT_DEAL, actor=actor,
        payload={"symbol": row.symbol, "content_hash": digest, "schema_version": DEAL_SCHEMA},
    )


def record_calibration_evidence(state, document: dict, *, raw_root, actor: str = "operator"):
    """Append a frozen calibration only after its raw cohort hash verifies."""
    normalized = _normalized_calibration(document)
    _verify_calibration_statistics(Path(raw_root), normalized)
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

    def status_text(self) -> str:
        with self._lock:
            last_evaluation = self._reason
        if not self.strategy_watchlist:
            return f"unavailable — {SOURCE_ID}: operator strategy watchlist is empty"
        try:
            now = _utc(self.state.clock.now(), field="clock")
            self._verify_calibration(now)
            market_data = getattr(self.state, "market_data", None)
            if market_data is None:
                raise MergerEvidenceError("market data is unavailable")
            for symbol in sorted(self.strategy_watchlist):
                deal = self._verify_deal(symbol, now)
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
        event = _latest_event(self.state, event_type, field, value)
        if event is None:
            raise MergerEvidenceError(f"signed {event_type} is missing or audit chain is invalid")
        row_id = (event.refs or {}).get("row_id")
        with self.state.session_factory() as session:
            row = session.get(model, row_id)
            if row is not None:
                session.expunge(row)
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

    def _verify_deal(self, symbol: str, now: datetime) -> MergerDealEvidence:
        row = self._load_signed(MergerDealEvidence, EVENT_DEAL, "symbol", symbol)
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
        resolution = _date(document["expected_resolution_date"], field="expected_resolution_date")
        if resolution < now.date() or resolution > now.date() + timedelta(days=MAX_RESOLUTION_DAYS):
            raise MergerEvidenceError("deal is outside the 30-day strategy horizon")

        raw_store = RawStore(self.raw_root / "sec", min_free_bytes=0)
        with self.state.session_factory() as session:
            company = session.execute(select(Company).where(
                Company.ticker == symbol,
                Company.cik == document["target_cik"],
                Company.active.is_(True),
            )).scalar_one_or_none()
            filings = session.execute(select(Filing).where(
                Filing.cik == document["target_cik"]
            ).order_by(Filing.detection_ts.desc())).scalars().all()
        if company is None:
            raise MergerEvidenceError("target symbol/CIK mapping changed")
        by_accession = {filing.accession_no: filing for filing in filings}
        for accession in document["source_accessions"]:
            filing = by_accession.get(accession)
            if (
                filing is None
                or filing.raw_ref != accession
                or not raw_store.verify(accession)
                or raw_store.stored_hash(accession) != document["filing_hashes"][accession]
            ):
                raise MergerEvidenceError("deal filing is missing or corrupt")
        reviewed_accessions = set(document["source_accessions"])
        if any(
            filing.accession_no not in reviewed_accessions
            and _utc(filing.detection_ts, field="detection_ts", allow_naive=True) > observed
            for filing in filings
        ):
            raise MergerEvidenceError("new target filing requires deal review")
        # Filing rows ingested after W-23 are signed atomically. Never fall back when one of
        # those rows is deleted or altered: the immutable event remains and must still resolve
        # to the exact DB/raw tuple it covered.
        with self.state.session_factory() as session:
            events = session.execute(select(AuditEntry).where(
                AuditEntry.event_type == EVENT_FILING_INGESTED
            ).order_by(AuditEntry.seq)).scalars().all()
            for event in events:
                payload = event.payload or {}
                if payload.get("cik") != document["target_cik"]:
                    continue
                detected = _utc(payload.get("detection_ts"), field="detection_ts")
                if detected <= observed:
                    continue
                signed_filing = session.get(Filing, (event.refs or {}).get("row_id"))
                accession = str(payload.get("accession_no") or "")
                if (
                    signed_filing is None
                    or signed_filing.cik != payload.get("cik")
                    or signed_filing.accession_no != accession
                    or signed_filing.raw_ref != (event.refs or {}).get("raw_ref")
                    or signed_filing.raw_ref != accession
                    or raw_store.stored_hash(accession) != payload.get("raw_content_hash")
                    or not raw_store.verify(accession)
                ):
                    raise MergerEvidenceError("signed target filing row/raw binding failed")
                if accession not in reviewed_accessions:
                    raise MergerEvidenceError("new signed target filing requires deal review")
        return row

    def _verify_calibration(self, now: datetime) -> MergerCalibrationEvidence:
        row = self._load_signed(
            MergerCalibrationEvidence, EVENT_CALIBRATION, "version", SOURCE_ID
        )
        document = _normalized_calibration(row.document)
        if row.version != SOURCE_ID:
            raise MergerEvidenceError("calibration row version does not match signed evidence")
        frozen = _utc(document["frozen_at"], field="frozen_at")
        if frozen > now or now - frozen > MAX_CALIBRATION_AGE:
            raise MergerEvidenceError("calibration is stale")
        _verify_calibration_statistics(self.raw_root, document)
        return row

    def _verify_quote(self, symbol: str, quote: dict, now: datetime) -> tuple[Decimal, str, str]:
        if not isinstance(quote, dict) or str(quote.get("symbol", "")).upper() != symbol:
            raise MergerEvidenceError("quote symbol mismatch")
        bid = _decimal(quote.get("bid"), field="bid", positive=True)
        ask = _decimal(quote.get("ask"), field="ask", positive=True)
        if ask < bid or ask < MIN_PRICE:
            raise MergerEvidenceError("quote is crossed or below the price floor")
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
        day = stamp.date().isoformat()
        key = f"{symbol}-quote-{ts_ns}"
        raw_ref = f"market-data://{day}/{key}"
        with self.state.session_factory() as session:
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
        quote_root = self.raw_root / "market-data" / "quotes"
        if not verify_snapshot(quote_root, day, key, digest):
            raise MergerEvidenceError("quote raw snapshot is missing or corrupt")
        records = read_snapshot(quote_root, day, key)
        if len(records) != 1:
            raise MergerEvidenceError("quote raw snapshot shape is invalid")
        raw = records[0]
        if (
            str(raw.get("symbol", "")).upper() != symbol
            or int(raw.get("exchange_ts_ns", -1)) != ts_ns
            or _decimal(raw.get("bid"), field="raw bid", positive=True) != bid
            or _decimal(raw.get("ask"), field="raw ask", positive=True) != ask
            or str(raw.get("feed", "")).lower() != feed
        ):
            raise MergerEvidenceError("quote does not match its signed raw snapshot")
        return ask, raw_ref, digest

    def _verify_metrics(self, symbol: str, now: datetime) -> dict:
        market_data = getattr(self.state, "market_data", None)
        metric = market_data.metrics(symbol) if market_data is not None else None
        if metric is None or str(metric.symbol).upper() != symbol:
            raise MergerEvidenceError("market metrics are unavailable")
        if not _verified_chain(self.state):
            raise MergerEvidenceError("audit chain is invalid")
        with self.state.session_factory() as session:
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
        if not verify_snapshot(bars_root, day, symbol, digest):
            raise MergerEvidenceError("daily-bar snapshot is missing or corrupt")
        derived = calculate_daily_metrics(symbol, read_snapshot(bars_root, day, symbol))
        adv = _decimal(derived.median_daily_dollar_volume, field="ADV", positive=True)
        if (
            metric.as_of != derived.as_of
            or _decimal(metric.median_daily_dollar_volume, field="cached ADV", positive=True) != adv
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
            "observations": int(metric.observations),
            "raw_ref": (latest.refs or {}).get("raw_ref"),
            "content_hash": digest,
        }

    def _estimate(self, proposal: dict, quote: dict) -> EdgeEstimate:
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

        deal = self._verify_deal(symbol, now)
        calibration = self._verify_calibration(now)
        ask, quote_ref, quote_hash = self._verify_quote(symbol, quote, now)
        metrics = self._verify_metrics(symbol, now)
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
        )
        return EdgeEstimate(
            expected_gross_return_bps=gross_bps,
            time_horizon=TIME_HORIZON,
            source_id=SOURCE_ID,
            evidence_ref=f"merger-edge://{row.id}",
            computed_at_ns=to_ns(now),
            verified=True,
        )

    def estimate(self, proposal: dict, *, quote: dict) -> EdgeEstimate | None:
        try:
            estimate = self._estimate(dict(proposal), dict(quote))
        except Exception as exc:  # noqa: BLE001 - any evidence/data fault means no edge authority
            self._fail(f"{type(exc).__name__}: {exc}")
            return None
        with self._lock:
            self._reason = "latest evidence verified"
        return estimate
