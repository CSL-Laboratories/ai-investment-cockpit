"""NF/W6: what ``reconcile_startup`` does when the only candidate evidence is ``filled_qty``.

The unit pins in ``test_reconcile_finite_qty.py`` call ``_order_landed`` directly. These two tests drive the
ledger end to end: a quantity-only record that is not a finite positive number leaves the intent unresolved and
raises the "never completed" alert; a finite positive quantity heals it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from orchestrator.reconcile import reconcile_startup
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"reconcile-qty-ledger-key"
NOW = datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    from core.clock import FrozenClock
    engine = make_engine(f"sqlite:///{(tmp_path / 'reconcile_qty.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(make_session_factory(engine), hmac_key=KEY, clock=FrozenClock(NOW))


def _crash_mid_submit(st, order_id):
    it = st.audit.intent(
        "order_submit",
        refs={"order_id": order_id, "proposal_id": "prop-1", "risk_decision_id": "rd-1",
              "order_tuple_hash": "hash-1"},
        payload={"symbol": "AAPL", "side": "buy", "qty": "1"},
    )
    return it.seq


@pytest.mark.parametrize("bad", ["Infinity", float("inf"), True, b"1", "NaN", "abc"])
def test_ledger_quantity_only_unreadable_alerts_and_stays_unresolved(st, bad):
    seq = _crash_mid_submit(st, "ord-q")
    msgs = []
    report = reconcile_startup(
        st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "unknown", "filled_qty": bad}],
        alerter=msgs.append,
    )
    assert report.healed == ()
    assert report.alerted == (seq,)
    assert report.ok is False
    assert len(msgs) == 1 and "never completed" in msgs[0]
    assert st.audit.unresolved_intents() == [seq]
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("good", ["5", Decimal("2"), 3])
def test_ledger_quantity_only_finite_positive_heals(st, good):
    seq = _crash_mid_submit(st, "ord-q")
    msgs = []
    report = reconcile_startup(
        st,
        broker_history_fn=lambda oid: {"order_id": oid, "status": "unknown", "filled_qty": good},
        alerter=msgs.append,
    )
    assert report.healed == (seq,)
    assert report.alerted == ()
    assert report.ok is True
    assert msgs == []
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok
