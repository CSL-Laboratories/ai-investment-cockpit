"""G5 ACCEPTANCE SUITE — the control-plane API contract (criterion 8; §6 auth + SEC-15).

Independent test-author's acceptance checks over the FastAPI shell:
  * every route requires the API key -> 401 unauthenticated / wrong-key
  * GET/PUT /mode round-trips; an illegal or un-gated transition -> 4xx
  * PUT /limits loosening beyond the hard config ceiling is rejected AND disarms auto
    modes to review_live (SEC-15, security-hardening-backlog §SEC-15 normative Test)
  * /status returns mode + breakers + usage

Skipped where FastAPI/httpx are absent (the WP0 CI subset) so the invariant gate stays
green; runs locally where the API deps are present.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from config.settings import Mode, Settings  # noqa: E402
from core.clock import FrozenClock  # noqa: E402
from orchestrator.app import create_app  # noqa: E402
from orchestrator.auth import API_KEY_HEADER, ApiKeyAuth  # noqa: E402
from orchestrator.gate import GateResult  # noqa: E402
from orchestrator.mode_ladder import ModeLadder  # noqa: E402
from orchestrator.state import OrchestratorState  # noqa: E402
from storage.engine import create_all, make_engine, make_session_factory  # noqa: E402

KEY = "g5-api-key-abcdef123456"
AUTH = {API_KEY_HEADER: KEY}
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)   # Tue 10:00 ET, session open


@pytest.fixture
def state(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'g5api.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"g5api", settings=Settings(),
                                   clock=FrozenClock(OPEN_UTC))


@pytest.fixture
def client(state):
    app = create_app(state, auth=ApiKeyAuth(key_provider=lambda: KEY))
    return TestClient(app)


def _arm_veto_via_ladder(state):
    lad = ModeLadder(state)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=GateResult(unlockable=True, target="auto_veto"),
                            operator="op")
    assert res.armed and lad.current() is Mode.auto_veto
    return lad


# ---- auth (§6) -------------------------------------------------------------------------
def test_g5_8_unauthenticated_is_401(client):
    assert client.get("/status").status_code == 401                     # no key
    assert client.get("/status", headers={API_KEY_HEADER: "wrong"}).status_code == 401
    assert client.get("/status", headers=AUTH).status_code == 200       # correct key


def test_g5_8_mutating_routes_require_auth(client):
    # a mutating route without the key is rejected before any handler runs.
    assert client.put("/mode", json={"mode": "paper", "actor": "op"}).status_code == 401
    assert client.post("/killswitch", json={"actor": "op"}).status_code == 401


# ---- mode round-trip + illegal/ungated -> 4xx ------------------------------------------
def test_g5_8_get_put_mode_roundtrips(client):
    assert client.get("/mode", headers=AUTH).json()["mode"] == "read_only"
    r = client.put("/mode", headers=AUTH, json={"mode": "paper", "actor": "op"})
    assert r.status_code == 200 and r.json()["mode"] == "paper"
    assert client.get("/mode", headers=AUTH).json()["mode"] == "paper"   # round-trips


def test_g5_8_put_mode_illegal_skip_up_is_4xx(client):
    # read_only -> review_live skips paper.
    r = client.put("/mode", headers=AUTH, json={"mode": "review_live", "actor": "op"})
    assert 400 <= r.status_code < 500
    assert client.get("/mode", headers=AUTH).json()["mode"] == "read_only"   # unchanged


def test_g5_8_put_mode_ungated_auto_arm_is_4xx(client, state):
    ModeLadder(state).set_mode(Mode.paper, actor="op")
    ModeLadder(state).set_mode(Mode.review_live, actor="op")
    r = client.put("/mode", headers=AUTH, json={"mode": "auto_veto", "actor": "op"})
    assert 400 <= r.status_code < 500
    assert r.json()["armed"] is False and r.json()["reasons"]
    # nothing persisted: still review_live.
    assert client.get("/mode", headers=AUTH).json()["mode"] == "review_live"


def test_g5_8_put_mode_unknown_mode_is_422(client):
    assert client.put("/mode", headers=AUTH,
                      json={"mode": "turbo", "actor": "op"}).status_code == 422


# ---- limits (SEC-15) -------------------------------------------------------------------
def test_g5_8_limits_beyond_ceiling_rejected(client):
    # funded-balance ceiling defaults to 25000; 30000 exceeds it.
    r = client.put("/limits", headers=AUTH,
                   json={"max_notional": "30000", "actor": "op", "confirm": True})
    assert r.status_code == 422 and r.json()["rejected"] == "ceiling_exceeded"


def test_g5_8_limits_beyond_ceiling_disarms_auto_mode(client, state):
    """SEC-15 normative Test (security-hardening-backlog §SEC-15): a /limits widening beyond
    the code ceiling is REJECTED **and disarms auto mode**. The rejection alone is not
    sufficient — an attempted ceiling breach while armed must also drop autonomy to
    review_live."""
    _arm_veto_via_ladder(state)
    assert client.get("/mode", headers=AUTH).json()["mode"] == "auto_veto"
    r = client.put("/limits", headers=AUTH,
                   json={"max_notional": "30000", "actor": "op", "confirm": True})
    assert 400 <= r.status_code < 500                       # rejected
    # SEC-15: the armed auto mode must be disarmed to review_live.
    assert client.get("/mode", headers=AUTH).json()["mode"] == "review_live"


def test_g5_8_limits_confirmed_loosen_within_ceiling_drops_auto(client, state):
    # a confirmed loosening WITHIN the ceiling is accepted and drops auto -> review_live.
    _arm_veto_via_ladder(state)
    r = client.put("/limits", headers=AUTH,
                   json={"max_notional": "5000", "actor": "op", "confirm": True})
    assert r.status_code == 200 and r.json()["mode_after"] == "review_live"
    assert client.get("/mode", headers=AUTH).json()["mode"] == "review_live"


def test_g5_8_limits_loosen_requires_confirmation(client):
    # raising max_notional from the 1000 default loosens -> needs explicit confirm.
    r = client.put("/limits", headers=AUTH, json={"max_notional": "5000", "actor": "op"})
    assert r.status_code == 409 and r.json()["rejected"] == "confirmation_required"


# ---- status ----------------------------------------------------------------------------
def test_g5_8_status_returns_mode_breakers_usage(client):
    body = client.get("/status", headers=AUTH).json()
    assert {"mode", "usage", "breakers", "watch_only"} <= set(body)
    assert body["mode"]["mode"] == "read_only"
    assert isinstance(body["breakers"], dict) and isinstance(body["usage"], dict)


def test_g5_8_status_reflects_a_breaker_trip(client, state):
    from orchestrator import breakers as bk
    _arm_veto_via_ladder(state)
    bk.BreakerEngine(state).trip(bk.BRK_DAILY_LOSS, "loss limit breached")
    body = client.get("/status", headers=AUTH).json()
    assert body["mode"]["mode"] == "review_live"
    assert body["breakers"][bk.BRK_DAILY_LOSS]["tripped"] is True
