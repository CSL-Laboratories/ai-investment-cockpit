"""WP9 — the 2-WEEK PAPER REPLAY RUNNER (G9 flagship; this author owns replay.py + the
test_paper_replay.py that asserts on it).

Implements docs/build-scaffold-ultracode.md WP9 ("wire the full system together end-to-end
and TEST it") + docs/ultracode-build-playbook.md §3 (G9 gate) as a REUSABLE soak driver: it
advances a deterministic ``FrozenClock`` across ~10 TRADING days (weekends/holidays skipped by
``harness.market_days``) and, each day, drives the WHOLE cockpit through its PUBLIC scheduler
seams exactly as ``main.run`` would — only with the hermetic fakes the harness composes:

    persist the day's recorded events (harness ``DayFixture.persist``)
      -> [auto days] arm ``auto_veto`` through the REAL ``ModeLadder`` + anchor the head
      -> ONE intraday tick (``run_intraday_loop`` -> triage -> escalate -> ``draft_proposal``)
      -> premarket brew (``run_premarket_brew`` -> BriefRecord + delivery)
      -> auto-APPROVE the day's corroborated proposal via the REAL human path
         (``bridge.approval.record_decision`` as the allowlisted operator; the only sanctioned
         token minter besides veto) and PLACE it through the REAL ``system.gateway`` +
         ``FakePaperBroker`` (review -> submit, two-phase intent/result on the signed chain)
      -> EOD review (``run_eod_review``)
      -> daily disarm (``run_daily_disarm``; auto -> review_live at close, SEC-23)
      -> anchor the verified head (``run_anchor_head``; SEC-11 external witness).

Everything runs through the frozen harness API (``build_system`` / ``market_days`` /
``DayFixture``); nothing here reaches for a private. The runner ACCUMULATES a ``ReplayReport``
whose fields let ``test_paper_replay.py`` assert the G9 guarantees across the WHOLE replay
without re-deriving anything: the audit chain still verifies at the end; the deep budget RESETS
per day (``spent(day_i)`` is that day's escalations, never cumulative); NO recorded event is
ever dropped (every persisted id lands in a triage disposition); auto modes disarm at close and
never silently re-arm; approved paper orders round-tripped review->submit (each ``FakePaperBroker``
submit is preceded by its two-phase ``intent:order_submit`` audit entry); and the external
anchor head advances monotonically.

REUSE (project rule — do not reinvent): the recorded day + scripted agent envelopes come from
the PROVEN ``tests/workflows/fixtures_market_day`` via the harness; the fully-signed approval /
order binding mirrors the PROVEN ``tests/brokers/conftest.build_scenario`` — but this runner
adjudicates through ``record_decision`` (the REAL bridge path) rather than hand-writing the
``EVENT_ADJUDICATION``, so the human-approval leg is exercised for real.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode
from core.ids import new_id
from ingest.normalize import content_hash
from orchestrator.gate import GateResult
from schemas.models.audit_entry import AuditEntry
from schemas.models.market_event import MarketEvent
from schemas.models.risk_decision import RiskDecision
from workflows.budget import DeepBudget

from bridge.approval import Rejection, record_decision

from tests.system.harness import (
    OPEN_UTC,
    FakeTransport,
    approved_risk_recheck,
    brief_body,
    envelope,
    market_days,
    scored_item,
    triage_body,
)

# The two-phase submit event types the gateway writes around the private broker call (SEC-13:
# an intent AuditEntry BEFORE the broker.submit_order, a result AuditEntry AFTER).
INTENT_ORDER_SUBMIT = "intent:order_submit"
RESULT_ORDER_SUBMIT = "result:order_submit"


# =======================================================================================
# report structures
# =======================================================================================
@dataclass
class DayReplay:
    """One replayed trading day's accumulated facts (all PUBLIC-entry / chain-derived)."""

    index: int
    day: date
    brief_id: str | None
    escalated: list[str]                       # MarketEvent ids escalated by the intraday tick
    placed_order_ids: list[str]                # Order ids that round-tripped review->submit
    broker_order_ids: list[str]                # broker acks returned by FakePaperBroker
    deep_budget_spent: int                     # DeepBudget.spent(this day) — MUST be per-day
    events_persisted: list[str]                # MarketEvent ids recorded this day
    mode_open: str                             # live mode after any morning arm
    mode_close: str                            # live mode after run_daily_disarm
    anchor_seq: int                            # external-witness head seq after the EOD anchor


