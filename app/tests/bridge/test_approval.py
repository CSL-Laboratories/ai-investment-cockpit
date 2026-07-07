"""WP6 — the approval gateway core (Inv 1/6; SEC-03/07/13)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from bridge.approval import (
    DEFAULT_MODAL_CONFIRM_NOTIONAL,
    Rejection,
    approval_validity,
    is_approval_valid,
    record_decision,
)
from brokers.capability import TokenRegistry
from brokers.order_tuple import order_tuple_hash
from config.settings import Mode
from schemas.models.approval_record import ApprovalRecord

from .conftest import FOREIGN, NOW, OPERATOR, make_order, make_risk


def _approval_rows(st):
    with st.session_factory() as s:
        return s.execute(select(ApprovalRecord)).scalars().all()


def _adjudication_count(st):
    from schemas.models.audit_entry import AuditEntry
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry)).scalars().all()
    return sum(1 for r in rows if (r.payload or {}).get("decision") is not None)


# ---- (i) foreign actor ----------------------------------------------------------------
def test_foreign_user_rejected_and_no_approval_record(st):
    reg = TokenRegistry()
    risk = make_risk()
    out = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=FOREIGN, user_decision="approve", surface="discord",
        interaction_id="i-1", token_registry=reg,
    )
    assert isinstance(out, Rejection)
    assert out.code == "unauthorized"
    assert _approval_rows(st) == []          # never mints a record that authorizes an order
    assert reg.live_count() == 0             # never mints a token
    # audited as a non-adjudication event (so it does not pollute gate metrics)
    from schemas.models.audit_entry import AuditEntry
    with st.session_factory() as s:
        events = [r.event_type for r in s.execute(select(AuditEntry)).scalars().all()]
    assert "unauthorized_actor" in events


# ---- (ii) idempotency -----------------------------------------------------------------
def test_replayed_interaction_id_is_idempotent(st):
    reg = TokenRegistry()
    risk = make_risk()
    kw = dict(proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
              actor_user_id=OPERATOR, user_decision="approve", surface="discord",
              interaction_id="i-42", token_registry=reg)
    first = record_decision(st, **kw)
    second = record_decision(st, **kw)
    assert not isinstance(first, Rejection)
    assert second.id == first.id             # SAME record
    assert len(_approval_rows(st)) == 1      # no second row
    assert _adjudication_count(st) == 1      # no second audit adjudication
    assert reg.live_count() == 1             # no second token


# ---- (iii) approve mints a bound token + audits the adjudication -----------------------
def test_approve_persists_bound_record_and_mints_token(st):
    reg = TokenRegistry()
    risk = make_risk()
    order = make_order()
    rec = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=order,
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-1", token_registry=reg,
    )
    assert rec.user_decision == "approve"
    assert rec.order_tuple_hash == order_tuple_hash(order)
    assert rec.risk_decision_id == risk.id
    assert rec.single_use_consumed is False
    assert reg.live_count() == 1
    # the token is bound to (proposal, risk_decision, order_tuple_hash)
    tok = reg.consume(nonce=f"subtok_{rec.id}", proposal_id="prop-1",
                      risk_decision_id=risk.id, order_tuple_hash=order_tuple_hash(order))
    assert tok.proposal_id == "prop-1"


def test_reject_records_but_mints_no_token(st):
    reg = TokenRegistry()
    risk = make_risk()
    rec = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=OPERATOR, user_decision="reject", surface="discord",
        interaction_id="i-1", token_registry=reg,
    )
    assert rec.user_decision == "reject"
    assert reg.live_count() == 0


# ---- (iv) modal double-confirm --------------------------------------------------------
def test_modal_required_above_threshold_live_mode(st):
    reg = TokenRegistry()
    risk = make_risk()
    big = DEFAULT_MODAL_CONFIRM_NOTIONAL + 1
    out = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-1", notional=big, mode=Mode.review_live, token_registry=reg,
    )
    assert isinstance(out, Rejection)
    assert out.code == "modal_required"
    assert reg.live_count() == 0
    # with the modal confirmed, it goes through
    ok = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-2", notional=big, mode=Mode.review_live, modal_confirmed=True,
        token_registry=reg,
    )
    assert not isinstance(ok, Rejection)
    assert reg.live_count() == 1


def test_modal_not_required_in_paper_mode(st):
    reg = TokenRegistry()
    risk = make_risk()
    big = DEFAULT_MODAL_CONFIRM_NOTIONAL + 1
    rec = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-1", notional=big, mode=Mode.paper, token_registry=reg,
    )
    assert not isinstance(rec, Rejection)      # paper is not a live broker; no modal gate
    assert reg.live_count() == 1


# ---- modify voids the prior live approval token ---------------------------------------
def test_modify_voids_prior_token(st):
    reg = TokenRegistry()
    risk = make_risk()
    record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(qty="10"),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-1", token_registry=reg,
    )
    assert reg.live_count() == 1
    # re-price (a modify) -> the old approval token is revoked
    record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(qty="20"),
        actor_user_id=OPERATOR, user_decision="modify", surface="discord",
        interaction_id="i-2", token_registry=reg,
    )
    assert reg.live_count() == 0


# ---- is_approval_valid ----------------------------------------------------------------
def test_valid_fresh_matching_approval():
    risk = make_risk()
    order = make_order()
    appr = ApprovalRecord(
        id="a1", proposal_id="prop-1", risk_decision_id=risk.id, actor_user_id=OPERATOR,
        user_decision="approve", surface="discord", order_tuple_hash=order_tuple_hash(order),
        single_use_consumed=False, decided_at=NOW, created_at=NOW,
    )
    assert is_approval_valid(appr, risk_decision=risk, resolved_order=order, now=NOW,
                             max_age_seconds=300)


def test_stale_quote_voids_approval():
    risk = make_risk(evaluated_at=NOW)
    order = make_order()
    appr = ApprovalRecord(
        id="a1", proposal_id="prop-1", risk_decision_id=risk.id, actor_user_id=OPERATOR,
        user_decision="approve", surface="discord", order_tuple_hash=order_tuple_hash(order),
        single_use_consumed=False, decided_at=NOW, created_at=NOW,
    )
    late = NOW + timedelta(seconds=301)
    assert not is_approval_valid(appr, risk_decision=risk, resolved_order=order, now=late,
                                 max_age_seconds=300)
    assert approval_validity(appr, risk_decision=risk, resolved_order=order, now=late,
                             max_age_seconds=300).reason == "stale"


def test_reprice_tuple_mismatch_voids_approval():
    risk = make_risk()
    approved = make_order(qty="10")
    appr = ApprovalRecord(
        id="a1", proposal_id="prop-1", risk_decision_id=risk.id, actor_user_id=OPERATOR,
        user_decision="approve", surface="discord", order_tuple_hash=order_tuple_hash(approved),
        single_use_consumed=False, decided_at=NOW, created_at=NOW,
    )
    repriced = make_order(qty="25")
    assert not is_approval_valid(appr, risk_decision=risk, resolved_order=repriced, now=NOW,
                                 max_age_seconds=300)
    assert approval_validity(appr, risk_decision=risk, resolved_order=repriced, now=NOW,
                             max_age_seconds=300).reason == "tuple_mismatch"


def test_consumed_approval_is_void():
    risk = make_risk()
    order = make_order()
    appr = ApprovalRecord(
        id="a1", proposal_id="prop-1", risk_decision_id=risk.id, actor_user_id=OPERATOR,
        user_decision="approve", surface="discord", order_tuple_hash=order_tuple_hash(order),
        single_use_consumed=True, decided_at=NOW, created_at=NOW,
    )
    assert not is_approval_valid(appr, risk_decision=risk, resolved_order=order, now=NOW,
                                 max_age_seconds=300)


def test_bad_decision_raises(st):
    from bridge.approval import ApprovalError
    with pytest.raises(ApprovalError):
        record_decision(
            st, proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order(),
            actor_user_id=OPERATOR, user_decision="explode", surface="discord",
            interaction_id="i-1",
        )
