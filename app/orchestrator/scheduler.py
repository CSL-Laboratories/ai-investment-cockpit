"""WP5 — APScheduler wiring for the four recurring jobs (draft-2 §topology).

Each job is factored into a plain injected callable ``(state, clock)`` so it is testable
WITHOUT real time or a running scheduler: the guard logic (market-open check, daily disarm)
is exercised against a ``FrozenClock`` and an in-memory DB. The actual WP8 workflows
(premarket brew, intraday triage loop, EOD review) are injected services; the default is a
``NotImplementedError``-raising stub so this shell schedules and guards deterministically
without pulling in the not-yet-built pipeline.

``run_daily_disarm`` is REAL now: it disarms auto modes to ``review_live`` at close through
the mode ladder (SEC-23 — the dangerous end of the ladder re-arms each morning by hand).

``build_scheduler`` registers the base cron jobs and returns the scheduler WITHOUT starting
real time. APScheduler is imported lazily so this module (and the job callables) import
cleanly even where APScheduler is not installed.

RW-01 adds the INGEST jobs. They are registered one-per-PROVISIONED-FEED off an injected
``IngestRunner`` (``orchestrator/ingest_runner.py``), so ``build_scheduler(state)`` with no
ingest registry still yields exactly the base job set. Unlike the workflow services — which are
always registered and dispatch to a raising stub — an unprovisioned feed gets NO job at all: a
feed the operator has not configured has nothing to poll, and a job that exists only to no-op
would make an unconfigured feed indistinguishable from a quiet one on the job list.

The Alpaca news WebSocket is deliberately NOT a cron job. Its consumer BLOCKS
(``ingest/alpaca_news.py`` runs its own reconnect loop), so it is driven by
``IngestRunner.start_streams()`` on a daemon thread from ``main.run()``, not by a trigger.
"""
from __future__ import annotations

from typing import Callable

from .breakers import BreakerEngine, BreakerSignals, BreakerThresholds
from .market_calendar import (
    EASTERN,
    CalendarCoverageError,
    is_market_open,
    require_calendar_coverage,
)
from .mode_ladder import ModeLadder

# Cron schedule (US/Eastern). Intraday additionally self-guards on is_market_open.
PREMARKET_HOUR, PREMARKET_MINUTE = 8, 30
INTRADAY_MINUTES = "*/15"
EOD_HOUR, EOD_MINUTE = 16, 15
DISARM_HOUR, DISARM_MINUTE = 16, 0
ANCHOR_MINUTES = "*/30"                   # SEC-11 external-witness head-anchor cadence
MARKET_QUOTE_MINUTES = "*/1"              # a roughly two-minute gap reaches the 120 s threshold
MARKET_BARS_HOUR, MARKET_BARS_MINUTE = 7, 30
PAPER_ORDER_POLL_MINUTES = "*/1"

# ---- RW-01 ingest cadences ------------------------------------------------------------
# Per-feed cron kwargs, keyed by the ``ingest_runner`` feed name. Each is sized to the feed's
# OWN publication rhythm, not to a single global tick:
#
#   finnhub    every minute, WEEKDAYS ONLY but ALL DAY. The free tier is 60 calls/min and the
#              poller carries its own TokenBucket, so a minute tick can never exceed the vendor
#              cap; news breaks pre-market and after the close, so the window is not clamped to
#              the session.
#   sec:edgar  every 5 minutes, all week. RW-11 prefers the in-process SEC pipeline, whose
#              SecFetcher enforces the declared UA, <10 req/s rate, retry and byte budgets;
#              the legacy local-brew fallback keeps this same cadence until RW-12 retirement.
#              Filings can land on weekends, so the schedule deliberately includes Sat/Sun.
#   calendar   twice daily on weekdays. A calendar is a re-published forward window, so polling
#              it often buys nothing; 06:00 ET seeds the premarket brew (which runs 08:30) and
#              17:00 ET picks up intraday revisions.
#   finra      once each weekday morning. Short interest publishes TWICE A MONTH and is ~2
#              weeks stale by the time it is readable (ingest/finra_short_interest.py) — a
#              daily poll is already generous, and the connector never derives a date from a
#              cadence rule, so a schedule change at FINRA is a no-op here.
INGEST_CRON = {
    "finnhub": {"minute": "*/1", "day_of_week": "mon-fri"},
    "sec:edgar": {"minute": "*/5"},
    "calendar": {"hour": "6,17", "minute": 0, "day_of_week": "mon-fri"},
    "finra": {"hour": 7, "minute": 15, "day_of_week": "mon-fri"},
}
# The cadence for a feed nobody listed above. Registering it on a conservative default beats
# silently NOT scheduling it (a feed that exists but never runs is the wedge this package is
# meant to remove).
INGEST_CRON_DEFAULT = {"minute": "*/15"}


