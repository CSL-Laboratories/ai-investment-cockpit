"""WP8 Owner C / G8 — SEC-07 residual chain-anchoring of the pending-exposure cap.

Enforces the SEC-07 "Sharpened" closure (docs/security-hardening-backlog.md SEC-07): the
documented residual was that ``OrderGateway._pending_ok`` summed the MUTABLE
``order_row.state`` + ``order_row.notional`` columns, so a keyless DB-writer could rewind a
row's state to a terminal (or shrink its notional) to free cap headroom and slip an over-cap
still-APPROVED order past gate (h). The pending set and each order's notional are now derived
from HMAC-verified ``EVENT_ORDER_TRANSITION`` ledger entries (whose signed payloads carry
``notional`` + ``symbol`` as of WP8), preferring max(chain, row) — understatement is the
attack. Hermetic: brokers conftest (FrozenClock, tmp SQLite, monkeypatched COCKPIT_HALT_PATH).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from brokers.gateway import OrderGateway
from brokers.order_state_machine import IllegalOrderTransition, create_order, transition
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from core.ids import new_id
from orchestrator.state import EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.risk_decision import RiskDecision
from storage.audit import audit_meta

from tests.brokers.conftest import build_scenario

# order1 (1500) + order2 (1500) + order3 (1500) = 4500 > CAP, but without order2's 1500 the
# total (3000) fits — so the rewind of order2 is exactly what would free headroom for order3.
CAP = Decimal("3500")


class RecordingBroker:
    def __init__(self):
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return {"broker_order_id": f"rec-{self.calls}", "status": "accepted",
                "filled_qty": Decimal("0"), "avg_fill_price": None}


def _approved_sibling(st, *, symbol="MSFT", qty="10", order_type="limit", limit_price="150",
                      notional="1500"):
    """A second order, legitimately walked to 'approved' THROUGH the FSM — so its open-ness
    and notional are on the signed chain (birth + approved transitions)."""
    o = create_order(st, proposal_id=new_id("p"), risk_decision_id=new_id("rd"),
                     account="paper", symbol=symbol, side="buy", qty=qty,
                     order_type=order_type, limit_price=limit_price, tif="day",
                     notional=notional, order_tuple_hash=new_id("h"), actor="claude")
    transition(st, o, "approved", actor="claude", reason="approved")
    return o


def _fresh_submittable(st, sc, *, proposal="p3", symbol="NVDA", qty="10", limit_price="150",
                       notional="1500", nonce="n3"):
    """A THIRD fully-approved order against the scenario's gateway: its own persisted
    RiskDecision, ApprovalRecord + signed binding adjudication, order row and minted token —
    so every gate (a)-(g) passes and only the SEC-07 pending-exposure gate (h) is in play."""
    clk = sc.clock
    rd = RiskDecision(id=new_id("rd"), proposal_id=proposal, decision="approved", checks={},
                      failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
                      human_required=False, evaluated_at=clk.now(), created_at=clk.now())
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": "approved"})
    canon = canonical_order_tuple(symbol=symbol, side="buy", qty=qty, order_type="limit",
                                  limit_price=limit_price, tif="day")
    h = order_tuple_hash(canon)
    appr = ApprovalRecord(id=new_id("appr"), proposal_id=proposal, risk_decision_id=rd.id,
                          actor_user_id="lucas", user_decision="approve", surface="discord",
                          order_tuple_hash=h, single_use_consumed=False,
                          decided_at=clk.now(), created_at=clk.now())
    st.audit.record_state_change(appr, EVENT_ADJUDICATION, actor="lucas",
                                 payload={"decision": "approve", "proposal_id": proposal,
                                          "risk_decision_id": rd.id, "order_tuple_hash": h})
    order = create_order(st, proposal_id=proposal, risk_decision_id=rd.id, approval_id=appr.id,
                         account="paper", symbol=symbol, side="buy", qty=qty,
                         order_type="limit", limit_price=limit_price, tif="day",
                         notional=notional, order_tuple_hash=h, actor="claude")
    sc.gateway.mint_submit_token(nonce=nonce, proposal_id=proposal, risk_decision_id=rd.id,
                                 order_tuple_hash=h)
    return order, appr, rd


# =======================================================================================
# (1) the residual attack, CLOSED: a keyless state+notional rewind frees NO headroom
# =======================================================================================
def test_keyless_state_and_notional_rewind_cannot_free_cap_headroom(st):
    """The documented SEC-07 residual: rewind order2's mutable state to 'cancelled' AND its
    notional to 0 (outside the HMAC ledger — verify_chain stays green). The chain-anchored
    ``_pending_ok`` still counts order2's SIGNED values (approved, 1500), so the over-cap
    third order is refused at gate (h) and never reaches the broker."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500
    o2 = _approved_sibling(st)                                               # order2 open: 1500

    # the keyless rewind of the two mutable columns (the attack the map flagged).
    with st.session_factory() as s, s.begin():
        s.execute(update(Order).where(Order.id == o2.id)
                  .values(state="cancelled", notional=Decimal("0")))
    assert st.audit.verify_chain().ok                # the rewind is invisible to the ledger
    with st.session_factory() as s:                  # the mutable columns really are rewound
        row2 = s.get(Order, o2.id)
        assert row2.state == "cancelled" and Decimal(str(row2.notional)) == Decimal("0")

    o3, appr3, rd3 = _fresh_submittable(st, sc)
    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "pending_exposure"
    assert broker.calls == 0                         # the over-cap order never reached the broker

    # the chain-derived sum counted order2 at its SIGNED 1500, not the rewound 0/terminal.
    ok, total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and total == Decimal("4500") and cap == CAP


