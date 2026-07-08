"""G8 — SEC-18 fresh-context interrogation tests (Owner B).

Enforces the WP8 'interrogation.py' contract of docs/build-scaffold-ultracode.md WP8 and
SEC-18/SEC-09 of docs/security-hardening-backlog.md: the interrogation runs a BRAND-NEW
agent context rebuilt only from the verified raw store + the validated stored output —
the transport argv NEVER contains ``--resume``; the answer is forced to pattern="fresh" /
session_id=None, carries the risk_decision_ref, is scrubbed before return, and is audited
as EVENT_INTERROGATION. Hermetic: FrozenClock, tmp SQLite, FakeTransport, tmp parquet root.
"""
from __future__ import annotations

from datetime import timedelta

import jsonschema
import pytest
from sqlalchemy import select

from ingest.normalize import to_market_event
from schemas.models.agent_job import AgentJob
from schemas.models.audit_entry import AuditEntry
from schemas.models.proposal_record import ProposalRecord
from tests.workflows.conftest import NOW, FakeTransport, envelope
from workflows.constants import EVENT_INTERROGATION
from workflows.events import persist_events
from workflows.interrogation import fresh_interrogate
from workflows.schema_loader import load_schema


def _persist_event(st, clock, tmp_path):
    ev = to_market_event(source="finnhub", article_id="i-1", symbols=["AAPL"],
                         headline="AAPL beats expectations", summary="detail",
                         ts=(NOW - timedelta(minutes=10)).isoformat())
    return persist_events(st, [ev], parquet_root=tmp_path / "raw", clock=clock).stored[0]


def _seed_proposal(st, ref, *, rd_id="rd-77"):
    payload = {"proposal_id": "prop-i", "symbol": "AAPL", "side": "buy",
               "asset_type": "equity", "order_type": "limit", "quantity": 5,
               "time_horizon": "short", "thesis": "AAPL momentum", "invalidation": "close low",
               "sizing_basis": "fixed", "source_refs": [ref]}
    record = ProposalRecord(id="prop-i", symbol="AAPL", side="buy", status="draft",
                            payload=payload, job_id=None, risk_decision_id=rd_id,
                            order_id=None, flagged=False, created_at=NOW, updated_at=NOW)
    st.audit.record_state_change(record, "trade_proposal", actor="test")
    return record


def _answer_body(question, answer):
    return {"question": question, "answer": answer, "pattern": "fresh",
            "session_id": None, "generated_at": NOW.isoformat()}


def _interro_payloads(st):
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry)
                            .where(AuditEntry.event_type == EVENT_INTERROGATION)).scalars().all()
    return [dict(e.payload or {}) for e in entries if st.audit.verify_entry(e)]


def test_fresh_interrogation_never_resumes_and_validates(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    _seed_proposal(st, row.raw_ref)
    q = "Was the sizing overridden?"
    transport = FakeTransport([envelope(_answer_body(q, "No; the stored context shows no override."))])
    answer = fresh_interrogate(st, question=q, proposal_id="prop-i", transport=transport,
                               parquet_root=tmp_path / "raw", clock=clock)

    jsonschema.validate(answer, load_schema("InterrogationAnswer"))
    assert answer["pattern"] == "fresh"
    assert answer["session_id"] is None
    assert answer["risk_decision_ref"] == "rd-77"
    assert answer["generated_at"] == clock.now().isoformat()
    assert transport.calls, "a real fresh agent call must run"
    for call in transport.calls:
        assert "--resume" not in call["argv"], "SEC-18: NEVER resume the proposing session"
    # the rebuilt context carries the verified raw slice + the stored output, wrapped
    assert "untrusted-content" in transport.calls[0]["input"]
    assert q in transport.calls[0]["input"]

    payloads = _interro_payloads(st)
    assert payloads and payloads[-1]["pattern"] == "fresh"
    assert payloads[-1]["proposal_id"] == "prop-i"


def test_answer_is_scrubbed_before_return(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    _seed_proposal(st, row.raw_ref)
    transport = FakeTransport([envelope(_answer_body("q?", "Risk was $1,234.56 of premium."))])
    answer = fresh_interrogate(st, question="q?", proposal_id="prop-i", transport=transport,
                               parquet_root=tmp_path / "raw", clock=clock)
    assert "$1,234.56" not in answer["answer"]
    assert "[amount]" in answer["answer"]                       # SEC-09 redaction


def test_blocking_scrub_hit_quarantines_the_answer_text(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    _seed_proposal(st, row.raw_ref)
    leak = "The account 12345678901 holds TSLA 40% of the book."
    transport = FakeTransport([envelope(_answer_body("q?", leak))])
    answer = fresh_interrogate(st, question="q?", proposal_id="prop-i", transport=transport,
                               parquet_root=tmp_path / "raw", clock=clock)
    assert "12345678901" not in answer["answer"]
    assert "quarantined" in answer["answer"], "a blocking hit must quarantine, never post"


def test_job_id_path_uses_stored_input_refs(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    job = AgentJob(id="job-i", runtime="claude", model="triage", model_pinned="pin",
                   prompt_template="triage_batch.v1", prompt_hash="h", schema_id="TriageResult",
                   input_refs={"raw_refs": [row.raw_ref]}, status="done",
                   created_at=NOW)
    st.audit.record_state_change(job, "agent_job", actor="test")
    transport = FakeTransport([envelope(_answer_body("what ran?", "A triage batch."))])
    answer = fresh_interrogate(st, question="what ran?", job_id="job-i", transport=transport,
                               parquet_root=tmp_path / "raw", clock=clock)
    assert answer["pattern"] == "fresh"
    assert _interro_payloads(st)[-1]["job_id"] == "job-i"


def test_unknown_target_raises(st, clock):
    with pytest.raises(ValueError):
        fresh_interrogate(st, question="q?", proposal_id="nope", clock=clock)
    with pytest.raises(ValueError):
        fresh_interrogate(st, question="q?", clock=clock)
