"""WP8 — SEC-21 chain-derived daily deep budget + deterministic urgency (contract Owner A).

Implements docs/security-hardening-backlog.md SEC-21 (denial-of-wallet as strategic
blinding): the daily deep-escalation budget is derived by COUNTING HMAC-verified
``EVENT_DEEP_ESCALATION`` entries on the signed ledger (restart-safe; a mutable counter is
attacker-writable — storage map '_consumed_on_chain' pattern), consumed under ``state.lock``
with a re-check inside the critical section. Deep escalation for a model-labeled 'urgent'
requires DETERMINISTIC corroboration — calendar proximity, halt/filing event type, or a
real quote move — the headline's self-description NEVER counts. Candidates are ranked by
deterministic signal strength (corroborated first), never arrival order, so a fake-urgent
flood cannot starve genuine signals. The budget limit comes from the AUDITED runtime
limits (EVENT_LIMITS_CHANGE, SEC-15), never raw Settings.
"""
from __future__ import annotations

import time
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from orchestrator.limits import current_limits, limits_from_verified_session
from schemas.models.audit_entry import AuditEntry
from schemas.models.market_event import MarketEvent
from storage.audit import AuditIntegrityError
from workflows.constants import EVENT_DEEP_ESCALATION, EVENT_INGEST_BATCH

_CALENDAR_WINDOW = timedelta(hours=36)
_QUOTE_MOVE_PCT = 3.0
_HARD_EVENT_TYPES = frozenset({"halt", "filing"})


def signed_event_type(state, event_id: str, *, session=None) -> str | None:
    """SEC-11 (W10) domain-row binding read-side: the ``event_type`` bound into the covering,
    HMAC-VERIFIED ``EVENT_INGEST_BATCH`` entry for ``event_id`` (events.py binds it at persist).
    Returns the SIGNED value, or ``None`` when no verified entry binds this id — the caller
    treats ``None`` (and any mismatch against the mutable ``MarketEvent.event_type`` column) as
    'no corroboration' / 'no consumption' (fail closed). A keyless writer who flips the column
    news->filing cannot also forge the signed copy (its HMAC would break -> verify_entry False
    -> ``None``), so a forged flip yields no deterministic corroboration and spends no budget."""
    if not event_id:
        return None
    with (nullcontext(session) if session is not None else state.session_factory()) as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_INGEST_BATCH)
        ).scalars().all()
    for e in entries:
        types = (e.payload or {}).get("event_types") or {}
        if event_id in types:
            if not state.audit.verify_entry(e):
                return None            # tampered ingest batch -> unverifiable -> fail closed
            return str(types[event_id])
    return None


def make_event_type_verifier(state):
    """A ``deterministic_urgency(verify_event_type=...)`` callable: True iff a MarketEvent's
    mutable ``event_type`` column (halt/filing OR ``calendar:%``) matches its signed
    EVENT_INGEST_BATCH value (SEC-11). Lazily capture one verified ledger snapshot for this
    caller's lifetime (one triage tick); neither authority nor misses survive the next tick.
    Spend authorization remains independently verified by ``DeepBudget.try_consume``."""
    bindings: dict[str, str | None] | None = None

    def _verify(event) -> bool:
        nonlocal bindings
        eid = getattr(event, "id", None)
        actual = getattr(event, "event_type", None)
        if not isinstance(eid, str) or not eid or not isinstance(actual, str) or not actual:
            return False
        if bindings is None:
            # An invalid snapshot is sticky for this tick. Publish no partial bindings if
            # verification, decoding, or the transaction fails; the next tick can retry.
            bindings = {}
            try:
                with state.session_factory() as session, session.begin():
                    _assert_budget_chain_ok(state, session)
                    entries = session.execute(select(AuditEntry).where(
                        AuditEntry.event_type == EVENT_INGEST_BATCH
                    )).scalars().all()
                    snapshot: dict[str, str | None] = {}
                    for entry in entries:
                        types = (entry.payload or {}).get("event_types")
                        if types is None:  # legacy ingest rows predate domain-row bindings
                            continue
                        if not isinstance(types, dict):
                            return False
                        for bound_id, signed in types.items():
                            if (not isinstance(bound_id, str) or not bound_id
                                    or not isinstance(signed, str) or not signed):
                                return False
                            if bound_id in snapshot and snapshot[bound_id] != signed:
                                snapshot[bound_id] = None  # conflicting bindings never recover
                            else:
                                snapshot.setdefault(bound_id, signed)
                bindings = snapshot
            except (BudgetIntegrityError, AuditIntegrityError, SQLAlchemyError,
                    OSError, TypeError, ValueError):
                return False
        signed = bindings.get(eid)
        return signed is not None and signed == actual
    return _verify


class BudgetIntegrityError(Exception):
    """A selected EVENT_DEEP_ESCALATION ledger row failed HMAC verification. The budget
    cannot be trusted, so the escalation path HALTS (fail closed) rather than undercount
    ``spent`` — which would inflate ``remaining`` and drive escalations past the audited
    cap (mirrors events.py::RawIntegrityError)."""


