"""WP1a — round-trip test for the ``AuditEntry`` ledger model."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.audit_entry import AuditEntry


def test_audit_entry_roundtrip() -> None:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            AuditEntry(
                id="ae_1",
                seq=1,
                prev_hash=None,  # genesis row
                entry_hash="a" * 64,
                event_type="order_submitted",
                actor="operator",
                refs={"proposal_id": "prop_1", "order_id": "ord_1"},
                payload={"symbol": "AAPL", "side": "buy"},
                created_at=now,
            )
        )
        s.commit()

        row = s.get(AuditEntry, "ae_1")
        assert row is not None
        assert row.seq == 1
        assert row.prev_hash is None
        assert row.entry_hash == "a" * 64
        assert row.event_type == "order_submitted"
        assert row.actor == "operator"
        assert row.refs["proposal_id"] == "prop_1"
        assert row.payload["symbol"] == "AAPL"
