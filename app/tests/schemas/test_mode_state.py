from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.mode_state import ModeState


def test_mode_state_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            ModeState(
                id="mode_1",
                mode="review_live",
                armed_at=now,
                armed_until=now,
                armed_by="operator",
                reason="gate passed",
                created_at=now,
            )
        )
        s.commit()
        row = s.get(ModeState, "mode_1")
        assert row.mode == "review_live"
        assert row.armed_by == "operator"


def test_mode_state_nullable_fields():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(
            ModeState(
                id="mode_2",
                mode="read_only",
                created_at=datetime.now(timezone.utc),
            )
        )
        s.commit()
        row = s.get(ModeState, "mode_2")
        assert row.mode == "read_only"
        assert row.armed_at is None
        assert row.armed_until is None
        assert row.armed_by is None
        assert row.reason is None
