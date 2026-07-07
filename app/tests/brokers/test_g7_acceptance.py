"""WP7 G-gate ACCEPTANCE tests — the order path is the ONE gate (Inv 1/6; SEC-01/03/04/07/08).

These are the gate-G7 acceptance criteria, written adversarially against the shipped
``brokers/`` implementation (state machine + ``OrderGateway`` + adapters). Each test states one
criterion and asserts real behavior on an in-memory SQLite DB, ``OrchestratorState.build`` and a
``FrozenClock`` (all wired by ``conftest``); nothing security-relevant is mocked away.

Criteria covered (one test each):
  * state-machine legal / illegal transitions (adjacency + terminal immutability);
  * EVERY order-state transition writes a hash-chained AuditEntry BEFORE returning success (Inv 6);
  * ``review_equity_order`` PROVABLY precedes ``place_equity_order``;
  * INVARIANT 1 CALL-SITE: the ONLY code reaching a broker submit passes risk -> mode -> approval,
    proven statically (AST scan: the sole ``.submit_order(`` call outside an adapter's own
    injected-transport delegation is inside ``OrderGateway.submit``, after the gates) AND at
    runtime (a stack-capturing broker + ``sys.settrace`` tracer that catches getattr / partial /
    string-shim dispatch and shows the gates ran before the broker call) — SEC-03;
  * TOCTOU: a risk verdict that deteriorates after approval VOIDS the submit (SEC-20 bypass logged);
  * single-use approval replay is refused (SEC-07);
  * HALT dropped mid-flight refuses the next submit (SEC-04);
  * auto mode + a non-Claude producing runtime is refused (SEC-08 / Inv 2);
  * the pending-exposure cap halts a burst (SEC-07).
"""
from __future__ import annotations

import ast
import functools
import inspect
import pathlib
import sys
from decimal import Decimal

import pytest
from sqlalchemy import select

import brokers
import brokers.gateway as gwmod
from brokers.gateway import (
    BYPASS_HARD_CHECK_FAILED,
    BYPASS_RESUBMIT_AFTER_DENIED,
    OrderGateway,
)
from brokers.order_state_machine import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    IllegalOrderTransition,
    create_order,
    legal_order_transition,
    transition,
)
from config.settings import Mode
from orchestrator.gate import GateResult
from orchestrator.halt import default_halt_path, set_halt
from orchestrator.state import EVENT_ORDER_TRANSITION
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order

from tests.brokers.conftest import build_scenario


# =======================================================================================
# test brokers: count reaches (RecordingBroker) and capture the call stack (StackBroker)
# =======================================================================================
class RecordingBroker:
    """A broker seam that counts ``submit_order`` calls so a refusal can be PROVEN to never
    reach the broker (fail closed)."""

    def __init__(self):
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return {"broker_order_id": "rec-1", "status": "accepted",
                "filled_qty": Decimal("0"), "avg_fill_price": None}


class StackBroker(RecordingBroker):
    """Records the call stack at ``submit_order`` time so the runtime tracer can prove the ONLY
    reach to the broker comes through ``OrderGateway.submit`` (SEC-03)."""

    def __init__(self):
        super().__init__()
        self.stacks: list[list[str]] = []

    def submit_order(self, canonical):
        self.stacks.append([fr.function for fr in inspect.stack()])
        return super().submit_order(canonical)


# =======================================================================================
# helpers
# =======================================================================================
def _entries(st, event_type):
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
    return [r for r in rows if r.event_type == event_type]


def _transition_entries(st):
    return _entries(st, EVENT_ORDER_TRANSITION)


def _new_order(st, hash_="h1"):
    return create_order(st, proposal_id="p1", risk_decision_id="rd1", account="paper",
                        symbol="AAPL", side="buy", qty=Decimal("10"), order_type="limit",
                        limit_price=Decimal("150"), tif="day", notional=Decimal("1500"),
                        order_tuple_hash=hash_, actor="claude")


def _arm_auto_veto(sc):
    res = sc.ladder.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"), operator="op")
    assert res.armed and sc.ladder.current() is Mode.auto_veto


