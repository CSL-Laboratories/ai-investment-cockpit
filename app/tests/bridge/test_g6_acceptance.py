"""WP6 — G6 ACCEPTANCE gate (Inv 1/2/6; SEC-01/03/07/11/23).

Adversarial acceptance suite for the human/auto approval bridge. Authored against the REAL
modules (no mock of the security check under test), on in-memory SQLite + a FrozenClock via
the shared ``st`` fixture in ``conftest.py``. Each acceptance criterion gets one clear test
that exercises the actual gate and asserts on persisted state, minted tokens, and the
tamper-evident audit chain — never on an internal we could have faked.

Criteria covered (one section each):
  1. foreign/forged-user interaction is REJECTED — no ApprovalRecord, no token, audited.
  2. a replayed ``interaction_id`` (Discord retry) is IDEMPOTENT — one record, one token.
  3. approval expiry on a STALE quote VOIDS execution (the WP7 read-side gate refuses).
  4. veto-window RACE — a veto at T-1s wins; execution at T+0 re-checks mode+breakers
     ATOMICALLY (a live breaker trip / a mode drop at the fire instant cancels the auto-exec).
  5. modal DOUBLE-CONFIRM above the notional threshold for a live order.
  6. SEC-23 — an UNDELIVERED proposal does NOT auto-execute on silence.
  7. SEC-11 — the anchor store is MONOTONIC and ``post_head_anchor`` records the VERIFIED head.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from bridge.anchor import (
    AnchorError,
    AnchorRewind,
    InMemoryAnchorStore,
    post_head_anchor,
)
from bridge.approval import (
    DEFAULT_MODAL_CONFIRM_NOTIONAL,
    Rejection,
    is_approval_valid,
    record_decision,
)
from bridge.veto import AutoVetoCoordinator
from brokers.capability import TokenRegistry
from config.settings import Mode
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.breaker_state import BreakerState

from .conftest import FOREIGN, NOW, OPERATOR, make_order, make_risk


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
class _Mono:
    """Injectable monotonic clock for the veto window (matches bridge.veto contract)."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class _FakeLadder:
    """A ModeLadder stand-in whose ``current()`` we control, so a resolve() re-check reads a
    known mode. NOTE: it is only injected where the MODE is not the thing under test; the
    breaker/veto races below still fire the REAL BreakerEngine + real token registry."""

    def __init__(self, mode: Mode) -> None:
        self._mode = mode

    def current(self) -> Mode:
        return self._mode


def _approval_rows(st) -> list[ApprovalRecord]:
    with st.session_factory() as s:
        return s.execute(select(ApprovalRecord)).scalars().all()


def _audit_event_types(st) -> list[str]:
    with st.session_factory() as s:
        return [r.event_type for r in s.execute(select(AuditEntry)).scalars().all()]


def _audit_decisions(st) -> list[str]:
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry)).scalars().all()
    return [(r.payload or {}).get("decision") for r in rows if (r.payload or {}).get("decision")]


# ======================================================================================
# 1. foreign / forged-user interaction is REJECTED
# ======================================================================================
def test_foreign_user_interaction_rejected_no_record_no_token(st):
    """A Discord user id NOT on the operator allowlist cannot adjudicate: record_decision
    returns a Rejection, persists NO ApprovalRecord (nothing that could authorize an order),
    mints NO token, and audits the attempt as a non-adjudication ``unauthorized_actor`` event
    (so it never pollutes the gate's decision metrics)."""
    reg = TokenRegistry()
    out = record_decision(
        st, proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order(),
        actor_user_id=FOREIGN, user_decision="approve", surface="discord",
        interaction_id="i-forged", token_registry=reg,
    )
    assert isinstance(out, Rejection)
    assert out.code == "unauthorized"
    # Invariant 1: a foreign actor NEVER mints order authority.
    assert _approval_rows(st) == []
    assert reg.live_count() == 0
    # audited, but NOT as an adjudication (no 'decision' payload key -> gate ignores it).
    assert "unauthorized_actor" in _audit_event_types(st)
    assert _audit_decisions(st) == []


