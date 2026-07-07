"""WP1a — AgentJob round-trip test (in-memory sqlite)."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.agent_job import AgentJob


def test_agent_job_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(
            AgentJob(
                id="job_1",
                runtime="claude",
                model="deep",
                model_pinned="claude-opus-4-8",
                prompt_template="proposals.v3",
                prompt_hash="deadbeef",
                input_refs={"events": ["me_1", "me_2"]},
                schema_id="TradeProposal",
                status="queued",
                usage_estimate=1.25,
                session_id="sess_abc",
                started_at=datetime.now(timezone.utc),
                completed_at=None,
                created_at=datetime.now(timezone.utc),
            )
        )
        s.commit()
        got = s.get(AgentJob, "job_1")
        assert got.runtime == "claude"
        assert got.model_pinned == "claude-opus-4-8"
        assert got.session_id == "sess_abc"
        assert got.input_refs == {"events": ["me_1", "me_2"]}
        assert got.completed_at is None
