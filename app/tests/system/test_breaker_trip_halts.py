"""G9 system test — a DELIBERATE breaker trip HALTS the next order before the broker.

WP9 wires the full cockpit together and proves the top-level safety guarantee end-to-end:
*a deliberate degradation (an L6 circuit breaker, the SEC-04 killswitch, or the SEC-01
synchronous injection breaker) prevents the very next order from reaching the broker*. The
proof is CAUSAL, never vacuous: a NEGATIVE CONTROL first drives an otherwise-identical clean
order all the way through the REAL ``OrderGateway`` + ``FakePaperBroker`` and shows exactly
one broker call; each halt then reruns the same path with one degradation switched on and
shows the broker is NOT called after the trip, with the trip's ``AuditEntry`` preceding the
refusal (seq ordering) on the HMAC-verified chain.

Everything runs through PUBLIC entry points (``gateway.place_equity_order`` /
``AutoVetoCoordinator.resolve`` / ``killswitch`` / ``BreakerEngine``), persisted rows
(``BreakerState``, ``Order``), and HMAC-verified audit entries — mirroring the
``test_g8_acceptance`` chain-derived style. Hermetic: the frozen harness (tmp SQLite, autouse
in-memory keyring, ``FrozenClock`` at the market-open instant, an isolated
``COCKPIT_HALT_PATH``, a credential-free ``FakePaperBroker``). No wall-clock, no network.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from bridge.anchor import post_head_anchor
from bridge.veto import AutoVetoCoordinator
from brokers.gateway import EVENT_ORDER_REFUSED
from brokers.order_tuple import canonical_order_tuple
from config.settings import Mode
from orchestrator.breakers import BRK_DAILY_DRAWDOWN, BreakerSignals
from orchestrator.gate import GateResult
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.killswitch import killswitch
from orchestrator.state import EVENT_BREAKER_TRIP, EVENT_KILLSWITCH
from schemas.models.audit_entry import AuditEntry
from schemas.models.breaker_state import BreakerState

from tests.system.harness import build_order_scenario

# The clean resolved order shared by the auto_veto tickets (matches build_order_scenario's
# AAPL/buy/10/limit/150/day defaults so the veto ticket hash binds the same order).
_RESOLVED = canonical_order_tuple(symbol="AAPL", side="buy", qty="10", order_type="limit",
                                  limit_price="150", tif="day")


# ---------------------------------------------------------------------------------------
# chain-derived reads (only HMAC-verified entries count; mirrors g8's _verified_entries)
# ---------------------------------------------------------------------------------------
def _verified_after(st, event_type: str, floor: int) -> list[AuditEntry]:
    """Verified AuditEntries of ``event_type`` with ``seq > floor``, seq-ascending. A refusal
    or trip that is not HMAC-verifiable is not trusted (contract audit rule)."""
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == event_type)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    return [e for e in entries if e.seq > floor and st.audit.verify_entry(e)]


def _injection_rows(st) -> list[BreakerState]:
    with st.session_factory() as s:
        return s.execute(
            select(BreakerState).where(BreakerState.breaker_name == "injection_anomaly")
        ).scalars().all()


def _clean_submit(system, **over):
    """Drive a fully-approved clean order through the REAL gateway (review-then-submit)."""
    sc = build_order_scenario(system, mode=Mode.review_live, **over)
    return sc, system.gateway.place_equity_order(sc.order, **sc.submit_kwargs())


# =======================================================================================
# G9 — the acceptance test: negative control submits ONCE, then three halts each refuse,
# leaving the broker at exactly one call. One system, one broker, one causal story.
# =======================================================================================
def test_g9_breaker_trip_halts_next_order(system):
    broker = system.broker

    # ---- NEGATIVE CONTROL (causality): with NO breaker tripped, a clean order provably
    # reaches the broker exactly once. Everything below is the SAME path with one
    # degradation switched on; if the broker count ever moves past 1, the trip was not the
    # cause of the halt. ----------------------------------------------------------------
    _, out = _clean_submit(system, proposal_id="pc", nonce="nc")
    assert out.ok is True, f"clean order must submit, got {out.stage}: {out.reason}"
    assert out.stage == "submitted"
    assert out.broker_order_id and out.broker_order_id.startswith("paper-")
    assert len(broker.submits) == 1, "the clean control must reach the broker exactly once"
    assert system.st.audit.verify_chain().ok
    assert system.ladder.current() is Mode.review_live

    # ---- (i) L6 circuit breaker in auto_veto: the trip drops the mode, so the auto-exec
    # window refuses to mint — the token that would feed the gateway never exists. -------
    sc_auto = build_order_scenario(system, mode=Mode.review_live, proposal_id="pi",
                                   mint=False, anchor=False)
    armed = system.ladder.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"), operator="op")
    assert armed.armed, armed.reasons
    post_head_anchor(system.st, record=False)          # cover the head so an auto read verifies
    assert system.ladder.current() is Mode.auto_veto

    mono = [1000.0]
    coord = AutoVetoCoordinator(
        system.st, system.token_registry, window_seconds=1.0,
        monotonic=lambda: mono[0], breaker_engine=system.breaker, mode_ladder=system.ladder)

    # positive micro-control: a CLEAN elapsed+delivered window in a still-live auto_veto mode
    # mints the auto-exec token (resolve itself NEVER calls the broker — it is the approval
    # equivalent). This is what the trip below takes away.
    coord.schedule(proposal_id="pi-clean", risk_decision=sc_auto.risk_decision,
                   resolved_order=_RESOLVED)
    coord.confirm_delivery("pi-clean")                 # stamps delivered_at = mono (1000)
    mono[0] = 1002                                     # past the 1s window
    live_before = system.token_registry.live_count()
    res_clean = coord.resolve("pi-clean")
    assert res_clean.executed is True and res_clean.reason == "elapsed_clean"
    assert res_clean.token is not None
    assert system.token_registry.live_count() == live_before + 1
    assert len(broker.submits) == 1, "resolve() must never call the broker"

    # negative: same clean window, but a deliberate BRK_DAILY_DRAWDOWN trip drops auto_veto ->
    # review_live between confirm and fire, so resolve refuses ('mode_changed'), mints NO token.
    coord.schedule(proposal_id="pi-trip", risk_decision=sc_auto.risk_decision,
                   resolved_order=_RESOLVED)
    coord.confirm_delivery("pi-trip")                  # delivered_at = mono (1002)
    mono[0] = 1004
    assert system.ladder.current() is Mode.auto_veto, "mode must be auto_veto BEFORE the trip"

    floor_i = system.st.audit.head()[0]
    events = system.breaker.evaluate_and_trip(
        BreakerSignals(equity_drawdown_today=Decimal("600")))  # 600 > 500 default limit
    assert any(ev.breaker == BRK_DAILY_DRAWDOWN for ev in events), "drawdown breaker must trip"
    trips_i = _verified_after(system.st, EVENT_BREAKER_TRIP, floor_i)
    assert any((e.payload or {}).get("breaker") == BRK_DAILY_DRAWDOWN for e in trips_i), \
        "an HMAC-verified EVENT_BREAKER_TRIP for the daily-loss breaker must be recorded"
    assert system.ladder.current() is Mode.review_live, "the L6 trip must drop autonomy"

    tokens_before = system.token_registry.live_count()
    res_trip = coord.resolve("pi-trip")
    assert res_trip.executed is False
    assert res_trip.reason == "mode_changed", "the trip must cancel the auto-exec"
    assert res_trip.token is None
    assert system.token_registry.live_count() == tokens_before, "no token may be minted post-trip"
    assert len(broker.submits) == 1, "no order may reach the broker after the L6 trip"

    # Active breakers remain an order-path backstop until explicitly cleared. Isolate the
    # independent SEC-01 injection subcase with the operator acknowledgement the real system
    # requires; a restart or mere mode change is not a clear.
    system.breaker.clear(BRK_DAILY_DRAWDOWN, "test isolates injection breaker", actor="op")

    # ---- (iii) SEC-01 synchronous injection breaker: gate (c) trips inside the order path,
    # so the gateway refuses at stage 'breaker' before ever touching the broker. ---------
    sc_inj = build_order_scenario(system, mode=Mode.review_live, proposal_id="pj", nonce="nj")
    sc_inj.order.injection_detected = True             # default_injection_predicate trips on this
    floor_iii = system.st.audit.head()[0]
    out_inj = system.gateway.place_equity_order(sc_inj.order, **sc_inj.submit_kwargs())
    assert out_inj.ok is False
    assert out_inj.stage == "breaker", f"expected gate (c) refusal, got {out_inj.stage}"
    assert len(broker.submits) == 1, "an injection-tripped order must not reach the broker"
    assert any(b.tripped for b in _injection_rows(system.st)), \
        "a BreakerState injection_anomaly row must be persisted"
    assert system.ladder.current() is Mode.review_live, "SEC-01 must force-disarm to review_live"

    inj_trips = [e for e in _verified_after(system.st, EVENT_BREAKER_TRIP, floor_iii)
                 if (e.payload or {}).get("breaker") == "injection_anomaly"]
    inj_refusals = _verified_after(system.st, EVENT_ORDER_REFUSED, floor_iii)
    assert inj_trips and inj_refusals
    assert min(e.seq for e in inj_trips) < min(e.seq for e in inj_refusals), \
        "the injection trip must precede the order refusal on the chain"

    # ---- (ii) SEC-04 killswitch: the HALT sentinel makes gate (a) refuse the very next
    # order at stage 'halt'. Built BEFORE the kill so the scenario itself is otherwise
    # fully submittable — only the HALT stops it. --------------------------------------
    sc_halt = build_order_scenario(system, mode=Mode.review_live, proposal_id="pk", nonce="nk")
    floor_ii = system.st.audit.head()[0]
    killswitch(system.st, actor="op", reason="g9 deliberate kill")
    assert halt_is_set(default_halt_path()) is True

    out_halt = system.gateway.place_equity_order(sc_halt.order, **sc_halt.submit_kwargs())
    assert out_halt.ok is False
    assert out_halt.stage == "halt", f"expected HALT refusal, got {out_halt.stage}"
    assert len(broker.submits) == 1, "no order may reach the broker after the killswitch"

    kills = _verified_after(system.st, EVENT_KILLSWITCH, floor_ii)
    halt_refusals = _verified_after(system.st, EVENT_ORDER_REFUSED, floor_ii)
    assert kills and halt_refusals
    assert min(e.seq for e in kills) < min(e.seq for e in halt_refusals), \
        "the killswitch must precede the order refusal on the chain"

    # ---- the guarantee: only the negative control ever reached the broker. ------------
    assert len(broker.submits) == 1, "exactly one broker call — the clean control, none after a trip"
    assert system.st.audit.verify_chain().ok, "every state-changing path leaves the chain verified"


# =======================================================================================
# Focused (i): an L6 trip in auto_veto blocks the auto-exec window — clean mints, trip refuses.
# =======================================================================================
def test_g9_l6_trip_in_auto_veto_blocks_autoexec(system):
    sc = build_order_scenario(system, mode=Mode.review_live, proposal_id="p", mint=False,
                              anchor=False)
    armed = system.ladder.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"), operator="op")
    assert armed.armed, armed.reasons
    post_head_anchor(system.st, record=False)
    assert system.ladder.current() is Mode.auto_veto

    mono = [500.0]
    coord = AutoVetoCoordinator(
        system.st, system.token_registry, window_seconds=1.0,
        monotonic=lambda: mono[0], breaker_engine=system.breaker, mode_ladder=system.ladder)

    # positive control: clean window mints the token (no broker call).
    coord.schedule(proposal_id="clean", risk_decision=sc.risk_decision, resolved_order=_RESOLVED)
    coord.confirm_delivery("clean")
    mono[0] = 502
    ok = coord.resolve("clean")
    assert ok.executed is True and ok.token is not None
    assert len(system.broker.submits) == 0, "the auto-exec path never calls the broker directly"

    # negative: deliberate trip drops the mode; the next window refuses to mint.
    coord.schedule(proposal_id="trip", risk_decision=sc.risk_decision, resolved_order=_RESOLVED)
    coord.confirm_delivery("trip")
    mono[0] = 504
    system.breaker.trip(BRK_DAILY_DRAWDOWN, "deliberate daily-loss trip")
    assert system.ladder.current() is Mode.review_live
    tokens = system.token_registry.live_count()
    blocked = coord.resolve("trip")
    assert blocked.executed is False and blocked.reason == "mode_changed"
    assert blocked.token is None
    assert system.token_registry.live_count() == tokens
    assert len(system.broker.submits) == 0
    assert system.st.audit.verify_chain().ok


# =======================================================================================
# Focused (ii): the killswitch HALT sentinel refuses the next order at gate (a).
# =======================================================================================
def test_g9_killswitch_halt_refuses_next_order(system):
    # positive control: an identical clean order reaches the broker BEFORE the kill.
    _, ok = _clean_submit(system, proposal_id="ok", nonce="ok")
    assert ok.ok is True and ok.stage == "submitted"
    assert len(system.broker.submits) == 1

    sc_halt = build_order_scenario(system, mode=Mode.review_live, proposal_id="halt", nonce="halt")
    floor = system.st.audit.head()[0]
    killswitch(system.st, actor="op", reason="focused kill")
    assert halt_is_set(default_halt_path()) is True

    out = system.gateway.place_equity_order(sc_halt.order, **sc_halt.submit_kwargs())
    assert out.ok is False and out.stage == "halt"
    assert len(system.broker.submits) == 1, "the HALT must stop the next order at the broker seam"

    kills = _verified_after(system.st, EVENT_KILLSWITCH, floor)
    refusals = _verified_after(system.st, EVENT_ORDER_REFUSED, floor)
    assert kills and refusals
    assert min(e.seq for e in kills) < min(e.seq for e in refusals)
    assert system.st.audit.verify_chain().ok


# =======================================================================================
# Focused (iii): the SEC-01 injection breaker refuses inside the order path at gate (c).
# =======================================================================================
def test_g9_injection_breaker_refuses_at_gate_c(system):
    # positive control: the same scenario WITHOUT the injection flag submits cleanly.
    _, ok = _clean_submit(system, proposal_id="ok", nonce="ok")
    assert ok.ok is True and ok.stage == "submitted"
    assert len(system.broker.submits) == 1

    sc = build_order_scenario(system, mode=Mode.review_live, proposal_id="bad", nonce="bad")
    sc.order.injection_detected = True
    floor = system.st.audit.head()[0]
    out = system.gateway.place_equity_order(sc.order, **sc.submit_kwargs())
    assert out.ok is False and out.stage == "breaker"
    assert len(system.broker.submits) == 1, "an injection-tripped order must not reach the broker"

    assert any(b.tripped for b in _injection_rows(system.st)), \
        "the SEC-01 breaker must persist a tripped injection_anomaly BreakerState row"
    assert system.ladder.current() is Mode.review_live, "SEC-01 must force-disarm to review_live"

    trips = [e for e in _verified_after(system.st, EVENT_BREAKER_TRIP, floor)
             if (e.payload or {}).get("breaker") == "injection_anomaly"]
    refusals = _verified_after(system.st, EVENT_ORDER_REFUSED, floor)
    assert trips and refusals
    assert min(e.seq for e in trips) < min(e.seq for e in refusals)
    assert system.st.audit.verify_chain().ok
