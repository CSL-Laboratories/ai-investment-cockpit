"""WP1a — AutomationRule round-trip test."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.automation_rule import AutomationRule


def test_automation_rule_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(
            AutomationRule(
                id="rule_1",
                natural_language="auto-approve rebalancing trades under $200 in existing positions",
                predicate={"side": "buy", "max_notional": 200},
                confirmed_hash="a1b2c3d4",
                worst_case_notional=200.00,
                symbols_scope=["AAPL", "MSFT"],
                existing_positions_only=True,
                enabled=True,
                expires_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
        )
        s.commit()
        row = s.get(AutomationRule, "rule_1")
        assert row.confirmed_hash == "a1b2c3d4"
        assert row.existing_positions_only is True
        assert row.symbols_scope == ["AAPL", "MSFT"]
