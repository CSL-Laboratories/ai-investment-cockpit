"""W12 / SEC-04 — the OUT-OF-BAND, fail-closed kill switch (G12 repro regressions).

These encode the three adversarial vectors the G12 gate must now defeat, each written so the
property cannot silently regress:

  (1) OUT-OF-BAND TRIGGER preempts the order path. Firing the kill via the SEPARATE-process
      CLI (``orchestrator.halt.main(['--set', ...])`` — representing ``python -m
      orchestrator.halt --set`` run from another process, NOT ``killswitch()`` on the event
      loop) MID a submit burst sets the sentinel FIRST, so the NEXT ``place_equity_order`` is
      refused at gateway gate (a) BEFORE the broker call (the fake broker's ``submitted`` list
      does not grow).

  (2) An UNREADABLE sentinel FAILS CLOSED. If the flag probe raises ``OSError`` (disk/permission
      fault), ``brokers.gateway._halt_active`` counts it as SET and the submit is refused at
      stage ``'halt'`` — the gateway never trusts an unreadable HALT flag.

  (3) The killswitch MCP-DISCONNECT step (W12 SEAM 4) runs AFTER cancel and BEFORE disarm, is
      recorded (``mcp_disconnected``), and a ``disconnect_hook`` that RAISES still drops the
      mode to ``review_live`` (fail closed — a botched disconnect never leaves auto armed).

Halt paths are redirected to a per-test tmp file by the autouse ``COCKPIT_HALT_PATH`` override
in ``tests/orchestrator/conftest.py``, so nothing here touches a real sentinel.
"""
from __future__ import annotations

import asyncio
import errno
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

import brokers.gateway as gateway_mod
from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.gateway import OrderGateway
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode, Settings
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator import halt
from orchestrator.breakers import BreakerEngine
from orchestrator.gate import GateResult
from orchestrator.halt import clear_halt, default_halt_path, ensure_halt_dir, halt_is_set
from orchestrator.killswitch import killswitch
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_ADJUDICATION, EVENT_KILLSWITCH, OrchestratorState
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"w12-oob-kill-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'oob.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(),
                                   clock=FrozenClock(OPEN_UTC))


def _to_review_live(lad):
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")


def _approved_risk_recheck(_positions):
    return {"decision": "approved", "failed_checks": []}


@dataclass
class _Scn:
    order: object
    approval: ApprovalRecord
    rd: RiskDecision
    tuple_hash: str
    nonce: str


def _make_scenario(st, gw, *, pid: str, nonce: str, notional: str = "1500") -> _Scn:
    """A single fully-valid, submittable order on ``st`` (risk decision + signed covering
    adjudication + order + minted token) — mirrors tests/brokers/conftest.build_scenario, kept
    local because tests/brokers is not an importable package."""
    clk = st.clock
    rd = RiskDecision(
        id=new_id("rd"), proposal_id=pid, decision="approved", checks={}, failed_checks=[],
        warnings=[], max_allowed_notional=Decimal("5000"), human_required=False,
        evaluated_at=clk.now(), created_at=clk.now(),
    )
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": "approved"})
    canon = canonical_order_tuple(symbol="AAPL", side="buy", qty="10", order_type="limit",
                                  limit_price="150", tif="day")
    h = order_tuple_hash(canon)
    appr = ApprovalRecord(
        id=new_id("appr"), proposal_id=pid, risk_decision_id=rd.id, actor_user_id="lucas",
        user_decision="approve", surface="discord", order_tuple_hash=h,
        single_use_consumed=False, decided_at=clk.now(), created_at=clk.now(),
    )
    st.audit.record_state_change(appr, EVENT_ADJUDICATION, actor="lucas",
                                 payload={"decision": "approve", "proposal_id": pid,
                                          "risk_decision_id": rd.id, "order_tuple_hash": h})
    order = create_order(st, proposal_id=pid, risk_decision_id=rd.id, approval_id=appr.id,
                         account="paper", symbol="AAPL", side="buy", qty="10",
                         order_type="limit", limit_price="150", tif="day",
                         notional=notional, order_tuple_hash=h, actor="claude")
    gw.mint_submit_token(nonce=nonce, proposal_id=pid, risk_decision_id=rd.id,
                         order_tuple_hash=h)
    return _Scn(order, appr, rd, h, nonce)


