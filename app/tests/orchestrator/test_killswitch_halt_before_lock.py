"""The HTTP kill switch sets HALT before it waits for ``state.lock``.

A holder of ``state.lock`` that is stalled in a SQLite write (PUT /limits, PUT /mode, or a
limits append caught behind a job row flush) must not delay the HALT flag: HALT is set
within PROMPT seconds of the kill call while the holder still waits, and the rest of the
kill (the mode drop and its audit row) still runs under the lock, after the holder's write.

Synthetic only: a temporary SQLite file, a test HMAC key, a fixture-owned HALT path, the
route endpoints called directly (no server, no HTTP). No network, no broker.
"""
from __future__ import annotations

import importlib
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from config.settings import Mode, Settings
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator.limits import EVENT_LIMITS_CHANGE
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_KILLSWITCH, EVENT_MODE_CHANGE, OrchestratorState
from schemas.models.agent_job import AgentJob
from schemas.models.audit_entry import AuditEntry
from storage.engine import create_all, make_engine, make_session_factory
from workflows.constants import EVENT_AGENT_JOB

HOLD = 1.5      # seconds an outside connection holds SQLite's write lock
PROMPT = 0.25   # HALT must be set this soon after the kill call


@pytest.fixture
def env(tmp_path, monkeypatch):
    halt = tmp_path / "HALT"
    for name in ("orchestrator.mode_ladder", "orchestrator.killswitch"):
        monkeypatch.setattr(importlib.import_module(name), "default_halt_path", lambda: halt)
    db = tmp_path / "kill-before-lock.db"
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    create_all(engine)
    st = OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"synthetic-kill-before-lock-key",
        settings=Settings(decision_runtime="claude"),
        clock=FrozenClock(datetime(2026, 6, 2, 18, tzinfo=timezone.utc)),
    )
    from orchestrator import app as api
    from orchestrator.auth import ApiKeyAuth

    app = api.create_app(st, auth=ApiKeyAuth(key_provider=lambda: None))

    def endpoint(path, method):
        return next(r.endpoint for r in app.routes
                    if r.path == path and method in getattr(r, "methods", set()))

    yield {"st": st, "api": api, "db": db, "halt": halt,
           "put_limits": endpoint("/limits", "PUT"), "post_kill": endpoint("/killswitch", "POST")}
    engine.dispose()


def _start(target, out, tag):
    def body():
        try:
            out[tag] = target()
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            out[tag] = exc

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread


def _outside_writer(db):
    """Another connection takes SQLite's write lock and holds it; returns its release."""
    conn = sqlite3.connect(str(db), isolation_level=None, timeout=10)
    conn.execute("BEGIN IMMEDIATE")
    held = {"yes": True}

    def release():
        if held["yes"]:
            held["yes"] = False
            conn.execute("ROLLBACK")
            conn.close()

    return release


def _kill_and_time(env, out, release=None, release_at=None):
    """Call POST /killswitch on a thread and poll for the HALT file; return the seconds from
    the call to HALT (None if it never appeared). ``release`` lets the outside writer go at
    ``release_at`` (a perf_counter time), whether or not HALT is set by then."""
    halt = env["halt"]
    assert not halt.exists()
    t0 = time.perf_counter()
    kill = _start(lambda: env["post_kill"](env["api"].KillRequest(actor="test", reason="stall")),
                  out, "kill")
    t_halt = None
    while time.perf_counter() - t0 < 15:
        if t_halt is None and halt.exists():
            t_halt = time.perf_counter() - t0
        if release is not None and time.perf_counter() >= release_at:
            release()
            release = None
        if t_halt is not None and release is None:
            break
        time.sleep(0.002)
    kill.join(15)
    return t_halt


def _last_seq(st, event_type):
    with st.session_factory() as s:
        return s.execute(
            select(AuditEntry.seq).where(AuditEntry.event_type == event_type)
            .order_by(AuditEntry.seq.desc())
        ).scalars().first()


def _assert_prompt_halt_then_ordered_kill(st, out, t_halt, holder_event):
    errors = {k: repr(v)[:200] for k, v in out.items() if isinstance(v, BaseException)}
    assert not errors, errors
    assert "kill" in out, "the kill did not finish"
    assert t_halt is not None, "HALT was never set"
    assert t_halt <= PROMPT, (
        f"HALT set {t_halt:.3f} s after the kill call (bound {PROMPT} s): the kill waited "
        "for state.lock before it set HALT")
    kill_seq, holder_seq = _last_seq(st, EVENT_KILLSWITCH), _last_seq(st, holder_event)
    assert kill_seq is not None and holder_seq is not None, (kill_seq, holder_seq)
    assert kill_seq > holder_seq, "the kill's audit row did not wait for the lock holder"


