"""WP8 — the pre-market morning brew pipeline (contract Owner B 'morning_brew.py').

Implements docs/build-scaffold-ultracode.md WP8 (morning brew: ingest window -> ONE batched
triage call -> ONE deep brief call -> validated MorningBrief -> BriefRecord + delivery) and
the SEC closures on that path: M2/M3 of docs/prompt-injection-defense.md (all feed text
enters prompts only via ``workflows.packets`` untrusted-content wrapping), SEC-09
(``scrub_output_fields`` on every MorningBrief prose field before store/post; blocked ==
quarantine, never post), SEC-14 (brief ``source_refs`` are clamped to the dispatched
packet — foreign refs are dropped and the brief is flagged degraded), SEC-23/Inv 7 (a
``limit_hit``/``timeout`` enters watch-only via ``call_agent``; the mode is unchanged and
NO event is lost — every window event id lands in exactly one disposition list of the
audited ``EVENT_TRIAGE_BATCH`` payload), and Inv 3 (a still-malformed deep output leaves a
quarantined AgentJob, no BriefRecord, nothing delivered).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from agent_runner.result import LIMIT_HIT, OK, TIMEOUT
from core.ids import new_id
from schemas.models.brief_record import BriefRecord
from workflows.agent_calls import call_agent
from workflows.budget import _HARD_EVENT_TYPES
from workflows.constants import EVENT_BRIEF, EVENT_TRIAGE_BATCH
from workflows.events import events_in_window
from workflows.packets import build_packet
from workflows.prompts import load_prompt
from workflows.scrubber import scrub_output_fields

# MorningBrief free-text fields the SEC-09 scrubber must cover before store/audit/post.
PROSE_FIELDS = ("market_regime", "portfolio_changes", "watch_items", "thesis_updates",
                "risk_flags", "proposed_actions")
MAX_PACKET_EVENTS = 150

# The triage call's timeout, sized to the BATCH rather than left at call_agent's flat 120s.
#
# THE CAP ALONE DID NOT FIX THE TIMEOUT. Bounding the packet to 150 events was the first
# response to the first live run; the second run capped correctly and TIMED OUT ANYWAY,
# because triage is OUTPUT-bound. A 150-event packet is only ~10k tokens of prompt, but the
# model must emit one scored entry PER ITEM — ~9k output tokens — and the run took ~214s
# against a 120s limit. Input grew 4x from 38 events to 150; the time grew with the OUTPUT.
#
# Two live runs measured it: 150 events in ~214s (1.43 s/event) and 100 events in ~187s
# (1.87 s/event). 2.5 s/event takes the WORSE of the two and adds margin, because the cost of
# being generous is a slower failure while the cost of being tight is a false timeout on a
# healthy run — and a false timeout trips the vendor breaker. The base covers CLI + model
# startup. The point is that the bound tracks the work, so a cap change cannot silently
# re-create a timeout that no batch can meet.
TRIAGE_TIMEOUT_BASE_SECONDS = 90.0
TRIAGE_TIMEOUT_PER_EVENT_SECONDS = 2.5

# The deep brief call's timeout. ESTIMATED, not measured — no live run has reached this stage
# yet, so this is stated as a guess rather than dressed up as data.
#
# Scaled differently from triage on purpose. The brief emits PROSE over the material/urgent
# subset, not one entry per item, so its output does not grow with the packet the way triage's
# does — but it runs on `deep` (opus, config/models.yaml), which is several times slower per
# token than triage's haiku. Hence the large base and the shallower slope.
#
# Erring generous is the cheap direction: a false timeout trips the vendor breaker and degrades
# the whole cockpit, whereas a generous ceiling only delays a genuine failure — and since the
# transport now actually kills the process tree, an overrun can no longer hang the orchestrator.
BRIEF_TIMEOUT_BASE_SECONDS = 180.0
BRIEF_TIMEOUT_PER_EVENT_SECONDS = 2.0

# Bulk reference data, not news. The earnings-calendar feed publishes a forward window for the
# WHOLE MARKET — the first live bring-up ingested 559 calendar rows against 38 news items — so
# without a filter the calendar drowns out everything the brief exists to surface.
_BULK_CALENDAR_PREFIX = "calendar:"


def partition_by_relevance(events, holdings_symbols: frozenset[str],
                           watchlist: frozenset[str]) -> tuple[list, list]:
    """Split the window into ``(eligible, ignored)`` BEFORE any model call.

    The intraday loop has always pre-filtered; the brew never did, and that is why its first
    live run dispatched the entire market's earnings calendar. But the brew must NOT copy the
    intraday filter: that one keeps only held/watched/hard events, which is right for
    *reacting* and wrong for a morning brief, whose whole job is awareness of things you do
    not yet hold. A brief filtered to your existing positions can only ever tell you what you
    already own.

    So the brew filters exactly one thing — BULK CALENDAR ROWS for symbols the operator
    neither holds nor watches. An earnings date for a company you have no relationship with is
    reference data; it is not something a human needs read to them at 08:30. News, filings and
    halts all stay eligible regardless of symbol.

    Ignored events are recorded as ``prefiltered_ignore``, the disposition ``triage_loop``
    already uses for "deliberately not analyzed" — distinct from ``deferred``, which promises
    re-coverage. Conflating the two would either lie in the audit or re-dispatch the whole
    market forever. Nothing is lost either way: ``deterministic_urgency`` reads calendar rows
    straight from the table, so a filtered row still corroborates urgency the day it matters.
    """
    relevant_symbols = holdings_symbols | watchlist
    eligible, ignored = [], []
    for ev in events:
        bulk = (ev.event_type or "").startswith(_BULK_CALENDAR_PREFIX)
        (ignored if bulk and ev.symbol not in relevant_symbols else eligible).append(ev)
    return eligible, ignored


def select_for_packet(events, cap: int, holdings_symbols: frozenset[str]) -> list:
    """Select a deterministic bounded packet while preserving presentation order."""
    if cap < 1:
        raise ValueError("Morning Brew packet cap must be positive")
    if len(events) <= cap:
        return list(events)

    def rank(event):
        hard = 0 if event.event_type in _HARD_EVENT_TYPES else 1
        held = 0 if event.symbol and event.symbol in holdings_symbols else 1
        ingested = event.ingested_at.timestamp() if event.ingested_at else 0
        return hard, held, -ingested, event.id

    selected_ids = {event.id for event in sorted(events, key=rank)[:cap]}
    return [event for event in events if event.id in selected_ids]


def _audit_batch(state, *, batch_id: str, window_start: str, window_end: str,
                 scored: list[dict], dispositions: dict[str, str], degraded: bool,
                 consumed: bool, actor: str = "morning_brew") -> None:
    """One EVENT_TRIAGE_BATCH per run: every window event id appears in EXACTLY one
    disposition (Inv 7 — nothing is ever silently dropped). ``consumed=False`` marks a
    window that must be re-covered by the next loop (degraded/quarantined runs)."""
    deferred = sorted(eid for eid, label in dispositions.items() if label == "deferred")
    state.audit.append(
        EVENT_TRIAGE_BATCH, actor=actor, refs={"batch_id": batch_id},
        payload={
            "batch_id": batch_id,
            "window_start": window_start,
            "window_end": window_end,
            "scored": scored,
            "deferred": deferred,
            "degraded": degraded,
            "consumed": consumed,
            "dispositions": dispositions,
        },
    )


def morning_brew(state, clock=None, *, transport=None, deliver=None, parquet_root=None,
                 window_hours: int = 18, holdings_symbols: frozenset[str] = frozenset(),
                 watchlist: frozenset[str] = frozenset(),
                 max_packet_events: int = MAX_PACKET_EVENTS) -> dict:
    """Run the pre-market brew. ``deliver`` is ``(channel: str, payload: dict) -> bool``;
    a False/raising deliver marks the brief undelivered but never raises. Returns a
    JSON-native summary dict (also served by POST /brief/run)."""
    clock = clock if clock is not None else state.clock
    now = clock.now()
    date = now.date().isoformat()

    with state.session_factory() as s:
        existing = s.execute(
            select(BriefRecord).where(BriefRecord.brief_date == date)
        ).scalar_one_or_none()
    if existing is not None:
        return {"status": "already_briefed", "date": date, "brief_id": existing.id}

    start = now - timedelta(hours=window_hours)
    # +1µs: the window END is inclusive (a FrozenClock ingests and brews at the same
    # instant; an event ingested exactly at `now` must not fall out of every window).
    events = events_in_window(state, start, now + timedelta(microseconds=1))
    window_start, window_end = start.isoformat(), now.isoformat()
    if not events:
        return {"status": "no_events", "date": date,
                "window_start": window_start, "window_end": window_end}

    # FILTER, then BOUND. Bulk calendar noise is deliberately not analyzed; whatever survives
    # that and still exceeds the cap keeps its "deferred" disposition, so it is audited as
    # deferred and re-covered next loop. Neither path drops an event (Inv 7).
    eligible, ignored = partition_by_relevance(events, holdings_symbols, watchlist)
    dispatched = select_for_packet(eligible, max_packet_events, holdings_symbols)
    packet = build_packet(dispatched)
    batch_id = new_id("tb")
    dispositions: dict[str, str] = {ev.id: "deferred" for ev in events}
    dispositions.update({ev.id: "prefiltered_ignore" for ev in ignored})
    over_cap = len(eligible) - len(dispatched)

    def _result(status: str, **extra) -> dict:
        deferred = sorted(k for k, v in dispositions.items() if v == "deferred")
        out = {"status": status, "date": date, "window_start": window_start,
               "window_end": window_end, "window_events": len(events),
               "prefiltered": len(ignored),
               "dispatched": len(dispatched), "deferred_count": len(deferred),
               # A COUNT plus a bounded sample, never the whole list: the first live run
               # returned 659 ids in one response body. The complete set is in the audited
               # EVENT_TRIAGE_BATCH payload, which is the record that matters.
               "deferred_sample": deferred[:20]}
        if over_cap:
            out["over_cap"] = over_cap
            out["note"] = (f"{over_cap} event(s) exceeded the {max_packet_events}-event triage "
                           "cap and were deferred to the next loop, not dropped")
        out.update(extra)
        return out

    if not dispatched:
        # Everything in the window was bulk calendar noise. That is a COMPLETE run, not a
        # degraded one — there was nothing for a model to read, so no model was asked, and
        # nothing is owed re-coverage.
        _audit_batch(state, batch_id=batch_id, window_start=window_start,
                     window_end=window_end, scored=[], dispositions=dispositions,
                     degraded=False, consumed=True)
        return _result("no_relevant_events")

    def _bail(status: str, *, scored: list[dict], degraded: bool) -> dict:
        _audit_batch(state, batch_id=batch_id, window_start=window_start,
                     window_end=window_end, scored=scored, dispositions=dispositions,
                     degraded=degraded, consumed=False)
        return _result(status)

    # ---- ONE batched triage call --------------------------------------------------------
    t_tmpl = load_prompt("triage_batch")
    t_prompt = t_tmpl.render(batch_id=batch_id, window_start=window_start,
                             window_end=window_end, packet_block=packet.prompt_block())
    t_result, _t_job = call_agent(
        state, alias="triage", prompt_ref=t_tmpl.ref, prompt_hash=t_tmpl.sha256,
        prompt=t_prompt, schema_name="TriageResult",
        input_refs={"raw_refs": sorted(packet.dispatched_refs)}, transport=transport,
        timeout_s=TRIAGE_TIMEOUT_BASE_SECONDS
        + TRIAGE_TIMEOUT_PER_EVENT_SECONDS * len(dispatched),
    )
    if t_result.status in (LIMIT_HIT, TIMEOUT):
        return _bail("degraded_watch_only", scored=[], degraded=True)
    if t_result.status != OK:
        return _bail("quarantined", scored=[], degraded=False)

    by_ref = {ev.raw_ref: ev for ev in dispatched}
    scored_entries: list[dict] = []
    class_by_event: dict[str, str] = {}
    for item in t_result.validated.get("scored", []):
        ev = by_ref.get(item.get("article_id"))
        if ev is None:
            continue  # a hallucinated ref scores nothing (the id set is ours, not the model's)
        scored_entries.append({
            "event_id": ev.id, "article_id": item.get("article_id"),
            "classification": item.get("classification"), "score": item.get("score"),
        })
        class_by_event[ev.id] = item.get("classification")
    # survivors the model failed to score keep the "deferred" disposition (never dropped)

    # ---- ONE deep brief call over the material/urgent packet ----------------------------
    brief_events = [ev for ev in events if class_by_event.get(ev.id) in ("material", "urgent")]
    brief_packet = build_packet(brief_events)
    b_tmpl = load_prompt("morning_brief")
    b_prompt = b_tmpl.render(date=date, packet_block=brief_packet.prompt_block())
    b_result, b_job = call_agent(
        state, alias="deep", prompt_ref=b_tmpl.ref, prompt_hash=b_tmpl.sha256,
        prompt=b_prompt, schema_name="MorningBrief",
        input_refs={"raw_refs": sorted(brief_packet.dispatched_refs)}, transport=transport,
        timeout_s=BRIEF_TIMEOUT_BASE_SECONDS
        + BRIEF_TIMEOUT_PER_EVENT_SECONDS * len(brief_events),
    )
    if b_result.status in (LIMIT_HIT, TIMEOUT):
        return _bail("degraded_watch_only", scored=scored_entries, degraded=True)
    if b_result.status != OK:
        # Inv 3: quarantined AgentJob; brief skipped; events deferred — never silently
        # retried into execution.
        return _bail("quarantined", scored=scored_entries, degraded=False)

    brief = dict(b_result.validated)
    cited = list(brief.get("source_refs") or [])
    kept_refs = [r for r in cited if r in brief_packet.dispatched_refs]
    degraded_refs = len(kept_refs) != len(cited)   # foreign refs dropped -> flag degraded
    brief["source_refs"] = kept_refs

    # SEC-09: scrub EVERY prose field before store/audit/post.
    subject = frozenset(i.symbol for i in packet.items if i.symbol)
    brief, blocked = scrub_output_fields(state, brief, PROSE_FIELDS,
                                         subject_symbols=subject, target="morning_brief")
    if blocked:
        return _bail("quarantined", scored=scored_entries, degraded=False)

    row = BriefRecord(id=new_id("brief"), brief_date=date, payload=brief, job_id=b_job.id,
                      degraded=degraded_refs, created_at=now)
    state.audit.record_state_change(
        row, EVENT_BRIEF, actor="morning_brew",
        payload={"brief_date": date, "degraded": degraded_refs, "job_id": b_job.id},
    )

    for ev in events:
        dispositions[ev.id] = "scored" if ev.id in class_by_event else "deferred"
    _audit_batch(state, batch_id=batch_id, window_start=window_start,
                 window_end=window_end, scored=scored_entries, dispositions=dispositions,
                 degraded=False, consumed=True)

    delivered = False
    if deliver is not None:
        try:
            delivered = bool(deliver("#brief", {"date": date, "degraded": degraded_refs,
                                                "brief": brief}))
        except Exception:
            delivered = False   # delivery failure is reported, never raised

    return _result("ok", brief_id=row.id, degraded=degraded_refs, delivered=delivered,
                   scored=len(scored_entries))
