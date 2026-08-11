"""RW-16 adversarial round 2 — the ``incomplete_lineage`` guard, from both sides.

``PaperExecutionService`` is the ONLY wired human gate, and it refuses to show or adjudicate an
order whose proposal cannot be traced to the run that produced it. Finding 8 was that the
deterministic copy path set ``ProposalRecord.job_id = None``, so EVERY copy order hit that
guard: the feature could not complete a trade even in paper.

The fix gave the deterministic producer a real, auditable ``AgentJob`` (see
``workflows/copytrade_orders._lineage_job``). This file exists to pin the OTHER half of that
fix — that the guard itself was not softened to let the copy path through. An order whose
lineage is genuinely absent, or genuinely dangling, or produced by a run that did not finish,
is still refused; and the deterministic runtime is admitted in ``paper`` and nowhere else.

House style: tmp SQLite, ``FrozenClock``, per-test HALT sentinel, a fake Alpaca transport
behind the PRODUCTION ``OrderGateway`` — a double for the gate would test nothing here.
"""
from __future__ import annotations

import pytest

from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from config.settings import DecisionRuntime, Mode, Settings
from core.ids import new_id
from schemas.models.agent_job import AgentJob
from schemas.models.proposal_record import ProposalRecord
from tests.brokers.conftest import build_scenario
from workflows.copytrade_orders import COPYTRADE_RUNTIME

OPERATOR_ID = "123456789012345678"


def _job(st, *, runtime="claude", status="done") -> AgentJob:
    job = AgentJob(
        id=new_id("job"), runtime=runtime, model="deep", model_pinned="pinned",
        prompt_template="trade_proposal.v1", prompt_hash="h" * 16,
        input_refs={}, schema_id="TradeProposal", status=status, created_at=st.clock.now(),
    )
    st.audit.record_state_change(job, "agent_job", actor="agent_calls",
                                 payload={"status": status, "runtime": runtime})
    return job


def _service(st, *, mode=Mode.paper, job_id="MISSING", runtime="claude", status="done"):
    """A stored, risk-approved, unflagged order whose LINEAGE is the variable under test.

    ``job_id="MISSING"`` means "write a real job and point at it". ``None`` means the proposal
    carries no lineage at all; any other string points the proposal at an id that does not
    resolve (the dangling case a deleted or never-written job produces).
    """
    st.settings = Settings(decision_runtime=DecisionRuntime("claude"),
                           operator_ids_config=OPERATOR_ID, _env_file=None)
    client = FakeAlpaca(cash="100000", buying_power="200000")
    broker = AlpacaPaperBroker(client=client)
    registry = TokenRegistry()
    gateway = OrderGateway(broker, st=st, token_registry=registry, execution_scope="paper")
    scenario = build_scenario(st, mode=mode, broker=broker, gateway=gateway, qty="1",
                              limit_price="150", notional="150", mint=False)
    resolved = _job(st, runtime=runtime, status=status).id if job_id == "MISSING" else job_id
    proposal = ProposalRecord(
        id="p1", symbol="AAPL", side="buy", status="draft", flagged=False,
        payload={"proposal_id": "p1", "symbol": "AAPL", "side": "buy",
                 "asset_type": "equity", "quantity": "1", "notional": "150",
                 "order_type": "limit", "sizing_basis": "capped"},
        job_id=resolved, risk_decision_id=scenario.risk_decision.id,
        order_id=scenario.order.id, created_at=st.clock.now(), updated_at=st.clock.now(),
    )
    st.audit.record_state_change(proposal, "proposal", actor="proposals",
                                 payload={"status": "draft", "symbol": "AAPL"})
    service = PaperExecutionService(
        st, broker=broker, gateway=gateway, token_registry=registry,
        quote_fn=lambda _s: {"price": "150", "age_s": 0, "spread_pct": "0.1"},
        watchlist_fn=lambda: {"AAPL"},
    )
    return service, scenario, client


def _refused(service, scenario, request_id):
    with pytest.raises(PaperExecutionError) as exc:
        service.decide(scenario.order.id, actor=OPERATOR_ID, decision="approve",
                       confirm_symbol="AAPL", request_id=request_id)
    return exc.value


@pytest.mark.parametrize("job_id, label", [
    (None, "no lineage at all"),
    ("job_that_was_never_written", "a dangling lineage pointer"),
])
def test_an_order_with_missing_lineage_is_still_refused(st, monkeypatch, job_id, label):
    """THE guard finding 8 must not have weakened. An order that cannot be traced back to a
    producing run must never be shown to, or adjudicated by, a human — whether the pointer is
    absent or merely does not resolve."""
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, job_id=job_id)

    with pytest.raises(PaperExecutionError) as preview_exc:
        service.preview(scenario.order.id)
    assert preview_exc.value.code == "incomplete_lineage", label

    assert _refused(service, scenario, "lineage-1").code == "incomplete_lineage", label
    assert client.submitted == [], "nothing may reach the broker without provenance"


@pytest.mark.parametrize("runtime", ["claude", COPYTRADE_RUNTIME])
def test_a_producing_run_that_did_not_finish_is_refused(st, monkeypatch, runtime):
    """Lineage that EXISTS but records a crashed run is not provenance, it is a guess. Refused
    for the model runtime and for the deterministic one alike."""
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime=runtime, status="failed")
    assert _refused(service, scenario, f"unfinished-{runtime}").code == "runtime_not_allowed"
    assert client.submitted == []


def test_a_foreign_model_runtime_is_still_refused(st, monkeypatch):
    """The configured-decision-runtime binding is untouched: a proposal from a runtime this
    deployment is not currently deciding with cannot enter the paper order path."""
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime="gemini")
    assert _refused(service, scenario, "foreign-runtime").code == "runtime_not_allowed"
    assert client.submitted == []


def test_the_deterministic_copy_runtime_is_admitted_in_paper(st, monkeypatch):
    """The positive half: a model-free deterministic producer IS valid provenance, so the copy
    path's orders are adjudicable. ``preview`` must name the runtime honestly."""
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime=COPYTRADE_RUNTIME)

    assert service.preview(scenario.order.id)["producing_runtime"] == COPYTRADE_RUNTIME
    out = service.decide(scenario.order.id, actor=OPERATOR_ID, decision="approve",
                         confirm_symbol="AAPL", request_id="copy-runtime-ok")
    assert out["status"] == "submitted"
    assert len(client.submitted) == 1
    assert st.audit.verify_chain().ok


def test_the_deterministic_copy_runtime_is_admitted_in_paper_and_nowhere_else(st, monkeypatch):
    """SEC-08 is not weakened by the admission above. Off the paper rung the deterministic
    producer is refused BEFORE any approval is recorded — belt to the mode check further down,
    so the allowance cannot survive a reordering of ``decide``."""
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, mode=Mode.review_live, runtime=COPYTRADE_RUNTIME)

    assert _refused(service, scenario, "copy-live").code == "paper_mode_required"
    assert client.submitted == []
