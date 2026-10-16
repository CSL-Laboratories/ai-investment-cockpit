"""G8 — morning brew pipeline tests (Owner B).

Enforces the WP8 'morning_brew.py' contract of docs/build-scaffold-ultracode.md WP8 and
SEC-09/SEC-14/SEC-23/Inv 3/Inv 7 of docs/security-hardening-backlog.md: one triage call +
one deep call, BriefRecord persisted via record_state_change, every window event id in
EXACTLY one disposition of the audited EVENT_TRIAGE_BATCH payload, prose scrubbed before
store/post, foreign source_refs dropped (degraded flag), limit-hit -> watch-only with the
mode UNCHANGED and no event lost, still-malformed deep output -> quarantined AgentJob and
no BriefRecord. Hermetic: FrozenClock, tmp SQLite, FakeTransport, tmp parquet root.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from agent_runner.transport import ProcResult
from config.settings import Mode
from ingest.normalize import to_market_event
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_WATCH_ONLY
from schemas.models.agent_job import AgentJob
from schemas.models.audit_entry import AuditEntry
from schemas.models.brief_record import BriefRecord
from tests.workflows.conftest import NOW, FakeTransport, envelope
from workflows.constants import EVENT_BRIEF, EVENT_TRIAGE_BATCH
from workflows.events import persist_events
from workflows.morning_brew import morning_brew


def _mk(article_id, symbol, headline, minutes_ago=30, event_type="news"):
    return to_market_event(source="finnhub", article_id=article_id, symbols=[symbol],
                           headline=headline, summary="summary text",
                           ts=(NOW - timedelta(minutes=minutes_ago)).isoformat(),
                           event_type=event_type)


def _persist(st, clock, tmp_path):
    events = [_mk("mb-1", "AAPL", "AAPL beats expectations on strong demand"),
              _mk("mb-2", "RNDZ", "RNDZ community newsletter published")]
    res = persist_events(st, events, parquet_root=tmp_path / "raw", clock=clock)
    assert len(res.stored) == 2
    return {r.symbol: r for r in res.stored}


def _scored(rows):
    return [
        {"article_id": rows["AAPL"].raw_ref, "symbols": ["AAPL"],
         "classification": "material", "score": 72, "rationale": "r"},
        {"article_id": rows["RNDZ"].raw_ref, "symbols": ["RNDZ"],
         "classification": "ignore", "score": 4, "rationale": "r"},
    ]


def _triage_body(scored):
    return {"batch_id": "tb-1", "window_start": (NOW - timedelta(hours=18)).isoformat(),
            "window_end": NOW.isoformat(), "scored": scored}


def _brief_body(source_refs, watch_items=None):
    return {"date": NOW.date().isoformat(), "market_regime": "risk-on",
            "portfolio_changes": [], "watch_items": watch_items or ["premium worth $500 traded"],
            "thesis_updates": ["AAPL demand inflection holds"], "risk_flags": [],
            "proposed_actions": [], "source_refs": list(source_refs)}


def _verified(st, event_type):
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry).where(AuditEntry.event_type == event_type)
                            .order_by(AuditEntry.seq)).scalars().all()
    return [e for e in entries if st.audit.verify_entry(e)]


def _rows(st, model):
    with st.session_factory() as s:
        return s.execute(select(model)).scalars().all()


class Recorder:
    def __init__(self, ok=True):
        self.posts, self._ok = [], ok

    def __call__(self, channel, payload):
        self.posts.append((channel, payload))
        return self._ok


def test_no_events_short_circuits(st, clock):
    assert morning_brew(st, clock)["status"] == "no_events"


def test_happy_path_brief_persisted_scrubbed_delivered(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path)
    transport = FakeTransport([envelope(_triage_body(_scored(rows))),
                               envelope(_brief_body([rows["AAPL"].raw_ref]))])
    deliver = Recorder()
    result = morning_brew(st, clock, transport=transport, deliver=deliver,
                          parquet_root=tmp_path / "raw", holdings_symbols=frozenset({"AAPL"}))
    assert result["status"] == "ok" and result["delivered"] is True

    briefs = _rows(st, BriefRecord)
    assert len(briefs) == 1
    payload = briefs[0].payload
    assert payload["source_refs"] == [rows["AAPL"].raw_ref]
    assert briefs[0].degraded is False
    # SEC-09: the dollar amount was redacted before store AND before delivery
    assert "[amount]" in payload["watch_items"][0] and "$500" not in str(payload)
    assert "$500" not in str(deliver.posts)
    assert _verified(st, EVENT_BRIEF), "BriefRecord must be covered by EVENT_BRIEF"

    # Inv 7: every window event id in EXACTLY one disposition
    batch = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload
    dispositions = batch["dispositions"]
    assert set(dispositions) == {rows["AAPL"].id, rows["RNDZ"].id}
    assert batch["consumed"] is True and batch["degraded"] is False
    assert {e["event_id"] for e in batch["scored"]} == set(dispositions)
    # ONE triage call + ONE deep call
    assert len(transport.calls) == 2
    assert st.audit.verify_chain().ok


def test_foreign_source_refs_dropped_and_flagged_degraded(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path)
    foreign = "parquet://2026-01-01/events-evil#0"
    transport = FakeTransport([envelope(_triage_body(_scored(rows))),
                               envelope(_brief_body([rows["AAPL"].raw_ref, foreign]))])
    result = morning_brew(st, clock, transport=transport, parquet_root=tmp_path / "raw")
    assert result["status"] == "ok" and result["degraded"] is True
    brief = _rows(st, BriefRecord)[0]
    assert brief.degraded is True
    assert foreign not in brief.payload["source_refs"], "a never-dispatched ref must be dropped"
    assert brief.payload["source_refs"] == [rows["AAPL"].raw_ref]


def test_triage_limit_hit_degrades_watch_only_without_losing_events(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path)
    mode_before = ModeLadder(st).current()
    transport = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
    result = morning_brew(st, clock, transport=transport, parquet_root=tmp_path / "raw")

    assert result["status"] == "degraded_watch_only"
    assert BreakerEngine(st).is_watch_only("claude") is True
    assert ModeLadder(st).current() == mode_before == Mode.read_only
    assert len(_verified(st, EVENT_WATCH_ONLY)) == 1

    batch = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload
    assert batch["degraded"] is True and batch["consumed"] is False
    assert set(batch["dispositions"]) == {rows["AAPL"].id, rows["RNDZ"].id}
    assert all(v == "deferred" for v in batch["dispositions"].values()), "no event dropped"
    assert _rows(st, BriefRecord) == []


def test_malformed_deep_output_quarantines_and_mints_nothing(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path)
    bad = envelope({"date": NOW.date().isoformat()})    # 7 of 8 keys missing (Inv 3)
    transport = FakeTransport([envelope(_triage_body(_scored(rows))), bad, bad])
    deliver = Recorder()
    result = morning_brew(st, clock, transport=transport, deliver=deliver,
                          parquet_root=tmp_path / "raw")
    assert result["status"] == "quarantined"
    assert _rows(st, BriefRecord) == []
    assert not _verified(st, EVENT_BRIEF)
    assert deliver.posts == [], "nothing may be delivered from quarantined output"
    assert any(j.status == "quarantined" for j in _rows(st, AgentJob))
    batch = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload
    assert set(batch["dispositions"]) == {rows["AAPL"].id, rows["RNDZ"].id}
    assert batch["consumed"] is False


def test_cap_that_drops_every_article_defers_without_a_brief(st, clock, tmp_path):
    _persist(st, clock, tmp_path)
    st.settings.context_token_cap = 1
    transport = FakeTransport([])
    result = morning_brew(
        st, clock, transport=transport, parquet_root=tmp_path / "raw",
        holdings_symbols=frozenset({"AAPL"}),
    )
    assert result["status"] == "deferred_context_limit"
    assert transport.calls == []
    assert _rows(st, BriefRecord) == []
    assert _rows(st, AgentJob) == []
    again = morning_brew(
        st, clock, transport=FakeTransport([]), parquet_root=tmp_path / "raw",
        holdings_symbols=frozenset({"AAPL"}),
    )
    assert again["status"] == "deferred_context_limit"
    assert _rows(st, BriefRecord) == []


def test_summary_packet_tail_stays_deferred_not_scored(st, clock, tmp_path, monkeypatch):
    """The morning_brief template is smaller than triage, so a real cap that
    keeps both articles for triage also keeps them for the summary. Force the
    brief fit to drop the tail and lock the disposition contract."""
    events = [
        _mk("mb-a", "AAPL", "AAPL beats expectations on strong demand"),
        _mk("mb-m", "MSFT", "MSFT wins a large cloud contract"),
    ]
    stored = persist_events(st, events, parquet_root=tmp_path / "raw", clock=clock).stored
    by = {r.symbol: r for r in stored}
    from workflows.context_limit import PacketFit, fit_packet as real_fit
    from workflows.packets import Packet

    seen = {"n": 0}

    def fake_fit(packet, *, reserved_tokens, cap):
        seen["n"] += 1
        if seen["n"] == 2 and len(packet.items) >= 2:
            kept = Packet(items=packet.items[:-1])
            dropped = packet.items[-1:]
            return PacketFit(
                packet=kept, dropped=dropped, estimated_tokens=reserved_tokens, cap=cap)
        return real_fit(packet, reserved_tokens=reserved_tokens, cap=cap)

    monkeypatch.setattr("workflows.morning_brew.fit_packet", fake_fit)
    scored = [
        {"article_id": by["AAPL"].raw_ref, "symbols": ["AAPL"],
         "classification": "material", "score": 80, "rationale": "r"},
        {"article_id": by["MSFT"].raw_ref, "symbols": ["MSFT"],
         "classification": "material", "score": 75, "rationale": "r"},
    ]
    transport = FakeTransport([
        envelope(_triage_body(scored)),
        envelope(_brief_body([by["AAPL"].raw_ref])),
    ])
    result = morning_brew(
        st, clock, transport=transport, parquet_root=tmp_path / "raw",
        holdings_symbols=frozenset({"AAPL", "MSFT"}),
    )
    assert result["status"] == "ok"
    assert len(_rows(st, BriefRecord)) == 1
    batch = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload
    assert batch["dispositions"][by["AAPL"].id] == "scored"
    assert batch["dispositions"][by["MSFT"].id] == "deferred"
    assert by["MSFT"].id in batch["deferred"]
    assert batch["consumed"] is True
    assert len(transport.calls) == 2


def test_second_run_same_day_is_idempotent(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path)
    transport = FakeTransport([envelope(_triage_body(_scored(rows))),
                               envelope(_brief_body([rows["AAPL"].raw_ref]))])
    first = morning_brew(st, clock, transport=transport, parquet_root=tmp_path / "raw")
    assert first["status"] == "ok"
    again = morning_brew(st, clock, transport=FakeTransport([]), parquet_root=tmp_path / "raw")
    assert again["status"] == "already_briefed"
    assert again["brief_id"] == first["brief_id"]
    assert len(_rows(st, BriefRecord)) == 1


def test_failed_delivery_is_reported_never_raised(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path)

    def exploding(channel, payload):
        raise ConnectionError("discord down")

    transport = FakeTransport([envelope(_triage_body(_scored(rows))),
                               envelope(_brief_body([rows["AAPL"].raw_ref]))])
    result = morning_brew(st, clock, transport=transport, deliver=exploding,
                          parquet_root=tmp_path / "raw")
    assert result["status"] == "ok" and result["delivered"] is False
    assert len(_rows(st, BriefRecord)) == 1, "the brief is stored even when delivery fails"


def test_triage_packet_is_bounded_without_dropping_window_events(st, clock, tmp_path):
    events = [
        _mk(f"cap-{index}", "AAPL", f"AAPL headline {index}")
        for index in range(160)
    ]
    persisted = persist_events(st, events, parquet_root=tmp_path / "raw", clock=clock)
    assert len(persisted.stored) == 160

    transport = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
    result = morning_brew(st, clock, transport=transport, parquet_root=tmp_path / "raw")

    assert result["status"] == "degraded_watch_only"
    job = _rows(st, AgentJob)[0]
    assert len(job.input_refs["raw_refs"]) == 150
    batch = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload
    assert len(batch["dispositions"]) == 160
    assert all(label == "deferred" for label in batch["dispositions"].values())


def test_packet_selection_prioritizes_hard_events_and_holdings():
    from datetime import datetime, timezone

    from workflows.morning_brew import select_for_packet

    class Event:
        def __init__(self, event_id, event_type, symbol, minute):
            self.id = event_id
            self.event_type = event_type
            self.symbol = symbol
            self.ingested_at = datetime(2026, 7, 7, 14, minute, tzinfo=timezone.utc)

    events = [
        Event("ordinary-new", "news", "MSFT", 59),
        Event("filing-old", "filing", "ZZZZ", 1),
        Event("holding-old", "news", "AAPL", 2),
        Event("ordinary-old", "news", "MSFT", 3),
    ]

    selected = select_for_packet(events, 3, frozenset({"AAPL"}))
    assert [event.id for event in selected] == [
        "ordinary-new",
        "filing-old",
        "holding-old",
    ]


def test_packet_selection_rejects_a_disabled_cap():
    from workflows.morning_brew import select_for_packet

    with pytest.raises(ValueError, match="cap must be positive"):
        select_for_packet([], 0, frozenset())


# =======================================================================================
# RW-01 bring-up regression — the packet must be RELEVANT, not merely bounded
# =======================================================================================
def _cal(article_id, symbol, minutes_ago=30):
    return to_market_event(source="finnhub", article_id=article_id, symbols=[symbol],
                           headline=f"{symbol} reports Q3", summary="",
                           ts=(NOW - timedelta(minutes=minutes_ago)).isoformat(),
                           event_type="calendar:earnings")


def test_bulk_calendar_is_prefiltered_but_held_and_watched_symbols_survive():
    """THE CAP ALONE DID NOT FIX IT. The second live run capped the packet at 150 and STILL
    timed out — because 150 of 659 whole-market earnings dates is still 150 earnings dates.
    A cap bounds cost; only relevance decides what is worth reading."""
    from workflows.morning_brew import partition_by_relevance

    class Ev:
        def __init__(self, i, event_type, symbol):
            self.id, self.event_type, self.symbol = f"e{i}", event_type, symbol

    events = [Ev(i, "calendar:earnings", f"SY{i}") for i in range(50)]
    events += [Ev(90, "calendar:earnings", "AAPL"),    # HELD -> keep
               Ev(91, "calendar:earnings", "TSLA"),    # WATCHED -> keep
               Ev(92, "news", "NVDA"),                 # news on an unheld symbol -> keep
               Ev(93, "filing", "MSFT"),               # hard event -> keep
               Ev(94, "halt", "GME")]                  # hard event -> keep

    eligible, ignored = partition_by_relevance(
        events, frozenset({"AAPL"}), frozenset({"TSLA"}))

    assert len(ignored) == 50, "bulk calendar rows should not reach the model"
    kept = {e.id for e in eligible}
    assert kept == {"e90", "e91", "e92", "e93", "e94"}
    # The brief exists for AWARENESS: news about something you do not own yet is the point.
    assert "e92" in kept, "the brew must not copy triage_loop's holdings-only filter"


def test_prefiltered_events_are_not_labelled_deferred(st, clock, tmp_path):
    """``deferred`` promises re-coverage next loop; ``prefiltered_ignore`` does not. Conflating
    them would either lie in the audit or re-dispatch the whole market forever."""
    persist_events(st, [_cal(f"cal-{i}", f"SY{i}") for i in range(6)]
                   + [_mk("news-1", "NVDA", "NVDA unveils new accelerator")],
                   parquet_root=tmp_path / "raw", clock=clock)

    transport = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
    result = morning_brew(st, clock, transport=transport, parquet_root=tmp_path / "raw")

    assert result["window_events"] == 7
    assert result["prefiltered"] == 6
    assert result["dispatched"] == 1, "only the news item was worth a model call"
    assert result["deferred_count"] == 1, "prefiltered rows must not inflate the deferred lane"

    payload = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload
    labels = payload["dispositions"]
    assert len(labels) == 7, "an event vanished from the audited dispositions"
    assert sorted(labels.values()).count("prefiltered_ignore") == 6
    assert st.audit.verify_chain().ok


def test_calendar_only_window_asks_no_model_and_is_a_complete_run(st, clock, tmp_path):
    """A window of pure reference data is not a degraded run — there was nothing to read, so
    nothing was asked and nothing is owed re-coverage."""
    persist_events(st, [_cal(f"only-{i}", f"SY{i}") for i in range(4)],
                   parquet_root=tmp_path / "raw", clock=clock)

    # An EMPTY transport raises on any agent call, so this also proves no model was dispatched.
    result = morning_brew(st, clock, transport=FakeTransport([]),
                          parquet_root=tmp_path / "raw")

    assert result["status"] == "no_relevant_events"
    assert result["prefiltered"] == 4 and result["dispatched"] == 0
    assert result["deferred_count"] == 0
    payload = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload
    assert payload["consumed"] is True and payload["degraded"] is False
    assert not _rows(st, BriefRecord)


class _TimeoutSpy(FakeTransport):
    """Records the timeout each agent call was actually bounded by."""

    def __init__(self, results):
        super().__init__(results)
        self.timeouts: list[float] = []

    def run(self, argv, input_text, timeout_s, cwd):
        self.timeouts.append(timeout_s)
        return super().run(argv, input_text, timeout_s, cwd)


def test_triage_timeout_scales_with_the_batch(st, clock, tmp_path):
    """The flat 120s timeout was the mismatch that killed the second live run: triage is
    OUTPUT-bound (one scored entry per item), so the bound has to track the batch size."""
    from workflows.morning_brew import (
        TRIAGE_TIMEOUT_BASE_SECONDS,
        TRIAGE_TIMEOUT_PER_EVENT_SECONDS,
    )

    rows = _persist(st, clock, tmp_path)
    transport = _TimeoutSpy([envelope(_triage_body(_scored(rows))),
                             envelope(_brief_body([rows["AAPL"].raw_ref]))])
    assert morning_brew(st, clock, transport=transport,
                        parquet_root=tmp_path / "raw")["status"] == "ok"

    assert transport.timeouts[0] == (TRIAGE_TIMEOUT_BASE_SECONDS
                                     + TRIAGE_TIMEOUT_PER_EVENT_SECONDS * 2)


def test_the_deep_brief_call_gets_its_own_larger_budget(st, clock, tmp_path):
    """The brief runs on `deep` (opus), far slower than triage's haiku — leaving it on
    call_agent's flat 120s default would have been the next thing to time out."""
    from workflows.morning_brew import (
        BRIEF_TIMEOUT_BASE_SECONDS,
        BRIEF_TIMEOUT_PER_EVENT_SECONDS,
    )

    rows = _persist(st, clock, tmp_path)
    transport = _TimeoutSpy([envelope(_triage_body(_scored(rows))),
                             envelope(_brief_body([rows["AAPL"].raw_ref]))])
    assert morning_brew(st, clock, transport=transport,
                        parquet_root=tmp_path / "raw")["status"] == "ok"

    # one of the two persisted events was scored "material" -> exactly one brief event
    assert transport.timeouts[1] == (BRIEF_TIMEOUT_BASE_SECONDS
                                     + BRIEF_TIMEOUT_PER_EVENT_SECONDS * 1)
    assert transport.timeouts[1] > 120.0, "the deep call must not inherit the flat default"


