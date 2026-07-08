"""WP9 — the SYSTEM TEST HARNESS substrate (keystone; Owner A).

Implements docs/build-scaffold-ultracode.md WP9 + docs/ultracode-build-playbook.md §3 (G9
gate) / §5 / script 4d. WP9 does NOT build a new subsystem — it WIRES the full cockpit
together end-to-end and TESTS it. This module is the DETERMINISTIC, HERMETIC substrate the
six G9 test files stand on: it mirrors ``main.compose`` (engine -> ``OrchestratorState`` ->
``build_services`` -> gateway/ladder/breaker) but with injected fakes (a credential-free
``FakePaperBroker``, ``DeliveryRecorder``, ``RoutedTransport``/``FakeTransport`` scripted
agent calls, a ``FrozenClock``, a tmp SQLite file, the session-scoped in-memory keyring, and
an isolated ``COCKPIT_HALT_PATH``). No wall-clock, no network, no real subprocess.

REUSE (project rule — do not reinvent): all agent-call scripting, envelopes, and the recorded
market day flow through the PROVEN ``tests/workflows/fixtures_market_day`` (real WP3 ingest +
``escape_untrusted``); the fully-approved submittable order scenario mirrors the PROVEN
``tests/brokers/conftest.build_scenario`` (it wires the signed ``EVENT_ADJUDICATION`` the
gateway's gate (e) anchors trust in).

Every state-changing path a test drives through this harness must leave
``st.audit.verify_chain().ok`` True — the substrate never weakens a gate to be green.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode, Settings
from core.clock import FrozenClock
from core.ids import new_id
from ingest.normalize import content_hash, to_market_event
from orchestrator.breakers import BreakerEngine
from orchestrator.market_calendar import is_market_open
from orchestrator.mode_ladder import ModeLadder
from orchestrator.scheduler import (
    run_anchor_head,
    run_daily_disarm,
    run_eod_review,
    run_intraday_loop,
    run_premarket_brew,
)
from orchestrator.state import EVENT_ADJUDICATION, OrchestratorState
from schemas.models.approval_record import ApprovalRecord
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory
from workflows.events import persist_events
from workflows.services import build_services, eod_review

from bridge.anchor import InMemoryAnchorStore

from tests.workflows.fixtures_market_day import (  # noqa: F401 (re-exported for authors)
    HOLDINGS,
    NOISE_MARKER,
    WATCHLIST,
    DeliveryRecorder,
    FakeTransport,
    RoutedTransport,
    brief_body,
    envelope,
    grounding_body,
    make_filing_event,
    market_day_events,
    proposal_body,
    scored_item,
    triage_body,
)

# The single deterministic clock instant: a weekday NYSE session (2026-07-07 = Tue,
# 14:00Z = 10:00 ET). is_market_open() is True here, so the intraday loop never SKIPs.
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)

# Re-export the EXACT secrets-in-logs pattern set the invariant gate uses (do not duplicate
# the regexes; the G9 "no secret in logs" test reuses this identical list).
from tests.test_invariants import _SECRET_PATTERNS as SECRET_PATTERNS  # noqa: E402


def secret_patterns() -> list:
    """Accessor for the module-private ``_SECRET_PATTERNS`` list from the invariant gate."""
    return list(SECRET_PATTERNS)


# ---------------------------------------------------------------------------------------
# credential-free, no-network paper broker
# ---------------------------------------------------------------------------------------
class FakePaperBroker:
    """The 'no broker creds' broker for dry-run + paper replay + order-path tests. It opens no
    socket and holds no credentials. ``submit_order`` records each call and returns a filled
    paper ack keyed off the canonical resolved-order tuple (qty/limit_price)."""

    def __init__(self) -> None:
        self.submits: list[dict] = []
        self._n = 0

    def submit_order(self, canonical: dict) -> dict:
        self._n += 1
        self.submits.append(dict(canonical))
        qty = canonical.get("qty")
        price = canonical.get("limit_price")
        return {
            "broker_order_id": f"paper-{self._n}",
            "status": "accepted",
            "filled_qty": qty if qty is not None else 0,
            "avg_fill_price": price if price is not None else None,
        }


def approved_risk_recheck(_positions):
    """Deterministic TOCTOU risk re-run that keeps an order approved (mirrors
    tests/brokers/conftest.approved_risk_recheck; re-defined here because tests/brokers is not
    an importable package)."""
    return {"decision": "approved", "failed_checks": []}


# ---------------------------------------------------------------------------------------
# the composed system-under-test
# ---------------------------------------------------------------------------------------
@dataclass
class SystemUnderTest:
    """The fully-composed cockpit, hermetic. Mirrors ``main.compose`` with injected fakes."""

    st: OrchestratorState
    services: dict
    gateway: OrderGateway
    ladder: ModeLadder
    breaker: BreakerEngine
    token_registry: TokenRegistry
    broker: FakePaperBroker
    deliver: DeliveryRecorder
    parquet_root: Path
    clock: FrozenClock
    hmac_key: bytes
    anchor_store: object | None
    holdings_fn: object
    watchlist_fn: object
    quote_fn: object | None
    settings: Settings

    # ---- convenience drives (each rebuilds the service closure over the passed transport so a
    # test scripts the EXACT agent calls for that tick — faithful to workflows/services.py) ----
    def _services_over(self, transport, deliver=None) -> dict:
        return build_services(
            self.st,
            transport=transport,
            deliver=deliver if deliver is not None else self.deliver,
            parquet_root=self.parquet_root,
            quote_fn=self.quote_fn,
            holdings_fn=self.holdings_fn,
            watchlist_fn=self.watchlist_fn,
        )

    def run_brew(self, transport, *, clock=None, deliver=None) -> dict:
        """Premarket morning brew over ``transport`` (via ``run_premarket_brew``)."""
        svc = self._services_over(transport, deliver)["brew"]
        return run_premarket_brew(self.st, clock or self.clock, service=svc)

    def run_intraday(self, transport, *, clock=None, deliver=None) -> dict:
        """One intraday triage tick over ``transport`` (via ``run_intraday_loop``; SKIPs if the
        clock is off-hours — the harness clock is a market-open instant, so it runs)."""
        svc = self._services_over(transport, deliver)["intraday"]
        return run_intraday_loop(self.st, clock or self.clock, service=svc)

    def run_eod(self, *, clock=None, drawdown_fn=None) -> dict:
        """EOD review (via ``run_eod_review``). ``drawdown_fn`` injects the realized-drawdown
        mark the performance gate reads (SEC-20)."""
        def _svc(st, clk):
            return eod_review(st, clk, drawdown_fn=drawdown_fn)

        return run_eod_review(self.st, clock or self.clock, service=_svc)

    def disarm(self, *, clock=None):
        """Daily disarm of auto modes -> review_live (via ``run_daily_disarm``)."""
        return run_daily_disarm(self.st, clock or self.clock)

    def anchor(self, *, clock=None):
        """Publish the verified audit head to the external witness (via ``run_anchor_head``)."""
        return run_anchor_head(self.st, clock or self.clock)


def _walk_ladder(ladder: ModeLadder, mode: Mode) -> None:
    """Walk the ladder up to ``mode`` the way tests/brokers/conftest.build_scenario does:
    read_only -> paper -> review_live (auto targets stop at review_live; a test arms auto
    explicitly via ``ladder.arm_auto_mode``)."""
    if mode not in (Mode.paper, Mode.review_live, Mode.auto_veto, Mode.auto_full):
        return
    cur = ladder.current()
    if cur not in (Mode.paper, Mode.review_live, Mode.auto_veto, Mode.auto_full):
        ladder.set_mode(Mode.paper, actor="op")
    if mode != Mode.paper and ladder.current() == Mode.paper:
        ladder.set_mode(Mode.review_live, actor="op")


def build_system(
    *,
    tmp_path,
    clock: FrozenClock | None = None,
    transport=None,
    deliver: DeliveryRecorder | None = None,
    broker: FakePaperBroker | None = None,
    mode: Mode | None = None,
    settings: Settings | None = None,
    with_anchor: bool = True,
    holdings_fn=None,
    watchlist_fn=None,
    quote_fn=None,
) -> SystemUnderTest:
    """Compose the full cockpit hermetically: tmp SQLite engine + ``create_all`` + session
    factory, ``OrchestratorState.build`` with a RANDOM test HMAC key (never a literal that
    would trip the secrets gate), ``build_services`` over the seams, the SEC-11 in-memory
    anchor store when ``with_anchor``, and the ladder walked up to ``mode`` if given.
    ``holdings_fn`` / ``watchlist_fn`` default to the fixtures' HOLDINGS / WATCHLIST."""
    tmp_path = Path(tmp_path)
    clock = clock if clock is not None else FrozenClock(OPEN_UTC)
    deliver = deliver if deliver is not None else DeliveryRecorder()
    broker = broker if broker is not None else FakePaperBroker()
    settings = settings if settings is not None else Settings()
    holdings_fn = holdings_fn if holdings_fn is not None else (lambda: HOLDINGS)
    watchlist_fn = watchlist_fn if watchlist_fn is not None else (lambda: WATCHLIST)

    parquet_root = tmp_path / "raw"
    parquet_root.mkdir(parents=True, exist_ok=True)

    engine = make_engine(f"sqlite:///{(tmp_path / 'system.db').as_posix()}")
    create_all(engine)
    session_factory = make_session_factory(engine)

    hmac_key = os.urandom(32)   # random per build; verify/append use the same key -> chain OK
    st = OrchestratorState.build(session_factory, hmac_key=hmac_key, settings=settings,
                                 clock=clock)
    anchor_store = None
    if with_anchor:
        anchor_store = InMemoryAnchorStore()
        st.anchor_store = anchor_store

    token_registry = TokenRegistry()
    gateway = OrderGateway(broker, token_registry=token_registry)
    ladder = ModeLadder(st)
    breaker = BreakerEngine(st)

    if mode is not None:
        _walk_ladder(ladder, mode)

    services = build_services(
        st, transport=transport, deliver=deliver, parquet_root=parquet_root,
        quote_fn=quote_fn, holdings_fn=holdings_fn, watchlist_fn=watchlist_fn,
    )

    return SystemUnderTest(
        st=st, services=services, gateway=gateway, ladder=ladder, breaker=breaker,
        token_registry=token_registry, broker=broker, deliver=deliver,
        parquet_root=parquet_root, clock=clock, hmac_key=hmac_key, anchor_store=anchor_store,
        holdings_fn=holdings_fn, watchlist_fn=watchlist_fn, quote_fn=quote_fn,
        settings=settings,
    )


