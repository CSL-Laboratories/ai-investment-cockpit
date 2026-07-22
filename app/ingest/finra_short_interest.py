"""W20 — FINRA consolidated short-interest + Reg SHO threshold connector.

ENRICHMENT / CONTEXT ONLY. Read this before wiring anything:

  * Short interest is published TWICE A MONTH, on the 7th business day after the reporting
    settlement date (CONFIRMED 2026-07-16, https://www.finra.org/finra-data/browse-catalog/
    equity-short-interest). By the time a row is readable it is ~2 weeks stale. It is NOT
    real-time and it may NEVER drive SEC-21 urgency or corroboration.
  * Accordingly the emitted ``event_type`` is ``"short_interest"`` or ``"reg_sho_threshold"``
    and MUST NOT be ``"halt"`` or ``"filing"`` — those two strings are the ENTIRE membership of
    ``_HARD_EVENT_TYPES`` (app/workflows/budget.py:27, tested at :181/:234) and of the
    triage pre-filter frozenset (app/workflows/triage_loop.py:47, used at :199). Emitting either
    from here would silently promote two-week-old enrichment into auto-corroborated urgency.
    ``_assert_enrichment_event_type`` below enforces this at emit time; a test pins it.
  * Reg SHO threshold SCOPING CAVEAT (load-bearing): FINRA's threshold list is OTC-ONLY
    ("OTC Threshold data is published daily by FINRA...", CONFIRMED 2026-07-16,
    https://www.finra.org/finra-data/browse-catalog/otc-threshold). Threshold lists for
    EXCHANGE-LISTED names are published by each listing SRO (NYSE, Nasdaq, Cboe BZX), not by
    FINRA. A caller that treats this feed as full-market coverage will silently miss every
    listed ticker. This connector does not cover them.

CADENCE IS OBSERVED DATA, NEVER A CONSTANT
------------------------------------------
FINRA Rule 4560 fixes no calendar frequency — it only says members report "no later than the
second business day after the reporting settlement date designated by FINRA" (REFUTED that the
rule carries a cadence; https://www.finra.org/rules-guidance/rulebooks/finra-rules/4560). The
cadence has always lived in FINRA's *published schedule*. SR-FINRA-2026-012 proposes moving all
three numbers at once — cadence twice-monthly -> weekly, member due T+2bd -> T+1bd, publication
7bd -> 5bd after settlement (CONFIRMED, SEC Release 34-105482). As of 2026-07-16 it is un-acted-
upon; SEC action extended to 2026-08-14 (Release 34-105824), and even an approval does not move
the feed ("FINRA will announce the effective date... in a Regulatory Notice", 34-105482 — no
implementation date stated). So this module DERIVES nothing from a calendar rule: the settlement
date is read off each row as published, and the cursor is whatever FINRA actually emitted. There
is no ``PUBLICATION_LAG_BUSINESS_DAYS`` here on purpose. A weekly transition is a no-op for this
code.

OPERATOR-SET / TBD (this module invents NONE of these — see ``make_finra_fetch``)
--------------------------------------------------------------------------------
  * ``endpoint`` — no default. FINRA Query API production base ``https://api.finra.org`` is
    CONFIRMED (https://developer.finra.org/docs), but the exact dataset identifier strings and
    the group/name path pattern are UNVERIFIABLE from any primary source (they gate behind
    https://gateway.finra.org/app/dfo-console). Catalog display names only: "Consolidated Short
    Interest", "Threshold List". Pin the real path with a free Mock credential before wiring.
  * ``user_agent`` — no default. Format is a convention, not a validated grammar: SEC's sample
    is "<Company/App Name> <contact email>" and a suppressed UA empirically returns HTTP 403
    (CONFIRMED by curl 2026-07-16). FINRA publishes no UA grammar of its own, so the shape is
    operator-set/TBD; we send whatever the operator declares, always.
  * ``bucket`` rate — FINRA publishes NO documented request-rate cap that could be verified.
    The SEC's "10 requests/second" fair-access figure is CONFIRMED but it governs *sec.gov*,
    NOT api.finra.org — importing it here would be inventing a limit. The default below is a
    deliberately conservative SELF-IMPOSED floor (1 req/s), not a claim about FINRA's ceiling.
    The real cap is operator-set/TBD.
  * OAuth2 is MANDATORY (client_credentials against
    https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token; Basic Auth dead since
    2022-06-01 — CONFIRMED). Token acquisition is the operator's ``token_provider`` closure;
    credentials are secrets and live in keyring (Inv 4), never here and never in settings.

Shape follows ingest/finnhub_poll.py deliberately (proven template, W20 reuses it): injected
``fetch``, empty-start TokenBucket, per-item try/except, cursor advances only from EMITTED items
under a poison bound. All I/O lives in ``make_finra_fetch``; the poller is pure.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re

from ingest.clock_contract import resolve_wall_clock, utc_now
from ingest.dedup import Dedup
from ingest.normalize import _canonical_ticker, to_market_event
from ingest.ratelimit import TokenBucket

# The §5 feed allowlist (ingest/normalize.py) authorizes "finra" in its literal;
# to_market_event RAISES on anything outside it. This module does NOT register itself at import
# — a self-registering allowlist is import-order-dependent and stops being auditable at one line.
SOURCE = "finra"

EVENT_SHORT_INTEREST = "short_interest"
EVENT_REG_SHO_THRESHOLD = "reg_sho_threshold"

# The ONLY members of _HARD_EVENT_TYPES (app/workflows/budget.py:27) and of the triage
# pre-filter frozenset (app/workflows/triage_loop.py:47). Enrichment may never emit these.
_FORBIDDEN_EVENT_TYPES = frozenset({"halt", "filing"})

# Canonical row kinds this poller understands, mapped to the emitted event_type. The FINRA wire
# schema is UNVERIFIED (see module docstring), so make_finra_fetch is responsible for mapping
# whatever the dataset actually returns onto these canonical keys.
_KIND_TO_EVENT_TYPE = {
    "short_interest": EVENT_SHORT_INTEREST,
    "reg_sho_threshold": EVENT_REG_SHO_THRESHOLD,
}
_KIND_TO_ID_PREFIX = {"short_interest": "si", "reg_sho_threshold": "sho"}

# Heuristic sanity bound on a single cursor advance (poison guard), mirroring MAX_ID_GAP in
# finnhub_poll.py. NOT a cadence claim: it only bounds how far one untrusted batch can shove the
# cursor forward, so a row dated 2099-01-01 cannot strand the feed forever. Generous enough that
# any real twice-monthly OR weekly schedule (and a long backfill gap) passes untouched. A gap
# never produces a synthetic cursor: it is held and signalled until observed rows bridge it.
MAX_CURSOR_GAP_DAYS = 400
_SETTLEMENT_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class FinraBatch(list):
    """Fetched rows plus failure metadata that must reach the poller's counted paths."""

    def __init__(self, rows=(), *, mapper_errors: int = 0, payload_error: bool = False):
        super().__init__(rows)
        self.mapper_errors = mapper_errors
        self.payload_error = payload_error