def _place(gw, scn, ladder, breaker, st):
    return gw.place_equity_order(
        scn.order, approval=scn.approval, risk_decision=scn.rd, ladder=ladder,
        breaker_engine=breaker, risk_recheck=_approved_risk_recheck, positions={},
        token_nonce=scn.nonce, producing_runtime="claude", clock=st.clock,
    )


def _latest_killswitch_payload(st) -> dict:
    with st.session_factory() as s:
        entry = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_KILLSWITCH)
            .order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()
        return dict(entry.payload or {})


# =======================================================================================
# G12 repro (1) — the OUT-OF-BAND CLI trigger preempts the next submit at gate (a)
# =======================================================================================
def test_out_of_band_cli_set_refuses_next_submit_before_broker(st):
    ladder = ModeLadder(st)
    _to_review_live(ladder)
    breaker = BreakerEngine(st)
    fake = FakeAlpaca()
    gw = OrderGateway(AlpacaPaperBroker(client=fake), st=st)   # SEC-03: pin the trust root

    # --- the submit burst: two orders land through the ONE order path before the kill. ---
    for i in range(2):
        scn = _make_scenario(st, gw, pid=f"pre{i}", nonce=f"pre{i}")
        res = _place(gw, scn, ladder, breaker, st)
        assert res.ok and res.stage == "submitted", res
    assert len(fake.submitted) == 2
    submitted_before = len(fake.submitted)

    # --- fire the kill via the OUT-OF-BAND trigger: a SEPARATE process, NOT killswitch() on
    #     the event loop. main([...]) represents `python -m orchestrator.halt --set`. ---
    halt_path = default_halt_path()          # the path gate (a) reads (COCKPIT_HALT_PATH tmp)
    assert not halt_is_set(halt_path)
    rc = halt.main(["--set", "--reason", "panic-oob", "--path", str(halt_path)])
    assert rc == 0
    assert halt_is_set(halt_path)            # the sentinel is set FIRST, out of band

    # --- the NEXT submit is refused at gate (a) BEFORE the broker call. ---
    nxt = _make_scenario(st, gw, pid="post", nonce="post")
    refused = _place(gw, nxt, ladder, breaker, st)
    assert not refused.ok and refused.stage == "halt", refused
    assert len(fake.submitted) == submitted_before   # broker NEVER called after the kill

    # --- and clearing the sentinel (the operator re-arm gate) re-enables the path. ---
    assert halt.main(["--clear", "--path", str(halt_path)]) == 0
    assert not halt_is_set(halt_path)
    again = _make_scenario(st, gw, pid="after", nonce="after")
    ok = _place(gw, again, ladder, breaker, st)
    assert ok.ok and ok.stage == "submitted"
    assert len(fake.submitted) == submitted_before + 1


def test_out_of_band_cli_uses_env_default_path(st, monkeypatch):
    """`--set` with NO `--path` falls back to default_halt_path() (== COCKPIT_HALT_PATH here),
    the exact path gate (a) reads — proving the separate process and the gateway agree on the
    sentinel location without an explicit path argument."""
    ladder = ModeLadder(st)
    _to_review_live(ladder)
    breaker = BreakerEngine(st)
    fake = FakeAlpaca()
    gw = OrderGateway(AlpacaPaperBroker(client=fake), st=st)

    assert not halt_is_set(default_halt_path())
    assert halt.main(["--set"]) == 0          # no --path -> default_halt_path()
    assert halt_is_set(default_halt_path())

    scn = _make_scenario(st, gw, pid="p", nonce="n")
    refused = _place(gw, scn, ladder, breaker, st)
    assert not refused.ok and refused.stage == "halt"
    assert len(fake.submitted) == 0


