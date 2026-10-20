"""CSL-192 — Vibe-Trading research sidecar. Fixtures only; no network.

The adapter is optional. These tests cover the decision-packet features, the
context cap, the cost audit row, and the guarantee that broker tools and a
staged signal engine cannot reach an order path.
"""
from __future__ import annotations

import ast
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from config.settings import Mode, Settings
from core.clock import FrozenClock
from ingest.normalize import ALLOWED_SOURCES, python_to_market_event, to_market_event
from main import build_ingest_runner
from orchestrator.ingest_runner import FEED_VIBE_TRADING, IngestRunner
from orchestrator.scheduler import INGEST_CRON
from orchestrator.state import OrchestratorState
from research.vibe_trading.adapter import ResearchAdapter, ResearchRequest
from research.vibe_trading.features import (
    categorise,
    normalize_run_card,
    project_alpha_bench,
    project_contracts,
    project_etf,
    project_holdings,
    project_run_card,
    to_events,
    _finite,
)
from research.vibe_trading.mcp_client import (
    SidecarUnavailable,
    StdioMcpClient,
    _child_env,
)
from research.vibe_trading.poller import VibeTradingPoller
from research.vibe_trading.policy import (
    RESEARCH_TOOLS,
    SidecarToolDenied,
    assert_callable,
    is_trading_tool,
    prepare_arguments,
)
from schemas.models.audit_entry import AuditEntry
from storage.engine import create_all, make_engine, make_session_factory
from workflows.context_limit import estimate_tokens
from workflows.events import events_in_window, resolve_raw
from workflows.packets import build_packet

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
APP = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _settings(**overrides) -> Settings:
    values = {
        "vibe_trading_enabled": True,
        "watchlist": "AAPL,MSFT",
        "vibe_trading_etfs": "SPY",
        "vibe_trading_event_queries": "Fed cut",
        "vibe_trading_token_budget": 4000,
    }
    values.update(overrides)
    return Settings(**values)


class FakeSidecar:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        payload = self.responses.get(tool, {"status": "error"})
        if callable(payload):
            return payload(arguments)
        return payload


def _responses():
    bench = _load("alpha_bench.json")
    etf = _load("etf.json")
    prediction = _load("prediction.json")

    def holdings(arguments):
        payload = json.loads(json.dumps(_load("holdings.json")))
        ticker = arguments.get("ticker")
        if type(ticker) is str:
            payload["data"]["ticker"] = ticker
        return payload

    return {
        "alpha_bench": bench,
        "get_institutional_holdings": holdings,
        "etf_holdings": etf,
        "prediction_market": prediction,
    }


def _state(tmp_path, settings, name="vibe.db"):
    engine = make_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    create_all(engine)
    factory = make_session_factory(engine)
    return OrchestratorState.build(
        factory, hmac_key=b"csl-192-vibe", settings=settings, clock=FrozenClock(NOW),
    )


@pytest.fixture
def state(tmp_path):
    return _state(tmp_path, _settings())


def test_source_is_allowlisted_and_off_by_default():
    assert "vibe-trading" in ALLOWED_SOURCES
    assert Settings().vibe_trading_enabled is False


def test_vibe_trading_stays_on_the_python_normalizer_when_rust_is_loaded(monkeypatch):
    """The installed wheel's allowlist does not include this source yet."""
    from ingest import normalize

    class Native:
        def market_event(self, source, article, symbols, headline, summary, event_type, raw):
            if source == "vibe-trading":
                raise AssertionError("sidecar entered the rust normalizer")
            return (
                "news", ["AAPL"], "h", "s", f"{source}:{article}", "deadbeef",
                '{"headline": "h", "summary": "s"}',
            )

    monkeypatch.setattr(normalize, "_NATIVE", Native())
    kwargs = dict(
        source="vibe-trading", article_id="signal:envelope:abc", symbols=[],
        headline="research_source", summary="", ts=None, event_type="signal:envelope",
        raw={"kind": "sidecar_envelope"},
    )
    assert to_market_event(**kwargs) == python_to_market_event(**kwargs)
    routed = to_market_event(
        source="finnhub", article_id="1", symbols=["AAPL"], headline="h",
        summary="s", ts=None,
    )
    assert routed["content_hash"] == "deadbeef"


def test_disabled_adapter_does_not_call_the_sidecar():
    transport = FakeSidecar(_responses())
    batch = ResearchAdapter(
        _settings(vibe_trading_enabled=False), transport=transport,
    ).collect()
    assert batch.status == "disabled"
    assert batch.events == ()
    assert transport.calls == []


def test_missing_binary_is_unavailable_and_does_not_raise(monkeypatch):
    monkeypatch.setenv("PATH", "/tmp/vibe-empty-path")
    batch = ResearchAdapter(_settings(vibe_trading_command="vibe-trading-mcp")).collect()
    assert batch.status == "unavailable"
    assert batch.events == ()
    assert batch.calls[0]["status"] == "unavailable"
    assert batch.calls[0]["usd"] is None


def test_features_land_in_a_decision_packet(state, tmp_path):
    transport = FakeSidecar(_responses())
    result = ResearchAdapter(_settings(), transport=transport).publish(
        state, parquet_root=tmp_path / "raw",
    )
    assert result.status == "ok"
    assert result.stored
    window = events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(seconds=1))
    packet = build_packet(window)
    text = packet.prompt_block()
    assert text.count("</untrusted-content>") == len(packet.items)
    assert "ic_mean=0.041000" in text
    assert "category=alive category_source=recomputed" in text
    assert "category=reversed category_source=recomputed" in text
    assert "alive=1 reversed=1 dead=1" in text
    assert "counts_scope=returned_top" in text
    assert "survivorship_bias=true" in text
    assert "13f ticker=AAPL" in text
    assert "shares=1200" in text
    assert "etf=SPY" in text
    assert "AAPL:0.0700" in text
    assert "ZZZZ" not in text
    assert "implied_probability=0.4200" in text
    assert "submit_order" not in text
    assert "[role-marker]" in text
    assert all(row.event_type.startswith("signal:") for row in window)
    assert all(row.source == "vibe-trading" for row in window)
    assert all(
        not event.headline.startswith("alpha_") or event.symbol == ""
        for event in window
    )
    tools = [tool for tool, _args in transport.calls]
    assert tools == [call[0] for call in ResearchAdapter(_settings()).plan()]
    assert "backtest" not in tools
    assert "trading_orders" not in tools
    assert all(row.raw_ref.startswith("parquet://") for row in result.stored)


