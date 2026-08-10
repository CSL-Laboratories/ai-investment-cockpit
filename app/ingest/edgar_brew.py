"""W20 — EDGAR evidence feed sourced from edgar-morning-brew's deterministic Delta layer.

The cockpit does NOT fetch sec.gov. The brew (``C:/Users/drfoo/GitHub/edgar-morning-brew``)
owns EDGAR fetching, the fair-access limiter, and the error taxonomy
(``SecFairAccessHalt`` -> halt + alert with ZERO retries, ``SecTransportError`` -> bounded
backoff, ``SecHttpError``). A SECOND process polling sec.gov independently would risk a
fair-access breach against the same declared identity, so this module consumes the brew's
already-fetched, LLM-FREE, byte-anchored Delta/Citation rows out of its local SQLite file
and normalizes them through the EXISTING ``ingest.normalize.to_market_event``.

Shape follows ``ingest.finnhub_poll.FinnhubPoller``: an injected ``fetch`` closure, a
``poll(now, on_event) -> int``, per-item try/except so one bad row never kills the batch,
and a cursor that advances ONLY from EMITTED items. ``now`` is MONOTONIC seconds and is OPAQUE
here — see "THE CLOCK CONTRACT" below.

DELIBERATE DEVIATION FROM THE TEMPLATE — no ``TokenBucket``
-----------------------------------------------------------
``FinnhubPoller`` carries a ``TokenBucket`` because it calls a rate-limited external HTTP
API. This poller reads a local SQLite file that the brew already populated; there is no
external quota to exceed and no counterparty to be fair to. A limiter here would buy
nothing and would actively HURT: the brew's own docs call the "rate-denied poll returns 0"
pattern EXACTLY THE ANTI-PATTERN, because a throttled feed then masquerades as a quiet day.
The absence of a bucket is a decision, not an omission. Freshness is instead established
positively by ``read_brew_run_outcome`` (below), which is the correct control for this feed.

EVENT UNIT — ONE MarketEvent PER ACCESSION, NOT PER DELTA
---------------------------------------------------------
A filing produces MANY deltas. ``workflows/budget.py:27`` and ``workflows/triage_loop.py:47``
both hold ``_HARD_EVENT_TYPES = frozenset({"halt", "filing"})``, and
``budget.py:234`` auto-corroborates on EXACT membership. Emitting one event per delta would
multiply a single filing's corroboration N-fold. So delta rows are grouped by
``accession_no`` and exactly one event is emitted per accession.

Because the membership test is EXACT, ``event_type`` is the canonical bare string
``"filing"`` — NEVER ``"filing:8-K"``, which would silently fall out of the frozenset and
lose hard-event treatment. The granular form lives in ``raw["form"]``.

THE SEC-13 AUDIT BOUNDARY — read this before trusting the chain
---------------------------------------------------------------
``normalize.content_hash`` (``normalize.py:60``) hashes WHATEVER dict is passed as ``raw=``.
Passing a brew record therefore binds the cockpit's audit chain to THE BREW'S RECORD, not
to the SEC bytes. The real chain is:

    cockpit content_hash
      -> brew Delta byte-anchor (accession_no + snippet_start_offset/snippet_end_offset)
        -> brew RawStore SHA-256
          -> the fetched SEC bytes

That IS a real chain, but it is ONE LINK LONGER than a direct EDGAR poller's would be, and
the added link lives in ANOTHER REPO'S TRUST DOMAIN — a cockpit-side audit replay can prove
"this is what the brew recorded", not "this is what the SEC served", without also trusting
the brew's RawStore. To keep the anchor independently re-checkable, ``raw`` carries the
accession, the edgar_url, and the snippet byte offsets for every delta. Anyone can re-fetch
the primary document and re-slice those offsets.

RESIDUAL 2 (NAMED, BOUNDED, COCKPIT-UNVERIFIABLE) — cik -> ticker ATTRIBUTION IS TRUSTED
----------------------------------------------------------------------------------------
The byte-anchor chain above covers the filing's CONTENT. It does NOT cover WHO the filing is
about. Symbol attribution travels through the brew's ``company`` table (``cik -> ticker``,
joined in ``_SELECT``), and that table is NOT part of the anchor: a tampered/stale company row
re-attributes a real, byte-verifiable filing to a DIFFERENT ticker, and the documented re-check
STILL PASSES, because the accession + snippet offsets do re-slice correctly against the SEC
bytes. Nothing in this repo can detect that — the cockpit never fetches sec.gov and therefore
has no independent cik->ticker mapping to compare against.

So the brew's ``company`` table is a TRUSTED INPUT, on the same footing as its RawStore. This
is inherent to consuming another repo's data and is NOT fixable here; inventing a cockpit-side
"verification" that re-reads the same untrusted table would be theater. It is disclosed rather
than repaired. The bound on the residual: it can only mis-ATTRIBUTE a filing that genuinely
exists (a ticker failing ``normalize._TICKER`` is dropped and counted, never guessed), and the
downstream SEC-14 grounding corroboration still requires the proposal's symbol to be a tagged
symbol of a cited item. Closing it would require an attribution source outside the brew's trust
domain; that is out of scope for W20 and is a deliberate, recorded acceptance.

THE HEAD-OF-LINE WEDGE — TWO SOUND PROPERTIES WHOSE INTERACTION IS NOT
-----------------------------------------------------------------------
Two facts, each individually correct and each individually TESTED:

  (1) the cursor advances ONLY from EMITTED items (deliberate — a failed emit must never skip
      a row; pinned by ``test_cursor_advances_only_from_emitted_items``), and
  (2) unattributable/invalid rows are DROPPED with a counter (deliberate — a row that cannot
      be attributed must never manufacture "filing" corroboration under a guessed key).

Their INTERACTION is a permanent wedge. If a fetch window FILLS (``len(rows) == limit``) with
rows that can never be emitted, ZERO are emitted, so the cursor NEVER advances, so the SAME
window is re-fetched forever and no later row can ever enter it. The EDGAR feed is DEAD — and
``read_brew_run_outcome`` reports ``trustworthy=True`` throughout, because it measures THE
BREW'S liveness, not THE POLLER'S progress. A keyless writer inside the brew's trust domain
can therefore kill this feed silently while the feed's own freshness control says "current".
Neither property is wrong; their COMPOSITION is. Do not "simplify" either one in isolation.

FORWARD PROGRESS IS THEREFORE AN INVARIANT, NOT AN EMERGENT PROPERTY:

  * THE SIGNATURE is a poll that fetched a FULL window and emitted ZERO. ``make_brew_fetch``
    returns a ``BrewBatch`` carrying ``saturated`` (``len(rows) >= limit``) so the poller can
    OBSERVE it; ``len(rows) == limit`` was previously computed nowhere and returned to nobody.
  * ON THE SIGNATURE the cursor JUMPS past the window (``_safe_jump``) instead of wedging. Every
    row it skips was either DROPPED WITH A COUNTER or DEDUP-SKIPPED as already-emitted, so this
    is not a silent drop — it is Inv-7-compliant progress.
  * ``progress_signal`` records it, distinct from ``strand_signal`` (which covers only the
    future-timestamp clamp) and distinct from a quiet day. The poller's own progress must be
    observable HERE, because ``read_brew_run_outcome`` structurally CANNOT see it.

DROPPED vs DEDUP-SKIPPED rows in a jumped window — WHAT WE CHOSE AND WHY
------------------------------------------------------------------------
A saturated all-unemitted window mixes two kinds of unemittable row, and they are NOT the same
event:

  * DROPPED (invalid/unattributable). A writer in the brew's trust domain is killing the feed.
    Operational ALARM.
  * DEDUP-SKIPPED (the accession's event already went out). This is reachable in NORMAL
    operation, not just under attack: a group is truncated to ``MAX_DELTAS_PER_EVENT`` BEFORE
    its cursor is taken, so deltas 201..N of a legitimately-emitted accession are re-fetched
    forever and deduped. Benign catch-up.

We JUMP PAST BOTH, because both are unemittable BY CONSTRUCTION — a dedup skip is positive
proof the accession's event was already emitted, so the row can never carry new evidence and
skipping it loses nothing. Refusing to jump the deduped case would re-create the identical
permanent wedge under a friendlier name. But we COUNT and SIGNAL them APART: an all-deduped
window advances QUIETLY (``saturated_all_deduped``, no ``progress_signal``) — treating benign
catch-up as a wedge would cry wolf — while a window containing DROPS raises the alarm. A
window we cannot safely jump (``wedge_blocked``) always alarms, whatever it contained.

A FAILED EMIT IS NOT A DROP — THE JUMP MUST REFUSE TO SKIP IT
-------------------------------------------------------------
The jump above is only Inv-7-compliant because every row it skips is unemittable BY
CONSTRUCTION. An ``emit_error`` is NOT that. It is a TRANSIENT downstream failure (persist_events
down, disk full, a momentary DB lock) whose row is perfectly good evidence and WILL emit on a
later poll. Composing "dedup key committed before the emit succeeded" + "emit_error counted as a
terminal drop" + "the saturated window jumps past drops" would make a downstream outage
SILENTLY AND PERMANENTLY DESTROY filings: the key is burnt so the row is never retried, and the
jump moves past it forever, while the counters record a tidy "drop" and the feed reports
progress. That defeats Inv 7 with the very mechanism meant to protect forward progress. So:

  * THE DEDUP KEY IS COMMITTED ONLY BY A SUCCESSFUL EMIT. ``Dedup.add`` is a check-and-set, so
    it must happen BEFORE ``on_event`` (there is no race-free peek); when ``on_event`` raises we
    ROLL THE KEY BACK via ``Dedup.discard``. The key means "this WAS emitted", never "we tried".
  * ``emit_error`` IS RETRIABLE AND IS NOT A DROP. It is counted apart from the STRUCTURAL drops
    (``bad_accession`` / ``no_ticker`` / ``symbol_less`` / ``non_canonical_ts`` /
    ``build_error``) and is deliberately NOT in ``_DROP_KEYS``. Structural drops are deterministic
    in the row — retrying one forever IS the wedge — so those remain terminal and jumpable.
    ``_build`` raising is structural for exactly that reason (it is a pure function of the row);
    only ``on_event`` raising is transient.
  * THE CURSOR MAY NOT ADVANCE TO OR PAST A ROW WE FAILED TO EMIT, even if a LATER accession in
    the same window emitted fine — the filter is ``created_at > :cursor``, so such an advance
    would drop the failed row out of every future window. The failed group's EARLIEST cursor is
    a BARRIER; only emitted cursors strictly below it may move the cursor.
  * A SATURATED WINDOW HOLDING AN emit_error MUST NOT JUMP. It WEDGES, loudly (``wedge_blocked``
    + ``progress_signal``). A REPORTED WEDGE BEATS SILENT EVIDENCE LOSS: a downstream outage
    should stop the feed and SAY SO, not quietly discard filings until it recovers. The
    round-3 distinctions survive intact — an all-DEDUPED saturated window still advances
    QUIETLY (normal operation), a window with STRUCTURAL drops still jumps. ONLY emit_error blocks.

THE ROW-CANONICALITY INVARIANT — WHY WE VALIDATE created_at AND NEVER "FIX" IT
------------------------------------------------------------------------------
``_SELECT`` compares the cursor to the stored ``created_at`` as RAW TEXT, lexicographically. The
poller compares cursors with Python's ``>``. Those two comparators agree ONLY IF every cursor is
BYTE-IDENTICAL to what SQL sees — i.e. only if the row's ``created_at`` is ALREADY in the brew's
storage format. ``_canonical_cursor`` fixed the format of values the poller CONSTRUCTS, but
SQLite is dynamically typed and enforces no DATETIME shape, so a keyless writer in the brew's
trust domain can write ``created_at`` in ANY text shape. Every cursor the poller takes
(``_build``'s ``max(cursors)``, ``poll``'s ``window_cursors``, ``_safe_jump``) is drawn from a
ROW, so a non-canonical row makes the two comparators disagree and rows are skipped or re-fetched
forever.

So the invariant is enforced where rows ENTER the poller (``_row_cursor``): a row whose
``created_at`` is not already canonical is a STRUCTURAL drop through the counted path
(``non_canonical_ts``) — exactly like a bad accession. It is a MALFORMED BREW ROW.

DO NOT "HELPFULLY" CANONICALIZE IT. Turning "2026-07-15T13:01:00+00:00" into
"2026-07-15 13:01:00.000000" and using THAT as the cursor re-introduces the very disagreement:
the canonicalized cursor is no longer the text SQL compares against, so ``created_at > :cursor``
answers a different question than the poller's ``>``. The ONLY safe cursor is a value already
byte-identical to what SQL sees; anything else must be dropped, counted, and never become one.

THE CLOCK CONTRACT — ``now`` IS MONOTONIC AND MAY NEVER BECOME A DATE
---------------------------------------------------------------------
THE CONTRACT ITSELF LIVES IN CODE, IN ``ingest/clock_contract.py`` — one error type, one
validator (``resolve_wall_clock``), called by BOTH this module and the sibling connector
``ingest/finra_short_interest.py``. It is summarised here; it is ENFORCED there.

It used to be stated in prose in both modules and implemented separately in each, and every time
they drifted the drift was SILENT: round 4 left edgar_brew deriving the bound from ``now`` (-> a
1970 cursor) while FINRA was correct; round 6 fixed edgar_brew ONLY, and FINRA's bound sat behind
``if isinstance(today, date) and ...``, so a mis-wired clock made that test False and the ENTIRE
poison bound was SKIPPED — no raise, no counter, no signal — while both docstrings advertised "the
same convention, same resolution". Two pollers, one convention: do not re-derive it a third time,
do not solve it differently per module, and do NOT define a second error type.

THE CONTRACT: a clock seam is a zero-arg callable returning a TZ-AWARE ``datetime``. A number, a
bare ``date``, a string, ``None``, or a NAIVE datetime each raise ``ClockContractError`` at the
top of ``poll``, before any work. Seams NARROW that reading (FINRA takes ``.astimezone(utc)
.date()``); they never redefine it. So ONE clock object drives BOTH pollers:
``clock = lambda: datetime.now(timezone.utc)`` works in ``EdgarBrewPoller(clock=...)`` and in
``FinraShortInterestPoller(clock=...)`` alike.

``poll(now, ...)``'s ``now`` is MONOTONIC SECONDS (``ingest/ratelimit.py:2``: "the runner passes
monotonic ``now`` seconds") — the elapsed-time clock ``TokenBucket`` consumes, NOT an epoch wall
clock. ``datetime.fromtimestamp(time.monotonic())`` is ~1970-01-01. This poller deliberately
carries no ``TokenBucket`` (see above), so ``now`` is OPAQUE here: it is forwarded to the injected
``fetch`` and used for NOTHING else. Nothing may derive a date, a bound, or a cursor from it.

The future-timestamp clamp DOES need a calendar, so a calendar clock is INJECTED —
``EdgarBrewPoller(clock=...)``, defaulting to real UTC so the clamp is ON BY DEFAULT and not
opt-in. That is exactly what ``FinraShortInterestPoller(clock=...)`` does for its cold-start
bound: same convention, same resolution, same validator — ``clock_contract.resolve_wall_clock``,
called by both. Not "stated in both places": CALLED from both places. A cross-reference is a
claim; a shared call is a fact.

THE VIOLATION IS LOUD, NOT SILENT. ``poll`` used to compute ``_canonical_cursor(now)``: hand it a
monotonic float and it yielded "1970-01-01 ...", the clamp then pinned the cursor at 1970, and the
feed was DEAD — quietly. A 1970 cursor is indistinguishable from a dead feed, which is the exact
failure class this module exists to prevent, reintroduced by the control meant to prevent it. So:

  * the clamp bound comes from ``clock``, never from ``now`` (``_wall_clock_cursor``), and a
    ``clock`` that returns a NUMBER (i.e. someone wired ``clock=time.monotonic`` or
    ``clock=lambda: now``) raises ``ClockContractError`` on the first poll;
  * ``_canonical_cursor`` NO LONGER COERCES NUMBERS AT ALL — an int/float RAISES. The epoch branch
    had no legitimate caller left (every cursor is a storage-format string taken from a row, and
    the bound comes from ``clock``), and an unreachable coercion that silently produces 1970 is a
    trap for the next session, not a feature. Its removal is what makes "someone re-derived a date
    from ``now``" fail at the first test instead of in production silence.

Nothing here imports ``brokers`` or touches the order path (Invariant 1). This module is
pure: all sqlite3 I/O lives in ``make_brew_fetch``.

Requires ``"sec:edgar"`` in ``ingest.normalize.ALLOWED_SOURCES`` (``normalize.py:18``);
``to_market_event`` raises ``ValueError`` on a non-allowlisted source (``normalize.py:67-68``).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

from ingest.clock_contract import ClockContractError, resolve_wall_clock, utc_now
from ingest.dedup import Dedup
from ingest.normalize import MAX_LEN, _canonical_ticker, to_market_event

SOURCE = "sec:edgar"
EVENT_TYPE = "filing"          # canonical bare string — budget.py:27 / triage_loop.py:47 frozenset
MAX_DELTAS_PER_EVENT = 200     # bound the per-accession fan-in carried into raw
SNIPPET_JOIN = " | "

# SEC accession numbers are fixed-format: 10-digit filer prefix, 2-digit year, 6-digit seq.
# Anything else is malformed/spoofed and is DROPPED — never emitted under a guessed key.
#
# EXPLICIT [0-9], NOT \d — LOAD-BEARING, DO NOT "SIMPLIFY" BACK TO \d.
# Python's \d matches every Unicode decimal digit (Nd), so '٠٠٠٠٠٠٠٠٠٠-٢٤-٠٠٠١٢٣' (Arabic-Indic)
# and '００００００００００-２４-０００１２３' (fullwidth) both SATISFY a \d-based pattern. Such a string
# would then be accepted as a "valid accession", become the event's article_id, and land in
# raw_ref — which is interpolated into a prompt attribute downstream (workflows/grounding.py
# extract_slice). This filter is the PRIMARY control that keeps raw_ref free of quotes/angle
# brackets, so it must match ASCII digits ONLY.
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$", re.ASCII)


# ClockContractError is RE-EXPORTED, NOT redefined: it is declared once in
# ingest/clock_contract.py and shared with ingest/finra_short_interest.py. Two error types for one
# contract is precisely how the two seams drifted apart — `except ClockContractError` would then
# catch one module's violation and miss the other's. There is ONE. (Imported above; still exported
# from this module's namespace, so `from ingest.edgar_brew import ClockContractError` keeps working.)

# The DEFAULT wall clock for the future-timestamp clamp (tz-aware UTC), injectable via
# ``EdgarBrewPoller(clock=...)`` and SHARED with FINRA's cold-start bound — same function, so the
# two seams cannot default differently. ``poll``'s ``now`` CANNOT serve (it is MONOTONIC seconds —
# see "THE CLOCK CONTRACT").
_utc_now = utc_now


# --------------------------------------------------------------------------- feed status

class BrewFeedStatus(str, Enum):
    """The brew's four run outcomes, plus the two absences the brew cannot report itself.

    Slugs mirror ``ArtifactWriter._CONTENT`` in the brew's ``app/scheduler/morning_run.py``
    exactly; the FILENAME is the signal (the file body is a fixed one-liner carrying no data).
    """

    PENDING_APPROVAL = "pending-approval"    # brew ran, produced material
    NOTHING_MATERIAL = "nothing-material"    # brew ran, LEGITIMATE quiet day: 0 events is CORRECT
    DEGRADED = "degraded"                    # brew run degraded/FAILED (e.g. SecFairAccessHalt)
    NOOP = "noop"                            # already processed; NOT a quiet day
    NOT_RUN = "not-run"                      # no artifact for the day: brew has not run
    NOT_PROVISIONED = "not-provisioned"      # brew DB absent entirely


_ARTIFACT_STATUSES = (
    BrewFeedStatus.PENDING_APPROVAL,
    BrewFeedStatus.NOTHING_MATERIAL,
    BrewFeedStatus.DEGRADED,
    BrewFeedStatus.NOOP,
)

# Probe order is FAIL-CLOSED, deliberately NOT the declaration order: if two slugs somehow
# exist for one day (a crash mid-rewrite, a clock/timezone overlap, a hand-copied artifact),
# the WORST outcome must win. Reporting "nothing-material" while a "degraded" artifact sits
# in the same directory is exactly the silence-as-good-news failure this module exists to stop.
_PROBE_ORDER = (
    BrewFeedStatus.DEGRADED,
    BrewFeedStatus.NOOP,
    BrewFeedStatus.PENDING_APPROVAL,
    BrewFeedStatus.NOTHING_MATERIAL,
)


@dataclass(frozen=True)
class BrewRunOutcome:
    """Whether this feed's silence means 'nothing happened' or 'we do not know'.

    A DEGRADED, NOT-RUN, or NOT-PROVISIONED feed must NEVER be indistinguishable from
    ``nothing-material``. The brew deliberately separates its outcomes (its own module
    docstring: a replay "is a ``noop`` -- never relabelled as a quiet day"), and the cockpit
    inherits that distinction rather than flattening it to a bool.

    Fails closed: ``quiet_day`` is True for exactly ONE status. Every other status leaves
    ``stale=True`` or ``quiet_day=False`` with a ``reason`` a caller cannot misread as good
    news. This does not raise — the status IS the return value.

    The consequence-wiring (E-08 treats staleness as a HARD risk fail) belongs to W15. This
    only makes the distinction AVAILABLE and unmissable.
    """

    status: BrewFeedStatus
    day: str
    artifact_path: str | None
    ran: bool          # the brew produced an artifact for this day
    stale: bool        # the feed CANNOT be trusted as current
    quiet_day: bool    # 0 events is CORRECT and expected (nothing-material ONLY)
    reason: str

    @property
    def trustworthy(self) -> bool:
        """True iff a caller may act on this feed's contents (or emptiness) for the day."""
        return not self.stale


