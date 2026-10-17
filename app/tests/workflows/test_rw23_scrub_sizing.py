"""BUG-149: a missing size must not skip the pre-persistence prose scrub."""
import pytest

from schemas.models.proposal_record import ProposalRecord
from tests.workflows.conftest import FakeTransport, envelope
from tests.workflows.test_proposals import (
    HOLDINGS, WATCHLIST, _grounding_body, _persist_event, _proposal_body,
)
from workflows.packets import build_packet
from workflows.proposals import draft_proposal


@pytest.mark.parametrize("field", ["thesis", "invalidation", "sizing_basis", "generated_by"])
def test_sizeless_proposal_scrubs_before_any_persistence(st, clock, tmp_path, field):
    event = _persist_event(st, clock, tmp_path)
    body = _proposal_body(quantity=None, notional=None, refs=[event.raw_ref])
    poison = "account 12345678901 holds TSLA 40% of book"
    body[field] = {"runtime": poison, "model": "fixture"} if field == "generated_by" else poison
    outcome = draft_proposal(
        st, event=event, packet=build_packet([event]),
        transport=FakeTransport([envelope(body)]), parquet_root=tmp_path / "raw",
    )
    assert outcome.status == "quarantined"
    assert outcome.record.flagged
    assert outcome.order is outcome.risk_decision is outcome.embed is None
    with st.session_factory() as session:
        stored = session.get(ProposalRecord, outcome.record.id)
        assert "12345678901" not in str(stored.payload)
        assert "TSLA 40%" not in str(stored.payload)
    assert "12345678901" not in str(outcome.proposal)
    assert st.audit.verify_chain().ok


def test_clean_sizeless_proposal_still_requests_sizing(st, clock, tmp_path):
    event = _persist_event(st, clock, tmp_path)
    body = _proposal_body(quantity=None, notional=None, refs=[event.raw_ref])
    outcome = draft_proposal(
        st, event=event, packet=build_packet([event]),
        transport=FakeTransport([
            envelope(body),
            envelope(_grounding_body("prop-1", [event.raw_ref])),
        ]),
        parquet_root=tmp_path / "raw",
        holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
    )
    assert outcome.status == "needs_human"
    assert not outcome.record.flagged
    assert outcome.order is None
