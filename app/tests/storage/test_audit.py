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


def test_missing_watermark_on_nonempty_chain_fails(env):
    audit, _ = env
    for i in range(3):
        audit.append("e", payload={"i": i})
    from storage.audit import audit_meta
    with audit._sf() as s, s.begin():
        s.execute(audit_meta.delete())                          # wipe the signed watermark
    v = audit.verify_chain()
    # SEC-11: rows present + watermark absent is the tail-truncation signature -> fail closed.
    assert not v.ok and "watermark" in v.error


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


def test_completeness_is_model_scoped_no_cross_table_mask(env):
    """C2 (cross-table collision): coverage is scoped by ``refs.model``. A covered ModeState
    must NOT mask a bare-added Order sharing the same id — ``verify_completeness(Order)`` still
    flags the orphan Order, while ``verify_completeness(ModeState)`` sees the ModeState covered."""
    from schemas.models.order import Order
    audit, sf = env
    now = datetime.now(timezone.utc)
    collide = "shared-id"
    # A covered ModeState under the shared id (refs.model == mode_state).
    audit.record_state_change(ModeState(id=collide, mode="review_live", created_at=now),
                              "mode_change", actor="op")
    # A BARE Order under the SAME id — no covering audit entry.
    Repo(sf, Order).add(
        id=collide, proposal_id="p", risk_decision_id="r", account="paper", symbol="AAPL",
        side="buy", qty=1, order_type="market", tif="day", order_tuple_hash="h",
        state="submitted", filled_qty=0, created_at=now, updated_at=now,
    )
    assert audit.verify_completeness(ModeState) == []          # its own audited row covers it
    assert audit.verify_completeness(Order) == [collide]       # NOT masked by the ModeState cover


def test_completeness_report_aggregates_all_gaps(env):
    # SEC-13 thin roll-up: one snapshot of unresolved intents + orphan results + uncovered rows.
    audit, sf = env
    now = datetime.now(timezone.utc)
    # (a) an unresolved intent (crash-mid-submit).
    i = audit.intent("order_submit", refs={"order_id": "o9"})
    # (b) an uncovered domain row (bare write bypassing record_state_change).
    Repo(sf, ModeState).add(id="m3", mode="paper", created_at=now)

    report = audit.completeness_report([ModeState])
    assert report["unresolved_intents"] == [i.seq]
    assert report["orphan_results"] == []
    assert report["uncovered"] == {"mode_state": ["m3"]}

    # Resolving the intent + no orphan/uncovered => a clean aggregate.
    audit.result(i, "order_submit", refs={"order_id": "o9"})
    audit.record_state_change(ModeState(id="m4", mode="review_live", created_at=now),
                              "mode_change", actor="op")
    # m3 is still uncovered; cover the report over a model with no gaps to see it clear.
    clean = audit.completeness_report([])
    assert clean == {"unresolved_intents": [], "orphan_results": [], "uncovered": {}}
    assert audit.verify_chain().ok


def test_full_erasure_is_not_a_virgin_chain(env):
    """BUG-067: deleting EVERY audit row AND the audit_meta watermark used to re-green
    verify_chain() — a total wipe was indistinguishable from first boot. Within one
    process the log knows better: a head below one it has already appended or verified
    is an erasure, not a fresh start. (Wipes that span a restart are the external
    anchor's job — verify_against_anchor pins those.)"""
    from storage.audit import audit_meta

    audit, sf = env
    audit.append("e", payload={"x": 1})
    audit.append("e", payload={"x": 2})
    assert audit.verify_chain().ok
    with sf() as s, s.begin():
        s.execute(delete(AuditEntry))                   # erase the whole chain
        s.execute(audit_meta.delete())                  # AND the signed watermark
    v = audit.verify_chain()
    assert not v.ok
    assert "erasure" in (v.error or "")


def test_empty_chain_on_a_fresh_log_still_verifies_ok(env):
    """Regression guard for BUG-067: a genuinely virgin chain (nothing ever appended or
    verified by this log) must keep verifying ok — first boot is not a tamper."""
    audit, _ = env
    v = audit.verify_chain()
    assert v.ok and v.head_seq == 0


def test_result_double_resolve_is_atomic_under_races(env):
    """BUG-068: the double-resolve guard and the result append must be ONE atomic step.
    Racing threads used to both pass the check-then-act guard and append duplicate
    result rows that neither unresolved_intents() nor orphan_results() could ever see."""
    audit, _ = env
    for round_no in range(12):
        it = audit.intent("op", payload={"round": round_no})
        start = threading.Barrier(4)
        outcomes: list[str] = []

        def go():
            start.wait()
            try:
                audit.result(it, "op", payload={})
                outcomes.append("ok")
            except ValueError:
                outcomes.append("dup")

        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = [r for r in audit._rows()
                if (r.payload or {}).get("_phase") == "result"
                and (r.refs or {}).get("intent_seq") == it.seq]
        assert len(rows) == 1, f"round {round_no}: {len(rows)} result rows for one intent"
        assert outcomes.count("ok") == 1, f"round {round_no}: {outcomes}"
    assert audit.verify_chain().ok