_REASONS = {
    BrewFeedStatus.PENDING_APPROVAL:
        "brew ran and produced material pending approval; feed is current",
    BrewFeedStatus.NOTHING_MATERIAL:
        "brew ran and found nothing material; zero events is CORRECT for this day",
    BrewFeedStatus.DEGRADED:
        "brew run DEGRADED/FAILED (e.g. SecFairAccessHalt); feed is STALE — "
        "zero events does NOT mean nothing happened",
    BrewFeedStatus.NOOP:
        "brew replayed an already-processed day; NOT a quiet day and NOT new evidence",
    BrewFeedStatus.NOT_RUN:
        "no brew artifact for this day: the brew HAS NOT RUN; feed is UNKNOWN/STALE — "
        "zero events does NOT mean nothing happened",
    BrewFeedStatus.NOT_PROVISIONED:
        "brew database absent, or present but not a readable SQLite file (a directory, an "
        "empty placeholder, junk): the feed is NOT PROVISIONED; feed is STALE — "
        "zero events does NOT mean nothing happened",
}


def _outcome(status: BrewFeedStatus, day: str, artifact: str | None) -> BrewRunOutcome:
    return BrewRunOutcome(
        status=status,
        day=day,
        artifact_path=artifact,
        ran=status in _ARTIFACT_STATUSES,
        stale=status in (BrewFeedStatus.DEGRADED, BrewFeedStatus.NOT_RUN,
                         BrewFeedStatus.NOT_PROVISIONED),
        quiet_day=status is BrewFeedStatus.NOTHING_MATERIAL,
        reason=_REASONS[status],
    )