# =======================================================================================
# G12 repro (2) — an UNREADABLE sentinel fails closed at stage 'halt'
# =======================================================================================
def test_unreadable_sentinel_fails_closed(st, monkeypatch):
    ladder = ModeLadder(st)
    _to_review_live(ladder)
    breaker = BreakerEngine(st)
    fake = FakeAlpaca()
    gw = OrderGateway(AlpacaPaperBroker(client=fake), st=st)

    # The HALT file is NOT set — but the probe raises (disk/permission fault). The gateway
    # imported halt_is_set into its own namespace, so patch it THERE (cross-module seam).
    def _boom(_path):
        raise OSError("sentinel unreadable")

    monkeypatch.setattr(gateway_mod, "halt_is_set", _boom)

    # _halt_active swallows the OSError and counts the flag as SET (fail closed).
    assert gateway_mod._halt_active(default_halt_path()) is True

    scn = _make_scenario(st, gw, pid="p", nonce="n")
    refused = _place(gw, scn, ladder, breaker, st)
    assert not refused.ok and refused.stage == "halt", refused
    assert len(fake.submitted) == 0          # never reached the broker


# =======================================================================================
# G12 repro (3) — killswitch MCP-disconnect: AFTER cancel, recorded; a raise still disarms
# =======================================================================================
def _arm_auto_veto(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=GateResult(unlockable=True, target="auto_veto"),
                            operator="op")
    assert res.armed and lad.current() is Mode.auto_veto
    return lad


def test_disconnect_hook_runs_after_cancel_and_is_recorded(st):
    lad = _arm_auto_veto(st)
    order: list[str] = []

    def cancel_hook() -> int:
        order.append("cancel")
        return 3

    def disconnect_hook() -> None:
        order.append("disconnect")

    res = killswitch(st, actor="op", reason="panic", cancel_hook=cancel_hook,
                     disconnect_hook=disconnect_hook)

    assert order == ["cancel", "disconnect"]          # disconnect AFTER cancel (SEAM 4 order)
    assert res.cancelled_count == 3
    assert res.mcp_disconnected is True
    assert res.mode_after is Mode.review_live and lad.current() is Mode.review_live
    assert _latest_killswitch_payload(st)["mcp_disconnected"] is True


def test_raising_disconnect_hook_still_drops_to_review_live(st):
    lad = _arm_auto_veto(st)
    order: list[str] = []

    def cancel_hook() -> int:
        order.append("cancel")
        return 1

    def disconnect_hook() -> None:
        order.append("disconnect")
        raise RuntimeError("MCP socket wedged")

    res = killswitch(st, actor="op", reason="panic", cancel_hook=cancel_hook,
                     disconnect_hook=disconnect_hook)

    # the disconnect was ATTEMPTED (after cancel) but raised -> recorded as not-disconnected,
    # yet the mode STILL drops to review_live (fail closed — a botched disconnect never leaves
    # an auto mode armed).
    assert order == ["cancel", "disconnect"]
    assert res.mcp_disconnected is False
    assert res.mode_after is Mode.review_live and lad.current() is Mode.review_live
    assert _latest_killswitch_payload(st)["mcp_disconnected"] is False


def test_no_disconnect_hook_leaves_mcp_disconnected_false(st):
    """The paper/in-process path passes no disconnect_hook — mcp_disconnected is False and the
    core set-flag -> cancel -> disarm sequence is byte-unchanged in behavior."""
    lad = _arm_auto_veto(st)
    res = killswitch(st, actor="op", reason="panic")
    assert res.mcp_disconnected is False
    assert res.mode_after is Mode.review_live and lad.current() is Mode.review_live
    assert halt_is_set(default_halt_path())
    clear_halt(default_halt_path())
    assert _latest_killswitch_payload(st)["mcp_disconnected"] is False


