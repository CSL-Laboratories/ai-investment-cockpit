"""BUG-136: signed monetary facts survive persistence but still detect repainting."""
from decimal import Decimal

import pytest

from bridge.bot import ProposalControls
from bridge.embeds import render_risk_section
from schemas.models.risk_decision import RiskDecision
from tests.bridge.conftest import make_risk


def _signed(ceiling):
    return {"decision": "approved", "checks": [], "failed_checks": [], "warnings": [],
            "max_allowed_notional": ceiling, "human_required": False}


@pytest.mark.parametrize("ceiling", ["2500", "2500.0", "2.5E+3", "0", "0.01"])
def test_saved_risk_facts_survive_decimal_scale_change(st, ceiling):
    row = make_risk(checks=[], max_allowed_notional=Decimal(ceiling))
    st.audit.record_state_change(row, "risk_decision", actor="risk", payload=_signed(ceiling))

    def load(_proposal_id):
        with st.session_factory() as session:
            return session.get(RiskDecision, row.id)

    controls = ProposalControls(st, None, resolve_order=lambda _: {}, resolve_risk=load)
    rendered = controls.render({"proposal_id": row.proposal_id})["risk"]
    assert rendered["verified"] is True
    assert rendered["decision"] == "approved"
    assert Decimal(rendered["max_allowed_notional"]) == Decimal(ceiling)
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("replacement", ["2499.99", "2500.01"])
def test_saved_ceiling_repaint_is_still_refused(st, replacement):
    row = make_risk(checks=[], max_allowed_notional=Decimal("2500"))
    st.audit.record_state_change(row, "risk_decision", actor="risk", payload=_signed("2500"))
    with st.session_factory() as session, session.begin():
        session.get(RiskDecision, row.id).max_allowed_notional = Decimal(replacement)
    with st.session_factory() as session:
        reloaded = session.get(RiskDecision, row.id)
    rendered = render_risk_section(reloaded, audit_facts=_signed("2500"))
    assert rendered["unverified"] is True
    assert rendered["max_allowed_notional"] is None


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "garbage", True])
def test_matching_invalid_money_never_becomes_verified(value):
    row = make_risk(checks=[], max_allowed_notional=value)
    assert render_risk_section(row, audit_facts=_signed(value))["unverified"] is True


def test_comparison_does_not_round_away_an_actual_money_difference():
    row = make_risk(checks=[], max_allowed_notional=Decimal("2500.001"))
    assert render_risk_section(row, audit_facts=_signed("2500"))["unverified"] is True
