"""WP1a round-trip test for the ``ApprovalRecord`` model.

Imports only its own model + the shared Base (never ``schemas.models``, which
would pull sibling modules that may not exist yet).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.approval_record import ApprovalRecord


def test_approval_record_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(
            ApprovalRecord(
                id="apr_1",
                proposal_id="prop_1",
                risk_decision_id="rd_1",
                actor_user_id="disc_user_42",
                user_decision="approve",
                surface="discord",
                interaction_id="int_abc",
                message_id="msg_xyz",
                device="mobile",
                order_tuple_hash="a1b2c3d4",
                single_use_consumed=False,
                notes="looks good",
                decided_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
        )
        s.commit()

        row = s.get(ApprovalRecord, "apr_1")
        assert row.proposal_id == "prop_1"
        assert row.risk_decision_id == "rd_1"
        assert row.user_decision == "approve"
        assert row.order_tuple_hash == "a1b2c3d4"
        assert row.single_use_consumed is False


def test_approval_record_nullable_fields_default_none():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(
            ApprovalRecord(
                id="apr_2",
                proposal_id="prop_2",
                risk_decision_id="rd_2",
                actor_user_id="disc_user_42",
                user_decision="veto",
                surface="desktop",
                decided_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
        )
        s.commit()

        row = s.get(ApprovalRecord, "apr_2")
        assert row.interaction_id is None
        assert row.message_id is None
        assert row.device is None
        assert row.order_tuple_hash is None
        assert row.notes is None
