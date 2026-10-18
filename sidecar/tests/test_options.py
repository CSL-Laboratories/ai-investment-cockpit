"""Defined-risk options: risk checks, paper host, and a synthetic book."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from sidecar.options.client import OptionsClientError, OptionsPaperClient, assert_options_url
from sidecar.options.config import PRIMARY, friday_expiry, trial_configs
from sidecar.options.plan import plan_actions, quote_from_parts
from sidecar.options.pricing import bs_delta, bs_price
from sidecar.options.risk import (
    Leg,
    OptionsRiskError,
    admit_spread,
    assert_defined_risk,
    assert_entry_dte,
    max_loss_dollars,
    opening_limit_price,
    validate_mleg_body,
)
from sidecar.options.runner import in_regular_session, main, run_options
from sidecar.options.simulate import simulate_book
from sidecar.options.structures import occ_symbol


def test_put_call_parity_at_zero_rates():
    call = bs_price(100, 100, 30 / 365, 0.2, "call")
    put = bs_price(100, 100, 30 / 365, 0.2, "put")
    assert abs((call - put) - (100 - 100)) < 1e-8
    delta = bs_delta(100, 90, 30 / 365, 0.2, "put")
    assert -1 < delta < 0


def test_occ_symbol_and_friday_tenor():
    assert occ_symbol("SPY", date(2025, 1, 17), "put", Decimal("500")) == "SPY250117P00500000"
    expiry = friday_expiry(date(2026, 10, 14), 30)
    assert expiry is not None
    assert expiry.weekday() == 4
    assert 7 <= (expiry - date(2026, 10, 14)).days <= 45


def test_defined_risk_rejects_a_naked_short_and_a_large_loss():
    naked = [Leg("SPY", date(2026, 11, 20), "put", Decimal("500"), "sell")]
    with pytest.raises(OptionsRiskError, match="naked_short"):
        assert_defined_risk(naked)
    covered = [
        Leg("SPY", date(2026, 11, 20), "put", Decimal("500"), "sell"),
        Leg("SPY", date(2026, 11, 20), "put", Decimal("495"), "buy"),
    ]
    assert_defined_risk(covered)
    assert max_loss_dollars(covered, Decimal("0.70"), 1) == Decimal("430.00")
    wide = [
        Leg("SPY", date(2026, 11, 20), "put", Decimal("500"), "sell"),
        Leg("SPY", date(2026, 11, 20), "put", Decimal("480"), "buy"),
    ]
    with pytest.raises(OptionsRiskError, match="max_loss_per_trade"):
        max_loss_dollars(wide, Decimal("0.10"), 1)


def test_caps_dte_and_credit_limit():
    kill = Path("/tmp/does-not-exist-sidecar-kill")
    with pytest.raises(OptionsRiskError, match="max_open_spreads"):
        admit_spread(Decimal("100"), [Decimal("100")] * 5, kill)
    with pytest.raises(OptionsRiskError, match="total_max_loss"):
        admit_spread(Decimal("1000"), [Decimal("1100")] * 4, None)
    present = Path("/tmp/sidecar-options-kill-test")
    present.write_text("stop", encoding="utf-8")
    try:
        with pytest.raises(OptionsRiskError, match="kill_switch"):
            admit_spread(Decimal("100"), [], present)
    finally:
        present.unlink(missing_ok=True)
    with pytest.raises(OptionsRiskError):
        assert_entry_dte(6)
    with pytest.raises(OptionsRiskError):
        assert_entry_dte(46)
    assert_entry_dte(30)
    assert opening_limit_price(Decimal("1.25")) == Decimal("-1.25")
    with pytest.raises(OptionsRiskError, match="credit_limit"):
        validate_mleg_body(
            {
                "order_class": "mleg",
                "qty": "1",
                "type": "limit",
                "limit_price": "1.00",
                "time_in_force": "day",
                "client_order_id": "scTestOpenLimit123",
                "legs": [
                    {"symbol": "SPY", "side": "sell", "position_intent": "sell_to_open", "ratio_qty": "1"},
                    {"symbol": "SPY", "side": "buy", "position_intent": "buy_to_open", "ratio_qty": "1"},
                ],
            },
            opening=True,
        )


def test_live_host_is_refused():
    with pytest.raises(OptionsClientError):
        assert_options_url("https://api.alpaca.markets/v2/orders", "POST")
    with pytest.raises(OptionsClientError):
        assert_options_url("https://paper-api.alpaca.markets/v2/orders", "PUT")
    assert_options_url("https://paper-api.alpaca.markets/v2/orders", "POST")
    assert_options_url("https://data.alpaca.markets/v1beta1/options/snapshots/SPY", "GET")


def _chain(now: datetime) -> dict:
    expiry = date(2026, 11, 13)
    specs = [
        ("SPY261113P00520000", "put", Decimal("520"), Decimal("-0.50"), Decimal("8.00"), Decimal("8.20")),
        ("SPY261113P00500000", "put", Decimal("500"), Decimal("-0.20"), Decimal("2.00"), Decimal("2.10")),
        ("SPY261113P00495000", "put", Decimal("495"), Decimal("-0.10"), Decimal("1.20"), Decimal("1.30")),
    ]
    quotes = []
    for symbol, right, strike, delta, bid, ask in specs:
        quote = quote_from_parts(
            symbol=symbol,
            underlying="SPY",
            expiry=expiry,
            right=right,
            strike=strike,
            delta=delta,
            iv=Decimal("0.25"),
            bid=bid,
            ask=ask,
            quote_time=now,
            now=now,
        )
        assert quote is not None
        quotes.append(quote)
    return {"SPY": quotes, "QQQ": [], "IWM": []}


def test_plan_is_a_credit_and_blocks_unknown_shorts(tmp_path):
    now = datetime(2026, 10, 14, 14, 30, tzinfo=timezone.utc)
    plan = plan_actions(
        today=now.date(),
        session_day="2026-10-14",
        views=_chain(now),
        signals={"SPY": True, "QQQ": False, "IWM": False},
        state=[],
        positions=[],
        kill_file=tmp_path / "absent",
        kind="put_credit",
        options_approved=True,
    )
    assert len(plan.opens) == 1
    assert plan.opens[0]["limit_price"].startswith("-")
    assert plan.opens[0]["order_class"] == "mleg"
    assert plan.opens[0]["type"] == "limit"
    blocked = plan_actions(
        today=now.date(),
        session_day="2026-10-14",
        views=_chain(now),
        signals={"SPY": True},
        state=[],
        positions=[{"symbol": "SPY261113P00400000", "qty": "-1"}],
        kill_file=tmp_path / "absent",
        kind="put_credit",
        options_approved=True,
    )
    assert blocked.opens == []
    assert blocked.unknown_short


def test_missing_greeks_are_not_priced():
    now = datetime(2026, 10, 14, 14, 30, tzinfo=timezone.utc)
    assert (
        quote_from_parts(
            symbol="SPY261113P00500000",
            underlying="SPY",
            expiry=date(2026, 11, 13),
            right="put",
            strike=Decimal("500"),
            delta=Decimal("NaN"),
            iv=Decimal("0.2"),
            bid=Decimal("1"),
            ask=Decimal("1.1"),
            quote_time=now,
            now=now,
        )
        is None
    )


def _account():
    return {
        "status": "ACTIVE",
        "equity": "100000",
        "option_trading_level": 3,
        "trading_blocked": False,
        "account_blocked": False,
    }


def _install_client(tmp_path, posts: list):
    now = datetime(2026, 10, 14, 14, 30, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request.content.decode())
            return httpx.Response(200, json={"id": "order-1", "client_order_id": "sc"})
        if request.method == "DELETE":
            posts.append("DELETE")
            return httpx.Response(200, json={})
        path = request.url.path
        if path.endswith("/v2/account"):
            return httpx.Response(200, json=_account())
        if path.endswith("/v2/positions") or path.endswith("/v2/orders"):
            return httpx.Response(200, json=[])
        if "by_client_order_id" in path:
            return httpx.Response(404, json={})
        if path.endswith("/v2/options/contracts"):
            if request.url.params.get("underlying_symbols") != "SPY":
                return httpx.Response(200, json={"option_contracts": [], "next_page_token": None})
            if request.url.params.get("type") == "call":
                return httpx.Response(200, json={"option_contracts": [], "next_page_token": None})
            return httpx.Response(
                200,
                json={
                    "option_contracts": [
                        {"symbol": "SPY261113P00520000"},
                        {"symbol": "SPY261113P00500000"},
                        {"symbol": "SPY261113P00495000"},
                    ],
                    "next_page_token": None,
                },
            )
        if "/options/snapshots/" in path:
            if not path.endswith("/SPY"):
                return httpx.Response(200, json={"snapshots": {}, "next_page_token": None})
            stamp = now.isoformat().replace("+00:00", "Z")
            snaps = {}
            for symbol, delta, bid, ask in (
                ("SPY261113P00520000", -0.5, 8.0, 8.2),
                ("SPY261113P00500000", -0.2, 2.0, 2.1),
                ("SPY261113P00495000", -0.1, 1.2, 1.3),
            ):
                snaps[symbol] = {
                    "impliedVolatility": 0.25,
                    "greeks": {"delta": delta},
                    "latestQuote": {"bp": bid, "ap": ask, "t": stamp},
                }
            return httpx.Response(200, json={"snapshots": snaps, "next_page_token": None})
        if path.endswith("/v2/stocks/bars"):
            closes = [100 + index * 0.2 for index in range(20)]
            rows = [{"c": close} for close in closes]
            return httpx.Response(200, json={"bars": {"SPY": rows, "QQQ": rows, "IWM": rows}})
        return httpx.Response(404, json={})

    client = OptionsPaperClient("key", "secret", transport=httpx.MockTransport(handler))
    history = tmp_path / "iv.jsonl"
    from datetime import timedelta

    start = date(2024, 1, 1)
    lines = []
    for index in range(252):
        day = (start + timedelta(days=index)).isoformat()
        lines.append('{"date": "%s", "symbol": "SPY", "iv": "0.10"}' % day)
    history.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return client, history, now


def test_dry_run_does_not_post(tmp_path):
    posts = []
    client, history, now = _install_client(tmp_path, posts)
    report = run_options(
        execute=False,
        now=now,
        client=client,
        kill_file=tmp_path / "no-kill",
        state_path=tmp_path / "state.json",
        iv_path=history,
        out=tmp_path / "report.json",
        order_log_path=tmp_path / "orders.jsonl",
    )
    client.close()
    assert posts == []
    assert report["orders_submitted"] == 0
    assert report["n_planned_opens"] == 1
    assert report["planned_limit_prices"][0].startswith("-")


def test_execute_with_kill_does_not_post(tmp_path):
    posts = []
    client, history, now = _install_client(tmp_path, posts)
    kill = tmp_path / "KILL"
    kill.write_text("stop", encoding="utf-8")
    report = run_options(
        execute=True,
        now=now,
        client=client,
        kill_file=kill,
        state_path=tmp_path / "state.json",
        iv_path=history,
        out=tmp_path / "report.json",
        order_log_path=tmp_path / "orders.jsonl",
    )
    client.close()
    assert posts == []
    assert report["orders_submitted"] == 0
    assert report["kill_switch"] is True
    assert report["n_planned_opens"] == 0


def test_window_and_missing_credentials(tmp_path, monkeypatch):
    import sys
    import types

    assert in_regular_session(datetime(2026, 10, 14, 14, 30, tzinfo=timezone.utc))
    assert not in_regular_session(datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc))
    code = main(
        [
            "--now",
            "2026-10-18T15:00:00+00:00",
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == 3
    fake = types.ModuleType("keyring")
    fake.get_password = lambda service, name: None
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.delenv("SIDECAR_PAPER_ALPACA_KEY_ID", raising=False)
    monkeypatch.delenv("SIDECAR_PAPER_ALPACA_SECRET", raising=False)
    code = main(
        [
            "--now",
            "2026-10-14T14:30:00+00:00",
            "--out",
            str(tmp_path / "out.json"),
            "--state",
            str(tmp_path / "state.json"),
            "--iv-history",
            str(tmp_path / "iv.jsonl"),
            "--order-log",
            str(tmp_path / "orders.jsonl"),
        ]
    )
    assert code == 2


def test_synthetic_book_submits_nothing(tmp_path):
    import math

    from sidecar.options.config import StrategyConfig

    closes = []
    spot = 100.0
    for index in range(80):
        spot *= math.exp(0.01 * math.sin(index / 3) - 0.0002)
        closes.append(spot)
    dates = [date(2024, 1, 1).fromordinal(date(2024, 1, 1).toordinal() + index) for index in range(80)]
    config = StrategyConfig(
        name="put_credit_d20_ivr60",
        kind="put_credit",
        iv_window=10,
        rv_window=5,
        rank_window=15,
    )
    result = simulate_book({"SPY": closes, "QQQ": closes, "IWM": closes}, dates, config)
    assert result["orders_submitted"] == 0
    assert result["name"] == PRIMARY.name
    assert trial_configs()[0].rank_window == 252
    assert [row.name for row in trial_configs()][0] == PRIMARY.name