def _assert_enrichment_event_type(event_type: str) -> str:
    """Fail closed if this connector ever tries to emit an auto-corroborating type."""
    if event_type in _FORBIDDEN_EVENT_TYPES:
        raise ValueError(
            f"FINRA is enrichment-only and must never emit {event_type!r}: it is a member of "
            "budget._HARD_EVENT_TYPES and would drive SEC-21 urgency from ~2-week-stale data"
        )
    return event_type


def _parse_settlement_date(value):
    """Return an ISO ``YYYY-MM-DD`` date or None. Untrusted input: never raises."""
    # datetime is a date subclass, but it cannot be compared or serialized as this cursor's
    # DATE-only identity. Reject it rather than emitting first and crashing on the poison bound.
    if isinstance(value, datetime):
        return None
    if type(value) is date:
        return value
    # This is an identity field for both cursor and dedup.  ``fromisoformat`` accepts compact
    # and ISO-week spellings, but those are not the published wire grammar and could alias a
    # genuine extended date.  Do not trim or normalize this raw value before validation.
    if type(value) is not str or not _SETTLEMENT_DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _utc_now() -> datetime:
    """The DEFAULT wall clock for the cold-start cursor bound. Injectable — see ``__init__``.

    Delegates to the SHARED ``clock_contract.utc_now`` — the same function ``ingest/edgar_brew.py``
    defaults to — so the two seams cannot default differently. It returns a TZ-AWARE ``datetime``,
    which this module narrows to a calendar day AFTER validation (see below).

    THE CLOCK CONTRACT AT THIS SEAM. Enforced by ``ingest/clock_contract.py``, not by prose.

    ``poll``'s ``now`` CANNOT be used for the bound. It is MONOTONIC seconds (ingest/ratelimit.py:2:
    "the runner passes monotonic ``now`` seconds"), fed to TokenBucket as an elapsed-time clock —
    it is NOT epoch wall-clock. ``date.fromtimestamp(time.monotonic())`` is ~1970-01-01, so
    bounding a settlement date against it would clamp EVERY real cursor to 1970 and strand the
    feed permanently. A calendar bound needs a calendar clock, so one is injected explicitly.

    THE CONVENTION IS REPO-WIDE AND IS NOW A SHARED CALL, NOT A CROSS-REFERENCE. The sibling
    connector ``ingest/edgar_brew.py`` narrates it under "THE CLOCK CONTRACT"; both modules CALL
    ``clock_contract.resolve_wall_clock`` and share its ONE ``ClockContractError``. Prose symmetry
    is what failed here twice: this module's bound sat behind ``if isinstance(today, date) and
    candidate > today``, so a mis-wired clock made the test False and the whole poison bound was
    SKIPPED — no raise, no counter, no signal, cursor pinned at 2099 forever — while the docstring
    on this very function asserted edgar_brew resolved it "THE SAME WAY ... plus a loud
    ClockContractError". It did; this side did not. Read them together; do not invent a third
    answer, and never guard a poison bound behind a type test of the clock's own output.

    THE SEAM RETURNS A TZ-AWARE ``datetime`` — NOT a ``date``, even though this module compares
    calendar days. One contract, narrowed here (``.astimezone(utc).date()``), never redefined:
    ``FinraShortInterestPoller(clock=lambda: datetime.now(timezone.utc))`` — the natural reading of
    "one convention, two pollers" — used to raise an UNCAUGHT ``TypeError`` at the comparison,
    because ``datetime`` subclasses ``date`` so the isinstance gate passed and ``date > datetime``
    then exploded. The same clock object now drives BOTH pollers.
    """
    return utc_now()


