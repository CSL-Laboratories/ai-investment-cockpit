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


@pytest.fixture
def wire_anchor():
    """SEC-11 test helper: wire + post a SATISFIED external anchor at the current verified head.

    ``compute_gate`` now HARD-REQUIRES an external audit anchor for an auto unlock (the in-DB
    watermark is replayable; only the out-of-DB witness closes a keyless rollback). A gate test
    that exercises the METRIC logic must therefore first stand up a satisfied anchor — exactly
    what a properly-configured deployment has. ``record=False`` avoids appending an
    ``EVENT_ANCHOR`` row so the gate's adjudication/mode metrics stay pristine."""
    from bridge.anchor import InMemoryAnchorStore, post_head_anchor

    def _wire(st):
        if st.anchor_store is None:
            st.anchor_store = InMemoryAnchorStore()
        post_head_anchor(st, record=False)
        return st.anchor_store

    return _wire