# =======================================================================================
# (1) state-machine legal / illegal transitions
# =======================================================================================
def test_g7_state_machine_legal_and_illegal_transitions(st):
    # legal single-rung edges accepted.
    for src, dst in [("proposed", "approved"), ("approved", "submitted"),
                     ("submitted", "partially_filled"), ("submitted", "filled"),
                     ("partially_filled", "filled")]:
        assert legal_order_transition(src, dst), f"{src}->{dst} should be legal"
    # illegal edges rejected: rung-skips, going backwards, and partially_filled->rejected.
    for src, dst in [("proposed", "submitted"), ("proposed", "filled"),
                     ("approved", "filled"), ("submitted", "approved"),
                     ("partially_filled", "rejected")]:
        assert not legal_order_transition(src, dst), f"{src}->{dst} should be illegal"
    # every terminal state is a sink (no outgoing edges), including canary_breach.
    for term in TERMINAL_STATES:
        assert not any(legal_order_transition(term, d)
                       for d in ("approved", "submitted", "filled", "cancelled"))
    # canary_breach is reachable from every non-terminal state (forced safety drop).
    for src in LEGAL_TRANSITIONS:
        assert legal_order_transition(src, "canary_breach")

    # runtime enforcement: an illegal transition on a live row raises and mutates NOTHING.
    o = _new_order(st)
    before = len(_transition_entries(st))
    with pytest.raises(IllegalOrderTransition):
        transition(st, o, "filled", actor="claude")          # proposed->filled is illegal
    assert len(_transition_entries(st)) == before            # no audit written
    with st.session_factory() as s:
        assert s.get(Order, o.id).state == "proposed"        # row untouched
    assert st.audit.verify_chain().ok


# =======================================================================================
# (2) EVERY transition writes a hash-chained AuditEntry BEFORE returning success (Inv 6)
# =======================================================================================
def test_g7_every_transition_writes_hash_chained_audit_before_return(st):
    o = _new_order(st)                                        # births at 'proposed'
    born = _transition_entries(st)
    assert len(born) == 1
    assert born[0].payload["from"] is None and born[0].payload["to"] == "proposed"
    assert (born[0].refs or {}).get("row_id") == o.id
    assert st.audit.verify_entry(born[0])                     # individually HMAC-valid

    # each subsequent legal step: the covering entry ALREADY exists and verifies on RETURN.
    for to_state, frm in [("approved", "proposed"), ("submitted", "approved"),
                          ("filled", "submitted")]:
        n = len(_transition_entries(st))
        returned = transition(st, o, to_state, actor="claude", reason="step")
        entries = _transition_entries(st)
        assert len(entries) == n + 1                          # exactly ONE new covering entry
        newest = entries[-1]
        assert newest.payload["from"] == frm and newest.payload["to"] == to_state
        assert (newest.refs or {}).get("row_id") == o.id == returned.id
        assert st.audit.verify_entry(newest)                 # the new link is HMAC-valid...
        assert st.audit.verify_chain().ok                    # ...and the whole chain still verifies
    # every covering entry carries the SEC-07 order_tuple_hash provenance.
    assert all(e.payload.get("order_tuple_hash") == "h1" for e in _transition_entries(st))


# =======================================================================================
# (3) review_equity_order PROVABLY precedes place_equity_order
# =======================================================================================
def test_g7_review_provably_precedes_place(st, monkeypatch):
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    calls: list[str] = []
    real_review, real_submit = sc.gateway.review_equity_order, sc.gateway.submit
    monkeypatch.setattr(sc.gateway, "review_equity_order",
                        lambda *a, **k: (calls.append("review"), real_review(*a, **k))[1])
    monkeypatch.setattr(sc.gateway, "submit",
                        lambda *a, **k: (calls.append("submit"), real_submit(*a, **k))[1])
    out = sc.gateway.place_equity_order(sc.order, **sc.submit_kwargs())
    assert out.ok and calls == ["review", "submit"]          # review PROVABLY first

    # and when review refuses, submit is NEVER reached (no broker call, token intact).
    broker2 = RecordingBroker()
    sc2 = build_scenario(st, broker=broker2)
    set_halt(default_halt_path(), reason="test")             # review gate (a) HALT fails
    out2 = sc2.gateway.place_equity_order(sc2.order, **sc2.submit_kwargs())
    assert not out2.ok and broker2.calls == 0
    assert sc2.gateway.live_token_count() == 1               # capability token never consumed