def _assert_budget_chain_ok(state, session):
    """FAIL CLOSED on the budget-read path (SEC-21 round-3). ``DeepBudget.spent`` scopes its
    per-row ``verify_entry`` to rows surviving ``where(event_type == EVENT_DEEP_ESCALATION)`` —
    but ``event_type`` is itself an HMAC-COVERED column, so a keyless writer that UPDATEs a
    consumed row's ``event_type`` OUT of the filter (or DELETEs it) understates ``spent`` with
    NO per-row failure: the selection predicate IS the tampered field, so the moved/erased row
    is never handed to ``verify_entry``. A FULL-chain scan sees every row regardless of the
    filter — a tampered ``event_type`` breaks that row's HMAC and a non-healed deletion is a
    seq gap — so run it before trusting the count and RAISE if it (or the external anchor, when
    wired) does not verify. Mirrors the gate's chain-first posture (SEC-20/SEC-11)."""
    anchor_store = getattr(state, "anchor_store", None)
    if anchor_store is not None:
        # SEC-11 (W10, G10 fix 2026-07-08): a WIRED store whose ``latest()`` is None (no genuine
        # out-of-domain witness, or the same-domain mirror-only store) must FAIL CLOSED for
        # parity with gate.py / gateway.py / mode_ladder.py (`if anchor is None or not ...`).
        # The old `if anchor is not None and not ...` failed OPEN on None, so a deleted witness
        # skipped the consult and only the replay-defeatable verify_chain() ran (the budget was
        # the sole outlier of the four consuming paths).
        anchor = anchor_store.latest()
        if anchor is None:
            raise BudgetIntegrityError(
                "external audit anchor absent or rollback/truncation detected — deep budget "
                "cannot be trusted (fail closed)"
            )
        verified = state.audit.verify_session_against_anchor(session, *anchor)
    else:
        verified = state.audit.verify_session(session)
    if not verified.ok:
        raise BudgetIntegrityError(
            f"audit chain or anchor failed verification ({verified.error}) — deep budget "
            "cannot be trusted (fail closed)"
        )
    return verified


