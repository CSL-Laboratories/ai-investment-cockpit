"""G8 — intraday triage-loop tests (Owner B).

Enforces the WP8 'triage_loop.py' contract of docs/build-scaffold-ultracode.md WP8 and
SEC-01/SEC-21/SEC-23/Inv 7 of docs/security-hardening-backlog.md: deterministic
pre-filters run BEFORE any model call; ONE batched triage call; self-labeled 'urgent'
without deterministic corroboration is demoted (never budgeted); the chain-derived deep
budget caps escalations and over-budget candidates land in EVENT_ESCALATION_DEFERRED; a
same-ticker burst (>=4) fires the injection breaker and defers the excess; mid-loop
limit-hit -> watch-only with the mode unchanged, remaining candidates ALL deferred, and an
UNCONSUMED window that the next fresh loop re-covers; every window event id appears in
EXACTLY one disposition of the audited EVENT_TRIAGE_BATCH payload. Hermetic: FrozenClock,
tmp SQLite, FakeTransport, tmp parquet root.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from agent_runner.transport import ProcResult
from config.settings import Mode
from ingest.normalize import to_market_event
from orchestrator.breakers import BreakerEngine
from orchestrator.limits import EVENT_LIMITS_CHANGE
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_BREAKER_TRIP, EVENT_WATCH_ONLY
from schemas.models.audit_entry import AuditEntry
from tests.workflows.conftest import NOW, FakeTransport, envelope
from workflows.constants import (
    EVENT_DEEP_ESCALATION,
    EVENT_ESCALATION_DEFERRED,
    EVENT_TRIAGE_BATCH,
)
from workflows.context_limit import estimate_tokens, repair_reserve_tokens
from workflows.events import persist_events
from workflows.packets import build_packet
from workflows.prompts import load_prompt
from workflows.proposals import ProposalOutcome, draft_proposal
from workflows.triage_loop import triage_loop

HOLDINGS = frozenset({"AAPL"})
WATCHLIST = frozenset({"ZZZ"})


def _mk(article_id, symbol, headline, minutes_ago=5, event_type="news"):
    return to_market_event(source="finnhub", article_id=article_id, symbols=[symbol],
                           headline=headline, summary="summary",
                           ts=(NOW - timedelta(minutes=minutes_ago)).isoformat(),
                           event_type=event_type)


def _persist(st, clock, tmp_path, events):
    res = persist_events(st, events, parquet_root=tmp_path / "raw", clock=clock)
    return list(res.stored)


def _scored(ref, symbol, classification, score):
    return {"article_id": ref, "symbols": [symbol], "classification": classification,
            "score": score, "rationale": "r"}


def _triage_body(scored):
    return {"batch_id": "tb-x", "window_start": (NOW - timedelta(minutes=30)).isoformat(),
            "window_end": NOW.isoformat(), "scored": scored}


def _payloads(st, event_type):
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry).where(AuditEntry.event_type == event_type)
                            .order_by(AuditEntry.seq)).scalars().all()
    return [dict(e.payload or {}) for e in entries if st.audit.verify_entry(e)]


class Recorder:
    def __init__(self):
        self.posts = []

    def __call__(self, channel, payload):
        self.posts.append((channel, payload))
        return True


def _stub_outcome(status="drafted", embed=None):
    return ProposalOutcome(status=status, proposal={"proposal_id": "p"}, record=None,
                           risk_decision=None, order=None, embed=embed, reasons=())


def test_prefilter_drops_noise_before_any_model_call(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path, [
        _mk("t-a", "AAPL", "AAPL earnings surprise"),
        _mk("t-n", "RNDA", "RNDA bake sale recap"),
        _mk("t-f", "QQQ", "8-K filing: QQQ definitive agreement", event_type="filing"),
    ])
    by = {r.symbol: r for r in rows}
    transport = FakeTransport([envelope(_triage_body([
        _scored(by["AAPL"].raw_ref, "AAPL", "material", 50),
        _scored(by["QQQ"].raw_ref, "QQQ", "urgent", 95),
    ]))])
    deliver = Recorder()
    result = triage_loop(st, clock, transport=transport, deliver=deliver,
                         parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                         watchlist=WATCHLIST)
    assert result["status"] == "ok"
    # noise never reached the model; exactly ONE batched triage call happened
    assert len(transport.calls) == 1
    assert "bake sale" not in transport.calls[0]["input"]
    assert "AAPL earnings" in transport.calls[0]["input"]

    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    d = batch["dispositions"]
    assert d[by["RNDA"].id] == "prefiltered_ignore"     # recorded, not dropped
    assert d[by["AAPL"].id] == "scored"                 # below threshold (50 < 60)
    assert d[by["QQQ"].id] == "escalated"               # filing-corroborated urgent
    assert set(d) == {r.id for r in rows}
    assert batch["consumed"] is True

    # the QQQ escalation had no holdings/watchlist match -> alert, not a proposal draft
    deep = _payloads(st, EVENT_DEEP_ESCALATION)
    assert len(deep) == 1 and deep[0]["symbol"] == "QQQ" and deep[0]["corroborated"] is True
    assert deep[0]["event_id"] == by["QQQ"].id
    channels = [c for c, _ in deliver.posts]
    assert "#alerts" in channels


def test_uncorroborated_urgent_is_demoted_not_budgeted(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path, [
        _mk("z-1", "ZZZ", "URGENT!!! ZZZ moon soon 1"),
        _mk("z-2", "ZZZ", "URGENT!!! ZZZ moon soon 2"),
    ])
    transport = FakeTransport([envelope(_triage_body(
        [_scored(r.raw_ref, "ZZZ", "urgent", 90) for r in rows]))])
    result = triage_loop(st, clock, transport=transport, parquet_root=tmp_path / "raw",
                         holdings_symbols=HOLDINGS, watchlist=WATCHLIST)
    assert result["status"] == "ok"
    assert _payloads(st, EVENT_DEEP_ESCALATION) == [], "self-labeled urgency consumed budget"
    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert all(batch["dispositions"][r.id] == "uncorroborated_urgent" for r in rows)


def test_uncorroborated_material_does_not_consume_or_starve_budget(st, clock, tmp_path):
    """SEC-21 round-2: the corroboration gate must cover EVERY classification, not just the
    single literal 'urgent'. Candidates are chosen by score>=threshold for ALL classes, and
    'material' is a valid TriageResult class, so an uncorroborated 'material' watchlist item
    with score>=threshold used to fall through and consume deep budget with corroborated=
    False — and a low-and-slow uncorroborated 'material' flood then drains the daily budget
    across ticks and starves a LATER corroborated event. With the fix, the uncorroborated
    'material' is demoted (no EVENT_DEEP_ESCALATION), so the budget survives for tick 2's
    corroborated filing. Old code: tick 1 minted a deep escalation for ZZZ and exhausted the
    budget-of-1, so tick 2's corroborated AAPL filing was starved (deferred)."""
    from core.clock import FrozenClock  # advancing clocks; fixture clock is frozen at NOW

    st.audit.append(EVENT_LIMITS_CHANGE, actor="op",
                    payload={"limits": {"deep_model_daily_budget": 1}})

    # ZZZ (watchlist) ingested at NOW so a later tick's arrival window can't re-cover it.
    m_rows = _persist(st, clock, tmp_path,
                      [_mk("m-1", "ZZZ", "ZZZ ordinary business update", event_type="news")])

    # ---- tick 1: an uncorroborated 'material' watchlist item (news, no calendar/quote) ---
    clock1 = FrozenClock(NOW + timedelta(minutes=1))
    res1 = triage_loop(st, clock1, transport=FakeTransport([envelope(_triage_body(
        [_scored(m_rows[0].raw_ref, "ZZZ", "material", 90)]))]),
        parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
        propose_fn=lambda *a, **k: _stub_outcome())
    assert res1["status"] == "ok"
    assert _payloads(st, EVENT_DEEP_ESCALATION) == [], \
        "uncorroborated 'material' must be demoted, never consume deep budget"
    batch1 = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch1["dispositions"][m_rows[0].id] in ("uncorroborated", "deferred")
    assert m_rows[0].id in res1["demoted"] or m_rows[0].id in res1["deferred"]

    # ---- tick 2: a corroborated (filing) holdings item must still escalate on the intact
    # budget-of-1 — proving the uncorroborated flood did not starve it -----------------------
    clock2 = FrozenClock(NOW + timedelta(minutes=2))
    f_rows = _persist(st, clock2, tmp_path,
                      [_mk("f-1", "AAPL", "8-K filing: AAPL agreement", event_type="filing")])
    escalated2 = []
    res2 = triage_loop(st, clock2, transport=FakeTransport([envelope(_triage_body(
        [_scored(f_rows[0].raw_ref, "AAPL", "material", 90)]))]),
        parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
        propose_fn=lambda s, c, **k: (escalated2.append(k["event"].id) or _stub_outcome()))
    assert res2["status"] == "ok"
    assert escalated2 == [f_rows[0].id]
    deep = _payloads(st, EVENT_DEEP_ESCALATION)
    assert [p["event_id"] for p in deep] == [f_rows[0].id], \
        "budget wasn't drained by the uncorroborated tick, so the corroborated filing wins"


