"""S6 regression: the kill switch attempts every containment step even when the HALT write
fails. All state is synthetic and all external effects are replaced. No network.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory


@pytest.fixture
def state(tmp_path, monkeypatch):
    # The arming guard must inspect only a fixture-owned, absent sentinel.
    ladder_module = importlib.import_module("orchestrator.mode_ladder")
    monkeypatch.setattr(ladder_module, "default_halt_path", lambda: tmp_path / "HALT")
    engine = make_engine(f"sqlite:///{(tmp_path / 's6.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"synthetic-s6-test-key",
        settings=Settings(decision_runtime="claude"),
        clock=FrozenClock(datetime(2026, 6, 2, 18, tzinfo=timezone.utc)),
    )


def test_f03_halt_write_failure_still_attempts_other_containment(state, tmp_path, monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    calls = []

    def fail_write(*args, **kwargs):
        raise PermissionError("synthetic unwritable sentinel parent")

    def cancel():
        calls.append("cancel")
        return 0

    def drop(*args, **kwargs):
        calls.append("disarm")
        return Mode.review_live

    monkeypatch.setattr(module, "set_halt", fail_write)
    monkeypatch.setattr(module, "ModeLadder", lambda st: NS(drop_to_review_live=drop))
    try:
        module.killswitch(
            state, actor="test", halt_path=tmp_path / "unused-HALT", cancel_hook=cancel,
            disconnect_hook=lambda: calls.append("disconnect"),
        )
    except OSError:
        pass  # surfacing failure is fine after independent containment is attempted
    assert calls == ["cancel", "disconnect", "disarm"]