def test_raw_payload_is_kept_out_of_the_headline():
    batch = ResearchAdapter(_settings(), transport=FakeSidecar(_responses())).collect(
        clock=FrozenClock(NOW),
    )
    summary = next(event for event in batch.events if event["headline"].startswith("alpha_bench"))
    assert "submit_order" not in summary["headline"]
    assert "source_payload" not in summary["raw_input"]
    envelope = next(
        event for event in batch.events
        if event["event_type"] == "signal:envelope" and "BUY AAPL" in json.dumps(event["raw_input"])
    )
    assert summary["raw_input"]["source_payload_sha256"] == envelope["raw_input"]["sha256"]


def test_trim_lines_keeps_a_fitting_prefix_of_a_long_list():
    import time

    from research.vibe_trading.features import FeatureLine, _wrapped, trim_lines

    lines = [
        FeatureLine(
            priority=(1, index, "a"),
            text=f"alpha factor={index} " + ("x" * 40),
            event_type="signal:alpha",
            symbols=(),
            raw={"kind": "alpha"},
            tool="alpha_bench",
        )
        for index in range(3000)
    ]
    started = time.perf_counter()
    kept, dropped = trim_lines(lines, budget_tokens=400)
    assert time.perf_counter() - started < 2
    assert dropped == 3000 - len(kept)
    assert kept
    block = "\n\n".join(_wrapped(line) for line in kept)
    assert estimate_tokens(block) <= 400
    longer = block + "\n\n" + _wrapped(lines[len(kept)])
    assert estimate_tokens(longer) > 400


def test_token_budget_trims_instead_of_overflowing(tmp_path):
    rows = []
    for index in range(30):
        rows.append({
            "id": f"alpha101_{index:03d}",
            "ic_mean": 0.05 - index * 0.001,
            "ic_std": 0.01,
            "ir": 0.5,
            "ic_positive_ratio": 0.7,
            "ic_count": 100,
        })
    payload = {
        "status": "ok", "zoo": "alpha101", "universe": "sp500", "period": "2018-2025",
        "alive": 30, "reversed": 0, "dead": 0, "rows": rows,
    }
    transport = FakeSidecar({"alpha_bench": payload})
    settings = _settings(
        vibe_trading_etfs="", vibe_trading_event_queries="", watchlist="AAPL",
        vibe_trading_max_symbols=0, vibe_trading_token_budget=400,
    )
    result = ResearchAdapter(settings, transport=transport).publish(
        _state(tmp_path, settings), parquet_root=tmp_path / "raw",
    )
    packet = build_packet(result.stored)
    assert result.dropped > 0
    assert result.stored
    assert estimate_tokens(packet.prompt_block()) <= 400
    assert any(row.headline.startswith("alpha_bench") for row in result.stored)


def test_context_cap_overrides_a_larger_research_budget(tmp_path):
    payload = _load("alpha_bench.json")
    settings = _settings(
        vibe_trading_etfs="", vibe_trading_event_queries="", watchlist="",
        vibe_trading_max_symbols=0, vibe_trading_token_budget=4000,
        context_token_cap=80,
    )
    result = ResearchAdapter(
        settings, transport=FakeSidecar({"alpha_bench": payload}),
    ).publish(_state(tmp_path, settings), parquet_root=tmp_path / "raw")
    packet = build_packet(result.stored)
    assert estimate_tokens(packet.prompt_block()) <= 80


def test_sidecar_calls_are_audited_as_unpriced_cost(state, tmp_path):
    ResearchAdapter(_settings(), transport=FakeSidecar(_responses())).publish(
        state, parquet_root=tmp_path / "raw",
    )
    with state.session_factory() as session:
        rows = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "sidecar_call")
        ).scalars().all()
    assert rows
    payload = rows[0].payload
    assert payload["model_pinned"] == "vibe-trading-mcp"
    assert payload["input_token_source"] == "estimate"
    assert payload["usd"] is None
    assert payload["usd_scope"] == "unpriced"
    assert payload["rate_tier"] == "sidecar-local"
    assert payload["context_token_cap"] == 90_000
    assert payload["input_tokens"] >= 0
    assert payload["output_tokens"] >= 0


def test_etf_symbols_lead_with_a_watchlist_constituent():
    lines = project_etf(_load("etf.json"), etf="SPY", universe=frozenset({"AAPL"}))
    assert lines[0].symbols[0] == "AAPL"
    assert lines[0].symbols[-1] == "SPY"


def test_a_later_day_is_a_fresh_observation():
    lines = project_alpha_bench(
        _load("alpha_bench.json"), zoo="alpha101", universe="sp500", period="2018-2025",
    )
    def feature(events):
        return next(event for event in events if event["event_type"] != "signal:envelope")

    first = to_events(lines, ts=NOW)
    later = to_events(lines, ts=NOW + timedelta(days=1))
    assert feature(first)["article_id"] != feature(later)["article_id"]
    assert feature(first)["raw_input"]["observed_on"] == "2026-07-07"
    assert feature(later)["raw_input"]["observed_on"] == "2026-07-08"
    assert feature(to_events(lines, ts=NOW))["article_id"] == feature(first)["article_id"]
    first_env = next(event for event in first if event["event_type"] == "signal:envelope")
    later_env = next(event for event in later if event["event_type"] == "signal:envelope")
    assert first_env["article_id"] == later_env["article_id"]


