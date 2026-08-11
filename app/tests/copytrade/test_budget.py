"""RW-17 §2 — the budget rails, and the one property that makes them worth having.

The rails replace the human as the bound on a day's damage, so the tests that matter are not
"does the arithmetic add up" but:

  * the spend is re-derived from the SIGNED chain, so a RESTART does not refill it;
  * a HUMAN approval on the same chain does not consume the ROBOT's budget (and vice versa);
  * an unreadable/unverifiable chain means EXHAUSTED, never zero-spent;
  * a lane SELL only frees an open-position slot once the signed order chain shows the order
    actually reached the broker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from brokers import gateway
from config.settings import Settings
from copytrade import budget as budget_module
from copytrade.budget import (
    ALL_RAILS,
    AUTOEXEC_LANE,
    RAIL_CHAIN_UNREADABLE,
    RAIL_DAILY_NOTIONAL,
    RAIL_OPEN_POSITIONS,
    RAIL_SYMBOL_NOTIONAL,
    RAIL_TRADES_PER_DAY,
    AutoExecBudget,
    BudgetState,
    budget_state,
    check,
)
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator.state import EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from schemas.models.approval_record import ApprovalRecord
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"rw17-budget-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
DAY = OPEN_UTC.date()


@pytest.fixture
def clock():
    return FrozenClock(OPEN_UTC)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw17-budget.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(make_session_factory(engine), hmac_key=KEY,
                                   settings=Settings(), clock=clock)


def lane_approval(st, *, symbol, side="buy", notional="50", order_id=None,
                  proposal_id=None) -> str:
    """Write the exact signed adjudication shape ``copytrade/autoexec.py`` mints."""
    order_id = order_id or new_id("ord")
    proposal_id = proposal_id or new_id("prop")
    st.audit.append(
        EVENT_ADJUDICATION, actor="copytrade_autoexec",
        refs={"proposal_id": proposal_id},
        payload={"decision": "approve", "proposal_id": proposal_id,
                 "risk_decision_id": "rd-x", "order_tuple_hash": "h",
                 "lane": AUTOEXEC_LANE, "auto": True, "order_id": order_id,
                 "symbol": symbol, "side": side, "notional": str(notional)},
    )
    return order_id


def mark_submitted(st, order_id: str, *, symbol: str = "AAPL", side: str = "sell") -> None:
    """One signed order transition to ``submitted``, shaped exactly like the one
    ``brokers.order_state_machine.transition`` writes — ``symbol`` and ``side`` included.

    ``side`` is what makes this the CLOSING edge of the open-position rail, and it is read
    regardless of which surface adjudicated the order (RW-17 adversarial round): a position the
    lane opened and the operator closed by hand must free its slot too."""
    st.audit.append(EVENT_ORDER_TRANSITION, actor="test", refs={"row_id": order_id},
                    payload={"from": "approved", "to": "submitted", "notional": "50",
                             "symbol": symbol, "side": side})


# ---- the policy object ------------------------------------------------------------------
def test_shipped_defaults_are_small_enough_to_lose_without_caring():
    b = AutoExecBudget()
    assert b.max_daily_notional == Decimal("500")
    assert b.max_trades_per_day == 3
    assert b.max_open_positions == 10
    assert b.max_notional_per_symbol_per_day == Decimal("250")


def test_from_settings_falls_back_to_the_conservative_defaults_when_unconfigured():
    """A ``Settings`` that predates RW-17 must not read as 'unbounded'."""
    assert AutoExecBudget.from_settings(Settings()) == AutoExecBudget()


def test_from_settings_reads_configured_rails():
    class S:
        congress_autoexec_max_daily_notional = "400"
        congress_autoexec_max_trades_per_day = 7
        congress_autoexec_max_open_positions = 2
        congress_autoexec_max_notional_per_symbol_per_day = "60"

    b = AutoExecBudget.from_settings(S())
    assert (b.max_daily_notional, b.max_trades_per_day, b.max_open_positions,
            b.max_notional_per_symbol_per_day) == (Decimal("400"), 7, 2, Decimal("60"))


@pytest.mark.parametrize("kwargs", [
    {"max_daily_notional": Decimal("-1")},
    {"max_daily_notional": Decimal("NaN")},
    {"max_trades_per_day": -1},
    {"max_open_positions": "not-an-int"},
])
def test_a_rail_that_cannot_be_evaluated_is_refused_at_construction(kwargs):
    with pytest.raises(ValueError):
        AutoExecBudget(**kwargs)


# ---- derivation from the signed chain ---------------------------------------------------
def test_a_virgin_chain_has_spent_nothing(st):
    state = budget_state(st, day=DAY)
    assert state.readable is True
    assert (state.spent_notional, state.trades, state.open_symbols) == (
        Decimal("0"), 0, frozenset())


def test_spend_is_derived_from_the_chain_and_survives_a_restart(st, tmp_path, clock):
    lane_approval(st, symbol="AAPL", notional="50")
    lane_approval(st, symbol="MSFT", notional="30")
    assert budget_state(st, day=DAY).spent_notional == Decimal("80")

    # "Restart": a brand-new OrchestratorState over the SAME database. Anything held in
    # process memory is gone; the budget must not be.
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw17-budget.db').as_posix()}")
    restarted = OrchestratorState.build(make_session_factory(engine), hmac_key=KEY,
                                        settings=Settings(), clock=clock)
    after = budget_state(restarted, day=DAY)
    assert after.spent_notional == Decimal("80")
    assert after.trades == 2


def test_a_human_approval_does_not_consume_the_robot_budget(st, clock):
    """``bridge/approval.record_decision``'s payload carries no ``lane`` marker."""
    row = ApprovalRecord(
        id=new_id("appr"), proposal_id="p-human", risk_decision_id="rd-1",
        actor_user_id="999", user_decision="approve", surface="discord",
        interaction_id="i-1", order_tuple_hash="h", single_use_consumed=False,
        decided_at=clock.now(), created_at=clock.now(),
    )
    st.audit.record_state_change(row, EVENT_ADJUDICATION, actor="999",
                                 payload={"decision": "approve", "proposal_id": "p-human",
                                          "risk_decision_id": "rd-1",
                                          "order_tuple_hash": "h"})
    state = budget_state(st, day=DAY)
    assert state.trades == 0 and state.spent_notional == Decimal("0")


