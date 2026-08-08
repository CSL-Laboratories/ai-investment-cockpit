"""WP9 / G9 — A FORGED INTERACTION IS REJECTED (the dedicated forged-interaction gate).

docs/build-scaffold-ultracode.md WP9 + docs/ultracode-build-playbook.md §3 (G9 gate). This is
the standalone, dedicated version of the forged-interaction guarantee the WP9 spec lists — the
sibling ``test_paper_replay.test_g9_5*`` exercises the same guarantee inline as a negative
control, and this file mirrors its harness idioms (same ``record_decision`` -> ``OrderGateway``
-> ``FakePaperBroker`` drive, same chain-derived assertion style).

The guarantee: only a GENUINE, allowlisted human adjudication that is PERSISTED on the
HMAC-signed chain can authorize an order to the broker. Everything else — a foreign
(non-allowlisted) actor, a fabricated in-memory ``ApprovalRecord`` the caller never persisted
(even paired with a self-minted capability token), a replayed Discord ``interaction_id`` — is
refused, and the broker is never called.

CAUSALITY (WP9 non-negotiable — a forged-rejection test that is vacuously green proves nothing):
this file drives the REAL ``bridge.approval.record_decision`` + the REAL ``system.gateway`` +
the REAL ``ModeLadder`` / ``BreakerEngine`` / ``FakePaperBroker``, and asserts ONLY on persisted
rows, minted/consumed capability tokens, HMAC-verified audit entries, and the broker's own
submit list — never a hand-set flag. A single GENUINE positive control submits first (proving
the whole path is live and the refusals below are caused by forgery, not by a broken path);
after it, EVERY forgery is refused and ``FakePaperBroker.submits`` never grows past that one.
Each block names the exact real guard it pins:

  * case 1 (positive control) — ``operators.is_authorized`` returns True for the allowlisted
    operator; ``record_decision`` persists the ``ApprovalRecord`` + mints the single-use token;
    the gateway submits. If this failed, the negatives would be vacuous.
  * case 2 (foreign actor) — ``record_decision`` gate (i) (``operators.is_authorized``): a
    non-allowlisted actor is a ``Rejection(unauthorized)`` that persists NO row, mints NO token,
    and audits ``EVENT_UNAUTHORIZED``; then the gateway gate (e) (``_load_row`` returns None for
    the unpersisted approval) refuses the fabricated order at stage 'approval'.
  * case 3 (forged in-memory approval + self-minted token) — the gateway gate (e): a well-formed
    ``ApprovalRecord`` object that was NEVER persisted, even with a genuinely-live self-minted
    capability token that matches its binding, is refused at stage 'approval' (the signed chain,
    not the caller-supplied object or the token, is the authority). Deleting gate (e) makes this
    submit -> the final ``submits == 1`` assertion FAILS.
  * case 4 (replayed interaction_id) — ``record_decision`` gate (ii) idempotency: a second call
    with the SAME ``interaction_id`` returns the SAME record, minting NO second token and writing
    NO second ``EVENT_ADJUDICATION``.

Hermetic per conftest: tmp SQLite, autouse session in-memory keyring, ``FrozenClock`` at a live
NYSE session, no network, isolated ``COCKPIT_HALT_PATH``. ``st.audit.verify_chain().ok`` stays
True throughout.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode
from core.ids import new_id
from orchestrator.state import EVENT_ADJUDICATION
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.risk_decision import RiskDecision

from bridge.approval import EVENT_UNAUTHORIZED, Rejection, record_decision

from tests.system.harness import approved_risk_recheck

OPERATOR = "123456789012345678"  # genuine allowlisted Discord operator (positive control)
INTRUDER = "mallory"      # a foreign Discord user id that is NOT on the allowlist


@pytest.fixture(autouse=True)
def _allowlist_operator(monkeypatch):
    """Put ONLY ``OPERATOR`` on the allowlist so the REAL approval path mints for it and refuses
    every other actor. Actor auth reads ``COCKPIT_OPERATOR_IDS`` directly (operators.py); the
    monkeypatch auto-reverts, keeping the test hermetic. Mirrors test_paper_replay's fixture."""
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)
    yield


# ---------------------------------------------------------------------------------------
# chain-derived helpers (verified entries only — mirror test_paper_replay._verified style)
# ---------------------------------------------------------------------------------------
def _verified_adjudications(st, approval_id: str) -> list[AuditEntry]:
    """Every HMAC-VERIFIED ``EVENT_ADJUDICATION`` entry whose refs.row_id binds ``approval_id``."""
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
        ).scalars().all()
    return [e for e in entries
            if (e.refs or {}).get("row_id") == approval_id and st.audit.verify_entry(e)]


