"""G9 — USAGE-LIMIT HIT MID-LOOP -> WATCH-ONLY, at the COMPOSED-SYSTEM level (WP9).

Elevates the g8-4 unit guarantee (``tests/workflows/test_g8_acceptance.test_g8_4_...``) to the
fully-wired cockpit: the intraday triage tick runs through the REAL scheduler seam
(``run_intraday_loop`` via ``SystemUnderTest.run_intraday``) over the REAL ``build_services``
closure, and the mid-loop vendor usage-limit is exercised by scripting the deep (opus) queue to
return ``fixtures_market_day.limit_result()`` on the FIRST deep escalation — exactly as a real
Anthropic usage-limit envelope would arrive after triage.

Guarantees under test (docs/build-scaffold-ultracode.md WP9 + Inv 7 / SEC-23):
  * the composed loop degrades to ``degraded_watch_only`` mid-loop;
  * L8 usage exhaustion enters WATCH-ONLY for the ``claude`` runtime but does NOT drop the mode
    (it is not an L6 breaker) — the mode is UNCHANGED;
  * ``EVENT_WATCH_ONLY`` 'entered' fires EXACTLY once per exhaustion;
  * NO event is dropped — every persisted event id lands in a triage-batch disposition (Inv 7);
  * SYSTEM ADDITION (beyond g8-4): while watch-only, an order pushed through the REAL
    ``OrderGateway`` is refused at gate (b) stage ``'mode'`` (watch-only => order path disabled)
    and the ``FakePaperBroker`` is NEVER called;
  * recovery is FAIL-CLOSED to ``review_live`` and RE-ENABLES the order path (positive control:
    the SAME broker now fills), and a fresh loop over the same window PICKS UP the deferred
    candidates — nothing was lost.

Causality (no vacuous green): the main test pairs the disabled order path (watch-only) with the
re-enabled order path (post-recovery, broker fills) — proving the refusal was CAUSED by the
watch-only state, not a permanently broken path. A dedicated negative-control test runs the
identical composed loop with a VALID deep result and asserts the system never goes watch-only
and the order path stays live — proving the ``limit_result()`` is the cause.

Hermetic: the shared ``system`` fixture (tmp SQLite, autouse in-memory keyring, FrozenClock at
the market-open instant, isolated COCKPIT_HALT_PATH). Assertions go through public entry points
(scheduler seams, the gateway) + persisted rows + HMAC-verified audit payloads only.
"""
from __future__ import annotations

from sqlalchemy import select

from config.settings import Mode
from orchestrator.state import EVENT_WATCH_ONLY
from schemas.models.audit_entry import AuditEntry

from tests.system.harness import (
    RoutedTransport,
    build_order_scenario,
    build_system,
    envelope,
    market_days,
    triage_body,
)
from tests.workflows.fixtures_market_day import limit_result


# ---------------------------------------------------------------------------------------
# chain-derived read helpers (mirror tests/workflows/test_g8_acceptance style)
# ---------------------------------------------------------------------------------------
def _verified(st, event_type: str) -> list[AuditEntry]:
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == event_type).order_by(AuditEntry.seq)
        ).scalars().all()
    return [e for e in entries if st.audit.verify_entry(e)]


def _payloads(st, event_type: str) -> list[dict]:
    return [dict(e.payload or {}) for e in _verified(st, event_type)]