def _not_implemented_service(name: str) -> Callable:
    def _svc(state, clock):
        raise NotImplementedError(f"{name} workflow is wired in WP8")

    return _svc


def run_premarket_brew(state, clock=None, *, service: Callable | None = None):
    """Morning brew. Dispatches to an injected WP8 brew service (default: not-wired stub)."""
    clock = clock or state.clock
    svc = service or _not_implemented_service("premarket_brew")
    return svc(state, clock)


def run_intraday_loop(state, clock=None, *, service: Callable | None = None):
    """Intraday triage loop — SKIPS when the market is closed (never runs off-hours), so a
    cron tick outside a trading session is a no-op regardless of the schedule granularity."""
    clock = clock or state.clock
    now = clock.now()
    try:
        require_calendar_coverage(now)
    except CalendarCoverageError:
        return {"skipped": "calendar_unknown", "at": now.isoformat()}
    if not is_market_open(now):
        return {"skipped": "market_closed", "at": now.isoformat()}
    svc = service or _not_implemented_service("intraday_loop")
    return svc(state, clock)


def run_eod_review(state, clock=None, *, service: Callable | None = None):
    """End-of-day review. Dispatches to an injected WP8 review service (default: stub)."""
    clock = clock or state.clock
    svc = service or _not_implemented_service("eod_review")
    return svc(state, clock)


def run_daily_disarm(state, clock=None, *, actor: str = "scheduler"):
    """Disarm auto modes at market close -> review_live (SEC-23). No-op outside auto modes.
    Deliberate friction: a one-tap re-arm is required next morning."""
    clock = clock or state.clock
    return ModeLadder(state).daily_disarm(clock=clock, actor=actor)


def run_anchor_head(state, clock=None, *, actor: str = "scheduler"):
    """SEC-11: publish the VERIFIED audit head to the external witness (``state.anchor_store``)
    so a later tail-truncation/replay is caught — the performance gate and the mode oracle
    compare the in-DB head against this anchor and fail closed for an auto unlock/read when the
    head falls below it. Frequent posting bounds the replay window (a blocking event that lands
    then gets truncated is caught once an anchor was posted past it).

    Fail-closed + no-op safe: skips when no anchor store is configured (auto is then unreachable
    anyway) and refuses to anchor an unverified chain (that would sign off on a tampered state).
    Kept dependency-light (no bridge import) so orchestrator does not depend upward on WP6."""
    store = getattr(state, "anchor_store", None)
    if store is None:
        return {"skipped": "no_anchor_store"}
    v = state.audit.verify_chain()
    if not v.ok:
        return {"skipped": "chain_unverified", "error": v.error}
    seq, head_hash = state.audit.head()
    if not head_hash or seq <= 0:
        return {"skipped": "no_head"}
    store.append(seq, head_hash)                       # monotonic; idempotent on the same head
    return {"anchored_seq": seq, "head_hash": head_hash}