class DeepBudget:
    """Chain-derived daily deep-escalation budget (SEC-21). ``spent`` counts only
    HMAC-verified ledger entries, so a fresh instance (or a fresh process) sees the same
    consumption — and a keyless DB writer cannot mint or erase budget."""

    def __init__(self, state, *, limit: int | None = None):
        self._state = state
        self._limit = limit

    @property
    def limit(self) -> int:
        return (self._limit if self._limit is not None
                else int(current_limits(self._state)["deep_model_daily_budget"]))

    def _spent_in(self, session, day: date) -> int:
        """Count only after the caller verifies this pinned transaction."""
        entries = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_DEEP_ESCALATION)
        ).scalars().all()
        count = 0
        for e in entries:
            # FAIL CLOSED (SEC-21): verify EVERY selected consumption row. A keyless writer
            # that tampers one covered field of a consumed row breaks its HMAC — treat that
            # as budget-integrity failure and RAISE, never silently skip (which would
            # undercount spent, inflate remaining, and over-escalate past the audited cap).
            if not self._state.audit.verify_entry(e):
                raise BudgetIntegrityError(
                    f"EVENT_DEEP_ESCALATION entry {getattr(e, 'id', '?')!r} failed HMAC "
                    "verification (budget ledger tampered — fail closed)"
                )
            if (e.payload or {}).get("date") == day.isoformat():
                count += 1
        return count

    def spent(self, day: date) -> int:
        with self._state.session_factory() as session, session.begin():
            _assert_budget_chain_ok(self._state, session)
            return self._spent_in(session, day)

    def _remaining_in(self, session, day: date) -> int:
        verified = _assert_budget_chain_ok(self._state, session)
        limit = (self._limit if self._limit is not None else int(
            limits_from_verified_session(self._state, session, verified)["deep_model_daily_budget"]
        ))
        return max(0, limit - self._spent_in(session, day))

    def remaining(self, day: date) -> int:
        with self._state.session_factory() as session, session.begin():
            return self._remaining_in(session, day)

    def try_consume(self, *, day: date, event_id: str, symbol: str, reason: str,
                    corroborated: bool) -> bool:
        """Atomically consume one unit: under ``state.lock``, re-check remaining, then
        append the EVENT_DEEP_ESCALATION ledger entry. True on success.

        SEC-21 (round-2) defense-in-depth backstop: an UNcorroborated consumption is
        REFUSED (return False, append nothing) so no caller can spend deep budget without
        deterministic corroboration even if a gate upstream is bypassed. The caller treats a
        False return as a deferral (Inv 7), never a drop.

        SEC-11 (W10) domain-row backstop: when the corroboration rests on the mutable
        ``MarketEvent.event_type`` (a ``event_type:{halt|filing}`` reason), reconcile it here
        against the value bound into the signed EVENT_INGEST_BATCH entry. A keyless writer who
        flipped the column to manufacture corroboration is caught (the signed copy still says
        'news' / no verified binding exists) and the consumption is REFUSED — the column alone
        is never a spend trust root. This closes the live path even if a caller computed
        ``deterministic_urgency`` without the verifier (the reason string still carries the
        claimed type)."""
        if not corroborated:
            return False
        # BUG-154: verify, read limits/count/corroboration and append in ONE transaction.
        # A stale SQLite read snapshot cannot upgrade to a writer after another commit;
        # rollback and recheck all authority on retry, including a newly tightened limit.
        for attempt in range(1, 7):
            try:
                with self._state.lock:
                    with self._state.session_factory() as session, session.begin():
                        if self._remaining_in(session, day) <= 0:
                            return False
                        if reason.startswith("event_type:"):
                            claimed = reason.split(":", 1)[1]
                            signed = signed_event_type(self._state, event_id, session=session)
                            if signed != claimed or signed not in _HARD_EVENT_TYPES:
                                return False
                        entry = self._state.audit.append_in(
                            session, EVENT_DEEP_ESCALATION, actor="budget",
                            refs={"event_id": event_id},
                            payload={"date": day.isoformat(), "event_id": event_id,
                                     "symbol": symbol, "reason": reason,
                                     "corroborated": bool(corroborated)},
                        )
                    self._state.audit.observe_committed(entry)
                return True
            except AuditIntegrityError as exc:
                raise BudgetIntegrityError(str(exc)) from exc
            except (IntegrityError, OperationalError):
                if attempt == 6:
                    raise
                time.sleep(0.02 * attempt)
        raise RuntimeError("deep budget: exhausted transaction retries")


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def deterministic_urgency(event: MarketEvent, calendar_events: Sequence[MarketEvent], *,
                          now: datetime, quote_move_fn=None,
                          verify_event_type=None) -> tuple[bool, str]:
    """Deterministic corroboration for 'urgent' (SEC-21): True iff a same-symbol calendar
    event sits within ±36h of ``now``, OR the event type is a halt/filing, OR an injected
    quote hook reports an abs move >= 3.0%. The headline's self-description NEVER counts.
    Returns (corroborated, reason).

    SEC-11 (W10) domain-row binding: when ``verify_event_type`` (a ``(event) -> bool``) is
    supplied, the halt/filing branch corroborates ONLY if it returns True — i.e. the mutable
    ``event.event_type`` column matches the value bound into the signed EVENT_INGEST_BATCH
    entry (``make_event_type_verifier``). A keyless flip news->filing therefore yields NO
    corroboration (fail closed). When ``None`` (unbound/pre-ingest events, and the deterministic
    pre-filter which spends no budget) the column is trusted as before — the fail-closed spend
    gate is ``DeepBudget.try_consume``, which reconciles every ``event_type:`` reason too."""
    now = _aware(now)
    symbol = (event.symbol or "").upper()
    if symbol:
        for cal in calendar_events or []:
            if (cal.symbol or "").upper() != symbol or cal.ts is None:
                continue
            # SEC-11 (W10, G10 fix 2026-07-08): the calendar row's ``event_type`` (``calendar:%``)
            # is itself a mutable column — a keyless writer can INSERT a forged calendar row to
            # manufacture proximity corroboration (the denial-of-wallet sibling of the closed
            # event_type-flip hole). When the verifier is supplied, a calendar row corroborates
            # ONLY if its event_type matches its signed EVENT_INGEST_BATCH binding (fail closed).
            if verify_event_type is not None and not verify_event_type(cal):
                continue
            if abs(_aware(cal.ts) - now) <= _CALENDAR_WINDOW:
                return True, f"calendar_proximity:{symbol}"
    if event.event_type in _HARD_EVENT_TYPES and (verify_event_type is None or verify_event_type(event)):
        return True, f"event_type:{event.event_type}"
    if quote_move_fn is not None and symbol:
        try:
            move = quote_move_fn(symbol)
        except Exception:
            move = None   # a broken quote hook corroborates nothing (fail closed)
        if move is not None and abs(float(move)) >= _QUOTE_MOVE_PCT:
            return True, f"quote_move:{float(move)}"
    return False, "no_deterministic_corroboration"


def _ts_key(value) -> str:
    if isinstance(value, datetime):
        return _aware(value).isoformat()
    return str(value or "")


def rank_candidates(cands: list[dict]) -> list[dict]:
    """Deterministic escalation priority (SEC-21 'prioritize by deterministic signal
    strength, not arrival order'): corroborated first, then holdings-match, then score
    descending, then ts ascending."""
    return sorted(cands, key=lambda c: (
        0 if c.get("corroborated") else 1,
        0 if c.get("holdings_match") else 1,
        -int(c.get("score") or 0),
        _ts_key(c.get("ts")),
    ))