# ---------------------------------------------------------------------------------------
# persistence helper (mirrors tests/workflows/test_g8_acceptance._persist_day)
# ---------------------------------------------------------------------------------------
def persist_day(system: SystemUnderTest, event_dicts: list[dict], *, clock=None):
    """``persist_events`` the normalized ``event_dicts`` and map each ``article_id`` -> its
    stored ``MarketEvent`` row via ``content_hash`` (the SEC-13 mapping rule). Returns
    ``(result, rows)`` where ``rows[article_id] -> MarketEvent``."""
    clock = clock or system.clock
    result = persist_events(system.st, event_dicts, parquet_root=system.parquet_root,
                            clock=clock)
    by_hash = {e.get("content_hash") or content_hash(e): e["article_id"] for e in event_dicts}
    rows: dict = {}
    for row in result.stored:
        aid = by_hash.get(row.content_hash)
        if aid is not None:
            rows[aid] = row
    return result, rows


# ---------------------------------------------------------------------------------------
# multi-day fixtures (small, deterministic; filing-corroborated urgents like g8-3/g8-4)
# ---------------------------------------------------------------------------------------
@dataclass
class DayFixture:
    """One recorded trading day: normalized event dicts + a scripted-transport factory. Each
    day carries two filing-corroborated urgents (AAPL, MSFT — held; SEC-21 deterministic
    corroboration) plus one benign RNDX noise item the deterministic pre-filter drops before
    any model call, so the RoutedTransport queues are predictable."""

    index: int
    now: datetime
    events: list[dict]
    holdings: frozenset
    watchlist: frozenset
    # article_ids of the two escalating filings, in rank order (score desc: AAPL then MSFT).
    escalating: tuple[tuple[str, str, int], ...]  # (article_id, symbol, score)

    def clock(self) -> FrozenClock:
        return FrozenClock(self.now)

    def persist(self, system: SystemUnderTest):
        """persist this day's events into ``system`` using this day's clock (so ingested_at is
        this trading day). Returns ``(result, rows)`` like ``persist_day``."""
        return persist_day(system, self.events, clock=self.clock())

    def triage_scored(self, rows: dict) -> list[dict]:
        """The TriageResult ``scored`` list over the persisted rows (article_id echoed as the
        packet ref = ``MarketEvent.raw_ref``; the noise row is pre-filtered, never scored)."""
        return [scored_item(rows[aid].raw_ref, sym, "urgent", score)
                for aid, sym, score in self.escalating]

    def scripted_transport(self, rows: dict, *, escalate=None, proposal_ids=None):
        """A ``RoutedTransport`` with the right haiku/opus queues for this day's intraday tick:
        haiku = [batched triage] + one grounding per escalating candidate (rank order); opus =
        one proposal per escalating candidate. ``escalate`` narrows to a subset of article_ids;
        ``proposal_ids`` overrides the per-candidate proposal id."""
        proposal_ids = proposal_ids or {}
        chosen = [(aid, sym, score) for (aid, sym, score) in self.escalating
                  if escalate is None or aid in escalate]
        scored = [scored_item(rows[aid].raw_ref, sym, "urgent", score)
                  for aid, sym, score in chosen]
        haiku = [envelope(triage_body(scored, now=self.now, batch_id=f"tb-d{self.index}"))]
        opus = []
        for aid, sym, _score in chosen:
            row = rows[aid]
            pid = proposal_ids.get(aid) or f"prop-d{self.index}-{sym.lower()}"
            haiku.append(envelope(grounding_body(pid, [row.raw_ref])))
            opus.append(envelope(proposal_body(pid, sym, [row.raw_ref])))
        return RoutedTransport(haiku=haiku, opus=opus)