# Both pollers must use the identical default object, not two wrappers that merely happen to
# return the same time today. Keep the historical name as a compatibility alias.
_utc_now = utc_now


class FinraShortInterestPoller:
    """Poll FINRA consolidated short interest + Reg SHO threshold rows.

    ``fetch(cursor, now) -> list[dict]`` is injected, so tests drive this entirely offline and
    no URL, credential, or HTTP client is reachable from this module. ``cursor`` is the highest
    settlement date already emitted (a ``datetime.date``), or None on a cold start — the caller's
    closure decides what that means on the wire. We never compute an expected settlement date.
    """

    def __init__(self, fetch, bucket=None, dedup=None, clock=None):
        self._fetch = fetch
        # Starts EMPTY (no birth burst) — see TokenBucket.__init__, ingest/ratelimit.py:10.
        # 1.0/s is a self-imposed politeness floor, NOT a verified FINRA cap (operator-set/TBD).
        self._bucket = bucket or TokenBucket(rate_per_sec=1.0, capacity=1.0)
        self._dedup = dedup or Dedup(capacity=50_000)
        self._cursor: date | None = None
        # Wall clock for the COLD-START cursor bound (see _utc_now for why poll()'s `now` cannot
        # serve). Defaults to a real tz-aware UTC clock so the bound is ON BY DEFAULT, not opt-in.
        # NAMED `clock=`, LIKE EdgarBrewPoller's, AND IT TAKES THE SAME KIND OF OBJECT: one
        # contract, one validator (clock_contract.resolve_wall_clock), two seams. It was `today=`
        # returning a `date` while edgar_brew's `clock=` returned a `datetime` — two type
        # contracts the docs presented as one, so reusing one clock object across both pollers
        # (the natural reading) raised an UNCAUGHT TypeError inside the comparison below.
        self._clock = clock or _utc_now
        # Every drop is COUNTED. A skip that increments nothing is a SILENT drop, and silence
        # from this feed is indistinguishable from "FINRA published nothing" — the same
        # failure mode edgar_brew's counters exist to prevent. Mirrors that counter set.
        #   unknown_kind       — row kind not in _KIND_TO_EVENT_TYPE
        #   bad_settlement     — settlementDate absent/malformed
        #   no_symbol          — symbol absent/blank/non-string
        #   symbol_less        — symbol present but fails normalize._TICKER, so the built event
        #                        would carry symbols == [] and name nobody (G20 round-6; the
        #                        analogue of edgar_brew's no_ticker/symbol_less pair)
        #   not_a_dict         — the row was not a dict at all
        #   build_error        — to_market_event raised, or the row was otherwise unusable.
        #                        It is counted AND held for retry: an untrusted row or a future
        #                        normalizer drift is not proof that its evidence is disposable.
        #   emit_error         — on_event raised: TRANSIENT/RETRIABLE, NOT a drop. The dedup key
        #                        is ROLLED BACK and the cursor is barred from passing the row —
        #                        see "A FAILED EMIT IS NOT A DROP" below
        #   duplicate          — dedup suppressed an already-emitted (date, symbol)
        #   future_settlement  — a row settled after `today` (poison); refused before emit/dedup
        #   gap_blocked        — a valid row would require a synthetic cursor gap; held instead
        #   clamped_cursor     — retained legacy telemetry; future poison must leave it at zero
        self.counters = {"unknown_kind": 0, "bad_settlement": 0, "no_symbol": 0,
                         "symbol_less": 0, "mapper_error": 0, "payload_error": 0,
                         "not_a_dict": 0, "build_error": 0, "emit_error": 0, "duplicate": 0,
                         "future_settlement": 0, "gap_blocked": 0, "clamped_cursor": 0}
        # A refused poison row is NOT "FINRA published nothing". Leave a durable signal.
        self.strand_signal: str | None = None
        # A counted invalid row, fetch fault, or retriable emit must not be mistaken for a quiet
        # poll or skipped by a later row.
        self.progress_signal: str | None = None

    @property
    def cursor(self) -> date | None:
        return self._cursor

    def _wall_clock_today(self) -> date:
        """The bound's calendar day, read from the INJECTED clock and VALIDATED FIRST.

        NEVER from ``poll``'s ``now`` — see ``_utc_now``. The clock's OUTPUT goes through the
        SHARED ``resolve_wall_clock`` (``ingest/clock_contract.py``) — the identical call
        ``EdgarBrewPoller._wall_clock_cursor`` makes — so a mis-wired ``clock=time.monotonic`` or
        ``clock=lambda: now`` raises the ONE ``ClockContractError`` LOUDLY, on every poll, before
        any row is read.

        THIS RETURNS A ``date`` OR RAISES. It cannot return None and it cannot return a non-date,
        which is what lets the caller compare UNCONDITIONALLY. The bound used to be written
        ``if isinstance(today, date) and candidate > today:`` — so a non-date clock silently
        SKIPPED the entire poison bound (no raise, no counter, no signal) and one row dated
        2099-01-01 pinned the cursor at 2099 forever, asking FINRA for
        settlementDateFrom=2099-01-01 on every subsequent poll. A control that a bad input
        DISABLES is not a control. Validate here; compare there.
        """
        wall = resolve_wall_clock(self._clock, seam="FinraShortInterestPoller(clock=...)")
        # NARROW the one contract's tz-aware datetime to this seam's question (a calendar day),
        # explicitly in UTC. Never redefine the contract per seam — narrow it, after validation.
        return wall.astimezone(timezone.utc).date()

    def poll(self, now: float, on_event) -> int:
        """A FAILED EMIT IS NOT A DROP (mirrors ingest/edgar_brew.py — same defect class).

        The dedup key means "this WAS emitted", never "we tried to emit this". ``Dedup.add`` is a
        check-and-set with no race-free peek, so it must be taken BEFORE ``on_event`` — and GIVEN
        BACK when ``on_event`` raises. Committing it before a successful emit while ALSO treating
        an ``emit_error`` as terminal would mean a TRANSIENT downstream failure (persist_events
        down, disk full, a momentary DB lock) permanently burns the key: the row could never be
        retried and the evidence would be silently, unrecoverably gone, with the counters showing
        a tidy "drop". Hence the rollback, the retriable counter, and the cursor barrier below.

        ``to_market_event`` raising is counted separately as ``build_error``. It is not allowed to
        burn the dedup key or let a later row move the cursor past its settlement date: the wire
        row, or a normalizer boundary that has drifted, may be corrected on the next fetch. A
        reported retry wedge is preferable to silently discarding evidence.

        ``now`` IS MONOTONIC SECONDS. It reaches the TokenBucket (an elapsed-time clock) and the
        injected ``fetch``, and NOTHING else. The cursor bound's calendar comes from the injected
        ``clock`` via ``_wall_clock_today``, which is resolved FIRST — before the rate check, before
        the fetch — so a mis-wired clock is loud on EVERY poll rather than only on polls that
        happen to emit a row. See "THE CLOCK CONTRACT" (ingest/clock_contract.py).
        """
        today = self._wall_clock_today()        # VALIDATE FIRST: raises on a mis-wired clock
        if not self._bucket.allow(now):
            return 0                                    # rate-limited: skip (never exceed)
        emitted, advance_dates = 0, []
        emit_barriers = []                              # settlement dates we FAILED to emit
        attribution_barriers = []                       # located rows a later cursor must re-fetch
        # A malformed/unmapped row whose settlement date we cannot establish is a stronger
        # barrier: advancing at all could skip it under FINRA's inclusive date cursor.
        unlocatable_barriers = 0
        future_settlements = []                         # poison rows refused before dedup/emit
        structural_keys = ("unknown_kind", "bad_settlement", "no_symbol", "symbol_less",
                           "mapper_error", "payload_error", "not_a_dict", "build_error")
        drops_before = sum(self.counters[key] for key in structural_keys)
        batch = self._fetch(self._cursor, now)
        mapper_errors = getattr(batch, "mapper_errors", 0)
        if type(mapper_errors) is not int or mapper_errors < 0:
            mapper_errors = 1
        if mapper_errors:
            self.counters["mapper_error"] += mapper_errors
            unlocatable_barriers += mapper_errors
        if bool(getattr(batch, "payload_error", False)):
            self.counters["payload_error"] += 1
            unlocatable_barriers += 1
        for row in batch:
            settlement = None
            key = None
            try:
                if type(row) is not dict:
                    self.counters["not_a_dict"] += 1
                    unlocatable_barriers += 1
                    continue
                # Parse before the row kind. An unknown kind at a known date may be corrected;
                # do not let a later valid row move the inclusive cursor beyond it.
                settlement = _parse_settlement_date(row.get("settlementDate"))
                if settlement is None:
                    self.counters["bad_settlement"] += 1
                    unlocatable_barriers += 1
                    continue                            # malformed date: never guess its window
                if settlement > today:
                    # Do not emit a future settlement and then manufacture a cursor at today's
                    # date: that synthetic jump can skip real rows between the old cursor and
                    # today. Keep the poison re-fetchable and visible until its date is real.
                    self.counters["future_settlement"] += 1
                    future_settlements.append(settlement)
                    self.strand_signal = (
                        f"FUTURE SETTLEMENT REFUSED at {settlement}: short interest is published "
                        "after settlement, so this row is poison. It was neither emitted nor used "
                        "to advance the cursor; subsequent emptiness may be a poison artifact."
                    )
                    continue
                kind = row.get("kind", "short_interest")
                event_type = _KIND_TO_EVENT_TYPE.get(kind) if type(kind) is str else None
                if event_type is None:
                    self.counters["unknown_kind"] += 1
                    attribution_barriers.append(settlement)
                    continue                            # unknown kind: skip, never guess
                raw_symbol = row.get("symbol")
                if type(raw_symbol) is not str or not raw_symbol.strip():
                    self.counters["no_symbol"] += 1
                    attribution_barriers.append(settlement)
                    continue
                symbol = _canonical_ticker(raw_symbol)
                # G20 round-6: PRESENCE is not VALIDITY. A non-empty-but-invalid symbol ("N/A"
                # for a non-listed issuer; "<system:>" for the hostile form) passes the strip
                # test, then normalize._clean_symbols silently filters it against _TICKER and
                # to_market_event returns symbols == []. Emitting that counts as a SUCCESS here
                # (cursor advances, dedup key burns) while incrementing NOTHING — a SILENT,
                # UNCOUNTED attribution loss in the one module whose stated invariant is that no
                # drop is silent. Validate with the SAME _TICKER normalize will apply, and drop
                # through a COUNTED path. This is edgar_brew's round-1 fix, applied to its
                # sibling: the identical defect survived five rounds here because the test that
                # named the control (test_hostile_symbol_is_ticker_validated) asserted the
                # symbol-less emission as CORRECT.
                if symbol is None:
                    self.counters["symbol_less"] += 1
                    attribution_barriers.append(settlement)
                    continue
                iso = settlement.isoformat()
                prefix = _KIND_TO_ID_PREFIX[kind]
                # Dedup identity is (settlementDate, symbol), namespaced by kind so a symbol can
                # legitimately appear in both feeds on the same date.
                key = f"{SOURCE}:{prefix}:{iso}:{symbol}"
                if not self._dedup.add(key):
                    self.counters["duplicate"] += 1
                    # Dedup's contract is a prior successful emit. A confirmed duplicate is
                    # therefore safe cursor evidence, and lets a corrected earlier row release
                    # an inclusive-date barrier instead of pinning the feed forever.
                    advance_dates.append(settlement)
                    continue
                try:
                    event = to_market_event(
                        source=SOURCE,
                        article_id=f"{prefix}:{iso}:{symbol}",
                        symbols=[symbol],
                        # Free text from the row (issuer/security name) flows through
                        # to_market_event -> escape_untrusted, which NFKC-folds and neutralizes
                        # role markers. Never interpolate it anywhere that skips the escaper.
                        headline=f"{symbol} {event_type} {iso} "
                                 f"{row.get('issueName', '')}".strip(),
                        summary=row.get("notes", ""),
                        ts=row.get("ts"),
                        event_type=_assert_enrichment_event_type(event_type),
                        raw=row,
                    )
                except Exception:
                    self._dedup.discard(key)
                    self.counters["build_error"] += 1
                    attribution_barriers.append(settlement)
                    continue
                if event.get("symbols") != [symbol]:
                    # Defense in depth: the pre-build canonical-ticker check and normalize's
                    # own check must agree EXACTLY. If a future drift substitutes a different
                    # valid symbol, that is just as dangerous as an empty one: do not emit it,
                    # burn its key, or advance beyond its row.
                    self._dedup.discard(key)
                    self.counters["symbol_less"] += 1
                    attribution_barriers.append(settlement)
                    continue
                try:
                    on_event(event)
                except Exception:
                    # TRANSIENT downstream failure — the row is GOOD DATA and is NOT dropped.
                    # Un-burn the key so it can be re-emitted once the consumer recovers, and
                    # bar the cursor from moving past it (below).
                    self._dedup.discard(key)
                    self.counters["emit_error"] += 1
                    emit_barriers.append(settlement)
                    continue
            except Exception:
                # Nothing downstream is reachable from here (on_event has its own handler), so an
                # exception at this level is counted and held: a later corrected representation
                # must not be silently skipped by an inclusive cursor.
                if key is not None:
                    self._dedup.discard(key)
                self.counters["build_error"] += 1
                if settlement is None:
                    unlocatable_barriers += 1
                else:
                    attribution_barriers.append(settlement)
                continue                                # one bad item never kills the poll
            emitted += 1
            advance_dates.append(settlement)
        gap_blocked = False
        if advance_dates and not unlocatable_barriers:
            candidate = max(advance_dates)
            if emit_barriers:
                # A LATER row emitting fine must not carry the cursor past an EARLIER row we
                # FAILED to emit. `settlementDateFrom` is an INCLUSIVE lower bound, so holding
                # the cursor AT the earliest failed date keeps that row in the next window;
                # anything beyond it would evict the row forever — silent evidence loss.
                candidate = min(candidate, min(emit_barriers))
            if attribution_barriers:
                # A counted symbol/no-symbol refusal is not permission to discard a possible
                # correction. The production query treats settlementDateFrom as inclusive, so
                # holding the cursor at the earliest refused row preserves that row's window.
                candidate = min(candidate, min(attribution_barriers))
            # Future rows were refused before dedup or emit, so every candidate here is observed
            # non-poison evidence. The gap cap remains a second guard for a persisted/legacy
            # cursor, never a license to manufacture today's cursor from a future row.
            if self._cursor is None:
                self._cursor = candidate
            else:
                ceiling = self._cursor + timedelta(days=MAX_CURSOR_GAP_DAYS)
                if candidate > ceiling:
                    # A synthetic ceiling date is not evidence. Moving to it would skip a real
                    # settlement in the unseen gap under FINRA's inclusive cursor predicate.
                    # Hold loudly until observed rows bridge the gap instead.
                    gap_blocked = True
                    self.counters["gap_blocked"] += 1
                    self.strand_signal = (
                        f"CURSOR GAP REFUSED: observed settlement {candidate} is more than "
                        f"{MAX_CURSOR_GAP_DAYS} days after cursor {self._cursor}. No synthetic "
                        "date was used; intermediate evidence remains re-fetchable."
                    )
                else:
                    self._cursor = max(self._cursor, candidate)
        dropped = sum(self.counters[key] for key in structural_keys) - drops_before
        if future_settlements:
            self.progress_signal = (
                f"POISON SETTLEMENT REFUSED: {len(future_settlements)} future row(s) were "
                f"not emitted and did not advance cursor {self._cursor}; this is not a quiet "
                "publication."
            )
        elif gap_blocked:
            self.progress_signal = (
                f"NO FORWARD PROGRESS: cursor held at {self._cursor}; an observed settlement "
                f"exceeded the {MAX_CURSOR_GAP_DAYS}-day safe gap and no synthetic cursor was "
                "created. Intermediate evidence remains re-fetchable; this is not a quiet "
                "publication."
            )
        elif unlocatable_barriers:
            self.progress_signal = (
                f"NO FORWARD PROGRESS — CURSOR HELD at {self._cursor}: "
                f"{unlocatable_barriers} fetched row(s) could not be mapped to a settlement "
                "date. Refusing to skip evidence whose inclusive cursor window is unknown; "
                "this is not a quiet publication."
            )
        elif emit_barriers:
            self.progress_signal = (
                f"NO FORWARD PROGRESS — CURSOR LIMITED at {self._cursor}: "
                f"{len(emit_barriers)} valid row(s) FAILED TO EMIT and remain re-fetchable. "
                "Refusing to advance past a downstream-retry barrier; this is not a quiet "
                "publication."
            )
        elif attribution_barriers:
            self.progress_signal = (
                f"NO FORWARD PROGRESS — CURSOR HELD at {self._cursor}: "
                f"{len(attribution_barriers)} counted attribution "
                "drop(s) remain re-fetchable. Refusing to advance past a row that FINRA may "
                "correct; this is not a quiet publication."
            )
        elif emitted == 0 and dropped:
            self.progress_signal = (
                f"NO FORWARD PROGRESS: {dropped} fetched row(s) were counted as structural "
                "drops and no event emitted. Cursor was not advanced; this is not a quiet "
                "publication."
            )
        elif emitted:
            self.progress_signal = None
        return emitted


