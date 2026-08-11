"""RW-16 — the congressional poller: two chambers, one feed, nothing silent.

WHAT THIS DRIVES, AND WHAT IT REFUSES TO DECIDE
------------------------------------------------
Providers fetch and parse (``house_clerk.py`` / ``senate_efd.py`` -> ``ptr_parse.py``); this
module owns everything ABOVE one document: which window to ask for, which rows are new, where
the evidence is stored, and what reaches the awareness layer. It decides nothing about whether a
disclosure is worth acting on — that is ``copytrade/engine.py``, deterministically, later, and
still behind risk-pass -> mode -> approval -> ``OrderGateway`` (Invariant 1).

No model, no LLM, no ``brokers/``, no ``risk/`` is reachable from this file. A congressional
filing is EVIDENCE; this is the part of the system that makes sure the evidence arrives.

EVERY DISCLOSURE IS EMITTED AS A MarketEvent, EVEN THE ONES NOBODY WILL TRADE
------------------------------------------------------------------------------
The copy engine follows a short list of filers and declines most rows (wrong filer, too stale,
an untradeable asset, an amount below the floor). Those declines are the copy ENGINE's opinion,
and they must not decide what the OPERATOR gets to see. Engine 1 (awareness) is the reliable
half of this cockpit; Engine 2 (execution) is the experimental half. So the poller emits every
readable, attributable transaction into ``on_event`` and lets the engine skip whatever it likes
downstream. A feed that only surfaced the rows it intended to trade would make "we saw nothing"
and "we chose nothing" the same observation — which is the exact silence this package exists to
refuse.

THE EVENT IS ENRICHMENT, NEVER A HARD EVENT
---------------------------------------------
``event_type`` is ``"congress_disclosure"`` and is asserted NOT to be ``"halt"`` or ``"filing"``
— the entire membership of ``workflows/budget.py::_HARD_EVENT_TYPES`` and of the triage
pre-filter, which auto-corroborate on EXACT membership. A PTR is 30-45 days stale by law; wiring
it into auto-corroborated urgency would be the same defect ``ingest/finra_short_interest.py``
guards against, committed with even older data.

THE FORWARD-PROGRESS CONTRACT (DEFECT D — THIRD TIME; DO NOT LOOSEN)
---------------------------------------------------------------------
``ingest/edgar_brew.py`` and ``ingest/finra_short_interest.py`` each shipped the same bug and
each paid for it in adversarial review: the dedup key was committed BEFORE ``on_event`` and a
raising consumer was counted as a terminal drop. A MOMENTARY downstream outage therefore
destroyed the row permanently, and a later row in the same batch carried the cursor past it,
while the counters recorded a tidy "drop". Two rules, both enforced below:

  1. THE KEY MEANS "THIS WAS EMITTED", NEVER "WE TRIED". ``Dedup.add`` is a check-and-set with
     no race-free peek, so it must be taken before ``on_event`` and GIVEN BACK
     (``Dedup.discard``) when the emit raises.
  2. THE CURSOR IS A BARRIER AT THE EARLIEST ROW THAT FAILED TO EMIT. The cursor is a filed-date
     high-water mark and the provider window is INCLUSIVE, so a LATER row emitting fine would
     otherwise carry the window past the failed EARLIER row and evict it from every future
     fetch.

A congressional PTR is the worst thing in this repo to lose that way: it is published once, in a
daily batch, by a server with no replay API, and the window that contained it closes. So a failed
emit WEDGES THE FEED LOUDLY (counted, named, signalled, cursor held) rather than losing data.

``on_event`` raising is CAUGHT here, exactly as in both sibling pollers — it is the signal the
contract is built on, not an error to propagate. What must never be swallowed is the layer BELOW:
``orchestrator/ingest_runner.PersistSink`` calls ``persist_events`` with a ONE-ITEM list and lets
its failure propagate INTO this handler. Batching there would make ``on_event`` return
successfully for rows that were never persisted, and both rules above would be defeated from the
outside. See that module's "ONE persist_events CALL PER EVENT".

RETRIABLE VS STRUCTURAL — WHY A PARSE ERROR SOMETIMES HOLDS THE WINDOW
------------------------------------------------------------------------
``provider.STRUCTURAL_PARSE_REASONS`` exists for this decision. A RETRIABLE error (a 5xx, a
truncated download) means the evidence is good and arrives next cycle: hold. A STRUCTURAL one (a
scanned paper PTR with no text layer, a row matching no known grammar) can NEVER be read, and
holding for it wedges the feed forever — a permanent wedge is how a feed dies quietly. Parse-error
locators are DOCUMENT IDS, not dates, so a retriable error cannot be pinned to a day: the only
honest barrier is the whole window, and the cursor for that chamber does not move at all.

PER-CHAMBER FAULT ISOLATION, PER-CHAMBER CURSOR
-------------------------------------------------
One provider raising must not cost the other its evidence (the eFD terms page changing must not
take the House feed down), so each ``fetch`` is isolated exactly like
``ingest/edgar_pipeline._poll_isolated`` isolates one issuer. The cursors are per-provider for
the same reason: the chambers publish independently, and letting a Senate barrier hold the House
window would spread one chamber's fault into the other's data by a different route.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from copytrade.models import Disclosure
from ingest.clock_contract import resolve_wall_clock, utc_now
from ingest.congress.provider import ProviderResult, is_retriable_reason
from ingest.dedup import Dedup
from ingest.normalize import to_market_event

log = logging.getLogger("cockpit.ingest.congress")

# ``ingest.normalize.ALLOWED_SOURCES`` already authorizes this literal (§5 feed allowlist). This
# module does NOT register itself at import: a self-registering allowlist is import-order
# dependent and stops being auditable at one greppable line.
SOURCE = "congress"

EVENT_CONGRESS_DISCLOSURE = "congress_disclosure"

# The ONLY members of ``workflows/budget.py::_HARD_EVENT_TYPES`` and of the triage pre-filter.
# Enrichment — and a 30-45 day old filing is enrichment — may never emit these.
_FORBIDDEN_EVENT_TYPES = frozenset({"halt", "filing"})

# Cold-start window, in days back from today on the FILED date. 45 is the STOCK Act's disclosure
# deadline, so one cold window covers a full legal filing cycle: any trade whose report is still
# within its statutory deadline is reachable on the first poll. Not a tuning knob — a shorter
# window silently starts the feed mid-cycle, and a longer one asks a small government file server
# for a backlog on every fresh deployment.
COLD_START_LOOKBACK_DAYS = 45

# Human-readable transaction verbs for the headline. Purely presentational; the machine-readable
# value travels in ``raw["tx_type"]`` and in the stored row, never parsed back out of prose.
_TX_VERB = {
    "purchase": "bought",
    "sale": "sold",
    "sale_partial": "partially sold",
    "exchange": "exchanged",
}


def _assert_enrichment_event_type(event_type: str) -> str:
    """Fail closed if this connector ever tries to emit an auto-corroborating type."""
    if event_type in _FORBIDDEN_EVENT_TYPES:
        raise ValueError(
            f"congressional disclosures are enrichment-only and must never emit {event_type!r}: "
            "it is a member of budget._HARD_EVENT_TYPES and would drive SEC-21 urgency from a "
            "filing that the STOCK Act allows to be up to 45 days old"
        )
    return event_type


@dataclass
class PollResult:
    """One cycle across every chamber. EVERY non-emitting outcome is counted and NAMED.

    A feed that reports nothing must be distinguishable from a feed that is broken — the property
    ``EdgarPipelineResult``'s counters exist to preserve, restated for two providers. The three
    observations this type keeps apart, which look identical from outside:

      * "Congress was quiet"      -> providers_run > 0, documents_seen == 0, no errors
      * "we read 40 filings and   -> documents_seen == 40, emitted == 0, parse_errors == 40
         parsed none of them"
      * "the feed is switched off" -> providers_unprovisioned names the chamber and the reason

    ``emitted`` is the throughput number (events that reached ``on_event`` and RETURNED).
    ``disclosures_new`` is larger whenever a new row failed to emit — that gap IS the wedge, and
    collapsing the two fields would hide it.
    """

    emitted: int = 0
    providers_run: int = 0
    providers_unprovisioned: list[tuple[str, str]] = field(default_factory=list)
    documents_seen: int = 0
    disclosures_new: int = 0
    disclosures_duplicate: int = 0
    persisted: int = 0
    symbol_less: int = 0
    parse_errors: list[tuple[str, str]] = field(default_factory=list)
    provider_errors: list[tuple[str, str]] = field(default_factory=list)
    emit_errors: list[tuple[str, str]] = field(default_factory=list)
    build_errors: list[tuple[str, str]] = field(default_factory=list)
    store_errors: list[tuple[str, str]] = field(default_factory=list)
    windows_held: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """At least one chamber ran, all of them are provisioned, and nothing failed."""
        return (self.providers_run > 0
                and not self.providers_unprovisioned
                and not self.provider_errors
                and not self.parse_errors
                and not self.emit_errors
                and not self.build_errors
                and not self.store_errors)

    @property
    def quiet(self) -> bool:
        """A GENUINE quiet cycle: the chambers were reached and had published nothing new.

        Requires ``documents_seen == 0``. A cycle that READ documents and emitted nothing is not
        quiet — it is a parsing failure wearing a quiet cycle's clothes.
        """
        return self.ok and self.documents_seen == 0 and self.emitted == 0

    def summary(self) -> str:
        if not self.providers_run and self.providers_unprovisioned:
            names = ", ".join(n for n, _ in self.providers_unprovisioned)
            return f"congress: NOT PROVISIONED ({names})"
        parts = [
            f"congress: {self.providers_run} provider(s)",
            f"{self.documents_seen} document(s)",
            f"{self.disclosures_new} new disclosure(s)",
            f"{self.emitted} emitted",
            f"{self.persisted} persisted",
            f"{self.disclosures_duplicate} duplicate(s)",
        ]
        for label, seq in (("unprovisioned", self.providers_unprovisioned),
                           ("provider error", self.provider_errors),
                           ("parse error", self.parse_errors),
                           ("emit error", self.emit_errors),
                           ("build error", self.build_errors),
                           ("store error", self.store_errors)):
            if seq:
                parts.append(f"{len(seq)} {label}(s)")
        if self.symbol_less:
            parts.append(f"{self.symbol_less} unattributable")
        if self.windows_held:
            parts.append(f"{len(self.windows_held)} window(s) HELD")
        return ", ".join(parts)


class CongressPoller:
    """Drive every ``DisclosureProvider`` once, dedup, store the evidence, emit the awareness.

    ``on_event`` and the evidence ``store`` are bound at CONSTRUCTION (the pinned RW-16 contract)
    while the WP3 pollers take their sink per tick; ``main.CongressPollAdapter`` owns that
    difference so neither contract has to be renegotiated at the other's expense.

    ``store`` is a session factory — the same object ``storage.engine.make_session_factory``
    returns. It is OPTIONAL: with no store the feed still emits awareness, and the absence is
    NOTED on every cycle rather than assumed. ``session_factory=`` is accepted as a synonym so
    the caller can spell it either way; passing both and disagreeing is a wiring bug and raises.
    """

    def __init__(self, providers, *, on_event, session_factory=None, dedup=None,
                 clock=utc_now, store=None):
        self._providers = tuple(providers or ())
        self._on_event = on_event
        if store is not None and session_factory is not None and store is not session_factory:
            raise ValueError(
                "CongressPoller got two DIFFERENT evidence stores (store= and session_factory=). "
                "They are synonyms for one seam; two of them means half the evidence would land "
                "somewhere nobody is reading."
            )
        self._store = store if store is not None else session_factory
        self._dedup = dedup if dedup is not None else Dedup(capacity=50_000)
        # The CALENDAR seam. Validated through the shared ``clock_contract`` on every poll, before
        # any fetch, so a mis-wired ``clock=time.monotonic`` raises loudly instead of asking both
        # chambers for a window in 1970 and reporting the empty answer as a quiet week.
        self._clock = clock if clock is not None else utc_now
        # Filed-date high-water mark PER PROVIDER. See the module docstring: the chambers publish
        # independently and must not hold each other's windows.
        self._cursors: dict[str, date] = {}
        # LIFETIME counters, merged into ``IngestRunner._signals`` (which filters the zeroes).
        # Every one of these is a NON-EMITTING outcome that would otherwise be invisible:
        #   duplicate            — dedup suppressed an already-emitted key (normal; the window
        #                          is re-fetched every cycle by construction)
        #   symbol_less          — a real disclosure that maps to no ticker (a bond, a fund, real
        #                          estate). Stored as evidence, NOT emitted: MarketEvent is
        #                          symbol-indexed, so an event naming nobody reaches nobody
        #   emit_errors          — on_event raised: TRANSIENT, key rolled back, cursor barred
        #   build_errors         — to_market_event refused the row (a normalizer boundary)
        #   store_errors         — the evidence write failed: the whole batch is held
        #   parse_errors_*       — split by retriability because the CURSOR treats them
        #                          differently, and a single total would hide which kind happened
        #   provider_errors      — one chamber raised; the other chamber is unaffected
        #   providers_unprovisioned — a chamber that is switched off, never "a quiet chamber"
        self.counters = {"polls": 0, "emitted": 0, "persisted": 0, "duplicate": 0,
                         "symbol_less": 0, "emit_errors": 0, "build_errors": 0,
                         "store_errors": 0, "parse_errors_retriable": 0,
                         "parse_errors_structural": 0, "provider_errors": 0,
                         "providers_unprovisioned": 0, "windows_held": 0}
        # A held window / failed emit is NOT "Congress published nothing". Both signals are read
        # off this object by ``IngestRunner._signals`` and surfaced in ``GET /status``.
        self.progress_signal: str | None = None
        self.strand_signal: str | None = None

    @property
    def cursor(self) -> dict:
        """The per-chamber filed-date high-water marks. A copy: nobody edits a cursor by hand."""
        return dict(self._cursors)

    # ------------------------------------------------------------------------------ the cycle

    def poll(self) -> PollResult:
        """One cycle over every provider. Returns counts; NEVER returns None, never silent.

        Raises only on a mis-wired clock (``ClockContractError`` — a deterministic wiring bug that
        ``IngestRunner.poll`` deliberately does not catch) or a programming error in this module.
        A dead counterparty, an unreadable PDF, an unprovisioned chamber and a downstream outage
        are all COUNTED states, because each of them has a different correct operator response and
        none of them is "the feed is fine".
        """
        today = self._today()               # VALIDATE FIRST — before any fetch, on every poll
        self.counters["polls"] += 1
        result = PollResult()
        if not self._providers:
            result.notes.append("no providers wired: nothing was fetched from either chamber")
        if self._store is None:
            result.notes.append(
                "no evidence store wired (store=None): disclosures are emitted but NOT persisted, "
                "so a re-run of the copy engine would have to re-fetch a government file server"
            )
        for provider in self._providers:
            name = str(getattr(provider, "name", "") or type(provider).__name__)
            self._poll_isolated(provider, name, today, result)
        self._publish_signals(result)
        return result

    def _poll_isolated(self, provider, name: str, today: date, result: PollResult) -> None:
        """Fetch and drain ONE chamber, isolating its faults from the other's evidence.

        Mirrors ``ingest/edgar_pipeline._poll_isolated``. The blanket ``except`` is correct HERE
        (and is not in that function) because there is no back-off directive to honour: neither
        chamber publishes a fair-access halt, providers already convert a dead counterparty into a
        COUNTED parse error, and every request is throttled inside the provider. So an exception
        reaching this frame is a bug or a genuinely novel failure — either way, one chamber's.
        """
        since = self._cursors.get(name) or (today - timedelta(days=COLD_START_LOOKBACK_DAYS))
        try:
            fetched = provider.fetch(since=since, today=today)
        except Exception as exc:            # noqa: BLE001 - isolate ONE chamber's fault
            result.provider_errors.append((name, type(exc).__name__))
            self.counters["provider_errors"] += 1
            # The cursor is UNTOUCHED: the window was never read, so it stays re-fetchable.
            log.warning("congress provider %s FAILED (%r); the other chamber is unaffected",
                        name, exc)
            return
        if not isinstance(fetched, ProviderResult):
            # A provider that returned something else (None, a bare list) is broken in a way that
            # would otherwise read as "this chamber filed nothing". Name it.
            result.provider_errors.append((name, f"bad_result:{type(fetched).__name__}"))
            self.counters["provider_errors"] += 1
            return
        if not fetched.provisioned:
            reason = str(fetched.unprovisioned_reason or "unprovisioned")
            result.providers_unprovisioned.append((name, reason))
            self.counters["providers_unprovisioned"] += 1
            # NOT counted as a run and NOT advanced: a switched-off chamber has observed nothing,
            # so pretending its window was covered would evict the filings it never fetched.
            return
        result.providers_run += 1
        self._drain(name, fetched, result)

    def _drain(self, name: str, fetched: ProviderResult, result: PollResult) -> None:
        """Store, dedup, emit and finally move the cursor for one chamber's cycle."""
        result.documents_seen += max(0, int(fetched.documents_seen or 0))
        for note in fetched.notes or ():
            result.notes.append(f"{name}: {note}")

        # ---- classify the parse errors: retriable ones are a BARRIER, structural ones are not
        retriable_hold = False
        for locator, reason in fetched.parse_errors or ():
            result.parse_errors.append((f"{name}:{locator}", str(reason)))
            if is_retriable_reason(str(reason)):
                retriable_hold = True
                self.counters["parse_errors_retriable"] += 1
            else:
                self.counters["parse_errors_structural"] += 1

        # ---- deterministic order. Earliest FILING first so the cursor's barrier arithmetic is
        # about dates and not about whatever order a ZIP happened to yield; ``key`` breaks ties
        # totally, so two runs over the same window emit in the same order (Invariant 2).
        rows: list[Disclosure] = sorted(fetched.disclosures or (),
                                        key=lambda d: (d.filed_date, d.key))

        # ---- EVIDENCE FIRST (Invariant 8). One idempotent transaction for the chamber's batch.
        # If the store refuses, NOTHING is emitted and NOTHING is deduped: the whole window stays
        # re-fetchable. Emitting an event whose evidence was never stored would burn the dedup key
        # against a row that can never be re-derived, which is the loss this feed cannot take.
        if rows and self._store is not None:
            try:
                result.persisted += self._persist(rows)
            except Exception as exc:        # noqa: BLE001 - a storage fault holds, never drops
                result.store_errors.append((name, type(exc).__name__))
                self.counters["store_errors"] += 1
                result.windows_held.append(f"{name}: evidence store failed ({exc!r})")
                self.counters["windows_held"] += 1
                log.warning("congress %s: evidence store FAILED (%r); window held, nothing "
                            "emitted, everything remains re-fetchable", name, exc)
                return

        advance: list[date] = []            # filing dates this cycle has genuinely finished with
        emit_barriers: list[date] = []      # filing dates a later row must NOT carry the cursor past
        for d in rows:
            symbol = d.symbol
            if not symbol:
                # A real disclosure with no ticker (a bond, a fund, real estate, crypto). The
                # evidence is stored above; the EVENT is not built, because ``MarketEvent`` is
                # symbol-indexed and an event that names nobody reaches nobody. Counted, not
                # silent — and it is finished with, so it does not hold the window.
                result.symbol_less += 1
                self.counters["symbol_less"] += 1
                advance.append(d.filed_date)
                continue
            if not self._dedup.add(d.key):
                result.disclosures_duplicate += 1
                self.counters["duplicate"] += 1
                # A CONFIRMED duplicate is safe cursor evidence: the key's contract is a prior
                # SUCCESSFUL emit. This is what lets a retried earlier row release its barrier
                # instead of pinning the window forever.
                advance.append(d.filed_date)
                continue
            result.disclosures_new += 1
            try:
                event = self._to_event(d)
            except Exception as exc:        # noqa: BLE001 - a refused build is held, not dropped
                self._dedup.discard(d.key)
                result.build_errors.append((d.key, type(exc).__name__))
                self.counters["build_errors"] += 1
                emit_barriers.append(d.filed_date)
                continue
            if event.get("symbols") != [symbol]:
                # Defence in depth. ``normalize._clean_symbols`` silently filters a ticker that
                # fails ``_TICKER``, and an event with ``symbols == []`` would emit "successfully"
                # while naming nobody — an UNCOUNTED attribution loss in the one module whose
                # stated invariant is that no drop is silent (the round-6 defect in both sibling
                # pollers). Refuse it through a counted path and hold its window.
                self._dedup.discard(d.key)
                result.symbol_less += 1
                self.counters["symbol_less"] += 1
                emit_barriers.append(d.filed_date)
                continue
            try:
                self._on_event(event)
            except Exception as exc:        # noqa: BLE001 - TRANSIENT downstream, NOT a drop
                # RULE 1: give the key back. RULE 2: bar the cursor. See the module docstring.
                self._dedup.discard(d.key)
                result.emit_errors.append((d.key, type(exc).__name__))
                self.counters["emit_errors"] += 1
                emit_barriers.append(d.filed_date)
                log.warning("congress %s: emit FAILED for %s (%r); dedup key rolled back and the "
                            "cursor is barred from passing %s", name, d.key, exc, d.filed_date)
                continue
            result.emitted += 1
            self.counters["emitted"] += 1
            advance.append(d.filed_date)

        self._advance_cursor(name, advance, emit_barriers, retriable_hold, result)

    # -------------------------------------------------------------------------------- cursor

    def _advance_cursor(self, name: str, advance: list[date], emit_barriers: list[date],
                        retriable_hold: bool, result: PollResult) -> None:
        """Move one chamber's filed-date high-water mark, or say WHY it did not move.

        Three refusals, in order of strength:

        ``retriable_hold`` is TOTAL. A retriable parse error's locator is a DOCUMENT ID, not a
        date, so the failed document cannot be pinned to a day and any advance at all could skip
        it under an inclusive window. Hold the cursor exactly where it was. A STRUCTURAL error
        never reaches here as a hold: that document can never be read, and holding for it would
        wedge the chamber forever.

        ``emit_barriers`` clamps AFTER the monotonic max, on purpose. Clamping before it (the
        shape ``ingest/finra_short_interest.py`` uses, where ``max(cursor, candidate)`` runs last)
        lets an already-advanced cursor swallow a barrier for a row filed BEFORE it, which is the
        precise eviction rule 2 exists to prevent. The resulting backwards step is bounded by an
        observed filing date inside a window that already started at or before it, so its only
        cost is a re-fetch — and a re-fetch is always cheaper than a filing nobody can see again.

        Nothing to advance ON is also a refusal: a cycle that read rows but finished with none of
        them has no evidence that the window is covered.
        """
        if retriable_hold:
            held = self._cursors.get(name)
            result.windows_held.append(
                f"{name}: retriable parse error(s) — window held at "
                f"{held.isoformat() if held else 'cold start'}"
            )
            self.counters["windows_held"] += 1
            return
        if not advance:
            if emit_barriers:
                result.windows_held.append(
                    f"{name}: every new row failed to emit — window held at "
                    f"{min(emit_barriers).isoformat()}"
                )
                self.counters["windows_held"] += 1
            return
        previous = self._cursors.get(name)
        candidate = max(advance)
        if previous is not None:
            candidate = max(previous, candidate)
        if emit_barriers:
            candidate = min(candidate, min(emit_barriers))
            result.windows_held.append(
                f"{name}: cursor limited to {candidate.isoformat()} by "
                f"{len(emit_barriers)} row(s) that failed to emit"
            )
            self.counters["windows_held"] += 1
        self._cursors[name] = candidate

    # -------------------------------------------------------------------------------- pieces

    def _today(self) -> date:
        """The window's end, from the INJECTED wall clock, VALIDATED FIRST then narrowed.

        ``resolve_wall_clock`` is the SHARED validator both other connectors call: one contract
        (a zero-arg callable returning a tz-AWARE datetime), one ``ClockContractError``, one
        answer. Narrowing to a calendar day happens explicitly in UTC and only AFTER validation —
        never guarded by an ``isinstance`` test of the clock's own output, which is how a poison
        bound became a silent no-op in ``finra_short_interest`` for two rounds.
        """
        wall = resolve_wall_clock(self._clock, seam="CongressPoller(clock=...)")
        return wall.astimezone(timezone.utc).date()

    def _persist(self, rows: list[Disclosure]) -> int:
        """Write the evidence. Imported lazily so this module stays importable without SQLAlchemy
        and so ``ingest/`` keeps no module-level dependency on ``schemas/``."""
        from schemas.models.congress_disclosure import persist_disclosures

        written = persist_disclosures(self._store, rows)
        self.counters["persisted"] += written
        return written

    def _to_event(self, d: Disclosure) -> dict:
        """Build the awareness event for one disclosure.

        EVERY free-text field here has ALREADY been escaped at the parse boundary
        (``ptr_parse.build_disclosure`` -> ``escape_untrusted`` for ``filer_display`` and
        ``asset_description``) AND is escaped again by ``to_market_event``. The escaper is a
        FIXED POINT (``f(f(x)) == f(x)``), so the double pass is genuinely free — it neither
        corrupts an ampersand nor weakens the neutralization — and the boundary holds even if a
        future provider builds a ``Disclosure`` some other way.

        THE AUDIT CHAIN. ``normalize.content_hash`` hashes whatever ``raw=`` carries, so ``raw``
        deliberately carries the row's OWN ``content_hash`` and ``raw_ref``. The chain is:
        cockpit event content_hash -> the row's content_hash -> (document digest + the untouched
        row text) -> the PDF/HTML at ``raw_ref``. An operator re-fetches the primary document and
        can prove the event came from those bytes (Invariant 8 / SEC-13).

        ``ts`` is the FILED date at UTC midnight, as an ISO string (``workflows.events._parse_ts``
        reads that spelling). The filing date is when the information became PUBLIC, which is the
        only instant this feed actually observes — the forms carry no time of day, and inventing
        one would put a fabricated precision into an audited timestamp. The trade date is NOT the
        event time; it travels in ``raw`` and in the stored row, where the staleness gate reads it.
        """
        verb = _TX_VERB.get(d.tx_type, d.tx_type)
        return to_market_event(
            source=SOURCE,
            article_id=d.key,
            symbols=[d.symbol],
            headline=(f"{d.filer_display} {verb} {d.symbol} ({d.amount.label}) on "
                      f"{d.tx_date.isoformat()} — disclosed {d.filed_date.isoformat()}, "
                      f"{d.disclosure_lag_days}d later"),
            summary=d.asset_description,
            ts=datetime.combine(d.filed_date, time(0, 0), tzinfo=timezone.utc).isoformat(),
            event_type=_assert_enrichment_event_type(EVENT_CONGRESS_DISCLOSURE),
            raw={
                "key": d.key,
                "chamber": d.chamber,
                "doc_id": d.doc_id,
                "line_no": d.line_no,
                "filer_slug": d.filer_slug,
                "tx_date": d.tx_date.isoformat(),
                "filed_date": d.filed_date.isoformat(),
                "disclosure_lag_days": d.disclosure_lag_days,
                "tx_type": d.tx_type,
                "asset_type": d.asset_type,
                "amount_low": str(d.amount.low),
                "amount_high": None if d.amount.high is None else str(d.amount.high),
                "amount_label": d.amount.label,
                "symbol": d.symbol,
                "option_expiry": None if d.option_expiry is None else d.option_expiry.isoformat(),
                "option_strike": None if d.option_strike is None else str(d.option_strike),
                "raw_ref": d.raw_ref,
                "row_content_hash": d.content_hash,
            },
        )

    def _publish_signals(self, result: PollResult) -> None:
        """Publish the two durable strings ``IngestRunner._signals`` surfaces on /status.

        The whole point is that a cycle returning zero is never ambiguous. Ordered most-severe
        first so the operator reads the reason that needs acting on, not the first one alphabet
        order happened to produce.
        """
        self.strand_signal = None
        if result.providers_unprovisioned:
            names = ", ".join(f"{n} ({why})" for n, why in result.providers_unprovisioned)
            self.strand_signal = (
                f"CHAMBER NOT PROVISIONED: {names}. Its filings were never fetched, so this "
                "cycle's silence for that chamber is a configuration fact, not a quiet week."
            )

        if result.store_errors:
            self.progress_signal = (
                f"NO FORWARD PROGRESS — WINDOW HELD: the evidence store refused "
                f"{len(result.store_errors)} chamber batch(es). Nothing was emitted and no dedup "
                "key was taken; every disclosure remains re-fetchable. Not a quiet publication."
            )
        elif result.emit_errors:
            self.progress_signal = (
                f"NO FORWARD PROGRESS — CURSOR LIMITED: {len(result.emit_errors)} valid "
                "disclosure(s) FAILED TO EMIT. Their dedup keys were rolled back and the cursor "
                "is barred from passing them, so they remain re-fetchable. Not a quiet "
                "publication."
            )
        elif result.build_errors:
            self.progress_signal = (
                f"NO FORWARD PROGRESS — CURSOR LIMITED: {len(result.build_errors)} disclosure(s) "
                "could not be built into an event. Held for retry rather than discarded; a "
                "normalizer boundary may have drifted. Not a quiet publication."
            )
        elif result.windows_held:
            self.progress_signal = (
                "NO FORWARD PROGRESS — WINDOW HELD: " + "; ".join(result.windows_held[:4])
                + ". Retriable failures keep evidence re-fetchable; not a quiet publication."
            )
        elif result.provider_errors:
            self.progress_signal = (
                f"CHAMBER FAILED: {len(result.provider_errors)} provider(s) raised "
                f"({', '.join(n for n, _ in result.provider_errors)}); their windows were not "
                "read and were not advanced. The other chamber's evidence is unaffected."
            )
        elif result.documents_seen and not result.emitted and not result.disclosures_duplicate:
            self.progress_signal = (
                f"NO EMISSIONS FROM {result.documents_seen} DOCUMENT(S): every row was refused "
                f"({len(result.parse_errors)} parse error(s), {result.symbol_less} "
                "unattributable). This is a reading failure, not a quiet publication."
            )
        elif not result.providers_run:
            self.progress_signal = (
                "NO CHAMBER RAN this cycle: nothing was fetched, so nothing can be concluded "
                "about what Congress published."
            )
        else:
            self.progress_signal = None