def run_ingest_poll(state, clock=None, *, feed: str, runner=None):
    """RW-01 — one tick of ONE ingest feed. Read-only; no model, no order path.

    Deliberately does NOT guard on ``is_market_open``: filings, calendar revisions and news all
    land outside the session, and this is awareness (Engine 1), not execution. Cadence windows
    live in ``INGEST_CRON``; the market-hours question is answered there per feed, not here.

    Deliberately does NOT guard on HALT / ``watch_only`` / mode either. Those stop the ORDER
    path. Blinding the feeds on a killswitch would leave the operator least-informed exactly
    when they most need to see, and would make this feed's emptiness indistinguishable from a
    quiet day — the failure the whole ingest layer is built to avoid.

    Never raises for an ordinary feed fault (``IngestRunner.poll`` catches, counts and REPORTS
    it) so one sick vendor cannot take down the scheduler thread or the other feeds. A
    ``ClockContractError`` — a wiring bug — still propagates, by design.
    """
    clock = clock or state.clock
    if runner is None:
        return {"feed": feed, "skipped": "no_ingest_runner"}
    return runner.poll(feed)


def run_market_quote_poll(state, clock=None, *, market_data=None):
    """RW-14 market-data tick: poll regardless of event volume; enforce only when open."""
    clock = clock or state.clock
    if market_data is None:
        return {"skipped": "market_data_not_wired"}
    result = market_data.poll_quotes()
    now = clock.now()
    try:
        require_calendar_coverage(now)
    except (CalendarCoverageError, TypeError, ValueError):
        return {**result, "breaker": "calendar_unknown"}
    if not is_market_open(now):
        return {**result, "breaker": "market_closed"}

    engine = BreakerEngine(state)
    thresholds = BreakerThresholds(
        data_staleness_s=getattr(
            market_data, "max_age_s", BreakerThresholds().data_staleness_s
        )
    )
    age = market_data.max_quote_age_s(now=now)
    events = engine.evaluate_and_trip(
        BreakerSignals(max_data_age_s=age), thresholds, clock=clock, actor="market_data"
    )
    recovered = False
    if age <= thresholds.data_staleness_s and engine.is_watch_only("market_data"):
        previous = ModeLadder(state).current()
        engine.recover_from_watch_only(
            "market_data", previous, clock=clock, actor="market_data"
        )
        recovered = True
    return {**result, "max_data_age_s": age, "system_events": events,
            "recovered": recovered}


def run_market_bars_refresh(state, clock=None, *, market_data=None):
    """RW-14 daily ADV/volatility refresh; never grants order authority."""
    del state, clock
    if market_data is None:
        return {"skipped": "market_data_not_wired"}
    return market_data.refresh_daily_bars()


def run_paper_order_reconcile(state, clock=None, *, paper=None):
    """RW-15 paper-order tick: read broker state and consume it through the audited FSM."""
    del state, clock
    if paper is None:
        return {"skipped": "paper_not_wired"}
    return paper.reconcile_orders()


def run_paper_performance(state, clock=None, *, paper=None):
    """W-22 equity/round-trip sampling; unavailable evidence fails closed in the service."""
    del state, clock
    if paper is None:
        return {"skipped": "paper_not_wired"}
    return paper.sample_performance()


