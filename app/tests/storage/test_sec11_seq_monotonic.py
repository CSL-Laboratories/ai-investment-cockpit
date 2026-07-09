"""W10 SEC-11 (b) REGRESSION — monotonic, NON-REUSABLE seq closes the WP8 delete-then-heal hole.

Root cause the WP8 adversarial review found (security-hardening-backlog SEC-11, L394-413): the
signed head watermark was REASSIGNED to the current head on every append, and ``_insert``
allocated ``seq = head.seq + 1`` from the max SURVIVING row — so a keyless writer who
``DELETE``d the signed tail and let the natural next appends REFILL those freed seqs produced a
chain ``verify_chain()`` re-greened (the truncation erased). W10 makes the high-water MONOTONIC
(never decreases across a delete) and allocates ``seq = max(high_water, max_existing) + 1``, so a
freed seq is NON-REUSABLE: the refill lands as a contiguity GAP ``verify_chain`` rejects, and
appending onto a chain whose signed high-water was deleted is REFUSED (healing at a refilled head
IS the bypass). This closure needs NO external witness (the REPLAY class — capturing a past
signed watermark — still does; see tests/orchestrator/test_sec11_anchor.py).
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select, update

from schemas.models.audit_entry import AuditEntry
from storage.audit import AuditIntegrityError, AuditLog, audit_meta
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"sec11-seq-monotonic-key"


@pytest.fixture
def env(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'seq.db').as_posix()}")
    create_all(engine)
    return AuditLog(make_session_factory(engine), hmac_key=KEY)


def _seqs(audit):
    with audit._sf() as s:
        return [r.seq for r in s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()]


# ---- normal operation is unchanged (contiguous, starts at 1) --------------------------
def test_normal_append_is_contiguous_and_verifies(env):
    for i in range(10):
        env.append("e", payload={"i": i})
    v = env.verify_chain()
    assert v.ok and v.head_seq == 10
    assert _seqs(env) == list(range(1, 11))    # monotonic, contiguous, starts at 1


# ---- THE hole, now closed: delete the signed tail + let appends refill -> no re-green --
def test_delete_then_refill_leaves_a_gap_not_a_regreen(env):
    for i in range(5):
        env.append("e", payload={"i": i})          # seqs 1..5, high-water 5
    with env._sf() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > 3))   # keyless tail-delete (leave audit_meta)
    # the natural next appends must NOT reissue the freed seqs 4,5 -> they jump to 6,7
    r6 = env.append("e", payload={"refill": 1})
    r7 = env.append("e", payload={"refill": 2})
    assert (r6.seq, r7.seq) == (6, 7)              # freed seqs 4,5 are NON-REUSABLE
    v = env.verify_chain()
    assert not v.ok and ("gap" in v.error or "rollback" in v.error)   # the refill is a seq gap


def test_truncation_below_high_water_fails_even_without_refill(env):
    for i in range(5):
        env.append("e", payload={"i": i})
    with env._sf() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > 3))    # leave the monotonic high-water at 5
    v = env.verify_chain()
    assert not v.ok and ("high-water" in v.error or "truncation" in v.error)


# ---- the attacker who ALSO deletes the signed high-water cannot heal -------------------
def test_delete_high_water_then_append_is_refused(env):
    for i in range(5):
        env.append("e", payload={"i": i})
    with env._sf() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > 3))
        s.execute(audit_meta.delete())             # wipe the signed high-water too
    # re-creating a valid high-water at a refilled head IS the bypass -> append fails closed
    with pytest.raises(AuditIntegrityError):
        env.append("e", payload={"heal": 1})
    # and verify_chain independently catches it (rows present, watermark absent)
    v = env.verify_chain()
    assert not v.ok and "watermark" in v.error


def test_high_water_signature_tamper_refuses_append(env):
    env.append("e", payload={"i": 1})
    with env._sf() as s, s.begin():
        s.execute(update(audit_meta).where(audit_meta.c.k == "head_seq_sig").values(v="deadbeef"))
    with pytest.raises(AuditIntegrityError):       # hwm == -1 (tampered sig) -> fail closed
        env.append("e", payload={"i": 2})


# ---- the high-water is HMAC-covered: a keyless writer can't raise it to hide a refill --
def test_high_water_forge_needs_the_key(env):
    for i in range(5):
        env.append("e", payload={"i": i})
    with env._sf() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > 3))
        # a keyless writer tries to lower the high-water to 3 to dodge head_seq<hwm -> can't sign it
        s.execute(update(audit_meta).where(audit_meta.c.k == "head_seq").values(v="3"))
    v = env.verify_chain()
    assert not v.ok and ("signature invalid" in v.error or "watermark" in v.error)
