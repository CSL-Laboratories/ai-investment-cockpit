"""W12 G12 SEC-13 CLOSURE REGRESSION — the two-phase atomic-audit + completeness invariant.

The gate here is an ADVERSARIAL repro that must now FAIL CLOSED, not merely a green suite.
The two-phase submit (``storage/audit.py`` intent/result, gateway.submit L624-643) writes an
``intent`` BEFORE the broker call and a ``result`` AFTER it. A crash BETWEEN the broker ACK
and the result-commit leaves an ``intent`` with no ``result``: ``verify_chain()`` still passes
(the chain is a valid HMAC sequence) but the ledger is INCOMPLETE — the order may have landed.

``reconcile_startup`` closes it:
  (1) crash-mid-submit -> unresolved intent. If the broker CONFIRMS the order landed, the
      missing result is written (HEAL) and the chain stays valid; if the broker does NOT,
      an alert fires and the report is NOT ok (fail closed).
  (2) an orphan domain row (a bare ``session.add`` bypassing ``record_state_change``) trips
      the completeness gate: ``verify_completeness`` flags it and reconcile is NOT ok.
  (3) an orphan_result (a result with no matching intent) is surfaced.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orchestrator.reconcile import _order_landed, reconcile_startup
from orchestrator.state import OrchestratorState
from schemas.models.approval_record import ApprovalRecord
from schemas.models.market_event import MarketEvent
from schemas.models.order import Order
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"reconcile-test-key"
NOW = datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'reconcile.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, clock=_frozen())


def _frozen():
    from core.clock import FrozenClock
    return FrozenClock(NOW)


class _Alerts:
    """A capturing alerter — records every ``#system`` message reconcile emits."""

    def __init__(self):
        self.msgs: list[str] = []

    def __call__(self, msg: str) -> None:
        self.msgs.append(msg)


def _crash_mid_submit(st, order_id="ord-1") -> int:
    """Append an ``order_submit`` intent and DO NOT write its result: the exact ledger state a
    crash between broker-ACK and result-commit leaves. Returns the intent seq."""
    it = st.audit.intent(
        "order_submit",
        refs={"order_id": order_id, "proposal_id": "prop-1", "risk_decision_id": "rd-1",
              "order_tuple_hash": "hash-1"},
        payload={"symbol": "AAPL", "side": "buy", "qty": "1"},
    )
    return it.seq


# --- G12 (1): crash-mid-submit heals when the broker confirms the order landed --------------

def test_unresolved_intent_healed_when_broker_confirms(st):
    seq = _crash_mid_submit(st, "ord-1")
    assert st.audit.unresolved_intents() == [seq]

    alerts = _Alerts()
    # broker REPORTS the order landed (a non-empty history for ord-1).
    report = reconcile_startup(
        st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "filled"}] if oid == "ord-1" else [],
        alerter=alerts,
    )

    assert report.healed == (seq,)
    assert report.alerted == ()
    assert report.ok is True
    assert alerts.msgs == []                                   # no alert — it healed
    assert st.audit.unresolved_intents() == []                 # the result closed the pair
    assert st.audit.verify_chain().ok                          # chain still valid after the heal


# --- G12 (1): crash-mid-submit alerts + fails closed when the broker has NO such order -------

def test_unresolved_intent_alerts_and_not_ok_when_broker_has_no_order(st):
    seq = _crash_mid_submit(st, "ord-2")
    assert st.audit.unresolved_intents() == [seq]

    alerts = _Alerts()
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts)

    assert report.alerted == (seq,)
    assert report.healed == ()
    assert report.ok is False                                  # fail closed — cannot confirm the submit
    assert len(alerts.msgs) == 1 and "never completed" in alerts.msgs[0]
    assert st.audit.unresolved_intents() == [seq]              # still unresolved (NOT healed)


# --- G12 (2): an orphan domain row (bypassed record_state_change) trips the completeness gate

