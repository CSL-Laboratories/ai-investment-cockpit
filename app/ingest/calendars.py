"""WP3 — earnings/macro calendar pull (injectable ``fetch() -> list[dict]``) -> MarketEvent.

RW-01 adds ``CalendarPoller``, the ``poll(now, on_event)`` driver the scheduler runs, built on
the same template as the sibling connectors (``ingest/finnhub_poll.py``,
``ingest/edgar_brew.py``, ``ingest/finra_short_interest.py``): an injected ``fetch``, per-item
isolation so one bad entry never kills the batch, and a dedup key that is ROLLED BACK when the
emit fails.

NO CURSOR HERE, AND THAT IS CORRECT. A calendar is a re-published SNAPSHOT of a forward window,
not an append-only log: the same "AAPL 2026-07-30 earnings" row is returned by every poll until
the date passes, and a row can be REVISED (a date moves, a note is added) rather than appended.
A high-water cursor would therefore either strand the feed or hide revisions. Dedup on the
entry identity is the right control, and re-fetching the whole window every poll is the
intended behaviour, not waste — which is also why there is no saturation/wedge machinery to
mirror from ``edgar_brew``: no window can fill, because nothing is ever "behind" a cursor.
"""
from __future__ import annotations

from ingest.dedup import Dedup
from ingest.normalize import to_market_event

DEFAULT_KIND = "earnings"


def pull_calendar(fetch, kind: str = DEFAULT_KIND) -> list[dict]:
    events = []
    for e in fetch():
        sym = e.get("symbol")
        events.append(to_market_event(
            source="calendar",
            article_id=e.get("id") or f"{kind}:{sym}:{e.get('date')}",
            symbols=[sym] if sym else [],
            headline=f"{kind}: {sym or ''} {e.get('date', '')}".strip(),
            summary=e.get("note", ""),
            ts=e.get("date"),
            event_type=f"calendar:{kind}",
            raw=e,
        ))
    return events


class CalendarPoller:
    """Emit one MarketEvent per NEW calendar entry.

    ``fetch() -> list[dict]`` is injected (``main.build_ingest_fetchers`` builds the production
    one); this class performs no I/O. ``poll``'s ``now`` is MONOTONIC SECONDS and is OPAQUE
    here — it is forwarded to nothing and used for nothing, because this poller carries neither
    a rate limiter nor a calendar bound. Do NOT derive a date from it (see
    ``ingest/clock_contract.py``): the fetch closure owns its own date window.

    A FAILED EMIT IS NOT A DROP — same contract as the sibling connectors. ``Dedup.add`` is a
    check-and-set, so the key is taken BEFORE ``on_event`` and GIVEN BACK when ``on_event``
    raises; the key means "this WAS emitted", never "we tried". Without the rollback a
    transient downstream outage would permanently burn the key and the entry could never be
    re-emitted — a silent loss, which for a calendar means a missed earnings date.
    """

    def __init__(self, fetch, kind: str = DEFAULT_KIND, dedup=None):
        self._fetch = fetch
        self._kind = kind
        self._dedup = dedup or Dedup()
        #   fetch_error  — the injected fetch raised: the whole window is unavailable this tick
        #   build_error  — pull_calendar raised while normalizing (structural, in the row)
        #   emit_error   — on_event raised: TRANSIENT/RETRIABLE, key rolled back, not a drop
        #   duplicate    — already emitted (the STEADY STATE for a re-published window)
        self.counters = {"fetch_error": 0, "build_error": 0, "emit_error": 0, "duplicate": 0}
        # A window we could not read is NOT an empty calendar. Leave a durable signal.
        self.progress_signal: str | None = None

    def poll(self, now, on_event) -> int:
        try:
            entries = list(self._fetch() or [])
        except Exception as exc:      # noqa: BLE001 - a dead window must not read as "no events"
            self.counters["fetch_error"] += 1
            self.progress_signal = (
                f"CALENDAR WINDOW UNREADABLE ({exc!r}): zero events this tick is a FETCH FAULT, "
                "not an empty calendar."
            )
            return 0

        emitted, retriable = 0, 0
        for entry in entries:
            # Normalize ONE entry at a time THROUGH THE SAME ``pull_calendar`` mapping, so a
            # malformed entry is a counted ``build_error`` instead of killing the whole window,
            # and the entry->event mapping still has exactly one definition.
            try:
                built = pull_calendar(lambda e=entry: [e], kind=self._kind)
            except Exception:         # noqa: BLE001 - structural: deterministic in the entry
                self.counters["build_error"] += 1
                continue
            if not built:
                continue
            event = built[0]
            key = f"calendar:{event.get('article_id')}"
            if not self._dedup.add(key):
                self.counters["duplicate"] += 1
                continue
            try:
                on_event(event)
            except Exception:         # noqa: BLE001 - transient downstream: retry, never drop
                self._dedup.discard(key)
                self.counters["emit_error"] += 1
                retriable += 1
                continue
            emitted += 1

        if retriable:
            self.progress_signal = (
                f"{retriable} calendar entr(ies) FAILED TO EMIT and remain re-fetchable "
                "(dedup keys rolled back). This is a downstream outage, not an empty calendar."
            )
        elif emitted or not entries:
            self.progress_signal = None
        return emitted