# =======================================================================================
# G12 repro B2 — a RAISING cancel_hook must NOT abort containment (fail-open closed)
# =======================================================================================
def test_killswitch_cancel_hook_raise_still_disarms_and_audits(st):
    """B2 (SEC-04 fail-open containment): the cancel_hook (the WP7 order-store DB hook — the
    likeliest to throw mid-crisis) RAISES. Before the fix this propagated out of the ``with
    st.lock`` block, so MCP-disconnect never ran, the mode STAYED auto_veto, and NO
    EVENT_KILLSWITCH row was written (clearing HALT then resumed auto). Now it is caught: the
    disconnect STILL runs, the mode STILL drops to review_live, and the kill is STILL audited with
    ``cancel_failed=True`` / ``cancelled=-1`` — a botched cancel never leaves an auto mode armed or
    the kill unaudited (Inv 6)."""
    lad = _arm_auto_veto(st)
    order: list[str] = []

    def cancel_hook() -> int:
        order.append("cancel")
        raise RuntimeError("order-store DB unreachable mid-crisis")

    def disconnect_hook() -> None:
        order.append("disconnect")

    res = killswitch(st, actor="op", reason="panic", cancel_hook=cancel_hook,
                     disconnect_hook=disconnect_hook)

    # the cancel raised, yet containment completed: disconnect ran AFTER the failed cancel and the
    # mode STILL dropped to review_live (never left armed).
    assert order == ["cancel", "disconnect"]
    assert res.cancelled_count == -1
    assert res.mcp_disconnected is True
    assert res.mode_after is Mode.review_live and lad.current() is Mode.review_live

    # the kill is AUDITED with the cancel failure flagged (Inv 6).
    payload = _latest_killswitch_payload(st)
    assert payload["cancel_failed"] is True
    assert payload["cancelled"] == -1
    assert payload["mode_after"] == "review_live"


# =======================================================================================
# G12 round-2 R2-S2 — the HALT probe FAILS CLOSED on the errno class Path.exists() swallows
# =======================================================================================
def test_halt_probe_fails_closed_on_unreadable_sentinel(st, monkeypatch):
    """R2-S2 (SEC-04): the sentinel is genuinely PRESENT-but-UNREADABLE — a not-yet-ready network
    path / an ENOTDIR component. ``Path.exists()`` SWALLOWS ``ENOTDIR`` (and EBADF / WSAENOTREADY /
    ERROR_NOT_READY / ERROR_INVALID_NAME / ERROR_CANT_RESOLVE_FILENAME) and returns ``False``
    WITHOUT raising, so the gateway's ``except OSError`` never fires and the order slips at BOTH
    probe sites. The raw ``os.stat`` probe treats such an OSError as SET (fail closed), so the
    primitive itself reports True AND the very next gateway submit is refused at stage 'halt'.

    Note: distinct from ``test_unreadable_sentinel_fails_closed`` (which patches the gateway's
    ``halt_is_set`` to RE-RAISE OSError and exercises the gateway's belt-and-suspenders catch).
    Here nothing re-raises out of the primitive — the swallowed-errno class is caught INSIDE
    ``halt_is_set`` so ALL callers inherit the fail-closed policy."""
    ladder = ModeLadder(st)
    _to_review_live(ladder)
    breaker = BreakerEngine(st)
    fake = FakeAlpaca()
    gw = OrderGateway(AlpacaPaperBroker(client=fake), st=st)

    target = default_halt_path()
    real_stat = os.stat

    def _stat_enotdir(p, *a, **k):
        # ENOTDIR (winerror ERROR_NOT_READY=21-class) is precisely what Path.exists() would
        # swallow; the sentinel is PRESENT but unreadable. Only the sentinel path faults.
        if str(p) == str(target):
            raise OSError(errno.ENOTDIR, "sentinel path component not ready")
        return real_stat(p, *a, **k)

    monkeypatch.setattr(os, "stat", _stat_enotdir)

    # the PRIMITIVE fails closed: an unreadable (not merely absent) sentinel reads as SET.
    assert halt_is_set(target) is True

    scn = _make_scenario(st, gw, pid="p", nonce="n")
    refused = _place(gw, scn, ladder, breaker, st)
    assert not refused.ok and refused.stage == "halt", refused
    assert len(fake.submitted) == 0          # the broker was never reached


