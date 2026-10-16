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
from ingest.normalize import ALLOWED_SOURCES
from orchestrator.state import OrchestratorState
from research.vibe_trading.adapter import ResearchAdapter, ResearchRequest
from research.vibe_trading.mcp_client import (
    SidecarUnavailable,
    StdioMcpClient,
    _child_env,
)
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
from workflows.events import events_in_window
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
    holdings = _load("holdings.json")
    etf = _load("etf.json")
    prediction = _load("prediction.json")
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


def test_features_land_in_a_decision_packet(state):
    transport = FakeSidecar(_responses())
    result = ResearchAdapter(_settings(), transport=transport).publish(state)
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
    assert "13f ticker=AAPL" in text
    assert "delta_shares=200" in text
    assert "etf=SPY" in text
    assert "AAPL:0.0700" in text
    assert "ZZZZ" not in text
    assert "implied_probability=0.4200" in text
    assert "submit_order" not in text
    assert "[role-marker]" in text
    assert all(row.event_type.startswith("signal:") for row in window)
    assert all(row.source == "vibe-trading" for row in window)
    tools = [tool for tool, _args in transport.calls]
    assert tools == [call[0] for call in ResearchAdapter(_settings()).plan()]
    assert "backtest" not in tools
    assert "trading_orders" not in tools


def test_raw_payload_is_kept_out_of_the_headline():
    batch = ResearchAdapter(_settings(), transport=FakeSidecar(_responses())).collect(
        clock=FrozenClock(NOW),
    )
    summary = next(event for event in batch.events if event["headline"].startswith("alpha_bench"))
    assert "submit_order" not in summary["headline"]
    assert "BUY AAPL" in json.dumps(summary["raw_input"])


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
    payload = {"status": "ok", "alive": 30, "reversed": 0, "dead": 0, "rows": rows}
    transport = FakeSidecar({"alpha_bench": payload})
    settings = _settings(
        vibe_trading_etfs="", vibe_trading_event_queries="", watchlist="AAPL",
        vibe_trading_max_symbols=0, vibe_trading_token_budget=400,
    )
    result = ResearchAdapter(settings, transport=transport).publish(_state(tmp_path, settings))
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
    ).publish(_state(tmp_path, settings))
    packet = build_packet(result.stored)
    assert estimate_tokens(packet.prompt_block()) <= 80


def test_sidecar_calls_are_audited_as_unpriced_cost(state):
    ResearchAdapter(_settings(), transport=FakeSidecar(_responses())).publish(state)
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


def test_republish_dedupes_the_same_feature(state):
    adapter = ResearchAdapter(_settings(), transport=FakeSidecar(_responses()))
    first = adapter.publish(state)
    second = adapter.publish(state)
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
    assert paper[1][1] == {"ticker": "AAPL"}
    assert paper[2][1] == {"ticker": "MSFT"}


def test_trading_tools_are_denied_before_any_transport():
    class Exploding:
        def call(self, tool, arguments):
            raise AssertionError(f"transport saw {tool}")

    denied = (
        "trading_orders", "trading_account", "trading_positions", "trading_history",
        "write_file", "run_swarm", "read_file", "broker_submit",
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


def test_point_in_time_run_card_is_evidence_and_not_an_order(tmp_path):
    run = _stage_run(tmp_path, engine="def signal(panel):\n    return panel\n")
    card = _load("run_card.json")
    (run / "run_card.json").write_text(json.dumps(card), encoding="utf-8")
    transport = FakeSidecar({
        **_responses(),
        "backtest": {"status": "ok", "run_dir": str(run), "side": "buy"},
    })
    batch = ResearchAdapter(
        _settings(vibe_trading_etfs="", vibe_trading_event_queries="", vibe_trading_max_symbols=0),
        transport=transport,
    ).collect(ResearchRequest(strategy_id="idea-1", run_root=str(tmp_path)), clock=FrozenClock(NOW))
    assert transport.calls[-1][0] == "backtest"
    assert set(transport.calls[-1][1]) == {"run_dir"}
    card_event = next(event for event in batch.events if event["event_type"] == "signal:run_card")
    assert "admits_order=false" in card_event["headline"]
    assert "side" not in card_event["headline"]
    assert "buy" not in card_event["headline"]
    assert card_event["raw_input"]["sidecar_card"]["admits_order"] is True


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
    env = _child_env()
    assert "COCKPIT_WATCHLIST" not in env
    assert "ALPACA_API_KEY" not in env
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
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise SystemExit(0)
        if line in (b"\\r\\n", b"\\n"):
            break
        key, _, value = line.partition(b":")
        headers[key.decode().lower().strip()] = value.strip()
    length = int(headers["content-length"])
    return json.loads(sys.stdin.buffer.read(length))

def write_msg(payload):
    body = json.dumps(payload).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode() + body)
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