def test_missing_collection_is_quarantined_and_an_empty_list_is_ok():
    payload = _load("holdings.json")
    assert project_holdings(payload, ticker="AAPL")
    empty = json.loads(json.dumps(payload))
    empty["data"]["holders"] = []
    assert project_holdings(empty, ticker="AAPL") == []
    missing = json.loads(json.dumps(payload))
    del missing["data"]["holders"]
    assert project_holdings(missing, ticker="AAPL") is None
    missing["data"]["holders"] = {"manager": "nope"}
    assert project_holdings(missing, ticker="AAPL") is None


def test_every_malformed_holder_is_quarantined():
    payload = _load("holdings.json")
    payload["data"]["holders"] = [
        {"manager": "No Size"},
        "not-a-row",
        {"manager": "   ", "shares": 10, "value_usd": 1},
        {"manager": "x" * 81, "shares": 10},
        {"shares": 10, "value_usd": 1},
    ]
    assert project_holdings(payload, ticker="AAPL") is None
    payload["data"]["holders"].append({
        "manager": "Example Capital", "shares": 12, "value_usd": 34,
    })
    lines = project_holdings(payload, ticker="AAPL")
    assert lines is not None and len(lines) == 1
    assert "manager=Example Capital" in lines[0].text
    etf = _load("etf.json")
    assert project_etf(etf, etf="SPY", universe=frozenset({"AAPL"}))
    del etf["data"]["holdings"]
    assert project_etf(etf, etf="SPY", universe=frozenset({"AAPL"})) is None
    contracts = _load("prediction.json")
    contracts["data"]["events"] = "nope"
    assert project_contracts(contracts, query="Fed cut") is None


def test_etf_scan_includes_a_watchlist_name_past_the_old_cap():
    holdings = [{"ticker": f"Z{index:03d}", "pct_of_net_assets": 0.1} for index in range(70)]
    holdings.append({"ticker": "AAPL", "pct_of_net_assets": 1.5})
    payload = {
        "ok": True, "as_of": "2026-06-30", "data": {"symbol": "SPY", "holdings": holdings},
    }
    lines = project_etf(payload, etf="SPY", universe=frozenset({"AAPL"}))
    assert lines[0].symbols[0] == "AAPL"
    assert "outside_universe=70" in lines[0].text
    payload["as_of"] = "2026-99-99"
    assert project_etf(payload, etf="SPY", universe=frozenset({"AAPL"})) is None
    payload["as_of"] = "----------"
    assert project_etf(payload, etf="SPY", universe=frozenset({"AAPL"})) is None


def test_later_day_row_resolves_the_deduped_envelope(tmp_path):
    from workflows.events import persist_events, resolve_raw

    from research.vibe_trading.poller import stamp_source_refs

    settings = _settings(
        watchlist="", vibe_trading_max_symbols=0, vibe_trading_etfs="",
        vibe_trading_event_queries="",
    )
    state = _state(tmp_path, settings, name="envelope.db")
    root = tmp_path / "raw"
    adapter = ResearchAdapter(
        settings, transport=FakeSidecar({"alpha_bench": _load("alpha_bench.json")}),
    )
    first = adapter.collect(clock=FrozenClock(NOW))
    envelopes = [event for event in first.events if event["event_type"] == "signal:envelope"]
    persist_events(state, envelopes, parquet_root=root, clock=FrozenClock(NOW))
    persisted = persist_events(
        state, stamp_source_refs(state, first.events), parquet_root=root, clock=FrozenClock(NOW),
    )
    assert persisted.stored
    later = adapter.collect(clock=FrozenClock(NOW + timedelta(days=1)))
    later_envelopes = [event for event in later.events if event["event_type"] == "signal:envelope"]
    duplicate = persist_events(
        state, later_envelopes, parquet_root=root, clock=FrozenClock(NOW + timedelta(days=1)),
    )
    assert duplicate.stored == []
    assert duplicate.duplicates >= 1
    stamped = stamp_source_refs(state, later.events)
    second = persist_events(
        state, stamped, parquet_root=root, clock=FrozenClock(NOW + timedelta(days=1)),
    )
    assert second.stored
    window = events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(days=2))
    envelopes_stored = [row for row in window if row.event_type == "signal:envelope"]
    assert len(envelopes_stored) == 1
    factor = next(row for row in second.stored if row.headline.startswith("alpha_factor"))
    image = resolve_raw(root, factor.raw_ref, state=state, row=factor)
    source = resolve_raw(root, image["raw_input"]["source_ref"], state=state, row=envelopes_stored[0])
    assert "BUY AAPL" in json.dumps(source["raw_input"]["source_payload"])
    assert image["raw_input"]["source_ref"] == envelopes_stored[0].raw_ref


def test_duplicate_envelope_rows_resolve_deterministically(tmp_path):
    from sqlalchemy.orm import make_transient
    from workflows.events import persist_events

    from research.vibe_trading.poller import _raw_ref_for_hash

    settings = _settings(
        watchlist="", vibe_trading_max_symbols=0, vibe_trading_etfs="",
        vibe_trading_event_queries="",
    )
    state = _state(tmp_path, settings, name="dupenv.db")
    adapter = ResearchAdapter(
        settings, transport=FakeSidecar({"alpha_bench": _load("alpha_bench.json")}),
    )
    batch = adapter.collect(clock=FrozenClock(NOW))
    envelope = next(e for e in batch.events if e["event_type"] == "signal:envelope")
    stored = persist_events(state, [envelope], parquet_root=tmp_path / "raw", clock=FrozenClock(NOW))
    row = stored.stored[0]
    with state.session_factory() as session:
        clone = session.get(type(row), row.id)
        make_transient(clone)
        clone.id = "evt_concurrent_copy"
        clone.raw_ref = "parquet://zz-concurrent-copy"
        session.add(clone)
        session.commit()
    assert _raw_ref_for_hash(state, envelope["content_hash"]) == min(
        row.raw_ref, "parquet://zz-concurrent-copy",
    )


def test_impossible_contract_dates_are_not_published():
    payload = {
        "ok": True,
        "data": {"query": "Fed cut", "events": [{
            "event_id": "fed-cut-2026",
            "title": "Fed cut by December",
            "end_date": "2026-99-99",
            "implied_probability": 0.42,
        }]},
    }
    lines = project_contracts(payload, query="Fed cut")
    assert lines
    assert "2026-99-99" not in lines[0].text
    assert "as_of=" in lines[0].text