# A provisioned brew DB is a READABLE FILE whose first bytes are SQLite's magic header.
#
# WHY NOT exists() — THIS IS THE ROUND-1 LESSON, GENERALIZED. ``exists()`` is True for a
# DIRECTORY, for a ZERO-BYTE file, and for a text file — none of which is a brew. So
# ``mkdir edgar_morning_brew.db`` (or an interrupted copy, or a placeholder someone touched)
# would pass the NOT_PROVISIONED gate that this module's docstring calls "checked FIRST" and
# says "outranks any artifact question" — and a <day>-nothing-material.txt would then return
# the good-news tuple quiet_day=True / stale=False. That tuple is the exact thing this module
# exists to make unreachable, so the probe must answer "is this a brew?", not "is this a name
# that resolves?".
#
# The header is read as BYTES. We never import the brew (its tree is STRICTLY READ-ONLY and
# never on our import path) and never open a connection: ``sqlite3.connect`` would happily
# CREATE a file at a bogus path, which is the opposite of a provisioning check. Any OSError
# fails CLOSED to not-provisioned.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_provisioned_db(db_path) -> bool:
    """True iff ``db_path`` is a real, readable SQLite file — the ONE provisioning predicate.

    Both call sites (``read_brew_run_outcome`` and ``make_brew_fetch``'s fetch) use this, so
    the status and the rows can never disagree about whether the feed exists.
    """
    try:
        path = Path(db_path)
        if not path.is_file():          # is_file(), NOT exists(): a directory is not a brew
            return False
        with path.open("rb") as fh:
            return fh.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except (OSError, ValueError, TypeError):
        return False                    # unreadable/unrepresentable -> fail closed