def test_same_ticker_burst_trips_injection_breaker_and_defers_excess(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path, [
        _mk(f"z-{i}", "ZZZ", f"URGENT!!! ZZZ rumor {i}") for i in range(1, 6)
    ])
    transport = FakeTransport([envelope(_triage_body(
        [_scored(r.raw_ref, "ZZZ", "urgent", 90) for r in rows]))])
    triage_loop(st, clock, transport=transport, parquet_root=tmp_path / "raw",
                holdings_symbols=HOLDINGS, watchlist=WATCHLIST)

    trips = _payloads(st, EVENT_BREAKER_TRIP)
    assert any(p.get("breaker") == "injection_anomaly" for p in trips)   # SEC-01 seam

    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    labels = [batch["dispositions"][r.id] for r in rows]
    assert labels.count("deferred") == 2, "burst EXCESS candidates go to the deferred lane"
    assert labels.count("uncorroborated_urgent") == 3
    deferred_reasons = [p["reason"] for p in _payloads(st, EVENT_ESCALATION_DEFERRED)]
    assert any(r.startswith("same_ticker_burst") for r in deferred_reasons)


def test_budget_gate_ranks_holdings_first_and_defers_over_budget(st, clock, tmp_path):
    st.audit.append(EVENT_LIMITS_CHANGE, actor="op",
                    payload={"limits": {"deep_model_daily_budget": 1}})
    rows = _persist(st, clock, tmp_path, [
        _mk("f-a", "AAPL", "8-K filing: AAPL agreement", event_type="filing"),
        _mk("f-q", "QQQ", "8-K filing: QQQ agreement", event_type="filing"),
    ])
    by = {r.symbol: r for r in rows}
    calls = []

    def propose_stub(state, clock_, **kw):
        calls.append(kw)
        return _stub_outcome(embed={"proposal_id": "p"})

    transport = FakeTransport([envelope(_triage_body([
        _scored(by["AAPL"].raw_ref, "AAPL", "urgent", 90),
        _scored(by["QQQ"].raw_ref, "QQQ", "urgent", 95),   # higher score but NOT held
    ]))])
    deliver = Recorder()
    result = triage_loop(st, clock, transport=transport, deliver=deliver,
                         parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                         watchlist=frozenset(), propose_fn=propose_stub)
    assert result["status"] == "ok"

    deep = _payloads(st, EVENT_DEEP_ESCALATION)
    assert len(deep) == 1, "the audited budget of 1 caps escalation at exactly 1"
    assert deep[0]["event_id"] == by["AAPL"].id, "holdings-match outranks raw score (SEC-21)"
    assert len(calls) == 1 and calls[0]["event"].id == by["AAPL"].id
    assert ("#proposals", {"proposal_id": "p"}) in deliver.posts

    deferred = _payloads(st, EVENT_ESCALATION_DEFERRED)
    assert any(p["event_id"] == by["QQQ"].id and p["reason"] == "deep_budget_exhausted"
               for p in deferred), "the over-budget candidate must be deferred, never dropped"