def test_trimmed_and_empty_payloads_still_store_an_envelope(tmp_path):
    settings = _settings(
        watchlist="", vibe_trading_max_symbols=0, vibe_trading_etfs="",
        vibe_trading_event_queries="Fed cut", vibe_trading_token_budget=1,
    )
    empty = {"ok": True, "data": {"query": "Fed cut", "events": []}}
    transport = FakeSidecar({
        "alpha_bench": _load("alpha_bench.json"),
        "prediction_market": empty,
    })
    state = _state(tmp_path, settings, name="empty-envelope.db")
    result = ResearchAdapter(settings, transport=transport).publish(
        state, parquet_root=tmp_path / "raw",
    )
    assert result.status == "ok"
    assert result.stored == ()
    window = events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(seconds=1))
    envelopes = [row for row in window if row.event_type == "signal:envelope"]
    assert len(envelopes) == 2
    from workflows.events import resolve_raw
    images = [
        resolve_raw(tmp_path / "raw", row.raw_ref, state=state, row=row)["raw_input"]["source_payload"]
        for row in envelopes
    ]
    assert empty in images
    assert any(image.get("zoo") == "alpha101" for image in images)


def test_brew_does_not_packetize_source_envelopes():
    from workflows.morning_brew import partition_by_relevance

    class Row:
        def __init__(self, event_type, symbol=""):
            self.event_type = event_type
            self.symbol = symbol

    archive = Row("signal:envelope")
    feature = Row("signal:alpha", "AAPL")
    eligible, ignored = partition_by_relevance(
        [archive, feature], frozenset(), frozenset({"AAPL"}),
    )
    assert feature in eligible
    assert archive in ignored


def test_republish_dedupes_the_same_feature(state, tmp_path):
    adapter = ResearchAdapter(_settings(), transport=FakeSidecar(_responses()))
    first = adapter.publish(state, parquet_root=tmp_path / "raw")
    second = adapter.publish(state, parquet_root=tmp_path / "raw")
    assert first.stored
    assert second.stored == ()


def test_plan_ignores_execution_mode():
    shared = {
        "vibe_trading_enabled": True,
        "watchlist": "MSFT,AAPL",
        "vibe_trading_etfs": "SPY",
        "vibe_trading_event_queries": "Fed cut|Rates",
    }
    paper = ResearchAdapter(_settings(mode=Mode.paper, **shared)).plan()
    autonomous = ResearchAdapter(_settings(mode=Mode.auto_full, **shared)).plan()
    assert paper == autonomous
    assert [call[0] for call in paper] == [
        "alpha_bench",
        "get_institutional_holdings",
        "get_institutional_holdings",
        "etf_holdings",
        "prediction_market",
        "prediction_market",
    ]
    assert paper[1][1] == {"mode": "ticker_holders", "ticker": "AAPL"}
    assert paper[2][1] == {"mode": "ticker_holders", "ticker": "MSFT"}
    assert paper[3][1] == {"mode": "holdings", "symbol": "SPY"}
    assert paper[4][1]["mode"] == "search"
    assert "action" not in paper[4][1]


def test_trading_tools_are_denied_before_any_transport():
    class Exploding:
        def call(self, tool, arguments):
            raise AssertionError(f"transport saw {tool}")

    denied = (
        "trading_orders", "trading_account", "trading_positions", "trading_history",
        "write_file", "run_swarm", "read_file", "broker_submit",
        "etoro_place_order", "etoro_execute_write",
    )
    for name in denied:
        assert is_trading_tool(name)
        with pytest.raises(SidecarToolDenied):
            prepare_arguments(name, {})
        payload, attempt = ResearchAdapter(_settings(), transport=Exploding())._invoke(name, {})
        assert payload is None
        assert attempt.status == "denied"
    with pytest.raises(SidecarToolDenied):
        StdioMcpClient("this-binary-does-not-exist").call("trading_orders", {})


def test_allowlist_cannot_reenable_a_trading_tool(monkeypatch):
    monkeypatch.setattr(
        "research.vibe_trading.policy.RESEARCH_TOOLS",
        RESEARCH_TOOLS | {"trading_orders"},
    )
    with pytest.raises(SidecarToolDenied):
        assert_callable("trading_orders")


@pytest.mark.parametrize("engine", [
    "from brokers.gateway import OrderGateway\n",
    "import importlib\nimportlib.import_module('brokers.gateway')\n",
    "getattr(mod, 'place_' + 'order')\n",
    "exec('place_order()')\n",
    "import keyring\n",
    "import os\n",
    "import socket\n",
])
def test_backtest_refuses_an_engine_that_imports_the_order_path(tmp_path, engine):
    run = _stage_run(tmp_path, engine=engine)
    transport = FakeSidecar({"backtest": {"status": "ok", "run_dir": str(run)}})
    batch = ResearchAdapter(_settings(), transport=transport).collect(
        ResearchRequest(strategy_id="idea-1", run_root=str(tmp_path)),
        clock=FrozenClock(NOW),
    )
    assert "backtest" not in [tool for tool, _args in transport.calls]
    assert any(call["tool"] == "backtest" and call["status"] == "denied" for call in batch.calls)
    assert not any(event["event_type"] == "signal:run_card" for event in batch.events)


def test_point_in_time_run_card_never_admits_an_order():
    sidecar = _load("run_card.json")
    card = normalize_run_card(sidecar, config_point_in_time=True)
    assert card is not None
    line = project_run_card(
        card, strategy_id="idea-1", symbols=("AAPL",), sidecar_card=sidecar,
    )
    assert "admits_order=false" in line.text
    assert "side" not in line.text
    assert "buy" not in line.text
    assert line.raw["sidecar_card"]["admits_order"] is True


