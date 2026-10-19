from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from sidecar.flow.client import FlowDataClient, assert_flow_url
from sidecar.flow.config import FlowError, resolve_feed
from sidecar.flow.normalize import assemble_chain
from sidecar.flow.score import ContractView, OptionPrint, events_from_scan, render_markdown, score_book
from sidecar.flow.study import calendar_null_events, event_study, flow_event_report, session_return
from sidecar.harness.backtest import Bar
from sidecar.harness.paper_credentials import SidecarCredentialsMissing


def _bar(symbol: str, day: date, close: str) -> Bar:
    px = Decimal(close)
    return Bar(day, symbol, px, px, px, px, px, Decimal("1000"))


def _contract(**overrides) -> ContractView:
    base = dict(
        underlying="SPY",
        symbol="SPY240607C00520000",
        expiration=date(2024, 6, 7),
        strike=520.0,
        right="call",
        spot=500.0,
        volume=1000.0,
        open_interest=100.0,
        open_interest_date=date(2024, 6, 3),
        bid=1.0,
        ask=1.2,
        delta=0.1,
        iv=0.2,
        prints=(),
        session_date=date(2024, 6, 3),
    )
    base.update(overrides)
    return ContractView(**base)


def test_feed_default_is_indicative_and_opra_is_explicit(monkeypatch):
    monkeypatch.delenv("SIDECAR_OPTIONS_FEED", raising=False)
    assert resolve_feed(None) == "indicative"
    monkeypatch.setenv("SIDECAR_OPTIONS_FEED", "opra")
    assert resolve_feed(None) == "opra"
    assert resolve_feed("indicative") == "indicative"
    with pytest.raises(FlowError):
        resolve_feed("sip")


def test_client_refuses_writes_and_the_live_host():
    with pytest.raises(FlowError):
        assert_flow_url("https://paper-api.alpaca.markets/v2/orders", "POST")
    with pytest.raises(FlowError):
        assert_flow_url("https://api.alpaca.markets/v2/options/contracts", "GET")
    with pytest.raises(FlowError):
        assert_flow_url("https://data.alpaca.markets/v2/stocks/bars", "GET")
    assert_flow_url("https://data.alpaca.markets/v1beta1/options/snapshots/SPY", "GET")