def _next_market_open(dt: datetime) -> datetime:
    """The first market-open instant at or after ``dt`` (skips weekends + NYSE holidays)."""
    cur = dt
    for _ in range(400):
        if is_market_open(cur):
            return cur
        cur = cur + timedelta(days=1)
    raise RuntimeError("no market-open instant found within a year")


def _day_events(now: datetime, i: int) -> list[dict]:
    return [
        make_filing_event("AAPL", f"d{i}-f-aapl", now, minutes_ago=5),
        make_filing_event("MSFT", f"d{i}-f-msft", now, minutes_ago=6),
        to_market_event(
            source="finnhub", article_id=f"d{i}-noise", symbols=["RNDX"],
            headline=f"RNDX {NOISE_MARKER} day {i}", summary="noise",
            ts=(now - timedelta(minutes=3)).isoformat()),
    ]


def market_days(n: int, *, start: datetime = OPEN_UTC, holdings=HOLDINGS,
                watchlist=WATCHLIST) -> list[DayFixture]:
    """``n`` consecutive TRADING days (weekends/holidays skipped), each a small deterministic
    ``DayFixture``. Day 0 starts at the first market-open instant at/after ``start``."""
    out: list[DayFixture] = []
    cur = _next_market_open(start)
    for i in range(n):
        out.append(DayFixture(
            index=i, now=cur, events=_day_events(cur, i),
            holdings=holdings, watchlist=watchlist,
            escalating=((f"d{i}-f-aapl", "AAPL", 95), (f"d{i}-f-msft", "MSFT", 90)),
        ))
        cur = _next_market_open(cur + timedelta(days=1))
    return out


