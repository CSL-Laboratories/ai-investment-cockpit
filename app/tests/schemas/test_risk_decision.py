"""WP1a round-trip test for the RiskDecision model."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.risk_decision import RiskDecision


def test_risk_decision_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            RiskDecision(
                id="rd_1",
                proposal_id="tp_1",
                decision="approved",
                checks={"max_notional": {"ok": True}},
                failed_checks=[],
                warnings=["stale_quote_soft"],
                max_allowed_notional=2500.00,
                human_required=False,
                evaluated_at=now,
                created_at=now,
            )
        )
        s.commit()
        row = s.get(RiskDecision, "rd_1")
        assert row.proposal_id == "tp_1"
        assert row.decision == "approved"
        assert row.checks == {"max_notional": {"ok": True}}
        assert row.warnings == ["stale_quote_soft"]
        assert float(row.max_allowed_notional) == 2500.00
        assert row.human_required is False
