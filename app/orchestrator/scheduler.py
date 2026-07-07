"""WP5 — APScheduler wiring for the four recurring jobs (draft-2 §topology).

Each job is factored into a plain injected callable ``(state, clock)`` so it is testable
WITHOUT real time or a running scheduler: the guard logic (market-open check, daily disarm)
is exercised against a ``FrozenClock`` and an in-memory DB. The actual WP8 workflows
(premarket brew, intraday triage loop, EOD review) are injected services; the default is a
``NotImplementedError``-raising stub so this shell schedules and guards deterministically
without pulling in the not-yet-built pipeline.

``run_daily_disarm`` is REAL now: it disarms auto modes to ``review_live`` at close through
the mode ladder (SEC-23 — the dangerous end of the ladder re-arms each morning by hand).

``build_scheduler`` registers the four cron jobs and returns the scheduler WITHOUT starting
real time. APScheduler is imported lazily so this module (and the job callables) import
cleanly even where APScheduler is not installed.
"""
from __future__ import annotations

from typing import Callable

from .market_calendar import EASTERN, is_market_open
from .mode_ladder import ModeLadder

# Cron schedule (US/Eastern). Intraday additionally self-guards on is_market_open.
PREMARKET_HOUR, PREMARKET_MINUTE = 8, 30
INTRADAY_MINUTES = "*/15"
EOD_HOUR, EOD_MINUTE = 16, 15
DISARM_HOUR, DISARM_MINUTE = 16, 0
ANCHOR_MINUTES = "*/30"                   # SEC-11 external-witness head-anchor cadence


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


def build_scheduler(state, clock=None, *, services: dict | None = None):
    """Register the four cron jobs and return the (UN-started) scheduler. APScheduler is
    imported here so the job callables above stay usable without APScheduler installed."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    clock = clock or state.clock
    services = services or {}
    sched = BackgroundScheduler(timezone=EASTERN)

    sched.add_job(
        lambda: run_premarket_brew(state, clock, service=services.get("brew")),
        CronTrigger(hour=PREMARKET_HOUR, minute=PREMARKET_MINUTE,
                    day_of_week="mon-fri", timezone=EASTERN),
        id="premarket_brew", replace_existing=True,
    )
    sched.add_job(
        lambda: run_intraday_loop(state, clock, service=services.get("intraday")),
        CronTrigger(minute=INTRADAY_MINUTES, day_of_week="mon-fri", timezone=EASTERN),
        id="intraday_loop", replace_existing=True,
    )
    sched.add_job(
        lambda: run_eod_review(state, clock, service=services.get("eod")),
        CronTrigger(hour=EOD_HOUR, minute=EOD_MINUTE,
                    day_of_week="mon-fri", timezone=EASTERN),
        id="eod_review", replace_existing=True,
    )
    sched.add_job(
        lambda: run_daily_disarm(state, clock),
        CronTrigger(hour=DISARM_HOUR, minute=DISARM_MINUTE,
                    day_of_week="mon-fri", timezone=EASTERN),
        id="daily_disarm", replace_existing=True,
    )
    # SEC-11 external-witness cadence: anchor the verified head every 30 min on weekdays so a
    # tail-truncation/replay is caught by the gate + mode oracle. No-op when no store is wired.
    sched.add_job(
        lambda: run_anchor_head(state, clock),
        CronTrigger(minute=ANCHOR_MINUTES, day_of_week="mon-fri", timezone=EASTERN),
        id="anchor_head", replace_existing=True,
    )
    return sched