# ======================================================================================
# 2. replayed interaction id is IDEMPOTENT
# ======================================================================================
def test_replayed_interaction_id_is_idempotent(st):
    """Discord retries deliver the same interaction twice. The second call MUST return the
    SAME ApprovalRecord and cause NO second effect: one row, one adjudication audit entry,
    one live token (never a duplicate order authority)."""
    reg = TokenRegistry()
    risk = make_risk()
    kw = dict(
        proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-retry", token_registry=reg,
    )
    first = record_decision(st, **kw)
    second = record_decision(st, **kw)

    assert not isinstance(first, Rejection)
    assert not isinstance(second, Rejection)
    assert second.id == first.id                 # SAME record, not a new one
    assert len(_approval_rows(st)) == 1          # no duplicate state row
    assert _audit_decisions(st) == ["approve"]   # exactly one adjudication on the chain
    assert reg.live_count() == 1                 # no second token minted


# ======================================================================================
# 3. approval expiry on a STALE quote VOIDS execution
# ======================================================================================
def test_stale_quote_voids_approval_execution(st):
    """The WP7 submit path calls is_approval_valid immediately before consuming the token.
    A genuine, freshly-minted approve token is nonetheless VOID once the underlying quote /
    RiskDecision has aged past ``max_age_seconds`` — silence + a stale price must not execute.
    Control: the same approval is valid while fresh."""
    reg = TokenRegistry()
    risk = make_risk(evaluated_at=NOW)             # quote evaluated at NOW
    order = make_order()
    appr = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=order,
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-1", token_registry=reg,
    )
    assert not isinstance(appr, Rejection)

    # fresh: still inside the window -> valid.
    assert is_approval_valid(appr, risk_decision=risk, resolved_order=order,
                             now=NOW, max_age_seconds=300) is True

    # stale: one second past the window -> execution is VOIDED.
    late = NOW + timedelta(seconds=301)
    assert is_approval_valid(appr, risk_decision=risk, resolved_order=order,
                             now=late, max_age_seconds=300) is False


# ======================================================================================
# 4. veto-window RACE + atomic re-check of mode+breakers at T+0
# ======================================================================================
def test_veto_at_t_minus_1s_wins_over_execution_at_t0(st):
    """The core race: a veto landing at T-1s must WIN over the auto-execution firing at T+0.
    Both take ``st.lock``, so resolve() re-reads the ticket and sees the veto -> no token is
    minted, and the veto is on the audit chain as an adjudication (so the gate counts it)."""
    mono = _Mono(0.0)
    reg = TokenRegistry()
    coord = AutoVetoCoordinator(st, reg, window_seconds=300.0, monotonic=mono,
                                mode_ladder=_FakeLadder(Mode.auto_veto))
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    coord.confirm_delivery("prop-1")             # window clock starts at mono=0

    mono.t = 299.0
    out = coord.veto("prop-1", actor_user_id=OPERATOR)   # veto at T-1s
    assert out.vetoed is True

    mono.t = 300.0
    res = coord.resolve("prop-1")                # execution attempt at T+0
    assert res.executed is False
    assert res.reason == "vetoed"
    assert reg.live_count() == 0                 # NO submit token minted
    assert "veto" in _audit_decisions(st)


def test_execution_at_t0_rechecks_breaker_atomically(st):
    """At the window edge, resolve() re-checks the SYNCHRONOUS injection breaker BEFORE minting
    (SEC-01). A live anomaly detected at the fire instant cancels the auto-exec, records a real
    BreakerState trip, and mints NO token — the injection breaker fires inside the order path,
    not after it."""
    mono = _Mono(0.0)
    reg = TokenRegistry()
    # real BreakerEngine (default): the trip must actually record + drop autonomy.
    coord = AutoVetoCoordinator(st, reg, window_seconds=300.0, monotonic=mono,
                                mode_ladder=_FakeLadder(Mode.auto_veto))
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    coord.confirm_delivery("prop-1")
    mono.t = 300.0

    # an injection/anomaly is present at the fire instant.
    res = coord.resolve("prop-1", injection_context=object(),
                        injection_predicate=lambda c: True)
    assert res.executed is False
    assert res.reason == "breaker"
    assert reg.live_count() == 0
    with st.session_factory() as s:
        trips = s.execute(select(BreakerState)).scalars().all()
    assert any(b.tripped for b in trips)         # the injection breaker recorded a real trip


def test_execution_at_t0_rechecks_mode_atomically(st):
    """resolve() re-reads the LIVE mode at the fire instant: a drop out of ``auto_veto`` between
    schedule and fire cancels the auto-exec. Uses the REAL ModeLadder reading the signed chain,
    so this exercises the actual mode oracle, not a stand-in."""
    mono = _Mono(0.0)
    reg = TokenRegistry()
    # arm auto_veto directly on the signed chain (payload-only mode_change, no ModeState row).
    st.audit.append("mode_change", payload={"kind": "arm", "from": "review_live",
                                            "to": "auto_veto"})
    coord = AutoVetoCoordinator(st, reg, window_seconds=300.0, monotonic=mono)  # REAL ladder
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    coord.confirm_delivery("prop-1")

    # the operator drops autonomy back to review_live while the window is running.
    st.audit.append("mode_change", payload={"kind": "manual", "from": "auto_veto",
                                            "to": "review_live"})
    mono.t = 300.0
    res = coord.resolve("prop-1")
    assert res.executed is False
    assert res.reason == "mode_changed"
    assert reg.live_count() == 0