def test_halt_is_set_while_put_limits_waits_on_a_stalled_writer(env):
    """PUT /limits (a tightening, 1000 to 900) holds state.lock and waits in its limits_change
    append while another connection holds SQLite's write lock for HOLD seconds."""
    st, api = env["st"], env["api"]
    entered = threading.Event()
    real_append = st.audit.append

    def spy_append(event_type, *args, **kwargs):
        if event_type == EVENT_LIMITS_CHANGE:
            entered.set()
        return real_append(event_type, *args, **kwargs)

    st.audit.append = spy_append
    release = _outside_writer(env["db"])
    out: dict = {}
    try:
        put = _start(lambda: env["put_limits"](
            api.LimitsUpdate(actor="test", max_notional=Decimal("900"))), out, "limits")
        assert entered.wait(5), "the limits append was never reached"
        time.sleep(0.3)
        t_halt = _kill_and_time(env, out, release, time.perf_counter() + HOLD - 0.3)
    finally:
        release()
    put.join(15)
    _assert_prompt_halt_then_ordered_kill(st, out, t_halt, EVENT_LIMITS_CHANGE)
    assert out["limits"]["limits"]["max_notional"] == "900"


def test_halt_is_set_while_a_job_flush_and_put_limits_wait_on_each_other(env):
    """A create_job-style record_state_change has flushed its row (it holds SQLite's write
    lock) and waits for the audit lock; PUT /limits holds state.lock and the audit lock and
    waits for SQLite's write lock until the busy timeout ends the wait."""
    st, api = env["st"], env["api"]
    flushed, entered = threading.Event(), threading.Event()
    real_append, real_append_in = st.audit.append, st.audit.append_in

    def spy_append(event_type, *args, **kwargs):
        if event_type == EVENT_LIMITS_CHANGE:
            entered.set()
        return real_append(event_type, *args, **kwargs)

    def slow_append_in(session, *args, **kwargs):
        flushed.set()             # the job row is flushed in this session
        entered.wait(5)
        time.sleep(0.2)           # the limits append now holds the audit lock and waits
        return real_append_in(session, *args, **kwargs)

    st.audit.append, st.audit.append_in = spy_append, slow_append_in
    job = AgentJob(id=new_id("job"), runtime="claude", model="test", model_pinned="test",
                   prompt_template="test", prompt_hash="", input_refs={}, schema_id=None,
                   status="queued", created_at=st.clock.now())
    out: dict = {}
    flush = _start(lambda: st.audit.record_state_change(
        job, EVENT_AGENT_JOB, actor="orchestrator.create_job", payload={"status": "queued"}),
        out, "job")
    assert flushed.wait(5), "the job row never flushed"
    put = _start(lambda: env["put_limits"](
        api.LimitsUpdate(actor="test", max_notional=Decimal("900"))), out, "limits")
    assert entered.wait(5), "the limits append was never reached"
    time.sleep(0.3)
    st.audit.append_in = real_append_in
    t_halt = _kill_and_time(env, out)
    flush.join(15)
    put.join(15)
    _assert_prompt_halt_then_ordered_kill(st, out, t_halt, EVENT_LIMITS_CHANGE)
    assert out["limits"]["limits"]["max_notional"] == "900"


def test_halt_is_set_while_a_mode_change_waits_on_a_stalled_writer(env):
    """PUT /mode's ModeLadder.set_mode persists under state.lock while another connection
    holds SQLite's write lock for HOLD seconds."""
    st = env["st"]
    entered = threading.Event()
    real_record = st.audit.record_state_change

    def spy_record(*args, **kwargs):
        entered.set()
        return real_record(*args, **kwargs)

    st.audit.record_state_change = spy_record
    release = _outside_writer(env["db"])
    out: dict = {}
    try:
        mode = _start(lambda: ModeLadder(st).set_mode(Mode.paper, actor="test"), out, "mode")
        assert entered.wait(5), "set_mode never reached its write"
        time.sleep(0.3)
        t_halt = _kill_and_time(env, out, release, time.perf_counter() + HOLD - 0.3)
    finally:
        release()
    mode.join(15)
    _assert_prompt_halt_then_ordered_kill(st, out, t_halt, EVENT_MODE_CHANGE)
    assert out["mode"] is Mode.paper
