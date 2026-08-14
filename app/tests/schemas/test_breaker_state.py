from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.breaker_state import BreakerState


def test_breaker_state_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            BreakerState(
                id="brk_1",
                breaker_name="daily_equity_drawdown",
                tripped=True,
                tripped_at=now,
                reason="daily realized loss limit exceeded",
                created_at=now,
            )
        )
        s.commit()
        row = s.get(BreakerState, "brk_1")
        assert row.breaker_name == "daily_equity_drawdown"
        assert row.tripped is True
        assert row.reason == "daily realized loss limit exceeded"
        assert row.cleared_at is None
