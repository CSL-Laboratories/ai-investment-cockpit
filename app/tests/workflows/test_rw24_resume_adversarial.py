"""Independent composed-control regressions for the resumed RW-24 repair."""

import pytest

from config.settings import Mode
from orchestrator.mode_ladder import ModeLadder
from schemas.models.order import Order
from tests.workflows.conftest import FakeTransport, envelope
from tests.workflows.test_proposals import (
    HOLDINGS,
    WATCHLIST,
    _grounding_body,
    _payloads,
    _persist_event,
    _proposal_body,
    _quote_fn,
    _rows,
    _seed_snapshot,
)
from workflows.constants import EVENT_PROPOSAL
from workflows.packets import build_packet
from workflows.proposals import draft_proposal


def _attempt(st, clock, tmp_path, *, quantity, notional=None, grounded=True):
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    row = _persist_event(st, clock, tmp_path)
    return draft_proposal(
        st, clock, event=row, packet=build_packet([row]),
        transport=FakeTransport([
            envelope(_proposal_body(
                refs=[row.raw_ref], quantity=quantity, notional=notional,
            )),
            envelope(_grounding_body("prop-1", [row.raw_ref], grounded=grounded)),
        ]),
        parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
        holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
    )


def _assert_no_order_and_signed_status(st, outcome, status):
    assert outcome.order is None
    assert outcome.record.order_id is None
    assert outcome.embed["order_id"] is None
    assert _rows(st, Order) == []
    assert outcome.record.status == status
    event = _payloads(st, EVENT_PROPOSAL)[-1]
    assert event["status"] == status
    assert event["order_id"] is None
    assert event["risk_decision_id"] == outcome.risk_decision.id
    assert outcome.embed["risk"]["decision"] == outcome.risk_decision.decision
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("notional,risk_result", [(None, "rejected"), (500, "approved")])
def test_unresolved_quantity_never_erases_grounding_flag(
    st, clock, tmp_path, notional, risk_result,
):
    """Even an approved dollar fallback cannot clear a flagged fractional draft."""
    _seed_snapshot(st)
    outcome = _attempt(
        st, clock, tmp_path, quantity=5.7, notional=notional, grounded=False,
    )
    assert outcome.risk_decision.decision == risk_result
    assert outcome.status == "flagged"
    assert outcome.record.flagged is True
    assert any("quantity unresolvable" in reason for reason in outcome.reasons)
    _assert_no_order_and_signed_status(st, outcome, "flagged")


@pytest.mark.parametrize("grounded", [True, False])
def test_resolvable_quantity_does_not_bypass_rejected_risk(st, clock, tmp_path, grounded):
    """Missing portfolio authority rejects a whole-share draft with a fresh quote."""
    outcome = _attempt(st, clock, tmp_path, quantity=5, grounded=grounded)
    assert outcome.risk_decision.decision == "rejected"
    assert not any("quantity unresolvable" in reason for reason in outcome.reasons)
    assert outcome.status == ("drafted" if grounded else "flagged")
    assert outcome.record.flagged is (not grounded)
    _assert_no_order_and_signed_status(st, outcome, "draft" if grounded else "flagged")
