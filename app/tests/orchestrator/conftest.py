"""Orchestrator test isolation.

The mode ladder's ``arm_auto_mode`` now consults the SEC-04 HALT sentinel at
``orchestrator.halt.default_halt_path()`` before arming. Redirect that path to a per-test
tmp file (via the ``COCKPIT_HALT_PATH`` override) so:
  * the arm-time HALT check never touches the real per-user app-data dir, and
  * a HALT set by one test (e.g. a killswitch) never leaks into another test's arm.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_halt_sentinel(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    yield