@dataclass
class ReplayReport:
    """The whole-replay accumulation the G9 test asserts on. Every field is derived from a
    PUBLIC entry point, a persisted row, or an HMAC-verified audit entry — never an internal."""

    days: list[DayReplay] = field(default_factory=list)
    audit_ok: bool = False                     # st.audit.verify_chain().ok at the very end
    audit_head_seq: int = 0
    dispositioned_event_ids: set[str] = field(default_factory=set)   # union of triage batches
    persisted_event_ids: set[str] = field(default_factory=set)       # union of persisted events

    # ---- whole-replay timelines (the G9 assertions read these) ------------------------
    @property
    def anchor_seq_timeline(self) -> list[int]:
        return [d.anchor_seq for d in self.days]

    @property
    def mode_timeline(self) -> list[tuple[int, str, str]]:
        return [(d.index, d.mode_open, d.mode_close) for d in self.days]

    @property
    def placed_order_ids(self) -> list[str]:
        return [oid for d in self.days for oid in d.placed_order_ids]

    @property
    def broker_order_ids(self) -> list[str]:
        return [bid for d in self.days for bid in d.broker_order_ids]

    @property
    def total_orders(self) -> int:
        return len(self.placed_order_ids)

    @property
    def total_escalations(self) -> int:
        return sum(len(d.escalated) for d in self.days)

    @property
    def brief_ids(self) -> list[str]:
        return [d.brief_id for d in self.days if d.brief_id]

    @property
    def dropped_event_ids(self) -> set[str]:
        """Recorded events that never landed in a triage disposition (Inv 7: must be empty)."""
        return self.persisted_event_ids - self.dispositioned_event_ids


# =======================================================================================
# chain-derived helpers (mirror test_g8_acceptance style: verified entries only)
# =======================================================================================
def _verified_dispositions(st) -> set[str]:
    """Every event id appearing in ANY HMAC-verified EVENT_TRIAGE_BATCH ``dispositions`` map.
    An event dropped from every disposition is a silent loss (Inv 7) — this is how the G9 test
    proves nothing was dropped across the whole replay."""
    seen: set[str] = set()
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == "triage_batch")
        ).scalars().all()
    for e in entries:
        if not st.audit.verify_entry(e):
            continue
        for eid in (e.payload or {}).get("dispositions", {}):
            seen.add(eid)
    return seen


def _all_persisted_event_ids(st) -> set[str]:
    with st.session_factory() as s:
        return set(s.execute(select(MarketEvent.id)).scalars().all())


def _ensure_review_live(system, *, operator: str, clock) -> None:
    """Walk the REAL ladder up to review_live (order authority) if the composed system booted
    lower. Mirrors the harness ladder walk (read_only -> paper -> review_live)."""
    cur = system.ladder.current()
    if cur is Mode.read_only:
        system.ladder.set_mode(Mode.paper, actor=operator, clock=clock)
        system.ladder.set_mode(Mode.review_live, actor=operator, clock=clock)
    elif cur is Mode.paper:
        system.ladder.set_mode(Mode.review_live, actor=operator, clock=clock)


def _arm_auto_veto(system, *, operator: str, clock) -> None:
    """Arm ``auto_veto`` through the REAL ``arm_auto_mode`` (gate + Claude-runtime pin), then
    anchor so the auto mode is READABLE (an auto read requires the external witness to cover the
    head — ModeLadder.current() fail-closes to read_only otherwise). Mirrors g8-5's arm path."""
    armed = system.ladder.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"), operator=operator,
        clock=clock,
    )
    if not armed.armed:
        raise RuntimeError(f"replay could not arm auto_veto: {armed.reasons}")
    system.anchor(clock=clock)   # publish the post-arm head so current() reads auto_veto


