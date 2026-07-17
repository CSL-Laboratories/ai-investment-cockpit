"""THE CLOCK CONTRACT — ONE convention, ONE error type, ONE validator, for EVERY wall-clock seam.

WHY THIS MODULE EXISTS (read before adding a third seam)
--------------------------------------------------------
``poll(now, ...)``'s ``now`` is MONOTONIC SECONDS (``ingest/ratelimit.py:2``: "the runner passes
monotonic ``now`` seconds") — the elapsed-time reading ``TokenBucket`` consumes, NOT an epoch wall
clock. ``datetime.fromtimestamp(time.monotonic())`` is ~1970-01-01. Any poller that needs a
CALENDAR bound (a poison clamp, a cold-start ceiling) therefore cannot derive it from ``now`` and
must take an INJECTED wall clock instead — defaulted to real UTC, so the bound is never opt-in.

The two connectors that need this (``ingest/edgar_brew.py``, ``ingest/finra_short_interest.py``)
each solved it separately, and EVERY TIME they drifted the drift was SILENT:

  * round 4: edgar_brew's ``poll`` derived the bound from ``now`` -> a 1970 cursor -> a dead feed
    that looked like a quiet day. FINRA had solved it; edgar_brew had not.
  * round 6: the fix landed on edgar_brew ONLY. FINRA's bound was guarded by
    ``if isinstance(today, date) and candidate > today`` — so a non-date clock made the test False
    and the ENTIRE poison bound was SKIPPED: no raise, no counter, no signal, and one row dated
    2099-01-01 pinned the cursor at 2099 forever. Meanwhile BOTH docstrings claimed the two seams
    were "the same convention, same resolution". A guard that is present, documented as loud, and
    silent in fact is WORSE than an absent one: the next session reads the cross-reference and
    believes the other side is covered.

So the contract does not live in a docstring in each module any more. It lives HERE, as CODE that
both seams CALL. One error type (two would let the same drift recur under two names), one
validator, one answer to "what does a clock return?".

THE CONTRACT (there is exactly one, and it is not per-seam)
-----------------------------------------------------------
    A wall-clock seam is a zero-argument callable returning a TIMEZONE-AWARE ``datetime``.

Anything else RAISES ``ClockContractError`` at the seam, on the first poll, before any work:

  * a NUMBER (``clock=time.monotonic``, ``clock=lambda: now``) — the original defect;
  * a bare ``date`` — it names no instant, and "midnight of which zone?" is exactly the ambiguity
    that lets two seams read one contract two ways;
  * a string, ``None``, anything else — not a clock;
  * a NAIVE ``datetime`` (``datetime.now()``, no tz) — it is a reading of an UNKNOWN zone. Treating
    it as UTC silently mis-clamps by the local offset in BOTH directions: a naive 09:00 local
    (=13:00Z) clamps a legitimate 13:00Z row backwards, and a naive UTC+X reading UNDER-fires the
    clamp so poison passes. Requiring awareness is the only reading with one answer.

AWARE datetime IS THE WHOLE CONTRACT — SEAMS NARROW, THEY DO NOT REDEFINE. A seam that wants a
calendar day says ``resolve_wall_clock(...).astimezone(timezone.utc).date()``; a seam that wants an
instant uses it as-is. Narrowing happens AFTER validation, in UTC, explicitly. This is what makes
the two pollers literally interchangeable:

    clock = lambda: datetime.now(timezone.utc)
    EdgarBrewPoller(fetch, clock=clock)
    FinraShortInterestPoller(fetch, clock=clock)      # the SAME object. Both work.

That reading used to raise an UNCAUGHT ``TypeError`` inside FINRA's comparison (``datetime`` is a
subclass of ``date``, so its isinstance gate passed and ``date > datetime`` then exploded) —
because edgar_brew wanted a datetime and FINRA wanted a date while the docs called them one
convention. They are one convention now, enforced in one place.

VALIDATE FIRST, THEN COMPARE — UNCONDITIONALLY. The comparison a clock feeds must never sit behind
an ``isinstance`` test of that clock's own output: a bad clock then disables the control instead of
tripping it. That is how a poison bound became a no-op. Resolve at the TOP of ``poll``, raise
there, and let the comparison below be unguarded.
"""
from __future__ import annotations

from datetime import datetime, timezone


class ClockContractError(TypeError):
    """A wall-clock seam returned something that is not a tz-aware datetime.

    RAISED, never swallowed, never softened to a ``None`` and never quietly skipped: every
    alternative is SILENCE — a ~1970 cursor, a 2099 cursor, or a bound that simply did not run —
    and silence from these feeds is indistinguishable from "there was nothing to report". Silence
    IS the bug class, so a violation must be the loudest thing in the room.

    Subclasses ``TypeError`` because that is precisely what it is: a type-contract violation at a
    clock seam. There is exactly ONE of these in the repo, on purpose — a second error type for one
    contract is how the two seams drifted apart the first time.
    """


def utc_now() -> datetime:
    """The DEFAULT wall clock for every seam: real, tz-AWARE UTC.

    Every poller defaults to this so its calendar bound is ON BY DEFAULT. A control you enable by
    typing an argument is not a control.
    """
    return datetime.now(timezone.utc)


def resolve_wall_clock(clock, *, seam: str) -> datetime:
    """Call ``clock`` and return its reading IFF it is a tz-aware datetime; else RAISE.

    ``seam`` names the injection point (e.g. ``"EdgarBrewPoller(clock=...)"``) so the failure says
    which wire is wrong. Call this at the TOP of ``poll``, before any other work — see "VALIDATE
    FIRST" above. Never guard a poison bound behind a type test of the clock's own output.
    """
    wall = clock()
    if not isinstance(wall, datetime):
        raise ClockContractError(
            f"{seam} must return a timezone-aware datetime, got {type(wall).__name__} "
            f"({wall!r}). In this repo a bare number is a MONOTONIC reading "
            "(ingest/ratelimit.py:2), NOT an epoch — a date derived from one is ~1970, which "
            "clamps the cursor to 1970 and kills the feed SILENTLY. A bare date names no instant. "
            "See ingest/clock_contract.py — ONE contract, both connectors."
        )
    if wall.tzinfo is None or wall.utcoffset() is None:
        raise ClockContractError(
            f"{seam} returned a NAIVE datetime ({wall!r}): it is a reading of an UNKNOWN zone. "
            "Reading it as UTC mis-clamps by the local offset in BOTH directions — a naive local "
            "reading ahead of UTC clamps legitimate rows backwards, one behind UTC lets poison "
            "through. Return datetime.now(timezone.utc), or attach the real tzinfo. See "
            "ingest/clock_contract.py."
        )
    return wall
