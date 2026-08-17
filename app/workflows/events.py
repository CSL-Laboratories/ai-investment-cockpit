"""WP8 — the ingest→storage persistence bridge (contract Owner A 'events.py').

The missing layer between WP3's callback-only ingest and WP2's stores: nothing before WP8
persisted MarketEvent rows or raw parquet batches. Implements docs/build-scaffold-ultracode.md
WP8 (ingest window) and SEC-13 / Invariant 8 (docs/security-hardening-backlog.md): raw items
are written ONCE to parquet, the batch ``content_hash`` returned by ``write_snapshot`` is
bound into the audit chain via ``EVENT_INGEST_BATCH``, and every read-back re-verifies the
snapshot against that audit-bound hash (``verify_snapshot`` + per-item
``ingest.normalize.content_hash``) and fails CLOSED (``RawIntegrityError`` /
``RawUnavailableError`` — grounding treats an unresolvable ref as flagged, SEC-14).

Owned mapping (contract 'Mapping rules'):
  * MarketEvent.id      = new opaque id (core.ids)
  * MarketEvent.symbol  = first validated symbol (the FULL ``symbols`` list stays in the
    raw parquet record; symbol-less items keep an empty-string symbol, never dropped)
  * raw_ref             = ``parquet://{date}/events-{batch_id}#{idx}`` (one batch per
    persist call; ``raw_key = events-{batch_id}`` is the parquet symbol key); with no
    parquet root the ingest identity form ``{source}:{article_id}`` is kept and
    ``resolve_raw`` raises ``RawUnavailableError``
  * MarketEvent.content_hash = the item's ingest-bound ``content_hash`` field (SEC-13:
    ``to_market_event`` hashes the raw feed payload at ingest; the row carries THAT hash,
    per the WP1a MarketEvent model: "bound at ingest"). Dicts without the field fall back
    to ``ingest.normalize.content_hash`` of the stored raw record.
  * dedupe within call AND against the DB on the raw source identity (source, article_id)
    — the identity string ``{source}:{article_id}`` is stored inside the raw record and
    matched against existing rows' ``raw_ref`` (fallback rows) and ``content_hash``
    (parquet rows; a byte-identical re-emit hashes identically).

Raw parquet records are stored as ``{"idx": i, "identity": ..., "item": <canonical JSON>}``
so mixed-type fields (Alpaca ISO ``ts`` vs Finnhub epoch ``ts``) survive the parquet
round-trip byte-exactly; ``resolve_raw`` returns the decoded item dict.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select

from core.ids import new_id
from ingest.normalize import content_hash
from schemas.models.audit_entry import AuditEntry
from schemas.models.market_event import MarketEvent
from storage.parquet_store import (
    SnapshotIntegrityError,
    read_snapshot,
    read_verified_snapshot,
    write_snapshot,
)
from workflows.constants import EVENT_INGEST_BATCH


class RawIntegrityError(Exception):
    """Raw read-back failed verification against the audit-bound hash (fail closed)."""


class RawUnavailableError(Exception):
    """The raw_ref does not point into the verified parquet store (fail closed)."""


_PARQUET_REF = re.compile(r"^parquet://(?P<date>\d{4}-\d{2}-\d{2})/(?P<key>[^#/]+)#(?P<idx>\d+)$")


@dataclass(frozen=True)
class PersistResult:
    stored: list[MarketEvent]
    duplicates: int
    raw_key: str | None


def _aware(dt: datetime) -> datetime:
    """SQLite round-trips may drop tzinfo; interpret naive as UTC (house precedent:
    brokers/gateway.py:81, bridge/approval.py:88)."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _parse_ts(ts, clock) -> datetime:
    """Ingest dicts carry ts as ISO string, epoch number, or None — normalize to a tz-aware
    UTC datetime, falling back to clock.now() (never wall-clock)."""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return _aware(clock.now())
    if isinstance(ts, str):
        try:
            return _aware(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except ValueError:
            return _aware(clock.now())
    return _aware(clock.now())


def _identity(item: dict) -> str:
    return f"{item.get('source')}:{item.get('article_id')}"


def persist_events(state, event_dicts: list[dict], *, parquet_root, clock=None) -> PersistResult:
    """Persist a batch of normalized ingest dicts: ONE write-once parquet batch + one
    ``EVENT_INGEST_BATCH`` audit entry binding its content_hash + MarketEvent rows, all
    row-writes covered in the same transaction as the audit entry (SEC-13)."""
    clock = clock if clock is not None else state.clock
    now = _aware(clock.now())

    # ---- dedupe: within call AND against DB on raw source identity ------------------
    fresh: list[dict] = []
    hashes: list[str] = []
    duplicates = 0
    seen: set[str] = set()
    for item in event_dicts or []:
        ident = _identity(item)
        if ident in seen:
            duplicates += 1
            continue
        seen.add(ident)
        fresh.append(item)
        hashes.append(_item_hash(item))
    if fresh:
        idents = [_identity(i) for i in fresh]
        with state.session_factory() as s:
            existing = s.execute(
                select(MarketEvent.raw_ref, MarketEvent.content_hash).where(
                    or_(MarketEvent.raw_ref.in_(idents), MarketEvent.content_hash.in_(hashes))
                )
            ).all()
        known_refs = {r[0] for r in existing}
        known_hashes = {r[1] for r in existing}
        kept, kept_hashes = [], []
        for item, h in zip(fresh, hashes):
            if _identity(item) in known_refs or h in known_hashes:
                duplicates += 1
            else:
                kept.append(item)
                kept_hashes.append(h)
        fresh, hashes = kept, kept_hashes

    if not fresh:
        return PersistResult(stored=[], duplicates=duplicates, raw_key=None)

    # ---- raw persistence: ONE write-once parquet batch per persist call --------------
    date = now.date().isoformat()
    raw_key: str | None = None
    batch_hash: str | None = None
    refs_by_idx: list[str] = []
    if parquet_root is not None:
        batch_id = new_id()
        raw_key = f"events-{batch_id}"
        records = [
            {"idx": i, "identity": _identity(item), "item": _canonical_item(item)}
            for i, item in enumerate(fresh)
        ]
        _, batch_hash = write_snapshot(parquet_root, date=date, symbol=raw_key, records=records)
        refs_by_idx = [f"parquet://{date}/{raw_key}#{i}" for i in range(len(fresh))]
    else:
        refs_by_idx = [item.get("raw_ref") or _identity(item) for item in fresh]

    # ---- MarketEvent rows + covering EVENT_INGEST_BATCH in ONE transaction -----------
    rows: list[MarketEvent] = []
    for item, item_hash, raw_ref in zip(fresh, hashes, refs_by_idx):
        symbols = item.get("symbols") or []
        rows.append(MarketEvent(
            id=new_id("evt"),
            symbol=symbols[0] if symbols else "",
            source=str(item.get("source", "")),
            event_type=str(item.get("event_type", "news")),
            headline=str(item.get("headline", "")),
            summary=item.get("summary"),
            raw_ref=raw_ref,
            content_hash=item_hash,
            ts=_parse_ts(item.get("ts"), clock),
            ingested_at=now,
            created_at=now,
        ))
    # SEC-11 (W10) domain-row binding: bind each row's mutable ``event_type`` (keyed by its
    # immutable id) into the HMAC-signed EVENT_INGEST_BATCH payload. ``deterministic_urgency``
    # treats ``event_type in {"halt","filing"}`` as auto-corroboration (workflows/budget.py),
    # yet the column is otherwise unsigned — a keyless writer flipping news->filing could
    # manufacture corroboration. The signed copy here is the tamper-evident trust root the
    # budget read-path reconciles against (workflows.budget.signed_event_type).
    payload = {
        "raw_key": raw_key, "date": date, "content_hash": batch_hash, "count": len(rows),
        "event_types": {r.id: r.event_type for r in rows},
    }
    with state.session_factory() as s, s.begin():
        for row in rows:
            s.add(row)
        s.flush()
        entry = state.audit.append_in(
            s, EVENT_INGEST_BATCH, actor="ingest",
            refs={"model": "market_event", "row_ids": [r.id for r in rows]},
            payload=payload,
        )
        for row in rows:
            s.expunge(row)
        s.expunge(entry)
    return PersistResult(stored=rows, duplicates=duplicates, raw_key=raw_key)


def _record_item(item: dict) -> dict:
    """The exact dict persisted as the raw record's ``item``."""
    return dict(item)


def _item_hash(item: dict) -> str:
    """The hash a MarketEvent row is bound to: the ingest-bound ``content_hash`` field
    (``to_market_event`` hashed the raw feed payload at ingest — SEC-13 'bound at
    ingest'); dicts without one get ``content_hash`` of the stored record itself."""
    h = item.get("content_hash")
    if isinstance(h, str) and h:
        return h
    return content_hash(_record_item(item))


def _canonical_item(item: dict) -> str:
    return json.dumps(_record_item(item), sort_keys=True, separators=(",", ":"), default=str)


def events_in_window(state, start: datetime, end: datetime) -> list[MarketEvent]:
    """MarketEvent rows with ``ingested_at`` in [start, end), ordered deterministically.
    Windows key on arrival time (ingested_at), never the source-claimed ts — an attacker
    controls the feed's self-declared timestamp, not our clock."""
    start = _aware(start)
    end = _aware(end)
    with state.session_factory() as s:
        rows = s.execute(
            select(MarketEvent)
            .where(MarketEvent.ingested_at >= start, MarketEvent.ingested_at < end)
            .order_by(MarketEvent.ingested_at, MarketEvent.id)
        ).scalars().all()
        for r in rows:
            s.expunge(r)
    return list(rows)


def _audit_bound_hash(state, raw_key: str) -> str:
    """The batch content_hash for ``raw_key`` from a per-entry HMAC-VERIFIED
    EVENT_INGEST_BATCH entry (chain-derived read — verify_entry required, storage map
    '_consumed_on_chain' pattern). Fail closed when absent/unverified."""
    with state.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_INGEST_BATCH)
        ).scalars().all()
    for e in entries:
        if (e.payload or {}).get("raw_key") == raw_key:
            if not state.audit.verify_entry(e):
                raise RawIntegrityError(
                    f"ingest_batch audit entry for {raw_key!r} failed HMAC verification"
                )
            h = (e.payload or {}).get("content_hash")
            if not h:
                raise RawIntegrityError(f"ingest_batch entry for {raw_key!r} carries no hash")
            return h
    raise RawIntegrityError(f"no verified ingest_batch audit entry for raw_key {raw_key!r}")