class _Unset:
    """Sentinel: ``db_path`` was OMITTED (not 'no DB configured' — simply not passed).

    Distinct from ``None``. Omission is a CALLER BUG, and the safe reading of a caller bug is
    NOT_PROVISIONED, never quiet_day. See ``read_brew_run_outcome``.
    """


UNSET = _Unset()


def read_brew_run_outcome(runs_dir, day, *, db_path=UNSET) -> BrewRunOutcome:
    """Read the brew's atomic run artifact for ``day`` and classify the feed's freshness.

    ``runs_dir`` is the brew's ``<output_dir>/runs`` directory; the brew's ``ArtifactWriter``
    writes ``<YYYY-MM-DD>-<slug>.txt`` there via mkstemp + fsync + os.replace, so a filename
    seen here is always a COMPLETE artifact (never a half-written one).

    ``db_path`` is REQUIRED and is checked FIRST: a DB that is not provisioned is
    NOT_PROVISIONED, which outranks any artifact question — the feed cannot have content at all.

    "PROVISIONED" MEANS A READABLE SQLITE FILE, NOT A PATH THAT RESOLVES. See
    ``_is_provisioned_db``: a directory, a zero-byte file and a non-SQLite file are each
    NOT_PROVISIONED, because none of them can hold a delta. A weaker ``exists()`` check let a
    ``mkdir``-able name satisfy the gate and hand back quiet_day=True.

    THE SAFE BEHAVIOUR IS THE DEFAULT, NOT OPT-IN. ``db_path`` was previously optional, which
    meant that simply OMITTING it silently disabled this module's advertised freshness control:
    a ``<day>-nothing-material.txt`` then returned ``quiet_day=True`` even with the brew DB
    absent from disk entirely — i.e. the one status that means "0 events is CORRECT" was
    reachable by forgetting an argument. A control you can turn off by not typing it is not a
    control. Omission is now the sentinel ``UNSET`` and forces NOT_PROVISIONED (fail closed);
    pass ``db_path=None`` to state explicitly that no DB check is wanted — and even that is
    treated as NOT_PROVISIONED, because we cannot prove the feed has content without a DB.
    """
    day = _as_day(day)
    if isinstance(db_path, _Unset) or db_path is None:
        # Caller did not (or could not) tell us where the DB is: we CANNOT prove the feed is
        # provisioned, so we must not let an artifact answer the question. Fail closed.
        return _outcome(BrewFeedStatus.NOT_PROVISIONED, day, None)
    if not _is_provisioned_db(db_path):
        return _outcome(BrewFeedStatus.NOT_PROVISIONED, day, None)
    root = Path(runs_dir)
    for status in _PROBE_ORDER:                    # DEGRADED before NOTHING_MATERIAL: fail closed
        candidate = root / f"{day}-{status.value}.txt"
        # is_file(), NOT exists(): exists() is True for a DIRECTORY named
        # "<day>-nothing-material.txt", so a mkdir could forge a quiet day.
        if candidate.is_file():
            return _outcome(status, day, str(candidate))
    return _outcome(BrewFeedStatus.NOT_RUN, day, None)


def _as_day(day) -> str:
    if isinstance(day, datetime):
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    return str(day)


# --------------------------------------------------------------------------- the poller

def _as_iso(value) -> str | None:
    """Render a RAW DISPLAY timestamp (detection_ts, filing_date, a delta's created_at echo) as an
    ISO string, or None.

    NOT A CURSOR PATH — its output only ever lands in ``raw``/``ts``, never in ``self._cursor``.
    That is why it may still read an epoch out of a number while ``_canonical_cursor`` refuses to:
    a brew row's numeric timestamp IS an epoch, whereas the only bare number that could reach a
    CURSOR is ``poll``'s monotonic ``now``. Do not merge the two functions.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        return value or None
    return None


def _storage_as_iso(cursor: str | None) -> str | None:
    """BUG-084: render a canonical STORAGE-format string (``_STORAGE_FMT`` below — space-
    separated, tz-dropped-but-UTC, the shape ``max(cursors)`` carries) as the tz-aware ISO
    shape every other feed's ``ts`` uses. DISPLAY PATH ONLY — its output lands in ``ts``,
    never in ``self._cursor`` (see _canonical_cursor). An unexpected shape returns None so
    the caller's fallback chain continues rather than emitting a mis-shapen timestamp."""
    if not cursor:
        return None
    try:
        return datetime.strptime(cursor, _STORAGE_FMT).replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


# THE CURSOR'S ONE TRUE FORMAT — see _canonical_cursor. Load-bearing, do not mix formats.
#
# The brew stores every timestamp via SQLAlchemy ``DateTime(timezone=True)``. On SQLite that
# renders TEXT as "YYYY-MM-DD HH:MM:SS.ffffff" — SPACE-separated, tz-offset DROPPED. But
# ``datetime.isoformat()`` renders "YYYY-MM-DDTHH:MM:SS+00:00" — 'T'-separated, offset kept.
# ``_SELECT`` compares the cursor to that stored TEXT LEXICOGRAPHICALLY, and 'T' (0x54) sorts
# ABOVE ' ' (0x20), so ANY isoformat()-shaped cursor sorts above EVERY real row: the feed
# silently returns 0 rows forever, which is indistinguishable from a quiet day — precisely the
# failure this module exists to prevent. Every value that can become a cursor therefore passes
# through _canonical_cursor FIRST, and nothing else ever constructs one.
_STORAGE_FMT = "%Y-%m-%d %H:%M:%S.%f"