# =======================================================================================
# G12 round-3 R3-S3 — the HALT probe FAILS CLOSED on the Windows winerror family os.stat
# collapses onto FileNotFoundError(errno 2), and on a NUL-byte-path ValueError
# =======================================================================================
def test_halt_probe_fails_closed_on_winerror_not_file_not_found(st, monkeypatch):
    """R3-S3 (SEC-04, the DEPLOY PLATFORM): on Windows ``os.stat`` maps ERROR_PATH_NOT_FOUND(3),
    ERROR_NOT_READY(21), ERROR_BAD_NETPATH(53), a missing subst/removable drive and an ENOTDIR path
    component ALL onto ``FileNotFoundError`` with ``errno == 2`` — indistinguishable by errno from a
    genuinely-absent sentinel. A blanket ``except FileNotFoundError: return False`` therefore reads a
    genuinely-SET sentinel on a network / removable / subst mount that TRANSIENTLY DROPS as ABSENT,
    flipping the gateway REFUSE -> SUBMIT. The class-closing fix trusts ``FileNotFoundError`` as
    absent ONLY on ``winerror == 2`` (ERROR_FILE_NOT_FOUND: final component absent, path valid);
    EVERY other ``winerror`` fails CLOSED. Here the sentinel is genuinely SET but the probe raises
    ``FileNotFoundError(winerror=3)`` (dropped mount): ``halt_is_set`` must read True AND the next
    submit is refused at stage 'halt', broker untouched."""
    ladder = ModeLadder(st)
    _to_review_live(ladder)
    breaker = BreakerEngine(st)
    fake = FakeAlpaca()
    gw = OrderGateway(AlpacaPaperBroker(client=fake), st=st)

    target = default_halt_path()
    real_stat = os.stat

    def _stat_dropped_mount(p, *a, **k):
        # ERROR_PATH_NOT_FOUND(3) — the sentinel's mount transiently dropped; os.stat collapses it
        # onto FileNotFoundError(errno 2) with winerror 3. Only the sentinel path faults.
        if str(p) == str(target):
            error = FileNotFoundError(2, "dropped network/removable/subst mount", str(target))
            # POSIX ignores the Windows-only fourth constructor argument, so set the attribute
            # explicitly to keep this Windows deployment-path simulation portable on macOS/Linux.
            error.winerror = 3
            raise error
        return real_stat(p, *a, **k)

    monkeypatch.setattr(os, "stat", _stat_dropped_mount)

    # the PRIMITIVE fails closed: a winerror-3 (NOT winerror-2) FileNotFoundError reads as SET.
    assert halt_is_set(target) is True

    scn = _make_scenario(st, gw, pid="p", nonce="n")
    refused = _place(gw, scn, ladder, breaker, st)
    assert not refused.ok and refused.stage == "halt", refused
    assert len(fake.submitted) == 0          # the dropped-mount sentinel never let an order through


def test_halt_probe_fails_closed_on_nul_byte_path(st, monkeypatch):
    """R3-S3 (SEC-04): a NUL byte in the sentinel path makes ``os.stat`` raise ``ValueError`` — NOT
    an ``OSError`` subclass, so NEITHER ``halt_is_set``'s ``FileNotFoundError`` nor its ``OSError``
    branch caught it before, and the exception would propagate out of the probe. The class-closing
    fix adds ``except ValueError: return True`` so this too fails closed."""
    ladder = ModeLadder(st)
    _to_review_live(ladder)
    breaker = BreakerEngine(st)
    fake = FakeAlpaca()
    gw = OrderGateway(AlpacaPaperBroker(client=fake), st=st)

    target = default_halt_path()
    real_stat = os.stat

    def _stat_nul(p, *a, **k):
        if str(p) == str(target):
            raise ValueError("embedded null byte")
        return real_stat(p, *a, **k)

    monkeypatch.setattr(os, "stat", _stat_nul)

    assert halt_is_set(target) is True       # ValueError => SET (fail closed)

    scn = _make_scenario(st, gw, pid="p", nonce="n")
    refused = _place(gw, scn, ladder, breaker, st)
    assert not refused.ok and refused.stage == "halt", refused
    assert len(fake.submitted) == 0


def test_halt_probe_positive_control_absent_sentinel_still_flows(st):
    """POSITIVE CONTROL for R3-S3: the fail-closed winerror-2 invariant must NOT over-fire on the
    happy path. A GENUINELY-ABSENT sentinel (final component missing, PARENT present -> Windows
    winerror 2 / POSIX ENOENT) reads as NOT SET, so a legit approved order still reaches the broker
    exactly once. This proves the tightened probe still lets real traffic through."""
    ladder = ModeLadder(st)
    _to_review_live(ladder)
    breaker = BreakerEngine(st)
    fake = FakeAlpaca()
    gw = OrderGateway(AlpacaPaperBroker(client=fake), st=st)

    target = default_halt_path()
    ensure_halt_dir(target)                  # parent present -> absent sentinel yields winerror 2
    assert not halt_is_set(target)           # genuinely absent => NOT set (does not fail closed)

    scn = _make_scenario(st, gw, pid="ok", nonce="ok")
    ok = _place(gw, scn, ladder, breaker, st)
    assert ok.ok and ok.stage == "submitted", ok
    assert len(fake.submitted) == 1          # legit order flows through the tightened probe