def test_orphan_domain_row_fails_completeness(st):
    # A bare session.add — bypassing AuditLog.record_state_change — leaves an Order with NO
    # covering AuditEntry (the exact hole the completeness invariant closes).
    with st.session_factory() as s, s.begin():
        s.add(Order(
            id="orphan-order", proposal_id="p", risk_decision_id="r", account="paper",
            symbol="AAPL", side="buy", qty=1, order_type="market", tif="day",
            order_tuple_hash="h", state="submitted", filled_qty=0,
            created_at=NOW, updated_at=NOW,
        ))

    assert st.audit.verify_completeness(Order) == ["orphan-order"]

    alerts = _Alerts()
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts)

    assert report.uncovered == {"order_row": ("orphan-order",)}
    assert report.ok is False
    assert any("order_row" in m and "no covering" in m.lower() for m in alerts.msgs)


def test_orphan_risk_decision_row_fails_completeness(st):
    with st.session_factory() as s, s.begin():
        s.add(RiskDecision(
            id="orphan-rd", proposal_id="p", decision="approved",
            evaluated_at=NOW, created_at=NOW,
        ))

    alerts = _Alerts()
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts)

    assert report.uncovered.get("risk_decision") == ("orphan-rd",)
    assert report.ok is False


# --- G12 (3): an orphan_result (a result with no matching intent) is surfaced ----------------

def test_orphan_result_is_surfaced(st):
    # Write a normal pair, then DELETE the intent, leaving the result orphaned.
    it = _make_intent(st)
    st.audit.result(it, "order_submit", refs={"order_id": "ord-3"})
    from sqlalchemy import delete
    from schemas.models.audit_entry import AuditEntry
    with st.session_factory() as s, s.begin():
        s.execute(delete(AuditEntry).where(AuditEntry.seq == it.seq))

    assert st.audit.orphan_results()                           # a result with no intent exists

    alerts = _Alerts()
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts)

    assert report.orphan_results == tuple(st.audit.orphan_results())
    assert report.ok is False
    assert any("orphan result" in m.lower() for m in alerts.msgs)


def _make_intent(st):
    return st.audit.intent("order_submit", refs={"order_id": "ord-3"},
                           payload={"symbol": "MSFT"})


# --- clean state: a healthy ledger reconciles ok=True (the compose smoke-test posture) ------

def test_clean_ledger_reconciles_ok(st):
    # A complete pair + nothing orphaned.
    it = st.audit.intent("order_submit", refs={"order_id": "ord-4"})
    st.audit.result(it, "order_submit", refs={"order_id": "ord-4"})

    report = reconcile_startup(st, broker_history_fn=lambda oid: [])

    assert report.ok is True
    assert report.healed == () and report.alerted == ()
    assert report.orphan_results == () and report.uncovered == {}


def test_empty_ledger_reconciles_ok(st):
    # A FRESH empty DB (the compose() smoke-test condition) has no intents -> clean.
    report = reconcile_startup(st, broker_history_fn=lambda oid: [])
    assert report.ok is True
    assert report.healed == () and report.alerted == ()


def test_default_alerter_does_not_raise(st):
    # alerter=None must fall back to the cockpit.reconcile logger (no crash on a not-ok pass).
    _crash_mid_submit(st, "ord-5")
    report = reconcile_startup(st, broker_history_fn=lambda oid: [])
    assert report.ok is False


# =====================================================================================
# G12 CLOSURE REGRESSIONS — the fresh adversarial red-team repros that must fail closed.
# =====================================================================================

# --- C1 (false-heal): a truthy-but-not-landed broker reply must NOT heal an intent ----------

