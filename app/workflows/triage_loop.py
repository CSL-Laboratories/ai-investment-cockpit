"""WP8 — the intraday triage loop (contract Owner B 'triage_loop.py').

Implements docs/build-scaffold-ultracode.md WP8 (intraday loop: window -> deterministic
pre-filters -> ONE batched triage call -> gated deep escalations) and the SEC closures on
that path: SEC-21 (deterministic urgency corroboration — a model-labeled 'urgent' with no
calendar/halt-filing/quote-move backing is DEMOTED, never budgeted; chain-derived
``DeepBudget`` capped by the audited limits; ``rank_candidates`` orders by signal strength,
never arrival), SEC-01 (same-ticker burst >= 4 -> ``BreakerEngine.check_order_path`` with
the deterministic ``injection_predicate``; excess candidates go to the deferred lane),
SEC-23/Inv 7 (a mid-loop ``limit_hit`` enters watch-only via ``call_agent``; remaining
candidates are ALL deferred; every window event id lands in EXACTLY one disposition of the
audited ``EVENT_TRIAGE_BATCH`` payload — nothing dropped; an unconsumed window is re-covered
by the next fresh loop), and L6 sampling (``BreakerSignals`` from this run's agent-job
statuses -> ``evaluate_and_trip``).

Invariant 1: this module produces proposals/alerts only; order EXECUTION stays in brokers/.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from agent_runner.result import LIMIT_HIT, OK, TIMEOUT
from core.ids import new_id
from ingest.cramer_signal import inverse_side
from orchestrator.breakers import BreakerEngine, BreakerSignals
from orchestrator.limits import current_limits
from schemas.models.audit_entry import AuditEntry
from schemas.models.market_event import MarketEvent
from workflows.agent_calls import call_agent
from workflows.budget import (
    BudgetIntegrityError,
    DeepBudget,
    deterministic_urgency,
    make_event_type_verifier,
    rank_candidates,
)
from workflows.constants import EVENT_ESCALATION_DEFERRED, EVENT_TRIAGE_BATCH
from workflows.events import events_in_window
from workflows.grounding import injection_predicate
from workflows.packets import build_packet
from workflows.prompts import load_prompt
from workflows.proposals import draft_proposal

_FALLBACK_WINDOW = timedelta(minutes=30)
_BURST_THRESHOLD = 4          # SEC-21 same-ticker novelty breaker (>= 4 candidates, one tick)
# cramer_call:buy/sell (Inverse Cramer): a deterministically-detected Cramer call is kept
# regardless of holdings/watchlist membership, same posture as halt/filing.
_HARD_EVENT_TYPES = frozenset({"halt", "filing", "cramer_call:buy", "cramer_call:sell"})


def _last_consumed_window_end(state) -> datetime | None:
    """The window_end of the last HMAC-VERIFIED, CONSUMED EVENT_TRIAGE_BATCH entry.
    Degraded/quarantined batches are skipped so their (deferred, unprocessed) window is
    re-covered by the next fresh loop — deferral never becomes loss (Inv 7)."""
    with state.session_factory() as s:
        entries = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_TRIAGE_BATCH)
            .order_by(AuditEntry.seq.desc())
        ).scalars().all()
    for e in entries:
        payload = e.payload or {}
        if not payload.get("consumed", not payload.get("degraded")):
            continue
        if not state.audit.verify_entry(e):
            continue
        we = payload.get("window_end")
        if not we:
            continue
        try:
            return datetime.fromisoformat(we)
        except ValueError:
            continue
    return None


def _pending_deferred_ids(state) -> set[str]:
    """Event ids whose LATEST HMAC-verified, CONSUMED batch disposition is 'deferred'
    (Inv 7 re-dispatch lane). A consumed batch advances the window cursor past its events,
    so the next loop's arrival-time [start,end) on ``ingested_at`` can NEVER re-cover a
    burst/budget-deferred event — it would be a permanent drop. These ids are re-enqueued
    explicitly so a deferral is always eventually picked up. Once an event later reaches a
    terminal disposition (escalated/scored/…) in a higher-seq consumed batch it drops out
    of this set, so re-dispatch is not an infinite loop."""
    with state.session_factory() as s:
        entries = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_TRIAGE_BATCH)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    latest: dict[str, str] = {}
    for e in entries:
        payload = e.payload or {}
        if not payload.get("consumed"):
            continue                      # unconsumed windows are re-covered by the cursor
        if not state.audit.verify_entry(e):
            continue
        for eid, label in (payload.get("dispositions") or {}).items():
            latest[eid] = label
    return {eid for eid, label in latest.items() if label == "deferred"}


def _events_by_ids(state, ids) -> list[MarketEvent]:
    if not ids:
        return []
    with state.session_factory() as s:
        rows = s.execute(
            select(MarketEvent).where(MarketEvent.id.in_(list(ids)))
        ).scalars().all()
        for r in rows:
            s.expunge(r)
    return list(rows)


def _calendar_events(state) -> list[MarketEvent]:
    with state.session_factory() as s:
        rows = s.execute(
            select(MarketEvent).where(MarketEvent.event_type.like("calendar:%"))
        ).scalars().all()
        for r in rows:
            s.expunge(r)
    return list(rows)


def _audit_batch(state, *, batch_id, window_start, window_end, scored, dispositions,
                 degraded, consumed) -> None:
    deferred = sorted(eid for eid, label in dispositions.items() if label == "deferred")
    state.audit.append(
        EVENT_TRIAGE_BATCH, actor="triage_loop", refs={"batch_id": batch_id},
        payload={
            "batch_id": batch_id, "window_start": window_start, "window_end": window_end,
            "scored": scored, "deferred": deferred, "degraded": degraded,
            "consumed": consumed, "dispositions": dispositions,
        },
    )


def _defer(state, dispositions: dict, event, reason: str) -> None:
    """Record an escalation deferral: disposition + EVENT_ESCALATION_DEFERRED (Inv 7 —
    recorded, never dropped)."""
    dispositions[event.id] = "deferred"
    state.audit.append(
        EVENT_ESCALATION_DEFERRED, actor="triage_loop", refs={"event_id": event.id},
        payload={"event_id": event.id, "symbol": event.symbol, "reason": reason},
    )


def triage_loop(state, clock=None, *, transport=None, deliver=None, parquet_root=None,
                holdings_symbols: frozenset[str] = frozenset(),
                watchlist: frozenset[str] = frozenset(),
                quote_move_fn=None, propose_fn=None) -> dict:
    """One intraday tick. Returns a JSON-native-plus-SystemEvents summary dict."""
    clock = clock if clock is not None else state.clock
    now = clock.now()
    engine = BreakerEngine(state)

    start = _last_consumed_window_end(state) or (now - _FALLBACK_WINDOW)
    if start > now:
        start = now
    window_start, window_end = start.isoformat(), now.isoformat()
    batch_id = new_id("tb")

    # inclusive end (see morning_brew): an event ingested exactly at `now` stays in-window.
    events = events_in_window(state, start, now + timedelta(microseconds=1))
    # Inv 7 re-dispatch lane: pull back events a prior CONSUMED batch deferred (burst/
    # budget) whose arrival time now sits behind the window cursor — deferral is never a
    # permanent drop; the next loop must be able to pick them up.
    present = {ev.id for ev in events}
    extra = _events_by_ids(state, _pending_deferred_ids(state) - present)
    if extra:
        events = sorted(events + extra, key=lambda ev: (ev.ingested_at, ev.id))
    if not events:
        return {"status": "no_events", "window_start": window_start,
                "window_end": window_end}

    dispositions: dict[str, str] = {}
    system_events: list = []
    undelivered: list[str] = []

    # ---- L8 guard: a watch-only vendor means NO agent dispatch; defer everything --------
    if engine.is_watch_only():
        for ev in events:
            dispositions[ev.id] = "deferred"
        _audit_batch(state, batch_id=batch_id, window_start=window_start,
                     window_end=window_end, scored=[], dispositions=dispositions,
                     degraded=True, consumed=False)
        return {"status": "degraded_watch_only", "window_start": window_start,
                "window_end": window_end, "deferred": sorted(dispositions),
                "system_events": system_events}

    # ---- deterministic pre-filters BEFORE any model call ---------------------------------
    calendars = _calendar_events(state)
    # SEC-11 (W10): reconcile every mutable event_type used for corroboration (halt/filing AND
    # calendar:%) against its signed EVENT_INGEST_BATCH binding, so a keyless writer cannot
    # manufacture deterministic corroboration (and thus deep-budget spend) with a forged row.
    verify_event_type = make_event_type_verifier(state)
    survivors: list[MarketEvent] = []
    for ev in events:
        keep = ev.symbol in holdings_symbols or ev.symbol in watchlist \
            or ev.event_type in _HARD_EVENT_TYPES
        if not keep:
            # calendar proximity via deterministic_urgency's calendar check (no quote hook
            # here — the pre-filter never spends quote calls on noise).
            keep, _ = deterministic_urgency(ev, calendars, now=now, quote_move_fn=None,
                                            verify_event_type=verify_event_type)
        if keep:
            survivors.append(ev)
        else:
            dispositions[ev.id] = "prefiltered_ignore"   # recorded, not dropped

    if not survivors:
        _audit_batch(state, batch_id=batch_id, window_start=window_start,
                     window_end=window_end, scored=[], dispositions=dispositions,
                     degraded=False, consumed=True)
        return {"status": "ok", "window_start": window_start, "window_end": window_end,
                "escalated": [], "system_events": system_events}

    packet = build_packet(survivors)
    jobs_total, jobs_failed_schema = 0, 0

    # ---- ONE batched triage call over the survivors --------------------------------------
    tmpl = load_prompt("triage_batch")
    prompt = tmpl.render(batch_id=batch_id, window_start=window_start,
                         window_end=window_end, packet_block=packet.prompt_block())
    t_result, _t_job = call_agent(
        state, alias="triage", prompt_ref=tmpl.ref, prompt_hash=tmpl.sha256, prompt=prompt,
        schema_name="TriageResult",
        input_refs={"raw_refs": sorted(packet.dispatched_refs)}, transport=transport,
    )
    jobs_total += 1
    if t_result.status in (LIMIT_HIT, TIMEOUT):
        for ev in survivors:
            dispositions[ev.id] = "deferred"
        _audit_batch(state, batch_id=batch_id, window_start=window_start,
                     window_end=window_end, scored=[], dispositions=dispositions,
                     degraded=True, consumed=False)
        return {"status": "degraded_watch_only", "window_start": window_start,
                "window_end": window_end,
                "deferred": sorted(k for k, v in dispositions.items() if v == "deferred"),
                "system_events": system_events}
    if t_result.status != OK:
        jobs_failed_schema += 1
        for ev in survivors:
            dispositions[ev.id] = "deferred"
        _audit_batch(state, batch_id=batch_id, window_start=window_start,
                     window_end=window_end, scored=[], dispositions=dispositions,
                     degraded=False, consumed=False)
        return {"status": "quarantined", "window_start": window_start,
                "window_end": window_end,
                "deferred": sorted(k for k, v in dispositions.items() if v == "deferred"),
                "system_events": system_events}

    by_ref = {ev.raw_ref: ev for ev in survivors}
    scored_entries: list[dict] = []
    score_by_event: dict[str, tuple[str, int]] = {}
    for item in t_result.validated.get("scored", []):
        ev = by_ref.get(item.get("article_id"))
        if ev is None:
            continue
        scored_entries.append({
            "event_id": ev.id, "article_id": item.get("article_id"),
            "classification": item.get("classification"), "score": item.get("score"),
        })
        score_by_event[ev.id] = (str(item.get("classification")), int(item.get("score") or 0))
    for ev in survivors:
        if ev.id not in score_by_event:
            _defer(state, dispositions, ev, "unscored_by_model")
        else:
            dispositions[ev.id] = "scored"       # provisional; escalation may upgrade it

    # ---- escalation candidates: score >= AUDITED triage threshold (SEC-15) ---------------
    threshold = int(current_limits(state)["triage_threshold"])
    raw_cands = [ev for ev in survivors
                 if ev.id in score_by_event and score_by_event[ev.id][1] >= threshold]

    # ---- deterministic corroboration is computed BEFORE the burst trim so a corroborated /
    # holdings candidate is never trimmed as "burst excess" behind an uncorroborated,
    # higher-model-score flood item (SEC-21: rank by signal strength, never model score).
    corroboration: dict[str, tuple[bool, str]] = {
        ev.id: deterministic_urgency(ev, calendars, now=now, quote_move_fn=quote_move_fn,
                                     verify_event_type=verify_event_type)
        for ev in raw_cands
    }

    # ---- SEC-01/SEC-21 same-ticker burst detector (>= 4 candidates, one tick) ------------
    by_symbol: dict[str, list[MarketEvent]] = {}
    for ev in raw_cands:
        by_symbol.setdefault(ev.symbol, []).append(ev)
    kept_cands: list[MarketEvent] = []
    for sym, group in by_symbol.items():
        if len(group) >= _BURST_THRESHOLD:
            evn = engine.check_order_path(
                context={"same_ticker_burst": {"symbol": sym, "count": len(group)}},
                predicate=injection_predicate,
                reason=f"same-ticker escalation burst: {sym} x{len(group)}",
            )
            if evn is not None:
                system_events.append(evn)
            # mirror budget.rank_candidates: corroborated first, then holdings-match, then
            # model score desc, then ts asc — so trimming sheds the WEAKEST signals, never a
            # corroborated / held candidate.
            group = sorted(group, key=lambda e: (
                0 if corroboration[e.id][0] else 1,
                0 if e.symbol in holdings_symbols else 1,
                -score_by_event[e.id][1],
                e.ts.isoformat() if e.ts else "",
                e.id,
            ))
            for excess in group[_BURST_THRESHOLD - 1:]:
                _defer(state, dispositions, excess, f"same_ticker_burst:{sym}")
            group = group[:_BURST_THRESHOLD - 1]
        kept_cands.extend(group)

    # ---- SEC-21 (round-2): EVERY uncorroborated candidate needs deterministic
    # corroboration, not just a self-labeled 'urgent'. Escalation candidates are chosen by
    # score >= threshold for ALL classifications, and 'material' is a valid TriageResult
    # class — so an uncorroborated 'material' with score >= threshold would fall through and
    # consume deep budget with corroborated=False, and a low-and-slow uncorroborated
    # 'material' flood drains the daily budget across ticks and starves a later corroborated
    # urgent event. Gate on `not corroborated` regardless of the model's label: demote ANY
    # uncorroborated candidate (self-labeled 'urgent' keeps its distinct demoted label so the
    # existing 'demoted' reporting/tests stay stable). DeepBudget.try_consume enforces the
    # same rule as a defense-in-depth backstop.
    cands: list[dict] = []
    for ev in kept_cands:
        classification, score = score_by_event[ev.id]
        corroborated, why = corroboration[ev.id]
        if not corroborated:
            dispositions[ev.id] = (
                "uncorroborated_urgent" if classification == "urgent"
                else "uncorroborated")               # demoted to watch; no deep budget
            continue
        cands.append({
            "event_id": ev.id, "symbol": ev.symbol, "score": score, "ts": ev.ts,
            "corroborated": corroborated,
            "holdings_match": ev.symbol in holdings_symbols,
            "reason": why if corroborated else f"triage:{classification}",
            "classification": classification, "_event": ev,
        })

    # ---- ranked, budget-gated escalation (never arrival order — SEC-21) ------------------
    budget = DeepBudget(state)
    day = now.date()
    degraded = False
    escalated: list[str] = []
    fn = propose_fn if propose_fn is not None else draft_proposal
    for cand in rank_candidates(cands):
        ev = cand["_event"]
        if degraded:
            _defer(state, dispositions, ev, "degraded_watch_only")
            continue
        try:
            consumed = budget.try_consume(
                day=day, event_id=ev.id, symbol=cand["symbol"],
                reason=cand["reason"], corroborated=cand["corroborated"])
        except BudgetIntegrityError:
            # tampered budget ledger -> treat as NO budget (fail closed), never spent=0
            _defer(state, dispositions, ev, "deep_budget_integrity")
            continue
        if not consumed:
            _defer(state, dispositions, ev, "deep_budget_exhausted")
            continue
        # Inverse Cramer: a deterministically-detected Cramer call always drafts a proposal
        # (any symbol, not just holdings/watchlist), with the side FIXED by the ingest-time
        # detector — never left to the deep model's reading of the (untrusted) article.
        is_cramer_call = ev.event_type.startswith("cramer_call:")
        propose_kwargs = {}
        if is_cramer_call:
            propose_kwargs["forced_side"] = inverse_side(ev.event_type.split(":", 1)[1])
            propose_kwargs["prompt_name"] = "inverse_cramer_proposal"
        if cand["holdings_match"] or cand["symbol"] in watchlist or is_cramer_call:
            outcome = fn(state, clock, event=ev, packet=packet, transport=transport,
                         parquet_root=parquet_root, holdings_symbols=holdings_symbols,
                         watchlist=watchlist, **propose_kwargs)
            jobs_total += 1
            if outcome.status == "degraded":
                degraded = True
                _defer(state, dispositions, ev, "degraded_watch_only")
                continue
            if outcome.status == "quarantined":
                jobs_failed_schema += 1
            dispositions[ev.id] = "escalated"
            escalated.append(ev.id)
            if deliver is not None and outcome.embed is not None \
                    and outcome.status in ("drafted", "flagged"):
                if not _safe_deliver(deliver, "#proposals", outcome.embed):
                    undelivered.append(ev.id)
        else:
            # escalation without a holdings/watchlist match: alert, not a proposal draft
            dispositions[ev.id] = "escalated"
            escalated.append(ev.id)
            alert = {"kind": "alert", "event_id": ev.id, "symbol": cand["symbol"],
                     "classification": cand["classification"], "score": cand["score"],
                     "reason": cand["reason"], "headline": ev.headline}
            if deliver is not None and not _safe_deliver(deliver, "#alerts", alert):
                undelivered.append(ev.id)

    # ---- L6 sampling: schema failures from THIS run's agent-job statuses -----------------
    signals = BreakerSignals(schema_failures=jobs_failed_schema,
                             schema_validations=jobs_total)
    system_events.extend(engine.evaluate_and_trip(signals))

    _audit_batch(state, batch_id=batch_id, window_start=window_start,
                 window_end=window_end, scored=scored_entries, dispositions=dispositions,
                 degraded=degraded, consumed=not degraded)
    return {
        "status": "degraded_watch_only" if degraded else "ok",
        "window_start": window_start, "window_end": window_end,
        "escalated": escalated,
        "deferred": sorted(k for k, v in dispositions.items() if v == "deferred"),
        "demoted": sorted(k for k, v in dispositions.items()
                          if v.startswith("uncorroborated")),
        "prefiltered": sorted(k for k, v in dispositions.items()
                              if v == "prefiltered_ignore"),
        "undelivered": undelivered,
        "system_events": system_events,
    }


def _safe_deliver(deliver, channel: str, payload: dict) -> bool:
    try:
        return bool(deliver(channel, payload))
    except Exception:
        return False   # delivery failure is recorded, never raised (Inv 7)
