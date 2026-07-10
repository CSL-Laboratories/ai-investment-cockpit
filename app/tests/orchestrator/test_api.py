"""WP5 builder smoke tests for the FastAPI shell — auth, the ladder/gate guard on /mode,
SEC-15 privileged /limits loosening, /killswitch, /status, and the thin not-wired handlers.
These are the builder's own checks; the full G5 acceptance suite is owned separately.

Skipped where FastAPI/httpx aren't installed (the WP0 CI subset) so the invariant gate stays
green; they run locally where the API deps are present.
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
from orchestrator.auth import ApiKeyAuth, API_KEY_HEADER, assert_loopback  # noqa: E402
from orchestrator.mode_ladder import ModeLadder  # noqa: E402
from orchestrator.state import OrchestratorState  # noqa: E402
from storage.engine import create_all, make_engine, make_session_factory  # noqa: E402

KEY = "test-api-key-123456"
AUTH = {API_KEY_HEADER: KEY}


def _clock():
    return FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))


@pytest.fixture
def state(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"k", settings=Settings(), clock=_clock())


@pytest.fixture
def client(state):
    app = create_app(state, auth=ApiKeyAuth(key_provider=lambda: KEY))
    return TestClient(app)


# ---- auth ------------------------------------------------------------------------------
def test_unauthenticated_call_is_401(client):
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={API_KEY_HEADER: "wrong"}).status_code == 401
    assert client.get("/status", headers=AUTH).status_code == 200


def test_assert_loopback_rejects_public_bind():
    assert_loopback("127.0.0.1")
    with pytest.raises(ValueError):
        assert_loopback("0.0.0.0")


# ---- mode ladder + arm guard -----------------------------------------------------------
def test_get_and_put_mode_walks_up(client):
    assert client.get("/mode", headers=AUTH).json()["mode"] == "read_only"
    r = client.put("/mode", headers=AUTH, json={"mode": "paper", "actor": "op"})
    assert r.status_code == 200 and r.json()["mode"] == "paper"


def test_put_mode_illegal_transition_is_409(client):
    # read_only -> review_live skips a rung.
    r = client.put("/mode", headers=AUTH, json={"mode": "review_live", "actor": "op"})
    assert r.status_code == 409


def test_arm_auto_without_gate_is_409(client, state):
    ModeLadder(state).set_mode(Mode.paper, actor="op")
    ModeLadder(state).set_mode(Mode.review_live, actor="op")
    r = client.put("/mode", headers=AUTH, json={"mode": "auto_veto", "actor": "op"})
    assert r.status_code == 409
    body = r.json()
    assert body["armed"] is False and body["reasons"]
    # nothing persisted: still review_live
    assert client.get("/mode", headers=AUTH).json()["mode"] == "review_live"


# ---- limits (SEC-15) -------------------------------------------------------------------
def test_limits_tighten_no_confirmation_no_drop(client):
    r = client.put("/limits", headers=AUTH,
                   json={"max_notional": "100", "actor": "op"})
    assert r.status_code == 200
    body = r.json()
    assert body["loosened"] == [] and body["mode_after"] is None
    assert client.get("/limits", headers=AUTH).json()["limits"]["max_notional"] == "100"


def test_limits_loosen_requires_confirmation(client):
    # default max_notional is 1000; raising to 5000 loosens.
    r = client.put("/limits", headers=AUTH, json={"max_notional": "5000", "actor": "op"})
    assert r.status_code == 409 and r.json()["rejected"] == "confirmation_required"


def test_limits_loosen_beyond_ceiling_rejected(client):
    # funded balance default 25000 -> ceiling; 30000 exceeds it.
    r = client.put("/limits", headers=AUTH,
                   json={"max_notional": "30000", "actor": "op", "confirm": True})
    assert r.status_code == 422 and r.json()["rejected"] == "ceiling_exceeded"


def test_limits_loosen_confirmed_drops_auto_to_review_live(client, state):
    # arm auto_veto directly via the ladder so a confirmed loosening must drop it.
    from orchestrator.gate import GateResult
    lad = ModeLadder(state)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=GateResult(unlockable=True, target="auto_veto"), operator="op")
    assert lad.current() is Mode.auto_veto
    r = client.put("/limits", headers=AUTH,
                   json={"max_notional": "5000", "actor": "op", "confirm": True})
    assert r.status_code == 200
    assert r.json()["mode_after"] == "review_live"
    assert client.get("/mode", headers=AUTH).json()["mode"] == "review_live"


# ---- killswitch ------------------------------------------------------------------------
def test_killswitch_disarms(client, state, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from orchestrator.gate import GateResult
    lad = ModeLadder(state)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=GateResult(unlockable=True, target="auto_veto"), operator="op")
    r = client.post("/killswitch", headers=AUTH, json={"actor": "lucas", "reason": "panic"})
    assert r.status_code == 200 and r.json()["mode_after"] == "review_live"


# ---- status + jobs + not-wired ---------------------------------------------------------
def test_status_shape(client):
    body = client.get("/status", headers=AUTH).json()
    assert set(body) >= {"mode", "usage", "watch_only", "breakers", "ingest"}
    assert body["mode"]["mode"] == "read_only"


def test_job_enqueue_and_fetch(client):
    r = client.post("/jobs", headers=AUTH, json={"model": "triage"})
    assert r.status_code == 202
    jid = r.json()["id"]
    assert r.json()["runtime"] == "claude"
    got = client.get(f"/jobs/{jid}", headers=AUTH)
    assert got.status_code == 200 and got.json()["status"] == "queued"
    assert client.get("/jobs/nope", headers=AUTH).status_code == 404


def test_job_unknown_model_422(client):
    assert client.post("/jobs", headers=AUTH, json={"model": "bogus"}).status_code == 422


def test_create_job_is_audited(client, state):
    """R3-C3 (Inv 6): the queued AgentJob create_job inserts MUST carry a covering AuditEntry —
    it was a bare ``Repo.add`` bypassing record_state_change, so with AgentJob in the default
    completeness gate every queued job left an uncovered row that tripped reconcile ok=False (cry
    wolf) AND, more fundamentally, was a state change with no audit. Fix routes it through
    record_state_change; the job row is now covered and a fresh reconcile is ok=True."""
    from orchestrator.reconcile import reconcile_startup
    from schemas.models.agent_job import AgentJob

    r = client.post("/jobs", headers=AUTH, json={"model": "triage"})
    assert r.status_code == 202
    jid = r.json()["id"]

    # the created job row now carries a covering AuditEntry (no completeness gap).
    assert state.audit.verify_completeness(AgentJob) == []
    # and a fresh startup reconcile over this state is clean — no cry-wolf alert.
    report = reconcile_startup(state, broker_history_fn=lambda oid: [])
    assert report.ok is True
    assert report.uncovered == {}
    assert state.audit.verify_chain().ok
    # return shape + fetch behavior unchanged.
    assert r.json()["runtime"] == "claude"
    assert client.get(f"/jobs/{jid}", headers=AUTH).json()["status"] == "queued"


def test_not_wired_endpoints_return_501(client):
    assert client.get("/brief/today", headers=AUTH).status_code == 501
    assert client.post("/brief/run", headers=AUTH).status_code == 501
    assert client.post("/automations", headers=AUTH).status_code == 501
    body = client.get("/brief/today", headers=AUTH).json()
    assert body["status"] == "not_wired"
