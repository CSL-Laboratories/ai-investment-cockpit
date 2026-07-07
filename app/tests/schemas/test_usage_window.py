from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.usage_window import UsageWindow


def test_usage_window_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            UsageWindow(
                id="uw_1",
                vendor="claude",
                window_start=now,
                window_end=now,
                used=42,
                limit_estimate=1000,
                generation=3,
                state="degraded",
                updated_at=now,
                created_at=now,
            )
        )
        s.commit()
        row = s.get(UsageWindow, "uw_1")
        assert row.vendor == "claude"
        assert row.used == 42
        assert row.limit_estimate == 1000
        assert row.generation == 3
        assert row.state == "degraded"
        assert row.window_end is not None
