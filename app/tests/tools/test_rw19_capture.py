"""Hermetic protocol tests for the RW-19 evidence-generating CLI."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("rw19_capture_tool", ROOT / "tools" / "rw19_capture.py")
rw19 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rw19
SPEC.loader.exec_module(rw19)


class Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"X-Request-ID": "fixture-request"}

    def json(self):
        return self._payload


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, params):
        self.requests.append((url, dict(params)))
        return self.responses.pop(0)


def test_bar_capture_paginates_without_losing_feed_or_page_token():
    client = Client([
        Response({"bars": {"AAPL": [{"t": "2026-08-11T13:30:00Z"}]},
                  "next_page_token": "next"}),
        Response({"bars": {"AAPL": [{"t": "2026-08-11T13:31:00Z"}]}}),
    ])
    rows = rw19._fetch_bars(
        client, symbols=("AAPL",), day=datetime(2026, 8, 11).date(), feed="iex"
    )
    assert len(rows["AAPL"]) == 2
    assert client.requests[0][1]["feed"] == "iex"
    assert "page_token" not in client.requests[0][1]
    assert client.requests[1][1]["page_token"] == "next"


def test_news_rest_capture_paginates_and_binds_the_window():
    client = Client([
        Response({"news": [{"id": 1}], "next_page_token": "next"}),
        Response({"news": [{"id": 2}]}),
    ])
    start = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    end = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
    rows = rw19._fetch_news_rest(client, symbols=("AAPL",), start=start, end=end)
    assert [row["id"] for row in rows] == [1, 2]
    assert client.requests[0][1]["start"] == start.isoformat()
    assert client.requests[1][1]["page_token"] == "next"


class Socket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self, timeout):
        assert timeout > 0
        if self.frames:
            return self.frames.pop(0)
        raise TimeoutError


def _connector(socket, seen):
    def connect(url, **kwargs):
        seen.append((url, kwargs))
        return socket
    return connect


def test_market_ws_sends_auth_and_all_measurement_subscriptions():
    socket = Socket([
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"q","S":"FAKEPACA","t":"2026-08-12T14:00:00Z"}]',
    ])
    seen = []
    _start, _end, rows = rw19._capture_market_ws(
        key="fixture-key", secret="fixture-secret", feed="test", symbols=("FAKEPACA",),
        duration_seconds=5, connector=_connector(socket, seen),
    )
    assert seen[0][0].endswith("/v2/test")
    assert socket.sent[0] == {"action": "auth", "key": "fixture-key", "secret": "fixture-secret"}
    assert socket.sent[1] == {
        "action": "subscribe", "quotes": ["FAKEPACA"], "trades": ["FAKEPACA"],
        "bars": ["FAKEPACA"],
    }
    assert len(rows) == 1 and rows[0]["message"]["T"] == "q"


def test_news_ws_sends_symbol_scope_and_rejects_vendor_error():
    socket = Socket(['[{"T":"error","code":401,"msg":"no"}]'])
    with pytest.raises(RuntimeError, match="code=401"):
        rw19._capture_news_ws(
            key="fixture-key", secret="fixture-secret", symbols=("AAPL",),
            duration_seconds=5, connector=_connector(socket, []),
        )
    assert socket.sent[1] == {"action": "subscribe", "news": ["AAPL"]}


def test_bar_command_refuses_today_before_reading_credentials(monkeypatch):
    monkeypatch.setattr(rw19, "require_secret", lambda _name: pytest.fail("read a credential"))
    args = SimpleNamespace(
        date=datetime.now(UTC).date().isoformat(), symbols=["AAPL"], timeout=1,
        output_root="unused", thresholds_bps=(10,),
    )
    with pytest.raises(ValueError, match="never today"):
        rw19.capture_alpaca_bars(args)


def test_news_reconnect_capture_deliberately_gaps_then_compares_combined_window(monkeypatch):
    first_start = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    first_end = datetime(2026, 8, 12, 14, 1, tzinfo=UTC)
    second_start = datetime(2026, 8, 12, 14, 1, 5, tzinfo=UTC)
    second_end = datetime(2026, 8, 12, 14, 2, 5, tzinfo=UTC)
    segments = [
        (first_start, first_end, [{"message": {"id": 1}}]),
        (second_start, second_end, [{"message": {"id": 2}}]),
    ]
    monkeypatch.setattr(
        rw19,
        "require_secret",
        lambda name: SimpleNamespace(reveal=lambda: f"fixture-{name}"),
    )
    monkeypatch.setattr(rw19, "_capture_news_ws", lambda **_kwargs: segments.pop(0))
    sleeps = []
    monkeypatch.setattr(rw19.time, "sleep", sleeps.append)

    class ContextClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(rw19.httpx, "Client", ContextClient)
    fetched = {}

    def fetch(_client, **kwargs):
        fetched.update(kwargs)
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(rw19, "_fetch_news_rest", fetch)
    written = {}

    def write(root, **kwargs):
        written["root"] = root
        written.update(kwargs)
        return Path("manifest.json")

    monkeypatch.setattr(rw19, "write_news_reconnect_evidence", write)
    args = SimpleNamespace(
        segment_seconds=60,
        gap_seconds=5,
        symbols=["AAPL"],
        timeout=1,
        output_root="evidence",
    )

    assert rw19.capture_alpaca_news_reconnect(args) == Path("manifest.json")
    assert sleeps == [5.0]
    assert fetched["start"] == first_start and fetched["end"] == second_end
    assert written["disconnected_at"] == first_end
    assert written["reconnected_at"] == second_start
    assert written["environment"]["deliberate_disconnect"] is True
