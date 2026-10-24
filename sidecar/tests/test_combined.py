"""The combined replay imports the risk gate and does not send an order."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sidecar.harness.backtest import Bar

from combined.broker import MAX_NOTIONAL, Book, admit, try_fill
from combined.replay import N_TRIALS, STARTING, run_study
from combined.standin import STAND_IN_ID, proposal_for, target_notional

ROOT = Path(__file__).resolve().parents[1]


def _proposal(**overrides) -> dict:
    body = {
        "symbol": "SPY",
        "side": "buy",
        "asset_type": "equity",
        "order_type": "limit",
        "quantity": Decimal("1"),
        "notional": Decimal("100"),
        "sizing_basis": "fixed-notional",
    }
    body.update(overrides)
    return body


def _admit(proposal, **kwargs):
    book = kwargs.pop("book", Book(cash=STARTING))
    defaults = dict(
        prices={"SPY": Decimal("100")},
        watchlist={"SPY"},
        pending_notional=Decimal(0),
        pending_sells={},
        drawdown=Decimal(0),
        buying_power=STARTING,
    )
    defaults.update(kwargs)
    return book, admit(book, proposal, **defaults)


def test_shipped_cap_rejects_and_leaves_the_book_unchanged():
    book, (admission, _verdict) = _admit(
        _proposal(notional=MAX_NOTIONAL + 1, quantity=Decimal("1")),
        prices={"SPY": MAX_NOTIONAL + 1},
    )
    assert admission.decision == "rejected"
    assert admission.admitted is False
    assert book.cash == STARTING
    assert book.simulated_fills == 0
    assert book.orders_submitted == 0


def test_market_and_option_orders_do_not_fill():
    _book, (market, _) = _admit(_proposal(order_type="market"))
    assert market.decision == "needs_human"
    assert market.admitted is False
    _book, (option, _) = _admit(_proposal(asset_type="option"))
    assert option.decision == "rejected"
    assert "asset_type_supported" in option.failed


def test_a_sell_without_shares_is_rejected():
    _book, (admission, _) = _admit(_proposal(side="sell", quantity=Decimal("5"), notional=Decimal("500")))
    assert admission.decision == "rejected"
    assert "sell_backed" in admission.failed
    assert _book.simulated_fills == 0


def test_read_only_mode_is_not_admitted():
    _book, (admission, _) = _admit(_proposal(), mode="read_only")
    assert admission.admitted is False
    assert admission.decision == "rejected"


def test_composed_flags_without_a_verdict_need_a_human():
    book, (admission, _) = _admit(_proposal(), require_quant_verdict=True, require_net_edge=True)
    assert admission.decision == "needs_human"
    assert admission.admitted is False
    assert book.simulated_fills == 0


def test_a_marketable_limit_fills_locally_and_submits_nothing():
    book = Book(cash=STARTING)
    record = try_fill(
        book,
        _proposal(),
        arrival=Decimal("99"),
        prices={"SPY": Decimal("99")},
        watchlist={"SPY"},
        pending_notional=Decimal(0),
        pending_sells={},
        drawdown=Decimal(0),
        buying_power=STARTING,
        sigma=Decimal("0.01"),
        adv=Decimal("1000000"),
        cost_model="flat",
        decision_price=Decimal("100"),
    )
    assert record is not None
    assert book.simulated_fills == 1
    assert book.orders_submitted == 0
    assert book.position("SPY") == Decimal("1")
    missed = try_fill(
        Book(cash=STARTING),
        _proposal(),
        arrival=Decimal("101"),
        prices={"SPY": Decimal("101")},
        watchlist={"SPY"},
        pending_notional=Decimal(0),
        pending_sells={},
        drawdown=Decimal(0),
        buying_power=STARTING,
        sigma=Decimal("0.01"),
        adv=Decimal("1000000"),
        cost_model="flat",
        decision_price=Decimal("100"),
    )
    assert missed is None


def test_the_stand_in_names_itself_and_ignores_later_closes():
    closes = [Decimal(100) + Decimal(index) for index in range(80)]
    early = closes[:-5]
    mutated = list(closes)
    mutated[-1] = Decimal(1)
    assert target_notional(early, variant="cockpit", cap=MAX_NOTIONAL, equity=STARTING) == target_notional(
        mutated[:-5], variant="cockpit", cap=MAX_NOTIONAL, equity=STARTING
    )
    proposal = proposal_for(
        symbol="SPY",
        closes=closes,
        held_shares=Decimal(0),
        price=closes[-1],
        variant="cockpit",
        cap=MAX_NOTIONAL,
        equity=STARTING,
    )
    assert proposal is not None
    assert proposal["generated_by"]["model"] == STAND_IN_ID
    assert "thesis" not in proposal
    assert "claude" not in proposal["generated_by"]["model"]


def _sessions(count: int) -> list[date]:
    cursor = date(2024, 1, 2)
    days = []
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _rising_book(count: int, *, last_close: Decimal | None = None) -> dict[str, list[Bar]]:
    days = _sessions(count)
    grouped: dict[str, list[Bar]] = {}
    for offset, symbol in enumerate(("SPY", "QQQ", "IWM")):
        rows = []
        for index, day in enumerate(days):
            close = Decimal(100 + offset) + Decimal(index) / Decimal(10)
            if last_close is not None and index == count - 1:
                close = last_close
            open_ = close - Decimal("0.50")
            rows.append(
                Bar(
                    day=day,
                    symbol=symbol,
                    open=open_,
                    high=close,
                    low=open_,
                    close=close,
                    adj_close=close,
                    volume=Decimal("1000000"),
                )
            )
        grouped[symbol] = rows
    return grouped


def test_a_later_close_does_not_change_earlier_fills():
    base = run_study(
        _rising_book(80),
        options_returns={},
        kronos_curve=[],
        bootstrap_reps=20,
        n_slices=4,
    )
    shifted = run_study(
        _rising_book(80, last_close=Decimal("1")),
        options_returns={},
        kronos_curve=[],
        bootstrap_reps=20,
        n_slices=4,
    )
    cutoff = _sessions(80)[-5].isoformat()

    def equity_through(study):
        return [
            (row["date"], row["equity"])
            for row in study["variants"]["cockpit"]["equity_curve"]
            if row["date"] < cutoff
        ]

    assert equity_through(base) == equity_through(shifted)
    assert base["orders_submitted"] == 0
    assert base["n_trials"] == N_TRIALS
    assert base["composed_production"]["decision"] == "needs_human"
    assert base["variants"]["cockpit"]["simulated_fills"] > 0
    assert base["options_days_applied"] == 0


def test_combined_sources_stay_outside_the_package_import_ban():
    package = ROOT / "sidecar"
    banned = Path(__file__).resolve().parents[1] / "combined"
    text = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
    assert "from app" not in text
    assert "import brokers" not in text
    local = "\n".join(path.read_text(encoding="utf-8") for path in banned.rglob("*.py"))
    assert "import brokers" not in local
    assert "submit_order" not in local
    assert "keyring" not in local


def test_loading_the_risk_gate_does_not_import_a_broker_module():
    sys.modules.pop("brokers", None)
    from combined.broker import load_risk

    load_risk()
    assert "risk.engine" in sys.modules
    assert "brokers" not in sys.modules