def test_staged_backtest_is_not_executed_and_leftover_cards_are_not_published(tmp_path):
    run = _stage_run(tmp_path, engine="def signal(panel):\n    return panel\n")
    (run / "run_card.json").write_text(json.dumps(_load("run_card.json")), encoding="utf-8")
    transport = FakeSidecar({
        **_responses(),
        "backtest": {"status": "ok", "run_dir": str(run), "side": "buy"},
    })
    batch = ResearchAdapter(
        _settings(vibe_trading_etfs="", vibe_trading_event_queries="", vibe_trading_max_symbols=0),
        transport=transport,
    ).collect(ResearchRequest(strategy_id="idea-1", run_root=str(tmp_path)), clock=FrozenClock(NOW))
    assert "backtest" not in [tool for tool, _args in transport.calls]
    assert any(call["tool"] == "backtest" and call["status"] == "denied" for call in batch.calls)
    assert not any(event["event_type"] == "signal:run_card" for event in batch.events)


def test_run_card_without_point_in_time_is_quarantined(tmp_path):
    _stage_run(tmp_path, engine="def signal(panel):\n    return panel\n", point_in_time=False)
    transport = FakeSidecar({"backtest": {"status": "ok"}})
    batch = ResearchAdapter(
        _settings(watchlist="", vibe_trading_etfs="", vibe_trading_event_queries="",
                  vibe_trading_max_symbols=0),
        transport=transport,
    ).collect(ResearchRequest(strategy_id="idea-1", run_root=str(tmp_path)))
    assert "backtest" not in [tool for tool, _args in transport.calls]
    assert batch.calls[-1]["tool"] == "backtest"
    assert batch.calls[-1]["status"] == "quarantined"


def test_package_does_not_import_the_order_path():
    package = APP / "research" / "vibe_trading"
    offenders = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = [node.module.split(".")[0]]
            else:
                continue
            if any(root in {"brokers", "risk"} for root in roots):
                offenders.append(str(path.relative_to(APP)))
        text = path.read_text(encoding="utf-8")
        assert "place_order(" not in text
        assert "submit_order(" not in text
    assert offenders == []


def test_stdio_client_roundtrip_and_scrubbed_env(tmp_path, monkeypatch):
    script = tmp_path / "vibe-trading-mcp"
    script.write_text(_MCP_SERVER, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("COCKPIT_WATCHLIST", "AAPL")
    monkeypatch.setenv("ALPACA_API_KEY", "should-not-pass")
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path / "runs"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "cockpit-tmp"))
    env = _child_env()
    assert "COCKPIT_WATCHLIST" not in env
    assert "ALPACA_API_KEY" not in env
    assert "HOME" not in env
    assert "TMPDIR" not in env
    assert env["VIBE_TRADING_ALLOWED_RUN_ROOTS"] == str(tmp_path / "runs")
    payload = StdioMcpClient("vibe-trading-mcp", timeout_s=5).call(
        "alpha_zoo", {"action": "health"},
    )
    assert payload == {"status": "ok", "tool": "alpha_zoo"}


def test_stdio_client_rejects_a_path_command():
    with pytest.raises(SidecarUnavailable):
        StdioMcpClient("/tmp/vibe-trading-mcp").call("alpha_zoo", {"action": "health"})


def test_settings_reject_a_shell_command():
    with pytest.raises(ValueError):
        _settings(vibe_trading_command="vibe-trading-mcp && true")


@pytest.mark.parametrize("period", [
    "2018-2025",
    "2024-02-29/2024-03-01",
    "2026-03-01/2026-03-01",
])
def test_bench_period_accepts_a_real_range(period):
    assert _settings(vibe_trading_bench_period=period).vibe_trading_bench_period == period


@pytest.mark.parametrize("period", [
    "2026-02-30/2026-03-01",
    "2025-02-29/2025-03-01",
    "2026-13-01/2026-13-02",
    "2026-03-02/2026-03-01",
    "2025-2018",
])
def test_bench_period_rejects_impossible_or_reversed_dates(period):
    with pytest.raises(ValueError, match="bench period"):
        _settings(vibe_trading_bench_period=period)


def test_publish_without_parquet_root_does_not_persist(state):
    result = ResearchAdapter(_settings(), transport=FakeSidecar(_responses())).publish(state)
    assert result.stored == ()
    assert events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(seconds=1)) == []


def test_published_refs_are_groundable(state, tmp_path):
    root = tmp_path / "raw"
    result = ResearchAdapter(_settings(), transport=FakeSidecar(_responses())).publish(
        state, parquet_root=root,
    )
    assert result.stored
    for row in result.stored:
        assert row.raw_ref.startswith("parquet://")
        assert resolve_raw(root, row.raw_ref, state=state, row=row)


def test_alpha_bench_ignores_envelope_zoo_counts():
    payload = {
        "status": "ok",
        "zoo": "alpha101",
        "universe": "sp500",
        "period": "2018-2025",
        "alive": 99,
        "reversed": 98,
        "dead": 97,
        "n_alphas_tested": 101,
        "top": [{
            "id": "alpha101_006",
            "ic_mean": 0.041,
            "ic_std": 0.01,
            "ir": 0.82,
            "ic_positive_ratio": 0.7,
            "ic_count": 100,
            "category": "dead",
        }],
    }
    lines = project_alpha_bench(
        payload, zoo="alpha101", universe="sp500", period="2018-2025",
        symbols=("AAPL", "MSFT"),
    )
    summary = lines[0].text
    assert "alive=1 reversed=0 dead=0 tested=101" in summary
    assert "counts_scope=returned_top" in summary
    assert "survivorship_bias=true" in summary
    assert "99" not in summary
    assert all(line.symbols == () for line in lines)


def test_categorise_skips_overflowing_numbers():
    assert _finite(10 ** 1000) is None
    assert categorise({"ic_mean": 10 ** 1000}) is None
    assert categorise({"ic_mean": float("inf")}) is None
    assert categorise({
        "ic_mean": 0.05, "ic_std": 0.01, "ic_positive_ratio": 0.7, "ic_count": 10 ** 400,
    }) is None
    assert categorise({
        "ic_mean": 0.05, "ic_std": 5e-324, "ic_positive_ratio": 0.7, "ic_count": 10 ** 300,
    }) is None


