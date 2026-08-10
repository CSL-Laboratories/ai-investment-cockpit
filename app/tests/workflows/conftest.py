"""WP8 workflow test fixtures (G8 substrate).

Copies the house harness shape from ``app/tests/bridge/conftest.py``: per-test SEC-04
HALT-sentinel isolation (``COCKPIT_HALT_PATH``), a ``FrozenClock`` at 2026-07-07T14:00Z
(10:00 ET, market open), and an ``OrchestratorState`` on a tmp SQLite DB — hermetic: no
wall-clock, no network, no real subprocesses (agent calls go through FakeTransport).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_runner.transport import ProcResult
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"wp8-workflows-key"
NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    yield


@pytest.fixture
def clock():
    return FrozenClock(NOW)


@pytest.fixture
def state_with(tmp_path, clock):
    """Build a state whose Settings differ from the default — for the SEC-05/06 controls,
    which are inactive under ``Settings()`` and so are invisible to the ``st`` fixture."""
    def _build(**settings_kw):
        name = f"wp8-{len(settings_kw)}-{abs(hash(tuple(sorted(settings_kw))))}.db"
        engine = make_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
        create_all(engine)
        sf = make_session_factory(engine)
        return OrchestratorState.build(sf, hmac_key=KEY,
                                       settings=Settings(**settings_kw), clock=clock)
    return _build


@pytest.fixture
def st(state_with):
    return state_with()


# ---- FakeTransport / envelope (canonical pattern from tests/agent_runner/test_runner.py)
class FakeTransport:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def run(self, argv, input_text, timeout_s, cwd):
        self.calls.append({"argv": argv, "input": input_text, "cwd": cwd})
        return self._results.pop(0)


def envelope(body, session_id="s1"):
    # mimic `claude -p --output-format json`: result carried as a JSON string
    return ProcResult(0, json.dumps({"session_id": session_id, "result": json.dumps(body)}),
                      "", False)
