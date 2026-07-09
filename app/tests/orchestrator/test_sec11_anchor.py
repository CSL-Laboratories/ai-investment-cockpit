"""WP6 SEC-11 CLOSURE REGRESSION — the replayable-watermark rollback the WP5 build deferred.

WP5 documented (storage/audit.py SEC-11 CAVEAT + security-hardening-backlog SEC-11) that the
in-DB signed watermark is REPLAYABLE: a keyless writer with DB *read* access captures a past
``(head_seq, head_seq_sig)`` pair, and after a blocking event lands at a higher seq runs
``DELETE FROM audit_entry WHERE seq > N`` + replays the captured ``audit_meta`` rows.
``verify_chain()`` then returns ``ok=True`` (present, validly-signed, head==watermark) with the
blocking event ERASED — so, before this closure, ``compute_gate`` read ``unlockable=True`` and
``ModeLadder.current()`` resurrected an old auto-arm with NO HMAC key touched.

WP6 wires the EXTERNAL out-of-DB witness (``bridge/anchor.py`` → ``OrchestratorState.anchor_store``)
into BOTH ``compute_gate`` (hard prerequisite for an auto unlock) and ``ModeLadder.current()``
(auto mode-read). This suite reproduces the exact replay attack and proves the anchor closes it:
the in-DB ``verify_chain()`` is DEFEATED (returns ok), yet the gate and the mode oracle both fail
CLOSED because the verified head fell below the last external anchor.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from bridge.anchor import GitAuditLogAnchorStore, InMemoryAnchorStore, post_head_anchor
from bridge.witness import DiscordAuditLogWitness, GitCommitWitness
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.gate import GateResult, compute_gate
from orchestrator.mode_ladder import ModeLadder
from orchestrator.killswitch import killswitch
from orchestrator.state import EVENT_MODE_CHANGE, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from storage.audit import audit_meta
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"sec11-anchor-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    """A state WITH an external anchor store wired (a properly-configured auto deployment)."""
    engine = make_engine(f"sqlite:///{(tmp_path / 'anchor.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(
        sf, hmac_key=KEY, settings=Settings(), clock=FrozenClock(OPEN_UTC),
        anchor_store=InMemoryAnchorStore(),
    )


def _good_gate(target="auto_veto"):
    return GateResult(unlockable=True, target=target)


def _arm_auto_veto(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate(), operator="op")
    assert res.armed
    return lad


def _latest_mode_change_seq(st):
    with st.session_factory() as s:
        return s.execute(
            select(AuditEntry.seq).where(AuditEntry.event_type == EVENT_MODE_CHANGE)
            .order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()


def _capture_watermark(st):
    with st.session_factory() as s:
        return list(s.execute(select(audit_meta.c.k, audit_meta.c.v)).all())


def _replay_rollback(st, to_seq, captured_watermark):
    """The keyless attack: truncate the tail back to ``to_seq`` and REPLAY the captured
    ``audit_meta`` rows so the in-DB watermark check passes on the rolled-back head."""
    with st.session_factory() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > to_seq))
        s.execute(audit_meta.delete())
        for k, v in captured_watermark:
            s.execute(audit_meta.insert().values(k=k, v=v))


# =======================================================================================
# THE deferred hole, now closed: replayed watermark defeats verify_chain, anchor catches it
# =======================================================================================
def test_watermark_replay_rollback_closed_by_external_anchor(st):
    lad = _arm_auto_veto(st)
    # Post the external anchor at the armed head; current() (now anchor-aware) still reads auto.
    post_head_anchor(st, record=False)
    assert lad.current() is Mode.auto_veto
    arm_seq = _latest_mode_change_seq(st)
    captured = _capture_watermark(st)                 # what the attacker reads over time (seq==arm_seq)

    # A blocking event lands (a killswitch drop), and the witness is anchored PAST it — this is
    # the scheduler's SEC-11 cadence bounding the replay window.
    killswitch(st, actor="op", reason="loss breaker")
    assert lad.current() is Mode.review_live
    post_head_anchor(st, record=False)
    anchored_seq, _ = st.anchor_store.latest()
    assert anchored_seq > arm_seq

    # THE REPLAY: roll the chain back to the auto-arm head and replay the captured watermark.
    _replay_rollback(st, arm_seq, captured)

    # The in-DB check is DEFEATED — exactly the WP5-deferred hole (present, signed, head==wm).
    assert st.audit.verify_chain().ok

    # ...but the EXTERNAL anchor closes it: both the mode oracle and the gate fail CLOSED.
    assert lad.current() is Mode.read_only
    gate = compute_gate(st, Mode.auto_veto)
    assert not gate.unlockable
    assert gate.metrics.get("anchor_ok") is False
    assert any("anchor" in r.lower() for r in gate.reasons)


def test_current_stands_at_auto_with_a_satisfied_anchor(st):
    """Sanity: the anchor consult does NOT break the happy path — a satisfied anchor lets the
    signed auto mode stand (no false-positive fail-closed)."""
    lad = _arm_auto_veto(st)
    post_head_anchor(st, record=False)
    assert lad.current() is Mode.auto_veto


def test_current_fails_closed_when_anchor_ahead_of_head(st):
    """If the external witness is ahead of the verified head (a tail truncation without even a
    watermark replay), an AUTO mode-read fails closed to read_only."""
    lad = _arm_auto_veto(st)
    post_head_anchor(st, record=False)
    arm_seq = _latest_mode_change_seq(st)
    captured = _capture_watermark(st)
    # Advance the chain (a benign mode_change) and anchor the new head, then roll back + replay.
    killswitch(st, actor="op", reason="panic")
    post_head_anchor(st, record=False)
    _replay_rollback(st, arm_seq, captured)
    assert st.audit.verify_chain().ok
    assert lad.current() is Mode.read_only


# =======================================================================================
# compute_gate hard-requires the external witness for an auto unlock (P0-auto prerequisite)
# =======================================================================================
def test_gate_refuses_auto_when_no_anchor_store_configured(st):
    st.anchor_store = None                            # a deployment that never wired the witness
    res = compute_gate(st, Mode.auto_veto)
    assert not res.unlockable
    assert res.metrics.get("anchor_ok") is False
    assert any("no external audit anchor" in r for r in res.reasons)


def test_gate_refuses_auto_when_anchor_store_empty(st):
    # store wired but never posted -> cannot prove freshness -> fail closed.
    assert st.anchor_store.latest() is None
    res = compute_gate(st, Mode.auto_veto)
    assert not res.unlockable
    assert res.metrics.get("anchor_ok") is False
    assert any("empty" in r for r in res.reasons)


# =======================================================================================
# W10: the SAME replay-rollback, now caught against the DURABLE out-of-domain witness
# (GitAuditLogAnchorStore over the concrete git + #audit-log adapters) — not just the
# InMemory stand-in. This is the store main.compose() wires in production.
# =======================================================================================
def _durable_store(tmp_path):
    """A production-shaped ``GitAuditLogAnchorStore`` over the two concrete adapters, with git
    commit + Discord I/O faked (the adapters' contract is monotonic append/read, tested in
    tests/bridge/test_witness_adapters.py). NEVER a FileAnchorStore (same trust domain)."""
    git = GitCommitWitness(tmp_path / "audit-head.json", commit_fn=lambda *a: None)
    posts: list = []
    disc = DiscordAuditLogWitness(
        post_fn=lambda t: posts.append(("2026-07-07T14:00:00+00:00", t)),
        fetch_fn=lambda: list(posts), channel_id=1)
    return GitAuditLogAnchorStore(git, disc)


@pytest.fixture
def st_durable(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'anchor_durable.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(
        sf, hmac_key=KEY, settings=Settings(), clock=FrozenClock(OPEN_UTC),
        anchor_store=_durable_store(tmp_path))


def test_replay_rollback_closed_by_durable_witness(st_durable):
    st = st_durable
    assert isinstance(st.anchor_store, GitAuditLogAnchorStore)     # the production witness, not File
    lad = _arm_auto_veto(st)
    post_head_anchor(st, record=False)
    assert lad.current() is Mode.auto_veto
    arm_seq = _latest_mode_change_seq(st)
    captured = _capture_watermark(st)

    killswitch(st, actor="op", reason="loss breaker")
    assert lad.current() is Mode.review_live
    post_head_anchor(st, record=False)                            # anchor past the blocking event
    anchored_seq, _ = st.anchor_store.latest()
    assert anchored_seq > arm_seq

    _replay_rollback(st, arm_seq, captured)                       # roll back + replay the watermark
    assert st.audit.verify_chain().ok                             # in-DB defeated (the deferred hole)

    # the DURABLE out-of-domain witness (git + #audit-log) closes it: gate + oracle fail CLOSED
    assert lad.current() is Mode.read_only
    gate = compute_gate(st, Mode.auto_veto)
    assert not gate.unlockable and gate.metrics.get("anchor_ok") is False


# =======================================================================================
# W10 G10-finding regressions (2026-07-08): a same-domain git mirror is NOT a runtime witness
# =======================================================================================
def _git_only_store(tmp_path):
    return GitAuditLogAnchorStore(
        GitCommitWitness(tmp_path / "audit-head.json", commit_fn=lambda *a: None))


def test_git_only_store_fails_closed_at_gate_and_oracle(tmp_path):
    """THE red-team finding: a git-ONLY store (its runtime read is the local working-tree head
    file a keyless writer can overwrite) must NOT satisfy the SEC-11 prerequisite. Even after a
    head is anchored to the git mirror, latest() is None, so an armed auto mode reads back
    read_only and compute_gate refuses (file-anchor-in-effect theater is caught)."""
    engine = make_engine(f"sqlite:///{(tmp_path / 'git_only.db').as_posix()}")
    create_all(engine)
    st = OrchestratorState.build(make_session_factory(engine), hmac_key=KEY, settings=Settings(),
                                 clock=FrozenClock(OPEN_UTC), anchor_store=_git_only_store(tmp_path))
    lad = _arm_auto_veto(st)
    post_head_anchor(st, record=False)                            # anchors to the git MIRROR only
    assert st.anchor_store.has_out_of_domain_witness is False
    assert st.anchor_store.latest() is None                       # same-domain mirror is not trusted
    assert lad.current() is Mode.read_only                        # auto mode-read fails closed
    gate = compute_gate(st, Mode.auto_veto)
    assert not gate.unlockable and gate.metrics.get("anchor_ok") is False


def test_git_head_file_rewrite_cannot_unlock_with_durable_witness(st_durable, tmp_path):
    """With a genuine out-of-domain witness (Discord) present, the keyless-writer move of
    rewriting the same-domain git head file to a rolled-back head does NOT move the trust read —
    the Discord anchor still sits above the rolled-back head, so gate + oracle fail closed."""
    st = st_durable
    lad = _arm_auto_veto(st)
    post_head_anchor(st, record=False)
    assert lad.current() is Mode.auto_veto                        # happy path (durable witness)
    arm_seq = _latest_mode_change_seq(st)
    captured = _capture_watermark(st)
    killswitch(st, actor="op", reason="loss breaker")
    post_head_anchor(st, record=False)                            # Discord anchored past the killswitch
    _replay_rollback(st, arm_seq, captured)                       # roll the DB back to the arm head
    # the keyless writer ALSO rewrites the same-domain git head file to the captured lower head
    (tmp_path / "audit-head.json").write_text(
        f'{{"head_hash":"stale","seq":{arm_seq}}}', encoding="utf-8")
    assert st.audit.verify_chain().ok                             # in-DB defeated
    assert lad.current() is Mode.read_only                        # out-of-domain witness still catches it
    assert not compute_gate(st, Mode.auto_veto).unlockable