def _canonical_cursor(value) -> str | None:
    """Render ANY timestamp into the brew's SQLite STORAGE format, or None if it isn't one.

    THE SINGLE SOURCE OF CURSOR FORMAT TRUTH. Accepts a datetime or a string in either 'T'- or
    space-separated ISO form; returns naive-UTC "YYYY-MM-DD HH:MM:SS.ffffff" so that a
    lexicographic comparison against the stored TEXT is also a chronological one.

    A NUMBER RAISES ``ClockContractError`` — IT IS NOT COERCED TO AN EPOCH. See "THE CLOCK
    CONTRACT". This branch used to do ``datetime.fromtimestamp(value)``, which is only correct if
    a bare number here is an epoch — but the ONE bare number that can reach a cursor in this repo
    is ``poll``'s ``now``, which is MONOTONIC, so the coercion's only real-world effect was to turn
    a clock-contract violation into a silent 1970 cursor. It now has no legitimate caller at all
    (row cursors are storage-format strings; the clamp bound is a datetime from ``clock``), so
    keeping it as a no-op would leave a live trap for the next session to fall into. Raising is
    what pins the contract: re-derive a date from ``now`` and the tests stop, loudly.
    """
    if value is None:
        return None
    dt = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):        # bool is an int: deliberately caught here too
        raise ClockContractError(
            f"refusing to read a wall clock out of the number {value!r}: in this repo a bare "
            "number is a MONOTONIC seconds reading (ingest/ratelimit.py:2), NOT an epoch, so "
            "datetime.fromtimestamp() of one is ~1970-01-01 — and a cursor clamped to 1970 kills "
            "this feed SILENTLY, which is indistinguishable from a dead feed. Pass a datetime, or "
            "inject EdgarBrewPoller(clock=...). See 'THE CLOCK CONTRACT' in this module docstring."
        )
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)      # handles BOTH 'T' and ' ' separators, +offset
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)   # storage drops the offset
    return dt.strftime(_STORAGE_FMT)


def _row_cursor(raw) -> str | None:
    """A row's ``created_at`` IF it is ALREADY a canonical storage-format cursor, else None.

    THE BOUNDARY WHERE THE CANONICALITY INVARIANT IS ENFORCED (see the module docstring). The
    test is EQUALITY against ``_canonical_cursor``, not "can it be parsed": ``_SELECT`` compares
    the cursor to the stored ``created_at`` as RAW TEXT, so the only value that makes SQL's ``>``
    and the poller's ``>`` the same question is one that is byte-identical to what SQL sees.

    Returning None here means "this row may NEVER become a cursor" — the caller drops it through
    the COUNTED path. Never canonicalize a non-canonical value into a cursor: that hands SQL one
    comparator and the poller another, which is the strand this invariant exists to prevent.
    """
    if type(raw) is not str:
        return None                      # a datetime/int is not what SQL stores or compares
    return raw if _canonical_cursor(raw) == raw else None


def _accession_of(row) -> str | None:
    acc = row.get("accession_no")
    # This is a raw identity used in article_id/raw_ref. Reject subclasses and any raw spelling
    # that would be transformed by whitespace cleanup; otherwise a forged value can alias a real
    # accession or inject text after this boundary.
    if type(acc) is not str or not acc.isascii() or acc != acc.strip():
        return None
    return acc if _ACCESSION.fullmatch(acc) else None


class BrewBatch(list):
    """A fetch result that ALSO reports whether the fetch window was FULL.

    ``len(rows) >= limit`` is the only observable that distinguishes "the feed is caught up"
    from "the window is saturated, so later rows cannot enter it until the cursor moves". A
    plain list cannot carry it, and an UNOBSERVED saturation is precisely the head-of-line wedge
    in the module docstring. Subclasses ``list``, so every existing consumer, ``isinstance``
    check and ``== []`` comparison is unaffected.
    """

    def __init__(self, rows=(), *, saturated: bool = False):
        super().__init__(rows)
        self.saturated = bool(saturated)