# =======================================================================================
# (4) INVARIANT 1 CALL-SITE — the ONLY code reaching a broker submit passes risk->mode->approval
# =======================================================================================
def _submit_order_call_sites():
    """Every ``.submit_order(...)`` call across the brokers package, tagged with the module and
    the innermost enclosing function it lives in."""
    brokers_dir = pathlib.Path(brokers.__file__).parent
    sites: list[tuple[str, str | None, int]] = []
    for py in sorted(brokers_dir.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "submit_order"):
                p, fname = parents.get(node), None
                while p is not None:
                    if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fname = p.name
                        break
                    p = parents.get(p)
                sites.append((py.stem, fname, node.lineno))
    return sites


def test_g7_invariant1_only_gated_callsite_reaches_broker_ast():
    sites = _submit_order_call_sites()
    modfuncs = sorted({(m, f) for m, f, _ in sites})
    # EXACTLY two ``.submit_order(`` calls exist in the whole brokers package:
    #   * ("gateway", "submit")          — the single GATED reach to the broker;
    #   * ("alpaca_paper", "submit_order") — the adapter delegating to its OWN injected
    #     transport (``self._client``), i.e. the broker calling itself, not reaching out.
    # Anything else (a job/bridge/module-level call bypassing the gate) fails Invariant 1.
    assert modfuncs == [("alpaca_paper", "submit_order"), ("gateway", "submit")], modfuncs
    assert len([s for s in sites if s[0] == "gateway"]) == 1  # the gateway reaches the broker ONCE


def test_g7_invariant1_gates_precede_broker_call_ast():
    tree = ast.parse(pathlib.Path(gwmod.__file__).read_text(encoding="utf-8"))
    submit = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "submit")

    def line_of(attr):
        for c in ast.walk(submit):
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == attr):
                return c.lineno
        return None

    ln_precheck = line_of("_precheck")      # risk / mode / breaker / approval / runtime gates
    ln_consume = line_of("consume")         # single-use capability token
    ln_pending = line_of("_pending_ok")     # SEC-07 pending-exposure cap
    ln_broker = line_of("submit_order")     # the broker reach
    assert None not in (ln_precheck, ln_consume, ln_pending, ln_broker)
    # every gate appears textually BEFORE the single broker call inside ``submit`` — the reach
    # to the broker provably follows risk -> mode -> approval -> capability -> exposure.
    assert ln_precheck < ln_broker
    assert ln_consume < ln_broker
    assert ln_pending < ln_broker


def test_g7_invariant1_runtime_tracer_gates_precede_and_dispatch_tricks_blocked(st):
    broker = StackBroker()
    sc = build_scenario(st, broker=broker, mint=False)       # NO live capability token
    kw = sc.submit_kwargs()

    # (a) every dispatch trick an attacker holding the gateway could try — none reach the broker.
    getattr(sc.gateway, "submit")(sc.order, **kw)                       # getattr dispatch
    functools.partial(sc.gateway.submit, sc.order)(**kw)               # partial dispatch
    ({"submit": sc.gateway.submit}["submit"])(sc.order, **kw)          # string-keyed shim
    assert broker.calls == 0 and broker.stacks == []                   # broker NEVER entered

    # (b) a LEGIT submit (token minted) reaches the broker — and the tracer proves the gates ran
    # before the reach, and the stack shows the reach came from inside OrderGateway.submit.
    sc.gateway.mint_submit_token(nonce=sc.nonce, proposal_id="p1",
                                 risk_decision_id=sc.risk_decision.id,
                                 order_tuple_hash=sc.tuple_hash)
    seen: list[str] = []

    def tracer(frame, event, arg):
        if event == "call":
            name, fn = frame.f_code.co_name, frame.f_code.co_filename
            if fn.endswith("gateway.py") or name == "submit_order":
                seen.append(name)
        return tracer

    prev = sys.gettrace()
    sys.settrace(tracer)
    try:
        out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    finally:
        sys.settrace(prev)

    assert out.ok and broker.calls == 1
    # risk/mode/approval (via _precheck) ran BEFORE the broker reach, all inside submit.
    assert "submit" in seen and "_precheck" in seen and "submit_order" in seen
    assert seen.index("_precheck") < seen.index("submit_order")
    # the runtime stack confirms the broker was entered through OrderGateway.submit.
    assert "submit" in broker.stacks[-1]