def test_mid_loop_degradation_defers_everything_and_window_is_recovered(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path, [
        _mk("f-a", "AAPL", "8-K filing: AAPL agreement", event_type="filing"),
        _mk("f-m", "MSFT", "8-K filing: MSFT agreement", event_type="filing"),
    ])
    by = {r.symbol: r for r in rows}
    holdings = frozenset({"AAPL", "MSFT"})
    scored = [_scored(by["AAPL"].raw_ref, "AAPL", "urgent", 95),
              _scored(by["MSFT"].raw_ref, "MSFT", "urgent", 90)]

    def degrading_propose(state, clock_, **kw):
        BreakerEngine(state).enter_watch_only("claude", "deep limit_hit (stub)")
        return _stub_outcome(status="degraded")

    mode_before = ModeLadder(st).current()
    res1 = triage_loop(st, clock, transport=FakeTransport([envelope(_triage_body(scored))]),
                       parquet_root=tmp_path / "raw", holdings_symbols=holdings,
                       propose_fn=degrading_propose)
    assert res1["status"] == "degraded_watch_only"
    assert BreakerEngine(st).is_watch_only("claude") is True
    assert ModeLadder(st).current() == mode_before == Mode.read_only   # L8 != mode change
    assert len([p for p in _payloads(st, EVENT_WATCH_ONLY) if not p.get("recovered")]) == 1

    batch1 = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch1["consumed"] is False
    assert set(batch1["dispositions"]) == {by["AAPL"].id, by["MSFT"].id}, "nothing dropped"
    assert all(v == "deferred" for v in batch1["dispositions"].values())

    # recovery + a fresh loop re-covers the UNCONSUMED window (deferred != lost, Inv 7)
    BreakerEngine(st).recover_from_watch_only("claude", mode_before)
    drafted = []

    def ok_propose(state, clock_, **kw):
        drafted.append(kw["event"].id)
        return _stub_outcome()

    res2 = triage_loop(st, clock, transport=FakeTransport([envelope(_triage_body(scored))]),
                       parquet_root=tmp_path / "raw", holdings_symbols=holdings,
                       propose_fn=ok_propose)
    assert res2["status"] == "ok"
    assert set(drafted) == {by["AAPL"].id, by["MSFT"].id}, "deferred events were picked up"