def test_reconcile_does_not_false_heal_on_rejected_or_wrong_id(st):
    """C1 repro: ``_order_landed(history)=bool(history)`` healed on ANY truthy reply — a
    REJECTED/canceled record, or a record for a DIFFERENT order_id, recorded a never-executed
    order in the tamper-evident spine as landed. The fix requires POSITIVE evidence: same
    order_id AND a terminal-landed signal, else the intent stays unresolved + alert + not ok."""
    # (a) a REJECTED record for the queried id is truthy but NOT landed => alert, NOT healed.
    seq = _crash_mid_submit(st, "ord-rej")
    alerts = _Alerts()
    rejected = reconcile_startup(
        st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "rejected"}],
        alerter=alerts,
    )
    assert rejected.healed == ()
    assert rejected.alerted == (seq,)
    assert rejected.ok is False
    assert len(alerts.msgs) == 1 and "never completed" in alerts.msgs[0]
    assert st.audit.unresolved_intents() == [seq]              # NOT healed

    # (b) a truthy record for a DIFFERENT order_id must not heal ord-wid either.
    seq2 = _crash_mid_submit(st, "ord-wid")
    alerts2 = _Alerts()
    wrong_id = reconcile_startup(
        st,
        # broker returns a LANDED record — but for someone else's order, not the queried id.
        broker_history_fn=lambda oid: [{"order_id": "SOMEONE-ELSE", "status": "filled"}],
        alerter=alerts2,
    )
    assert seq2 in wrong_id.alerted and seq2 not in wrong_id.healed
    assert wrong_id.ok is False
    assert st.audit.unresolved_intents() == sorted([seq, seq2])   # both still unresolved

    # (c) POSITIVE evidence (matching id + a filled status) DOES heal and clears the pair.
    alerts3 = _Alerts()
    healed = reconcile_startup(
        st,
        broker_history_fn=(
            lambda oid: [{"order_id": oid, "status": "filled"}]
            if oid in ("ord-rej", "ord-wid") else []
        ),
        alerter=alerts3,
    )
    assert set(healed.healed) == {seq, seq2}
    assert healed.alerted == ()
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


def test_reconcile_does_not_false_heal_on_rejected_with_broker_order_id(st):
    """C1' (round-2) repro: real brokers assign a ``broker_order_id`` at SUBMIT — including to
    REJECTED/canceled orders (gateway.py records the identical {broker_order_id,status} shape).
    The old bare ``if rec.get('broker_order_id'): return True`` clause therefore healed a
    settled NON-landing: once W17 wires the live broker_history_fn, a crash-gap that resolved to
    a rejected order would be recorded in the tamper-evident spine as landed=broker_confirmed.
    Fix: a terminal-negative status is skipped BEFORE any landed-signal check, and a bare
    broker_order_id is no longer fill evidence. Only positive-terminal-landed evidence heals."""
    # (a) a REJECTED record that ALSO carries a broker_order_id must NOT heal => alert, not ok.
    seq = _crash_mid_submit(st, "ord-rej-brk")
    alerts = _Alerts()
    rejected = reconcile_startup(
        st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "rejected", "broker_order_id": "brk-1"}],
        alerter=alerts,
    )
    assert rejected.healed == ()
    assert rejected.alerted == (seq,)
    assert rejected.ok is False
    assert len(alerts.msgs) == 1 and "never completed" in alerts.msgs[0]
    assert st.audit.unresolved_intents() == [seq]              # NOT healed

    # (b) a CANCELED record with a broker_order_id is likewise a settled non-landing.
    seq2 = _crash_mid_submit(st, "ord-can-brk")
    alerts2 = _Alerts()
    canceled = reconcile_startup(
        st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "canceled", "broker_order_id": "brk-2"}],
        alerter=alerts2,
    )
    assert seq2 in canceled.alerted and seq2 not in canceled.healed
    assert canceled.ok is False
    assert st.audit.unresolved_intents() == sorted([seq, seq2])   # both still unresolved

    # (c) POSITIVE control: a FILLED record (with the same broker_order_id shape) DOES heal.
    alerts3 = _Alerts()
    healed = reconcile_startup(
        st,
        broker_history_fn=(
            lambda oid: [{"order_id": oid, "status": "filled", "broker_order_id": "brk-1"}]
            if oid in ("ord-rej-brk", "ord-can-brk") else []
        ),
        alerter=alerts3,
    )
    assert set(healed.healed) == {seq, seq2}
    assert healed.alerted == ()
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


def test_reconcile_does_not_false_heal_on_error_object(st):
    """C1: a broker reply that is an ERROR/exception object (not a list of landed records)
    must read as NOT landed — a broker read that surfaced an error is not proof of landing."""
    seq = _crash_mid_submit(st, "ord-err")
    alerts = _Alerts()
    report = reconcile_startup(
        st,
        broker_history_fn=lambda oid: RuntimeError("broker 500"),
        alerter=alerts,
    )
    assert report.healed == () and report.alerted == (seq,)
    assert report.ok is False
    assert st.audit.unresolved_intents() == [seq]