def test_a_lane_veto_is_not_spend(st):
    st.audit.append(EVENT_ADJUDICATION, actor="999", refs={"proposal_id": "p1"},
                    payload={"decision": "veto", "proposal_id": "p1", "lane": AUTOEXEC_LANE,
                             "symbol": "AAPL", "side": "buy", "notional": "50"})
    assert budget_state(st, day=DAY).trades == 0


def test_spend_is_attributed_to_the_signed_day_not_the_query_day(st):
    lane_approval(st, symbol="AAPL", notional="50")
    assert budget_state(st, day=DAY).trades == 1
    tomorrow = DAY.replace(day=DAY.day + 1)
    assert budget_state(st, day=tomorrow).trades == 0
    # ... but an open position is NOT day-scoped: it is still open tomorrow.
    assert budget_state(st, day=tomorrow).open_symbols == frozenset({"AAPL"})


def test_per_symbol_spend_is_tracked_separately(st):
    lane_approval(st, symbol="AAPL", notional="50")
    lane_approval(st, symbol="AAPL", notional="20")
    lane_approval(st, symbol="MSFT", notional="90")
    state = budget_state(st, day=DAY)
    assert state.per_symbol == {"AAPL": Decimal("70"), "MSFT": Decimal("90")}


# ---- open positions ---------------------------------------------------------------------
def test_a_buy_opens_a_position_immediately(st):
    lane_approval(st, symbol="AAPL")
    assert budget_state(st, day=DAY).open_symbols == frozenset({"AAPL"})


def test_a_sell_that_never_reached_the_broker_does_not_free_a_slot(st):
    """The adjudication is not the exit. If the gateway refused the sell, the position is
    still open, and a slot it never freed must not be handed to a fresh buy."""
    lane_approval(st, symbol="AAPL")
    lane_approval(st, symbol="AAPL", side="sell", notional="50")   # adjudicated, never submitted
    assert budget_state(st, day=DAY).open_symbols == frozenset({"AAPL"})


