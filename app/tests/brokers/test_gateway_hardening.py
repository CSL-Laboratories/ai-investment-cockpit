"""WP7 gateway hardening — the reproduced 4c Invariant-1 bypass, now CLOSED (SEC-03/SEC-12).

The order-path adversarial pass reproduced a keyless bypass: a caller holding ONLY the
``OrderGateway`` placed a never-approved order at the broker, because the gateway trusted the
caller-supplied ``approval``/``risk_decision`` objects BY VALUE and its approval check only
matched ``approval.id`` against *some* signed adjudication (never binding that signed entry to
THIS order). These regressions pin the fix: the gateway now re-loads the PERSISTED rows and
requires an HMAC-signed ``EVENT_ADJUDICATION`` whose payload binds the exact resolved order —
so a forged in-memory object (or a self-minted token) authorizes nothing.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from brokers.capability import CapabilityError
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from core.ids import new_id
from orchestrator.state import EVENT_ADJUDICATION
from schemas.models.approval_record import ApprovalRecord
from schemas.models.risk_decision import RiskDecision
from storage.repositories import Repo

from tests.brokers.conftest import approved_risk_recheck, build_scenario


class RecordingBroker:
    def __init__(self):
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return {"broker_order_id": "rec-1", "status": "accepted",
                "filled_qty": Decimal("0"), "avg_fill_price": None}


def _gme_order(st, sc, *, hash_override=None):
    """A never-approved GME order the attacker wants to push (reuses the legit ids)."""
    canon = canonical_order_tuple(symbol="GME", side="buy", qty="99999",
                                  order_type="market", tif="day")
    h = hash_override or order_tuple_hash(canon)
    return create_order(st, proposal_id="p1", risk_decision_id=sc.risk_decision.id,
                        approval_id=sc.approval.id, account="paper", symbol="GME", side="buy",
                        qty="99999", order_type="market", tif="day", notional=None,
                        order_tuple_hash=h, actor="attacker"), h


def _attack_kwargs(sc, *, approval, risk_decision, nonce):
    return dict(approval=approval, risk_decision=risk_decision, ladder=sc.ladder,
                breaker_engine=sc.breaker, risk_recheck=approved_risk_recheck, positions={},
                token_nonce=nonce, producing_runtime="claude", clock=sc.clock)


def test_forged_inmemory_approval_for_unapproved_order_refused(st):
    """The reproduced bypass: a caller forges an in-memory ApprovalRecord reusing a LEGIT
    approval's id but claiming to bind an unapproved order, self-mints a token, and submits.
    The gateway re-loads the persisted (AAPL) approval and refuses the GME order."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)                 # a legit APPROVED AAPL order exists
    gme_order, gme_hash = _gme_order(st, sc)

    forged = ApprovalRecord(
        id=sc.approval.id,                                 # reuse a real signed adjudication's row
        proposal_id="p1", risk_decision_id=sc.risk_decision.id, actor_user_id="attacker",
        user_decision="approve", surface="x", order_tuple_hash=gme_hash,   # LIE about the binding
        single_use_consumed=False, decided_at=sc.clock.now(), created_at=sc.clock.now(),
    )
    forged_rd = SimpleNamespace(id=sc.risk_decision.id, decision="approved")
    sc.gateway.mint_submit_token(nonce="evil", proposal_id="p1",
                                 risk_decision_id=sc.risk_decision.id, order_tuple_hash=gme_hash)

    out = sc.gateway.submit(gme_order, **_attack_kwargs(sc, approval=forged,
                                                        risk_decision=forged_rd, nonce="evil"))
    assert not out.ok and out.stage == "approval"
    assert broker.calls == 0                               # BYPASS CLOSED — never reached the broker


