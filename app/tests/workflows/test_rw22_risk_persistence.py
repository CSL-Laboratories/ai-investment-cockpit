"""The real drafting path signs the same conservative cents that it persists."""
from decimal import Decimal

from bridge.embeds import render_risk_section, verified_risk_facts
from config.settings import Mode
from orchestrator.limits import EVENT_LIMITS_CHANGE
from orchestrator.mode_ladder import ModeLadder
from schemas.models.risk_decision import RiskDecision
from tests.workflows.conftest import FakeTransport, envelope
from tests.workflows.test_proposals import (
    HOLDINGS, WATCHLIST, _grounding_body, _persist_event, _proposal_body, _quote_fn,
    _seed_snapshot,
)
from workflows.packets import build_packet
from workflows.proposals import draft_proposal


def test_draft_signs_and_renders_persisted_subcent_ceiling(st, clock, tmp_path):
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    st.audit.append(EVENT_LIMITS_CHANGE, actor="op",
                    payload={"limits": {"max_notional": "999.999"}})
    event = _persist_event(st, clock, tmp_path)
    transport = FakeTransport([
        envelope(_proposal_body(refs=[event.raw_ref])),
        envelope(_grounding_body("prop-1", [event.raw_ref])),
    ])
    outcome = draft_proposal(
        st, clock, event=event, packet=build_packet([event]), transport=transport,
        parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
        holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
    )
    assert outcome.status == "drafted"
    with st.session_factory() as session:
        saved = session.get(RiskDecision, outcome.risk_decision.id)
    facts = verified_risk_facts(st, saved.id)
    assert saved.max_allowed_notional == Decimal("999.99")
    assert Decimal(facts["max_allowed_notional"]) == saved.max_allowed_notional
    assert outcome.embed["risk"]["verified"] is True
    assert render_risk_section(saved, audit_facts=facts)["verified"] is True
    assert st.audit.verify_chain().ok