def test_categorise_subnormal_std_is_not_dead():
    row = {"ic_mean": 0.05, "ic_std": 5e-324, "ic_positive_ratio": 0.7, "ic_count": 100}
    assert categorise(row) == ("alive", "recomputed")
    assert categorise({**row, "ic_mean": -0.05}) == ("reversed", "recomputed")


def test_categorise_rejects_a_negative_std():
    assert categorise({
        "ic_mean": 0.05, "ic_std": -0.01, "ic_positive_ratio": 0.6, "ic_count": 100,
    }) is None


def test_categorise_large_finite_stats_do_not_overflow():
    assert categorise({
        "ic_mean": 2, "ic_std": 0.01, "ic_positive_ratio": 0.7, "ic_count": 100,
    }) is None
    assert categorise({
        "ic_mean": 0.05, "ic_std": 0.01, "ic_positive_ratio": 2, "ic_count": 100,
    }) is None
    row = {"ic_mean": 0.05, "ic_std": 1e308, "ic_positive_ratio": 0.7, "ic_count": 1000}
    assert categorise(row) == ("dead", "recomputed")


def test_collect_quarantines_an_overflowing_bench_row_without_aborting():
    payload = _load("alpha_bench.json")
    payload["top"] = [{
        "id": "alpha101_overflow",
        "ic_mean": 0.05,
        "ic_std": 0.01,
        "ic_positive_ratio": 0.7,
        "ic_count": 10 ** 400,
        "category": "alive",
    }]
    settings = _settings(
        watchlist="", vibe_trading_max_symbols=0, vibe_trading_etfs="",
        vibe_trading_event_queries="",
    )
    batch = ResearchAdapter(
        settings, transport=FakeSidecar({"alpha_bench": payload}),
    ).collect(clock=FrozenClock(NOW))
    assert batch.status in {"ok", "quarantined"}
    assert not any(event["headline"].startswith("alpha_factor") for event in batch.events)


def test_run_card_normalizes_mixed_timezone_windows():
    card = _load("run_card.json")
    card["tool_traces"][0]["started_at"] = "2026-01-01T00:00:00"
    card["tool_traces"][0]["ended_at"] = "2026-06-30T00:00:00-04:00"
    out = normalize_run_card(card, config_point_in_time=True)
    assert out is not None
    assert out["pit_source"] == "staged_config"


def test_run_card_inverted_mixed_timezone_is_quarantined_not_a_typeerror():
    card = _load("run_card.json")
    card["tool_traces"][0]["started_at"] = "2026-06-30T00:00:00-04:00"
    card["tool_traces"][0]["ended_at"] = "2026-06-30T00:00:00Z"
    assert normalize_run_card(card, config_point_in_time=True) is None


def test_run_card_without_explicit_pit_uses_staged_config():
    card = _load("run_card.json")
    assert "point_in_time" not in card
    assert normalize_run_card(card, config_point_in_time=True) is not None
    assert normalize_run_card(card, config_point_in_time=False) is None


def test_backtest_refuses_a_sibling_module_that_calls_getattr(tmp_path):
    run = _stage_run(tmp_path, engine="def signal(panel):\n    return panel\n")
    (run / "helper.py").write_text("getattr(__builtins__, 'exec')\n", encoding="utf-8")
    transport = FakeSidecar({"backtest": {"status": "ok", "run_dir": str(run)}})
    batch = ResearchAdapter(
        _settings(watchlist="", vibe_trading_etfs="", vibe_trading_event_queries="",
                  vibe_trading_max_symbols=0),
        transport=transport,
    ).collect(ResearchRequest(strategy_id="idea-1", run_root=str(tmp_path)))
    assert "backtest" not in [tool for tool, _args in transport.calls]
    assert any(call["tool"] == "backtest" and call["status"] == "denied" for call in batch.calls)


def test_collect_skips_remaining_tools_after_the_pass_deadline(monkeypatch):
    now = [1_000.0]
    monkeypatch.setattr("research.vibe_trading.adapter.time.monotonic", lambda: now[0])

    class Slow:
        def call(self, tool, arguments):
            now[0] += 10
            return {"status": "ok", "top": []}

    settings = _settings(vibe_trading_timeout_seconds=1)
    batch = ResearchAdapter(settings, transport=Slow()).collect()
    statuses = [call["status"] for call in batch.calls]
    assert statuses[0] in {"ok", "quarantined"}
    assert "unavailable" in statuses[1:]


def test_build_ingest_runner_registers_vibe_only_when_enabled(tmp_path):
    enabled = _settings()
    disabled = _settings(vibe_trading_enabled=False)
    on = build_ingest_runner(
        _state(tmp_path, enabled, name="on.db"),
        parquet_root=tmp_path / "raw-on",
        fetchers={},
        settings=enabled,
    )
    off = build_ingest_runner(
        _state(tmp_path, disabled, name="off.db"),
        parquet_root=tmp_path / "raw-off",
        fetchers={},
        settings=disabled,
    )
    assert FEED_VIBE_TRADING in on.polls
    assert FEED_VIBE_TRADING not in off.polls
    assert INGEST_CRON[FEED_VIBE_TRADING]["hour"] == 8


def test_etf_percent_weights_are_always_divided_by_one_hundred():
    from research.vibe_trading.features import _holding_weight, project_etf

    assert _holding_weight({"pct_of_net_assets": 0.5329}) == pytest.approx(0.005329)
    assert _holding_weight({"pct_of_net_assets": 7.0}) == pytest.approx(0.07)
    payload = {
        "ok": True,
        "as_of": "2026-06-30",
        "data": {"symbol": "SPY", "holdings": [
            {"ticker": "AAPL", "pct_of_net_assets": 0.70},
        ]},
    }
    lines = project_etf(payload, etf="SPY", universe=frozenset({"AAPL"}))
    assert "AAPL:0.0070" in lines[0].text