def test_unpersisted_forged_approval_refused(st):
    """A wholly fabricated approval id (never persisted) authorizes nothing."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    gme_order, gme_hash = _gme_order(st, sc)
    ghost = ApprovalRecord(
        id=new_id("appr"), proposal_id="p1", risk_decision_id=sc.risk_decision.id,
        actor_user_id="attacker", user_decision="approve", surface="x",
        order_tuple_hash=gme_hash, single_use_consumed=False,
        decided_at=sc.clock.now(), created_at=sc.clock.now(),
    )
    forged_rd = SimpleNamespace(id=sc.risk_decision.id, decision="approved")
    sc.gateway.mint_submit_token(nonce="evil", proposal_id="p1",
                                 risk_decision_id=sc.risk_decision.id, order_tuple_hash=gme_hash)
    out = sc.gateway.submit(gme_order, **_attack_kwargs(sc, approval=ghost,
                                                        risk_decision=forged_rd, nonce="evil"))
    assert not out.ok and out.stage == "approval" and broker.calls == 0


def test_forged_db_rows_without_signed_adjudication_refused(st):
    """A DB-writer forges an ApprovalRecord + RiskDecision row bound to the GME hash (mutable
    tables need no key), but CANNOT produce a valid signed EVENT_ADJUDICATION (no HMAC key).
    The chain stays valid (so the mode gate passes), and gate (e) refuses because no signed
    adjudication binds the approval — the unforgeable signature is the real root of trust."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    _, gme_hash = _gme_order(st, sc)

    frd = Repo(st.session_factory, RiskDecision).add(
        id=new_id("rd"), proposal_id="p1", decision="approved", checks={}, failed_checks=[],
        warnings=[], max_allowed_notional=Decimal("999999"), human_required=False,
        evaluated_at=sc.clock.now(), created_at=sc.clock.now())
    fappr = Repo(st.session_factory, ApprovalRecord).add(
        id=new_id("appr"), proposal_id="p1", risk_decision_id=frd.id, actor_user_id="attacker",
        user_decision="approve", surface="x", order_tuple_hash=gme_hash,
        single_use_consumed=False, decided_at=sc.clock.now(), created_at=sc.clock.now())
    # NO adjudication entry is forged — the attacker cannot sign one (SEC-12). The chain is intact.
    gme_order2 = create_order(st, proposal_id="p1", risk_decision_id=frd.id, approval_id=fappr.id,
                              account="paper", symbol="GME", side="buy", qty="99999",
                              order_type="market", tif="day", notional=None,
                              order_tuple_hash=gme_hash, actor="attacker")
    sc.gateway.mint_submit_token(nonce="evil", proposal_id="p1", risk_decision_id=frd.id,
                                 order_tuple_hash=gme_hash)
    out = sc.gateway.submit(
        gme_order2, **_attack_kwargs(sc, approval=SimpleNamespace(id=fappr.id),
                                     risk_decision=SimpleNamespace(id=frd.id, decision="approved"),
                                     nonce="evil"))
    assert not out.ok and out.stage == "approval" and broker.calls == 0
    assert "no HMAC-signed adjudication" in (out.reason or "")
    assert st.audit.verify_chain().ok                      # the chain was never broken


def test_self_minted_token_alone_grants_no_authority(st):
    """SEC-03 re-framed: a self-minted token proves nothing. With NO approval object that binds
    the order, the submit is refused at gate (e) before the token even matters."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    gme_order, gme_hash = _gme_order(st, sc)
    sc.gateway.mint_submit_token(nonce="evil", proposal_id="p1",
                                 risk_decision_id=sc.risk_decision.id, order_tuple_hash=gme_hash)
    # a totally forged approval that doesn't match anything persisted
    forged = SimpleNamespace(id="nope")
    out = sc.gateway.submit(gme_order, **_attack_kwargs(sc, approval=forged,
                                                        risk_decision=SimpleNamespace(id=sc.risk_decision.id, decision="approved"),
                                                        nonce="evil"))
    assert not out.ok and out.stage == "approval" and broker.calls == 0


def test_keyless_column_rewind_cannot_duplicate_execution(st):
    """Re-verification residual (SEC-12): a keyless DB writer resets the MUTABLE, non-signed
    columns the gate trusted by value — approval_record.single_use_consumed=0 AND
    order_row.state='approved' — to replay a genuine approval for a SECOND broker order. Both
    signals are now derived from the append-only SIGNED ledger (an unforgeable approval_consumed
    adjudication + submitted transition), so the replay is refused and verify_chain stays green."""
    from sqlalchemy import update

    from schemas.models.order import Order as OrderModel

    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    first = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert first.ok and broker.calls == 1

    # the keyless rewind of the two mutable columns (outside the HMAC ledger)
    with st.session_factory() as s, s.begin():
        s.execute(update(ApprovalRecord).where(ApprovalRecord.id == sc.approval.id)
                  .values(single_use_consumed=False))
        s.execute(update(OrderModel).where(OrderModel.id == sc.order.id).values(state="approved"))
    assert st.audit.verify_chain().ok                  # the rewind is invisible to the ledger

    # re-mint a token and replay -> the SIGNED ledger catches it; NO duplicate broker order
    sc.gateway.mint_submit_token(nonce="dup", proposal_id="p1",
                                 risk_decision_id=sc.risk_decision.id, order_tuple_hash=sc.tuple_hash)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(token_nonce="dup"))
    assert not out.ok and out.stage in ("approval", "order_state")
    assert broker.calls == 1                            # duplicate execution CLOSED


def test_second_valid_approval_same_order_refused_by_state_guard(st):
    """The approval-idempotency race yields TWO genuine approvals for ONE order. The first
    submits; the second (a fresh, un-consumed approval + its own token) is refused by the
    pre-broker order-state guard — no duplicate broker order."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    first = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert first.ok and broker.calls == 1

    # mint a SECOND genuine approval + token for the SAME order (as the idempotency race would).
    h = sc.tuple_hash
    appr2 = ApprovalRecord(
        id=new_id("appr"), proposal_id="p1", risk_decision_id=sc.risk_decision.id,
        actor_user_id="lucas", user_decision="approve", surface="discord", order_tuple_hash=h,
        single_use_consumed=False, decided_at=sc.clock.now(), created_at=sc.clock.now())
    st.audit.record_state_change(appr2, EVENT_ADJUDICATION, actor="lucas",
                                 payload={"decision": "approve", "proposal_id": "p1",
                                          "risk_decision_id": sc.risk_decision.id,
                                          "order_tuple_hash": h})
    sc.gateway.mint_submit_token(nonce="n2", proposal_id="p1",
                                 risk_decision_id=sc.risk_decision.id, order_tuple_hash=h)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(approval=appr2, token_nonce="n2"))
    assert not out.ok and out.stage == "order_state"
    assert broker.calls == 1                               # NO duplicate broker order


