"""RW-15 paper broker polling: terminal convergence and lost-response healing."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from brokers.alpaca_paper import AlpacaPaperBroker, AlpacaPaperError, FakeAlpaca
from brokers.paper_reconciliation import PaperOrderReconciler
from orchestrator.reconcile import reconcile_startup
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from tests.brokers.conftest import build_scenario


def _order(st, order_id: str) -> Order:
    with st.session_factory() as session:
        row = session.get(Order, order_id)
        session.expunge(row)
    return row


def _approval(st, approval_id: str) -> ApprovalRecord:
    with st.session_factory() as session:
        row = session.get(ApprovalRecord, approval_id)
        session.expunge(row)
    return row


def test_resting_partial_progress_and_terminal_fill_converge(st):
    client = FakeAlpaca()
    broker = AlpacaPaperBroker(client=client)
    scenario = build_scenario(st, broker=broker)
    submitted = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert submitted.ok and _order(st, scenario.order.id).state == "submitted"

    broker_id = submitted.broker_order_id
    notifications = []
    reconciler = PaperOrderReconciler(
        st,
        broker=broker,
        deliver=lambda channel, payload: notifications.append((channel, payload)) or True,
    )
    assert reconciler.poll().unchanged == (scenario.order.id,)

    client.set_order_status(
        broker_id, status="partially_filled", filled_qty="4", avg_fill_price="149.25"
    )
    first = reconciler.poll()
    row = _order(st, scenario.order.id)
    assert first.transitioned == (scenario.order.id,)
    assert first.notified == (scenario.order.id,)
    assert row.state == "partially_filled" and row.filled_qty == Decimal("4")

    client.set_order_status(
        broker_id, status="partially_filled", filled_qty="6", avg_fill_price="149.50"
    )
    progress = reconciler.poll()
    row = _order(st, scenario.order.id)
    assert progress.progressed == (scenario.order.id,)
    assert progress.notified == (scenario.order.id,)
    assert row.state == "partially_filled" and row.filled_qty == Decimal("6")

    client.set_order_status(
        broker_id, status="filled", filled_qty="10", avg_fill_price="150.00"
    )
    final = reconciler.poll()
    row = _order(st, scenario.order.id)
    assert final.transitioned == (scenario.order.id,)
    assert final.notified == (scenario.order.id,)
    assert row.state == "filled" and row.filled_qty == Decimal("10")
    assert row.avg_fill_price == Decimal("150.00")
    assert [payload["broker_status"] for _channel, payload in notifications] == [
        "partially_filled", "partially_filled", "filled",
    ]
    assert all(channel == "#proposals" for channel, _payload in notifications)
    with st.session_factory() as session:
        notification_rows = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "paper_fill_notification")
        ).scalars().all()
    assert len(notification_rows) == 3
    assert all((row.payload or {}).get("delivered") is True for row in notification_rows)
    assert st.audit.verify_chain().ok


class _LandedThenLostResponse(FakeAlpaca):
    def submit_order(self, canonical: dict) -> dict:
        super().submit_order(canonical)
        raise AlpacaPaperError("simulated response loss")


def test_lost_submit_response_is_found_by_client_id_and_settled(st):
    client = _LandedThenLostResponse()
    broker = AlpacaPaperBroker(client=client)
    scenario = build_scenario(st, broker=broker)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert not outcome.ok and outcome.stage == "broker_error"
    assert _order(st, scenario.order.id).state == "proposed"
    assert _approval(st, scenario.approval.id).single_use_consumed is False

    reconciler = PaperOrderReconciler(st, broker=broker)
    repaired = reconciler.poll()
    row = _order(st, scenario.order.id)
    assert repaired.transitioned == (scenario.order.id,)
    assert row.state == "submitted"
    assert row.broker_order_id == client.submitted[0]["broker_order_id"]
    assert _approval(st, scenario.approval.id).single_use_consumed is True

    client.set_order_status(
        row.broker_order_id, status="filled", filled_qty="10", avg_fill_price="151"
    )
    assert reconciler.poll().transitioned == (scenario.order.id,)
    report = reconcile_startup(st, broker_history_fn=reconciler.order_history)
    assert report.ok and report.ambiguous_aborts == ()
    assert st.audit.verify_chain().ok


def test_inconsistent_filled_ack_stays_open_and_is_audited(st):
    client = FakeAlpaca()
    broker = AlpacaPaperBroker(client=client)
    scenario = build_scenario(st, broker=broker)
    submitted = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    client.set_order_status(
        submitted.broker_order_id, status="filled", filled_qty="3", avg_fill_price="150"
    )

    report = PaperOrderReconciler(st, broker=broker).poll()
    assert report.unchanged == (scenario.order.id,)
    assert _order(st, scenario.order.id).state == "submitted"
    with st.session_factory() as session:
        events = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "order_status_ignored")
        ).scalars().all()
    assert events and st.audit.verify_chain().ok


def test_fill_delivery_failure_is_audited_after_terminal_state_commits(st):
    client = FakeAlpaca()
    broker = AlpacaPaperBroker(client=client)
    scenario = build_scenario(st, broker=broker)
    submitted = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    client.set_order_status(
        submitted.broker_order_id, status="filled", filled_qty="10", avg_fill_price="150"
    )

    def unavailable(_channel, _payload):
        raise RuntimeError("Discord unavailable")

    report = PaperOrderReconciler(st, broker=broker, deliver=unavailable).poll()
    assert report.transitioned == (scenario.order.id,)
    assert report.undelivered == (scenario.order.id,)
    assert _order(st, scenario.order.id).state == "filled"
    with st.session_factory() as session:
        event = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "paper_fill_notification")
        ).scalar_one()
    assert (event.payload or {}).get("delivered") is False
    assert st.audit.verify_chain().ok


def test_mutable_broker_id_tamper_never_terminalizes_order(st):
    client = FakeAlpaca()
    broker = AlpacaPaperBroker(client=client)
    scenario = build_scenario(st, broker=broker)
    scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    with st.session_factory() as session, session.begin():
        row = session.get(Order, scenario.order.id)
        row.broker_order_id = "attacker-order"

    report = PaperOrderReconciler(st, broker=broker).poll()
    assert report.errors == (scenario.order.id,)
    assert _order(st, scenario.order.id).state == "submitted"
    assert st.audit.verify_chain().ok


def test_mutable_approval_consumption_tamper_never_consumes_broker_ack(st):
    client = FakeAlpaca()
    broker = AlpacaPaperBroker(client=client)
    scenario = build_scenario(st, broker=broker)
    submitted = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    client.set_order_status(
        submitted.broker_order_id, status="filled", filled_qty="10", avg_fill_price="150"
    )
    with st.session_factory() as session, session.begin():
        approval = session.get(ApprovalRecord, scenario.approval.id)
        approval.single_use_consumed = False

    report = PaperOrderReconciler(st, broker=broker).poll()
    assert report.errors == (scenario.order.id,)
    assert _order(st, scenario.order.id).state == "submitted"


def test_unverified_audit_chain_blocks_reads_and_lifecycle_mutation(st):
    client = FakeAlpaca()
    broker = AlpacaPaperBroker(client=client)
    scenario = build_scenario(st, broker=broker)
    submitted = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    client.set_order_status(
        submitted.broker_order_id, status="filled", filled_qty="10", avg_fill_price="150"
    )
    with st.session_factory() as session, session.begin():
        entry = session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().first()
        entry.payload = {**(entry.payload or {}), "tampered": True}

    report = PaperOrderReconciler(st, broker=broker).poll()
    assert report.chain_verified is False and report.errors == ("audit_chain",)
    assert _order(st, scenario.order.id).state == "submitted"