def test_opra_forbidden_does_not_fall_back():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(403, json={"message": "no"})

    client = FlowDataClient("key", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(FlowError, match="403"):
        client.snapshots("SPY", "opra")
    assert len(seen) == 1
    assert "feed=opra" in seen[0]
    assert "secret" not in repr(client)


def test_missing_sidecar_credentials_do_not_fall_back(monkeypatch):
    monkeypatch.delenv("SIDECAR_PAPER_ALPACA_KEY_ID", raising=False)
    monkeypatch.delenv("SIDECAR_PAPER_ALPACA_SECRET", raising=False)
    monkeypatch.setattr("sidecar.harness.paper_credentials._keyring_pair", lambda: None)
    with pytest.raises(SidecarCredentialsMissing):
        FlowDataClient.from_sidecar_credentials()


def test_volume_over_open_interest_and_stale_interest():
    fresh = score_book([_contract(volume=300, open_interest=100)], [], feed="indicative", session_date=date(2024, 6, 3))
    assert fresh["contracts"][0]["alert"] is True
    assert fresh["contracts"][0]["vol_oi"] == 3
    stale = score_book(
        [_contract(volume=300, open_interest=100, open_interest_date=date(2024, 5, 1))],
        [],
        feed="indicative",
        session_date=date(2024, 6, 3),
    )
    assert stale["contracts"][0]["open_interest"] is None
    assert stale["contracts"][0]["alert"] is False
    assert fresh["orders_submitted"] == 0


def test_volume_z_ignores_the_current_session_and_needs_twenty_priors():
    session = date(2024, 6, 3)
    history = [
        {"kind": "volume", "session_date": (session - timedelta(days=index)).isoformat(), "symbol": "SPY240607C00520000", "volume": 100.0}
        for index in range(1, 21)
    ]
    history.append(
        {"kind": "volume", "session_date": session.isoformat(), "symbol": "SPY240607C00520000", "volume": 10000.0}
    )
    scored = score_book([_contract(volume=100)], history, feed="indicative", session_date=session)
    assert scored["contracts"][0]["volume_z"] is None
    varied = [
        {
            "kind": "volume",
            "session_date": (session - timedelta(days=index)).isoformat(),
            "symbol": "SPY240607C00520000",
            "volume": 100.0 if index > 1 else 200.0,
        }
        for index in range(1, 21)
    ]
    high = score_book([_contract(volume=500, open_interest=None)], varied, feed="indicative", session_date=session)
    assert high["contracts"][0]["volume_z"] is not None
    assert high["contracts"][0]["volume_z"] > 2
    short = varied[:19]
    missing = score_book([_contract(volume=500, open_interest=None)], short, feed="indicative", session_date=session)
    assert missing["contracts"][0]["volume_z"] is None


def test_sweep_and_aggressive_print_score():
    stamp = datetime(2024, 6, 3, 14, 30, tzinfo=timezone.utc)
    prints = (
        OptionPrint(5.0, 100.0, stamp, 5.0),
        OptionPrint(5.1, 100.0, stamp + timedelta(milliseconds=400), 5.0),
    )
    scored = score_book(
        [_contract(volume=10, open_interest=None, prints=prints)],
        [],
        feed="opra",
        session_date=date(2024, 6, 3),
    )
    row = scored["contracts"][0]
    assert row["large_premium"] is True
    assert row["aggressive_buy"] is True
    assert row["sweep"] is True
    assert row["alert"] is True
    text = render_markdown(scored)
    assert "SPY240607C00520000" in text
    assert "Orders submitted: 0" in text


def test_skew_shift_is_applied_only_to_short_dated_otm():
    session = date(2024, 6, 3)
    contracts = [
        _contract(symbol="SPY240607P00460000", right="put", strike=460, expiration=date(2024, 6, 7), iv=0.30, volume=10, open_interest=None),
        _contract(symbol="SPY240607P00470000", right="put", strike=470, expiration=date(2024, 6, 7), iv=0.30, volume=10, open_interest=None),
        _contract(symbol="SPY240607C00520000", right="call", strike=520, expiration=date(2024, 6, 7), iv=0.20, volume=10, open_interest=None),
        _contract(symbol="SPY240607C00530000", right="call", strike=530, expiration=date(2024, 6, 7), iv=0.20, volume=10, open_interest=None),
        _contract(symbol="SPY241220P00460000", right="put", strike=460, expiration=date(2024, 12, 20), iv=0.30, volume=10, open_interest=None),
    ]
    history = [{"kind": "skew", "session_date": "2024-06-02", "underlying": "SPY", "skew": 0.05}]
    scored = score_book(contracts, history, feed="indicative", session_date=session)
    by_symbol = {row["symbol"]: row for row in scored["contracts"]}
    assert by_symbol["SPY240607P00460000"]["skew_shift"] == pytest.approx(0.05)
    assert "skew_shift" in by_symbol["SPY240607P00460000"]["flags"]
    assert "skew_shift" not in by_symbol["SPY241220P00460000"]["flags"]
    assert scored["underlyings"][0]["skew_shift"] == pytest.approx(0.05)


def test_assemble_chain_joins_open_interest_and_tape():
    views = assemble_chain(
        underlying="SPY",
        session_date=date(2024, 6, 3),
        spot=500.0,
        snapshots={
            "snapshots": {
                "SPY240607C00520000": {
                    "dailyBar": {"v": 250},
                    "latestQuote": {"bp": 1.0, "ap": 1.2},
                    "latestTrade": {"p": 1.2, "s": 3, "t": "2024-06-03T14:00:00Z"},
                    "greeks": {"delta": 0.1},
                    "impliedVolatility": 0.2,
                },
                "NOTANOPTION": {},
            }
        },
        contracts={
            "option_contracts": [
                {"symbol": "SPY240607C00520000", "open_interest": 80, "open_interest_date": "2024-06-03"}
            ]
        },
        trades={
            "trades": {
                "SPY240607C00520000": [
                    {"p": 1.25, "s": 10, "t": "2024-06-03T14:30:00Z"},
                ]
            }
        },
    )
    assert len(views) == 1
    assert views[0].open_interest == 80
    assert views[0].volume == 250
    assert len(views[0].prints) == 1
    assert views[0].prints[0].ask == 1.2


def test_event_study_uses_forward_closes_and_drops_a_short_horizon():
    start = date(2024, 1, 2)
    rows = [_bar("SPY", start + timedelta(days=index), close) for index, close in enumerate(["100", "110", "121"])]
    bars = {"SPY": rows}
    events = [
        {"date": "2024-01-02", "symbol": "SPY", "score": 3, "source": "options_flow"},
        {"date": "2024-01-02", "symbol": "SPY", "score": 4, "source": "options_flow"},
    ]
    assert session_return(rows, start, 1) == pytest.approx(0.1)
    report = event_study(bars, events)
    assert report["n_events_input"] == 1
    assert float(report["horizons"]["1"]["mean_return"]) == pytest.approx(0.1)
    assert report["horizons"]["5"]["n_events"] == 0
    assert report["n_trials"] == 2
    wrapped = flow_event_report(bars, events, source="options_flow")
    assert wrapped["market_question_answered"] is True
    assert wrapped["orders_submitted"] == 0


def test_calendar_null_is_not_a_flow_claim():
    from sidecar.harness.backtest import load_bars
    from sidecar.flow.study import DEFAULT_BARS

    bars = load_bars(DEFAULT_BARS)
    events = calendar_null_events(bars)
    report = flow_event_report(bars, events, source="calendar_null")
    assert report["market_question_answered"] is False
    assert report["event_source"] == "calendar_null"
    assert report["horizons"]["1"]["n_events"] >= 8
    assert report["horizons"]["1"]["eval"]["n_trials"] == 2
    assert "unusual options activity" in report["reason"]
    committed = json.loads((Path(__file__).resolve().parents[1] / "examples" / "flow_event_study.json").read_text())
    assert committed["market_question_answered"] is False
    assert report["events_sha256"] == committed["events_sha256"]
    assert report["horizons"]["1"]["eval"]["psr"] == committed["horizons"]["1"]["eval"]["psr"]
    assert report["horizons"]["5"]["eval"]["dsr"] == committed["horizons"]["5"]["eval"]["dsr"]


def test_trades_omit_feed_and_a_403_stays_in_the_scan(tmp_path: Path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/v2/calendar"):
            return httpx.Response(
                200,
                json=[
                    {"date": "2026-10-16", "open": "09:30", "close": "16:00"},
                    {"date": "2026-10-19", "open": "09:30", "close": "16:00"},
                ],
            )
        if "/options/snapshots/" in path:
            return httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SPY261219C00600000": {
                            "dailyBar": {"v": 400},
                            "latestQuote": {"bp": 1.0, "ap": 1.2},
                            "latestTrade": {"p": 1.2, "s": 10, "t": "2026-10-16T14:00:00Z"},
                            "greeks": {"delta": 0.2},
                            "impliedVolatility": 0.2,
                        }
                    },
                    "next_page_token": None,
                },
            )
        if path.endswith("/v2/options/contracts"):
            assert request.url.params.get("expiration_date_gte")
            assert request.url.params.get("expiration_date_lte")
            if request.url.params.get("page_token") == "next":
                return httpx.Response(
                    200,
                    json={
                        "option_contracts": [
                            {
                                "symbol": "SPY261219C00600000",
                                "open_interest": "1500",
                                "open_interest_date": "2026-10-16",
                            }
                        ],
                        "page_token": None,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "option_contracts": [
                        {"symbol": "SPY261219P00500000", "open_interest": "10", "open_interest_date": "2026-10-16"}
                    ],
                    "page_token": "next",
                },
            )
        if path.endswith("/v2/stocks/snapshots"):
            return httpx.Response(200, json={"SPY": {"latestTrade": {"p": 500}}})
        if path.endswith("/options/trades"):
            assert "feed" not in request.url.params
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(404, json={})

    from sidecar.flow.__main__ import run_scan

    client = FlowDataClient("key", "secret", transport=httpx.MockTransport(handler))
    scan = run_scan(
        feed="indicative",
        out=tmp_path / "scan.json",
        markdown=tmp_path / "scan.md",
        history_path=tmp_path / "history.jsonl",
        symbols=("SPY",),
        today=date(2026, 10, 18),
        client=client,
    )
    assert scan["session_date"] == "2026-10-16"
    assert scan["tape"] == "snapshot_only"
    assert "403" in scan["tape_reason"]
    text = (tmp_path / "scan.md").read_text(encoding="utf-8")
    assert "snapshot" in text.lower() or "403" in text
    row = next(item for item in scan["contracts"] if item["symbol"] == "SPY261219C00600000")
    assert row["open_interest"] == 1500
    trade_calls = [call for call in calls if call.url.path.endswith("/options/trades")]
    assert len(trade_calls) == 1