# ---------------------------------------------------------------------------------------
# fully-approved, submittable order scenario on the COMPOSED system (adapted from
# tests/brokers/conftest.build_scenario; drives the REAL system.gateway)
# ---------------------------------------------------------------------------------------
@dataclass
class OrderScenario:
    """A fully-valid submit scenario on the composed ``system`` (its st + gateway). It persists
    the RiskDecision, the signed EVENT_ADJUDICATION binding, and the Order, and mints the submit
    token — everything the gateway's gate (e) anchors trust in. Tests mutate ONE field via
    ``submit_kwargs(**over)`` / ``review_kwargs(**over)`` to prove each gate independently."""

    system: SystemUnderTest
    order: object
    approval: ApprovalRecord
    risk_decision: RiskDecision
    tuple_hash: str
    nonce: str

    def submit_kwargs(self, **over) -> dict:
        kw = dict(
            approval=self.approval, risk_decision=self.risk_decision,
            ladder=self.system.ladder, breaker_engine=self.system.breaker,
            risk_recheck=approved_risk_recheck, positions={},
            token_nonce=self.nonce, producing_runtime="claude", clock=self.system.clock,
        )
        kw.update(over)
        return kw

    def review_kwargs(self, **over) -> dict:
        kw = self.submit_kwargs(**over)
        kw.pop("token_nonce", None)
        return kw