def _safe_jump(window_cursors: list[str]) -> str | None:
    """The furthest cursor we can PROVABLY advance to without skipping a row we never saw.

    The window is a contiguous prefix of ``ORDER BY created_at ASC, delta_id ASC`` cut by
    ``LIMIT``, and the filter is ``created_at > :cursor``. Jumping to the window's MAXIMUM
    created_at would therefore be UNSOUND: rows TIED at that exact timestamp may have fallen
    outside the LIMIT, and the jump would skip them forever, unseen and uncounted.

    Rows tied at any LOWER value cannot straddle the boundary: if such a row were outside the
    window, then every strictly-greater row would be outside it too — yet those are
    demonstrably inside it. So every row at or below the SECOND-largest distinct value was in
    this window and has been accounted for. That value is the furthest lossless jump.

    Fewer than two distinct values -> NO safe jump exists. Return None and stay wedged, LOUDLY
    (the caller alarms): a wedge we report beats a silent skip of unseen rows.
    """
    distinct = sorted(set(window_cursors))
    return distinct[-2] if len(distinct) >= 2 else None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class EdgarBrewPoller:
    """Group brew delta rows by accession and emit ONE ``filing`` MarketEvent per accession.

    ``fetch(cursor, now) -> list[dict]`` is injected (``make_brew_fetch`` is the production
    one); this class performs NO I/O and is fully driveable offline. ``cursor`` is the last
    emitted ``delta.created_at`` (None on the first poll).

    ``clock() -> datetime`` is the WALL CLOCK, injected and defaulting to real UTC. It exists
    because ``poll``'s ``now`` is MONOTONIC and therefore cannot bound a calendar comparison —
    see "THE CLOCK CONTRACT" in the module docstring, and the same seam on
    ``FinraShortInterestPoller(today=...)``. It is a DEFAULTED seam, not an opt-in: a caller who
    never mentions the clock still gets the clamp.
    """

    def __init__(self, fetch, dedup=None, clock=None):
        self._fetch = fetch
        self._dedup = dedup or Dedup()
        # The clamp's calendar bound. Defaults to a REAL wall clock so the poison guard is on by
        # default; `poll`'s monotonic `now` is never, ever used for this.
        self._clock = clock or _utc_now
        self._cursor: str | None = None
        # Attribution/validity drops are COUNTED, never guessed away and never silent.
        #   bad_accession  — accession absent/malformed/non-ASCII-digit (spoof filter)
        #   no_ticker      — no company row, or a ticker failing normalize._TICKER
        #   symbol_less    — belt-and-braces: a built event whose symbols came out EMPTY
        #   non_canonical_ts— created_at was not ALREADY in storage format (see _row_cursor)
        #   build_error    — _build raised: STRUCTURAL (a pure function of the row), terminal
        #   emit_error     — on_event raised: TRANSIENT/RETRIABLE. NOT a drop, NOT jumpable —
        #                    the dedup key is rolled back and the cursor is barred from passing
        #                    the row. See "A FAILED EMIT IS NOT A DROP" in the module docstring.
        #   future_ts      — an emitted row was timestamped after `now` (poison)
        #   clamped_cursor — the cursor was clamped to `now` rather than to a real row
        # Forward-progress counters (the head-of-line wedge — see the module docstring):
        #   saturated_no_emit    — a FULL window emitted ZERO: the wedge signature
        #   saturated_all_deduped— ...and every unemitted row was already-emitted (benign)
        #   window_jumped        — the cursor was jumped past such a window to keep the feed alive
        #   wedge_blocked        — the signature fired but NO safe jump existed: still wedged
        self.counters = {"bad_accession": 0, "not_a_dict": 0, "no_ticker": 0, "symbol_less": 0,
                         "non_canonical_ts": 0, "build_error": 0,
                         "emit_error": 0, "future_ts": 0, "clamped_cursor": 0,
                          "saturated_no_emit": 0, "saturated_tie_held": 0,
                          "saturated_all_deduped": 0,
                         "window_jumped": 0, "wedge_blocked": 0}
        # A clamp is NOT a quiet day. Silence caused by clamping must be readable as such, so
        # the last clamp leaves a durable, human-readable signal rather than only a count.
        self.strand_signal: str | None = None
        # A WEDGE is not a quiet day either — and it is a DIFFERENT thing from a clamp, so it
        # gets its own signal rather than overloading strand_signal. Set when a full window
        # emits zero; CLEARED by any poll that makes forward progress, so it always reads as
        # the feed's CURRENT progress state rather than as ancient history.
        self.progress_signal: str | None = None

    @property
    def cursor(self) -> str | None:
        return self._cursor

    # STRUCTURAL drops ONLY — deterministic in the row, therefore terminal and jumpable.
    # `emit_error` is DELIBERATELY ABSENT: it is a transient downstream failure, the row is good
    # evidence, and it WILL be retried. Counting it here would let the saturated-window jump skip
    # a row that was never dropped at all — silent evidence loss. Do not "tidy" it back in.
    _DROP_KEYS = ("bad_accession", "not_a_dict", "no_ticker", "symbol_less", "non_canonical_ts", "build_error")

    def _drop_total(self) -> int:
        return sum(self.counters[k] for k in self._DROP_KEYS)

    def _advance_to(self, candidate: str | None) -> bool:
        """Move the cursor forward to ``candidate``. Returns whether it actually moved."""
        if candidate is None:
            return False
        if self._cursor is None or candidate > self._cursor:
            self._cursor = candidate
            return True
        return False

    def _wall_clock_cursor(self) -> str | None:
        """The clamp's bound, in canonical cursor space, read from the INJECTED calendar clock.

        NEVER from ``poll``'s ``now`` — see "THE CLOCK CONTRACT". The clock's OUTPUT is validated
        by the SHARED ``resolve_wall_clock`` (``ingest/clock_contract.py``) — the same call FINRA's
        seam makes — so wiring ``clock=time.monotonic`` (or ``clock=lambda: now``) fails LOUDLY on
        the first poll instead of clamping every cursor to 1970 in silence, and so the two seams
        cannot drift into different notions of "a clock".

        TZ-AWARENESS IS PART OF THE CHECK, not an afterthought. A naive ``datetime.now()`` here was
        accepted and then read AS IF UTC: a naive 09:00 local (= 13:00Z) clamped a legitimate
        13:00Z row backwards, and the mirror case (naive UTC+X) UNDER-fired the clamp and let
        poison through. ``resolve_wall_clock`` refuses both.
        """
        return _canonical_cursor(resolve_wall_clock(self._clock,
                                                    seam="EdgarBrewPoller(clock=...)"))

    def poll(self, now, on_event) -> int:
        """Fetch a window, group by accession, emit ONE ``filing`` event per accession.

        ``now`` IS MONOTONIC SECONDS AND IS OPAQUE HERE. It is forwarded to the injected ``fetch``
        and used for NOTHING else. DO NOT derive a date, a bound or a cursor from it — that yields
        1970 and kills the feed silently. The clamp's calendar bound comes from the injected
        ``clock`` via ``_wall_clock_cursor``. See "THE CLOCK CONTRACT" in the module docstring.
        """
        # From the CLOCK, never from `now`. The name says wall on purpose: `now_cursor` invited
        # exactly the confusion that put a monotonic reading on this line.
        wall_cursor = self._wall_clock_cursor()
        drops_before = self._drop_total()
        batch = self._fetch(self._cursor, now)
        # A plain list cannot report fullness, so it reads as UNSATURATED. That is the SAFE
        # default: the jump below is the risky action (it skips rows), so "we were not told the
        # window was full" must never authorise it. Only make_brew_fetch, which knows the LIMIT
        # it passed to SQLite, can assert saturation.
        saturated = bool(getattr(batch, "saturated", False))

        groups: dict[str, list[dict]] = {}
        # Per-accession cursors, kept so a FAILED emit can bar the cursor from passing its
        # EARLIEST row (the whole group must stay re-fetchable to rebuild the identical event).
        group_cursors: dict[str, list[str]] = {}
        window_cursors: list[str] = []
        fetched = 0
        for row in batch:
            fetched += 1
            try:
                if type(row) is not dict:
                    self.counters["not_a_dict"] += 1
                    continue
                # THE CANONICALITY INVARIANT, enforced at the boundary rows ENTER the poller: a
                # created_at that is not ALREADY storage-format can never be a cursor (SQL
                # compares raw TEXT — see _row_cursor), so it is a MALFORMED BREW ROW and is
                # dropped through the counted path, never silently skipped and never "fixed".
                c = _row_cursor(row.get("created_at"))
                if c is None:
                    self.counters["non_canonical_ts"] += 1
                    continue
                # Collected for EVERY surviving row in the window, emittable or not — _safe_jump
                # reasons about the WINDOW, not about what survived it.
                window_cursors.append(c)
                acc = _accession_of(row)
                if acc is None:
                    # null/absent/absurd accession: a row that cannot be attributed to a real
                    # filing must NOT manufacture "filing" corroboration under a guessed key.
                    self.counters["bad_accession"] += 1
                    continue
                groups.setdefault(acc, []).append(row)
                group_cursors.setdefault(acc, []).append(c)
            except Exception:
                self.counters["bad_accession"] += 1     # one bad row never kills the batch

        emitted, emitted_cursors, deduped, retriable = 0, [], 0, 0
        emit_barriers: list[str] = []
        for acc, rows in groups.items():
            try:
                event, row_cursor = self._build(acc, rows)
            except Exception:
                # STRUCTURAL, not transient: _build is a pure function of the row, so it will
                # raise identically forever. Retrying it IS the wedge. Terminal + jumpable.
                self.counters["build_error"] += 1
                continue
            if event is None:
                continue
            key = f"sec:edgar:{acc}"
            # add() is a check-and-set — there is no race-free peek — so the key is TAKEN here
            # and GIVEN BACK below if the emit fails. It must never outlive a failed emit.
            if not self._dedup.add(key):
                deduped += 1
                continue
            try:
                on_event(event)
            except Exception:
                # TRANSIENT downstream failure. The row is GOOD EVIDENCE; it is NOT dropped.
                # Un-burn the key (or this filing could never be retried and would be lost for
                # good) and record a BARRIER so the cursor cannot advance past the row either.
                self._dedup.discard(key)
                self.counters["emit_error"] += 1
                retriable += 1
                cs = group_cursors.get(acc)
                if cs:
                    emit_barriers.append(min(cs))
                continue                                 # a failed emit must NOT advance the cursor
            emitted += 1
            if row_cursor is not None:
                emitted_cursors.append(row_cursor)

        if emit_barriers:
            # A LATER accession emitting fine must not carry the cursor past an EARLIER row we
            # failed to emit: the filter is `created_at > :cursor`, so that advance would evict
            # the failed row from every future window — a silent, permanent loss. Only emitted
            # cursors STRICTLY BELOW the earliest failed row may move us.
            barrier = min(emit_barriers)
            emitted_cursors = [c for c in emitted_cursors if c < barrier]

        moved = False
        saturated_tie_held = False
        if emitted_cursors:
            candidate = max(emitted_cursors)
            # Poison guard (the created_at analogue of finnhub_poll's MAX_ID_GAP): a row
            # timestamped in the future would strand the cursor past every real row, so a
            # cursor may never advance beyond `now`. Both sides are ALREADY canonical (storage
            # format) — the comparison is chronological because the format makes it so.
            if wall_cursor is not None and candidate > wall_cursor:
                self.counters["future_ts"] += 1
                self.counters["clamped_cursor"] += 1
                self.strand_signal = (
                    f"cursor CLAMPED to now ({wall_cursor}): an emitted row was timestamped "
                    f"{candidate}, in the future. Subsequent emptiness from this feed may be a "
                    "CLAMP artifact, NOT a quiet day."
                )
                candidate = wall_cursor
            if saturated:
                # `_SELECT` is `created_at > :cursor` with a LIMIT. A saturated successful
                # window is still a prefix: setting the cursor to its maximum timestamp would
                # orphan unseen rows tied at that timestamp. The second-largest distinct value
                # is the furthest safe cursor, exactly as in the zero-emission jump path.
                safe_ceiling = _safe_jump(window_cursors)
                if safe_ceiling is None:
                    saturated_tie_held = True
                    candidate = None
                elif candidate > safe_ceiling:
                    candidate = safe_ceiling
            moved = self._advance_to(candidate)

        # THE FORWARD-PROGRESS INVARIANT. A full window that emitted nothing is the wedge
        # signature: without intervention this exact window is re-fetched forever and the feed
        # is dead. An UNSATURATED zero-emit poll is NOT a wedge — the next real row carries a
        # later created_at, enters the window, emits, and drags the cursor with it; that case
        # self-heals and must not alarm.
        if saturated and emitted == 0:
            self._on_wedge_signature(wall_cursor, window_cursors, fetched,
                                     self._drop_total() - drops_before, deduped, retriable)
        elif emit_barriers:
            self.progress_signal = (
                f"NO FORWARD PROGRESS: cursor limited at {self._cursor}; "
                f"{len(emit_barriers)} row(s) FAILED TO EMIT and remain re-fetchable. "
                "This is a downstream-retry barrier, not a quiet day."
            )
        elif saturated_tie_held:
            self.counters["saturated_tie_held"] += 1
            self.progress_signal = (
                f"NO FORWARD PROGRESS: cursor held at {self._cursor}; the saturated window "
                "has no lossless timestamp cursor because unseen rows may tie its maximum. "
                "Refusing to orphan them; this is not a quiet day."
            )
        elif moved or emitted:
            self.progress_signal = None                  # progress made: the signal is not history
        return emitted

    def _on_wedge_signature(self, wall_cursor, window_cursors, fetched, dropped, deduped,
                            retriable=0) -> None:
        """A FULL window emitted ZERO. Jump past it if provably safe, and say so either way."""
        self.counters["saturated_no_emit"] += 1
        # THE JUMP IS ONLY LOSSLESS OVER ROWS THAT ARE UNEMITTABLE BY CONSTRUCTION. A row whose
        # ONLY failure was a TRANSIENT emit_error is not one of those — it is good evidence
        # awaiting a healthy downstream, so jumping past it would DESTROY it silently. Refuse,
        # and WEDGE LOUDLY below: a reported wedge beats silent evidence loss.
        jump = None if retriable else _safe_jump(window_cursors)
        if jump is not None and wall_cursor is not None and jump > wall_cursor:
            jump = wall_cursor         # same poison rule as the emitted path: never past the wall clock
        jumped = self._advance_to(jump)
        self.counters["window_jumped" if jumped else "wedge_blocked"] += 1

        if jumped and dropped == 0 and deduped and not retriable:
            # BENIGN: the window is full of an already-emitted accession's excess deltas (the
            # MAX_DELTAS_PER_EVENT catch-up described in the module docstring). Nothing was
            # dropped, progress was made, the feed is alive. Counted — but NOT alarmed, because
            # a signal that fires on normal operation is a signal nobody reads.
            self.counters["saturated_all_deduped"] += 1
            self.progress_signal = None
            return
        if jumped:
            why = (f"The cursor was JUMPED past the window to {self._cursor} to keep the feed "
                   "alive; every skipped row was counted or already emitted, never silent.")
        elif retriable:
            why = (f"The cursor is STUCK at {self._cursor}: the feed is WEDGED because "
                   f"{retriable} row(s) FAILED TO EMIT (a DOWNSTREAM outage), and we REFUSE to "
                   "jump past evidence that is merely awaiting a healthy consumer — that would "
                   "destroy it silently. Nothing was dropped and no dedup key was burnt: this "
                   "feed RESUMES BY ITSELF once the downstream recovers. FIX THE DOWNSTREAM.")
        else:
            why = (f"The cursor is STUCK at {self._cursor}: the feed is WEDGED — no later row "
                   "can enter this window, and no safe jump exists.")
        self.progress_signal = (
            f"NO FORWARD PROGRESS: fetched {fetched} rows — the window is SATURATED (full at the "
            f"fetch limit) — and emitted ZERO: {dropped} dropped as unemittable (structural), "
            f"{retriable} FAILED TO EMIT (retriable — NOT dropped), {deduped} already emitted. "
            + why +
            " This feed's emptiness is a DROP/WEDGE artifact, NOT a quiet day — "
            "read_brew_run_outcome CANNOT see this, it measures the brew, not the poller."
        )

    def _build(self, acc: str, rows: list[dict]):
        # PROVENANCE BINDING — the ticker comes from THE ROW THAT SUPPLIES raw["cik"], full stop.
        #
        # `first` is rows[0] both before and after the MAX_DELTAS_PER_EVENT truncation below
        # (truncation only cuts the tail), and it is the row that stamps raw["cik"],
        # raw["company_name"] and raw["filing_date"]. Resolving the ticker from ANY OTHER row is
        # the defect: this used to SCAN the group for the first row with a _TICKER-valid ticker,
        # so a group whose rows disagreed on cik (the LEFT JOIN is on d.cik, so per-row tickers
        # can differ) emitted `symbols` attributed to one company while shipping `raw` provenance
        # for a DIFFERENT one. `raw` is what content_hash binds (the SEC-13 audit chain) and what
        # an operator re-checks, so symbol attribution and carried provenance MUST be the same row
        # BY CONSTRUCTION, not by coincidence. Since the join is on d.cik, `first`'s ticker IS the
        # ticker for `first`'s cik.
        #
        # There is deliberately NO FALLBACK to other rows. The fallback IS the defect: it trades
        # a counted drop for a mis-attributed hard event.
        first = rows[0]
        ticker = _canonical_ticker(first.get("ticker"))
        # VALIDATE, don't merely test truthiness. _TICKER is imported from normalize (ONE
        # source of truth — never re-declare the pattern here, the two would drift).
        #
        # WHY THIS IS NOT COSMETIC: a truthiness-only gate accepts "N/A" (entirely plausible
        # for a non-listed filer) or any junk, and then normalize._clean_symbols filters that
        # value out — producing an event whose event_type is the HARD "filing" (an EXACT
        # member of budget.py:27 _HARD_EVENT_TYPES, tested at :234, which AUTO-CORROBORATES)
        # but whose `symbols` list is EMPTY. That is an unattributable hard event: it carries
        # hard-event weight while naming nobody. Reject it HERE, into the counted drop below.
        if ticker is None:
            # No company row maps this cik -> ticker, or the mapped ticker is not a valid symbol.
            # DROP it — a guessed ticker would attach hard-event corroboration to the wrong name.
            self.counters["no_ticker"] += 1
            return None, None

        rows = rows[:MAX_DELTAS_PER_EVENT]
        form = rows[0].get("form_type")
        form = form if isinstance(form, str) and form.strip() else "unknown"

        deltas, snippets, cursors = [], [], []
        for row in rows:
            snippet = row.get("snippet")
            snippet = snippet if isinstance(snippet, str) else ""
            snippets.append(snippet)
            deltas.append({
                "delta_id": row.get("delta_id"),
                "kind": row.get("kind"),
                # byte anchor — preserved so the chain stays independently re-checkable
                "edgar_url": row.get("edgar_url"),
                "snippet_start_offset": _int_or_none(row.get("snippet_start_offset")),
                "snippet_end_offset": _int_or_none(row.get("snippet_end_offset")),
                "snippet": snippet[:MAX_LEN],
                "materiality_score": _stringify(row.get("materiality_score")),
                "source_ref": row.get("source_ref"),
                "prior_value": _stringify(row.get("prior_value")),
                "current_value": _stringify(row.get("current_value")),
                "raw_ref": row.get("delta_raw_ref"),
                "created_at": _as_iso(row.get("created_at")),
            })
            # Already validated canonical by poll's boundary check — re-derived through the
            # SAME predicate so the two can never drift into different notions of "a cursor".
            c = _row_cursor(row.get("created_at"))
            if c is not None:
                cursors.append(c)

        # Deterministic + LLM-FREE. No summarizer, no model, no judgement — form, ticker, count.
        headline = f"{form} filing for {ticker} ({len(rows)} material delta"
        headline += ")" if len(rows) == 1 else "s)"
        summary = SNIPPET_JOIN.join(s for s in snippets if s)[:MAX_LEN]

        raw = {
            "feed": "edgar-morning-brew",
            "accession_no": acc,
            "form": form,                                # granular form lives HERE, not in event_type
            "cik": _stringify(first.get("cik")),
            "ticker": ticker,
            "company_name": first.get("company_name"),
            "filing_date": _as_iso(first.get("filing_date")) or _stringify(first.get("filing_date")),
            "detection_ts": _as_iso(first.get("detection_ts")),
            "primary_doc": first.get("primary_doc"),
            "filing_raw_ref": first.get("filing_raw_ref"),
            "delta_count": len(rows),
            "deltas": deltas,
        }
        ts = (_as_iso(first.get("detection_ts"))
              or (_storage_as_iso(max(cursors)) if cursors else None)
              or _as_iso(first.get("filing_date"))
              or _stringify(first.get("filing_date")))
        event = to_market_event(
            source=SOURCE,
            article_id=acc,
            symbols=[ticker],
            headline=headline,
            summary=summary,           # untrusted brew snippet text -> escape_untrusted (SEC-02)
            ts=ts,
            event_type=EVENT_TYPE,     # bare "filing" — EXACT frozenset membership
            raw=raw,
        )
        # BELT AND BRACES. The _TICKER gate above should make this unreachable, but a symbol-less
        # HARD event must never leave this module even if _TICKER and _clean_symbols ever drift
        # apart (they are the same object today; they might not be forever). Checked on the BUILT
        # event, i.e. against what normalize ACTUALLY produced — not against what we passed in.
        if event.get("symbols") != [ticker]:
            self.counters["symbol_less"] += 1
            return None, None
        return event, (max(cursors) if cursors else None)