def test_a_sell_the_signed_order_chain_shows_submitted_frees_the_slot(st):
    lane_approval(st, symbol="AAPL")
    exit_order = lane_approval(st, symbol="AAPL", side="sell", notional="50")
    mark_submitted(st, exit_order)
    assert budget_state(st, day=DAY).open_symbols == frozenset()


# ---- fail closed ------------------------------------------------------------------------
def test_an_unverifiable_chain_is_an_exhausted_budget(st):
    lane_approval(st, symbol="AAPL", notional="10")
    with st.session_factory() as s, s.begin():
        head = s.execute(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)).scalar_one()
        s.delete(head)                                   # keyless tail truncation
    state = budget_state(st, day=DAY)
    assert state.readable is False and state.error
    allowed, reason, detail = check(state, AutoExecBudget(), symbol="AAPL", side="buy",
                                    notional=Decimal("1"))
    assert (allowed, reason) == (False, RAIL_CHAIN_UNREADABLE)
    assert detail


def test_a_verify_chain_fault_is_an_exhausted_budget(st):
    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("db is on fire")

    st.audit.verify_chain = Boom().__getattr__          # any probe fault
    state = budget_state(st, day=DAY)
    assert state.readable is False


def test_an_anchor_rollback_is_an_exhausted_budget(st):
    class RolledBackAnchor:
        def latest(self):
            return (9_999_999, "deadbeef")               # a head far above the real chain

    st.anchor_store = RolledBackAnchor()
    state = budget_state(st, day=DAY)
    assert state.readable is False
    assert "anchor" in (state.error or "")


def test_an_unreadable_signed_notional_poisons_the_day_rather_than_reading_as_free(st):
    st.audit.append(EVENT_ADJUDICATION, actor="copytrade_autoexec",
                    payload={"decision": "approve", "proposal_id": "p", "lane": AUTOEXEC_LANE,
                             "symbol": "AAPL", "side": "buy", "notional": "not-a-number"})
    state = budget_state(st, day=DAY)
    assert state.trades == 1
    allowed, reason, _detail = check(state, AutoExecBudget(), symbol="MSFT", side="buy",
                                     notional=Decimal("1"))
    assert (allowed, reason) == (False, RAIL_DAILY_NOTIONAL)


# ---- the rails themselves ---------------------------------------------------------------
def _state(**over) -> BudgetState:
    base = dict(day=DAY, readable=True, spent_notional=Decimal("0"), trades=0,
                per_symbol={}, open_symbols=frozenset())
    base.update(over)
    return BudgetState(**base)


def test_check_admits_a_trade_that_fits_every_rail():
    assert check(_state(), AutoExecBudget(), symbol="AAPL", side="buy",
                 notional=Decimal("50")) == (True, None, None)


def test_trades_per_day_rail_is_named():
    allowed, reason, detail = check(_state(trades=3), AutoExecBudget(), symbol="AAPL",
                                    side="buy", notional=Decimal("1"))
    assert (allowed, reason) == (False, RAIL_TRADES_PER_DAY)
    assert "3" in detail


def test_daily_notional_rail_is_named():
    allowed, reason, detail = check(_state(spent_notional=Decimal("490")), AutoExecBudget(),
                                    symbol="AAPL", side="buy", notional=Decimal("50"))
    assert (allowed, reason) == (False, RAIL_DAILY_NOTIONAL)
    assert "500" in detail


def test_per_symbol_rail_is_named_and_leaves_other_symbols_alone():
    state = _state(per_symbol={"AAPL": Decimal("245")})
    allowed, reason, _ = check(state, AutoExecBudget(), symbol="AAPL", side="buy",
                               notional=Decimal("10"))
    assert (allowed, reason) == (False, RAIL_SYMBOL_NOTIONAL)
    assert check(state, AutoExecBudget(), symbol="MSFT", side="buy",
                 notional=Decimal("10")).allowed is True


FULL_BOOK = frozenset("ABCDEFGHIJ")            # exactly max_open_positions (10)