# =======================================================================================
# the REAL human-approval + gateway order round-trip (bridge.record_decision -> gateway)
# =======================================================================================
def approve_and_place(system, *, clock, symbol: str, proposal_id: str, interaction_id: str,
                      operator: str, side: str = "buy", qty: str = "1",
                      limit_price: str = "150", producing_runtime: str = "claude"):
    """Adjudicate ONE proposal through the REAL path and place it through the REAL gateway.

    Persists an APPROVED ``RiskDecision``, then ``record_decision`` (the allowlisted operator,
    surface='discord') persists the ``ApprovalRecord`` + its signed ``EVENT_ADJUDICATION`` binding
    and mints the single-use submit token (nonce ``subtok_<approval id>``). Creates the ``Order``
    row bound to that approval, anchors the head (so the gateway's SEC-11 consult passes on the
    happy path), and calls ``place_equity_order`` — which PROVABLY reviews then submits, running
    every gate under the lock before the two-phase broker call. Returns ``(GateOutcome, Order)``.
    Raises if the operator adjudication is refused (that would be a harness/allowlist misconfig,
    not a system finding)."""
    st = system.st
    notional = str((Decimal(qty) * Decimal(limit_price)))

    rd = RiskDecision(
        id=new_id("rd"), proposal_id=proposal_id, decision="approved", checks={},
        failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
        human_required=False, evaluated_at=clock.now(), created_at=clock.now(),
    )
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": "approved", "proposal_id": proposal_id})

    resolved = {"symbol": symbol, "side": side, "qty": qty, "order_type": "limit",
                "limit_price": limit_price, "tif": "day"}
    tuple_hash = order_tuple_hash(canonical_order_tuple(**resolved))

    appr = record_decision(
        st, proposal_id=proposal_id, risk_decision=rd, resolved_order=resolved,
        actor_user_id=operator, user_decision="approve", surface="discord",
        interaction_id=interaction_id, clock=clock, modal_confirmed=True, notional=notional,
        token_registry=system.token_registry, mode=system.ladder.current(),
    )
    if isinstance(appr, Rejection):
        raise RuntimeError(f"real approval path refused the operator decision: {appr.code}")

    order = create_order(
        st, proposal_id=proposal_id, risk_decision_id=rd.id, approval_id=appr.id,
        account="paper", symbol=symbol, side=side, qty=qty, order_type="limit", tif="day",
        order_tuple_hash=tuple_hash, limit_price=limit_price, notional=notional,
        actor=producing_runtime, clock=clock,
    )

    system.anchor(clock=clock)   # head >= anchor so the gateway's SEC-11 check passes (happy path)

    outcome = system.gateway.place_equity_order(
        order, approval=appr, risk_decision=rd, ladder=system.ladder,
        breaker_engine=system.breaker, risk_recheck=approved_risk_recheck, positions={},
        token_nonce=f"subtok_{appr.id}", producing_runtime=producing_runtime, clock=clock,
    )
    return outcome, order


# =======================================================================================
# per-day drivers (all through the PUBLIC scheduler seams)
# =======================================================================================
def _make_day_distinct(day) -> None:
    """SEC-13 dedupe (events.persist_events) collapses byte-identical re-emits by their
    ingest-bound ``content_hash``. The harness ``market_days`` filing fixtures reuse the SAME
    headline/summary every day (only ``article_id`` carries the day), so their ``content_hash``
    collides and day-2+ filings would be dropped as duplicates on the ONE accumulating replay
    system — an artifact of fixture reuse, NOT an app bug (two real trading days' 8-Ks are
    distinct articles with distinct content). Rebind each event's ingest ``content_hash`` to a
    day-distinct value so every trading day persists its own filings. Done here in the runner;
    the frozen harness is never touched. Idempotent per fixture (fresh each ``market_days`` call)."""
    for ev in day.events:
        ev["content_hash"] = content_hash({"ingest": ev.get("content_hash"), "day": day.index})