def _harvest(obj, out=None) -> set:
    """Every string reachable inside a JSON payload — robust disposition-membership check."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            _harvest(k, out)
            _harvest(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _harvest(v, out)
    elif isinstance(obj, str):
        out.add(obj)
    return out


# =======================================================================================
# G9 — usage-limit hit mid-loop -> watch-only, order path disabled, recovery re-enables
# =======================================================================================
def test_g9_usage_limit_midloop_watch_only_disables_order_path(tmp_path):
    system = build_system(tmp_path=tmp_path, mode=Mode.review_live)
    st = system.st
    day = market_days(1)[0]
    _, rows = day.persist(system)
    assert len(rows) == 3, "the fixture day must persist AAPL filing + MSFT filing + noise"
    # SEC-11 (W10): anchor the head before the deep-budget-consuming intraday tick (the budget
    # consult fails closed on an absent anchor, like the order path).
    system.anchor(clock=day.clock())
    aapl_id = rows["d0-f-aapl"].id
    msft_id = rows["d0-f-msft"].id
    noise_id = rows["d0-noise"].id

    mode_before = system.ladder.current()
    assert mode_before is Mode.review_live

    # ---- mid-loop usage-limit: triage succeeds (haiku), the FIRST deep escalation hits the
    # vendor usage limit (opus) -> L8 watch-only enters mid-loop; the rest are deferred. -----
    scored = day.triage_scored(rows)   # AAPL urgent 95, MSFT urgent 90 (both corroborated filings)
    t1 = RoutedTransport(
        haiku=[envelope(triage_body(scored, now=day.now, batch_id="ul-tb1"))],
        opus=[limit_result()],   # ONE deep call is made; watch-only + degrade stops the loop
    )
    res = system.run_intraday(t1, clock=day.clock())

    # (1) composed loop degrades mid-loop to watch-only.
    assert res["status"] == "degraded_watch_only"
    # (2) L8 watch-only for the claude runtime (deep alias -> runtime 'claude').
    assert system.breaker.is_watch_only("claude") is True
    # (3) mode is UNCHANGED — L8 exhaustion is not an L6 breaker drop.
    assert system.ladder.current() == mode_before, "L8 watch-only must NOT change the mode"

    # (4) EVENT_WATCH_ONLY 'entered' fires EXACTLY once (no 'recovered' key yet).
    entered = [p for p in _payloads(st, EVENT_WATCH_ONLY) if not p.get("recovered")]
    assert len(entered) == 1, "enter_watch_only must fire EXACTLY once per exhaustion"
    assert entered[0]["vendor"] == "claude"
    assert entered[0]["watch_only"] is True

    # (5) NO events dropped — every persisted event id appears in the triage-batch dispositions.
    harvest: set = set()
    for p in _payloads(st, "triage_batch"):
        harvest |= _harvest(p)
    for eid in (aapl_id, msft_id, noise_id):
        assert eid in harvest, f"event {eid} was dropped on usage exhaustion (Inv 7 violated)"
    # both escalation candidates must be recorded as deferred, never lost.
    deferred_ids = set(res.get("deferred") or [])
    assert {aapl_id, msft_id} <= deferred_ids, "both candidates must be DEFERRED under watch-only"

    assert st.audit.verify_chain().ok

    # ---- SYSTEM ADDITION: while watch-only, the REAL gateway refuses at stage 'mode' ---------
    submits_before = len(system.broker.submits)
    scen_w = build_order_scenario(system, mode=Mode.review_live, proposal_id="pw", nonce="nw")
    refused = system.gateway.place_equity_order(scen_w.order, **scen_w.submit_kwargs())
    assert refused.ok is False
    assert refused.stage == "mode", "watch-only must refuse at gate (b) 'mode', not later"
    # The durable watch-only coordinator now supplies a stable denial code.
    # Keep an exact assertion, plus the stage/no-broker and recovery controls.
    assert refused.reason == "review refused: watch_only_active"
    assert len(system.broker.submits) == submits_before, "FakePaperBroker must NOT be called"

    # audit-level proof of the refusal (chain-derived).
    refusals = [p for p in _payloads(st, "order_refused")
                if p.get("reason") == "[review] watch_only_active"
                and p.get("stage") == "mode"]
    assert refusals, "the watch-only order refusal must be audited (EVENT_ORDER_REFUSED)"

    # ---- recovery: fail-closed to review_live; watch-only clears --------------------------
    _event, mode_after = system.breaker.recover_from_watch_only("claude", mode_before)
    assert mode_after is Mode.review_live, "recovery must land FAIL-CLOSED on review_live"
    assert system.breaker.is_watch_only("claude") is False
    assert system.ladder.current() is Mode.review_live

    # (positive control for the order-path guarantee) the order path is RE-ENABLED: a fresh,
    # fully-valid scenario now SUBMITS and the broker fills — proving the earlier 'mode' refusal
    # was CAUSED by watch-only, not a permanently-broken path.
    scen_r = build_order_scenario(system, mode=Mode.review_live, proposal_id="pr", nonce="nr")
    placed = system.gateway.place_equity_order(scen_r.order, **scen_r.submit_kwargs())
    assert placed.ok is True and placed.stage == "submitted", "order path must be live again"
    assert placed.broker_order_id and placed.broker_order_id.startswith("paper-")
    assert len(system.broker.submits) == submits_before + 1, "the broker must fill exactly once"

    # ---- nothing lost: a fresh intraday loop over the same window PICKS UP the deferred ------
    head_before = st.audit.head()[0]
    t2 = day.scripted_transport(rows, proposal_ids={"d0-f-aapl": "prop-r-a",
                                                    "d0-f-msft": "prop-r-m"})
    res2 = system.run_intraday(t2, clock=day.clock())
    assert res2["status"] == "ok", "the fresh loop must run cleanly once watch-only cleared"

    picked_up: set = set()
    with st.session_factory() as s:
        fresh = s.execute(select(AuditEntry).where(AuditEntry.seq > head_before)).scalars().all()
    for e in fresh:
        if e.event_type in ("triage_batch", "deep_escalation", "escalation_deferred") \
                and st.audit.verify_entry(e):
            picked_up |= _harvest(e.payload or {})
    assert {aapl_id, msft_id} <= picked_up, "deferred candidates must be picked up by the next loop"

    assert st.audit.verify_chain().ok


# =======================================================================================
# NEGATIVE CONTROL — a VALID deep result on the identical composed loop never goes watch-only
# (proves the limit_result() is the CAUSE, not the loop structure) — and the order path stays live
# =======================================================================================
def test_g9_negative_control_valid_deep_no_watch_only(tmp_path):
    system = build_system(tmp_path=tmp_path / "ctrl", mode=Mode.review_live)
    st = system.st
    day = market_days(1)[0]
    _, rows = day.persist(system)
    system.anchor(clock=day.clock())   # SEC-11 (W10): witness fresh before the deep-budget tick

    mode_before = system.ladder.current()
    # identical structure to the main test, but the deep queue returns VALID proposals.
    transport = day.scripted_transport(rows)
    res = system.run_intraday(transport, clock=day.clock())

    assert res["status"] == "ok", "a valid deep result must NOT degrade the composed loop"
    assert system.breaker.is_watch_only("claude") is False, "no limit => no watch-only"
    assert [p for p in _payloads(st, EVENT_WATCH_ONLY) if not p.get("recovered")] == [], \
        "EVENT_WATCH_ONLY must NEVER fire without a usage-limit hit"
    assert system.ladder.current() == mode_before

    # the order path is LIVE (not watch-only-disabled): a valid scenario submits and fills.
    scen = build_order_scenario(system, mode=Mode.review_live, proposal_id="cp", nonce="cn")
    placed = system.gateway.place_equity_order(scen.order, **scen.submit_kwargs())
    assert placed.ok is True and placed.stage == "submitted"
    assert len(system.broker.submits) == 1, "the order path must be live when not watch-only"

    assert st.audit.verify_chain().ok