def _verified_unauthorized(st, interaction_id: str) -> list[AuditEntry]:
    """Every HMAC-VERIFIED ``EVENT_UNAUTHORIZED`` entry for a given foreign ``interaction_id``."""
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_UNAUTHORIZED)
        ).scalars().all()
    return [e for e in entries
            if (e.payload or {}).get("interaction_id") == interaction_id
            and st.audit.verify_entry(e)]


def _approval_by_interaction(st, interaction_id: str) -> ApprovalRecord | None:
    with st.session_factory() as s:
        return s.execute(
            select(ApprovalRecord).where(ApprovalRecord.interaction_id == interaction_id)
        ).scalars().first()


def _approval_by_id(st, approval_id: str) -> ApprovalRecord | None:
    with st.session_factory() as s:
        return s.get(ApprovalRecord, approval_id)


def _to_review_live(system, *, clock) -> None:
    """Walk the REAL ladder up to review_live (order authority) via the public ``set_mode``."""
    cur = system.ladder.current()
    if cur is Mode.read_only:
        system.ladder.set_mode(Mode.paper, actor=OPERATOR, clock=clock)
        system.ladder.set_mode(Mode.review_live, actor=OPERATOR, clock=clock)
    elif cur is Mode.paper:
        system.ladder.set_mode(Mode.review_live, actor=OPERATOR, clock=clock)


def _persist_approved_risk(st, *, proposal_id: str, clock) -> RiskDecision:
    """Persist an APPROVED RiskDecision (+ its covering audit entry) the way production does."""
    rd = RiskDecision(
        id=new_id("rd"), proposal_id=proposal_id, decision="approved", checks={},
        failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
        human_required=False, evaluated_at=clock.now(), created_at=clock.now(),
    )
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": "approved", "proposal_id": proposal_id})
    return rd


# The single resolved order shape reused across every case (notional 150 < the modal-confirm
# threshold, so a live approve needs no modal double-confirm; well under the exposure cap).
def _resolved(symbol: str = "AAPL") -> dict:
    return {"symbol": symbol, "side": "buy", "qty": "1", "order_type": "limit",
            "limit_price": "150", "tif": "day"}


