"""G8 — SEC-21 deep-budget + deterministic-urgency tests
(docs/security-hardening-backlog.md SEC-21: denial-of-wallet as strategic blinding).

Enforces: the daily deep budget is derived by counting HMAC-VERIFIED
EVENT_DEEP_ESCALATION ledger entries (restart-safe, tamper-proof against a keyless DB
writer); consumption is atomic under state.lock; 'urgent' requires deterministic
corroboration (calendar proximity / halt-filing / real quote move) — the headline's
self-description NEVER counts; ranking is by signal strength, never arrival order; the
limit comes from the audited runtime limits, never raw Settings.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from schemas.models.audit_entry import AuditEntry
from schemas.models.market_event import MarketEvent
from tests.workflows.conftest import NOW
from workflows.budget import (
    BudgetIntegrityError,
    DeepBudget,
    deterministic_urgency,
    rank_candidates,
)
from workflows.constants import (
    EVENT_DEEP_ESCALATION,
    EVENT_DEEP_ESCALATION_RELEASE,
    EVENT_INGEST_BATCH,
)

DAY = NOW.date()


def bind_event_type(st, event_id, event_type):
    """Seed the signed EVENT_INGEST_BATCH binding events.py writes at persist time, so an
    ``event_type:{halt|filing}`` corroboration passes the SEC-11 (W10) reconciliation in
    ``try_consume`` (an unbound / forged event_type spends no deep budget — fail closed)."""
    st.audit.append(EVENT_INGEST_BATCH, actor="ingest",
                    refs={"model": "market_event", "row_ids": [event_id]},
                    payload={"raw_key": "events-x", "date": DAY.isoformat(),
                             "content_hash": "h" * 64, "count": 1,
                             "event_types": {event_id: event_type}})


def consume(budget, i=1, **kw):
    base = dict(day=DAY, event_id=f"evt-{i}", symbol="AAPL", reason="quote_move:4.0",
                corroborated=True)
    base.update(kw)
    return budget.try_consume(**base)


def _entry_id(value) -> str:
    """try_consume returns the ledger id on success and False on refusal."""
    assert isinstance(value, str) and value
    return value


def make_event(symbol="AAPL", event_type="news", ts=NOW, headline="URGENT!!! act now"):
    return MarketEvent(id="evt-x", symbol=symbol, source="finnhub", event_type=event_type,
                       headline=headline, summary=None, raw_ref="finnhub:1",
                       content_hash="c" * 64, ts=ts, ingested_at=NOW, created_at=NOW)


def cal_event(symbol="AAPL", ts=NOW + timedelta(hours=10)):
    return MarketEvent(id="cal-x", symbol=symbol, source="calendar",
                       event_type="calendar:earnings", headline=f"earnings: {symbol}",
                       summary=None, raw_ref="calendar:1", content_hash="c" * 64,
                       ts=ts, ingested_at=NOW, created_at=NOW)


# ---- DeepBudget (chain-derived) --------------------------------------------------------
def test_budget_consumes_to_limit_then_refuses(st):
    b = DeepBudget(st, limit=2)
    assert b.spent(DAY) == 0 and b.remaining(DAY) == 2
    assert _entry_id(consume(b, 1))
    assert _entry_id(consume(b, 2))
    assert consume(b, 3) is False           # over budget -> caller defers, never drops
    assert b.spent(DAY) == 2 and b.remaining(DAY) == 0


def test_budget_is_restart_safe_chain_derived(st):
    b = DeepBudget(st, limit=3)
    consume(b, 1)
    consume(b, 2)
    fresh = DeepBudget(st, limit=3)         # a fresh instance sees the same signed ledger
    assert fresh.spent(DAY) == 2 and fresh.remaining(DAY) == 1


def test_budget_tamper_fails_closed_not_undercount(st):
    """SEC-21 fail-closed: a keyless DB writer that mutates ONE covered field of a consumed
    EVENT_DEEP_ESCALATION row breaks its HMAC. The budget must RAISE (halt escalation),
    NEVER silently undercount — undercounting would inflate remaining() and drive
    escalations past the audited cap. Reproduces the fail-OPEN bypass (old code returned 1)."""
    b = DeepBudget(st, limit=5)
    consume(b, 1)
    consume(b, 2)
    with st.session_factory() as s, s.begin():
        e = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION).order_by(
            AuditEntry.seq)).scalars().first()
        s.execute(update(AuditEntry).where(AuditEntry.id == e.id).values(
            payload={**e.payload, "symbol": "EVIL"}))
    with pytest.raises(BudgetIntegrityError):
        b.spent(DAY)
    with pytest.raises(BudgetIntegrityError):
        b.remaining(DAY)
    with pytest.raises(BudgetIntegrityError):     # caller can't be handed a phantom unit
        b.try_consume(day=DAY, event_id="evt-9", symbol="AAPL",
                      reason="quote_move:4.0", corroborated=True)


def test_budget_event_type_moved_out_of_filter_fails_closed(st):
    """SEC-21 round-3 (keyless-writer BYPASS): ``spent`` scopes its per-row verify to rows
    surviving ``where(event_type == EVENT_DEEP_ESCALATION)``. Because event_type is itself an
    HMAC-COVERED column, a keyless writer that UPDATEs a consumed row's event_type OUT of the
    filter (deep_escalation -> noise) removes it from the selection — the per-row verify never
    sees it, so old code silently returned spent()==1 (understated), inflating remaining() and
    granting one extra escalation past the audited cap. A full-chain verify sees the row
    regardless of the filter (its HMAC is now broken) and must RAISE (fail closed)."""
    b = DeepBudget(st, limit=5)
    consume(b, 1)
    consume(b, 2)
    assert b.spent(DAY) == 2                      # clean chain: both consumed units counted
    # Move ONE consumed row OUT of the event_type filter (event_type is HMAC-covered).
    with st.session_factory() as s, s.begin():
        e = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION).order_by(
            AuditEntry.seq)).scalars().first()
        s.execute(update(AuditEntry).where(AuditEntry.id == e.id).values(event_type="noise"))
    with pytest.raises(BudgetIntegrityError):
        b.spent(DAY)                              # was: silently 1 (understated) -> now RAISES
    with pytest.raises(BudgetIntegrityError):
        b.remaining(DAY)
    with pytest.raises(BudgetIntegrityError):     # no phantom extra escalation past the cap
        b.try_consume(day=DAY, event_id="evt-9", symbol="AAPL",
                      reason="quote_move:4.0", corroborated=True)


def test_try_consume_refuses_uncorroborated(st):
    """SEC-21 round-2 backstop: try_consume must REFUSE an uncorroborated consumption —
    return False and append NOTHING — so no caller can spend deep budget without
    deterministic corroboration. Reproduces the round-2 bypass (old code appended an
    EVENT_DEEP_ESCALATION regardless of the corroborated flag)."""
    b = DeepBudget(st, limit=5)
    assert consume(b, 1, corroborated=False) is False
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION)).scalars().all()
    assert rows == [], "an uncorroborated try_consume must not append a budget ledger entry"
    assert b.spent(DAY) == 0 and b.remaining(DAY) == 5   # budget untouched
    # a corroborated consumption still succeeds afterwards
    assert _entry_id(consume(b, 2, corroborated=True)) and b.spent(DAY) == 1


def test_budget_per_day_isolation(st):
    b = DeepBudget(st, limit=2)
    consume(b, 1)
    other_day = (NOW + timedelta(days=1)).date()
    assert b.spent(other_day) == 0 and b.remaining(other_day) == 2


def test_budget_ledger_payload_shape(st):
    b = DeepBudget(st, limit=1)
    bind_event_type(st, "evt-7", "halt")          # SEC-11 (W10): event_type corroboration needs a signed binding
    consume(b, 7, symbol="MSFT", reason="event_type:halt", corroborated=True)
    with st.session_factory() as s:
        e = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION)).scalars().one()
    assert e.payload["date"] == DAY.isoformat()
    assert e.payload["event_id"] == "evt-7" and e.payload["symbol"] == "MSFT"
    assert e.payload["reason"] == "event_type:halt" and e.payload["corroborated"] is True
    assert st.audit.verify_chain().ok


def test_default_limit_reads_audited_limits(st):
    try:
        from orchestrator.limits import EVENT_LIMITS_CHANGE
    except ImportError:      # reader not extracted yet — same event string in app.py
        from orchestrator.app import EVENT_LIMITS_CHANGE
    assert DeepBudget(st).limit == 5                       # Settings default
    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 2}})
    assert DeepBudget(st).limit == 2                       # audited value wins
    assert DeepBudget(st, limit=9).limit == 9              # explicit override


def test_release_restores_one_unused_unit_and_does_not_mint(st):
    """An unused reservation nets back to zero. A second release of that row, or a
    duplicate signed release, must not push remaining above the audited limit."""
    b = DeepBudget(st, limit=1)
    assert _entry_id(consume(b, 1))
    assert b.spent(DAY) == 1 and b.remaining(DAY) == 0
    assert b.release(day=DAY, event_id="evt-1", reason="context_limit") is True
    assert b.spent(DAY) == 0 and b.remaining(DAY) == 1
    assert b.release(day=DAY, event_id="evt-1", reason="context_limit") is False
    assert b.spent(DAY) == 0 and b.remaining(DAY) == 1
    fresh = DeepBudget(st, limit=1)
    assert fresh.spent(DAY) == 0 and fresh.remaining(DAY) == 1

    with st.session_factory() as s:
        row = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION_RELEASE)).scalars().one()
        payload, refs = dict(row.payload), dict(row.refs or {})
    st.audit.append(EVENT_DEEP_ESCALATION_RELEASE, actor="budget", refs=refs, payload=payload)
    assert b.spent(DAY) == 0 and b.remaining(DAY) == 1
    assert _entry_id(consume(b, 2))
    assert consume(b, 3) is False
    assert b.spent(DAY) == 1


def test_release_binds_to_the_try_consume_entry_id(st):
    """A later same-event consumption must not steal the release.

    ``state.lock`` is process-local, so binding to the latest unreleased row
    can attribute a release to a row this caller did not reserve. The id
    ``try_consume`` returns is the only row ``release`` may cancel. Omitting
    the id still releases the latest unreleased row.
    """
    b = DeepBudget(st, limit=2)
    first = _entry_id(consume(b, 1))
    second = _entry_id(b.try_consume(
        day=DAY, event_id="evt-1", symbol="AAPL", reason="quote_move:4.0",
        corroborated=True))
    assert first != second
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION).order_by(AuditEntry.seq)
        ).scalars().all()
    assert [row.id for row in rows] == [first, second]

    other_day = (NOW + timedelta(days=1)).date()
    assert b.release(day=DAY, event_id="evt-other", reason="context_limit",
                     entry_id=first) is False
    assert b.release(day=other_day, event_id="evt-1", reason="context_limit",
                     entry_id=first) is False
    assert b.release(day=DAY, event_id="evt-1", reason="context_limit",
                     entry_id="") is False
    assert b.release(day=DAY, event_id="evt-1", reason="context_limit",
                     entry_id="missing") is False
    assert b.spent(DAY) == 2 and b.remaining(DAY) == 0

    assert b.release(day=DAY, event_id="evt-1", reason="context_limit",
                     entry_id=first) is True
    assert b.spent(DAY) == 1 and b.remaining(DAY) == 1
    assert b.release(day=DAY, event_id="evt-1", reason="context_limit",
                     entry_id=first) is False
    assert b.spent(DAY) == 1
    with st.session_factory() as s:
        release = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION_RELEASE)).scalars().one()
    assert release.payload["releases_entry_id"] == first
    assert st.audit.verify_entry(release)

    assert b.release(day=DAY, event_id="evt-1", reason="context_limit") is True
    assert b.spent(DAY) == 0 and b.remaining(DAY) == 2
    assert b.release(day=DAY, event_id="evt-1", reason="context_limit") is False
    assert b.remaining(DAY) == 2


def test_release_does_not_refund_a_different_event_or_day(st):
    b = DeepBudget(st, limit=2)
    assert _entry_id(consume(b, 1))
    other_day = (NOW + timedelta(days=1)).date()
    assert b.release(day=DAY, event_id="evt-other", reason="context_limit") is False
    assert b.release(day=other_day, event_id="evt-1", reason="context_limit") is False
    assert b.release(day=DAY, event_id="", reason="context_limit") is False
    assert b.spent(DAY) == 1 and b.remaining(DAY) == 1
    assert b.spent(other_day) == 0
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION_RELEASE)).scalars().all()
    assert rows == []


def test_tampered_release_fails_closed(st):
    b = DeepBudget(st, limit=2)
    consume(b, 1)
    assert b.release(day=DAY, event_id="evt-1", reason="context_limit") is True
    assert b.spent(DAY) == 0
    with st.session_factory() as s, s.begin():
        row = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION_RELEASE)).scalars().one()
        s.execute(update(AuditEntry).where(AuditEntry.id == row.id).values(
            payload={**row.payload, "event_id": "evt-other"}))
    with pytest.raises(BudgetIntegrityError):
        b.spent(DAY)
    with pytest.raises(BudgetIntegrityError):
        b.remaining(DAY)
    with pytest.raises(BudgetIntegrityError):
        b.release(day=DAY, event_id="evt-1", reason="context_limit")
    with pytest.raises(BudgetIntegrityError):
        consume(b, 2)


def test_normal_consume_without_release_is_unchanged(st):
    b = DeepBudget(st, limit=2)
    assert _entry_id(consume(b, 1))
    assert _entry_id(consume(b, 2))
    assert consume(b, 3) is False
    assert b.spent(DAY) == 2 and b.remaining(DAY) == 0
    with st.session_factory() as s:
        releases = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION_RELEASE)).scalars().all()
        spends = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION)).scalars().all()
    assert releases == [] and len(spends) == 2


# ---- deterministic_urgency (headline self-description NEVER counts) --------------------
def test_headline_self_description_never_counts():
    ok, reason = deterministic_urgency(make_event(), [], now=NOW)
    assert ok is False and reason == "no_deterministic_corroboration"


def test_calendar_proximity_corroborates():
    ok, reason = deterministic_urgency(make_event(), [cal_event()], now=NOW)
    assert ok is True and reason == "calendar_proximity:AAPL"


def test_calendar_wrong_symbol_or_too_far_does_not():
    ok, _ = deterministic_urgency(make_event(), [cal_event(symbol="MSFT")], now=NOW)
    assert ok is False
    far = cal_event(ts=NOW + timedelta(hours=48))
    ok, _ = deterministic_urgency(make_event(), [far], now=NOW)
    assert ok is False


def test_naive_calendar_ts_treated_as_utc():
    naive = cal_event(ts=datetime(2026, 7, 7, 20, 0))      # SQLite round-trip drops tzinfo
    ok, _ = deterministic_urgency(make_event(), [naive], now=NOW)
    assert ok is True


def test_halt_and_filing_corroborate():
    ok, reason = deterministic_urgency(make_event(event_type="halt"), [], now=NOW)
    assert ok is True and reason == "event_type:halt"
    ok, reason = deterministic_urgency(make_event(event_type="filing"), [], now=NOW)
    assert ok is True and reason == "event_type:filing"


def test_quote_move_hook():
    ok, reason = deterministic_urgency(make_event(), [], now=NOW,
                                       quote_move_fn=lambda s: -4.2)
    assert ok is True and reason.startswith("quote_move:")
    ok, _ = deterministic_urgency(make_event(), [], now=NOW, quote_move_fn=lambda s: 1.0)
    assert ok is False
    ok, _ = deterministic_urgency(make_event(), [], now=NOW, quote_move_fn=lambda s: None)
    assert ok is False
    ok, _ = deterministic_urgency(make_event(), [], now=NOW,
                                  quote_move_fn=lambda s: 1 / 0)   # broken hook fails closed
    assert ok is False


# ---- rank_candidates (never arrival order) ---------------------------------------------
def test_rank_candidates_deterministic_priority():
    ts0 = NOW - timedelta(minutes=30)
    ts1 = NOW - timedelta(minutes=5)
    cands = [
        {"event_id": "d", "corroborated": False, "holdings_match": False, "score": 99, "ts": ts0},
        {"event_id": "c", "corroborated": True, "holdings_match": False, "score": 70, "ts": ts0},
        {"event_id": "a", "corroborated": True, "holdings_match": True, "score": 80, "ts": ts1},
        {"event_id": "b", "corroborated": True, "holdings_match": True, "score": 80, "ts": ts0},
    ]
    ranked = rank_candidates(cands)
    assert [c["event_id"] for c in ranked] == ["b", "a", "c", "d"]
    assert [c["event_id"] for c in ranked] != [c["event_id"] for c in cands]