# =======================================================================================
# (5) TOCTOU — a risk verdict that deteriorates after approval VOIDS the submit
# =======================================================================================
def test_g7_toctou_risk_flip_voids_submit(st):
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)

    def flipped(_positions):     # risk deteriorated AFTER the human approved (time-of-use)
        return {"decision": "rejected", "failed_checks": ["buying_power"]}

    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(risk_recheck=flipped))
    assert not out.ok and out.stage == "risk" and broker.calls == 0
    # the stale approval could NOT authorize the now-failing order; nothing was consumed.
    assert sc.approval.single_use_consumed is False
    assert sc.gateway.live_token_count() == 1
    # a hard-check failure records a BYPASS event the performance gate watches (SEC-20).
    assert _entries(st, BYPASS_HARD_CHECK_FAILED)
    assert st.audit.verify_chain().ok


# =======================================================================================
# (6) single-use approval replay refused (SEC-07)
# =======================================================================================
def test_g7_single_use_approval_replay_refused(st):
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    first = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert first.ok and broker.calls == 1
    assert sc.approval.single_use_consumed is True           # approval burned single-use
    assert sc.gateway.live_token_count() == 0                # token burned single-use

    # a replay with the SAME approval is refused at the approval gate BEFORE the broker.
    replay = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not replay.ok and replay.stage == "approval"
    assert broker.calls == 1                                 # the replay never reached the broker
    assert _entries(st, BYPASS_RESUBMIT_AFTER_DENIED)


# =======================================================================================
# (7) HALT dropped mid-flight refuses the next submit (SEC-04)
# =======================================================================================
def test_g7_halt_mid_flight_refuses_next_submit(st):
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    # review passes cleanly (time-of-check)...
    review = sc.gateway.review_equity_order(sc.order, **sc.review_kwargs())
    assert review.ok and broker.calls == 0
    # ...then the operator drops the killswitch HALT before submit (time-of-use).
    set_halt(default_halt_path(), reason="killswitch mid-flight")
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "halt" and broker.calls == 0
    assert sc.gateway.live_token_count() == 1                # token never consumed on the refusal


# =======================================================================================
# (8) auto mode + a non-Claude producing runtime is refused (SEC-08 / Inv 2)
# =======================================================================================
def test_g7_auto_mode_non_claude_runtime_refused(st):
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker)
    _arm_auto_veto(sc)
    # a non-Claude producing runtime under an auto mode is refused at the runtime pin...
    bad = sc.gateway.submit(sc.order, **sc.submit_kwargs(producing_runtime="codex"))
    assert not bad.ok and bad.stage == "runtime" and broker.calls == 0
    assert sc.gateway.live_token_count() == 1                # not consumed on the runtime refusal
    # ...and claude under the SAME auto mode is allowed (proving the pin is the discriminator).
    good = sc.gateway.submit(sc.order, **sc.submit_kwargs(producing_runtime="claude"))
    assert good.ok and broker.calls == 1


# =======================================================================================
# (9) the pending-exposure cap halts a burst (SEC-07)
# =======================================================================================
def test_g7_pending_exposure_cap_halts_burst(st):
    broker = RecordingBroker()
    gw = OrderGateway(broker, pending_exposure_cap=Decimal("2000"))
    # first order (1500) fits under the 2000 cap and submits.
    sc1 = build_scenario(st, gateway=gw, broker=broker, notional="1500")
    out1 = sc1.gateway.submit(sc1.order, **sc1.submit_kwargs())
    assert out1.ok and broker.calls == 1
    # a burst: a SECOND order (1500) pushes pending exposure to 3000 > 2000 -> refused.
    sc2 = build_scenario(st, gateway=gw, broker=broker, notional="1500")
    out2 = sc2.gateway.submit(sc2.order, **sc2.submit_kwargs())
    assert not out2.ok and out2.stage == "pending_exposure"
    assert broker.calls == 1                                 # the burst never reached the broker