def test_triage_limit_hit_defers_survivors(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path,
                    [_mk("f-a", "AAPL", "8-K filing: AAPL agreement", event_type="filing")])
    transport = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
    result = triage_loop(st, clock, transport=transport, parquet_root=tmp_path / "raw",
                         holdings_symbols=HOLDINGS)
    assert result["status"] == "degraded_watch_only"
    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch["dispositions"][rows[0].id] == "deferred"
    assert batch["degraded"] is True and batch["consumed"] is False


def test_watch_only_at_entry_dispatches_no_agent(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path,
                    [_mk("f-a", "AAPL", "8-K filing: AAPL agreement", event_type="filing")])
    st.usage.trip("claude", "exhausted")
    transport = FakeTransport([])       # any call would raise IndexError
    result = triage_loop(st, clock, transport=transport, parquet_root=tmp_path / "raw",
                         holdings_symbols=HOLDINGS)
    assert result["status"] == "degraded_watch_only"
    assert transport.calls == []
    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch["dispositions"][rows[0].id] == "deferred"


def test_corroborated_holding_not_burst_deferred_behind_flood(st, clock, tmp_path):
    """SEC-21 burst-trim ordering: a corroborated (filing) holdings candidate with a LOWER
    model score must NOT be deferred as burst-excess behind higher-score, uncorroborated
    flood headlines. Old code sorted the burst by model score only -> the corroborated
    holding fell out of the kept set and never escalated."""
    rows = _persist(st, clock, tmp_path, [
        _mk("f-corr", "AAPL", "8-K filing: AAPL definitive agreement", event_type="filing"),
        *[_mk(f"n-{i}", "AAPL", f"URGENT!!! AAPL rumor {i}") for i in range(1, 5)],
    ])
    filing = rows[0]
    transport = FakeTransport([envelope(_triage_body(
        [_scored(filing.raw_ref, "AAPL", "material", 65)]
        + [_scored(r.raw_ref, "AAPL", "urgent", 95) for r in rows[1:]]))])
    result = triage_loop(st, clock, transport=transport, parquet_root=tmp_path / "raw",
                         holdings_symbols=HOLDINGS, watchlist=frozenset(),
                         propose_fn=lambda *a, **k: _stub_outcome())
    assert result["status"] == "ok"

    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch["dispositions"][filing.id] == "escalated", \
        "corroborated holding must survive the burst trim, not be deferred behind the flood"
    deep = _payloads(st, EVENT_DEEP_ESCALATION)
    assert any(p["event_id"] == filing.id for p in deep)
    assert filing.id not in {p["event_id"] for p in _payloads(st, EVENT_ESCALATION_DEFERRED)}