def build_scheduler(state, clock=None, *, services: dict | None = None, ingest=None,
                    market_data=None, paper=None):
    """Register the cron jobs and return the (UN-started) scheduler. APScheduler is imported
    here so the job callables above stay usable without APScheduler installed.

    ``ingest`` is the RW-01 ``IngestRunner``. When given, ONE ``ingest_<feed>`` job is
    registered per PROVISIONED poll feed on its ``INGEST_CRON`` cadence; when None (the WP5
    posture, and every pre-RW-01 test) the job set is exactly the base five. Stream feeds get
    no job — see the module docstring.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    clock = clock or state.clock
    services = services or {}
    sched = BackgroundScheduler(timezone=EASTERN)

    sched.add_job(
        lambda: run_premarket_brew(state, clock, service=services.get("brew")),
        CronTrigger(hour=PREMARKET_HOUR, minute=PREMARKET_MINUTE,
                    day_of_week="mon-fri", timezone=EASTERN),
        id="premarket_brew", name="premarket_brew", replace_existing=True,
    )
    sched.add_job(
        lambda: run_intraday_loop(state, clock, service=services.get("intraday")),
        CronTrigger(minute=INTRADAY_MINUTES, day_of_week="mon-fri", timezone=EASTERN),
        id="intraday_loop", name="intraday_loop", replace_existing=True,
    )
    sched.add_job(
        lambda: run_eod_review(state, clock, service=services.get("eod")),
        CronTrigger(hour=EOD_HOUR, minute=EOD_MINUTE,
                    day_of_week="mon-fri", timezone=EASTERN),
        id="eod_review", name="eod_review", replace_existing=True,
    )
    sched.add_job(
        lambda: run_daily_disarm(state, clock),
        CronTrigger(hour=DISARM_HOUR, minute=DISARM_MINUTE,
                    day_of_week="mon-fri", timezone=EASTERN),
        id="daily_disarm", name="daily_disarm", replace_existing=True,
    )
    # SEC-11 external-witness cadence: anchor the verified head every 30 min on weekdays so a
    # tail-truncation/replay is caught by the gate + mode oracle. No-op when no store is wired.
    sched.add_job(
        lambda: run_anchor_head(state, clock),
        CronTrigger(minute=ANCHOR_MINUTES, day_of_week="mon-fri", timezone=EASTERN),
        id="anchor_head", name="anchor_head", replace_existing=True,
    )
    # RW-01: one job per PROVISIONED ingest poll feed, each on its own cadence. This is the
    # wire that makes ingest/ actually run — before it, build_ingest_fetchers was called by
    # nothing and persist_events was reached only from tests.
    for feed in sorted(getattr(ingest, "polls", {}) or {}):
        cron = INGEST_CRON.get(feed, INGEST_CRON_DEFAULT)
        sched.add_job(
            # default-arg bind, NOT a closure over the loop variable: a bare `lambda:
            # run_ingest_poll(..., feed=feed, ...)` would capture the NAME, so every registered
            # job would poll whichever feed the loop happened to end on.
            lambda f=feed: run_ingest_poll(state, clock, feed=f, runner=ingest),
            CronTrigger(timezone=EASTERN, **cron),
            id=f"ingest_{feed}", name=f"ingest_{feed}", replace_existing=True,
            max_instances=1, coalesce=True,
        )
    if market_data is not None:
        sched.add_job(
            lambda: run_market_quote_poll(state, clock, market_data=market_data),
            CronTrigger(minute=MARKET_QUOTE_MINUTES, day_of_week="mon-fri", timezone=EASTERN),
            id="market_quotes", name="market_quotes", replace_existing=True,
            max_instances=1, coalesce=True,
        )
        sched.add_job(
            lambda: run_market_bars_refresh(state, clock, market_data=market_data),
            CronTrigger(hour=MARKET_BARS_HOUR, minute=MARKET_BARS_MINUTE,
                        day_of_week="mon-fri", timezone=EASTERN),
            id="market_daily_bars", name="market_daily_bars", replace_existing=True,
            max_instances=1, coalesce=True,
        )
    if paper is not None:
        sched.add_job(
            lambda: run_paper_order_reconcile(state, clock, paper=paper),
            CronTrigger(
                minute=PAPER_ORDER_POLL_MINUTES,
                day_of_week="mon-fri",
                timezone=EASTERN,
            ),
            id="paper_order_reconcile",
            name="paper_order_reconcile",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.add_job(
            lambda: run_paper_performance(state, clock, paper=paper),
            CronTrigger(
                minute=PAPER_ORDER_POLL_MINUTES,
                day_of_week="mon-fri",
                timezone=EASTERN,
            ),
            id="paper_performance",
            name="paper_performance",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    return sched