def test_live_capability_token_requires_a_covering_signed_approval(st):
    """SEC-03 (d): the LIVE-MCP capability mint refuses when no HMAC-signed adjudication covers this
    exact order — the token cannot even be CREATED for an unapproved order (defense-in-depth atop
    gate (e), which independently refuses at submit)."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)                 # legit AAPL approval + covering adjudication
    _, gme_hash = _gme_order(st, sc)

    # (1) a live token for an order NO signed adjudication approves -> refused at MINT (never created).
    with pytest.raises(CapabilityError, match="no covering signed"):
        sc.gateway.mint_live_submit_token(
            st, approval_id=sc.approval.id, nonce="live-evil", proposal_id="p1",
            risk_decision_id=sc.risk_decision.id, order_tuple_hash=gme_hash)
    assert sc.gateway.live_token_count() == 1              # only the scenario's paper token exists

    # (2) the LEGIT binding (the approved AAPL order) mints fine.
    tok = sc.gateway.mint_live_submit_token(
        st, approval_id=sc.approval.id, nonce="live-ok", proposal_id="p1",
        risk_decision_id=sc.risk_decision.id, order_tuple_hash=sc.tuple_hash)
    assert tok.nonce == "live-ok"


def test_agentic_place_refuses_when_the_tracer_is_not_active(st):
    """SEC-03: the live Robinhood-Agentic path refuses unless the always-on order tracer guards the
    broker (the live order authority must never run untraced); with it installed, the gated path
    reaches the broker exactly once."""
    from brokers.order_tracer import OrderAuthorityViolation, install
    from brokers.robinhood_mcp import agentic_place

    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker, mint=False)
    sc.gateway.mint_live_submit_token(
        st, approval_id=sc.approval.id, nonce=sc.nonce, proposal_id="p1",
        risk_decision_id=sc.risk_decision.id, order_tuple_hash=sc.tuple_hash)

    # (1) tracer NOT installed -> the live path refuses before the broker.
    with pytest.raises(OrderAuthorityViolation):
        agentic_place(sc.gateway, sc.order, **sc.submit_kwargs())
    assert broker.calls == 0

    # (2) tracer installed -> the gated live path reaches the broker.
    install(broker, state=st)
    out = agentic_place(sc.gateway, sc.order, **sc.submit_kwargs())
    assert out.ok and broker.calls == 1


def test_pinned_gateway_rejects_a_caller_substituted_trust_root(st):
    """SEC-03 (G11 round-2): gate (e)'s root of trust is the SIGNED chain, and its HMAC key / DB are
    PINNED at construction. A caller holding only the gateway cannot swap in an attacker-keyed chain
    by threading a forged ladder — the same never-approved order that would fire under an
    attacker-keyed state is refused before the broker."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)                 # gateway pinned to `st` (conftest st=st)
    # a foreign ladder whose .st is a DIFFERENT (attacker-keyed) state object
    foreign = SimpleNamespace(st=SimpleNamespace(name="attacker-keyed-state"))
    with pytest.raises(CapabilityError, match="pinned trust root"):
        sc.gateway.submit(sc.order, **sc.submit_kwargs(ladder=foreign))
    assert broker.calls == 0
