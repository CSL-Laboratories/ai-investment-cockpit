"""RW-17 — THE CLAIM, EXECUTED THROUGH THE COMPOSITION ROOT.

*A congressional disclosure becomes a SUBMITTED paper order with ZERO human calls.*

Three files already test the pieces: ``tests/copytrade/test_autoexec.py`` drives the executor,
``tests/copytrade/test_notify.py`` drives the renderer, ``tests/workflows/test_copytrade_orders.py``
drives the cycle. Each one builds its own collaborators, and that is exactly why none of them
could see the defect this file exists to prevent: the three builds were joined by ``main.py``
through interfaces none of them shared, and the joined lane **did not work at all**.

  * ``main.build_autoexec_notify`` searched ``copytrade.notify`` for a factory that did not
    exist, so ``build_paper_autoexec`` returned ABSENT on every real deployment. Zero-touch
    execution was unreachable in production while every unit test was green.
  * ``main.build_paper_autoexec`` never wired ``risk_recheck_factory``, so even a composed lane
    would have defaulted to ``fail_closed_risk_recheck`` and refused every ticket at gateway
    gate (d) — arming, notifying, counting down and trading nothing.
  * nothing carried the FILING facts onto the ticket, so the queued message could not render,
    delivery could never be confirmed, and no ticket could ever fire.

So this test refuses to build its own lane. It uses ``main.build_paper_autoexec``,
``main.build_copy_cycle_service`` and ``main.build_autoexec_resolve_service`` — the functions
``compose()`` calls — and asserts on the broker double and the ``Order`` row. If the seams drift
apart again, this fails; a unit test will not.

ZERO HUMAN CALLS IS ENFORCED, NOT CLAIMED. ``bridge.approval.record_decision`` and
``PaperExecutionService.decide`` are both replaced with landmines for the whole run.

NOTHING HERE MAKES LIVE CAPITAL REACHABLE. The ladder is ``paper``, the gateway is
``execution_scope="paper"``, and the last test in the file moves the ladder mid-window and
proves the ticket is VOIDED rather than followed up the rungs.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import main
from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionService
from config.settings import Mode, Settings
from copytrade.models import TX_PURCHASE, AmountRange, Disclosure
from core.clock import FrozenClock
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import OrchestratorState
from schemas.models.congress_disclosure import persist_disclosures
from schemas.models.order import Order
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from storage.engine import create_all, make_engine, make_session_factory

OPERATOR = "123456789012345678"                  # a well-formed Discord snowflake
NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
FILER = "pelosi-nancy"
SYMBOL = "NVDA"
KEY = b"rw17-e2e-key"


# =========================================================================================
# harness — the real composition seams, hermetic collaborators
# =========================================================================================
@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Per-test HALT sentinel and operator allowlist. A HALT set by another test must never
    decide this one, and the lane must never touch the machine-wide sentinel."""
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)


@pytest.fixture(autouse=True)
def _no_humans(monkeypatch):
    """THE HARNESS ENFORCES "no human was involved"; the test does not merely assert it.

    Both wired human gates become landmines for the duration: the approval writer the Discord
    tap calls, and the paper-execution service's ``decide``. If the lane ever reaches a trade by
    routing through the operator's path, this run fails with a message saying so.
    """
    def _explode(*_a, **_k):                            # pragma: no cover - must never run
        raise AssertionError("the zero-touch lane must not call a human approval path")

    monkeypatch.setattr("bridge.approval.record_decision", _explode)
    monkeypatch.setattr(PaperExecutionService, "decide", _explode)


class Mono:
    """A hand-cranked monotonic clock. The cancel window is measured on ``time.monotonic``
    (SEC-23: a wall clock a caller can move is not a window), and the settings floor keeps the
    real window at >= 60s, so the window is crossed by cranking rather than by waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Surface:
    """The abstract ``deliver(channel, text) -> bool`` seam — ``bridge/deliver.py``'s contract,
    with no Discord anywhere in it (CLAUDE.md: the operator surface is abstracted)."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.posts: list[tuple[str, str]] = []

    def __call__(self, channel: str, text) -> bool:
        self.posts.append((channel, str(text)))
        return self.ok

    def text_for(self, needle: str) -> str:
        return next(t for _c, t in self.posts if needle in t)


