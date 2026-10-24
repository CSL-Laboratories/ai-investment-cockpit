"""CSL-468 — the in-process kill switch publishes HALT before it waits on ``st.lock``.

A holder of ``st.lock`` (hung fence, broker I/O) must not delay the sentinel. Cancel,
disconnect, disarm and the killswitch audit still run under that lock. ``set_halt``
also fsyncs the file, and a directory fsync that fails after the directory was opened
fails the call.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.gate import GateResult
from orchestrator.halt import halt_is_set, set_halt
from orchestrator.killswitch import killswitch
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_KILLSWITCH, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"csl-468-killswitch-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
# Wall-clock bound from the call start. On origin/main the call blocks inside
# ``st.lock`` before ``set_halt``, so the sentinel stays absent for this whole wait.
HALT_VISIBLE_S = 1.0
LOCK_HOLD_S = 3.0


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'csl468.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(
        sf, hmac_key=KEY, settings=Settings(), clock=FrozenClock(OPEN_UTC),
    )


def _arm_auto_veto(st):
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    ladder.set_mode(Mode.review_live, actor="op")
    armed = ladder.arm_auto_mode(
        Mode.auto_veto,
        decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"),
        operator="op",
    )
    assert armed.armed and ladder.current() is Mode.auto_veto
    return ladder


def _killswitch_audit(st) -> dict:
    with st.session_factory() as session:
        entry = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_KILLSWITCH)
            .order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()
        return dict(entry.payload or {})


class _LockProbe:
    """Count ``st.lock`` entries without changing the real lock."""

    def __init__(self, inner):
        self._inner = inner
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self._inner.__enter__()

    def __exit__(self, exc_type, exc, tb):
        return self._inner.__exit__(exc_type, exc, tb)


def test_killswitch_writes_halt_while_st_lock_is_held(st, tmp_path):
    """HALT is visible while another thread still holds ``st.lock``.

    The holder keeps the lock until the sentinel appears or ``LOCK_HOLD_S`` elapses,
    then releases it. Cancel and the mode drop run only after that release.
    """
    ladder = _arm_auto_veto(st)
    halt_path = tmp_path / "HALT"
    assert halt_is_set(halt_path) is False

    held = threading.Event()
    observed: dict = {}
    cancel_at: list[float] = []

    def holder():
        with st.lock:
            held.set()
            deadline = time.monotonic() + LOCK_HOLD_S
            while time.monotonic() < deadline:
                if halt_is_set(halt_path):
                    observed["seen_at"] = time.monotonic()
                    observed["cancel_during_hold"] = len(cancel_at)
                    break
                time.sleep(0.01)
            observed["released_at"] = time.monotonic()

    def cancel() -> int:
        cancel_at.append(time.monotonic())
        return 1

    worker = threading.Thread(target=holder, name="st-lock-holder")
    worker.start()
    try:
        assert held.wait(timeout=2.0), "holder did not acquire st.lock"
        call_started = time.monotonic()
        result = killswitch(
            st, actor="op", reason="panic", halt_path=halt_path, cancel_hook=cancel,
        )
    finally:
        worker.join(timeout=LOCK_HOLD_S + 2.0)
    assert not worker.is_alive()

    assert "seen_at" in observed, (
        "HALT sentinel was not visible while the other thread held st.lock"
    )
    elapsed = observed["seen_at"] - call_started
    assert elapsed <= HALT_VISIBLE_S, (
        f"HALT became visible {elapsed:.3f}s after the call; bound is {HALT_VISIBLE_S:.1f}s"
    )
    assert observed["cancel_during_hold"] == 0
    assert len(cancel_at) == 1
    assert cancel_at[0] >= observed["released_at"]
    assert result.cancelled_count == 1
    assert result.mode_after is Mode.review_live
    assert ladder.current() is Mode.review_live
    assert halt_is_set(halt_path) is True

    payload = _killswitch_audit(st)
    assert payload["reason"] == "panic"
    assert payload["cancelled"] == 1
    assert payload["cancel_failed"] is False
    assert payload["halt_path"] == str(halt_path)
    assert payload["mode_after"] == Mode.review_live.value
    assert st.audit.verify_chain().ok


def test_set_halt_error_does_not_acquire_st_lock(st, monkeypatch):
    """A ``set_halt`` failure propagates and does not disarm under ``st.lock``."""
    ladder = _arm_auto_veto(st)
    probe = _LockProbe(st.lock)
    st.lock = probe
    cancelled: list[int] = []

    def boom(path, reason="killswitch"):
        raise OSError("halt write failed")

    monkeypatch.setattr("orchestrator.killswitch.set_halt", boom)
    with pytest.raises(OSError, match="halt write failed"):
        killswitch(
            st, actor="op", reason="disk", halt_path="unused",
            cancel_hook=lambda: cancelled.append(1) or 1,
        )

    assert probe.entered == 0
    assert cancelled == []
    assert ladder.current() is Mode.auto_veto
    assert _killswitch_audit_missing(st)


def _killswitch_audit_missing(st) -> bool:
    with st.session_factory() as session:
        entry = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_KILLSWITCH)
            .limit(1)
        ).scalar_one_or_none()
    return entry is None


def _directory_openable(directory) -> bool:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(os.fspath(directory), flags)
    except OSError:
        return False
    os.close(fd)
    return True


def test_halt_sentinel_fsyncs_file_and_directory(tmp_path, monkeypatch):
    """File fsync is mandatory. A directory fsync error after a successful open raises.

    When the directory cannot be opened, ``set_halt`` still returns after the file
    fsync (Windows). That open failure is simulated below on every platform.
    """
    synced: list = []
    fd_path: dict[int, str] = {}
    real_open = os.open
    real_fsync = os.fsync

    def tracked_open(file, flags, mode=0o777, *, dir_fd=None):
        kwargs = {}
        if dir_fd is not None:
            kwargs["dir_fd"] = dir_fd
        fd = real_open(file, flags, mode, **kwargs)
        fd_path[fd] = os.fspath(file)
        return fd

    def tracked_fsync(fd):
        synced.append(fd_path.get(fd))
        return real_fsync(fd)

    monkeypatch.setattr("orchestrator.halt.os.open", tracked_open)
    monkeypatch.setattr("orchestrator.halt.os.fsync", tracked_fsync)
    path = tmp_path / "halt-dir" / "HALT"
    set_halt(path, reason="durability")
    text = path.read_text(encoding="utf-8")
    assert "durability" in text

    file_key = os.fspath(path)
    dir_key = os.fspath(path.parent)
    assert synced.count(file_key) >= 1
    directory_open = _directory_openable(path.parent)
    if directory_open:
        assert synced.count(dir_key) >= 1

        def failing_directory_fsync(fd):
            if fd_path.get(fd) == dir_key:
                raise OSError("directory fsync failed")
            return real_fsync(fd)

        monkeypatch.setattr("orchestrator.halt.os.fsync", failing_directory_fsync)
        with pytest.raises(OSError, match="directory fsync failed"):
            set_halt(path, reason="directory-fsync-fails")
    else:
        assert dir_key not in synced

    def open_fails_for_directory(file, flags, mode=0o777, *, dir_fd=None):
        if os.fspath(file) == dir_key and not (flags & os.O_CREAT):
            raise OSError("directory open failed")
        kwargs = {}
        if dir_fd is not None:
            kwargs["dir_fd"] = dir_fd
        return real_open(file, flags, mode, **kwargs)

    monkeypatch.setattr("orchestrator.halt.os.open", open_fails_for_directory)
    monkeypatch.setattr("orchestrator.halt.os.fsync", real_fsync)
    set_halt(path, reason="directory-open-best-effort")
    assert "directory-open-best-effort" in path.read_text(encoding="utf-8")