def test_open_positions_rail_is_named():
    allowed, reason, detail = check(_state(open_symbols=FULL_BOOK), AutoExecBudget(),
                                    symbol="K", side="buy", notional=Decimal("10"))
    assert (allowed, reason) == (False, RAIL_OPEN_POSITIONS)
    assert "K" in detail


def test_adding_to_an_already_open_position_does_not_trip_the_open_positions_rail():
    assert check(_state(open_symbols=FULL_BOOK), AutoExecBudget(), symbol="A", side="buy",
                 notional=Decimal("10")).allowed is True


def test_an_exit_is_never_refused_for_too_many_open_positions():
    """``copytrade/universe.py``'s rule, applied here: a bound may prevent a NEW BUY; it must
    never be the reason a position cannot be closed — including when the book is ALREADY over
    a cap the operator just lowered, which is exactly when exits matter most."""
    over_cap = frozenset("ABCDEFGHIJKLM")      # 13 open, cap 10
    assert check(_state(open_symbols=over_cap), AutoExecBudget(), symbol="A", side="sell",
                 notional=Decimal("10")).allowed is True


def test_an_unpriceable_order_is_refused_rather_than_treated_as_free():
    allowed, reason, _ = check(_state(), AutoExecBudget(), symbol="AAPL", side="buy",
                               notional=Decimal("NaN"))
    assert (allowed, reason) == (False, RAIL_DAILY_NOTIONAL)


def test_every_refusal_reason_is_in_the_closed_vocabulary():
    seen = {
        check(_state(trades=3), AutoExecBudget(), symbol="A", side="buy",
              notional=Decimal("1")).reason,
        check(_state(spent_notional=Decimal("500")), AutoExecBudget(), symbol="A", side="buy",
              notional=Decimal("1")).reason,
        check(_state(per_symbol={"A": Decimal("250")}), AutoExecBudget(), symbol="A",
              side="buy", notional=Decimal("1")).reason,
        check(_state(open_symbols=FULL_BOOK), AutoExecBudget(), symbol="K",
              side="buy", notional=Decimal("1")).reason,
        check(BudgetState(day=DAY, readable=False, error="x"), AutoExecBudget(), symbol="A",
              side="buy", notional=Decimal("1")).reason,
    }
    assert seen <= set(ALL_RAILS)


def test_remaining_reports_zero_headroom_on_an_unreadable_chain():
    remaining = BudgetState(day=DAY, readable=False, error="tamper").remaining(AutoExecBudget())
    assert remaining == {"readable": False, "error": "tamper", "notional": "0", "trades": 0,
                         "open_position_slots": 0}


# =========================================================================================
# RW-17 adversarial round, C2 — the open-position rail must not RATCHET TO A PERMANENT FLOOR
#
# The rail opened a slot on the lane's ADJUDICATION and could only ever close one on a SELL
# that reached the broker. An adjudicated buy the gateway then REFUSED (gate (d) risk, the
# SEC-07 pending-exposure cap, a tripped breaker, HALT) therefore held a slot that NO event
# could ever release: not a sell (there is no position to sell), not tomorrow (open positions
# are correctly not day-scoped). With the shipped default of ten, the tenth refused buy left
# the lane refusing every new name FOREVER with ``budget_open_positions`` — a lane that has
# quietly stopped trading while reporting a full, healthy-looking book.
# =========================================================================================
def mark_refused(st, order_id: str, *, reason: str = "[review] risk re-check rejected") -> None:
    """The signed refusal ``OrderGateway._record_refusal`` writes when a gate fails. It is
    recorded BEFORE the two-phase intent for every pre-broker gate, so its presence without an
    intent is proof the order never reached the broker."""
    st.audit.append(gateway.EVENT_ORDER_REFUSED, actor="deterministic:copytrade",
                    refs={"order_id": order_id},
                    payload={"reason": reason, "order_id": order_id, "proposal_id": "p",
                             "risk_decision_id": "rd-x", "order_tuple_hash": "h"})