# =======================================================================================
# G12 round-2 R2-S1 — default_halt_path() is ACCOUNT-INDEPENDENT (operator --set == service read)
# =======================================================================================
def test_default_halt_path_is_account_independent(monkeypatch, tmp_path):
    """R2-S1 (SEC-04): with ``COCKPIT_HALT_PATH`` UNSET, the default must be machine-wide, NOT
    per-user. The old ``LOCALAPPDATA`` / ``XDG_STATE_HOME`` fallback diverged between the operator's
    interactive ``--set`` (their account) and the NSSM service (its account), so the kill wrote
    where the gateway never looked. Resolved under two DIFFERENT per-user envs it must yield the
    SAME path; and with ``COCKPIT_HALT_PATH`` pinned, both envs resolve to the pin."""
    monkeypatch.delenv("COCKPIT_HALT_PATH", raising=False)   # the autouse conftest pins it; unpin

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "operatorA"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "operatorA_xdg"))
    p1 = default_halt_path()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "serviceB"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "serviceB_xdg"))
    p2 = default_halt_path()

    assert p1 == p2                                          # both accounts converge (divergence-proof)
    # and the default must NOT live under either per-user dir.
    assert "operatorA" not in str(p1) and "serviceB" not in str(p2)

    # with COCKPIT_HALT_PATH pinned, BOTH per-user envs resolve to the SAME pinned path.
    pinned = tmp_path / "shared" / "HALT"
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(pinned))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "operatorA"))
    assert default_halt_path() == Path(str(pinned))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "serviceB"))
    assert default_halt_path() == Path(str(pinned))


# =======================================================================================
# G12 round-5 R5-S2 — default_halt_path() must NOT read the MUTABLE %PROGRAMDATA% env var
# =======================================================================================
def test_default_halt_path_independent_of_programdata_env(monkeypatch):
    """R5-S2 (SEC-04): with ``COCKPIT_HALT_PATH`` UNSET, the Windows default must NOT be derived
    from ``%PROGRAMDATA%``. ``PROGRAMDATA`` is a per-account-MUTABLE env var (an
    ``HKCU\\Environment`` shadow, or a ``CreateProcessWithLogonW`` service env block lacking
    ``CreateEnvironmentBlock``), so the operator's out-of-band ``--set`` process and the NSSM-service
    gateway can resolve DIFFERENT bases — the kill writes where the gateway never looks. The default
    is now a FIXED HARDCODED LITERAL: two DIFFERENT injected ``PROGRAMDATA`` values (and a DELETED
    ``PROGRAMDATA``) must all resolve to the IDENTICAL path, and that path must contain NEITHER
    injected value (proving the env var is never consulted)."""
    monkeypatch.delenv("COCKPIT_HALT_PATH", raising=False)   # the autouse conftest pins it; unpin

    monkeypatch.setenv("PROGRAMDATA", "D:/operator-shadow/ProgramData")
    p_operator = default_halt_path()
    monkeypatch.setenv("PROGRAMDATA", "E:/service-envblock/ProgramData")
    p_service = default_halt_path()
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    p_missing = default_halt_path()

    # all three resolve to the SAME sentinel — no per-account divergence.
    assert p_operator == p_service == p_missing
    # and NEITHER injected PROGRAMDATA value leaked into the resolved path (env var not consulted).
    for resolved in (p_operator, p_service, p_missing):
        assert "operator-shadow" not in str(resolved)
        assert "service-envblock" not in str(resolved)


def test_default_halt_path_positive_control_explicit_pin_wins(monkeypatch, tmp_path):
    """POSITIVE CONTROL for R5-S2: the fixed-literal default must NOT clobber the
    ``COCKPIT_HALT_PATH`` production pin. With it SET, ``default_halt_path()`` returns EXACTLY that
    path — even while a bogus ``PROGRAMDATA`` is present — so the first-precedence override (the
    divergence-proof production pin AND the test-isolation seam) still resolves correctly."""
    pinned = tmp_path / "shared" / "HALT"
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(pinned))
    monkeypatch.setenv("PROGRAMDATA", "D:/should-be-ignored/ProgramData")
    assert default_halt_path() == Path(str(pinned))
    assert "should-be-ignored" not in str(default_halt_path())


