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

RW-17 adds THE JOBS OF THE PAPER AUTO-EXECUTION LANE — the congressional copy CYCLE, the
``resolve_due`` tick that fires (or refuses) a queued trade at the end of its cancel window, and
the two REPORTERS the adversarial round found had no producer at all: the end-of-day digest and
the fill notice (``run_autoexec_digest`` / ``run_autoexec_fills``). Both reporters read the
SIGNED chain, so neither can under-report across a restart. RW-16 deliberately left the cycle
unscheduled and pinned that absence in
``tests/system/test_congress_wiring.py``; the operator has REVERSED that decision for the
paper lane ("i do not want to do any approvals ... i want it to make trades for me"), and the
pinning test now pins the new wiring instead of the old absence. Both jobs follow the ingest
posture rather than the workflow posture: an unprovisioned lane gets NO JOB AT ALL, so a
deployment that never opted in has exactly the base five and the RW-16 shape it always had.
"""
from __future__ import annotations

import logging
from typing import Callable

from .market_calendar import EASTERN, is_market_open
from .mode_ladder import ModeLadder

log = logging.getLogger("cockpit.scheduler")

# Cron schedule (US/Eastern). Intraday additionally self-guards on is_market_open.
PREMARKET_HOUR, PREMARKET_MINUTE = 8, 30
INTRADAY_MINUTES = "*/15"
EOD_HOUR, EOD_MINUTE = 16, 15
DISARM_HOUR, DISARM_MINUTE = 16, 0
ANCHOR_MINUTES = "*/30"                   # SEC-11 external-witness head-anchor cadence

# ---- RW-01 ingest cadences ------------------------------------------------------------
# Per-feed cron kwargs, keyed by the ``ingest_runner`` feed name. Each is sized to the feed's
# OWN publication rhythm, not to a single global tick:
#
#   finnhub    every minute, WEEKDAYS ONLY but ALL DAY. The free tier is 60 calls/min and the
#              poller carries its own TokenBucket, so a minute tick can never exceed the vendor
#              cap; news breaks pre-market and after the close, so the window is not clamped to
#              the session.
#   sec:edgar  every 5 minutes, all week. This reads the edgar-morning-brew SQLite file (no
#              network, no vendor quota — see ingest/edgar_brew.py "no TokenBucket"), and
#              filings land on weekends too, so there is nothing to be polite about and no
#              reason to skip Sat/Sun.
#   calendar   twice daily on weekdays. A calendar is a re-published forward window, so polling
#              it often buys nothing; 06:00 ET seeds the premarket brew (which runs 08:30) and
#              17:00 ET picks up intraday revisions.
#   finra      once each weekday morning. Short interest publishes TWICE A MONTH and is ~2
#              weeks stale by the time it is readable (ingest/finra_short_interest.py) — a
#              daily poll is already generous, and the connector never derives a date from a
#              cadence rule, so a schedule change at FINRA is a no-op here.
#   congress   hourly on weekdays (RW-16). PTRs are published in DAILY BATCHES on business
#              days, and the signal is already 30-45 days old by law (the STOCK Act deadline),
#              so minutes buy literally nothing while every extra tick is another request at
#              a .gov host we have no documented rate budget for. Hourly still catches a
#              filing the same session it posts. Weekday-only because the Clerk and the EFD
#              publish on business days — unlike sec:edgar above, which reads a LOCAL file and
#              so has no reason to skip a weekend. The :20 offset keeps this off the top of the
#              hour, where every other cron in this file already lands.
INGEST_CRON = {
    "finnhub": {"minute": "*/1", "day_of_week": "mon-fri"},
    "sec:edgar": {"minute": "*/5"},
    "calendar": {"hour": "6,17", "minute": 0, "day_of_week": "mon-fri"},
    "finra": {"hour": 7, "minute": 15, "day_of_week": "mon-fri"},
    "congress": {"minute": 20, "day_of_week": "mon-fri"},
}
# The cadence for a feed nobody listed above. Registering it on a conservative default beats
# silently NOT scheduling it (a feed that exists but never runs is the wedge this package is
# meant to remove).
INGEST_CRON_DEFAULT = {"minute": "*/15"}

# ---- RW-17 paper auto-execution cadences ----------------------------------------------
# THE COPY CYCLE — three times per REGULAR SESSION, on weekdays, at :40.
#
# Three things set this cadence, and none of them is "as often as possible":
#
#   1. THE SIGNAL HAS NO URGENCY. A PTR is 30-45 days old by statute (docs/congress-copytrade.md
#      §1). Nothing is gained by planning it at 09:31 rather than 12:40, and the copy path emits
#      a limit order at the gate quote precisely because there is nothing here worth paying for
#      immediacy over.
#   2. IT MUST RUN WHERE A QUOTE IS REAL. The cycle's risk gate has a HARD ``quote_sane`` check;
#      off-hours the quote is stale or absent, every intent is REJECTED, and each rejection
#      spends one of the filing's ``MAX_COPY_ATTEMPTS`` (3). A 24/7 tick would therefore burn a
#      filing's whole retry budget overnight, against a market that was closed. So the ticks sit
#      inside the regular session AND the job re-checks ``is_market_open`` (a cron cannot know
#      about a market holiday or an early close).
#   3. THE TICK COUNT IS THE RETRY BUDGET. Three ticks a day, three attempts per filing: a
#      transient rejection is retried at a genuinely DIFFERENT market state hours later rather
#      than 60 seconds later at the same broken quote, and the budget is spent across one
#      session instead of inside one bad minute.
#
# :40 keeps it off the top of the hour (where every other cron here lands) and 20 minutes AFTER
# the congress ingest tick at :20, so a cycle plans against the batch that tick just stored.
CONGRESS_COPY_CYCLE_CRON = {"hour": "10,12,14", "minute": 40, "day_of_week": "mon-fri"}

# THE RESOLVE TICK — every minute, ALL WEEK.
#
# WELL UNDER THE CANCEL WINDOW, WHICH IS THE WHOLE REQUIREMENT. The window defaults to 300s and
# is floored at 60s (``Settings.congress_autoexec_window_seconds``), so a 60s tick means a
# trade fires within one minute of its window closing and the operator's cancel window is never
# SHORTER than advertised — only, at most, one tick longer. That is the safe direction to err:
# extra cancel time can only ever prevent a trade. A tick at or above the window would let the
# window elapse unnoticed between ticks and make the real cancel time a function of scheduler
# jitter.
#
# ALL WEEK, not weekdays, and not session hours. A ticket queued at 15:58 Friday comes due at
# 16:03 Friday; one queued before a holiday comes due during it. Those tickets do not TRADE
# off-session — the executor's own gates decide that — but they must still be RESOLVED, because
# an unresolved ticket produces no outcome notification, and the operator's only interface is
# now notifications. A tick with nothing due is a pure no-op, so running it always costs
# nothing and running it selectively strands tickets in a state nobody hears about.
AUTOEXEC_RESOLVE_CRON = {"minute": "*"}
AUTOEXEC_RESOLVE_SECONDS = 60

# THE DAILY DIGEST — once per weekday, after the close and after the EOD review.
#
# It is the lane's ONLY unconditional statement, and it is the message that makes "the lane did
# nothing today" a positive fact rather than an absence. Everything else this lane sends is
# event-driven: no filings means no messages, and a lane broken since 09:31 sends exactly the
# same nothing. Registered as a real job for that reason and no other.
#
# 16:20 ET: after ``eod_review`` (16:15) and after ``daily_disarm`` (16:00), so the digest is
# written against a settled day. Weekdays only — the digest reports a TRADING day, and a Sunday
# digest reporting zero of everything is noise that would train the operator to skip it.
AUTOEXEC_DIGEST_CRON = {"hour": 16, "minute": 20, "day_of_week": "mon-fri"}

# THE FILL NOTICE — every 5 minutes, ALL WEEK.
#
# ``submitted`` and ``filled`` are not the same fact, and only one of them is a position. RW-16
# prices this lane's orders as LIMIT at the gate quote, so an order that rests unfilled is the
# NORMAL case; without this job the operator's last word on every trade was "submitted", which
# they would reasonably read as "done". The producer reads the SIGNED chain
# (``copytrade.notify.build_fill_service``), so it announces fills applied by RW-15's
# order-status reconcile tick in whatever process ran it — including after a restart.
#
# 5 minutes, not 1: a fill is news, not a deadline. Nothing is gated on this message (unlike the
# queued notice, which gates execution), the reconcile tick that produces the transition is
# itself periodic, and a slower cadence keeps a recovering surface from being hit by the resolve
# tick and this one in the same second. ALL WEEK for the same reason the resolve tick is: an
# outcome the operator never hears about is the failure mode this lane exists to avoid, and a
# tick with nothing pending is a pure no-op.
AUTOEXEC_FILLS_CRON = {"minute": "*/5"}
"""The resolve cadence in seconds. ``Settings.congress_autoexec_window_seconds`` is floored at
this value and ``tests/system/test_autoexec_wiring.py`` pins the two together, so a change here
that would make the cancel window unpredictable fails a test instead of shipping."""

# THE ORDER-STATUS RECONCILE TICK — every 5 minutes, ALL WEEK.
#
# THE DRAIN ON THE SEC-07 PENDING-EXPOSURE CAP. A paper LIMIT order that does not cross acks
# ``accepted``, which no state-machine edge consumes, so without this tick the sum of
# approved-but-unsettled notional only ever RISES — and once it reaches the cap the gateway
# refuses EVERY order, human or lane, permanently. That is not a lane feature, which is why this
# job is registered off the paper transport rather than off the auto-exec lane.
#
# 5 minutes: the tick reads one endpoint per open order, nothing is gated on its latency, and
# an order that settles four minutes late costs only four minutes of cap headroom. ALL WEEK
# because an order submitted at 15:59 fills after the tick set stops on a weekday cadence, and
# an un-drained order over a weekend is three days of headroom lost. A tick with no open orders
# is a pure no-op.
ORDER_STATUS_POLL_CRON = {"minute": "*/5"}


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


def run_congress_copy_cycle(state, clock=None, *, service: Callable | None = None):
    """RW-17 — one congressional copy CYCLE tick. Dispatches to the injected service.

    RW-16 LEFT THIS UNSCHEDULED ON PURPOSE, AND THE OPERATOR REVERSED THAT. The original
    reasoning (``main.py``, and ``tests/system/test_congress_wiring.py``) was that a cron job
    "would quietly manufacture proposals off a 30-45 day-old signal into an approval queue
    nobody asked to fill". The operator has now asked to fill it, and asked for the queue to be
    adjudicated without them: RW-17 replaces the human approver with an audited, budget-railed,
    paper-only auto-adjudication. What did NOT change is the part that reasoning was actually
    protecting: ``producing_runtime`` is still ``"deterministic:copytrade"``,
    ``assert_execution_runtime`` still REFUSES it above paper, and the cycle still emits
    nothing at all in any live/auto rung. Scheduling it cannot make live capital reachable.

    SKIPS WHEN THE MARKET IS CLOSED, like ``run_intraday_loop`` and for a sharper reason: the
    risk gate's ``quote_sane`` check is HARD, and a rejection on a stale off-hours quote spends
    one of the filing's three copy attempts. A cron cannot know about a market holiday or an
    early close, so the guard is here rather than only in ``CONGRESS_COPY_CYCLE_CRON``.

    NEVER RAISES. The scheduler thread runs the resolve tick and every other feed; one bad
    cycle (an unreadable disclosure batch, a vendor hiccup inside the quote seam) must not take
    them down with it. The failure is LOGGED at error level and returned as a named outcome —
    it is never swallowed into a tidy zero, which for this job would read exactly like "Congress
    filed nothing".
    """
    clock = clock or state.clock
    now = clock.now()
    if service is None:
        # Unlike the WP8 workflow stubs this does NOT raise: the lane is opt-in, and
        # ``build_scheduler`` only registers this job when the service exists, so reaching here
        # means a caller drove the callable directly.
        return {"skipped": "no_copy_cycle_service", "at": now.isoformat()}
    if not is_market_open(now):
        return {"skipped": "market_closed", "at": now.isoformat()}
    try:
        return service(state, clock)
    except Exception as exc:                # noqa: BLE001 - one cycle never kills the scheduler
        log.exception("RW-17 congressional copy cycle FAILED: %r", exc)
        return {"error": repr(exc), "at": now.isoformat()}


def run_autoexec_resolve(state, clock=None, *, service: Callable | None = None):
    """RW-17 — the ``PaperAutoExecutor.resolve_due`` tick: fire or refuse every ticket whose
    cancel window has closed. Runs every ``AUTOEXEC_RESOLVE_SECONDS`` (see that constant for
    why it must stay well under the window).

    RESILIENCE IS THE WHOLE CONTRACT OF THIS FUNCTION. Two independent isolations exist, and
    they are not the same one:

      * PER TICKET — owned by ``resolve_due``. One ticket that cannot be adjudicated must be
        settled and counted without abandoning the tickets behind it in the list.
      * PER TICK — owned HERE. If ``resolve_due`` itself raises, that exception must not
        propagate into the APScheduler worker: a job that raises is a job whose NEXT run is the
        only recovery, and swallowing it here (loudly) keeps the following minute's tick
        running against the same tickets. A ticket that fails to resolve does not trade — the
        executor fails closed — so the cost of a bad tick is a delayed outcome, never an
        unadjudicated order.

    Returns the resolutions (or the named failure) rather than a count: an auto-executed trade
    that nobody can name afterwards is indistinguishable from one that never happened.
    """
    clock = clock or state.clock
    now = clock.now()
    if service is None:
        return {"skipped": "no_autoexec_service", "at": now.isoformat()}
    try:
        resolutions = service(state, clock)
    except Exception as exc:                # noqa: BLE001 - a bad tick must not wedge the job
        log.exception("RW-17 paper auto-exec resolve tick FAILED: %r", exc)
        return {"error": repr(exc), "at": now.isoformat()}
    resolutions = list(resolutions or [])
    return {"resolved": len(resolutions), "resolutions": resolutions, "at": now.isoformat()}


def run_autoexec_digest(state, clock=None, *, service: Callable | None = None):
    """RW-17 — the end-of-day statement for the paper auto-execution lane.

    THE ONE MESSAGE THAT ARRIVES WHETHER OR NOT ANYTHING HAPPENED. Every other notification on
    this lane is event-driven, so a day with no filings and a day on which the lane has been
    broken since 09:31 produce identical silence — and the operator gave up their approval queue,
    which is the surface that would otherwise have told them the difference. The digest closes
    that: it always prints what traded, what was refused and by which gate, and what is left of
    each budget rail.

    Its rows come from the SIGNED chain (``copytrade/digest.py``), not from the executor's
    in-memory histogram, so a restart cannot make the day look quiet.

    NEVER RAISES, for the same reason as the other two lane jobs: the digest is a report, and a
    report that takes down the scheduler thread has made things worse than the gap it was
    reporting on.
    """
    clock = clock or state.clock
    now = clock.now()
    if service is None:
        return {"skipped": "no_autoexec_digest_service", "at": now.isoformat()}
    try:
        return service(state, clock)
    except Exception as exc:                # noqa: BLE001 - a report never kills the scheduler
        log.exception("RW-17 paper auto-exec daily digest FAILED: %r", exc)
        return {"error": repr(exc), "at": now.isoformat()}


def run_autoexec_fills(state, clock=None, *, service: Callable | None = None):
    """RW-17 — announce every SIGNED fill of a lane order the operator has not been told about.

    THE MISSING HALF OF "SUBMITTED". ``copytrade/notify.py`` could always render a ``filled``
    message and nothing ever produced one, so the operator's last word on a trade was that it had
    been sent to the broker. A resting limit order that never crosses produces exactly the same
    silence as a filled one, and the operator gave up the approval queue that would otherwise
    have shown them the difference.

    Derived from the chain rather than from an in-process handle: the transition is written by
    RW-15's order-status reconcile tick, possibly in another process and certainly across
    restarts, and a CONFIRMED notice writes its own signed marker so nothing is announced twice.

    NEVER RAISES, like the other lane jobs — this is a report, and a report that takes down the
    scheduler thread has made things worse than the gap it was reporting.
    """
    clock = clock or state.clock
    now = clock.now()
    if service is None:
        return {"skipped": "no_autoexec_fills_service", "at": now.isoformat()}
    try:
        return service(state, clock)
    except Exception as exc:                # noqa: BLE001 - a report never kills the scheduler
        log.exception("RW-17 paper auto-exec fill notice FAILED: %r", exc)
        return {"error": repr(exc), "at": now.isoformat()}


def run_order_status_reconcile(state, clock=None, *, service: Callable | None = None):
    """RW-15 (minimal) — settle every resting/partial order the broker is DEFINITIVE about.

    THE ONLY THING THAT DRAINS THE SEC-07 PENDING-EXPOSURE CAP. Every other job here can be
    skipped for a tick with no lasting cost; this one cannot, because the quantity it reduces
    only ever grows without it, and when it reaches the cap the gateway refuses every order from
    every caller — including the human path — with no way back short of a restart.

    Registered off the PAPER TRANSPORT, not off the auto-exec lane: a resting order is a resting
    order whoever approved it.

    NOT AN ORDER PATH. The service this dispatches to passes a status READER to
    ``brokers.order_status_poll``, which asserts at import that the states it can reach are all
    draining ones — it can never advance an order toward a broker (Inv 1).

    NEVER RAISES, like every other job in this module: a tick that dies takes the next five
    minutes of drain with it, and the failure mode it exists to prevent is precisely the one
    that compounds while nobody is looking.
    """
    clock = clock or state.clock
    now = clock.now()
    if service is None:
        return {"skipped": "no_order_status_service", "at": now.isoformat()}
    try:
        return service(state, clock)
    except Exception as exc:                # noqa: BLE001 - a bad tick never wedges the job
        log.exception("RW-15 order-status reconcile tick FAILED: %r", exc)
        return {"error": repr(exc), "at": now.isoformat()}


def build_scheduler(state, clock=None, *, services: dict | None = None, ingest=None):
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
        )
    # RW-16/RW-17: the congressional INGEST feed is scheduled by the loop above like any other
    # feed. The copy CYCLE and the auto-exec resolve tick are registered HERE, and ONLY when
    # their services were composed — the ingest posture ("an unprovisioned feed gets NO job at
    # all"), not the workflow posture (always registered, dispatching to a raising stub).
    #
    # WHY THAT DISTINCTION IS LOAD-BEARING HERE. These two jobs are the paper auto-execution
    # lane: together they turn a filing into a trade with no human tap. ``main.compose`` builds
    # their services only when the operator has explicitly set COCKPIT_CONGRESS_AUTOEXEC and the
    # whole lane (paper gateway + notification seam) is provisioned. So a deployment that never
    # opted in has EXACTLY the base five jobs plus its feeds, unchanged from RW-16 — the lane is
    # not merely inert, it does not exist. A half-built lane is the one shape forbidden: without
    # a notification seam the operator would not hear about the trades it made.
    if services.get("copy_cycle") is not None:
        sched.add_job(
            lambda: run_congress_copy_cycle(state, clock, service=services.get("copy_cycle")),
            CronTrigger(timezone=EASTERN, **CONGRESS_COPY_CYCLE_CRON),
            id="congress_copy_cycle", name="congress_copy_cycle", replace_existing=True,
        )
    if services.get("autoexec_resolve") is not None:
        sched.add_job(
            lambda: run_autoexec_resolve(state, clock, service=services.get("autoexec_resolve")),
            CronTrigger(timezone=EASTERN, **AUTOEXEC_RESOLVE_CRON),
            id="autoexec_resolve", name="autoexec_resolve", replace_existing=True,
        )
    if services.get("autoexec_digest") is not None:
        sched.add_job(
            lambda: run_autoexec_digest(state, clock, service=services.get("autoexec_digest")),
            CronTrigger(timezone=EASTERN, **AUTOEXEC_DIGEST_CRON),
            id="autoexec_digest", name="autoexec_digest", replace_existing=True,
        )
    if services.get("autoexec_fills") is not None:
        sched.add_job(
            lambda: run_autoexec_fills(state, clock, service=services.get("autoexec_fills")),
            CronTrigger(timezone=EASTERN, **AUTOEXEC_FILLS_CRON),
            id="autoexec_fills", name="autoexec_fills", replace_existing=True,
        )
    # RW-15 (minimal): registered off the PAPER TRANSPORT rather than the lane — see
    # ``run_order_status_reconcile``. A deployment with no status reader gets no job (the ingest
    # posture), which is the honest shape: nothing drains, and ``/status`` shows why.
    if services.get("order_status_poll") is not None:
        sched.add_job(
            lambda: run_order_status_reconcile(
                state, clock, service=services.get("order_status_poll")),
            CronTrigger(timezone=EASTERN, **ORDER_STATUS_POLL_CRON),
            id="order_status_poll", name="order_status_poll", replace_existing=True,
        )
    return sched
