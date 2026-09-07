"""BUG-148: prices cannot manufacture shares or release outstanding sell reservations."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, update

from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.order_state_machine import create_order, transition
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from brokers.sell_reservations import pending_sell_quantities
from config.settings import Mode
from ingest.market_data import MarketMetrics, MarketObservation
from orchestrator.mode_ladder import ModeLadder
from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import PriceScale, to_ns
from quant.verify import VerifyParams
from schemas.models.order import Order


def _service(st, holdings=None):
    ModeLadder(st).set_mode(Mode.paper, actor="operator")
    positions = holdings if holdings is not None else [
        {"symbol": "AAPL", "qty": "100", "market_value": "1000"}
    ]
    client = FakeAlpaca(cash="100000", equity="101000", positions=positions)
    quote = {"price": "9.90", "age_s": 1, "spread_pct": "0.2"}
    book = BookSnapshot(
        symbol="AAPL", seq=1, ts=to_ns(st.clock.now()),
        bids=(LevelSnapshot(price_ticks=989, qty=10000, order_count=1),),
        asks=(LevelSnapshot(price_ticks=991, qty=10000, order_count=1),),
    )
    observation = MarketObservation("AAPL", quote, book, "test-raw", "a" * 64)
    metric = MarketMetrics("AAPL", st.clock.now().date(), Decimal("100000000"), 0.1, 30)
    market = SimpleNamespace(
        observation=lambda symbol: observation, book=lambda symbol: book,
        metrics=lambda symbol: metric,
        verify_params=VerifyParams(scale=PriceScale(tick_size=Decimal("0.01"))),
    )
    return PaperExecutionService(
        st, broker=AlpacaPaperBroker(client), gateway=None, token_registry=None,
        quote_fn=lambda symbol: quote, market_data=market, require_performance=True,
    )


def _risk(service, qty, **kwargs):
    proposal = {"symbol": "AAPL", "side": "sell", "asset_type": "equity",
                "quantity": qty, "order_type": "limit", "sizing_basis": "fixed",
                "time_horizon": "short"}
    resolved = canonical_order_tuple(symbol="AAPL", side="sell", qty=qty,
                                     order_type="limit", limit_price="9.90", tif="day")
    result, _ = service._fresh_risk(proposal, "AAPL", resolved_order=resolved,
                                   require_w21=True, **kwargs)
    return result


def _sell(st, qty=60, status="submitted"):
    canon = canonical_order_tuple(symbol="AAPL", side="sell", qty=qty,
                                  order_type="limit", limit_price="9.90", tif="day")
    order = create_order(st, proposal_id="p-sale", risk_decision_id="r-sale", account="paper",
                         **canon, order_tuple_hash=order_tuple_hash(canon),
                         notional=str(Decimal(qty) * Decimal("9.90")))
    if status != "proposed":
        transition(st, order, "approved")
    if status in {"submitted", "partially_filled", "cancelled"}:
        intent = st.audit.intent("order_submit", refs={"order_id": order.id},
                                 payload={"symbol": "AAPL", "side": "sell", "qty": str(qty),
                                          "execution_scope": "paper", "mode": "paper"})
        st.audit.result(intent, "order_submit", refs={"order_id": order.id,
                                                     "broker_order_id": "paper-sale"},
                        payload={"status": "accepted", "filled_qty": "0"})
        transition(st, order, "submitted", broker_order_id="paper-sale")
    if status == "partially_filled":
        transition(st, order, "partially_filled", filled_qty="20", avg_fill_price="9.90")
    if status == "cancelled":
        transition(st, order, "cancelled", actor="alpaca_paper_reconcile",
                   reason="broker status poll reported 'canceled'")
    return order


def test_price_difference_does_not_allow_an_extra_share(st):
    service = _service(st)
    oversized = _risk(service, 101)
    assert oversized["decision"] == "rejected"
    assert "sell_backed" in oversized["failed_checks"]
    assert "net_edge_available" in oversized["warnings"]
    assert _risk(service, 100)["decision"] == "approved"


@pytest.mark.parametrize("qty", [None, "NaN", "Infinity", "-1", "garbage", True])
def test_unknown_or_malformed_owned_quantity_never_backs_sell(st, qty):
    service = _service(st, [{"symbol": "AAPL", "qty": qty, "market_value": "1000"}])
    assert "sell_backed" in _risk(service, 1)["failed_checks"]


def test_duplicate_position_identity_cannot_supply_backing(st):
    holding = {"symbol": "AAPL", "qty": "100", "market_value": "1000"}
    assert "sell_backed" in _risk(_service(st, [holding, holding]), 1)["failed_checks"]


def test_broker_available_quantity_caps_external_orders(st):
    service = _service(st, [{"symbol": "AAPL", "qty": "100", "qty_available": "40",
                             "market_value": "1000"}])
    _sell(st, qty=60)
    assert _risk(service, 40)["decision"] == "approved"  # no double reservation
    assert "sell_backed" in _risk(service, 41)["failed_checks"]


@pytest.mark.parametrize("status", ["approved", "submitted"])
def test_outstanding_sell_reserves_shares(st, status):
    service = _service(st)
    _sell(st, status=status)
    assert pending_sell_quantities(st) == {"AAPL": Decimal("60")}
    assert _risk(service, 40)["decision"] == "approved"
    assert "sell_backed" in _risk(service, 41)["failed_checks"]


def test_partial_fill_reserves_only_remaining_shares(st):
    service = _service(st, [{"symbol": "AAPL", "qty": "80", "market_value": "800"}])
    _sell(st, status="partially_filled")
    assert pending_sell_quantities(st) == {"AAPL": Decimal("40")}
    assert _risk(service, 40)["decision"] == "approved"
    assert "sell_backed" in _risk(service, 41)["failed_checks"]


def test_signed_cancel_releases_reservation(st):
    service = _service(st)
    _sell(st, status="cancelled")
    assert pending_sell_quantities(st) == {}
    assert _risk(service, 100)["decision"] == "approved"


def test_unsubmitted_drafts_do_not_reserve_and_current_approval_is_excluded(st):
    service = _service(st)
    _sell(st, qty=60, status="proposed")
    current = _sell(st, qty=100, status="approved")
    assert _risk(service, 100, order_id=current.id)["decision"] == "approved"


def test_lost_response_and_canary_preserve_reservation(st):
    service = _service(st)
    order = _sell(st, status="proposed")
    st.audit.intent("order_submit", refs={"order_id": order.id},
                    payload={"symbol": "AAPL", "side": "sell", "qty": "60"})
    assert "sell_backed" in _risk(service, 41, order_id=order.id)["failed_checks"]
    transition(st, order, "canary_breach")
    assert "sell_backed" in _risk(service, 41)["failed_checks"]


@pytest.mark.parametrize("damage", ["delete", "qty", "state", "filled_qty"])
def test_unsigned_order_damage_cannot_release_reservation(st, damage):
    service = _service(st)
    order = _sell(st)
    with st.session_factory() as session, session.begin():
        if damage == "delete":
            session.execute(delete(Order).where(Order.id == order.id))
        else:
            replacement = {"qty": "1", "state": "cancelled", "filled_qty": "60"}[damage]
            session.execute(update(Order).where(Order.id == order.id).values({damage: replacement}))
    assert st.audit.verify_chain().ok
    assert "sell_backed" in _risk(service, 1)["failed_checks"]


def test_operator_veto_after_uncertain_submit_keeps_order_reconcilable(st):
    from tests.brokers.test_paper_execution import OPERATOR_ID, _service as composed_service

    service, scenario, _ = composed_service(st)
    intent = st.audit.intent("order_submit", refs={"order_id": scenario.order.id},
                             payload={"symbol": "AAPL", "side": "buy", "qty": "1",
                                      "execution_scope": "paper"})
    st.audit.result(intent, "order_submit", refs={"order_id": scenario.order.id,
                                                 "aborted": "broker_error"},
                    payload={"aborted": "response lost"})
    with pytest.raises(PaperExecutionError, match="requires broker reconciliation"):
        service.decide(scenario.order.id, actor=OPERATOR_ID, decision="veto",
                       confirm_symbol=None, request_id="veto-after-response-loss")
    with st.session_factory() as session:
        assert session.get(Order, scenario.order.id).state == "proposed"