def resolve_raw(parquet_root, raw_ref: str, *, state=None, row=None) -> dict:
    """Verified read-back of ONE raw item by its ``parquet://{date}/{key}#{idx}`` ref.

    Verification (fail closed, SEC-13/SEC-14):
      1. ``state`` given  -> the batch hash is looked up from a verify_entry-checked
         EVENT_INGEST_BATCH entry and ``verify_snapshot`` must pass (RawIntegrityError
         otherwise). Grounding-path resolvers MUST pass ``state``.
      2. ``row`` given    -> the re-read item's ingest-bound hash (its ``content_hash``
         field, else ``ingest.normalize.content_hash`` of the record) must equal
         ``row.content_hash`` (RawIntegrityError on mismatch); record-body tampering is
         caught by the audit-bound batch hash in (1).
    Non-parquet refs (the ``{source}:{article_id}`` fallback) raise RawUnavailableError —
    grounding then fails closed.
    """
    m = _PARQUET_REF.match(raw_ref or "")
    if not m:
        raise RawUnavailableError(f"raw_ref does not point into the parquet store: {raw_ref!r}")
    date, key, idx = m.group("date"), m.group("key"), int(m.group("idx"))
    if state is not None:
        expected = _audit_bound_hash(state, key)
        try:
            records = read_verified_snapshot(parquet_root, date, key, expected)
        except FileNotFoundError as exc:
            raise RawUnavailableError(f"raw snapshot missing for {raw_ref!r}") from exc
        except SnapshotIntegrityError:
            raise RawIntegrityError(f"snapshot {date}/{key} does not match audit-bound hash")

    else:
        try:
            records = read_snapshot(parquet_root, date, key)
        except FileNotFoundError as exc:
            raise RawUnavailableError(f"raw snapshot missing for {raw_ref!r}") from exc
    if idx >= len(records):
        raise RawIntegrityError(f"raw index {idx} out of range for {date}/{key}")
    try:
        item = json.loads(records[idx]["item"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RawIntegrityError(f"raw record {raw_ref!r} is not decodable") from exc
    if row is not None and _item_hash(item) != row.content_hash:
        raise RawIntegrityError(
            f"item hash mismatch for {raw_ref!r} vs MarketEvent {getattr(row, 'id', '?')}"
        )
    return item