def build_order_scenario(
    system: SystemUnderTest,
    *,
    mode: Mode = Mode.review_live,
    symbol: str = "AAPL",
    side: str = "buy",
    qty: str = "10",
    order_type: str = "limit",
    limit_price: str = "150",
    notional: str = "1500",
    proposal_id: str = "p1",
    risk_decision_state: str = "approved",
    consumed: bool = False,
    mint: bool = True,
    nonce: str = "n1",
    anchor: bool = True,
) -> OrderScenario:
    """Assemble a fully-valid submittable order on ``system``. Walks the ladder to ``mode``,
    persists RiskDecision + the signed covering EVENT_ADJUDICATION (full binding payload, as
    production bridge/approval.record_decision does) + the Order, and mints the submit token
    into ``system.gateway``'s registry. When ``anchor`` and an anchor store is wired, posts the
    verified head to the external witness LAST so the SEC-11 gateway check passes on the happy
    path (real operation anchors the head continuously; a rollback test passes ``anchor=False``
    or advances the head past the anchor before submitting)."""
    st = system.st
    clk = system.clock

    _walk_ladder(system.ladder, mode)

    rd = RiskDecision(
        id=new_id("rd"), proposal_id=proposal_id, decision=risk_decision_state, checks={},
        failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
        human_required=False, evaluated_at=clk.now(), created_at=clk.now(),
    )
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": risk_decision_state})

    canon = canonical_order_tuple(symbol=symbol, side=side, qty=qty, order_type=order_type,
                                  limit_price=limit_price, tif="day")
    h = order_tuple_hash(canon)

    appr = ApprovalRecord(
        id=new_id("appr"), proposal_id=proposal_id, risk_decision_id=rd.id,
        actor_user_id="lucas", user_decision="approve", surface="discord",
        order_tuple_hash=h, single_use_consumed=consumed,
        decided_at=clk.now(), created_at=clk.now(),
    )
    st.audit.record_state_change(
        appr, EVENT_ADJUDICATION, actor="lucas",
        payload={"decision": "approve", "proposal_id": proposal_id,
                 "risk_decision_id": rd.id, "order_tuple_hash": h},
    )

    order = create_order(
        st, proposal_id=proposal_id, risk_decision_id=rd.id, approval_id=appr.id,
        account="paper", symbol=symbol, side=side, qty=qty, order_type=order_type,
        limit_price=limit_price, tif="day", notional=notional, order_tuple_hash=h,
        actor="claude",
    )

    if mint:
        system.gateway.mint_submit_token(nonce=nonce, proposal_id=proposal_id,
                                         risk_decision_id=rd.id, order_tuple_hash=h)

    if anchor and system.anchor_store is not None:
        system.anchor()   # post the verified head to the SEC-11 witness (head >= anchor)

    return OrderScenario(system=system, order=order, approval=appr, risk_decision=rd,
                         tuple_hash=h, nonce=nonce)


# ---------------------------------------------------------------------------------------
# log capture (respects core.logging format)
# ---------------------------------------------------------------------------------------
@contextlib.contextmanager
def capture_logs(level: int = logging.INFO):
    """Attach a StringIO ``StreamHandler`` (core.logging format) to the root logger; yield a
    callable returning the captured text so far. Restores the prior handlers/level on exit."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(
        logging.Formatter(fmt="%(asctime)s %(levelname)s %(name)s :: %(message)s"))
    root = logging.getLogger()
    prev_handlers = root.handlers[:]
    prev_level = root.level
    root.addHandler(handler)
    if root.level > level or root.level == 0:
        root.setLevel(level)
    try:
        yield buf.getvalue
    finally:
        root.handlers[:] = prev_handlers
        root.setLevel(prev_level)
