"""QT-09 — the /quant routes over real HTTP, through the real key gate.

``tests/bridge/test_quant_surface.py`` pins the pure functions and the rendering. This file
pins the thing those tests cannot: that the routes are actually MOUNTED on the app, behind
the same auth every other route has, and that the surface is read-only.

The property that matters most here is the last one. The quant engine is the newest and
least-proven component in the tree and it is being put on the operator surface, so what it
CANNOT do from there is more important than what it can.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import BoundedSemaphore

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from config.settings import Settings  # noqa: E402
from core.clock import FrozenClock  # noqa: E402
from orchestrator.app import create_app  # noqa: E402
from orchestrator.auth import API_KEY_HEADER, ApiKeyAuth  # noqa: E402
from orchestrator.quant_api import MAX_SIM_EVENTS  # noqa: E402
from orchestrator import quant_api  # noqa: E402
from orchestrator.state import OrchestratorState  # noqa: E402
from storage.engine import create_all, make_engine, make_session_factory  # noqa: E402

KEY = "test-api-key-123456"
AUTH = {API_KEY_HEADER: KEY}


@pytest.fixture
def state(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'quant-api.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(
        sf, hmac_key=b"k", settings=Settings(),
        clock=FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)),
    )


@pytest.fixture
def client(state):
    return TestClient(create_app(state, auth=ApiKeyAuth(key_provider=lambda: KEY)))


@pytest.fixture(autouse=True)
def fresh_simulation_capacity(monkeypatch):
    """A failed concurrency test must never poison another app/test in this process."""
    slots = BoundedSemaphore(quant_api.MAX_CONCURRENT_SIMS)
    monkeypatch.setattr(quant_api, "_SIM_SLOTS", slots)
    return slots


# ---- mounted, and behind the key gate ------------------------------------------------------


def test_quant_status_requires_the_api_key():
    """Every route requires the key (security-architecture §6). A new route group is exactly
    where that would be forgotten."""
    engine = make_engine("sqlite://")
    create_all(engine)
    st = OrchestratorState.build(make_session_factory(engine), hmac_key=b"k",
                                 settings=Settings(),
                                 clock=FrozenClock(datetime(2026, 7, 7, tzinfo=timezone.utc)))
    unauthenticated = TestClient(create_app(st, auth=ApiKeyAuth(key_provider=lambda: KEY)))
    assert unauthenticated.get("/quant/status").status_code == 401
    assert unauthenticated.post("/quant/sim", json={}).status_code == 401


def test_quant_status_returns_the_containment_posture(client):
    body = client.get("/quant/status", headers=AUTH).json()
    assert body["engine"] == "available"
    assert body["containment"]["quant_may_import_brokers"] is False
    assert body["book_feed"] == "not_wired"
    assert "market_maker" in body["strategies"]


def test_a_simulation_runs_over_http_and_returns_a_tearsheet(client):
    body = client.post("/quant/sim", json={"events": 2_000, "seed": 5}, headers=AUTH).json()
    assert body["events"] == 2_000
    assert "attribution" in body and "spread_capture" in body["attribution"]
    assert "SYNTHETIC DATA" in body["caveat"]


def test_the_defaults_work_with_an_empty_body(client):
    """An operator typing `/quant sim` with no arguments must get a run, not a 422."""
    response = client.post("/quant/sim", json={}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["events"] > 0


def test_an_oversized_simulation_is_refused_at_the_http_boundary(client):
    """Server-side, because a Discord slash-command argument is operator input like any
    other and the cap is what keeps /killswitch reachable."""
    response = client.post("/quant/sim", json={"events": MAX_SIM_EVENTS + 1}, headers=AUTH)
    assert response.status_code == 422
    assert "killswitch" in response.json()["detail"]


def test_an_unknown_strategy_is_refused(client):
    response = client.post("/quant/sim", json={"strategy": "nope"}, headers=AUTH)
    assert response.status_code == 422


def test_simulation_capacity_is_fail_fast_and_keeps_killswitch_headroom(
    client, fresh_simulation_capacity,
):
    assert fresh_simulation_capacity.acquire(blocking=False)
    try:
        response = client.post("/quant/sim", json={"events": 10}, headers=AUTH)
        assert response.status_code == 503
        assert "capacity" in response.json()["detail"]
        assert "killswitch" in response.json()["detail"]
    finally:
        fresh_simulation_capacity.release()


def test_invalid_input_is_rejected_before_capacity_admission(
    client, fresh_simulation_capacity,
):
    assert fresh_simulation_capacity.acquire(blocking=False)
    try:
        response = client.post(
            "/quant/sim", json={"events": MAX_SIM_EVENTS + 1}, headers=AUTH,
        )
        assert response.status_code == 422
    finally:
        fresh_simulation_capacity.release()


def test_simulation_capacity_is_released_after_compute_failure(
    monkeypatch, fresh_simulation_capacity,
):
    def fail(_body):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(quant_api, "_execute_sim", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        quant_api.run_sim(quant_api.SimRequest(events=10))

    assert fresh_simulation_capacity.acquire(blocking=False)
    fresh_simulation_capacity.release()


# ---- read-only ------------------------------------------------------------------------------------


def test_the_quant_surface_exposes_no_mutating_route(client):
    """The strongest thing a compromised operator account achieves here is arithmetic.

    Enumerated from the app's own route table rather than asserted in prose, so adding a
    mutating quant route later fails this test instead of quietly widening the surface.
    """
    quant_routes = {
        (method, r.path)
        for r in client.app.routes
        if getattr(r, "path", "").startswith("/quant") and hasattr(r, "methods")
        for method in (r.methods - {"HEAD", "OPTIONS"})
    }
    assert quant_routes, "the quant routes must actually be mounted"
    assert quant_routes == {("GET", "/quant/status"), ("POST", "/quant/sim")}


def test_running_a_simulation_persists_nothing(client, state):
    """A read-only surface that quietly wrote rows would not be read-only. Checked against
    the audit chain, which is where any state change in this system leaves a mark."""
    from schemas.models.audit_entry import AuditEntry
    from sqlalchemy import select

    def _count():
        with state.session_factory() as s:
            return len(s.execute(select(AuditEntry.seq)).all())

    before = _count()
    client.post("/quant/sim", json={"events": 1_000}, headers=AUTH)
    assert _count() == before


def test_the_simulation_is_deterministic_across_http_calls(client):
    """Same seed, same numbers — otherwise a tearsheet an operator pastes into a channel
    cannot be reproduced by anyone reading it later."""
    payload = {"events": 1_500, "seed": 21}
    first = client.post("/quant/sim", json=payload, headers=AUTH).json()
    second = client.post("/quant/sim", json=payload, headers=AUTH).json()
    assert first["attribution"] == second["attribution"]


# ---- operator input is bounded, not just counted -------------------------------------------


@pytest.mark.parametrize("payload", [
    {"strategy": "market_maker", "quote_size": 0},
    {"strategy": "market_maker", "quote_size": -100},
    {"strategy": "market_maker", "max_inventory": -1},
    {"strategy": "market_maker", "requote_threshold_ticks": -5},
    {"strategy": "market_maker", "events": 0},
    {"strategy": "market_maker", "events": -1},
])
def test_a_nonsense_sim_parameter_is_rejected_not_a_500(client, payload):
    """``events`` was the only bounded field. Everything else went straight into
    ``MarketMakerParams``, so a plausible operator value came back as an unhandled 500 from
    the loopback control plane rather than a validation error naming the field.

    The module docstring argues the event cap is 'a safety property of the control plane, not
    a nicety' — the same argument covers every other parameter reaching the same run loop.
    """
    r = client.post("/quant/sim", json=payload, headers=AUTH)
    assert r.status_code == 422, f"{payload} -> {r.status_code}"
    assert "detail" in r.json()


def test_a_large_quote_size_does_not_run_the_book_away(client):
    """quote_size=500 is an ordinary value, and it used to reach an uncaught OverflowError:
    the unclamped inventory skew walked the quote off the book (see
    tests/quant/strategies/test_market_maker.py). Bounded input is only half the fix — this
    asserts the run itself now completes."""
    r = client.post("/quant/sim",
                    json={"strategy": "market_maker", "quote_size": 500, "events": 2_000},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events"] > 0
    best_bid, best_ask = body["book"]["best_bid_ticks"], body["book"]["best_ask_ticks"]
    for px in (best_bid, best_ask):
        if px is not None:
            assert 0 < px < 10_000_000, f"book price {px} is not a plausible tick value"