# --- C2 (cross-table collision): completeness coverage must be scoped by model --------------

def test_completeness_gate_model_scoped_no_cross_table_mask(st):
    """C2 repro: an UNSCOPED coverage set let an unaudited Order pass because a covered
    ApprovalRecord shared the same id. Scoping coverage to ``refs.model`` unmasks the gap:
    ``verify_completeness(Order)`` on a colliding id must still flag the orphan Order."""
    collide = "collision-id"
    # A properly-audited ApprovalRecord under the SHARED id (covered, refs.model=approval_record).
    appr = ApprovalRecord(
        id=collide, proposal_id="p", risk_decision_id="r", actor_user_id="lucas",
        user_decision="approve", surface="discord", order_tuple_hash="h",
        single_use_consumed=False, decided_at=NOW, created_at=NOW,
    )
    st.audit.record_state_change(appr, "adjudication", actor="lucas",
                                 payload={"decision": "approve"})
    # A BARE Order under the SAME id — bypassing record_state_change (no covering audit entry).
    with st.session_factory() as s, s.begin():
        s.add(Order(
            id=collide, proposal_id="p", risk_decision_id="r", account="paper",
            symbol="AAPL", side="buy", qty=1, order_type="market", tif="day",
            order_tuple_hash="h", state="submitted", filled_qty=0,
            created_at=NOW, updated_at=NOW,
        ))

    # The ApprovalRecord is covered; the colliding Order must STILL be flagged (not masked).
    assert st.audit.verify_completeness(ApprovalRecord) == []
    assert st.audit.verify_completeness(Order) == [collide]

    alerts = _Alerts()
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts)
    assert report.uncovered.get("order_row") == (collide,)
    assert report.ok is False


# --- C3 (coverage-set gap): the default gate must cover ALL load-bearing models -------------

def test_completeness_gate_covers_all_load_bearing_models(st):
    """C3 repro: ``_DEFAULT_MODELS`` gated only 3 of the models persisted via
    record_state_change, so a bare ``session.add`` on an uncovered table read ok=True. The fix
    extends the default to every audited model — a bare ProposalRecord now trips ok=False."""
    with st.session_factory() as s, s.begin():
        s.add(ProposalRecord(
            id="orphan-prop", symbol="AAPL", side="buy", status="proposed", payload={},
            created_at=NOW, updated_at=NOW,
        ))

    alerts = _Alerts()
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts)
    assert report.uncovered.get("proposal_record") == ("orphan-prop",)
    assert report.ok is False
    assert any("proposal_record" in m and "no covering" in m.lower() for m in alerts.msgs)


# =====================================================================================
# G12 ROUND-3 CLOSURE REGRESSIONS — deeper in-scope defects, fixed with class-closing invariants.
# =====================================================================================

# --- R3-C1'' (terminal-negative is a GLOBAL VETO, not a per-record continue) -----------------