def _settings(**over) -> Settings:
    """A legitimately-armed lane.

    ``congress_max_notional_per_trade`` is lowered from its 500 default so a full-size copy
    lands under the 250 per-symbol daily rail. That is a REAL configuration, not a loosened
    control: every budget rail keeps its default value, and the per-symbol rail is separately
    proved to bite in ``tests/copytrade/test_budget.py``.
    """
    kw = dict(
        congress_enabled=True,
        congress_user_agent="Cockpit ops@example.com",
        congress_follow=FILER,
        congress_autoexec=True,
        congress_max_notional_per_trade="200",
        operator_ids_config=OPERATOR,
        _env_file=None,
    )
    kw.update(over)
    return Settings(**kw)


@pytest.fixture
def clock():
    return FrozenClock(NOW)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw17-e2e.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(make_session_factory(engine), hmac_key=KEY,
                                    settings=_settings(), clock=clock)
    ModeLadder(state).set_mode(Mode.paper, actor="op")
    # SEC-15: the RiskContext is built from the latest reconciled snapshot. Absent, it is zeros
    # and the risk gate rejects conservatively — no order, and nothing for this file to prove.
    snapshot = PortfolioSnapshot(
        id="snap-1", account="paper", cash=Decimal("100000"),
        buying_power=Decimal("200000"), exposure=None,
        holdings=[{"symbol": "AAPL", "market_value": "5000"}], open_orders=[],
        ts=NOW, created_at=NOW,
    )
    state.audit.record_state_change(snapshot, "portfolio_snapshot", actor="test")
    return state


def disclose(st, clock, **over) -> Disclosure:
    """Store one PTR line, exactly as ``ingest.congress`` leaves it behind."""
    kw = dict(
        chamber="house", doc_id="D1", line_no=0, filer_slug=FILER,
        filer_display="Pelosi, Nancy", tx_date=date(2026, 6, 20), filed_date=date(2026, 7, 1),
        tx_type=TX_PURCHASE, asset_type="equity",
        amount=AmountRange(Decimal("1001"), Decimal("15000")), symbol=SYMBOL,
        asset_description="NVIDIA Corp common stock",
        raw_ref="parquet://2026-07-01/congress-D1#0", content_hash="c0ffee00",
    )
    kw.update(over)
    row = Disclosure(**kw)
    persist_disclosures(st.session_factory, [row], clock=clock.now)
    return row


def compose_lane(st, clock, *, surface=None, mono=None):
    """The lane as ``compose()`` builds it: the paper transport behind the guarded gateway, and
    the RW-17 services hung off it. Nothing is hand-assembled — that is the point of the file.
    """
    surface = surface if surface is not None else Surface()
    client = FakeAlpaca(cash="100000", buying_power="200000")
    broker = AlpacaPaperBroker(client=client)
    registry = TokenRegistry()
    gateway = OrderGateway(broker, st=st, token_registry=registry, execution_scope="paper")
    paper = PaperExecutionService(
        st, broker=broker, gateway=gateway, token_registry=registry,
        quote_fn=lambda _symbol: {"price": "100", "age_s": 1.0, "spread_pct": "0.01"},
        watchlist_fn=lambda: frozenset(),               # empty: only the COPY UNIVERSE admits it
    )

    wiring = main.build_paper_autoexec(st, paper=paper, deliver=surface, clock=clock)
    assert wiring.enabled, f"the lane must compose from the real modules: {wiring.reason}"
    executor = wiring.executor
    # The ONE injected seam. ``build_paper_autoexec`` does not offer a monotonic override (a
    # production composition root has no business choosing one), and the settings floor holds
    # the real window at >= 60 seconds, so the alternative is a sleeping test.
    executor._monotonic = mono if mono is not None else Mono()

    return {
        "surface": surface, "client": client, "gateway": gateway, "paper": paper,
        "wiring": wiring, "executor": executor,
        "cycle": main.build_copy_cycle_service(st, quote_fn=paper.quote, autoexec=executor),
        "resolve": main.build_autoexec_resolve_service(executor),
    }


def order_rows(st) -> list[Order]:
    with st.session_factory() as session:
        rows = list(session.query(Order).all())
        for row in rows:
            session.expunge(row)
        return rows