def test_burst_deferred_events_are_repicked_by_next_loop(st, clock, tmp_path):
    """Inv 7: burst-deferred events sit in a CONSUMED batch, so the window cursor advances
    past them; a LATER loop's arrival-time window (keyed on ingested_at) then excludes them
    permanently. They must be re-dispatched (never a permanent drop). Old code lost them ->
    the second loop saw no_events. The clock advances between loops so the deferred events'
    arrival time falls strictly BEHIND the second loop's window cursor (the real bug — a
    frozen clock would mask it by landing them on the inclusive window boundary)."""
    from core.clock import FrozenClock  # advancing clocks; fixture clock is frozen at NOW

    rows = _persist(st, clock, tmp_path, [
        _mk(f"b-{i}", "AAPL", f"8-K filing: AAPL item {i}", event_type="filing")
        for i in range(1, 6)
    ])  # ingested_at == NOW
    body = _triage_body([_scored(r.raw_ref, "AAPL", "material", 90) for r in rows])

    clock1 = FrozenClock(NOW + timedelta(minutes=1))
    escalated1 = []
    res1 = triage_loop(st, clock1, transport=FakeTransport([envelope(body)]),
                       parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                       propose_fn=lambda s, c, **k: (escalated1.append(k["event"].id)
                                                     or _stub_outcome()))
    assert res1["status"] == "ok"
    burst_deferred = {p["event_id"] for p in _payloads(st, EVENT_ESCALATION_DEFERRED)
                      if p["reason"].startswith("same_ticker_burst")}
    assert len(burst_deferred) == 2, "5 candidates, 3 kept -> 2 burst-deferred"

    # a later loop whose arrival window [prev_window_end, now) NO LONGER covers the deferred
    # events (ingested_at == NOW < prev window_end == NOW+1min).
    clock2 = FrozenClock(NOW + timedelta(minutes=2))
    drafted2 = []
    res2 = triage_loop(st, clock2, transport=FakeTransport([envelope(body)]),
                       parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                       propose_fn=lambda s, c, **k: (drafted2.append(k["event"].id)
                                                     or _stub_outcome()))
    assert res2["status"] == "ok"
    assert set(drafted2) == burst_deferred, "burst-deferred events must be re-dispatched"


def test_no_events_is_a_clean_noop(st, clock):
    assert triage_loop(st, clock)["status"] == "no_events"


