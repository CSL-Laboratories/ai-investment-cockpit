"""BUG-153: gate population consists of decisions, not approval lifecycle writes."""
from datetime import timedelta

import pytest

from bridge.anchor import InMemoryAnchorStore, post_head_anchor
from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from orchestrator.gate import compute_gate
from orchestrator.state import EVENT_ADJUDICATION
from tests.brokers.conftest import OPEN_UTC, build_scenario


def metrics(st):
    st.anchor_store = InMemoryAnchorStore()
    post_head_anchor(st, record=False)
    return compute_gate(st, "auto_veto", now=OPEN_UTC + timedelta(weeks=5)).metrics


def test_successful_gateway_consumption_does_not_count_as_second_proposal(st):
    scenario = build_scenario(st, broker=AlpacaPaperBroker(client=FakeAlpaca()))
    before = metrics(st)
    assert scenario.gateway.submit(scenario.order, **scenario.submit_kwargs()).ok
    after = metrics(st)
    assert before["adjudicated"] == after["adjudicated"] == 1
    assert before["approval_rate"] == after["approval_rate"] == 1
    assert st.audit.verify_chain().ok


def test_repeated_decisions_count_one_proposal_using_latest_decision(st):
    for decision in ["modify", "approve", "approve", "reject"]:
        st.audit.append(EVENT_ADJUDICATION, actor="operator", payload={
            "decision": decision, "proposal_id": "one-proposal",
        })
    result = metrics(st)
    assert result["adjudicated"] == 1
    assert result["approvals"] == 0


def test_unknown_or_unidentified_decision_does_not_manufacture_population(st):
    for payload in [{"decision": "approve"}, {"decision": "approve", "proposal_id": ""},
                    {"decision": "invalid", "proposal_id": "p"}]:
        st.audit.append(EVENT_ADJUDICATION, actor="operator", payload=payload)
    result = metrics(st)
    assert result["adjudicated"] == 0
    assert result["approval_rate"] == 0


def test_consumption_kind_never_overrides_a_decision(st):
    st.audit.append(EVENT_ADJUDICATION, actor="operator", payload={
        "decision": "reject", "proposal_id": "p",
    })
    st.audit.append(EVENT_ADJUDICATION, actor="gateway", payload={
        "kind": "approval_consumed", "decision": "approve", "proposal_id": "p",
    })
    result = metrics(st)
    assert result["adjudicated"] == 1 and result["approvals"] == 0


@pytest.mark.parametrize("decision", [[], {}, None, "invalid"])
def test_malformed_latest_decision_cannot_keep_prior_approval(st, decision):
    st.audit.append(EVENT_ADJUDICATION, actor="operator", payload={
        "decision": "approve", "proposal_id": "p",
    })
    st.audit.append(EVENT_ADJUDICATION, actor="operator", payload={
        "decision": decision, "proposal_id": "p",
    })
    assert metrics(st)["adjudicated"] == 0