# =========================================================================================
# 1. THE CLAIM
# =========================================================================================
def test_a_disclosure_reaches_a_SUBMITTED_paper_order_with_no_human_call(st, clock):
    """Disclosure -> cycle -> queued + notified -> window elapses -> SUBMITTED at the broker."""
    disclose(st, clock)
    mono = Mono()
    lane = compose_lane(st, clock, mono=mono)

    cycle = lane["cycle"](st, clock)
    assert cycle["approved"] == 1, cycle
    assert cycle["queued_for_autoexec"] == 1, cycle
    assert cycle["queue_failures"] == {}
    # Nothing has traded yet: the ticket is queued behind the operator's cancel window.
    assert lane["client"].submitted == []
    assert [r.state for r in order_rows(st)] == ["proposed"]

    mono.advance(Settings().congress_autoexec_window_seconds + 1)
    resolutions = lane["resolve"](st, clock)

    assert len(resolutions) == 1
    outcome = resolutions[0]
    assert outcome.executed is True, f"refused by {outcome.reason}: {outcome.detail}"
    assert outcome.reason == "executed"
    assert outcome.broker_order_id and outcome.broker_status == "accepted"

    # THE BROKER DOUBLE SAW THE ORDER, and the stored row says submitted.
    assert len(lane["client"].submitted) == 1
    submitted = lane["client"].submitted[0]
    assert submitted["symbol"] == SYMBOL and submitted["side"] == "buy"
    rows = order_rows(st)
    assert [r.state for r in rows] == ["submitted"]
    assert submitted["client_order_id"] == f"cockpit-{rows[0].id}"

    # The approval it submitted under is a REAL persisted row minted by the ROBOT, and the
    # signed chain still verifies end to end.
    assert outcome.approval_id
    assert st.audit.verify_chain().ok


def test_the_operator_is_told_everything_the_queued_message_must_carry(st, clock):
    """The notification IS the interface now, so its contents are a contract, not a courtesy."""
    disclose(st, clock)
    surface = Surface()
    mono = Mono()
    lane = compose_lane(st, clock, surface=surface, mono=mono)
    lane["cycle"](st, clock)

    queued = surface.text_for("QUEUED")
    for fact in ("NVDA", "$200.00", "Pelosi, Nancy", "2026-06-20", "2026-07-01",
                 "11 days", "$1,001 - $15,000", "/cancel ", "PAPER"):
        assert fact in queued, f"the queued message must name {fact!r}:\n{queued}"
    # The risk block comes from the stored RiskDecision ROW and from nowhere else (SEC-17).
    assert "verdict: approved" in queued

    mono.advance(Settings().congress_autoexec_window_seconds + 1)
    lane["resolve"](st, clock)
    assert "SUBMITTED" in surface.text_for("SUBMITTED")


def test_a_lane_whose_surface_is_DOWN_notifies_nobody_and_trades_NOTHING(st, clock):
    """SEC-23: silence is not consent. An unconfirmed queued notification blocks the trade for
    as long as the surface stays down — the ticket keeps retrying rather than firing blind."""
    disclose(st, clock)
    mono = Mono()
    lane = compose_lane(st, clock, surface=Surface(ok=False), mono=mono)
    lane["cycle"](st, clock)

    mono.advance(Settings().congress_autoexec_window_seconds + 1)
    resolutions = lane["resolve"](st, clock)

    assert [r.reason for r in resolutions] == ["undelivered"]
    assert lane["client"].submitted == []
    assert [r.state for r in order_rows(st)] == ["proposed"]


# =========================================================================================
# 2. the operator's ONLY lever, and the rung that is never climbed
# =========================================================================================
def test_one_cancel_inside_the_window_stops_the_trade(st, clock):
    """A VETO, not an approval: the operator acts to STOP a trade, never to permit one."""
    disclose(st, clock)
    mono = Mono()
    lane = compose_lane(st, clock, mono=mono)
    lane["cycle"](st, clock)

    proposal_id = lane["executor"].pending()[0].proposal_id
    assert lane["executor"].cancel(proposal_id, actor_user_id=OPERATOR).cancelled is True

    mono.advance(Settings().congress_autoexec_window_seconds + 1)
    lane["resolve"](st, clock)

    assert lane["client"].submitted == []
    assert [r.state for r in order_rows(st)] == ["proposed"]


def test_a_ticket_queued_in_paper_is_VOID_after_the_ladder_moves(st, clock):
    """PAPER ONLY, BY CONSTRUCTION (RW-17 hard rule 3). A mode change mid-window voids the
    ticket; it is NOT re-queued at the new rung, because the rung is what decides whether a
    robot may adjudicate at all. This is the test that proves nothing here reaches live."""
    disclose(st, clock)
    mono = Mono()
    lane = compose_lane(st, clock, mono=mono)
    lane["cycle"](st, clock)

    ModeLadder(st).set_mode(Mode.review_live, actor="op")
    mono.advance(Settings().congress_autoexec_window_seconds + 1)
    resolutions = lane["resolve"](st, clock)

    assert [r.reason for r in resolutions] == ["mode_not_paper"]
    assert lane["client"].submitted == []
    assert [r.state for r in order_rows(st)] == ["proposed"]