# =======================================================================================
# (1b) delete-then-heal: erasing a sibling's chain tail cannot make it vanish from the sum
# =======================================================================================
def test_delete_then_heal_still_counts_db_open_sibling(st):
    """The reproduced SEC-07 'delete-then-heal' bypass: a keyless writer DELETEs order2's
    EVENT_ORDER_TRANSITION entries (its whole tail), so order2 VANISHES from a presence-derived
    chain sum — while its ``Order`` row remains 'approved' in the DB. Pre-W10 a later
    order-creation append REUSED the freed seqs and re-bumped the watermark, so the chain
    re-greened and the gate-(h) completeness reconcile was the catch. W10's MONOTONIC,
    NON-REUSABLE seq closes it one layer earlier: the refill can no longer reuse the freed seqs,
    so the delete-then-heal lands as a seq GAP that ``verify_chain`` rejects — the mode oracle
    drops to read_only and the over-cap third order is REFUSED at the mode gate (b), never
    reaching the broker. (The gate-(h) reconcile remains as defense-in-depth for the NON-deletion
    tampers — column rewind / row delete / notional laundering — covered by the other tests.)"""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500
    o2 = _approved_sibling(st)                                               # order2 open: 1500

    # keyless attack: DELETE order2's signed EVENT_ORDER_TRANSITION tail (birth + approved), so
    # it disappears from the chain entirely; the mutable Order row stays 'approved' in the DB.
    with st.session_factory() as s, s.begin():
        victim_ids = [e.id for e in s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
        ).scalars().all() if (e.refs or {}).get("row_id") == o2.id]
        s.execute(delete(AuditEntry).where(AuditEntry.id.in_(victim_ids)))
    with st.session_factory() as s:                  # the row really is chain-erased but DB-open
        assert s.get(Order, o2.id).state == "approved"
        remaining = [e for e in s.execute(select(AuditEntry)).scalars().all()
                     if e.event_type == EVENT_ORDER_TRANSITION
                     and (e.refs or {}).get("row_id") == o2.id]
        assert remaining == []

    # the third order-creation appends new entries — W10's monotonic, NON-REUSABLE seq allocates
    # ABOVE the high-water, so the freed tail seqs are NOT reissued and the chain does NOT
    # re-green (the 'heal' fails).
    o3, appr3, rd3 = _fresh_submittable(st, sc)
    assert not st.audit.verify_chain().ok            # W10: delete-then-heal caught as a seq gap

    # _pending_ok fails closed on the un-verifiable chain BEFORE the exposure math (total 0).
    ok, total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and total == Decimal("0") and cap == CAP

    # the tampered chain drops the mode oracle to read_only, so the submit is refused at the mode
    # gate (b) — earlier than, and strictly stronger than, the SEC-07 exposure gate (h).
    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "mode"
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (1c) round-2: delete-tail + notional=0 (sibling stays db-open) fails CLOSED
# =======================================================================================
def test_delete_tail_and_zero_notional_db_open_sibling_fails_closed(st):
    """The reproduced round-2 bypass composed from BOTH mutations at once: a keyless writer
    DELETEs order2's whole EVENT_ORDER_TRANSITION tail (so it drops from chain-open AND its
    signed chain_notional vanishes) AND UPDATEs order2.notional=0 while keeping state='approved'
    (so it stays in the DB-open set with ``_order_notional(row) == 0``). Under the old
    max(chain, row) rule order2 then contributes amounts=[0] -> 0, and the mutable
    order_row.notional column is the SOLE (and zeroed) notional source — exactly the trust
    SEC-07 forbids: order1(1500) + order2(0) + order3(1500) = 3000 <= cap would slip the
    over-cap sibling past gate (h). The fail-closed rule treats a missing verified
    chain_notional for an open member as TAMPER, so ``_pending_ok`` returns ok=False. Post-W10
    the DELETE of the signed tail is itself caught at ``verify_chain`` (the monotonic non-reusable
    seq turns the refill into a gap), so the mode oracle drops to read_only and the submit is
    refused at the mode gate (b) — a stronger, earlier fail-closed than gate (h); the broker is
    never called either way."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500
    o2 = _approved_sibling(st)                                               # order2 open: 1500

    # composed keyless attack: DELETE order2's signed transition tail AND zero its row notional,
    # leaving state='approved' (so it stays DB-open with the mutable column as the sole source).
    with st.session_factory() as s, s.begin():
        victim_ids = [e.id for e in s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
        ).scalars().all() if (e.refs or {}).get("row_id") == o2.id]
        s.execute(delete(AuditEntry).where(AuditEntry.id.in_(victim_ids)))
        s.execute(update(Order).where(Order.id == o2.id).values(notional=Decimal("0")))
    with st.session_factory() as s:                  # chain-erased, DB-open, notional zeroed
        row2 = s.get(Order, o2.id)
        assert row2.state == "approved" and Decimal(str(row2.notional)) == Decimal("0")
        remaining = [e for e in s.execute(select(AuditEntry)).scalars().all()
                     if e.event_type == EVENT_ORDER_TRANSITION
                     and (e.refs or {}).get("row_id") == o2.id]
        assert remaining == []

    o3, appr3, rd3 = _fresh_submittable(st, sc)
    assert not st.audit.verify_chain().ok            # W10: the delete-then-heal no longer re-greens

    # the un-verifiable chain fails _pending_ok closed (and drops the mode oracle to read_only).
    ok, _total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and cap == CAP

    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "mode"        # W10: refused at the mode gate before gate (h)
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (1d) round-3 bypass A: the CURRENT order's own zeroed notional cannot understate it
# =======================================================================================
def test_current_order_zeroed_notional_counts_signed_chain_notional(st):
    """The reproduced round-3 bypass A: the CURRENT order under evaluation was sized from its
    MUTABLE ``order_row.notional`` (``_order_notional`` trusts that column whenever it is non-
    None, INCLUDING a keyless-writer-set 0). ``notional`` is NOT in ``order_tuple_hash``, so
    zeroing it breaks neither gate (e) nor ``verify_chain``. Real exposure qty*limit_price =
    10*250 = 2500 exceeds the 2000 cap, but the zeroed column made the current order contribute
    0 -> the over-cap order reached the broker. Sizing the current order from its own HMAC-
    verified chain notional (2500, signed on its birth transition) refuses it at gate (h)
    without ever calling the broker."""
    broker = RecordingBroker()
    cap = Decimal("2000")
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=cap),
                        broker=broker)
    # the current over-cap order: real qty*limit_price = 10*250 = 2500, signed on its birth
    # transition; then a keyless writer zeroes the mutable notional column (chain untouched).
    o3, appr3, rd3 = _fresh_submittable(st, sc, qty="10", limit_price="250", notional="2500")
    with st.session_factory() as s, s.begin():
        s.execute(update(Order).where(Order.id == o3.id).values(notional=Decimal("0")))
    assert st.audit.verify_chain().ok                # the zeroing is invisible to the ledger
    # the gateway reads the order row from the DB, so the object it evaluates carries the
    # keyless-writer-set notional=0 (order_tuple_hash — hence gate (e) — is untouched).
    with st.session_factory() as s:
        o3 = s.get(Order, o3.id)
        s.expunge(o3)
    assert Decimal(str(o3.notional)) == Decimal("0")   # the object the gateway sees is zeroed

    # the current order is counted at its SIGNED 2500, not the rewound 0.
    ok, total, out_cap = sc.gateway._pending_ok(st, o3)
    assert not ok and total == Decimal("2500") and out_cap == cap

    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "pending_exposure"
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (1e) round-3 bypass B: delete-tail + set-terminal (ROW REMAINS) is still detected
# =======================================================================================
def test_delete_tail_and_set_terminal_row_remains_fails_closed(st):
    """The reproduced round-3 bypass B: a keyless writer removes a sibling from BOTH open sets at
    once — DELETE order2's whole EVENT_ORDER_TRANSITION tail (gone from chain-open) AND UPDATE
    order2.state to a TERMINAL (gone from db-open) — while the ``Order`` ROW REMAINS. The prior
    union loop only visited members already in {chain-open} u {db-open}, so order2 was in NEITHER
    set: the loop never visited it and the chain_n-is-None tamper flag (which only guards members
    already in the union) never fired. The over-cap third order then slipped past gate (h)
    (1500+1500 = 3000 <= cap). The completeness reconcile visits EVERY Order row: order2 is a row
    with no verified transition -> TAMPER -> fail closed, so the third order is REFUSED at gate
    (h) and never reaches the broker."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500
    o2 = _approved_sibling(st)                                               # order2 open: 1500

    # keyless attack: DELETE order2's signed transition tail AND rewind its state to a terminal,
    # leaving the Order ROW present (so order2 is in NEITHER the chain-open NOR the db-open set).
    with st.session_factory() as s, s.begin():
        victim_ids = [e.id for e in s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
        ).scalars().all() if (e.refs or {}).get("row_id") == o2.id]
        s.execute(delete(AuditEntry).where(AuditEntry.id.in_(victim_ids)))
        s.execute(update(Order).where(Order.id == o2.id).values(state="cancelled"))
    with st.session_factory() as s:                  # row present, chain-erased, db-terminal
        row2 = s.get(Order, o2.id)
        assert row2 is not None and row2.state == "cancelled"
        remaining = [e for e in s.execute(select(AuditEntry)).scalars().all()
                     if e.event_type == EVENT_ORDER_TRANSITION
                     and (e.refs or {}).get("row_id") == o2.id]
        assert remaining == []

    o3, appr3, rd3 = _fresh_submittable(st, sc)
    assert not st.audit.verify_chain().ok            # W10: the delete-then-heal no longer re-greens

    # order2 is in NEITHER open set; post-W10 the DELETE of its signed tail already fails
    # verify_chain, so _pending_ok fails closed (the row-completeness reconcile remains the catch
    # for the NON-deletion tampers).
    ok, _total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and cap == CAP

    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "mode"        # W10: refused at the mode gate before gate (h)
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (1f) round-4 gap 1: partial-tail delete (chain regressed to 'proposed', DB still 'approved')
# =======================================================================================
def test_partial_tail_delete_chain_regressed_db_open_fails_closed(st):
    """The reproduced round-4 gap 1: a keyless writer deletes ONLY order2's LATER signed
    transition (its 'approved' step) and keeps its 'proposed' birth, so chain_latest[order2]
    REGRESSES to 'proposed' while the DB row stays 'approved'. The old ``state not in open_states:
    continue`` then SKIPPED a genuinely-open sibling -> total understated (order1 1500 + order3
    1500 = 3000 <= cap) and the over-cap third order reached the broker. Post-W10 the partial
    DELETE is itself caught at ``verify_chain`` (the non-reusable seq turns the refill into a gap),
    dropping the mode oracle to read_only so the third order is REFUSED at the mode gate (b) —
    earlier than gate (h); the completeness reconcile remains for the NON-deletion regressions."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500
    o2 = _approved_sibling(st)                                               # order2 open: 1500

    # keyless attack: DELETE ONLY order2's 'approved' transition (its LATER signed step), keeping
    # the 'proposed' birth -> chain_latest[order2]='proposed' while the DB row stays 'approved'.
    with st.session_factory() as s, s.begin():
        rows = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
        ).scalars().all()
        victim_ids = [e.id for e in rows if (e.refs or {}).get("row_id") == o2.id
                      and (e.payload or {}).get("to") == "approved"]
        s.execute(delete(AuditEntry).where(AuditEntry.id.in_(victim_ids)))
    with st.session_factory() as s:                  # chain regressed to 'proposed', DB 'approved'
        assert s.get(Order, o2.id).state == "approved"
        o2_entries = [e for e in s.execute(select(AuditEntry).order_by(AuditEntry.seq))
                      .scalars().all()
                      if e.event_type == EVENT_ORDER_TRANSITION
                      and (e.refs or {}).get("row_id") == o2.id]
        assert [e.payload.get("to") for e in o2_entries] == ["proposed"]   # only the birth survives

    # the third order-creation appends new entries; W10's non-reusable seq does NOT reissue the
    # deleted 'approved' step's freed seq, so the chain does NOT re-green.
    o3, appr3, rd3 = _fresh_submittable(st, sc)
    assert not st.audit.verify_chain().ok    # W10: the partial-tail delete is caught as a seq gap

    # order2's chain regressed below open; post-W10 the DELETE already fails verify_chain, so
    # _pending_ok fails closed (the db-open reconcile remains for the NON-deletion regressions).
    ok, _total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and cap == CAP

    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "mode"        # W10: refused at the mode gate before gate (h)
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (1g) round-4 gap 2: deleted Order ROW (chain intact + open) is still counted
# =======================================================================================
def test_deleted_order_row_chain_intact_fails_closed(st):
    """The reproduced round-4 gap 2: a keyless writer DELETEs an open sibling's ``Order`` ROW while
    leaving its signed chain intact (chain_latest='approved'). The prior reconcile iterated ONLY
    ``select(Order)`` rows, so a chain-open sibling whose ROW was deleted was NEVER counted ->
    total understated (order1 1500 + order3 1500 = 3000 <= cap) and the over-cap third order
    reached the broker. The reconcile now flags a chain-open row_id absent from db_ids as TAMPER
    (and re-includes it via the chain-open set), so the third order is REFUSED at gate (h)."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500
    o2 = _approved_sibling(st)                                               # order2 open: 1500

    # keyless attack: DELETE order2's Order ROW; its signed EVENT_ORDER_TRANSITION chain is intact.
    with st.session_factory() as s, s.begin():
        s.execute(delete(Order).where(Order.id == o2.id))
    with st.session_factory() as s:                  # row gone, chain intact + still 'approved'
        assert s.get(Order, o2.id) is None
        o2_entries = [e for e in s.execute(select(AuditEntry).order_by(AuditEntry.seq))
                      .scalars().all()
                      if e.event_type == EVENT_ORDER_TRANSITION
                      and (e.refs or {}).get("row_id") == o2.id]
        assert [e.payload.get("to") for e in o2_entries] == ["proposed", "approved"]
    assert st.audit.verify_chain().ok                # deleting a domain row leaves the ledger GREEN

    o3, appr3, rd3 = _fresh_submittable(st, sc)

    # order2 is chain-open but its Order row is gone -> TAMPER -> fail closed.
    ok, _total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and cap == CAP

    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "pending_exposure"
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (1h) round-5 bypass 1: a laundered notional=0 on a LATER signed transition cannot shrink it
# =======================================================================================
def test_laundered_zero_notional_on_later_transition_counts_max(st):
    """BUG-125 refuses to sign a zeroed mutable notional over its signed predecessor.

    Refusal preserves the entire order/audit/watermark state. The existing conservative
    pending-exposure cap still counts the approved sibling's signed 1500 after refusal.
    """
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500

    # The approved sibling's real notional is already signed before the keyless mutation.
    o2 = _approved_sibling(st)
    with st.session_factory() as s, s.begin():
        s.execute(update(Order).where(Order.id == o2.id).values(notional=Decimal("0")))
    with st.session_factory() as s:                  # routine flow re-loads the row (notional=0)
        o2r = s.get(Order, o2.id)
        s.expunge(o2r)
    def snapshot():
        with st.session_factory() as session:
            rows = [
                [{column.name: getattr(row, column.name) for column in model.__table__.columns}
                 for row in session.scalars(select(model).order_by(model.id))]
                for model in (Order, AuditEntry)
            ]
            return rows, dict(session.execute(select(audit_meta)).all())

    caller_before = {column.name: getattr(o2r, column.name) for column in Order.__table__.columns}
    before = snapshot()
    with pytest.raises(IllegalOrderTransition):
        transition(st, o2r, "submitted", actor="claude", reason="submitted")
    assert snapshot() == before
    assert {column.name: getattr(o2r, column.name)
            for column in Order.__table__.columns} == caller_before
    assert st.audit.verify_chain().ok
    with st.session_factory() as s:
        o2_entries = [e for e in
                      s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
                      if e.event_type == EVENT_ORDER_TRANSITION
                      and (e.refs or {}).get("row_id") == o2.id]
    assert [(e.payload["to"], Decimal(e.payload["notional"])) for e in o2_entries] == [
        ("proposed", Decimal("1500")), ("approved", Decimal("1500"))]

    o3, appr3, rd3 = _fresh_submittable(st, sc)
    # The unchanged pending-exposure guard still counts order2 at its signed 1500.
    ok, total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and total == Decimal("4500") and cap == CAP

    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "pending_exposure"
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (1i) round-5 bypass 2: a db TERMINAL state with no signed terminal transition fails CLOSED
# =======================================================================================
def test_terminal_rewind_without_signed_transition_fails_closed(st):
    """The reproduced round-5 bypass 2 (terminal rewind without a signed transition): a keyless
    writer DELETEs order2's signed 'approved' step (chain_latest regresses to 'proposed', so it
    leaves the chain-open set) AND bare-UPDATEs its db state to a TERMINAL 'cancelled' (no signed
    cancel transition, so it also leaves the db-open set) -> order2 dropped from BOTH open sets
    while its birth survives (so the chain_ids-miss / no-history reconcile does NOT fire). The
    over-cap third order then slipped past gate (h) (1500+1500 = 3000 <= cap). The new rule — any
    db row in a TERMINAL state MUST be backed by a verified transition whose 'to' is that terminal —
    flags order2 as TAMPER (its db 'cancelled' has no signed 'cancelled' 'to') -> fail closed.
    Post-W10 the DELETE of the signed 'approved' step is itself caught at ``verify_chain`` (the
    non-reusable seq turns the refill into a gap), so the mode oracle drops to read_only and the
    third order is REFUSED at the mode gate (b) — a stronger, earlier fail-closed than gate (h)."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")  # order1 open: 1500
    o2 = _approved_sibling(st)                                               # order2 open: 1500

    # keyless attack: DELETE order2's signed 'approved' step (chain regresses to 'proposed') AND
    # bare-UPDATE its db state to a TERMINAL 'cancelled' (no signed cancel transition).
    with st.session_factory() as s, s.begin():
        rows = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_ORDER_TRANSITION)).scalars().all()
        victim = [e.id for e in rows if (e.refs or {}).get("row_id") == o2.id
                  and (e.payload or {}).get("to") == "approved"]
        s.execute(delete(AuditEntry).where(AuditEntry.id.in_(victim)))
        s.execute(update(Order).where(Order.id == o2.id).values(state="cancelled"))
    with st.session_factory() as s:                  # chain regressed to birth, db terminal
        row2 = s.get(Order, o2.id)
        assert row2.state == "cancelled"
        o2_tos = [e.payload.get("to") for e in
                  s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
                  if e.event_type == EVENT_ORDER_TRANSITION
                  and (e.refs or {}).get("row_id") == o2.id]
        assert o2_tos == ["proposed"]                # only the birth survives; no signed cancel

    o3, appr3, rd3 = _fresh_submittable(st, sc)       # heal attempt: new appends can't reuse the freed seq
    assert not st.audit.verify_chain().ok    # W10: the deleted 'approved' step is caught as a seq gap

    # order2's db state is terminal but no signed transition backs it; post-W10 the DELETE already
    # fails verify_chain, so _pending_ok fails closed (the terminal-backing reconcile remains for
    # the NON-deletion terminal rewrites).
    ok, _total, cap = sc.gateway._pending_ok(st, o3)
    assert not ok and cap == CAP

    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert not out.ok and out.stage == "mode"        # W10: refused at the mode gate before gate (h)
    assert broker.calls == 0                         # the over-cap order never reached the broker


# =======================================================================================
# (2) a LEGITIMATE signed cancel (via the FSM) DOES free headroom
# =======================================================================================
def test_legitimate_signed_cancel_frees_headroom(st):
    """The dual of (1): cancelling order2 through ``transition`` writes a signed 'cancelled'
    entry, so its latest verified chain state is terminal — headroom is freed and the third
    order submits cleanly under the same cap."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    transition(st, sc.order, "approved", actor="claude", reason="approved")
    o2 = _approved_sibling(st)
    transition(st, o2, "cancelled", actor="lucas", reason="operator cancel")   # SIGNED cancel

    o3, appr3, rd3 = _fresh_submittable(st, sc)
    out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                   token_nonce="n3"))
    assert out.ok and out.stage == "submitted"       # 1500 + 0 + 1500 = 3000 <= 3500
    assert broker.calls == 1
    assert st.audit.verify_chain().ok