def mark_submit_intent(st, order_id: str) -> None:
    """The SEC-13 two-phase intent the gateway writes immediately BEFORE the broker call."""
    st.audit.intent("order_submit", refs={"order_id": order_id, "proposal_id": "p"},
                    payload={"symbol": "AAPL", "side": "buy", "qty": "1"})


def test_the_refusal_and_intent_event_names_are_PINNED_to_the_order_path(st):
    """This module names two order-path events by string (it must not import the gateway).
    A rename there would silently turn the release edge below into dead code — the ratchet,
    back, undetected — so the two names are asserted against their real writers."""
    assert budget_module._ORDER_REFUSED == gateway.EVENT_ORDER_REFUSED
    assert st.audit.intent("order_submit", refs={"order_id": "o"}).event_type == \
        budget_module._SUBMIT_INTENT


def test_a_buy_the_gateway_REFUSED_BEFORE_THE_BROKER_frees_its_slot(st):
    """THE RATCHET, AS A TEST. Ten adjudicated buys, every one refused at a pre-broker gate:
    zero positions were opened in the account, so zero slots may be held."""
    for symbol in "ABCDEFGHIJ":                        # exactly max_open_positions
        mark_refused(st, lane_approval(st, symbol=symbol))

    today = budget_state(st, day=DAY)
    assert today.open_symbols == frozenset(), "a refused order opened nothing to bound"
    # The DAY rails still counted every one of those adjudications — unchanged, deliberate, and
    # the reason the ratchet is asked about TOMORROW: an approval whose submit failed still
    # spends today's budget, and today's budget is the only part of this that resets.
    assert today.trades == 10 and today.spent_notional == Decimal("500")

    tomorrow = budget_state(st, day=DAY.replace(day=DAY.day + 1))
    assert tomorrow.trades == 0
    assert tomorrow.open_symbols == frozenset(), "the floor must not survive the day"
    assert check(tomorrow, AutoExecBudget(), symbol="K", side="buy",
                 notional=Decimal("10")).allowed is True


def test_a_refusal_AFTER_the_order_reached_the_broker_never_frees_a_slot(st):
    """The fail-safe half. A second submit attempt on an order that already landed (an
    ``approval_replay``, say) is refused too — and that refusal must not release a position
    that genuinely exists."""
    order_id = lane_approval(st, symbol="AAPL")
    mark_submitted(st, order_id, symbol="AAPL", side="buy")
    mark_refused(st, order_id, reason="[review] approval already consumed")

    assert budget_state(st, day=DAY).open_symbols == frozenset({"AAPL"})


def test_an_AMBIGUOUS_broker_error_never_frees_a_slot(st):
    """A refusal recorded after the two-phase INTENT can be response loss on an order that
    LANDED (``gateway`` says so in as many words). Unknown must read as open, never as free."""
    order_id = lane_approval(st, symbol="MSFT")
    mark_submit_intent(st, order_id)
    mark_refused(st, order_id, reason="broker submit failed: timeout")

    assert budget_state(st, day=DAY).open_symbols == frozenset({"MSFT"})


def test_a_refused_buy_does_not_release_a_slot_a_DIFFERENT_order_still_holds(st):
    """Two buys of the same name, one landed and one refused. The refused one releases its own
    claim and nothing else — the position the first one opened is still open."""
    landed = lane_approval(st, symbol="AAPL")
    mark_submitted(st, landed, symbol="AAPL", side="buy")
    mark_refused(st, lane_approval(st, symbol="AAPL"))

    assert budget_state(st, day=DAY).open_symbols == frozenset({"AAPL"})


def test_a_refused_buy_with_no_order_id_keeps_its_slot(st):
    """Backwards compatibility, in the fail-safe direction: an adjudication that carries no
    ``order_id`` cannot be matched to a refusal, so it keeps the pre-existing (over-counting)
    behaviour rather than being released on a guess."""
    st.audit.append(EVENT_ADJUDICATION, actor="copytrade_autoexec",
                    payload={"decision": "approve", "proposal_id": "p", "lane": AUTOEXEC_LANE,
                             "symbol": "AAPL", "side": "buy", "notional": "50"})
    mark_refused(st, "some-other-order")

    assert budget_state(st, day=DAY).open_symbols == frozenset({"AAPL"})