def test_success_path_keeps_prefiltered_labels(st, clock, tmp_path):
    """BUG-054: the SUCCESS path's final disposition loop overwrote every event back to
    scored/deferred, silently erasing prefiltered_ignore — the audit lied (Inv 6) and the
    whole filtered calendar became a 'deferred' re-dispatch obligation for the intraday
    loop (each calendar row is its own calendar match in deterministic_urgency, so it
    survives corroboration and drains deep budget). The degraded path already had a test;
    THIS is the path that fired every quiet morning."""
    res = persist_events(st, [_cal(f"cal-{i}", f"SY{i}") for i in range(6)]
                         + [_mk("news-1", "NVDA", "NVDA unveils new accelerator")],
                         parquet_root=tmp_path / "raw", clock=clock)
    news = next(r for r in res.stored if r.symbol == "NVDA")
    scored = [{"article_id": news.raw_ref, "symbols": ["NVDA"],
               "classification": "material", "score": 70, "rationale": "r"}]
    transport = FakeTransport([envelope(_triage_body(scored)),
                               envelope(_brief_body([news.raw_ref]))])
    result = morning_brew(st, clock, transport=transport, parquet_root=tmp_path / "raw")
    assert result["status"] == "ok"
    assert result["prefiltered"] == 6
    assert result["deferred_count"] == 0, "prefiltered rows leaked into the deferred lane"

    labels = _verified(st, EVENT_TRIAGE_BATCH)[-1].payload["dispositions"]
    assert sorted(labels.values()).count("prefiltered_ignore") == 6
    assert labels[news.id] == "scored"
    assert st.audit.verify_chain().ok