def _stringify(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)                  # Decimal/Date etc -> JSON-stable for content_hash


# --------------------------------------------------------------------------- production I/O

_SELECT = """
SELECT d.delta_id, d.kind, d.accession_no, d.edgar_url,
       d.snippet_start_offset, d.snippet_end_offset, d.snippet,
       d.raw_ref AS delta_raw_ref, d.materiality_score, d.cik, d.source_ref,
       d.prior_value, d.current_value, d.created_at,
       f.form_type, f.filing_date, f.detection_ts, f.primary_doc,
       f.raw_ref AS filing_raw_ref,
       c.ticker, c.name AS company_name, c.active
FROM delta AS d
JOIN filing AS f ON d.accession_no = f.accession_no
LEFT JOIN company AS c ON d.cik = c.cik
WHERE (:cursor IS NULL OR d.created_at > :cursor)
ORDER BY d.created_at ASC, d.delta_id ASC
LIMIT :limit
"""


def read_only_uri(db_path) -> str:
    """``file:...?mode=ro`` URI. Read-only is enforced by SQLite itself, not by convention.

    The brew's tree is STRICTLY READ-ONLY and mid-soak; ``mode=ro`` makes a write attempt
    against the connection raise ``sqlite3.OperationalError`` rather than rely on discipline.
    ``Path.as_uri()`` handles Windows drive letters and spaces correctly.
    """
    return f"{Path(db_path).resolve().as_uri()}?mode=ro"