def test_degraded_batch_does_not_re_escalate_already_escalated_events(st, clock, tmp_path):
    """BUG-055: tick T escalates the first candidate (proposal drafted, budget spent), then
    the second candidate's deep call degrades — the batch lands consumed=False so the
    window is re-covered next tick. The re-covered window used to re-score the FIRST event
    too, spend a second budget unit, and mint a second proposal/order for it — both
    reaching the human. Escalation must be terminal across batches."""
    rows = _persist(st, clock, tmp_path, [
        _mk("f-a", "AAPL", "8-K filing: AAPL agreement", event_type="filing"),
        _mk("f-m", "MSFT", "8-K filing: MSFT agreement", event_type="filing"),
    ])
    by = {r.symbol: r for r in rows}
    holdings = frozenset({"AAPL", "MSFT"})
    scored = [_scored(by["AAPL"].raw_ref, "AAPL", "urgent", 95),
              _scored(by["MSFT"].raw_ref, "MSFT", "urgent", 90)]

    drafted1: list[str] = []

    def draft_then_degrade(state, clock_, **kw):
        drafted1.append(kw["event"].id)
        if len(drafted1) == 1:
            return _stub_outcome()                          # first candidate: drafted
        BreakerEngine(state).enter_watch_only("claude", "deep limit_hit (stub)")
        return _stub_outcome(status="degraded")             # second: degrades mid-loop

    res1 = triage_loop(st, clock, transport=FakeTransport([envelope(_triage_body(scored))]),
                       parquet_root=tmp_path / "raw", holdings_symbols=holdings,
                       propose_fn=draft_then_degrade)
    assert res1["status"] == "degraded_watch_only"
    first = drafted1[0]
    batch1 = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch1["consumed"] is False
    assert batch1["dispositions"][first] == "escalated"

    BreakerEngine(st).recover_from_watch_only("claude", Mode.read_only)
    drafted2: list[str] = []

    def ok_propose(state, clock_, **kw):
        drafted2.append(kw["event"].id)
        return _stub_outcome()

    res2 = triage_loop(st, clock, transport=FakeTransport([envelope(_triage_body(scored))]),
                       parquet_root=tmp_path / "raw", holdings_symbols=holdings,
                       propose_fn=ok_propose)
    assert res2["status"] == "ok"
    assert first not in drafted2, "an already-escalated event was re-drafted (duplicate order)"
    assert set(drafted2) == {by["AAPL"].id, by["MSFT"].id} - {first}, \
        "the genuinely deferred event must still be picked up"


def test_context_limited_proposal_is_deferred_not_escalated(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path,
                    [_mk("f-a", "AAPL", "8-K filing: AAPL agreement", event_type="filing")])
    ev = rows[0]
    calls = []

    def limited_propose(state, clock_, **kw):
        calls.append(kw["event"].id)
        return _stub_outcome(status="context_limited")

    result = triage_loop(
        st, clock,
        transport=FakeTransport([envelope(_triage_body([
            _scored(ev.raw_ref, "AAPL", "urgent", 90),
        ]))]),
        parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
        propose_fn=limited_propose,
    )
    assert result["status"] == "ok"
    assert calls == [ev.id]
    assert ev.id in result["deferred"]
    assert ev.id not in result["escalated"]
    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch["dispositions"][ev.id] == "deferred"
    assert any(
        p["event_id"] == ev.id and p["reason"] == "context_limit"
        for p in _payloads(st, EVENT_ESCALATION_DEFERRED)
    )


def test_unfit_proposal_defers_without_spending_deep_budget(st, clock, tmp_path):
    """Triage can fit an article the proposal template cannot; do not spend budget.

    Production ``build_services`` injects a wrapper around ``draft_proposal``,
    so preflight must not key off callable identity.
    """
    rows = _persist(st, clock, tmp_path,
                    [_mk("f-a", "AAPL", "8-K filing: AAPL agreement", event_type="filing")])
    ev = rows[0]
    packet = build_packet(rows)
    article = estimate_tokens(packet.items[0].text)
    reserve = repair_reserve_tokens()
    triage_oh = estimate_tokens(load_prompt("triage_batch").render(
        batch_id="tb-x",
        window_start=(NOW - timedelta(minutes=30)).isoformat(),
        window_end=NOW.isoformat(),
        packet_block="",
    ))
    proposal_oh = estimate_tokens(load_prompt("trade_proposal").render(
        symbol="AAPL", packet_block="",
    ))
    cap = triage_oh + reserve + article + 80
    assert cap < proposal_oh + reserve + article
    st.settings.context_token_cap = cap

    def _propose(st_, clock_, **kw):
        return draft_proposal(st_, clock_, **kw)

    transport = FakeTransport([envelope(_triage_body([
        _scored(ev.raw_ref, "AAPL", "urgent", 90),
    ]))])
    result = triage_loop(
        st, clock, transport=transport, parquet_root=tmp_path / "raw",
        holdings_symbols=HOLDINGS, propose_fn=_propose,
    )
    assert result["status"] == "ok"
    assert ev.id in result["deferred"]
    assert ev.id not in result["escalated"]
    assert _payloads(st, EVENT_DEEP_ESCALATION) == []
    assert len(transport.calls) == 1
    assert any(
        p["event_id"] == ev.id and p["reason"] == "context_limit"
        for p in _payloads(st, EVENT_ESCALATION_DEFERRED)
    )