def test_etf_mismatching_or_missing_symbol_is_quarantined():
    from research.vibe_trading.features import project_etf

    payload = _load("etf.json")
    universe = frozenset({"AAPL"})
    assert project_etf(payload, etf="SPY", universe=universe)
    assert project_etf(payload, etf="IVV", universe=universe) is None
    missing = json.loads(json.dumps(payload))
    missing["data"]["symbol"] = ""
    assert project_etf(missing, etf="SPY", universe=universe) is None
    del missing["data"]["symbol"]
    assert project_etf(missing, etf="SPY", universe=universe) is None


def test_holdings_mismatching_ticker_are_quarantined():
    from research.vibe_trading.features import project_holdings

    payload = _load("holdings.json")
    assert project_holdings(payload, ticker="MSFT") is None
    assert project_holdings(payload, ticker="AAPL")
    missing = json.loads(json.dumps(payload))
    del missing["data"]["ticker"]
    assert project_holdings(missing, ticker="AAPL") is None


def test_alpha_bench_mismatching_identity_is_quarantined():
    payload = _load("alpha_bench.json")
    lines = project_alpha_bench(
        payload, zoo="alpha101", universe="sp500", period="2018-2025",
    )
    assert lines
    assert "identity_zoo=echo" in lines[0].text
    assert "identity_universe=echo" in lines[0].text
    assert project_alpha_bench(
        payload, zoo="alpha101", universe="csi300", period="2018-2025",
    ) is None


def test_alpha_bench_accepts_the_upstream_envelope_without_echoed_identity():
    """Upstream run_alpha_bench omits zoo, universe, and period on the envelope."""
    payload = {
        "status": "ok",
        "report_path": "/tmp/alpha_bench.html",
        "n_alphas_tested": 1,
        "n_skipped": 0,
        "horizon": 1,
        "forward_return_sampling": "daily",
        "top": [{
            "id": "alpha101_006",
            "zoo": "alpha101",
            "theme": ["momentum"],
            "ic_mean": 0.041,
            "ic_std": 0.01,
            "ir": 0.82,
            "ic_positive_ratio": 0.7,
            "ic_count": 100,
        }],
    }
    lines = project_alpha_bench(
        payload, zoo="alpha101", universe="sp500", period="2018-2025",
    )
    assert lines
    assert "identity_zoo=row" in lines[0].text
    assert "identity_universe=request" in lines[0].text
    assert "identity_period=request" in lines[0].text
    assert "universe=sp500" in lines[0].text
    assert lines[1].raw["source_row"]["theme"] == ["momentum"]
    payload["top"][0]["zoo"] = "qlib158"
    assert project_alpha_bench(
        payload, zoo="alpha101", universe="sp500", period="2018-2025",
    ) is None


def test_projected_events_keep_the_sidecar_source_image():
    from ingest.normalize import content_hash

    holdings = project_holdings(_load("holdings.json"), ticker="AAPL")
    assert holdings[0].raw["source_row"]["cik"] == "0001067983"
    assert "source_payload" not in holdings[0].raw
    etf = project_etf(_load("etf.json"), etf="SPY", universe=frozenset({"AAPL"}))
    contracts = project_contracts(_load("prediction.json"), query="Fed cut")
    assert "SYSTEM:" in contracts[0].raw["source_row"]["title"]
    events = to_events([*holdings, *etf, *contracts], ts=NOW)
    for event in events:
        assert event["content_hash"] == content_hash(event["raw_input"])
    holding = next(event for event in events if event["event_type"] == "signal:13f")
    envelope = next(
        event for event in events
        if event["event_type"] == "signal:envelope"
        and event["raw_input"]["sha256"] == holding["raw_input"]["source_payload_sha256"]
    )
    assert envelope["raw_input"]["source_payload"]["paging"]["count"] == 1
    assert envelope["raw_input"]["source_payload"]["source"] == "sec_edgar_13f"
    assert "source_payload" not in holding["raw_input"]


def test_prediction_mismatching_or_missing_query_is_quarantined():
    from research.vibe_trading.features import project_contracts

    payload = _load("prediction.json")
    assert project_contracts(payload, query="Fed cut")
    assert project_contracts(payload, query="Rates") is None
    missing = json.loads(json.dumps(payload))
    del missing["data"]["query"]
    assert project_contracts(missing, query="Fed cut") is None


def test_valid_empty_prediction_keeps_the_batch_ok():
    settings = _settings(
        watchlist="", vibe_trading_max_symbols=0, vibe_trading_etfs="",
        vibe_trading_event_queries="Fed cut",
    )
    transport = FakeSidecar({
        "alpha_bench": _load("alpha_bench.json"),
        "prediction_market": {"ok": True, "data": {"query": "Fed cut", "events": []}},
    })
    batch = ResearchAdapter(settings, transport=transport).collect(clock=FrozenClock(NOW))
    assert batch.status == "ok"
    pred = next(call for call in batch.calls if call["tool"] == "prediction_market")
    assert pred["status"] == "ok"


def test_failed_run_card_status_is_quarantined():
    card = _load("run_card.json")
    assert "status" not in card
    assert normalize_run_card(card, config_point_in_time=True) is not None
    card["status"] = "failed"
    assert normalize_run_card(card, config_point_in_time=True) is None
    card["status"] = "ok"
    assert normalize_run_card(card, config_point_in_time=True) is not None


def test_malformed_backtest_trace_status_quarantines_the_card():
    card = _load("run_card.json")
    card["tool_traces"][0]["status"] = []
    assert normalize_run_card(card, config_point_in_time=True) is None


def test_outcome_odds_are_not_an_event_probability():
    from research.vibe_trading.features import project_contracts

    payload = {
        "ok": True,
        "data": {"query": "Fed cut", "events": [{
            "event_id": "fed-cut-2026",
            "title": "Fed cut by December",
            "end_date": "2026-07-01T00:00:00Z",
            "markets": [{"outcomes": [
                {"name": "No", "implied_probability": 0.60},
                {"name": "Yes", "implied_probability": 0.40},
            ]}],
        }]},
    }
    lines = project_contracts(payload, query="Fed cut")
    assert lines
    assert "implied_probability=" not in lines[0].text


