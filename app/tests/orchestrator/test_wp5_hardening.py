"""WP5 HARDENING REGRESSION SUITE — the four reproduced bypasses from the script-4c
adversarial pass on ``orchestrator/mode_ladder.py`` (Inv 1/2/7; SEC-04/20/23).

Each test encodes an attack a skeptic *reproduced* against the first build, so the fix
cannot silently regress. Authored by the integrator when clearing the G5 must_fix list.

  * finding #1 (skeptics 1 & 3): ``current()`` read the mutable ``mode_state.mode`` column,
    so ``UPDATE mode_state SET mode='auto_full'`` flipped the live mode while ``verify_chain``
    stayed green. Fixed: the mode is the HMAC-signed ``payload['to']``; a divergent/forged
    record fails CLOSED to ``read_only``.
  * finding #2 (skeptic 5): ``compute_gate`` verified the chain in one transaction then
    counted rows in another with no binding, so rows inserted in the window counted as
    history. Fixed: the counted set is re-verified and bound to the verified head.
  * findings #3/#4 (skeptic 4): ``arm_auto_mode`` never checked the killswitch HALT sentinel,
    so an arm re-armed auto after (or raced) a killswitch. Fixed: arm refuses while HALT is
    set, under the shared lock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.gate import GateResult, GateThresholds, compute_gate
from orchestrator.halt import clear_halt, default_halt_path, halt_is_set
from orchestrator.killswitch import killswitch
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_ADJUDICATION, EVENT_MODE_CHANGE, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState
from storage.audit import audit_meta
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"wp5-hardening-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'harden.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(),
                                   clock=FrozenClock(OPEN_UTC))


def _good_gate(target="auto_veto"):
    return GateResult(unlockable=True, target=target)


def _to_review_live(lad):
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")


def _latest_mode_change_row_id(st):
    with st.session_factory() as s:
        entry = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_MODE_CHANGE)
            .order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()
        return (entry.refs or {}).get("row_id")


# =======================================================================================
# finding #1 — mutable mode_state.mode column flip must NOT change current()
# =======================================================================================
def test_mutable_mode_column_flip_cannot_grant_autonomy(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    assert lad.current() is Mode.review_live
    row_id = _latest_mode_change_row_id(st)

    # the exact reproduced attack: a local DB writer (no HMAC key) flips the mutable column.
    with st.session_factory() as s, s.begin():
        s.execute(update(ModeState).where(ModeState.id == row_id).values(mode="auto_full"))

    # current() ignores the mutable column and fails CLOSED on the row<->signed divergence.
    assert lad.current() is Mode.read_only
    assert lad.current() not in (Mode.auto_veto, Mode.auto_full)   # never reaches auto
    # the signed chain itself is untouched -> still verifies (the tamper was off-chain).
    assert st.audit.verify_chain().ok


def test_payload_tamper_fails_closed(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    # forge the SIGNED payload to an auto mode -> breaks that entry's HMAC.
    with st.session_factory() as s, s.begin():
        entry = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_MODE_CHANGE)
            .order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()
        entry.payload = {**entry.payload, "to": "auto_full"}
    assert lad.current() is Mode.read_only          # verify_entry fails -> safe floor
    assert not st.audit.verify_chain().ok           # and the tamper is detectable


def test_current_is_signed_value_on_the_happy_path(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate(), operator="op")
    assert res.armed and lad.current() is Mode.auto_veto     # untampered -> signed value


# =======================================================================================
# finding #2 — gate must not count rows inserted after verification (verify/count TOCTOU)
# =======================================================================================
def _seed_unlockable(st, start):
    st.clock = FrozenClock(start)
    st.audit._clock = st.clock
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op", clock=FrozenClock(start))
    lad.set_mode(Mode.review_live, actor="op", clock=FrozenClock(start))
    for _ in range(30):
        st.audit.append(EVENT_ADJUDICATION, actor="op", payload={"decision": "approve"})
    return lad


def test_gate_rejects_rows_injected_after_verification(st, monkeypatch):
    """Simulate the verify->count window: verify_chain returns the CLEAN head it saw, then
    forged rows land before the counting read. The binding re-verification must fail closed."""
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_unlockable(st, start)
    now = FrozenClock(start + timedelta(weeks=5)).now()

    # sanity: with the genuine chain, the gate unlocks.
    assert compute_gate(st, Mode.auto_veto, now=now, thresholds=GateThresholds()).unlockable

    clean = st.audit.verify_chain()                 # the head an attacker's verify would see

    # forge extra "approved" adjudications directly into the audit table (bad HMAC, seq>head).
    with st.session_factory() as s, s.begin():
        head = clean.head_seq
        for i in range(40):
            s.add(AuditEntry(
                id=f"forged{i}", seq=head + 1 + i, prev_hash="x" * 64, entry_hash="0" * 64,
                event_type=EVENT_ADJUDICATION, actor="attacker",
                payload={"decision": "approve", "_ts": now.replace(tzinfo=None).isoformat()},
                refs={}, created_at=now.replace(tzinfo=None),
            ))

    # pin verify_chain to the STALE clean head (the TOCTOU: it ran before the inserts).
    monkeypatch.setattr(st.audit, "verify_chain", lambda: clean)

    res = compute_gate(st, Mode.auto_veto, now=now, thresholds=GateThresholds())
    assert not res.unlockable and res.metrics.get("chain_ok") is False   # bound + fails closed


# =======================================================================================
# findings #3/#4 — arm must refuse while the killswitch HALT sentinel is set (SEC-04)
# =======================================================================================
def test_arm_refused_while_halt_set_then_allowed_after_clear(st):
    lad = ModeLadder(st)
    _to_review_live(lad)

    # killswitch (default path == the per-test COCKPIT_HALT_PATH) sets HALT + drops mode.
    res = killswitch(st, actor="op", reason="panic")
    assert res.mode_after is Mode.review_live
    assert halt_is_set(default_halt_path())

    # the reproduced bypass: arm auto with a passing gate while HALT is set -> now REFUSED.
    refused = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                                gate_result=_good_gate(), operator="op")
    assert not refused.armed and any("HALT" in r for r in refused.reasons)
    assert lad.current() is Mode.review_live

    # only after the operator clears the sentinel can a gated arm succeed.
    clear_halt(default_halt_path())
    ok = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                           gate_result=_good_gate(), operator="op")
    assert ok.armed and lad.current() is Mode.auto_veto


# =======================================================================================
# re-verify finding (skeptics 1/4/5) — current() must detect TRUNCATION, not just on-row
# tamper: deleting newer disarm/drop rows must not resurrect an old auto-arm.
# =======================================================================================
def _latest_mode_change_seq(st):
    with st.session_factory() as s:
        return s.execute(
            select(AuditEntry.seq).where(AuditEntry.event_type == EVENT_MODE_CHANGE)
            .order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()


def test_current_detects_tail_truncation_via_watermark(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate(), operator="op")
    assert res.armed and lad.current() is Mode.auto_veto
    arm_seq = _latest_mode_change_seq(st)                 # the auto arm entry's seq
    killswitch(st, actor="op", reason="panic")            # drops to review_live at a higher seq
    assert lad.current() is Mode.review_live

    # attacker deletes every row after the auto arm so it reads as 'latest' (watermark intact).
    with st.session_factory() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > arm_seq))

    assert not st.audit.verify_chain().ok                 # head_seq < signed watermark
    assert lad.current() is Mode.read_only                # oracle fails closed, NOT auto_veto


def test_current_and_gate_fail_closed_when_watermark_also_deleted(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate(), operator="op")
    assert res.armed
    arm_seq = _latest_mode_change_seq(st)
    killswitch(st, actor="op", reason="panic")

    # truncate the tail AND wipe the signed watermark (the fail-open attempt).
    with st.session_factory() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > arm_seq))
        s.execute(audit_meta.delete())

    assert not st.audit.verify_chain().ok                 # fix B: rows present + no watermark
    assert lad.current() is Mode.read_only                # fix A: oracle consults verify_chain


def test_gate_fails_closed_when_bypass_event_truncated_with_watermark(st):
    """The reproduced skeptic-2 vector: truncate away a recorded bypass-attempt event AND the
    watermark; the gate must not then read unlockable=True."""
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_unlockable(st, start)
    st.audit.append("hard_check_failed", actor="op", payload={"proposal_id": "p1"})
    now = FrozenClock(start + timedelta(weeks=5)).now()
    assert not compute_gate(st, Mode.auto_veto, now=now).unlockable    # blocked by the bypass event

    with st.session_factory() as s, s.begin():
        bseq = s.execute(
            select(AuditEntry.seq).where(AuditEntry.event_type == "hard_check_failed")
        ).scalar_one()
        s.execute(delete(AuditEntry).where(AuditEntry.seq >= bseq))    # drop the bypass event
        s.execute(audit_meta.delete())                                 # and the watermark

    res = compute_gate(st, Mode.auto_veto, now=now)
    assert not res.unlockable and res.metrics.get("chain_ok") is False