def replay_day(system, day, *, operator: str, orders: int, arm_auto: bool) -> DayReplay:
    """Drive ONE trading day end-to-end through the public seams and return its accumulation."""
    st = system.st
    clock = day.clock()

    # ---- persist the recorded day (real ingest normalization already applied in the fixture) --
    _make_day_distinct(day)
    _result, rows = day.persist(system)
    events_persisted = [row.id for row in rows.values()]

    # SEC-11 (W10): the scheduler's independent run_anchor_head cadence keeps the external witness
    # fresh BEFORE the intraday tick consults it — the deep-budget path now fails closed on an
    # absent anchor, exactly like the order path (approve_and_place also anchors before placing).
    # Mirror that cadence here so the tick reads a current anchor (auto days re-anchor post-arm).
    system.anchor(clock=clock)

    # ---- [auto days] arm auto_veto BEFORE the tick so the mode timeline captures it -----------
    if arm_auto:
        _arm_auto_veto(system, operator=operator, clock=clock)
    mode_open = system.ladder.current().value

    # ---- ONE intraday tick: triage -> escalate -> draft proposal (deep budget consumed here) --
    intraday = system.run_intraday(day.scripted_transport(rows), clock=clock)
    escalated = list(intraday.get("escalated", []))

    # ---- premarket brew: BriefRecord + delivery (runs AFTER the tick so the shared triage-batch
    # window cursor does not starve the intraday window of the day's events) -------------------
    aapl_aid, aapl_sym, _ = day.escalating[0]
    msft_aid, msft_sym, _ = day.escalating[1]
    aapl, msft = rows[aapl_aid], rows[msft_aid]
    brew_scored = [scored_item(aapl.raw_ref, aapl_sym, "material", 72),
                   scored_item(msft.raw_ref, msft_sym, "material", 68)]
    brew_refs = sorted([aapl.raw_ref, msft.raw_ref])
    brew_transport = FakeTransport([
        envelope(triage_body(brew_scored, now=day.now, batch_id=f"brew-d{day.index}")),
        envelope(brief_body(brew_refs, now=day.now)),
    ])
    brew = system.run_brew(brew_transport, clock=clock)
    brief_id = brew.get("brief_id")

    # ---- auto-approve the day's corroborated proposal(s) via the REAL path + place them --------
    placed_order_ids: list[str] = []
    broker_order_ids: list[str] = []
    for k in range(orders):
        outcome, order = approve_and_place(
            system, clock=clock, symbol=aapl_sym,
            proposal_id=f"replay-d{day.index}-{k}",
            interaction_id=f"ix-d{day.index}-{k}", operator=operator,
        )
        if outcome.ok and outcome.broker_order_id is not None:
            placed_order_ids.append(order.id)
            broker_order_ids.append(outcome.broker_order_id)
        else:  # a refused placement is a real finding — surface it, never swallow it
            raise RuntimeError(
                f"day {day.index}: approved paper order refused at gate "
                f"{outcome.stage!r}: {outcome.reason}")

    # ---- EOD review + daily disarm + head anchor --------------------------------------------
    system.run_eod(clock=clock, drawdown_fn=lambda: Decimal("0"))
    system.disarm(clock=clock)
    mode_close = system.ladder.current().value
    anchored = system.anchor(clock=clock)
    anchor_seq = int(anchored.get("anchored_seq", 0))

    return DayReplay(
        index=day.index, day=day.now.date(), brief_id=brief_id, escalated=escalated,
        placed_order_ids=placed_order_ids, broker_order_ids=broker_order_ids,
        deep_budget_spent=DeepBudget(st).spent(day.now.date()),
        events_persisted=events_persisted, mode_open=mode_open, mode_close=mode_close,
        anchor_seq=anchor_seq,
    )


# =======================================================================================
# the public runner
# =======================================================================================
def run_paper_replay(system_or_factory, *, days: int = 10, orders_per_day: int = 1,
                     auto_days: tuple[int, ...] = (), operator: str = "lucas",
                     start: datetime = OPEN_UTC) -> ReplayReport:
    """Run a deterministic N-trading-day paper replay and return a ``ReplayReport``.

    ``system_or_factory`` is either a composed ``SystemUnderTest`` (from the harness ``system``
    fixture / ``build_system``) or a zero-arg factory returning one. The ladder is walked up to
    ``review_live`` once at the start (order authority); ``auto_days`` are indices on which
    ``auto_veto`` is armed at the open and disarmed at the close (SEC-23). ``operator`` MUST be on
    the ``COCKPIT_OPERATOR_IDS`` allowlist for the real approval path to mint — the test sets it.
    """
    system = system_or_factory() if callable(system_or_factory) else system_or_factory
    auto = set(auto_days)

    schedule = market_days(days, start=start)
    _ensure_review_live(system, operator=operator, clock=schedule[0].clock())

    report = ReplayReport()
    for day in schedule:
        report.days.append(
            replay_day(system, day, operator=operator, orders=orders_per_day,
                       arm_auto=day.index in auto)
        )

    report.dispositioned_event_ids = _verified_dispositions(system.st)
    report.persisted_event_ids = _all_persisted_event_ids(system.st)
    v = system.st.audit.verify_chain()
    report.audit_ok = v.ok
    report.audit_head_seq = v.head_seq
    return report