def test_collect_retries_after_a_missing_binary_pass():
    class Flaky:
        def __init__(self):
            self.calls = 0

        def call(self, tool, arguments):
            self.calls += 1
            if self.calls == 1:
                from research.vibe_trading.mcp_client import SidecarUnavailable
                raise SidecarUnavailable("missing")
            return {"status": "ok", "top": []}

    settings = _settings(
        vibe_trading_etfs="", vibe_trading_event_queries="", watchlist="",
        vibe_trading_max_symbols=0,
    )
    adapter = ResearchAdapter(settings, transport=Flaky())
    first = adapter.collect()
    assert first.status == "unavailable"
    second = adapter.collect()
    assert second.status in {"ok", "quarantined"}


def test_poller_surfaces_an_unavailable_collection(tmp_path):
    settings = _settings()
    state = _state(tmp_path, settings, name="down.db")
    class Down:
        def call(self, tool, arguments):
            from research.vibe_trading.mcp_client import SidecarUnavailable
            raise SidecarUnavailable("missing")

    adapter = ResearchAdapter(settings, transport=Down())
    runner = IngestRunner(state, parquet_root=tmp_path / "raw", clock=FrozenClock(NOW))
    runner.add_poller(FEED_VIBE_TRADING, VibeTradingPoller(adapter, state=state))
    out = runner.poll(FEED_VIBE_TRADING)
    assert out["status"] == "error"
    assert "unavailable" in out["error"]


def test_poller_surfaces_a_partial_collection(tmp_path):
    settings = _settings(
        watchlist="", vibe_trading_max_symbols=0, vibe_trading_event_queries="",
    )
    state = _state(tmp_path, settings, name="partial.db")
    responses = _responses()

    class Mixed:
        def call(self, tool, arguments):
            if tool == "etf_holdings":
                from research.vibe_trading.mcp_client import SidecarTimeout
                raise SidecarTimeout("etf")
            payload = responses[tool]
            return payload(arguments) if callable(payload) else payload

    adapter = ResearchAdapter(settings, transport=Mixed())
    runner = IngestRunner(state, parquet_root=tmp_path / "raw", clock=FrozenClock(NOW))
    runner.add_poller(FEED_VIBE_TRADING, VibeTradingPoller(adapter, state=state))
    out = runner.poll(FEED_VIBE_TRADING)
    assert out["status"] == "error"
    assert "partial" in out["error"]
    assert out["emitted"] > 0
    assert runner.polls[FEED_VIBE_TRADING].emitted == out["emitted"]
    window = events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(seconds=1))
    assert window
    assert all(row.source == "vibe-trading" for row in window)


def test_poller_propagates_a_persist_failure(tmp_path):
    from workflows.events import persist_events

    settings = _settings(
        watchlist="", vibe_trading_max_symbols=0, vibe_trading_etfs="",
        vibe_trading_event_queries="",
    )
    state = _state(tmp_path, settings, name="persist-fail.db")
    failed = {"open": True}

    def flaky(db, events, **kwargs):
        headline = events[0].get("headline", "") if events else ""
        if failed["open"] and headline.startswith("alpha_factor"):
            failed["open"] = False
            raise OSError("disk full")
        return persist_events(db, events, **kwargs)

    adapter = ResearchAdapter(
        settings, transport=FakeSidecar({"alpha_bench": _load("alpha_bench.json")}),
    )
    runner = IngestRunner(
        state, parquet_root=tmp_path / "raw", clock=FrozenClock(NOW), persist_fn=flaky,
    )
    poller = VibeTradingPoller(adapter, state=state)
    runner.add_poller(FEED_VIBE_TRADING, poller)
    out = runner.poll(FEED_VIBE_TRADING)
    assert out["status"] == "error"
    assert "disk full" in out["error"]
    assert out["emitted"] >= 1
    assert runner.polls[FEED_VIBE_TRADING].emitted == out["emitted"]
    assert poller.counters["emit_error"] == 1
    window = events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(seconds=1))
    assert any(row.headline.startswith("alpha_bench") for row in window)
    assert not any(row.headline.startswith("alpha_factor") for row in window)
    retry = runner.poll(FEED_VIBE_TRADING)
    assert retry["status"] == "ok"
    assert retry["emitted"] > 1
    again = events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(seconds=1))
    assert len(again) > 1


def test_vibe_poller_persists_parquet_backed_events(tmp_path):
    settings = _settings()
    state = _state(tmp_path, settings, name="poller.db")
    root = tmp_path / "raw"
    adapter = ResearchAdapter(settings, transport=FakeSidecar(_responses()))
    runner = IngestRunner(state, parquet_root=root, clock=FrozenClock(NOW))
    runner.add_poller(FEED_VIBE_TRADING, VibeTradingPoller(adapter, state=state))
    out = runner.poll(FEED_VIBE_TRADING)
    assert out["status"] == "ok"
    assert out["emitted"] > 0
    window = events_in_window(state, NOW - timedelta(hours=1), NOW + timedelta(seconds=1))
    assert window
    assert all(row.source == "vibe-trading" for row in window)
    assert all(row.raw_ref.startswith("parquet://") for row in window)
    assert resolve_raw(root, window[0].raw_ref, state=state, row=window[0])


def _stage_run(root: Path, *, engine: str, point_in_time: bool = True) -> Path:
    run = root / "idea-1"
    code = run / "code"
    code.mkdir(parents=True)
    (code / "signal_engine.py").write_text(engine, encoding="utf-8")
    (run / "config.json").write_text(json.dumps({
        "point_in_time": point_in_time,
        "source": "yfinance",
        "symbols": ["AAPL", "OFF"],
    }), encoding="utf-8")
    return run


_MCP_SERVER = """#!/usr/bin/env python3
import json
import sys

def read_msg():
    line = sys.stdin.buffer.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line)

def write_msg(payload):
    sys.stdout.buffer.write(json.dumps(payload).encode() + b"\\n")
    sys.stdout.buffer.flush()

while True:
    msg = read_msg()
    method = msg.get("method")
    if method == "initialize":
        write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2024-11-05"}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/call":
        name = msg["params"]["name"]
        write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": json.dumps({"status": "ok", "tool": name})}],
        }})
    else:
        raise SystemExit(0)
"""
