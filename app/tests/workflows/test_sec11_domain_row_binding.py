"""W10 SEC-11 (c) REGRESSION — MarketEvent.event_type is bound into the signed chain.

The GAP the WP8 review named (security-hardening-backlog SEC-11, L411-413): ``Order`` open-state
was already signed, but ``MarketEvent.event_type`` was a mutable column bound into NO signed
payload — yet ``deterministic_urgency`` treats ``event_type in {"halt","filing"}`` as
auto-corroboration for the deep-escalation budget (workflows/budget.py). A keyless writer flipping
a row news->filing could manufacture corroboration and drain the audited budget.

W10 binds each row's ``event_type`` (keyed by its immutable id) into the HMAC-signed
EVENT_INGEST_BATCH payload (events.py) and RECONCILES it on the budget read-path (budget.py):
``deterministic_urgency(verify_event_type=...)`` refuses the halt/filing corroboration on a flip,
and ``try_consume`` refuses to spend on any ``event_type:`` reason whose signed value doesn't
match — so the live path is closed even if a caller computed urgency without the verifier. This
file also pins the SEC-21 deep-budget delete-then-refill closure that the monotonic seq (W10-b)
delivers on the budget read-path.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update

from bridge.anchor import GitAuditLogAnchorStore
from bridge.witness import GitCommitWitness
from ingest.normalize import to_market_event
from schemas.models.audit_entry import AuditEntry
from schemas.models.market_event import MarketEvent
from tests.workflows.conftest import NOW
from workflows.budget import (
    BudgetIntegrityError,
    DeepBudget,
    deterministic_urgency,
    make_event_type_verifier,
    signed_event_type,
)
from workflows.constants import EVENT_DEEP_ESCALATION, EVENT_INGEST_BATCH
from workflows.events import persist_events

DAY = NOW.date()


def _persist_one(st, clock, *, event_type="news", symbol="AAPL", article_id="a-1"):
    # headline keyed on article_id so distinct events never collide on content_hash (dedupe).
    d = to_market_event(source="finnhub", article_id=article_id, symbols=[symbol],
                        headline=f"h-{article_id}", summary="s", ts=NOW.isoformat(),
                        event_type=event_type)
    return persist_events(st, [d], parquet_root=None, clock=clock).stored[0]


def _flip_event_type(st, event_id, new_type):
    with st.session_factory() as s, s.begin():
        s.execute(update(MarketEvent).where(MarketEvent.id == event_id).values(event_type=new_type))


def _reread(st, event_id):
    with st.session_factory() as s:
        row = s.execute(select(MarketEvent).where(MarketEvent.id == event_id)).scalar_one()
        s.expunge(row)
    return row


# ---- deterministic_urgency reconciles the flip (G10 repro 4) ---------------------------
def test_flip_event_type_yields_no_corroboration(st, clock):
    ev = _persist_one(st, clock, event_type="news")
    _flip_event_type(st, ev.id, "filing")               # keyless flip news -> filing
    flipped = _reread(st, ev.id)
    assert flipped.event_type == "filing"
    ok, reason = deterministic_urgency(flipped, [], now=NOW,
                                       verify_event_type=make_event_type_verifier(st))
    assert ok is False and reason == "no_deterministic_corroboration"
    # WITHOUT the binding check the mutable column would (wrongly) corroborate — proving the
    # signed binding is exactly what closes it.
    ok2, _ = deterministic_urgency(flipped, [], now=NOW)
    assert ok2 is True


def test_genuine_filing_still_corroborates(st, clock):
    ev = _persist_one(st, clock, event_type="filing")
    ok, reason = deterministic_urgency(_reread(st, ev.id), [], now=NOW,
                                       verify_event_type=make_event_type_verifier(st))
    assert ok is True and reason == "event_type:filing"


# ---- try_consume backstop closes the live path even without the verifier ---------------
def test_try_consume_refuses_forged_event_type(st, clock):
    ev = _persist_one(st, clock, event_type="news")
    _flip_event_type(st, ev.id, "filing")
    b = DeepBudget(st, limit=5)
    ok = b.try_consume(day=DAY, event_id=ev.id, symbol="AAPL",
                       reason="event_type:filing", corroborated=True)
    assert ok is False                                  # forged event_type -> no spend
    with st.session_factory() as s:
        deep = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION)).scalars().all()
    assert deep == [] and b.spent(DAY) == 0


def test_try_consume_allows_genuine_filing(st, clock):
    ev = _persist_one(st, clock, event_type="filing")
    b = DeepBudget(st, limit=5)
    assert isinstance(b.try_consume(day=DAY, event_id=ev.id, symbol="AAPL",
                                    reason="event_type:filing", corroborated=True), str)
    assert b.spent(DAY) == 1


def test_try_consume_refuses_unbound_event_type(st, clock):
    """An ``event_type:`` corroboration for an id with NO signed ingest binding is refused —
    an unbound event_type is not a spend trust root (fail closed)."""
    b = DeepBudget(st, limit=5)
    assert b.try_consume(day=DAY, event_id="never-ingested", symbol="AAPL",
                         reason="event_type:halt", corroborated=True) is False
    assert b.spent(DAY) == 0


# ---- signed_event_type read-side: binding + HMAC-tamper fail-closed --------------------
def test_signed_event_type_reads_binding_and_fails_closed_on_tamper(st, clock):
    ev = _persist_one(st, clock, event_type="filing")
    assert signed_event_type(st, ev.id) == "filing"
    assert signed_event_type(st, "unknown-id") is None
    # a keyless writer rewrites the signed binding to 'halt' -> breaks the entry HMAC ->
    # verify_entry False -> None (never a forged 'halt').
    with st.session_factory() as s, s.begin():
        e = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_INGEST_BATCH)).scalars().first()
        s.execute(update(AuditEntry).where(AuditEntry.id == e.id).values(
            payload={**e.payload, "event_types": {ev.id: "halt"}}))
    assert signed_event_type(st, ev.id) is None


# ---- SEC-21 x SEC-11 (W10-b): deep-budget delete-then-refill fails closed ---------------
def test_deep_budget_delete_then_refill_fails_closed(st):
    b = DeepBudget(st, limit=5)
    assert b.try_consume(day=DAY, event_id="e1", symbol="AAPL",
                         reason="quote_move:4.0", corroborated=True)
    assert b.try_consume(day=DAY, event_id="e2", symbol="AAPL",
                         reason="quote_move:4.0", corroborated=True)
    assert b.spent(DAY) == 2
    # keyless: delete a consumed EVENT_DEEP_ESCALATION to under-count, then try to heal by refill
    with st.session_factory() as s, s.begin():
        row = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION)
            .order_by(AuditEntry.seq.desc())).scalars().first()
        s.execute(delete(AuditEntry).where(AuditEntry.seq >= row.seq))
    st.audit.append("noise", actor="attacker")          # freed seq NON-REUSABLE -> gap, not re-green
    with pytest.raises(BudgetIntegrityError):
        b.spent(DAY)                                     # fail closed, never a silent undercount


# ---- G10 finding 2 (2026-07-08): budget fails CLOSED on a wired-but-None-latest store --
def test_budget_fails_closed_when_wired_store_has_no_out_of_domain_witness(st, tmp_path):
    """A WIRED anchor store whose latest() is None (a git-only / deleted same-domain witness)
    must fail CLOSED on the budget read-path, for parity with gate/gateway/mode. The old
    `anchor is not None` guard failed OPEN — skipping the consult so only the replay-defeatable
    verify_chain ran (budget was the sole outlier of the four consuming paths)."""
    st.anchor_store = GitAuditLogAnchorStore(
        GitCommitWitness(tmp_path / "audit-head.json", commit_fn=lambda *a: None))
    assert st.anchor_store.latest() is None              # git-only: no genuine out-of-domain witness
    b = DeepBudget(st, limit=5)
    with pytest.raises(BudgetIntegrityError):
        b.spent(DAY)
    with pytest.raises(BudgetIntegrityError):            # no phantom spend past a same-domain-only witness
        b.try_consume(day=DAY, event_id="e1", symbol="AAPL",
                      reason="quote_move:4.0", corroborated=True)


# ---- G10 finding 3 (2026-07-08): a forged calendar:% row cannot manufacture corroboration --
def test_forged_calendar_row_yields_no_corroboration(st, clock):
    """A keyless writer INSERTs an unsigned ``calendar:%`` row to manufacture calendar_proximity
    corroboration (the denial-of-wallet sibling of the closed event_type-flip hole). With the
    verifier, a calendar row corroborates ONLY if its event_type matches its signed binding."""
    target = _persist_one(st, clock, event_type="news", symbol="AAPL", article_id="a-1")
    forged_cal = MarketEvent(id="forged-cal", symbol="AAPL", source="x",
                             event_type="calendar:earnings", headline="h", summary=None,
                             raw_ref="x:1", content_hash="c" * 64, ts=NOW + timedelta(hours=10),
                             ingested_at=NOW, created_at=NOW)
    verifier = make_event_type_verifier(st)
    ok, reason = deterministic_urgency(_reread(st, target.id), [forged_cal], now=NOW,
                                       verify_event_type=verifier)
    assert ok is False and reason == "no_deterministic_corroboration"
    # WITHOUT the binding check the forged calendar row would (wrongly) corroborate.
    ok2, _ = deterministic_urgency(_reread(st, target.id), [forged_cal], now=NOW)
    assert ok2 is True


def test_genuine_calendar_row_still_corroborates(st, clock):
    target = _persist_one(st, clock, event_type="news", symbol="AAPL", article_id="a-1")
    cal = _persist_one(st, clock, event_type="calendar:earnings", symbol="AAPL", article_id="cal-1")
    ok, reason = deterministic_urgency(_reread(st, target.id), [_reread(st, cal.id)], now=NOW,
                                       verify_event_type=make_event_type_verifier(st))
    assert ok is True and reason == "calendar_proximity:AAPL"