def make_finra_fetch(*, endpoint, user_agent, token_provider, kind="short_interest",
                     row_mapper=None, http_get=None, timeout=30.0):
    """Build the production ``fetch(cursor, now)`` closure. ALL I/O lives here, never in the
    poller — mirroring how main.compose() (app/main.py:119-144) injects closures.

    Every argument below is REQUIRED and has NO default because no primary source confirms it:

    ``endpoint``       Fully-qualified dataset URL. Base ``https://api.finra.org`` is CONFIRMED;
                       the dataset identifier + path pattern are UNVERIFIABLE (console-gated) —
                       operator-set/TBD, pin against a free Mock credential first.
    ``user_agent``     Declared UA string, operator-set/TBD. ALWAYS sent. Convention is
                       "<App Name> <contact email>"; a suppressed UA is empirically a 403.
    ``token_provider`` ``() -> str`` returning an OAuth2 bearer access_token (client_credentials
                       against https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token —
                       MANDATORY, Basic Auth dead since 2022-06-01). Credentials are secrets:
                       keyring only (Inv 4), never settings, never this module.
    ``row_mapper``     ``(wire_row) -> dict`` producing the canonical keys the poller reads
                       (``kind``, ``settlementDate`` ISO, ``symbol``, optional ``issueName`` /
                       ``notes`` / ``ts``). Required in practice because the wire field names are
                       UNVERIFIED; defaults to a passthrough only so a Mock-credential probe can
                       inspect the raw shape.
    ``http_get``       ``(url, headers=..., params=..., timeout=...) -> object`` with
                       ``.json()``. Injected so httpx/urllib stay out of the import graph here.
    """
    if not endpoint:
        raise ValueError("endpoint is operator-set/TBD and required: no FINRA dataset URL is "
                         "verified from a primary source, so this module refuses to invent one")
    if not user_agent:
        raise ValueError("user_agent is operator-set/TBD and required: requests without a "
                         "declared UA are rejected (HTTP 403)")
    if token_provider is None:
        raise ValueError("token_provider is required: the FINRA API is OAuth2-only since "
                         "2022-06-01")
    if http_get is None:
        raise ValueError("http_get is required: HTTP client injection keeps I/O out of the "
                         "pure ingest module")
    if kind not in _KIND_TO_EVENT_TYPE:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(_KIND_TO_EVENT_TYPE)}")
    mapper = row_mapper or (lambda r: r)

    def fetch(cursor, now):
        headers = {
            "User-Agent": user_agent,                    # ALWAYS sent
            "Authorization": f"Bearer {token_provider()}",
            "Accept": "application/json",
        }
        params = {}
        if cursor is not None:
            # Pass the observed cursor through; we never synthesize an expected settlement date
            # from a cadence rule (see module docstring).
            params["settlementDateFrom"] = cursor.isoformat()
        payload = http_get(endpoint, headers=headers, params=params, timeout=timeout).json()
        if not isinstance(payload, list):
            # An error object / unexpected payload is not an empty publication. Preserve the
            # distinction for poll() so it reports a counted, fail-closed fetch fault.
            return FinraBatch(payload_error=True)
        rows = payload
        out = []
        mapper_errors = 0
        for r in rows:
            try:
                mapped = dict(mapper(r))
            except Exception:
                mapper_errors += 1                       # surfaced by poll(), never a quiet skip
                continue
            mapped.setdefault("kind", kind)
            out.append(mapped)
        return FinraBatch(out, mapper_errors=mapper_errors)

    return fetch