# =======================================================================================
# G12 round-2 R2-S3 — a BaseException-raising hook still disarms + audits (not just Exception)
# =======================================================================================
def test_killswitch_base_exception_hook_still_disarms_and_audits(st):
    """R2-S3 (SEC-04): the hook catches must be ``except BaseException``, NOT ``except Exception``.
    An ``asyncio.CancelledError`` (or ``KeyboardInterrupt`` / ``SystemExit``) raised by the injected
    hook — exactly the W17 live-async-disconnect loop-teardown crisis — is a ``BaseException`` and
    would ESCAPE ``st.lock`` under a plain ``except Exception``, skipping step 4 disarm and step 5
    EVENT_KILLSWITCH audit => auto stays armed + the kill goes unaudited. The kill must COMPLETE
    containment: disarm to review_live + audit ALWAYS run; we never re-raise."""
    # (a) a disconnect_hook raising asyncio.CancelledError still disarms + audits.
    lad = _arm_auto_veto(st)
    order: list[str] = []

    def cancel_ok() -> int:
        order.append("cancel")
        return 2

    def disconnect_cancelled() -> None:
        order.append("disconnect")
        raise asyncio.CancelledError()

    res = killswitch(st, actor="op", reason="panic", cancel_hook=cancel_ok,
                     disconnect_hook=disconnect_cancelled)
    assert order == ["cancel", "disconnect"]
    assert res.mcp_disconnected is False
    assert res.mode_after is Mode.review_live and lad.current() is Mode.review_live
    payload = _latest_killswitch_payload(st)
    assert payload["mcp_disconnected"] is False
    assert payload["mode_after"] == "review_live"

    # re-arm for part (b): the first kill SET the HALT sentinel, which arm refuses while set.
    clear_halt(default_halt_path())

    # (b) a cancel_hook raising asyncio.CancelledError still disconnects, disarms + audits.
    lad2 = _arm_auto_veto(st)
    order2: list[str] = []

    def cancel_cancelled() -> int:
        order2.append("cancel")
        raise asyncio.CancelledError()

    def disconnect_ok() -> None:
        order2.append("disconnect")

    res2 = killswitch(st, actor="op", reason="panic2", cancel_hook=cancel_cancelled,
                      disconnect_hook=disconnect_ok)
    assert order2 == ["cancel", "disconnect"]
    assert res2.cancelled_count == -1
    assert res2.mcp_disconnected is True
    assert res2.mode_after is Mode.review_live and lad2.current() is Mode.review_live
    payload2 = _latest_killswitch_payload(st)
    assert payload2["cancel_failed"] is True
    assert payload2["cancelled"] == -1
    assert payload2["mode_after"] == "review_live"


# ---- G12 R6 regression: a RELATIVE sentinel pin diverges by CWD -> fail closed ----
def test_relative_cockpit_halt_path_is_rejected(monkeypatch):
    """R6-S: a RELATIVE COCKPIT_HALT_PATH resolves CWD-relative, so the NSSM-service gateway and the
    operator's --set process (different CWDs) read/write DIFFERENT files -> fail OPEN. default_halt_path()
    must REJECT a non-absolute pin (compose's ensure_halt_dir triggers it at boot, failing closed)."""
    monkeypatch.setenv("COCKPIT_HALT_PATH", "cockpit-HALT")     # relative
    with pytest.raises(ValueError):
        default_halt_path()


def test_relative_cli_path_is_rejected():
    """R6-S: a relative --path would make the OOB --set process write a CWD-relative sentinel the
    gateway never reads. main() must error (SystemExit) rather than drop a diverging sentinel."""
    with pytest.raises(SystemExit):
        halt.main(["--set", "--path", "relative-HALT"])


def test_absolute_cockpit_halt_path_positive_control(monkeypatch, tmp_path):
    """R6-S positive control: an ABSOLUTE pin still resolves to exactly that path."""
    p = tmp_path / "abs-HALT"
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(p))
    assert default_halt_path() == p