# =======================================================================================
# (3) an un-priced market order still contributes 0 to the pending sum
# =======================================================================================
def test_unpriced_market_order_contributes_zero(st):
    """An approved market order with no notional and no limit_price carries "0" on its signed
    payloads and folds 0 into the chain-derived pending sum (same behavior as the pre-WP8
    row-derived sum — the risk engine's own pending-exposure check still gates it)."""
    broker = RecordingBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=CAP),
                        broker=broker)
    om = _approved_sibling(st, symbol="GME", order_type="market", limit_price=None,
                           notional=None)

    # its signed payloads carry the "0" notional (the _order_notional rule, JSON-native str).
    with st.session_factory() as s:
        entries = [e for e in s.execute(select(AuditEntry).order_by(AuditEntry.seq))
                   .scalars().all()
                   if e.event_type == EVENT_ORDER_TRANSITION
                   and (e.refs or {}).get("row_id") == om.id]
    assert entries and all(e.payload["notional"] == "0" for e in entries)
    assert all(st.audit.verify_entry(e) for e in entries)

    # chain-open but 0 notional: total = just the current order's 1500.
    ok, total, cap = sc.gateway._pending_ok(st, sc.order)
    assert ok and total == Decimal("1500") and cap == CAP


# =======================================================================================
# (4) every signed transition payload now carries notional + symbol
# =======================================================================================
def test_transition_payloads_carry_notional_and_symbol(st):
    """The enabler fix: the birth payload (create_order) and every transition() payload carry
    ``notional`` (str) + ``symbol`` — JSON-native, HMAC-covered, chain-verifiable. A notional
    of qty*limit_price is derived when the row has no explicit notional."""
    o = create_order(st, proposal_id="p1", risk_decision_id="rd1", account="paper",
                     symbol="AAPL", side="buy", qty="10", order_type="limit",
                     limit_price="150", tif="day", notional="1500",
                     order_tuple_hash="h1", actor="claude")
    transition(st, o, "approved", actor="claude", reason="approved")

    # derived-notional variant: NO explicit notional -> qty*limit_price on the payload.
    o2 = create_order(st, proposal_id="p2", risk_decision_id="rd2", account="paper",
                      symbol="MSFT", side="buy", qty="4", order_type="limit",
                      limit_price="200", tif="day", notional=None,
                      order_tuple_hash="h2", actor="claude")

    with st.session_factory() as s:
        entries = [e for e in s.execute(select(AuditEntry).order_by(AuditEntry.seq))
                   .scalars().all() if e.event_type == EVENT_ORDER_TRANSITION]
    by_row = {}
    for e in entries:
        assert st.audit.verify_entry(e)              # each payload is HMAC-covered
        by_row.setdefault((e.refs or {}).get("row_id"), []).append(e.payload)

    assert len(by_row[o.id]) == 2                    # birth + approved
    for p in by_row[o.id]:
        assert p["symbol"] == "AAPL"
        assert isinstance(p["notional"], str) and Decimal(p["notional"]) == Decimal("1500")
    (p2,) = by_row[o2.id]
    assert p2["symbol"] == "MSFT"
    assert isinstance(p2["notional"], str) and Decimal(p2["notional"]) == Decimal("800")
    assert st.audit.verify_chain().ok