def test_reconcile_terminal_negative_vetoes_earlier_landed(st):
    """R3-C1'' repro: ``_order_landed`` returned True on the FIRST landed record even when a
    LATER record for the SAME order_id settled it canceled/rejected — so the per-record skip let
    ``[{id,accepted},{id,canceled}]`` heal a settled non-landing as broker_confirmed. Class-closing
    fix: a terminal-negative ANYWHERE in the matched history is a GLOBAL VETO. Only with NO
    terminal-negative does positive landed evidence heal (positive control at the end)."""
    # unit: a landed record BEFORE a terminal-negative for the same id must NOT read as landed.
    assert _order_landed([{"order_id": "o1", "status": "accepted"},
                          {"order_id": "o1", "status": "canceled"}], "o1") is False
    assert _order_landed([{"order_id": "o1", "status": "submitted"},
                          {"order_id": "o1", "status": "rejected"}], "o1") is False
    # ordering-independent: the veto also holds when the negative comes FIRST.
    assert _order_landed([{"order_id": "o1", "status": "canceled"},
                          {"order_id": "o1", "status": "filled"}], "o1") is False

    # end-to-end: a crash-gap whose history is [accepted, canceled] alerts + fails closed.
    seq = _crash_mid_submit(st, "ord-veto")
    alerts = _Alerts()
    report = reconcile_startup(
        st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "accepted"},
                                       {"order_id": oid, "status": "canceled"}],
        alerter=alerts,
    )
    assert report.healed == ()
    assert report.alerted == (seq,)
    assert report.ok is False
    assert st.audit.unresolved_intents() == [seq]              # NOT healed

    # POSITIVE CONTROL: a lone landed record (no terminal-negative anywhere) still heals.
    assert _order_landed([{"order_id": "o1", "status": "accepted"}], "o1") is True
    seq2 = _crash_mid_submit(st, "ord-clean")
    alerts2 = _Alerts()
    healed = reconcile_startup(
        st,
        broker_history_fn=(lambda oid: [{"order_id": oid, "status": "accepted"}]
                           if oid == "ord-clean" else []),
        alerter=alerts2,
    )
    assert seq2 in healed.healed and seq2 not in healed.alerted
    assert st.audit.verify_chain().ok


# --- R3-C2 (MarketEvent completeness coverage: append_in row_ids union, no cry wolf) ----------

def test_completeness_gate_covers_market_event(st):
    """R3-C2 repro: MarketEvent is a load-bearing audited model (persisted via ``append_in`` with
    a PLURAL ``refs.row_ids`` batch, workflows/events.py) but was ABSENT from _DEFAULT_MODELS, so a
    forged bare-add MarketEvent slipped ok=True. Fix adds it to the default gate; a bare-added row
    with no covering entry is now flagged and fails reconcile closed."""
    with st.session_factory() as s, s.begin():
        s.add(MarketEvent(
            id="forged-evt", symbol="AAPL", source="alpaca_news", event_type="filing",
            headline="h", summary=None, raw_ref="parquet://x", content_hash="c",
            ts=NOW, ingested_at=NOW, created_at=NOW,
        ))

    assert st.audit.verify_completeness(MarketEvent) == ["forged-evt"]

    alerts = _Alerts()
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts)
    assert report.uncovered.get("market_event") == ("forged-evt",)
    assert report.ok is False
    assert any("market_event" in m and "no covering" in m.lower() for m in alerts.msgs)


def test_legit_append_in_market_event_is_covered(st):
    """R3-C2 POSITIVE CONTROL: a normally ingested MarketEvent batch is audited via ``append_in``
    with a PLURAL ``refs.row_ids`` (mirroring workflows/events.py EVENT_INGEST_BATCH). The
    row_id + row_ids UNION in verify_completeness must recognize it as covered — NOT cry wolf —
    so reconcile stays ok=True even with the model now in the default gate."""
    rows = [
        MarketEvent(id="evt-a", symbol="AAPL", source="alpaca_news", event_type="news",
                    headline="a", summary=None, raw_ref="parquet://a", content_hash="ca",
                    ts=NOW, ingested_at=NOW, created_at=NOW),
        MarketEvent(id="evt-b", symbol="MSFT", source="alpaca_news", event_type="news",
                    headline="b", summary=None, raw_ref="parquet://b", content_hash="cb",
                    ts=NOW, ingested_at=NOW, created_at=NOW),
    ]
    # Mirror the ingest path: rows + covering append_in batch in ONE transaction, refs.row_ids plural.
    with st.session_factory() as s, s.begin():
        for row in rows:
            s.add(row)
        s.flush()
        st.audit.append_in(
            s, "ingest_batch", actor="ingest",
            refs={"model": "market_event", "row_ids": [r.id for r in rows]},
            payload={"count": len(rows)},
        )

    # The union (row_id singular OR row_ids plural) recognizes both as covered.
    assert st.audit.verify_completeness(MarketEvent) == []

    report = reconcile_startup(st, broker_history_fn=lambda oid: [])
    assert report.uncovered == {}
    assert report.ok is True
    assert st.audit.verify_chain().ok
