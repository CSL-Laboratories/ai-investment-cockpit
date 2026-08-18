"""W-24 deterministic semantic authority for the cash-merger edge source.

The verifier is deliberately narrow.  It accepts only deal language that its pinned parser can
classify unambiguously and only calibration evaluation rows selected in an outcome-free manifest
whose hash was server-stamped outside the cockpit.  Ambiguity, missing source text, an unwitnessed
manifest, an incomplete evaluation universe, or an unproven outcome all fail closed.

This module performs no model calls and has no broker dependency.  Discord I/O is isolated behind
an injected witness adapter; normal verification only invokes that exact-message adapter.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from bridge.anchor import AnchorError
from filings.raw_store import RawStore, RawStoreError
from schemas.models.audit_entry import AuditEntry
from schemas.models.filing import Filing
from schemas.models.merger_edge import MergerSelectionCommitment
from strategies.merger_edge import (
    EVENT_FILING_INGESTED,
    MIN_EVALUATION_COHORT_SIZE,
    SOURCE_ID,
    SemanticEvidenceUnavailable,
    _authority_session,
    _calibration_records,
    _canonical_hash,
    _current_edgar_binding_in,
    _date,
    _exact_fields,
    _filing_event_map,
    _latest_filing_event_seq_in,
    _normalized_calibration,
    _normalized_deal,
    _normalized_eligibility,
    _normalized_source_ref,
    _source_event_in,
    _utc,
    _verify_price_source_in,
)
from orchestrator.state import EVENT_EDGAR_POLL_COMPLETED


SELECTION_SCHEMA = "merger_selection_manifest.v2"
EVENT_SELECTION = "merger_selection_commitment"
MAX_WITNESS_CLOCK_SKEW = timedelta(minutes=5)
MAX_SELECTION_QUOTE_AGE = timedelta(minutes=5)
MAX_SELECTION_SOURCE_COUNT = 250

_HASH = re.compile(r"^[0-9a-f]{64}$")
_CIK = re.compile(r"^\d{10}$")
_ACCESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_MANIFEST_FIELDS = frozenset({
    "schema_version", "source_id", "committed_at", "selection_date",
    "training_cutoff", "deals",
})
_CANDIDATE_INPUT_FIELDS = frozenset({
    "deal_id", "symbol", "target_cik", "source_accession", "source_accessions",
    "current_accession", "selected_at", "entry_ask", "eligibility_prediction",
    "eligibility_source", "entry_price_source",
})
_CANDIDATE_FIELDS = _CANDIDATE_INPUT_FIELDS | frozenset({"selection_authority"})
_AUTHORITY_FIELDS = frozenset({
    "filing_event_seq", "filing_set_hash", "edgar_poll_event_seq", "edgar_poll_completed_at",
    "baseline_accession", "cursor_accession",
})
_CURRENT_AUTHORITY_FIELDS = frozenset({
    "filing_event_seq", "filing_set_hash", "edgar_poll_event_seq", "edgar_poll_completed_at",
})
_COMMITTED_ROW_FIELDS = frozenset({
    "deal_id", "symbol", "target_cik", "source_accession", "selected_at", "entry_ask",
    "eligibility_prediction", "eligibility_source", "entry_price_source",
})

_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_CASH = re.compile(
    r"(?:each|every)\s+(?:issued\s+and\s+outstanding\s+|outstanding\s+)?"
    r"share\s+of\s+(?:the\s+)?(?:(?:company|target)(?:'s)?\s+)?common\s+stock"
    r"[^.]{0,240}?"
    r"(?:"
    r"(?:will|shall)\s+(?:receive|(?:be\s+)?entitled\s+to\s+receive)|"
    r"(?:will|shall)\s+(?:be\s+)?converted\s+(?:automatically\s+)?into\s+"
    r"(?:the\s+)?right\s+to\s+receive"
    r")"
    r"[^.]{0,120}?(?:\$|usd\s+)(\d+(?:\.\d+)?)\s+"
    r"(?:in\s+cash\s+)?per\s+(?:each\s+)?share"
)
_DEFINITIVE = re.compile(
    r"(?:entered\s+into\s+)?(?:a\s+)?definitive\s+"
    r"(?:agreement\s+and\s+plan\s+of\s+)?merger"
)
_NOT_DEFINITIVE = re.compile(
    r"(?:has|have)\s+not\s+entered\s+into\s+(?:a\s+)?definitive\s+"
    r"(?:agreement\s+and\s+plan\s+of\s+)?merger|"
    r"no\s+definitive\s+(?:merger(?:\s+agreement)?|agreement\s+and\s+plan\s+of\s+merger)|"
    r"non-binding\s+(?:offer|proposal)|indication\s+of\s+interest"
)
_STOCK_CONSIDERATION = re.compile(
    r"stock\s+consideration|cash\s+(?:and|plus)\s+stock\s+consideration|"
    r"(?:receive|plus|and)[^.]{0,100}"
    r"(?:\d+(?:\.\d+)?|\d+/\d+|one(?:-|\s)(?:half|quarter)|one)"
    r"(?:\s+of)?\s+(?:a\s+)?shares?\s+of\s+[^.]{0,80}(?:common\s+)?stock|"
    r"\d+(?:\.\d+)?\s+shares\s+of\s+[^.]{0,80}(?:and|plus)\s+(?:\$|usd)"
)
_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
_MONTH_DATE = (
    r"(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+\d{1,2},\s+\d{4}"
)
_DATE_TOKEN = rf"(?:{_ISO_DATE}|{_MONTH_DATE})"
_EXPECTED_DATE = re.compile(
    rf"(?:expected\s+(?:resolution|closing)\s+date\s*(?::|is)|"
    rf"expected\s+to\s+(?:close|be\s+completed)\s+(?:on|by))\s+({_DATE_TOKEN})"
)
_OUTSIDE_DATE = re.compile(rf"outside\s+date\s*(?::|is|of)\s+({_DATE_TOKEN})")
_CLOSED = re.compile(
    r"merger\s+(?:was|has\s+been)\s+(?:completed|consummated)|"
    r"(?:has|have)\s+completed\s+(?:its|the)\s+acquisition|"
    r"announced\s+(?:the\s+)?completion\s+of\s+(?:the\s+)?merger"
)
_FAILED = re.compile(
    r"(?:has|have)\s+terminated\s+(?:the\s+)?merger\s+agreement|"
    r"announced\s+(?:the\s+)?termination\s+of\s+(?:the\s+)?merger\s+agreement|"
    r"merger\s+agreement\s+(?:was|has\s+been)\s+terminated|"
    r"transaction\s+will\s+not\s+proceed"
)
_CONDITIONAL_OUTCOME = re.compile(
    r"\b(?:if|unless|whether|would|could|may|might|should|in\s+the\s+event)\b"
)
_MERGER_AMENDMENT = re.compile(
    r"(?:amend(?:ed|ment|ments|ing)?[^.]{0,80}(?:merger|transaction)|"
    r"(?:merger|transaction)[^.]{0,80}amend(?:ed|ment|ments|ing)?)"
)


def _text(raw: bytes) -> str:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticEvidenceUnavailable("SEC source text is not valid UTF-8") from exc
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", decoded))).strip().lower()


def _combined_source_text(texts: list[str]) -> str:
    """Keep separately signed documents from synthesizing one accepted sentence."""
    return " . ".join(texts)


def _source_date(value: str, *, field: str) -> date:
    try:
        if re.fullmatch(_ISO_DATE, value):
            return date.fromisoformat(value)
        return datetime.strptime(value.title(), "%B %d, %Y").date()
    except ValueError as exc:
        raise SemanticEvidenceUnavailable(f"{field} is not an unambiguous calendar date") from exc


def _unique_match(pattern: re.Pattern, text: str, *, field: str) -> str:
    values = {match.group(1) for match in pattern.finditer(text)}
    if len(values) != 1:
        raise SemanticEvidenceUnavailable(f"SEC source does not state one unambiguous {field}")
    return values.pop()


def _asserted(pattern: re.Pattern, text: str) -> bool:
    """Accept an outcome only from an unconditional sentence-sized assertion.

    Merger agreements routinely describe what happens *if* a transaction terminates. Treating
    that boilerplate as an observed failure would poison the evaluation labels. The parser is
    intentionally conservative: a sentence containing conditional/modal language is not an
    outcome authority even when it also contains one of the pinned phrases.
    """
    return any(
        pattern.search(sentence) and not _CONDITIONAL_OUTCOME.search(sentence)
        for sentence in re.split(r"[.!?;]+", text)
    )


def _derive_eligibility(text: str, *, amended: bool) -> dict:
    offers = {str(Decimal(value)) for value in _CASH.findall(text)}
    if len(offers) != 1:
        raise SemanticEvidenceUnavailable("SEC source does not state one cash-per-share value")
    if "common stock" not in text:
        raise SemanticEvidenceUnavailable("SEC source does not identify target common stock")
    exchanges = []
    if "nasdaq" in text:
        exchanges.append("nasdaq")
    if "new york stock exchange" in text or re.search(r"\bnyse\b", text):
        exchanges.append("nyse")
    if len(set(exchanges)) != 1:
        raise SemanticEvidenceUnavailable("SEC source does not identify one supported US exchange")
    definitive = bool(_DEFINITIVE.search(text))
    if not definitive or _NOT_DEFINITIVE.search(text):
        raise SemanticEvidenceUnavailable("SEC source does not state a definitive merger")
    resolved = _asserted(_CLOSED, text)
    terminated = _asserted(_FAILED, text)
    return {
        "cash_offer": offers.pop(),
        "security_type": "common_equity",
        "listing_country": "US",
        "listing_exchange": exchanges[0],
        "agreement_status": "definitive",
        "lifecycle_status": "terminated" if (resolved or terminated) else "active",
        "has_cvr": bool(re.search(r"contingent\s+value\s+right|\bcvr\b", text)),
        "has_earnout": bool(re.search(r"earn[- ]?out", text)),
        "has_stock": bool(_STOCK_CONSIDERATION.search(text)),
        # This is a strategy-construction fact, independently enforced by the proposal path.
        # Target-option treatment in a merger filing does not mean this strategy uses options.
        "uses_options_or_leverage": False,
        "is_rumor_or_competing_bid": bool(re.search(
            r"competing\s+bid\s+(?:has\s+been\s+)?received|"
            r"superior\s+proposal\s+(?:has\s+been\s+)?received|merger\s+rumou?r",
            text,
        )),
        "amendment_reviewed": amended,
    }


def _require_strategy_eligible(label: dict) -> None:
    if (
        label["security_type"] != "common_equity"
        or label["listing_country"] != "US"
        or label["listing_exchange"] not in {"nasdaq", "nyse", "nyse_american"}
        or label["agreement_status"] != "definitive"
        or label["lifecycle_status"] != "active"
        or any(label[name] for name in (
            "has_cvr", "has_earnout", "has_stock", "uses_options_or_leverage",
            "is_rumor_or_competing_bid",
        ))
    ):
        raise SemanticEvidenceUnavailable(
            "calibration row is outside the pinned cash-merger strategy universe"
        )


def _outcome_states(text: str) -> set[str]:
    states = set()
    if _asserted(_CLOSED, text):
        states.add("closed")
    if _asserted(_FAILED, text):
        states.add("failed")
    if _asserted(re.compile(r"(?:merger|transaction)\s+remains\s+pending"), text):
        states.add("open")
    return states


def _derive_outcome(text: str) -> str:
    states = _outcome_states(text)
    if len(states) != 1:
        raise SemanticEvidenceUnavailable("outcome source is absent or semantically ambiguous")
    return states.pop()


def _normalized_candidate(value: dict, *, authority: bool) -> dict:
    _exact_fields(
        value, _CANDIDATE_FIELDS if authority else _CANDIDATE_INPUT_FIELDS,
        kind="selection candidate",
    )
    deal_id = str(value["deal_id"]).strip()
    symbol = str(value["symbol"]).strip().upper()
    cik = str(value["target_cik"]).strip()
    accession = str(value["source_accession"]).strip()
    current_accession = str(value["current_accession"]).strip()
    source_values = value["source_accessions"]
    if not isinstance(source_values, list):
        raise SemanticEvidenceUnavailable("selection source accessions must be an ordered list")
    source_accessions = [str(item).strip() for item in source_values]
    if (
        not deal_id or len(deal_id) > 80 or _SYMBOL.fullmatch(symbol) is None
        or _CIK.fullmatch(cik) is None or _ACCESSION.fullmatch(accession) is None
        or _ACCESSION.fullmatch(current_accession) is None
        or not source_accessions
        or len(source_accessions) > MAX_SELECTION_SOURCE_COUNT
        or any(_ACCESSION.fullmatch(item) is None for item in source_accessions)
        or len(set(source_accessions)) != len(source_accessions)
        or source_accessions[0] != accession
        or source_accessions[-1] != current_accession
    ):
        raise SemanticEvidenceUnavailable("selection candidate identity is invalid")
    selected = _date(value["selected_at"], field="selected_at")
    try:
        entry_ask = Decimal(str(value["entry_ask"]))
    except Exception as exc:
        raise SemanticEvidenceUnavailable("selection entry ask is invalid") from exc
    if not entry_ask.is_finite() or entry_ask <= 0:
        raise SemanticEvidenceUnavailable("selection entry ask is invalid")
    normalized = {
        "deal_id": deal_id,
        "symbol": symbol,
        "target_cik": cik,
        "source_accession": accession,
        "source_accessions": source_accessions,
        "current_accession": current_accession,
        "selected_at": selected.isoformat(),
        "entry_ask": str(entry_ask),
        "eligibility_prediction": _normalized_eligibility(
            value["eligibility_prediction"], kind="selection eligibility prediction"
        ),
        "eligibility_source": _normalized_source_ref(
            value["eligibility_source"], field="selection eligibility source"
        ),
        "entry_price_source": _normalized_source_ref(
            value["entry_price_source"], field="selection entry price source"
        ),
    }
    if authority:
        auth = value["selection_authority"]
        _exact_fields(auth, _AUTHORITY_FIELDS, kind="selection authority")
        if (
            type(auth["filing_event_seq"]) is not int or auth["filing_event_seq"] <= 0
            or type(auth["edgar_poll_event_seq"]) is not int
            or auth["edgar_poll_event_seq"] <= 0
            or _HASH.fullmatch(str(auth["filing_set_hash"])) is None
        ):
            raise SemanticEvidenceUnavailable("selection authority binding is invalid")
        normalized["selection_authority"] = {
            **auth,
            "edgar_poll_completed_at": _utc(
                auth["edgar_poll_completed_at"], field="selection EDGAR poll"
            ).isoformat(),
        }
        for name in ("baseline_accession", "cursor_accession"):
            value = auth[name]
            if value is not None and _ACCESSION.fullmatch(str(value)) is None:
                raise SemanticEvidenceUnavailable(f"selection authority {name} is invalid")
            normalized["selection_authority"][name] = None if value is None else str(value)
    return normalized


def _normalized_manifest(document: dict) -> dict:
    _exact_fields(document, _MANIFEST_FIELDS, kind="selection manifest")
    if document["schema_version"] != SELECTION_SCHEMA or document["source_id"] != SOURCE_ID:
        raise SemanticEvidenceUnavailable("unsupported selection manifest schema/source")
    committed = _utc(document["committed_at"], field="manifest committed_at")
    selection_date = _date(document["selection_date"], field="manifest selection_date")
    cutoff = _date(document["training_cutoff"], field="manifest training_cutoff")
    deals = [_normalized_candidate(value, authority=True) for value in document["deals"]]
    if len(deals) < MIN_EVALUATION_COHORT_SIZE:
        raise SemanticEvidenceUnavailable("selection manifest evaluation universe is too small")
    if cutoff >= selection_date or committed.date() != selection_date:
        raise SemanticEvidenceUnavailable("selection manifest chronology is invalid")
    unique_fields = (
        "deal_id", "symbol", "target_cik", "source_accession", "current_accession"
    )
    if any(
        len({row[name] for row in deals}) != len(deals) for name in unique_fields
    ) or any(
        _date(row["selected_at"], field="selected_at") != selection_date for row in deals
    ):
        raise SemanticEvidenceUnavailable("selection manifest universe is duplicate or incomplete")
    return {
        **document,
        "committed_at": committed.isoformat(),
        "selection_date": selection_date.isoformat(),
        "training_cutoff": cutoff.isoformat(),
        "deals": sorted(deals, key=lambda row: row["deal_id"]),
    }


def _verify_selection_sources_in(session, raw_root: Path, document: dict) -> None:
    raw_store = RawStore(raw_root / "sec", min_free_bytes=0)
    committed = _utc(document["committed_at"], field="manifest committed_at")
    for row in document["deals"]:
        selected = _date(row["selected_at"], field="selected_at")
        eligibility = _source_event_in(
            session, row["eligibility_source"], event_type=EVENT_FILING_INGESTED
        )
        payload = eligibility.payload or {}
        if (
            payload.get("cik") != row["target_cik"]
            or payload.get("accession_no") != row["source_accession"]
            or _date(payload.get("filing_date"), field="selection filing date") > selected
            or _utc(payload.get("detection_ts"), field="selection detection time") > committed
            or _utc(
                eligibility.created_at, field="selection filing audit time", allow_naive=True
            ) > committed
            or not raw_store.verify(row["source_accession"])
        ):
            raise SemanticEvidenceUnavailable("selection filing source is not prospectively bound")
        signed = _filing_event_map(session, cik=row["target_cik"], raw_store=raw_store)
        domain_rows = session.execute(
            select(Filing).where(Filing.cik == row["target_cik"])
        ).scalars().all()
        domain_accessions = [filing.accession_no for filing in domain_rows]
        if (
            len(domain_accessions) != len(set(domain_accessions))
            or set(domain_accessions) != set(signed)
        ):
            raise SemanticEvidenceUnavailable(
                "selection filing universe contains unsigned or conflicting rows"
            )
        try:
            source = signed[row["source_accession"]]
            current_source = signed[row["current_accession"]]
        except KeyError as exc:
            raise SemanticEvidenceUnavailable(
                "selection source set contains an unsigned accession"
            ) from exc
        if any(filing.filing_date > selected for filing in signed.values()):
            raise SemanticEvidenceUnavailable(
                "selection filing universe contains a future-dated filing"
            )
        if source.filing_date > current_source.filing_date or any(
            filing.filing_date > current_source.filing_date for filing in signed.values()
        ):
            raise SemanticEvidenceUnavailable(
                "selection current accession is not the latest signed filing"
            )
        complete_set = {
            filing.accession_no for filing in signed.values()
            if source.filing_date <= filing.filing_date <= current_source.filing_date
        }
        if set(row["source_accessions"]) != complete_set:
            raise SemanticEvidenceUnavailable(
                "selection source set omits, adds, or reorders a signed filing"
            )
        complete = [signed[accession] for accession in row["source_accessions"]]
        if any(
            complete[index].filing_date > complete[index + 1].filing_date
            for index in range(len(complete) - 1)
        ):
            raise SemanticEvidenceUnavailable(
                "selection source set omits, adds, or reorders a signed filing"
            )
        filing_events = session.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_FILING_INGESTED)
            .order_by(AuditEntry.seq)
        ).scalars().all()
        texts = []
        for filing in complete:
            matching_events = [event for event in filing_events if (
                (event.payload or {}).get("cik") == row["target_cik"]
                and (event.payload or {}).get("accession_no") == filing.accession_no
                and (event.refs or {}).get("row_id") == filing.id
            )]
            if (
                filing.filing_date > selected
                or _utc(filing.detection_ts, field="selection detection time", allow_naive=True)
                > committed
                or not matching_events
                or any(
                    _utc(event.created_at, field="selection filing audit time", allow_naive=True)
                    > committed
                    for event in matching_events
                )
            ):
                raise SemanticEvidenceUnavailable(
                    "selection source set was not fully signed before commitment"
                )
            texts.append(_text(raw_store.resolve(filing.raw_ref)))
        amended = any(_MERGER_AMENDMENT.search(text) for text in texts[1:])
        derived_prediction = _derive_eligibility(
            _combined_source_text(texts), amended=amended
        )
        if row["eligibility_prediction"] != derived_prediction:
            raise SemanticEvidenceUnavailable(
                "selection eligibility prediction is not source-set-derived"
            )
        price_raw, price_event = _verify_price_source_in(
            session,
            raw_root,
            row["entry_price_source"],
            symbol=row["symbol"],
            observed_on=_date(row["selected_at"], field="selected_at"),
            price=Decimal(row["entry_ask"]),
            entry=True,
        )
        price_time = _utc(price_raw.get("exchange_ts"), field="selection quote timestamp")
        price_recorded = _utc(
            price_event.created_at, field="selection quote audit time", allow_naive=True
        )
        if (
            price_time > committed
            or price_recorded > committed
            or price_recorded < price_time
            or committed - price_time > MAX_SELECTION_QUOTE_AGE
        ):
            raise SemanticEvidenceUnavailable(
                "selection entry quote is not fresh point-in-time evidence"
            )
        current = _current_edgar_binding_in(
            session,
            cik=row["target_cik"],
            now=committed,
            process_epoch=getattr(session.info.get("semantic_state"), "process_epoch", ""),
        )
        authority = row["selection_authority"]
        poll = session.execute(
            select(AuditEntry).where(AuditEntry.seq == authority["edgar_poll_event_seq"])
        ).scalar_one_or_none()
        poll_payload = {} if poll is None else (poll.payload or {})
        if (
            any(current[name] != authority[name] for name in _CURRENT_AUTHORITY_FIELDS)
            or poll is None
            or (poll.refs or {}).get("cik") != row["target_cik"]
            or poll_payload.get("baseline_accession") != authority["baseline_accession"]
            or poll_payload.get("cursor_accession") != authority["cursor_accession"]
            or authority["baseline_accession"] != row["current_accession"]
        ):
            raise SemanticEvidenceUnavailable("selection filing universe changed before commitment")


def record_selection_commitment(
    state,
    candidates: list[dict],
    *,
    training_cutoff,
    raw_root,
    witness,
    actor: str = "operator",
) -> MergerSelectionCommitment:
    """Commit a complete outcome-free evaluation universe to an out-of-domain timestamp."""
    if witness is None or not getattr(witness, "out_of_domain", False):
        raise SemanticEvidenceUnavailable("out-of-domain semantic witness is not provisioned")
    now = _utc(state.clock.now(), field="clock")
    cutoff = _date(training_cutoff, field="training_cutoff")
    initial = [_normalized_candidate(value, authority=False) for value in candidates]
    if len(initial) < MIN_EVALUATION_COHORT_SIZE:
        raise SemanticEvidenceUnavailable("selection manifest evaluation universe is too small")
    raw_root = Path(raw_root)
    enriched = []
    with _authority_session(state) as session:
        for row in initial:
            current = _current_edgar_binding_in(
                session, cik=row["target_cik"], now=now, process_epoch=state.process_epoch
            )
            poll = session.execute(
                select(AuditEntry).where(AuditEntry.seq == current["edgar_poll_event_seq"])
            ).scalar_one_or_none()
            payload = {} if poll is None else (poll.payload or {})
            enriched.append({
                **row,
                "selection_authority": {
                    **current,
                    "baseline_accession": payload.get("baseline_accession"),
                    "cursor_accession": payload.get("cursor_accession"),
                },
            })
    document = _normalized_manifest({
        "schema_version": SELECTION_SCHEMA,
        "source_id": SOURCE_ID,
        "committed_at": now.isoformat(),
        "selection_date": now.date().isoformat(),
        "training_cutoff": cutoff.isoformat(),
        "deals": enriched,
    })
    # Refuse a malformed/stale local universe before creating an out-of-domain artifact. The
    # same checks run again in the persistence transaction after Discord returns, closing the
    # poll/source race without leaving routine validation failures as orphan witness posts.
    with _authority_session(state) as session:
        session.info["semantic_state"] = state
        _verify_selection_sources_in(session, raw_root, document)
    digest = _canonical_hash(document)
    try:
        receipt = witness.publish(digest)
        witnessed_at = _utc(receipt.server_ts, field="semantic witness timestamp")
    except (AnchorError, AttributeError) as exc:
        raise SemanticEvidenceUnavailable(str(exc)) from exc
    if witnessed_at < now - MAX_WITNESS_CLOCK_SKEW or witnessed_at > now + MAX_WITNESS_CLOCK_SKEW:
        raise SemanticEvidenceUnavailable("semantic witness timestamp exceeds the clock-skew bound")
    row = MergerSelectionCommitment(
        id=f"msel_{digest}",
        source_id=SOURCE_ID,
        document=document,
        content_hash=digest,
        witness_message_id=str(receipt.message_id),
        witnessed_at=witnessed_at,
        created_at=now,
    )
    with _authority_session(state) as session:
        session.info["semantic_state"] = state
        _verify_selection_sources_in(session, raw_root, document)
        existing = session.get(MergerSelectionCommitment, row.id)
        if existing is not None:
            raise SemanticEvidenceUnavailable("selection commitment already exists")
        session.add(row)
        session.flush()
        state.audit.append_in(
            session,
            EVENT_SELECTION,
            actor=actor,
            refs={"model": row.__tablename__, "row_id": row.id},
            payload={
                "source_id": SOURCE_ID,
                "content_hash": digest,
                "schema_version": SELECTION_SCHEMA,
                "witness_message_id": row.witness_message_id,
                "witnessed_at": witnessed_at.isoformat(),
            },
        )
        session.expunge(row)
    return row


class CashMergerSemanticVerifier:
    """Verify deal terms, calibration labels/outcomes, and prospective cohort completeness."""

    def __init__(self, state, *, raw_root, witness) -> None:
        self.state = state
        self.raw_root = Path(raw_root)
        self.witness = witness

    def _load_manifest_in(self, session, manifest_id: str) -> tuple[MergerSelectionCommitment, dict]:
        row = session.get(MergerSelectionCommitment, str(manifest_id))
        if row is None:
            raise SemanticEvidenceUnavailable("signed selection commitment is missing")
        document = _normalized_manifest(row.document)
        digest = _canonical_hash(document)
        events = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_SELECTION)
        ).scalars().all()
        matches = [event for event in events if (
            (event.refs or {}).get("model") == row.__tablename__
            and (event.refs or {}).get("row_id") == row.id
            and (event.payload or {}).get("content_hash") == digest
            and (event.payload or {}).get("witness_message_id") == row.witness_message_id
        )]
        if (
            len(matches) != 1
            or row.id != f"msel_{digest}"
            or row.content_hash != digest
            or row.source_id != SOURCE_ID
            or (matches[0].payload or {}).get("source_id") != SOURCE_ID
            or (matches[0].payload or {}).get("schema_version") != SELECTION_SCHEMA
        ):
            raise SemanticEvidenceUnavailable("selection commitment audit binding failed")
        if self.witness is None or not getattr(self.witness, "out_of_domain", False):
            raise SemanticEvidenceUnavailable("out-of-domain semantic witness is not provisioned")
        try:
            witnessed_at = _utc(
                self.witness.verify(row.witness_message_id, digest),
                field="semantic witness timestamp",
            )
        except (AnchorError, AttributeError) as exc:
            raise SemanticEvidenceUnavailable(str(exc)) from exc
        stored = _utc(row.witnessed_at, field="stored witness timestamp", allow_naive=True)
        payload_time = _utc(
            (matches[0].payload or {}).get("witnessed_at"), field="audited witness timestamp"
        )
        if witnessed_at != stored or witnessed_at != payload_time:
            raise SemanticEvidenceUnavailable("semantic witness timestamp binding failed")
        committed = _utc(document["committed_at"], field="manifest committed_at")
        if abs(witnessed_at - committed) > MAX_WITNESS_CLOCK_SKEW:
            raise SemanticEvidenceUnavailable("semantic witness chronology is invalid")
        return row, document

    def _verify_deal(self, document: dict) -> None:
        raw_store = RawStore(self.raw_root / "sec", min_free_bytes=0)
        texts = []
        for accession in document["source_accessions"]:
            try:
                texts.append(_text(raw_store.resolve(accession)))
            except (OSError, RawStoreError, ValueError) as exc:
                raise SemanticEvidenceUnavailable("deal SEC source is missing or corrupt") from exc
        combined = _combined_source_text(texts)
        current_text = texts[document["source_accessions"].index(document["current_accession"])]
        amended = document["current_accession"] != document["announcement_accession"]
        if amended and "amend" not in current_text:
            raise SemanticEvidenceUnavailable("current deal amendment is not explicit in source")
        derived = _derive_eligibility(combined, amended=amended)
        for name, value in derived.items():
            if document[name] != value:
                raise SemanticEvidenceUnavailable(f"deal {name} is not source-derived")
        expected = _source_date(
            _unique_match(_EXPECTED_DATE, combined, field="expected resolution date"),
            field="expected resolution date",
        )
        outside = _source_date(
            _unique_match(_OUTSIDE_DATE, combined, field="outside date"), field="outside date"
        )
        if (
            expected.isoformat() != document["expected_resolution_date"]
            or outside.isoformat() != document["outside_date"]
        ):
            raise SemanticEvidenceUnavailable("deal resolution dates are not source-derived")
        for condition in document["conditions"]:
            if _SPACE.sub(" ", str(condition).strip().lower()) not in combined:
                raise SemanticEvidenceUnavailable("deal conditions are not all present in source")

    def _poll_covers_outcome_in(self, session, row: dict, manifest_row: dict, outcome_event) -> None:
        cik = row["target_cik"]
        events = session.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_EDGAR_POLL_COMPLETED)
            .order_by(AuditEntry.seq)
        ).scalars().all()
        selected_poll = next(
            (event for event in events if event.seq == manifest_row["selection_authority"][
                "edgar_poll_event_seq"
            ]),
            None,
        )
        if selected_poll is None or (selected_poll.payload or {}).get("cik") != cik:
            raise SemanticEvidenceUnavailable("selection EDGAR poll binding is missing")
        selected_payload = selected_poll.payload or {}
        selected_authority = manifest_row["selection_authority"]
        if (
            (selected_poll.refs or {}).get("cik") != cik
            or selected_payload.get("filing_event_seq")
            != selected_authority["filing_event_seq"]
            or selected_payload.get("baseline_accession")
            != selected_authority["baseline_accession"]
            or selected_payload.get("cursor_accession")
            != selected_authority["cursor_accession"]
            or selected_authority["baseline_accession"] != manifest_row["current_accession"]
            or _utc(selected_payload.get("completed_at"), field="selection EDGAR poll")
            != _utc(
                selected_authority["edgar_poll_completed_at"],
                field="committed selection EDGAR poll",
            )
        ):
            raise SemanticEvidenceUnavailable("selection EDGAR poll authority is inconsistent")
        horizon_end = datetime.combine(
            _date(row["outcome_as_of"], field="outcome_as_of") + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        )
        qualifying = [event for event in events if (
            event.seq > selected_poll.seq
            and (event.payload or {}).get("cik") == cik
            and _utc((event.payload or {}).get("completed_at"), field="outcome EDGAR poll")
            >= horizon_end
        )]
        if not qualifying:
            raise SemanticEvidenceUnavailable("evaluation outcome lacks a post-horizon EDGAR poll")
        final = qualifying[-1]
        completed = _utc((final.payload or {}).get("completed_at"), field="outcome EDGAR poll")
        recorded = _utc(final.created_at, field="outcome poll audit time", allow_naive=True)
        final_baseline = (final.payload or {}).get("baseline_accession")
        final_cursor = (final.payload or {}).get("cursor_accession")
        if (
            (final.refs or {}).get("cik") != cik
            or (final.payload or {}).get("cik") != cik
            or recorded < completed
            or recorded - completed > MAX_WITNESS_CLOCK_SKEW
            or _ACCESSION.fullmatch(str(final_baseline or "")) is None
            or _ACCESSION.fullmatch(str(final_cursor or "")) is None
        ):
            raise SemanticEvidenceUnavailable("outcome EDGAR poll chronology is invalid")
        final_head = int((final.payload or {}).get("filing_event_seq") or 0)
        if (
            final_head != _latest_filing_event_seq_in(session, cik=cik)
            or final_head < int(outcome_event.seq)
        ):
            raise SemanticEvidenceUnavailable("outcome filing universe is not poll-complete")

    def _verify_outcome_universe_in(self, session, row: dict, raw_store: RawStore) -> None:
        selected = _date(row["selected_at"], field="selected_at")
        outcome_as_of = _date(row["outcome_as_of"], field="outcome_as_of")
        signed = _filing_event_map(session, cik=row["target_cik"], raw_store=raw_store)
        domain_rows = session.execute(
            select(Filing).where(
                Filing.cik == row["target_cik"],
                Filing.filing_date <= outcome_as_of,
            )
        ).scalars().all()
        domain_accessions = [filing.accession_no for filing in domain_rows]
        signed_accessions = {
            accession for accession, filing in signed.items()
            if filing.filing_date <= outcome_as_of
        }
        if (
            len(domain_accessions) != len(set(domain_accessions))
            or set(domain_accessions) != signed_accessions
        ):
            raise SemanticEvidenceUnavailable(
                "outcome filing universe contains unsigned or conflicting rows"
            )
        observed_states: set[str] = set()
        for filing in signed.values():
            if not selected < filing.filing_date <= outcome_as_of:
                continue
            observed_states.update(_outcome_states(_text(raw_store.resolve(filing.raw_ref))))
        expected = str(row["outcome"])
        resolutions = observed_states & {"closed", "failed"}
        if len(resolutions) > 1 or (resolutions and expected not in resolutions):
            raise SemanticEvidenceUnavailable("complete filing universe conflicts with outcome")
        if expected == "open":
            if resolutions or "open" not in observed_states:
                raise SemanticEvidenceUnavailable("complete filing universe does not prove open")
        elif resolutions != {expected}:
            raise SemanticEvidenceUnavailable("complete filing universe does not prove resolution")

    def _verify_calibration_in(
        self, session, calibration: dict, manifest: dict, witnessed_at: datetime
    ) -> None:
        if calibration["training_cutoff"] != manifest["training_cutoff"]:
            raise SemanticEvidenceUnavailable("selection manifest training cutoff mismatches cohort")
        records = _calibration_records(self.raw_root, calibration)
        evaluation = [row for row in records if row["split"] == "evaluation"]
        committed = {row["deal_id"]: row for row in manifest["deals"]}
        observed = {str(row["deal_id"]): row for row in evaluation}
        if (
            set(observed) != set(committed)
            or len(observed) != len(evaluation)
            or len(committed) != len(manifest["deals"])
        ):
            raise SemanticEvidenceUnavailable("calibration dropped or added evaluation deals")
        raw_store = RawStore(self.raw_root / "sec", min_free_bytes=0)
        for row in records:
            eligibility_event = _source_event_in(
                session, row["eligibility_source"], event_type=EVENT_FILING_INGESTED
            )
            manifest_row = committed[str(row["deal_id"])] if row["split"] == "evaluation" else None
            if manifest_row is None:
                eligibility_text = _text(raw_store.resolve(row["source_accession"]))
                amended = False
            else:
                eligibility_texts = [
                    _text(raw_store.resolve(accession))
                    for accession in manifest_row["source_accessions"]
                ]
                eligibility_text = _combined_source_text(eligibility_texts)
                amended = any(
                    _MERGER_AMENDMENT.search(text) for text in eligibility_texts[1:]
                )
            derived_label = _derive_eligibility(eligibility_text, amended=amended)
            if _normalized_eligibility(
                row["eligibility_label"], kind="eligibility label"
            ) != derived_label:
                raise SemanticEvidenceUnavailable("calibration eligibility label is not source-derived")
            _require_strategy_eligible(derived_label)
            outcome_event = _source_event_in(
                session, row["outcome_source"], event_type=EVENT_FILING_INGESTED
            )
            outcome_accession = str((outcome_event.payload or {}).get("accession_no") or "")
            if _derive_outcome(_text(raw_store.resolve(outcome_accession))) != row["outcome"]:
                raise SemanticEvidenceUnavailable("calibration outcome is not source-derived")
            self._verify_outcome_universe_in(session, row, raw_store)
            if row["split"] != "evaluation":
                continue
            candidate_view = {
                **{name: row[name] for name in _COMMITTED_ROW_FIELDS},
                "source_accessions": manifest_row["source_accessions"],
                "current_accession": manifest_row["current_accession"],
            }
            normalized_view = _normalized_candidate(candidate_view, authority=False)
            if any(
                normalized_view[name] != manifest_row[name]
                for name in _COMMITTED_ROW_FIELDS
            ):
                raise SemanticEvidenceUnavailable("evaluation row differs from its commitment")
            detection = _utc(
                (outcome_event.payload or {}).get("detection_ts"), field="outcome detection time"
            )
            recorded = _utc(
                outcome_event.created_at, field="outcome audit time", allow_naive=True
            )
            if (
                detection <= witnessed_at
                or recorded <= witnessed_at
                or eligibility_event.seq > manifest_row["selection_authority"][
                    "filing_event_seq"
                ]
            ):
                raise SemanticEvidenceUnavailable("evaluation evidence was not committed pre-outcome")
            self._poll_covers_outcome_in(session, row, manifest_row, outcome_event)

    def verify(self, *, deal_document: dict, calibration_document: dict, session=None) -> str:
        """Return a deterministic authority digest or raise ``SemanticEvidenceUnavailable``."""
        deal = _normalized_deal(deal_document)
        calibration = _normalized_calibration(calibration_document)

        def verify_in(active_session) -> str:
            manifest_row, manifest = self._load_manifest_in(
                active_session, calibration["selection_manifest_id"]
            )
            witnessed_at = _utc(
                manifest_row.witnessed_at, field="witnessed_at", allow_naive=True
            )
            if _utc(calibration["frozen_at"], field="frozen_at") <= witnessed_at:
                raise SemanticEvidenceUnavailable("calibration predates its selection commitment")
            self._verify_deal(deal)
            self._verify_calibration_in(active_session, calibration, manifest, witnessed_at)
            return _canonical_hash({
                "schema_version": "merger_semantic_authority.v1",
                "deal_content_hash": _canonical_hash(deal),
                "calibration_content_hash": _canonical_hash(calibration),
                "selection_content_hash": manifest_row.content_hash,
                "witness_message_id": manifest_row.witness_message_id,
                "witnessed_at": witnessed_at.isoformat(),
            })

        if session is not None:
            return verify_in(session)
        with _authority_session(self.state) as active_session:
            return verify_in(active_session)
