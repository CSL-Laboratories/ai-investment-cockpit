"""RW-01 — the INGEST RUNNER: the missing wire between WP3's pollers and WP8's storage.

Until this module, ``ingest/`` and ``workflows/events.py`` were connected only by tests.
``main.build_ingest_fetchers`` built fetch closures that nothing called, the WP3 pollers had no
production driver, and ``persist_events`` was reachable only from ``tests/``. This is the
driver: it owns the poller/consumer INSTANCES, gives each one an ``on_event`` sink that lands
in ``persist_events``, and exposes two drive shapes the composition root can schedule —
``poll(name)`` for the cron-cadence feeds and ``start_streams()`` for the one push feed
(Alpaca's news WebSocket).

Nothing here imports ``brokers/`` or ``risk/``, and no model is on this path (Invariant 1 +
"execution stays model-free"). Feed text is already neutralized as it enters, by
``ingest.normalize.to_market_event`` -> ``escape_untrusted`` (SEC-02/M3): every poller in
``ingest/`` builds its events through that one function, so this runner never handles a raw
untrusted string itself and must never grow a path that bypasses it.

ONE ``persist_events`` CALL PER EVENT — LOAD-BEARING, DO NOT BATCH IT
---------------------------------------------------------------------
``persist_events`` is batch-shaped (a list in, ONE parquet batch + ONE ``EVENT_INGEST_BATCH``
audit entry out) while every poller is event-shaped (``on_event(event)``, called per item).
The obvious adapter — buffer the tick, flush once at the end — is WRONG HERE, and wrong in a
way that silently destroys evidence.

``ingest/edgar_brew.py`` ("A FAILED EMIT IS NOT A DROP") and ``ingest/finra_short_interest.py``
both build their forward-progress invariant on ``on_event`` RAISING: when it does, the poller
un-burns the dedup key (``Dedup.discard``) and bars the cursor from advancing past that row, so
the evidence stays re-fetchable and the feed wedges LOUDLY instead of losing it. With a
buffered flush, ``on_event`` has already RETURNED for every event in the batch by the time the
flush fails — the dedup keys are burnt, the cursors have advanced past rows that were never
persisted, and the counters report a tidy success. That is precisely the failure those two
modules spent six adversarial rounds closing, re-opened from the outside.

So the sink calls ``persist_events(state, [event], ...)`` per event and lets the exception
propagate. The cost is one parquet batch + one audit entry per event, which is real but is a
STORAGE-LAYOUT problem (fix it with compaction in ``storage/parquet_store.py``) — never a
reason to weaken the emit contract. If you are here to make ingest cheaper: batching is
available to you only if you ALSO give the pollers a way to learn which events failed, and
that is a change to ``ingest/``, not to this file.

WHAT THIS RUNNER DOES **NOT** GATE ON
--------------------------------------
Ingest keeps running under HALT / ``watch_only`` / any mode. Those controls exist to stop the
ORDER path; ingest is read-only awareness (Engine 1), and Invariant 7's "fail closed" means
"never trade without fresh analysis", NOT "stop looking at the world". Silencing the feeds on a
killswitch would blind the operator at exactly the moment they most need to see, and would make
this feed's emptiness indistinguishable from a quiet day — the failure mode the whole ingest
layer is built to avoid. It is also why nothing here consults ``ModeLadder``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("cockpit.ingest")

# Canonical feed names. These are the ``ingest.normalize.ALLOWED_SOURCES`` spellings wherever a
# feed maps 1:1 to a source, so a runner key and an event's ``source`` never drift apart.
FEED_ALPACA_NEWS = "alpaca:benzinga"
FEED_FINNHUB = "finnhub"
FEED_EDGAR = "sec:edgar"
FEED_CALENDAR = "calendar"
FEED_FINRA = "finra"
FEED_VIBE_TRADING = "vibe-trading"

# Restart backoff for a stream reader whose consumer returned or exhausted its own reconnect
# budget. The consumer (``ingest/alpaca_news.py``) already does fine-grained exponential backoff
# INSIDE one ``run``; this is the coarse outer retry after it gives up entirely.
STREAM_RESTART_SECONDS = 30.0
MAX_PENDING_DELIVERY_GAPS = 1024


def _delivery_identity(event) -> bytes | None:
    """Bounded token for one exact source image; never retain payloads in health state."""
    if not isinstance(event, dict):
        return None
    fields = [event.get(name) for name in ("source", "article_id", "content_hash")]
    if any(not isinstance(value, str) or not value or len(value) > 4096 for value in fields):
        return None
    return hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).digest()


class PersistSink:
    """The ``on_event`` callback the WP3 pollers/consumers are driven with.

    Calls ``persist_events`` with a ONE-ITEM list and lets any failure PROPAGATE — see "ONE
    ``persist_events`` CALL PER EVENT" in the module docstring. A raise here is the signal the
    cursor-carrying pollers need in order to roll back their dedup key and hold their cursor;
    swallowing it would convert a transient storage outage into permanent evidence loss.

    A DUPLICATE IS A SUCCESS, NOT A FAILURE. ``persist_events`` de-dupes against the DB on the
    raw source identity and returns ``stored=[] , duplicates=1`` rather than raising, so a
    re-emitted item (a poller restart, a re-fetched window) is a clean no-op and the poller
    correctly keeps its key and advances. Counted separately so a feed that is only ever
    producing duplicates is visible as such and not as throughput.
    """

    def __init__(self, state, *, feed: str, parquet_root, persist_fn=None, clock=None,
                 max_pending_gaps: int = MAX_PENDING_DELIVERY_GAPS) -> None:
        if type(max_pending_gaps) is not int or max_pending_gaps < 1:
            raise ValueError("pending delivery gap capacity must be a positive integer")
        self._state = state
        self._parquet_root = parquet_root
        self._clock = clock
        self.feed = feed
        self.persisted = 0
        self.duplicates = 0
        self.persist_errors = 0
        self._lock = threading.Lock()
        self._pending_gaps: set[bytes] = set()
        self._gap_capacity = max_pending_gaps
        self._gap_overflow = False
        self._unidentified_gap = False
        if persist_fn is not None:
            self._persist = persist_fn
        else:
            # Imported lazily so ``orchestrator`` keeps no module-level dependency on
            # ``workflows`` (the existing direction is workflows -> orchestrator.state).
            from workflows.events import persist_events

            self._persist = persist_events

    def __call__(self, event: dict) -> None:
        identity = _delivery_identity(event)
        try:
            result = self._persist(self._state, [event], parquet_root=self._parquet_root,
                                   clock=self._clock)
            stored, duplicates = len(result.stored), result.duplicates
        except Exception:
            # BUG-151: consumers may catch this raise and continue receiving. Keep the
            # missed identity visible independently of the consumer's worker lifetime.
            with self._lock:
                self.persist_errors += 1
                if identity is None:
                    self._unidentified_gap = True
                elif identity in self._pending_gaps or len(self._pending_gaps) < self._gap_capacity:
                    self._pending_gaps.add(identity)
                else:
                    self._gap_overflow = True
            raise  # pollers still roll back their dedup/cursor on this exact callback
        with self._lock:
            self.persisted += stored
            self.duplicates += duplicates
            pending = identity in self._pending_gaps
        # A same-id legacy duplicate can refer to DIFFERENT content. Resolve a pending
        # gap only after exact storage success or a verified matching raw duplicate.
        resolved = stored > 0 or (pending and duplicates > 0 and self._verified_duplicate(event))
        if pending and resolved:
            with self._lock:
                self._pending_gaps.discard(identity)

    def _verified_duplicate(self, event: dict) -> bool:
        """Rare recovery path: prove a duplicate really contains the failed source image."""
        from sqlalchemy import select

        from schemas.models.market_event import MarketEvent
        from workflows.events import resolve_raw

        try:
            with self._state.session_factory() as session:
                row = session.execute(select(MarketEvent).where(
                    MarketEvent.source == event["source"],
                    MarketEvent.content_hash == event["content_hash"],
                ).limit(1)).scalar_one_or_none()
                if row is None:
                    return False
                session.expunge(row)
            item = resolve_raw(self._parquet_root, row.raw_ref, state=self._state, row=row)
            return _delivery_identity(item) == _delivery_identity(event)
        except Exception:
            return False  # unavailable/unverified storage cannot clear an existing gap

    def counters(self) -> dict:
        with self._lock:
            gap = bool(self._pending_gaps or self._gap_overflow or self._unidentified_gap)
            return {"persisted": self.persisted, "duplicates": self.duplicates,
                    "persist_errors": self.persist_errors, "delivery_gap": gap,
                    "unresolved_events": len(self._pending_gaps),
                    "delivery_gap_overflow": self._gap_overflow,
                    "unidentified_delivery_gap": self._unidentified_gap}


@dataclass
class PollFeed:
    """A cron-cadence feed: something with ``poll(now, on_event) -> int``."""

    name: str
    poller: object
    sink: PersistSink
    polls: int = 0
    emitted: int = 0
    errors: int = 0
    consecutive_errors: int = 0
    last_error: str | None = None
    # The feed's own freshness reader, when it has one (``make_brew_fetch`` attaches
    # ``brew_status``). Consulted by ``status()`` so an unprovisioned/degraded/not-run feed is
    # never reported as a quiet day — see ingest/edgar_brew.BrewRunOutcome.
    status_fn: object | None = None


@dataclass
class StreamFeed:
    """A push feed: something with ``run(on_event, max_frames=None) -> int`` that BLOCKS."""

    name: str
    consumer: object
    sink: PersistSink
    runs: int = 0
    emitted: int = 0
    errors: int = 0
    last_error: str | None = None
    failed_at_deliveries: int | None = None
    thread: threading.Thread | None = field(default=None, repr=False)


class IngestRunner:
    """Owns the live poller/consumer instances and drives them.

    ``monotonic`` is injected because ``poll(now, ...)``'s ``now`` is MONOTONIC SECONDS (the
    ``TokenBucket`` elapsed-time reading — ``ingest/ratelimit.py:2``), NOT a wall clock. Tests
    pass a counter so rate limiting is deterministic. The pollers' CALENDAR seams are a separate
    injection entirely (``EdgarBrewPoller(clock=...)`` / ``FinraShortInterestPoller(clock=...)``,
    validated by ``ingest/clock_contract.resolve_wall_clock``) and are wired at construction by
    the caller — never derived from ``now`` here.
    """

    def __init__(self, state, *, parquet_root, persist_fn=None, monotonic=None,
                 clock=None) -> None:
        self._state = state
        self._parquet_root = parquet_root
        self._persist_fn = persist_fn
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._clock = clock
        self.polls: dict[str, PollFeed] = {}
        self.streams: dict[str, StreamFeed] = {}
        self._stop = threading.Event()

    # ---- registration -------------------------------------------------------------------
    def _sink(self, feed: str) -> PersistSink:
        return PersistSink(self._state, feed=feed, parquet_root=self._parquet_root,
                           persist_fn=self._persist_fn, clock=self._clock)

    def add_poller(self, name: str, poller, *, status_fn=None) -> PollFeed:
        """Register a cron-cadence feed. ``status_fn(day) -> object`` is the feed's own
        freshness reader when it has one (EDGAR's ``fetch.brew_status``)."""
        feed = PollFeed(name=name, poller=poller, sink=self._sink(name), status_fn=status_fn)
        self.polls[name] = feed
        return feed

    def add_stream(self, name: str, consumer) -> StreamFeed:
        """Register a push feed whose ``run`` blocks (driven on its own thread)."""
        feed = StreamFeed(name=name, consumer=consumer, sink=self._sink(name))
        self.streams[name] = feed
        return feed

    @property
    def feed_names(self) -> list[str]:
        return sorted([*self.polls, *self.streams])

    # ---- drive: one poll tick -----------------------------------------------------------
    def poll(self, name: str, *, now=None) -> dict:
        """Run ONE tick of ``name`` and return a JSON-native summary.

        FAIL-SOFT, NOT FAIL-SILENT. A feed that raises must not take down the scheduler thread
        or the other feeds, so the exception is caught, logged at WARNING, counted, and RETURNED
        in the summary. It is never swallowed into a success: ``status`` is ``"error"``,
        ``consecutive_errors`` climbs, and ``last_error`` carries the repr — so a feed that is
        dead reads as dead rather than as quiet.

        ``ClockContractError`` is DELIBERATELY NOT caught. It is a WIRING bug (someone passed
        ``clock=time.monotonic`` or a naive datetime), it is deterministic, and
        ``ingest/clock_contract.py`` exists precisely because every previous attempt to soften
        it produced a silently dead feed. Let it raise on the first tick.
        """
        from ingest.clock_contract import ClockContractError

        feed = self.polls.get(name)
        if feed is None:
            return {"feed": name, "status": "not_registered"}
        feed.polls += 1
        tick = self._monotonic() if now is None else now
        try:
            emitted = int(feed.poller.poll(tick, feed.sink))
        except ClockContractError:
            raise                       # wiring bug: loud, on the first tick, always
        except Exception as exc:        # noqa: BLE001 - one sick feed never kills the others
            feed.errors += 1
            feed.consecutive_errors += 1
            feed.last_error = repr(exc)
            log.warning("ingest feed %s FAILED (consecutive=%d): %r",
                        name, feed.consecutive_errors, exc)
            credited = getattr(exc, "emitted", None)
            if type(credited) is int and credited > 0:
                feed.emitted += credited
            if name == FEED_EDGAR:
                self._sync_edgar_watch_only(False, f"EDGAR poll failed: {exc!r}")
            result = {"feed": name, **self._poll_health(feed), "error": repr(exc),
                      "consecutive_errors": feed.consecutive_errors}
            if type(credited) is int:
                result["emitted"] = credited
            return result
        feed.consecutive_errors = 0
        feed.emitted += emitted
        result = {"feed": name, "emitted": emitted, **self._poll_health(feed),
                  **self._signals(feed)}
        if name == FEED_EDGAR:
            feed_status = (
                self._read_status(feed, self._today())
                if feed.status_fn is not None
                else {
                    "status": "unreadable",
                    "trustworthy": False,
                    "reason": "EDGAR has no freshness reader",
                }
            )
            result["feed_status"] = feed_status
            self._sync_edgar_watch_only(
                bool(feed_status.get("trustworthy")),
                str(feed_status.get("reason") or "EDGAR freshness is unavailable"),
            )
        return result

    def poll_all(self, *, now=None) -> dict:
        """One tick of EVERY registered poll feed (the dry-run / manual-catch-up drive)."""
        return {name: self.poll(name, now=now) for name in sorted(self.polls)}

    @staticmethod
    def _poll_health(feed: PollFeed) -> dict:
        stats = feed.sink.counters()
        degraded = stats["delivery_gap"]
        return {**stats, "degraded": degraded,
                "status": ("error" if feed.consecutive_errors else
                           "degraded" if degraded else "ok")}

    @staticmethod
    def _signals(feed: PollFeed) -> dict:
        """The poller's OWN progress/strand signals, surfaced rather than discarded.

        ``EdgarBrewPoller`` and ``FinraShortInterestPoller`` both publish ``progress_signal`` /
        ``strand_signal`` / ``counters`` specifically so that a wedged or clamped feed is
        DISTINGUISHABLE from a quiet one. A runner that dropped them would re-create the very
        silence those attributes exist to break.
        """
        out: dict = {}
        for attr in ("progress_signal", "strand_signal"):
            value = getattr(feed.poller, attr, None)
            if value:
                out[attr] = value
        counters = getattr(feed.poller, "counters", None)
        if isinstance(counters, dict):
            nonzero = {k: v for k, v in counters.items() if v}
            if nonzero:
                out["counters"] = nonzero
        cursor = getattr(feed.poller, "cursor", None)
        if cursor is not None:
            out["cursor"] = str(cursor)
        return out

    # ---- drive: the blocking stream -----------------------------------------------------
    @staticmethod
    def _stream_health(feed: StreamFeed) -> dict:
        counters = getattr(feed.consumer, "counters", {})
        consumer_counts = {
            name: counters.get(name, 0) for name in ("frame_error", "emit_error")
        } if isinstance(counters, dict) else {}
        stats = feed.sink.counters()
        # Frame failures have no successfully normalized identity to prove recovered.
        # Historical emit errors alone do not prevent an exact successful redelivery
        # from clearing its gap; unidentified callback failures remain conservative.
        degraded = (stats["delivery_gap"] or bool(consumer_counts.get("frame_error"))
                    or consumer_counts.get("emit_error", 0) > stats["persist_errors"])
        connection_failed = (feed.failed_at_deliveries is not None
                             and stats["persisted"] + stats["duplicates"]
                             <= feed.failed_at_deliveries)
        degraded = degraded or connection_failed
        return {**stats, "counters": consumer_counts,
                "degraded": degraded,
                "status": "error" if connection_failed else "degraded" if degraded else "ok"}

    def run_stream(self, name: str, *, max_frames: int | None = None) -> dict:
        """Drive a push feed ONCE. Blocks until the consumer returns (clean end of stream, or
        ``max_frames`` reached) or raises. Used directly by tests; production goes through
        ``start_streams``."""
        feed = self.streams.get(name)
        if feed is None:
            return {"feed": name, "status": "not_registered"}
        feed.runs += 1
        try:
            emitted = int(feed.consumer.run(feed.sink, max_frames=max_frames))
        except Exception as exc:        # noqa: BLE001 - reported, then retried by the supervisor
            feed.errors += 1
            feed.last_error = repr(exc)
            stats = feed.sink.counters()
            feed.failed_at_deliveries = stats["persisted"] + stats["duplicates"]
            log.warning("ingest stream %s FAILED: %r", name, exc)
            return {"feed": name, **self._stream_health(feed),
                    "status": "error", "error": repr(exc)}
        feed.emitted += emitted
        feed.failed_at_deliveries = None
        return {"feed": name, "emitted": emitted, **self._stream_health(feed)}

    def _supervise(self, name: str) -> None:
        """Restart a stream reader after it returns or gives up (daemon-thread body).

        ``AlpacaNewsConsumer.run`` already backs off and reconnects internally; it only returns
        on a CLEAN end of stream, or raises once its reconnect budget is spent. Either way the
        feed is then dead until something restarts it, so this is the outer supervisor. It exits
        promptly on ``stop()`` so the process can shut down.
        """
        while not self._stop.is_set():
            self.run_stream(name)
            if self._stop.wait(STREAM_RESTART_SECONDS):
                return

    def start_streams(self) -> list[threading.Thread]:
        """Start one DAEMON supervisor thread per push feed. Idempotent per feed.

        Daemon threads so a killswitch / Ctrl-C tears the process down without waiting on a
        blocked socket read. The audit chain is already serialized across threads
        (``storage/audit.py`` holds an in-process lock plus a cross-process seq-collision
        retry), so these threads may persist concurrently with the scheduler's poll jobs.
        """
        started: list[threading.Thread] = []
        for name, feed in sorted(self.streams.items()):
            if feed.thread is not None and feed.thread.is_alive():
                continue
            thread = threading.Thread(target=self._supervise, args=(name,),
                                      name=f"ingest-{name}", daemon=True)
            feed.thread = thread
            thread.start()
            started.append(thread)
            log.info("ingest stream %s started", name)
        return started

    def stop(self) -> None:
        """Signal the stream supervisors to exit. The in-flight blocking ``run`` is not
        interrupted (the threads are daemons; process exit reaps them)."""
        self._stop.set()

    def join_streams(self, *, timeout_s: float = 5.0) -> bool:
        """Boundedly prove every configured stream supervisor stopped.

        A blocked consumer is intentionally not declared clean merely because its thread is
        a daemon. The caller leaves the safety-session marker open when this returns False.
        """
        self.stop()
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        for feed in self.streams.values():
            thread = feed.thread
            if thread is None:
                continue
            thread.join(max(0.0, deadline - time.monotonic()))
        return all(
            feed.thread is None or not feed.thread.is_alive()
            for feed in self.streams.values()
        )

    # ---- observability ------------------------------------------------------------------
    def status(self, *, day=None) -> dict:
        """Per-feed health for ``GET /status`` and the live-smoke runbook.

        For a feed that publishes its own freshness verdict (EDGAR's ``brew_status``) the
        verdict is INCLUDED, because "0 events" from that feed is only good news under exactly
        one status (``nothing-material``). Reporting throughput without it would let a
        NOT_PROVISIONED / NOT_RUN / DEGRADED feed read as a quiet day.
        """
        day = day if day is not None else self._today()
        feeds: dict = {}
        for name, feed in sorted(self.polls.items()):
            entry = {
                "kind": "poll", "polls": feed.polls, "emitted": feed.emitted,
                "errors": feed.errors, "consecutive_errors": feed.consecutive_errors,
                "last_error": feed.last_error, **self._poll_health(feed), **self._signals(feed),
            }
            if feed.status_fn is not None:
                entry["feed_status"] = self._read_status(feed, day)
            feeds[name] = entry
        for name, feed in sorted(self.streams.items()):
            feeds[name] = {
                "kind": "stream", "runs": feed.runs, "emitted": feed.emitted,
                "errors": feed.errors, "last_error": feed.last_error,
                "alive": bool(feed.thread is not None and feed.thread.is_alive()),
                **self._stream_health(feed),
            }
        return {"day": day, "feeds": feeds}

    @staticmethod
    def _read_status(feed: PollFeed, day) -> dict:
        """Read a feed's own freshness verdict, fail-closed on a reader that raises."""
        try:
            outcome = feed.status_fn(day)
        except Exception as exc:        # noqa: BLE001 - an unreadable verdict is not good news
            return {"status": "unreadable", "trustworthy": False, "reason": repr(exc)}
        return {
            "status": getattr(getattr(outcome, "status", None), "value", None),
            "trustworthy": bool(getattr(outcome, "trustworthy", False)),
            "quiet_day": bool(getattr(outcome, "quiet_day", False)),
            "reason": getattr(outcome, "reason", None),
        }

    def _sync_edgar_watch_only(self, trustworthy: bool, reason: str) -> None:
        """Make EDGAR degradation an audited order-path condition, not just status text."""
        from orchestrator.breakers import BreakerEngine
        from orchestrator.mode_ladder import ModeLadder

        engine = BreakerEngine(self._state)
        if not trustworthy:
            if engine.needs_watch_only_transition(FEED_EDGAR):
                engine.enter_watch_only(
                    FEED_EDGAR, reason, clock=self._clock, actor="edgar-pipeline"
                )
            return
        if engine.can_recover_watch_only(FEED_EDGAR):
            engine.recover_from_watch_only(
                FEED_EDGAR,
                ModeLadder(self._state).current(),
                clock=self._clock,
                actor="edgar-pipeline",
            )

    def _today(self) -> str:
        clock = self._clock if self._clock is not None else getattr(self._state, "clock", None)
        return clock.now().date().isoformat() if clock is not None else ""