# ======================================================================================
# 5. modal DOUBLE-CONFIRM above the notional threshold
# ======================================================================================
def test_modal_double_confirm_required_above_threshold(st):
    """A LIVE-mode order at/above the modal-confirm notional threshold is REFUSED unless the
    operator supplies the second (modal) confirmation. Without it: a Rejection, no token. With
    ``modal_confirmed=True``: the approve persists and mints the token."""
    reg = TokenRegistry()
    risk = make_risk()
    big = DEFAULT_MODAL_CONFIRM_NOTIONAL + 1     # strictly above the threshold

    refused = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-1", notional=big, mode=Mode.review_live, token_registry=reg,
    )
    assert isinstance(refused, Rejection)
    assert refused.code == "modal_required"
    assert reg.live_count() == 0                 # unconfirmed large live order mints nothing

    confirmed = record_decision(
        st, proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="i-2", notional=big, mode=Mode.review_live, modal_confirmed=True,
        token_registry=reg,
    )
    assert not isinstance(confirmed, Rejection)
    assert confirmed.user_decision == "approve"
    assert reg.live_count() == 1                 # the double-confirmed order is authorized


# ======================================================================================
# 6. SEC-23 — an UNDELIVERED proposal does NOT auto-execute on silence
# ======================================================================================
def test_undelivered_proposal_never_auto_executes_on_silence(st):
    """SEC-23: silence is not consent. If delivery to the operator was never CONFIRMED, the
    veto window MUST NOT fire — a proposal the operator never saw is UNDELIVERED, not
    un-vetoed. Even long past the window, resolve() refuses and mints no token."""
    mono = _Mono(0.0)
    reg = TokenRegistry()
    coord = AutoVetoCoordinator(st, reg, window_seconds=300.0, monotonic=mono,
                                mode_ladder=_FakeLadder(Mode.auto_veto))
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    # NOTE: no confirm_delivery — the operator never received it.
    mono.t = 1_000_000.0                         # arbitrarily far past any window
    res = coord.resolve("prop-1")
    assert res.executed is False
    assert res.reason == "undelivered"
    assert reg.live_count() == 0
    assert "approve" not in _audit_decisions(st)  # never recorded an auto-approval


# ======================================================================================
# 7. SEC-11 — anchor store is MONOTONIC + post_head_anchor records the VERIFIED head
# ======================================================================================
def test_anchor_store_is_monotonic():
    """The external-witness anchor is append-only + monotonic: it accepts advancing heads,
    idempotently re-accepts the same head, and REFUSES a rewind (lower seq) or a fork (same
    seq / different hash) — exactly the replayable rollback it exists to catch."""
    store = InMemoryAnchorStore()
    assert store.latest() is None
    store.append(1, "h1")
    assert store.latest() == (1, "h1")
    store.append(2, "h2")
    assert store.latest() == (2, "h2")
    store.append(2, "h2")                        # idempotent re-anchor of the same head
    assert store.latest() == (2, "h2")
    with pytest.raises(AnchorRewind):
        store.append(1, "hx")                    # rewind refused
    with pytest.raises(AnchorRewind):
        store.append(2, "forked")                # fork refused
    with pytest.raises(AnchorError):
        store.append(3, "")                      # empty head refused


def test_post_head_anchor_records_verified_head(st):
    """post_head_anchor VERIFIES the chain, then publishes the verified ``(seq, head_hash)`` to
    the external witness. The anchored head must equal the store's ``latest()`` and the chain
    must verify AGAINST that anchor (proving the seq/hash pair is the genuine verified head)."""
    st.anchor_store = InMemoryAnchorStore()
    st.audit.append("mode_change", payload={"kind": "manual", "from": "read_only", "to": "paper"})
    st.audit.append("drawdown_mark", payload={"drawdown": "0.01"})

    seq, head_hash = post_head_anchor(st)
    assert st.anchor_store.latest() == (seq, head_hash)
    assert st.audit.verify_against_anchor(seq, head_hash).ok is True