def test_trade_batches_stay_within_100_symbols():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["symbols"])
        assert "feed" not in request.url.params
        return httpx.Response(200, json={"trades": {}, "next_page_token": None})

    client = FlowDataClient("key", "secret", transport=httpx.MockTransport(handler))
    symbols = [f"SPY261219C{index:08d}" for index in range(101)]
    result = client.trades(symbols, start="2026-10-16T13:30:00+00:00", end="2026-10-16T20:00:00+00:00")
    client.close()
    assert result["available"] is True
    assert result["batches"] == 2
    assert all(len(batch.split(",")) <= 100 for batch in seen)


def test_last_trading_session_skips_the_weekend_and_a_holiday():
    from sidecar.flow.session import expiration_bounds, last_trading_session

    weekend = [
        {"date": "2026-10-16", "open": "09:30", "close": "16:00"},
        {"date": "2026-10-19", "open": "09:30", "close": "16:00"},
    ]
    assert last_trading_session(weekend, date(2026, 10, 18))["date"] == "2026-10-16"
    assert last_trading_session(weekend, date(2026, 10, 19))["date"] == "2026-10-19"
    holiday = [{"date": "2026-10-09"}, {"date": "2026-10-13"}]
    assert last_trading_session(holiday, date(2026, 10, 12))["date"] == "2026-10-09"
    assert expiration_bounds(["SPY261219C00600000", "NOT"], date(2026, 10, 16)) == ("2026-10-16", "2026-12-19")


def test_events_from_scan_keep_the_highest_score():
    scored = score_book(
        [
            _contract(volume=300, open_interest=100),
            _contract(symbol="SPY240607C00530000", strike=530, volume=10, open_interest=None),
        ],
        [],
        feed="indicative",
        session_date=date(2024, 6, 3),
    )
    events = events_from_scan(scored)
    assert len(events) == 1
    assert events[0]["symbol"] == "SPY"
    assert events[0]["source"] == "options_flow"