def test_order_notional_uses_the_larger_of_notional_and_qty_times_limit():
    order = Order(notional="1", qty="10", limit_price="30", order_type="limit")
    assert OrderGateway._order_notional(order) == Decimal("300")


def test_pending_sibling_uses_qty_times_limit_when_the_chain_is_smaller(st):
    """A birth signed at $1 must not hide a sibling whose limit is 10 x $300."""
    broker = RecordingBroker()
    cap = Decimal("2000")
    sc = build_scenario(
        st, gateway=OrderGateway(broker, pending_exposure_cap=cap), broker=broker,
    )
    _approved_sibling(st, qty="10", limit_price="300", notional="1")
    current, _appr, _rd = _fresh_submittable(
        st, sc, qty="1", limit_price="100", notional="100",
    )
    ok, total, out_cap = sc.gateway._pending_ok(st, current)
    assert ok is False
    assert total == Decimal("3100")
    assert out_cap == cap


def test_pending_market_sibling_uses_the_frozen_exposure(st):
    broker = RecordingBroker()
    cap = Decimal("2000")
    sc = build_scenario(
        st, gateway=OrderGateway(broker, pending_exposure_cap=cap), broker=broker,
    )
    sibling = _approved_sibling(
        st, qty="10", order_type="market", limit_price=None, notional="1",
    )
    st.audit.append(
        "paper_submission",
        actor="paper",
        refs={"order_id": sibling.id},
        payload={"exposure": "2500"},
    )
    current, _appr, _rd = _fresh_submittable(
        st, sc, qty="1", limit_price="100", notional="100",
    )
    ok, total, out_cap = sc.gateway._pending_ok(st, current)
    assert ok is False
    assert total == Decimal("2600")
    assert out_cap == cap
