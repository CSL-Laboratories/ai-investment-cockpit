"""WP3 — calendar pull normalizes to provenance-tagged MarketEvents.

RW-01 adds the ``CalendarPoller`` driver tests: dedup across polls (a re-published window is
the STEADY STATE, not an error), per-entry isolation, and the "a failed emit is not a drop"
rollback the sibling connectors establish.
"""
from __future__ import annotations

import pytest

from ingest.calendars import CalendarPoller, pull_calendar


def test_pull_calendar():
    entries = [
        {"symbol": "AAPL", "date": "2026-07-30", "note": "Q3 earnings"},
        {"symbol": "MSFT", "date": "2026-07-29", "note": "Q4 earnings"},
    ]
    events = pull_calendar(lambda: entries, kind="earnings")
    assert len(events) == 2
    assert events[0]["event_type"] == "calendar:earnings"
    assert events[0]["source"] == "calendar" and events[0]["symbols"] == ["AAPL"]
    assert "AAPL" in events[0]["headline"]


# ---------------------------------------------------------------------------------------
# RW-01 — CalendarPoller
# ---------------------------------------------------------------------------------------
ENTRIES = [
    {"symbol": "AAPL", "date": "2026-07-30", "note": "Q3 earnings"},
    {"symbol": "MSFT", "date": "2026-07-29", "note": "Q4 earnings"},
]


def test_poller_emits_each_entry_once_across_repeated_windows():
    """A calendar RE-PUBLISHES its whole forward window every poll. The second poll must emit
    nothing and count duplicates — the steady state, not a fault."""
    seen = []
    poller = CalendarPoller(lambda: list(ENTRIES))

    assert poller.poll(0.0, seen.append) == 2
    assert [e["symbols"] for e in seen] == [["AAPL"], ["MSFT"]]
    assert poller.poll(1.0, seen.append) == 0
    assert len(seen) == 2
    assert poller.counters["duplicate"] == 2
    assert poller.progress_signal is None      # a re-published window is not a wedge


def test_poller_emits_a_newly_added_entry_on_a_later_poll():
    window = list(ENTRIES)
    poller = CalendarPoller(lambda: list(window))
    seen = []
    poller.poll(0.0, seen.append)

    window.append({"symbol": "NVDA", "date": "2026-08-05", "note": "Q2 earnings"})
    assert poller.poll(1.0, seen.append) == 1
    assert seen[-1]["symbols"] == ["NVDA"]


def test_failed_emit_rolls_back_the_dedup_key_and_is_retried():
    """A FAILED EMIT IS NOT A DROP (the sibling-connector contract). A transient downstream
    outage must leave the entry re-emittable, not permanently burn its key."""
    poller = CalendarPoller(lambda: list(ENTRIES[:1]))

    def broken(_event):
        raise RuntimeError("persist_events is down")

    assert poller.poll(0.0, broken) == 0
    assert poller.counters["emit_error"] == 1
    assert poller.counters["duplicate"] == 0            # the key was GIVEN BACK, not held
    assert "FAILED TO EMIT" in (poller.progress_signal or "")

    seen = []
    assert poller.poll(1.0, seen.append) == 1, "the entry was lost after a transient failure"
    assert poller.progress_signal is None               # recovery clears the signal


def test_unreadable_window_is_signalled_not_reported_as_an_empty_calendar():
    def boom():
        raise OSError("finnhub unreachable")

    poller = CalendarPoller(boom)
    assert poller.poll(0.0, lambda _e: None) == 0
    assert poller.counters["fetch_error"] == 1
    assert "FETCH FAULT" in (poller.progress_signal or "")


def test_one_malformed_entry_never_kills_the_batch():
    class Hostile(dict):
        def get(self, key, default=None):
            raise ValueError("hostile entry")

    poller = CalendarPoller(lambda: [Hostile(), ENTRIES[0]])
    seen = []
    assert poller.poll(0.0, seen.append) == 1
    assert poller.counters["build_error"] == 1
    assert seen[0]["symbols"] == ["AAPL"]


@pytest.mark.parametrize("bad_now", [0.0, 1e9, -1.0])
def test_now_is_opaque_to_the_poller(bad_now):
    """``poll``'s ``now`` is MONOTONIC seconds and must influence nothing here — no date, no
    bound, no cursor (ingest/clock_contract.py)."""
    poller = CalendarPoller(lambda: list(ENTRIES))
    assert poller.poll(bad_now, lambda _e: None) == 2