def make_brew_fetch(db_path, *, runs_dir=None, limit: int = 500):
    """Production ``fetch(cursor, now) -> list[dict]`` over the brew's SQLite, READ-ONLY.

    ALL sqlite3 I/O in this module lives here so ``EdgarBrewPoller`` stays pure/injectable.

    The company join is a LEFT JOIN on purpose: an INNER JOIN would silently filter out
    deltas whose CIK has no company row, so the drop would be invisible AND uncounted. The
    LEFT JOIN surfaces the row with ``ticker=None`` and lets the poller drop it against
    ``counters["no_ticker"]``.

    An unprovisioned DB returns an EMPTY batch — which is why callers MUST consult
    ``read_brew_run_outcome`` (exposed here as ``fetch.brew_status(day)``) rather than reading
    emptiness as a quiet day. The provisioning predicate is the SAME ``_is_provisioned_db`` the
    status uses, so the rows and the status can never disagree about whether the feed exists.

    Returns a ``BrewBatch``, not a bare list: it carries ``saturated``, which the poller needs
    to detect the head-of-line wedge (see the module docstring). ``limit`` is the ONLY place
    that knows how full "full" is, so it is the only place that can report it.
    """
    runs = Path(runs_dir) if runs_dir is not None else None
    lim = int(limit)

    def fetch(cursor, now):
        if not _is_provisioned_db(db_path):
            return BrewBatch()                            # NOT_PROVISIONED — see fetch.brew_status
        conn = sqlite3.connect(read_only_uri(db_path), uri=True)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                # _canonical_cursor, NOT _as_iso: _SELECT compares this to SQLAlchemy-written
                # DATETIME TEXT lexicographically, and an isoformat()-shaped cursor sorts above
                # every real row -> silent permanent strand. See _canonical_cursor.
                _SELECT, {"cursor": _canonical_cursor(cursor), "limit": lim}
            ).fetchall()
            # >= not ==: defensive. A window at the limit means later rows CANNOT enter it.
            return BrewBatch((dict(r) for r in rows), saturated=len(rows) >= lim)
        finally:
            conn.close()

    def brew_status(day) -> BrewRunOutcome:
        if runs is None:
            # No runs dir configured: we cannot prove the brew ran. Fail closed.
            return _outcome(BrewFeedStatus.NOT_RUN, _as_day(day), None)
        return read_brew_run_outcome(runs, day, db_path=db_path)

    fetch.brew_status = brew_status          # type: ignore[attr-defined]
    fetch.limit = lim                        # type: ignore[attr-defined]
    fetch.db_path = str(db_path)             # type: ignore[attr-defined]
    fetch.runs_dir = str(runs) if runs else None   # type: ignore[attr-defined]
    return fetch