# =======================================================================================
# G9 — a forged interaction is rejected; only a genuine persisted adjudication authorizes
# =======================================================================================
def test_g9_forged_interaction_is_rejected(system):
    st = system.st
    clk = system.clock
    submits = system.broker.submits
    _to_review_live(system, clock=clk)   # order authority (review_live is a LIVE mode)

    # -----------------------------------------------------------------------------------
    # CASE 1 — POSITIVE / NEGATIVE CONTROL (causality): a GENUINE allowlisted operator's
    # adjudication persists an ApprovalRecord, mints a single-use token, and the REAL gateway
    # SUBMITS. PINS: operators.is_authorized(OPERATOR) is True + record_decision mint + the full
    # gateway happy path. Without this the refusals below would prove nothing.
    # -----------------------------------------------------------------------------------
    tokens_before = system.token_registry.live_count()
    rd1 = _persist_approved_risk(st, proposal_id="genuine", clock=clk)
    resolved1 = _resolved()
    hash1 = order_tuple_hash(canonical_order_tuple(**resolved1))

    appr1 = record_decision(
        st, proposal_id="genuine", risk_decision=rd1, resolved_order=resolved1,
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="ix-genuine", clock=clk, modal_confirmed=True, notional="150",
        token_registry=system.token_registry, mode=system.ladder.current(),
    )
    # a genuine approve returns a PERSISTED ApprovalRecord (never a Rejection)...
    assert isinstance(appr1, ApprovalRecord)
    assert _approval_by_id(st, appr1.id) is not None, "the genuine approval must be persisted"
    # ...and mints EXACTLY one single-use submit token bound to this approval.
    nonce1 = f"subtok_{appr1.id}"
    assert system.token_registry.is_live(nonce1), "a genuine approve must mint a submit token"
    assert system.token_registry.live_count() == tokens_before + 1

    order1 = create_order(
        st, proposal_id="genuine", risk_decision_id=rd1.id, approval_id=appr1.id,
        account="paper", symbol="AAPL", side="buy", qty="1", order_type="limit", tif="day",
        order_tuple_hash=hash1, limit_price="150", notional="150", actor="claude", clock=clk,
    )
    system.anchor(clock=clk)   # head >= anchor so the gateway's SEC-11 consult passes (happy path)
    outcome1 = system.gateway.place_equity_order(
        order1, approval=appr1, risk_decision=rd1, ladder=system.ladder,
        breaker_engine=system.breaker, risk_recheck=approved_risk_recheck, positions={},
        token_nonce=nonce1, producing_runtime="claude", clock=clk,
    )
    assert outcome1.ok is True and outcome1.stage == "submitted"
    assert outcome1.broker_order_id is not None
    assert len(submits) == 1, "the genuine order MUST reach the broker exactly once"
    assert not system.token_registry.is_live(nonce1), "the token must be consumed (single-use)"
    assert st.audit.verify_chain().ok

    # -----------------------------------------------------------------------------------
    # CASE 2 — FOREIGN ACTOR: a non-allowlisted actor_user_id is rejected by record_decision
    # gate (i); then a fabricated (unpersisted) approval driven through the REAL gateway is
    # refused by gate (e). PINS: operators.is_authorized(INTRUDER) is False + gateway gate (e).
    # -----------------------------------------------------------------------------------
    n_before = len(submits)                          # == 1
    tokens_before = system.token_registry.live_count()
    rd2 = _persist_approved_risk(st, proposal_id="foreign", clock=clk)
    resolved2 = _resolved()
    hash2 = order_tuple_hash(canonical_order_tuple(**resolved2))

    foreign = record_decision(
        st, proposal_id="foreign", risk_decision=rd2, resolved_order=resolved2,
        actor_user_id=INTRUDER, user_decision="approve", surface="discord",
        interaction_id="ix-foreign", clock=clk, modal_confirmed=True, notional="150",
        token_registry=system.token_registry, mode=system.ladder.current(),
    )
    # gate (i): a foreign actor is refused — no ApprovalRecord, no token, an audited attempt.
    assert isinstance(foreign, Rejection) and foreign.code == "unauthorized"
    assert _approval_by_interaction(st, "ix-foreign") is None, \
        "a foreign actor must NEVER persist an ApprovalRecord"
    assert system.token_registry.live_count() == tokens_before, \
        "a foreign actor must NEVER mint a submit token"
    unauth = _verified_unauthorized(st, "ix-foreign")
    assert len(unauth) == 1, "the foreign attempt must record exactly one EVENT_UNAUTHORIZED entry"
    assert (unauth[0].payload or {}).get("proposal_id") == "foreign"

    # a caller now fabricates an approval (id never in the DB) and drives the REAL gateway.
    forged_id2 = new_id("appr")
    order2 = create_order(
        st, proposal_id="foreign", risk_decision_id=rd2.id, approval_id=forged_id2,
        account="paper", symbol="AAPL", side="buy", qty="1", order_type="limit", tif="day",
        order_tuple_hash=hash2, limit_price="150", notional="150", actor="claude", clock=clk,
    )
    forged_appr2 = ApprovalRecord(   # a real ApprovalRecord OBJECT — but never persisted
        id=forged_id2, proposal_id="foreign", risk_decision_id=rd2.id,
        actor_user_id=INTRUDER, user_decision="approve", surface="discord",
        order_tuple_hash=hash2, single_use_consumed=False,
        decided_at=clk.now(), created_at=clk.now(),
    )
    system.anchor(clock=clk)
    outcome2 = system.gateway.place_equity_order(
        order2, approval=forged_appr2, risk_decision=rd2, ladder=system.ladder,
        breaker_engine=system.breaker, risk_recheck=approved_risk_recheck, positions={},
        token_nonce=f"subtok_{forged_id2}", producing_runtime="claude", clock=clk,
    )
    # gate (e): _load_row(ApprovalRecord, forged_id2) is None (never persisted) -> refuse.
    assert outcome2.ok is False and outcome2.stage == "approval"
    assert len(submits) == n_before, "a foreign/unpersisted approval MUST NEVER reach the broker"
    assert st.audit.verify_chain().ok

    # -----------------------------------------------------------------------------------
    # CASE 3 — FORGED IN-MEMORY ApprovalRecord + SELF-MINTED token: a well-formed approval
    # object the caller never persisted, paired with a genuinely-live capability token it minted
    # itself for the exact binding, is STILL refused. PINS: gateway gate (e) is the authority —
    # the signed chain, not the caller-supplied object nor the token. Deleting gate (e) (trusting
    # the caller's approval) would submit here and break the final submits == 1 assertion.
    # -----------------------------------------------------------------------------------
    n_before = len(submits)                          # == 1
    rd3 = _persist_approved_risk(st, proposal_id="self-mint", clock=clk)
    resolved3 = _resolved()
    hash3 = order_tuple_hash(canonical_order_tuple(**resolved3))

    forged_appr3 = ApprovalRecord(   # NEVER persisted (no record_state_change)
        id=new_id("appr"), proposal_id="self-mint", risk_decision_id=rd3.id,
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        order_tuple_hash=hash3, single_use_consumed=False,
        decided_at=clk.now(), created_at=clk.now(),
    )
    nonce3 = f"subtok_{forged_appr3.id}"
    # self-mint a matching, single-use capability token for the forged approval's binding.
    system.gateway.mint_submit_token(nonce=nonce3, proposal_id="self-mint",
                                     risk_decision_id=rd3.id, order_tuple_hash=hash3)
    assert system.token_registry.is_live(nonce3), "the self-minted token is genuinely live"

    order3 = create_order(
        st, proposal_id="self-mint", risk_decision_id=rd3.id, approval_id=forged_appr3.id,
        account="paper", symbol="AAPL", side="buy", qty="1", order_type="limit", tif="day",
        order_tuple_hash=hash3, limit_price="150", notional="150", actor="claude", clock=clk,
    )
    system.anchor(clock=clk)
    outcome3 = system.gateway.place_equity_order(
        order3, approval=forged_appr3, risk_decision=rd3, ladder=system.ladder,
        breaker_engine=system.breaker, risk_recheck=approved_risk_recheck, positions={},
        token_nonce=nonce3, producing_runtime="claude", clock=clk,
    )
    # gate (e) refuses BEFORE the token gate (g): the forged approval is not on the signed chain.
    assert outcome3.ok is False and outcome3.stage == "approval"
    assert len(submits) == n_before, "a self-minted token cannot authorize a forged approval"
    assert system.token_registry.is_live(nonce3), \
        "the token was NOT consumed — gate (e) refused before gate (g), proving it is not the authority"
    assert st.audit.verify_chain().ok

    # -----------------------------------------------------------------------------------
    # CASE 4 — REPLAYED interaction_id: a second record_decision with the SAME interaction_id
    # returns the SAME ApprovalRecord and causes NO second effect. PINS: record_decision gate
    # (ii) idempotency (no second token, no second EVENT_ADJUDICATION). Never touches the broker.
    # -----------------------------------------------------------------------------------
    rd4 = _persist_approved_risk(st, proposal_id="replay", clock=clk)
    resolved4 = _resolved()

    appr_a = record_decision(
        st, proposal_id="replay", risk_decision=rd4, resolved_order=resolved4,
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="ix-replay", clock=clk, modal_confirmed=True, notional="150",
        token_registry=system.token_registry, mode=system.ladder.current(),
    )
    assert isinstance(appr_a, ApprovalRecord)
    nonce4 = f"subtok_{appr_a.id}"
    assert system.token_registry.is_live(nonce4)
    tokens_after_first = system.token_registry.live_count()
    assert len(_verified_adjudications(st, appr_a.id)) == 1, "the first approve writes one adjudication"

    appr_b = record_decision(
        st, proposal_id="replay", risk_decision=rd4, resolved_order=resolved4,
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="ix-replay", clock=clk, modal_confirmed=True, notional="150",
        token_registry=system.token_registry, mode=system.ladder.current(),
    )
    # idempotent: same record id, no second token minted, no second adjudication entry.
    assert isinstance(appr_b, ApprovalRecord) and appr_b.id == appr_a.id
    assert system.token_registry.live_count() == tokens_after_first, \
        "a replayed interaction_id must NOT mint a second token"
    assert len(_verified_adjudications(st, appr_a.id)) == 1, \
        "a replayed interaction_id must NOT write a second EVENT_ADJUDICATION"

    # -----------------------------------------------------------------------------------
    # FINAL cross-cutting assertions: across the whole module state, ONLY the genuine positive
    # control ever reached the broker, and the HMAC chain verified throughout.
    # -----------------------------------------------------------------------------------
    assert len(submits) == 1, "only the genuine positive-control order reached the broker"
    assert st.audit.verify_chain().ok
