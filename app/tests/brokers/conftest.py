"""WP7 broker-test isolation + a fully-approved order scenario builder.

Every test runs against a tmp SQLite DB + a ``FrozenClock`` (deterministic, no network). The
SEC-04 HALT sentinel is redirected to a per-test tmp path (unset by default) so a HALT set by
one test never leaks into another and the arm-time check never touches real app-data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import OrderGateway
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode, Settings
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_ADJUDICATION, OrchestratorState
from schemas.models.approval_record import ApprovalRecord
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"wp7-brokers-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_halt(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    yield


@pytest.fixture
def clock():
    return FrozenClock(OPEN_UTC)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'wp7.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=clock)


def approved_risk_recheck(_positions):
    """Deterministic risk re-run that keeps the order approved."""
    return {"decision": "approved", "failed_checks": []}


@dataclass
class Scenario:
    st: OrchestratorState
    ladder: ModeLadder
    breaker: BreakerEngine
    gateway: OrderGateway
    order: object
    approval: ApprovalRecord
    risk_decision: RiskDecision
    tuple_hash: str
    nonce: str
    clock: FrozenClock

    def submit_kwargs(self, **over):
        kw = dict(
            approval=self.approval, risk_decision=self.risk_decision, ladder=self.ladder,
            breaker_engine=self.breaker, risk_recheck=approved_risk_recheck, positions={},
            token_nonce=self.nonce, producing_runtime="claude", clock=self.clock,
        )
        kw.update(over)
        return kw

    def review_kwargs(self, **over):
        kw = self.submit_kwargs(**over)
        kw.pop("token_nonce", None)
        return kw


def build_scenario(st, *, mode=Mode.review_live, broker=None, gateway=None,
                   qty="10", order_type="limit", limit_price="150", notional="1500",
                   consumed=False, risk_decision_state="approved",
                   mint=True) -> Scenario:
    """Assemble a fully-valid submit scenario. Individual tests mutate ONE field to prove each
    gate independently."""
    clk = st.clock
    ladder = ModeLadder(st)
    breaker = BreakerEngine(st)
    # walk the ladder up to the requested mode
    if mode in (Mode.paper, Mode.review_live, Mode.auto_veto, Mode.auto_full):
        ladder.set_mode(Mode.paper, actor="op")
        if mode != Mode.paper:
            ladder.set_mode(Mode.review_live, actor="op")

    rd = RiskDecision(
        id=new_id("rd"), proposal_id="p1", decision=risk_decision_state, checks={},
        failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
        human_required=False, evaluated_at=clk.now(), created_at=clk.now(),
    )
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": risk_decision_state})

    canon = canonical_order_tuple(symbol="AAPL", side="buy", qty=qty, order_type=order_type,
                                  limit_price=limit_price, tif="day")
    h = order_tuple_hash(canon)

    appr = ApprovalRecord(
        id=new_id("appr"), proposal_id="p1", risk_decision_id=rd.id, actor_user_id="lucas",
        user_decision="approve", surface="discord", order_tuple_hash=h,
        single_use_consumed=consumed, decided_at=clk.now(), created_at=clk.now(),
    )
    # The covering adjudication carries the FULL binding payload (as production
    # bridge/approval.record_decision does) — the gateway anchors trust in this signed payload.
    st.audit.record_state_change(appr, EVENT_ADJUDICATION, actor="lucas",
                                 payload={"decision": "approve", "proposal_id": "p1",
                                          "risk_decision_id": rd.id, "order_tuple_hash": h})

    order = create_order(st, proposal_id="p1", risk_decision_id=rd.id, approval_id=appr.id,
                         account="paper", symbol="AAPL", side="buy", qty=qty,
                         order_type=order_type, limit_price=limit_price, tif="day",
                         notional=notional, order_tuple_hash=h, actor="claude")

    gw = gateway or OrderGateway(broker or AlpacaPaperBroker(), st=st)   # SEC-03: pin the trust root
    nonce = "n1"
    if mint:
        gw.mint_submit_token(nonce=nonce, proposal_id="p1", risk_decision_id=rd.id,
                             order_tuple_hash=h)
    return Scenario(st, ladder, breaker, gw, order, appr, rd, h, nonce, clk)


@pytest.fixture
def scenario(st):
    return build_scenario(st)
