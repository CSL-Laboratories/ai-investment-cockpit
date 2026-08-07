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
