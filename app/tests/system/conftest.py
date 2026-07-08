"""WP9 — system-test fixtures (Owner A substrate).

Per-test COCKPIT_HALT_PATH isolation (a HALT set by one test never leaks into another and the
arm-time check never touches real app-data), a ``FrozenClock`` at the weekday market-open
instant, a tmp parquet root, and a fully-composed hermetic ``system``. The in-memory keyring
is provided session-wide by app/tests/conftest.py — it is NOT redefined here.
"""
from __future__ import annotations

import pytest

from core.clock import FrozenClock

from tests.system.harness import OPEN_UTC, build_system


@pytest.fixture(autouse=True)
def _isolate_halt(tmp_path, monkeypatch):
    """SEC-04: isolate the HALT sentinel to a per-test tmp path (unset by default)."""
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    yield


@pytest.fixture
def sys_clock() -> FrozenClock:
    """A FrozenClock at 2026-07-07T14:00Z = 10:00 ET Tue (a live NYSE session, so the intraday
    loop never SKIPs on the market-open guard)."""
    return FrozenClock(OPEN_UTC)


@pytest.fixture
def parquet_root(tmp_path):
    root = tmp_path / "raw"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def system(tmp_path, sys_clock, _isolate_halt):
    """The fully-composed, hermetic cockpit (mirrors main.compose with injected fakes)."""
    return build_system(tmp_path=tmp_path, clock=sys_clock)
