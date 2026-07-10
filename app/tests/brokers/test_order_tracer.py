"""W11 (a) — the always-on runtime order-authority tracer (SEC-03 / P-B).

The static Inv-1 call-site grep (tests/test_invariants.py) stays GREEN through every dispatch
trick below (none carries a literal ``broker.submit_order(`` outside brokers/). These regressions
pin the RUNTIME complement: a broker order fn invoked outside ``OrderGateway.submit`` fails closed
(broker never entered, a bypass AuditEntry recorded) regardless of how it is dispatched, while a
legit gated submit still reaches the broker and writes NO bypass entry.
"""
from __future__ import annotations

import functools
from decimal import Decimal

import pytest
from sqlalchemy import select

from brokers.order_tracer import (
    EVENT_ORDER_AUTHORITY_BYPASS,
    OrderAuthorityViolation,
    install,
    is_installed,
    traced_broker,
    uninstall,
)
from schemas.models.audit_entry import AuditEntry

from tests.brokers.conftest import build_scenario


class RecordingBroker:
    def __init__(self):
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return {"broker_order_id": "rec-1", "status": "accepted",
                "filled_qty": Decimal("0"), "avg_fill_price": None}


CANON = {"symbol": "GME", "side": "buy", "qty": "99999", "order_type": "market",
         "tif": "day", "order_tuple_hash": "hEVIL"}


def _bypass_entries(st):
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry)).scalars().all()
    return [r for r in rows if r.event_type == EVENT_ORDER_AUTHORITY_BYPASS]


def test_tracer_catches_every_out_of_gateway_dispatch_trick(st):
    """Direct / getattr / stored-partial / string-keyed-shim broker dispatch outside the gateway
    is ALL caught: broker never entered, a bypass AuditEntry per attempt (Inv 6)."""
    broker = RecordingBroker()
    install(broker, state=st)

    # (1) direct call
    with pytest.raises(OrderAuthorityViolation):
        broker.submit_order(CANON)
    # (2) getattr dispatch (dodges a symbol grep)
    with pytest.raises(OrderAuthorityViolation):
        getattr(broker, "submit_order")(CANON)
    # (3) stored functools.partial
    with pytest.raises(OrderAuthorityViolation):
        functools.partial(broker.submit_order, CANON)()
    # (4) string-keyed shim
    with pytest.raises(OrderAuthorityViolation):
        ({"x": broker.submit_order}["x"])(CANON)

    assert broker.calls == 0                       # the broker was NEVER entered
    assert len(_bypass_entries(st)) == 4           # each attempt is audited (Inv 6)
    assert st.audit.verify_chain().ok


def test_tracer_catches_class_level_and_descriptor_dispatch(st):
    """G11 round-1 repro: dispatching through the CLASS (not the instance) skipped a per-instance
    setattr shadow. Class-level wrapping closes it — type(broker).submit_order(broker, …) and a
    descriptor-bound Cls.submit_order.__get__(broker, Cls)(…) are BOTH caught."""
    broker = RecordingBroker()
    install(broker, state=st)
    with pytest.raises(OrderAuthorityViolation):
        type(broker).submit_order(broker, CANON)           # class dispatch (skips an instance shadow)
    with pytest.raises(OrderAuthorityViolation):
        RecordingBroker.submit_order.__get__(broker, RecordingBroker)(CANON)   # descriptor bind
    assert broker.calls == 0
    assert len(_bypass_entries(st)) == 2


def test_tracer_exposes_no_public_unwrap_handle(st):
    """G11 round-3: the class guard must NOT expose the pre-guard original as a readable attribute —
    a discoverable `_order_tracer_original` handle let in-process code call the broker AROUND the
    guard (vars(type(b).submit_order)['_order_tracer_original'](b, CANON)). The original now lives
    ONLY in the closure; only a boolean sentinel is public."""
    broker = RecordingBroker()
    install(broker, state=st)
    guard = type(broker).__dict__["submit_order"]
    assert getattr(guard, "_order_tracer_original", None) is None      # the named handle is gone
    assert not any(callable(v) for v in vars(guard).values())          # no readable unwrap handle
    with pytest.raises(KeyError):
        vars(guard)["_order_tracer_original"](broker, CANON)           # the exact round-3 repro is dead


def test_untraced_instance_of_a_traced_class_is_transparent(st):
    """The class guard must be transparent for an instance without a handle: a second, untraced
    broker of the same (now class-wrapped) type calls straight through."""
    traced = RecordingBroker()
    install(traced, state=st)
    untraced = RecordingBroker()                   # same class, but never install()ed
    assert untraced.submit_order(CANON)["status"] == "accepted"
    assert untraced.calls == 1 and _bypass_entries(st) == []


def test_tracer_allows_legit_gateway_submit_and_writes_no_bypass(st):
    """With the tracer guarding the gateway's private broker, a full gated submit reaches the
    broker exactly once and records NO bypass entry — the sanctioned frame is recognized."""
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    install(broker, state=st)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok and broker.calls == 1
    assert _bypass_entries(st) == []               # the legit path is never flagged
    assert st.audit.verify_chain().ok


def test_uninstall_restores_original_method(st):
    broker = RecordingBroker()
    install(broker, state=st)
    assert is_installed(broker)
    uninstall(broker)
    assert not is_installed(broker)
    # restored: a direct call now reaches the broker (the guard is gone)
    broker.submit_order(CANON)
    assert broker.calls == 1
    assert _bypass_entries(st) == []


def test_context_manager_scopes_the_tracer(st):
    broker = RecordingBroker()
    with traced_broker(broker, state=st):
        assert is_installed(broker)
        with pytest.raises(OrderAuthorityViolation):
            broker.submit_order(CANON)
    assert not is_installed(broker)
    broker.submit_order(CANON)                     # allowed again outside the block
    assert broker.calls == 1


def test_double_install_is_refused(st):
    broker = RecordingBroker()
    install(broker, state=st)
    with pytest.raises(OrderAuthorityViolation):
        install(broker, state=st)


def test_fail_closed_when_bypass_audit_raises(st):
    """The refusal — not the audit — is the protection: if the AuditLog append itself raises, the
    tracer STILL refuses and the broker is never entered (no 'record it later' allow path)."""
    class ExplodingAudit:
        def append(self, *a, **k):
            raise RuntimeError("audit backend down")

    class FakeState:
        audit = ExplodingAudit()

    broker = RecordingBroker()
    install(broker, state=FakeState())
    with pytest.raises(OrderAuthorityViolation):
        broker.submit_order(CANON)
    assert broker.calls == 0                        # fail closed even with a dead audit backend
