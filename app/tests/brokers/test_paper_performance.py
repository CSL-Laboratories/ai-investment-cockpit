"""W-22 performance signals come only from the verified audit chain."""
from __future__ import annotations

from decimal import Decimal

from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.paper_performance import reconstruct_paper_performance


def _filled(st, order_id: str, *, symbol: str, side: str, qty: str, price: str):
    intent = st.audit.intent(
        "order_submit",
        refs={"order_id": order_id},
        payload={"symbol": symbol, "side": side, "qty": qty, "execution_scope": "paper"},
    )
    st.audit.result(
        intent,
        "order_submit",
        refs={"order_id": order_id, "broker_order_id": f"broker-{order_id}"},
        payload={"status": "filled", "filled_qty": qty, "avg_fill_price": price},
    )


def test_equity_drawdown_and_consecutive_round_trip_losses_are_reconstructed(st):
    client = FakeAlpaca(equity="100000")
    broker = AlpacaPaperBroker(client=client)
    broker.get_positions(st=st, clock=st.clock)
    st.audit.append(
        "portfolio_snapshot", actor="other_broker",
        payload={"kind": "positions", "equity": "1"},
    )
    client.equity = "99400"
    broker.get_positions(st=st, clock=st.clock)

    _filled(st, "o1", symbol="AAPL", side="buy", qty="1", price="100")
    _filled(st, "o2", symbol="AAPL", side="sell", qty="1", price="90")
    _filled(st, "o3", symbol="MSFT", side="buy", qty="2", price="50")
    _filled(st, "o4", symbol="MSFT", side="sell", qty="2", price="48")
    _filled(st, "o5", symbol="NVDA", side="sell", qty="1", price="25")
    _filled(st, "o6", symbol="NVDA", side="buy", qty="1", price="27")

    result = reconstruct_paper_performance(st)

    assert result.ok
    assert result.day_open_equity == Decimal("100000")
    assert result.current_equity == Decimal("99400")
    assert result.equity_drawdown_today == Decimal("600")
    assert result.closed_round_trips == 3
    assert result.consecutive_losses == 3
    assert result.realized_pnl == Decimal("-16")
    assert st.audit.verify_chain().ok


def test_cumulative_average_produces_the_exact_incremental_fill_price(st):
    client = FakeAlpaca(equity="100000")
    AlpacaPaperBroker(client=client).get_positions(st=st, clock=st.clock)
    intent = st.audit.intent(
        "order_submit",
        refs={"order_id": "buy"},
        payload={"symbol": "AAPL", "side": "buy", "qty": "2",
                 "execution_scope": "paper"},
    )
    st.audit.result(
        intent, "order_submit", refs={"order_id": "buy"},
        payload={"status": "partially_filled", "filled_qty": "1", "avg_fill_price": "100"},
    )
    st.audit.append(
        "order_fill_progress",
        refs={"row_id": "buy"},
        payload={
            "to": "partially_filled", "symbol": "AAPL", "side": "buy",
            "filled_qty": "2", "avg_fill_price": "105",
        },
    )
    _filled(st, "sell", symbol="AAPL", side="sell", qty="2", price="106")

    result = reconstruct_paper_performance(st)
    # Cumulative average 105 means the second share filled at 110; exit at 106 => +2 total.
    assert result.ok and result.realized_pnl == Decimal("2")
    assert result.consecutive_losses == 0


def test_missing_fill_price_and_a_tampered_chain_fail_closed(st):
    client = FakeAlpaca(equity="100000")
    AlpacaPaperBroker(client=client).get_positions(st=st, clock=st.clock)
    intent = st.audit.intent(
        "order_submit", refs={"order_id": "bad"},
        payload={"symbol": "AAPL", "side": "buy", "qty": "1",
                 "execution_scope": "paper"},
    )
    st.audit.result(
        intent, "order_submit", refs={"order_id": "bad"},
        payload={"status": "filled", "filled_qty": "1", "avg_fill_price": None},
    )
    result = reconstruct_paper_performance(st)
    assert not result.ok and "incomplete" in str(result.reason)

    with st.session_factory() as session, session.begin():
        row = session.get(type(intent), intent.id)
        row.payload = {**row.payload, "qty": "999"}
    tampered = reconstruct_paper_performance(st)
    assert not tampered.ok and "audit chain verification failed" in str(tampered.reason)
    assert "hash mismatch" in str(tampered.reason)


def test_fill_larger_than_the_signed_paper_order_fails_closed(st):
    AlpacaPaperBroker(client=FakeAlpaca(equity="100000")).get_positions(
        st=st, clock=st.clock
    )
    intent = st.audit.intent(
        "order_submit", refs={"order_id": "overfill"},
        payload={"symbol": "AAPL", "side": "buy", "qty": "1",
                 "execution_scope": "paper"},
    )
    st.audit.result(
        intent, "order_submit", refs={"order_id": "overfill"},
        payload={"status": "filled", "filled_qty": "2", "avg_fill_price": "100"},
    )

    result = reconstruct_paper_performance(st)
    assert not result.ok and "signed order size" in str(result.reason)
