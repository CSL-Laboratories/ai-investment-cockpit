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

from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry
from schemas.models.market_event import MarketEvent
from workflows.constants import EVENT_DEEP_ESCALATION

_CALENDAR_WINDOW = timedelta(hours=36)
_QUOTE_MOVE_PCT = 3.0


class BudgetIntegrityError(Exception):
    """A selected EVENT_DEEP_ESCALATION ledger row failed HMAC verification. The budget
    cannot be trusted, so the escalation path HALTS (fail closed) rather than undercount
    ``spent`` — which would inflate ``remaining`` and drive escalations past the audited
    cap (mirrors events.py::RawIntegrityError)."""


def _assert_budget_chain_ok(state) -> None:
    """FAIL CLOSED on the budget-read path (SEC-21 round-3). ``DeepBudget.spent`` scopes its
    per-row ``verify_entry`` to rows surviving ``where(event_type == EVENT_DEEP_ESCALATION)`` —
    but ``event_type`` is itself an HMAC-COVERED column, so a keyless writer that UPDATEs a
    consumed row's ``event_type`` OUT of the filter (or DELETEs it) understates ``spent`` with
    NO per-row failure: the selection predicate IS the tampered field, so the moved/erased row
    is never handed to ``verify_entry``. A FULL-chain scan sees every row regardless of the
    filter — a tampered ``event_type`` breaks that row's HMAC and a non-healed deletion is a
    seq gap — so run it before trusting the count and RAISE if it (or the external anchor, when
    wired) does not verify. Mirrors the gate's chain-first posture (SEC-20/SEC-11)."""
    v = state.audit.verify_chain()
    if not v.ok:
        raise BudgetIntegrityError(
            f"audit chain failed verification ({v.error}) — deep budget cannot be trusted "
            "(fail closed)"
        )
    anchor_store = getattr(state, "anchor_store", None)
    if anchor_store is not None:
        anchor = anchor_store.latest()
        if anchor is not None and not state.audit.verify_against_anchor(*anchor).ok:
            raise BudgetIntegrityError(
                "external audit anchor rollback/truncation detected — deep budget cannot be "
                "trusted (fail closed)"
            )


def _effective_limit(state) -> int:
    """The audited ``deep_model_daily_budget`` via the limits reader (SEC-15: effective
    values come from the latest EVENT_LIMITS_CHANGE, falling back to Settings defaults)."""
    try:
        from orchestrator.limits import current_limits
    except ImportError:   # pre-extraction fallback: the reader still lives in app.py
        from orchestrator.app import _current_limits as current_limits
    return int(current_limits(state)["deep_model_daily_budget"])


class DeepBudget:
    """Chain-derived daily deep-escalation budget (SEC-21). ``spent`` counts only
    HMAC-verified ledger entries, so a fresh instance (or a fresh process) sees the same
    consumption — and a keyless DB writer cannot mint or erase budget."""

    def __init__(self, state, *, limit: int | None = None):
        self._state = state
        self._limit = limit

    @property
    def limit(self) -> int:
        return self._limit if self._limit is not None else _effective_limit(self._state)

    def spent(self, day: date) -> int:
        # SEC-21 round-3: a full-chain integrity check FIRST — a keyless writer that moves a
        # consumed row OUT of the event_type filter (or deletes it) understates the per-row
        # count below; verify_chain scans ALL rows unfiltered and catches it. Fail closed.
        _assert_budget_chain_ok(self._state)
        with self._state.session_factory() as s:
            entries = s.execute(
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

    def remaining(self, day: date) -> int:
        return max(0, self.limit - self.spent(day))

    def try_consume(self, *, day: date, event_id: str, symbol: str, reason: str,
                    corroborated: bool) -> bool:
        """Atomically consume one unit: under ``state.lock``, re-check remaining, then
        append the EVENT_DEEP_ESCALATION ledger entry. True on success.

        SEC-21 (round-2) defense-in-depth backstop: an UNcorroborated consumption is
        REFUSED (return False, append nothing) so no caller can spend deep budget without
        deterministic corroboration even if a gate upstream is bypassed. The caller treats a
        False return as a deferral (Inv 7), never a drop."""
        if not corroborated:
            return False
        with self._state.lock:
            if self.remaining(day) <= 0:
                return False
            self._state.audit.append(
                EVENT_DEEP_ESCALATION, actor="budget",
                refs={"event_id": event_id},
                payload={
                    "date": day.isoformat(),
                    "event_id": event_id,
                    "symbol": symbol,
                    "reason": reason,
                    "corroborated": bool(corroborated),
                },
            )
            return True


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def deterministic_urgency(event: MarketEvent, calendar_events: Sequence[MarketEvent], *,
                          now: datetime, quote_move_fn=None) -> tuple[bool, str]:
    """Deterministic corroboration for 'urgent' (SEC-21): True iff a same-symbol calendar
    event sits within ±36h of ``now``, OR the event type is a halt/filing, OR an injected
    quote hook reports an abs move >= 3.0%. The headline's self-description NEVER counts.
    Returns (corroborated, reason)."""
    now = _aware(now)
    symbol = (event.symbol or "").upper()
    if symbol:
        for cal in calendar_events or []:
            if (cal.symbol or "").upper() != symbol or cal.ts is None:
                continue
            if abs(_aware(cal.ts) - now) <= _CALENDAR_WINDOW:
                return True, f"calendar_proximity:{symbol}"
    if event.event_type in {"halt", "filing"}:
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
