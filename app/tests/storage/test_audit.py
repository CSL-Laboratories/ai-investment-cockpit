"""WP2 G2 — audit chain: verify, tamper detection (incl. id + created_at coverage),
HMAC-key requirement, watermark truncation + anchor rollback, single-writer concurrency,
two-phase invariants, and state<->ledger completeness."""
from __future__ import annotations

import hashlib
import hmac
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState
from storage.audit import AuditLog, _canonical
from storage.engine import create_all, make_engine, make_session_factory
from storage.repositories import Repo

KEY = b"unit-test-hmac-key"


@pytest.fixture
def env(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'audit.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return AuditLog(sf, hmac_key=KEY), sf


def test_append_and_verify(env):
    audit, _ = env
    for i in range(5):
        audit.append("test_event", actor="op", payload={"i": i})
    v = audit.verify_chain()
    assert v.ok and v.head_seq == 5
    assert audit.head()[0] == 5


def test_tamper_is_detected(env):
    audit, _ = env
    audit.append("e", payload={"x": 1})
    audit.append("e", payload={"x": 2})
    with audit._sf() as s, s.begin():
        r = s.execute(select(AuditEntry).where(AuditEntry.seq == 1)).scalar_one()
        r.payload = {"x": 999, "_ts": r.payload["_ts"]}
    v = audit.verify_chain()
    assert not v.ok and "tamper" in v.error


def test_full_recompute_needs_the_key(env):
    audit, _ = env
    audit.append("e", payload={"x": 1})
    with audit._sf() as s, s.begin():                       # edit AND recompute with a WRONG key
        r = s.execute(select(AuditEntry).where(AuditEntry.seq == 1)).scalar_one()
        r.payload = {"x": 999, "_ts": r.payload["_ts"]}
        r.entry_hash = hmac.new(
            b"attacker-key",
            _canonical(r.id, r.seq, r.prev_hash, r.event_type, r.actor, r.refs, r.payload),
            hashlib.sha256,
        ).hexdigest()
    assert not audit.verify_chain().ok                      # real key still rejects it (SEC-10)


def test_created_at_edit_is_detected(env):
    audit, _ = env
    audit.append("e", payload={"x": 1})
    with audit._sf() as s, s.begin():                       # move time-of-event without touching _ts
        r = s.execute(select(AuditEntry).where(AuditEntry.seq == 1)).scalar_one()
        r.created_at = datetime(2020, 1, 1, 0, 0, 0)
    v = audit.verify_chain()
    assert not v.ok and "created_at" in v.error


def test_truncation_detected_by_watermark(env):
    audit, _ = env
    for i in range(5):
        audit.append("e", payload={"i": i})
    with audit._sf() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq > 3))   # drop the tail
    v = audit.verify_chain()
    assert not v.ok and "truncation" in v.error             # signed watermark catches it (SEC-11)


def test_anchor_rollback_detected(env):
    audit, _ = env
    for i in range(5):
        audit.append("e", payload={"i": i})
    anchored_seq, anchored_hash = audit.head()
    assert audit.verify_against_anchor(anchored_seq, anchored_hash).ok
    # a fabricated older anchor with a bad hash is rejected
    assert not audit.verify_against_anchor(anchored_seq, "deadbeef").ok


def test_single_writer_concurrency(env):
    audit, _ = env

    def worker():
        for _ in range(5):
            audit.append("concurrent", payload={"t": True})

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    v = audit.verify_chain()
    assert v.ok and v.head_seq == 20                        # 4x5 appends, contiguous (OPS-01)


def test_two_phase_reconciliation(env):
    audit, _ = env
    i = audit.intent("place_order", payload={"symbol": "AAPL"})
    assert audit.unresolved_intents() == [i.seq]
    audit.result(i, "place_order", payload={"order_id": "o1"})
    assert audit.unresolved_intents() == []
    assert audit.verify_chain().ok


def test_result_invariants(env):
    audit, _ = env
    i = audit.intent("place_order")
    audit.result(i, "place_order")
    with pytest.raises(ValueError, match="already resolved"):
        audit.result(i, "place_order")                      # double-resolve rejected
    ghost = AuditEntry(seq=999)
    with pytest.raises(ValueError, match="no intent"):
        audit.result(ghost, "place_order")                  # result-without-intent rejected


def test_completeness_catches_unaudited_state(env):
    audit, sf = env
    now = datetime.now(timezone.utc)
    audit.record_state_change(ModeState(id="m1", mode="review_live", created_at=now),
                              "mode_change", actor="op")
    assert audit.verify_completeness(ModeState) == []       # atomic write is covered
    Repo(sf, ModeState).add(id="m2", mode="paper", created_at=now)   # bare write, no audit
    assert audit.verify_completeness(ModeState) == ["m2"]   # uncovered state row is flagged
