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

from ingest.clock_contract import resolve_wall_clock, utc_now
from ingest.dedup import Dedup
from ingest.normalize import _TICKER, to_market_event
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
# any real twice-monthly OR weekly schedule (and a long backfill gap) passes untouched.
MAX_CURSOR_GAP_DAYS = 400


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
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
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
        #   build_error        — to_market_event raised, or the row was otherwise unusable:
        #                        STRUCTURAL (a pure function of the row), terminal
        #   emit_error         — on_event raised: TRANSIENT/RETRIABLE, NOT a drop. The dedup key
        #                        is ROLLED BACK and the cursor is barred from passing the row —
        #                        see "A FAILED EMIT IS NOT A DROP" below
        #   duplicate          — dedup suppressed an already-emitted (date, symbol)
        #   future_settlement  — a row settled after `today` (poison); cursor clamped
        #   clamped_cursor     — the cursor was clamped rather than taken from a real row
        self.counters = {"unknown_kind": 0, "bad_settlement": 0, "no_symbol": 0,
                         "symbol_less": 0,
                         "not_a_dict": 0, "build_error": 0, "emit_error": 0, "duplicate": 0,
                         "future_settlement": 0, "clamped_cursor": 0}
        # A clamp is NOT "FINRA published nothing". Leave a durable, readable signal.
        self.strand_signal: str | None = None

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

        ``to_market_event`` raising is different in kind: it is a pure function of the row, so it
        will raise identically forever. That is STRUCTURAL — counted apart as ``build_error``,
        terminal, and the key stays burnt so a deterministic bad row cannot re-count every poll.

        ``now`` IS MONOTONIC SECONDS. It reaches the TokenBucket (an elapsed-time clock) and the
        injected ``fetch``, and NOTHING else. The cursor bound's calendar comes from the injected
        ``clock`` via ``_wall_clock_today``, which is resolved FIRST — before the rate check, before
        the fetch — so a mis-wired clock is loud on EVERY poll rather than only on polls that
        happen to emit a row. See "THE CLOCK CONTRACT" (ingest/clock_contract.py).
        """
        today = self._wall_clock_today()        # VALIDATE FIRST: raises on a mis-wired clock
        if not self._bucket.allow(now):
            return 0                                    # rate-limited: skip (never exceed)
        emitted, emitted_dates = 0, []
        emit_barriers = []                              # settlement dates we FAILED to emit
        for row in self._fetch(self._cursor, now):
            try:
                if not isinstance(row, dict):
                    self.counters["not_a_dict"] += 1
                    continue
                kind = row.get("kind", "short_interest")
                event_type = _KIND_TO_EVENT_TYPE.get(kind)
                if event_type is None:
                    self.counters["unknown_kind"] += 1
                    continue                            # unknown kind: skip, never guess
                settlement = _parse_settlement_date(row.get("settlementDate"))
                if settlement is None:
                    self.counters["bad_settlement"] += 1
                    continue                            # malformed date: skip, never abort batch
                symbol = row.get("symbol")
                if not isinstance(symbol, str) or not symbol.strip():
                    self.counters["no_symbol"] += 1
                    continue
                symbol = symbol.strip().upper()
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
                if not _TICKER.match(symbol):
                    self.counters["symbol_less"] += 1
                    continue
                iso = settlement.isoformat()
                prefix = _KIND_TO_ID_PREFIX[kind]
                # Dedup identity is (settlementDate, symbol), namespaced by kind so a symbol can
                # legitimately appear in both feeds on the same date.
                key = f"{SOURCE}:{prefix}:{iso}:{symbol}"
                if not self._dedup.add(key):
                    self.counters["duplicate"] += 1
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
                    self.counters["build_error"] += 1   # STRUCTURAL: deterministic in the row
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
                # exception at this level is structural: a row we could not even read.
                self.counters["build_error"] += 1
                continue                                # one bad item never kills the poll
            emitted += 1
            emitted_dates.append(settlement)
        if emitted_dates:
            candidate = max(emitted_dates)
            if emit_barriers:
                # A LATER row emitting fine must not carry the cursor past an EARLIER row we
                # FAILED to emit. `settlementDateFrom` is an INCLUSIVE lower bound, so holding
                # the cursor AT the earliest failed date keeps that row in the next window;
                # anything beyond it would evict the row forever — silent evidence loss.
                candidate = min(candidate, min(emit_barriers))
            # Advance only from EMITTED items, bounded so a poison far-future date can't strand
            # the cursor.
            #
            # COLD START IS BOUNDED TOO. It previously took the batch "as-is (nothing to bound
            # against)" — but MAX_CURSOR_GAP_DAYS is measured from self._cursor, so on the FIRST
            # poll there was no ceiling at all: one hostile row dated 2099-01-01 in the very
            # first batch set the cursor to 2099 permanently, and every later poll then asked
            # FINRA for settlementDateFrom=2099-01-01 and got nothing, forever. "No prior
            # cursor" is not "no bound available" — the calendar is a bound. Clamp to today.
            #
            # THE COMPARISON IS UNCONDITIONAL, ON PURPOSE. `today` was resolved and TYPE-CHECKED at
            # the TOP of poll (_wall_clock_today raises on anything that is not a tz-aware
            # datetime), so there is nothing left to isinstance-test here. This line used to read
            # `if isinstance(today, date) and candidate > today:` — so a mis-wired clock made the
            # test False and silently DISABLED the entire poison bound: no raise, no counter, no
            # signal, cursor pinned at 2099 forever. That is the exact strand the bound exists to
            # prevent, delivered by the bound's own guard. NEVER re-add a type test to this line:
            # if `today` could be the wrong type here, the fix belongs at the SEAM, not in this
            # condition.
            if candidate > today:
                self.counters["future_settlement"] += 1
                self.counters["clamped_cursor"] += 1
                self.strand_signal = (
                    f"cursor CLAMPED to {today}: an emitted row carried settlementDate "
                    f"{candidate}, in the future. Short interest is published ~2 weeks AFTER "
                    "its settlement date, so this row is not merely early — it is poison. "
                    "Subsequent emptiness may be a CLAMP artifact, not an empty publication."
                )
                candidate = today
            if self._cursor is None:
                self._cursor = candidate
            else:
                ceiling = self._cursor + timedelta(days=MAX_CURSOR_GAP_DAYS)
                self._cursor = max(self._cursor, min(candidate, ceiling))
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
        rows = payload if isinstance(payload, list) else []
        out = []
        for r in rows:
            try:
                mapped = dict(mapper(r))
            except Exception:
                continue                                 # a bad wire row never kills the batch
            mapped.setdefault("kind", kind)
            out.append(mapped)
        return out

    return fetch
