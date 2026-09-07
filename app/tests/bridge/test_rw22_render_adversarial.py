"""Independent RW-22 attack cases for the persisted risk ceiling renderer."""
from decimal import Decimal, localcontext

import pytest

from bridge.bot import ProposalControls
from bridge.embeds import render_risk_section
from schemas.models.risk_decision import RiskDecision
from tests.bridge.conftest import make_risk


def _facts(ceiling):
    return {
        "decision": "approved", "checks": [], "failed_checks": [], "warnings": [],
        "max_allowed_notional": ceiling, "human_required": False,
    }


def test_legacy_subcent_signed_ceiling_does_not_bless_rounded_persistence(st):
    row = make_risk(checks=[], max_allowed_notional=Decimal("999.999"))
    st.audit.record_state_change(
        row, "risk_decision", actor="risk", payload=_facts("999.999"),
    )

    def load(_proposal_id):
        with st.session_factory() as session:
            saved = session.get(RiskDecision, row.id)
            assert saved.max_allowed_notional == Decimal("1000.00")
            return saved

    controls = ProposalControls(st, None, resolve_order=lambda _: {}, resolve_risk=load)
    risk = controls.render({"proposal_id": row.proposal_id})["risk"]
    assert risk["unverified"] is True
    assert risk["decision"] is None and risk["max_allowed_notional"] is None
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("replacement", [
    "2499.999999999999999999999999999",
    "2500.000000000000000000000000001",
])
def test_comparison_preserves_differences_beyond_decimal_context(replacement):
    row = make_risk(checks=[], max_allowed_notional=Decimal(replacement))
    # Decimal arithmetic or normalization under this context would erase the attack.
    # Equality must compare the exact values supplied by the row and signed payload.
    with localcontext() as context:
        context.prec = 6
        risk = render_risk_section(row, audit_facts=_facts("2500"))
    assert risk["unverified"] is True and risk["max_allowed_notional"] is None


@pytest.mark.parametrize("value", [
    [], {}, "1e999999999999999999999999", Decimal("sNaN"), False,
])
@pytest.mark.parametrize("side", ["row", "signed"])
def test_invalid_money_on_either_side_refuses_without_exception(value, side):
    row = make_risk(checks=[], max_allowed_notional=value if side == "row" else Decimal("2500"))
    risk = render_risk_section(row, audit_facts=_facts(value if side == "signed" else "2500"))
    assert risk["unverified"] is True
    assert risk["decision"] is None and risk["max_allowed_notional"] is None
