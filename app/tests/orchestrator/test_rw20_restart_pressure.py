"""Restart pressure against isolated DB/HALT paths; no production recovery or ledger repair."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.breakers import BreakerEngine, StaleAnalysisError
from orchestrator.halt import default_halt_path, halt_is_set, set_halt
from orchestrator.state import OrchestratorState
from schemas.models.audit_entry import AuditEntry
from storage.engine import create_all, make_engine, make_session_factory
from tests.brokers.test_paper_execution import OPERATOR_ID, _service

KEY = b"rw20-isolated-restart-test-key"


@pytest.fixture
def state(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw20-restart.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    clock = FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))
    yield OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=clock)
    engine.dispose()


def _restart(old, service):
    state = OrchestratorState.build(
        old.session_factory, hmac_key=KEY, settings=old.settings, clock=old.clock,
    )
    registry = TokenRegistry()
    gateway = OrderGateway(
        service._broker, st=state, token_registry=registry, execution_scope="paper",
        require_analysis_generation=True,
    )
    restarted_service = PaperExecutionService(
        state, broker=service._broker, gateway=gateway, token_registry=registry,
        quote_fn=service._quote_fn, watchlist_fn=service._watchlist_fn,
    )
    return state, restarted_service


@pytest.mark.parametrize("vendor", ["claude", "codex"])
@pytest.mark.parametrize("recovered", [False, True])
def test_vendor_blackout_and_recovery_never_revive_old_proposal_after_restart(
    state, vendor, recovered,
):
    service, scenario, client = _service(state, runtime=vendor, selected_runtime=vendor)
    breaker = BreakerEngine(state)
    breaker.enter_watch_only(vendor, "isolated vendor limit", actor="test")
    if recovered:
        breaker.recover_from_watch_only(vendor, Mode.paper, actor="test")
    restarted, after = _restart(state, service)
    expected_generation = 2 if recovered else 1
    new_breaker = BreakerEngine(restarted)
    assert restarted.process_epoch != state.process_epoch
    assert new_breaker.current_generation(vendor) == expected_generation
    assert new_breaker.is_watch_only(vendor) is not recovered
    with pytest.raises(StaleAnalysisError):
        new_breaker.assert_fresh_analysis(vendor, 0)
    with pytest.raises(PaperExecutionError) as refused:
        after.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve", confirm_symbol="AAPL",
            request_id="rw20-restarted-stale-proposal",
        )
    assert refused.value.code == "stale_analysis"
    assert client.submitted == []


def test_sec_limit_halt_survives_restart_without_assuming_claude_generation_bumps(state):
    service, scenario, client = _service(state)
    set_halt(default_halt_path(), reason="isolated SEC 429 HALT")
    BreakerEngine(state).enter_watch_only("sec", "SEC 429", actor="test")
    restarted, after = _restart(state, service)
    breaker = BreakerEngine(restarted)
    assert halt_is_set(default_halt_path())
    assert breaker.is_watch_only("sec") and breaker.current_generation("sec") == 1
    # Vendor generations are independent: HALT and global watch_only supply this refusal.
    assert breaker.current_generation("claude") == 0
    with pytest.raises(PaperExecutionError) as refused:
        after.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve", confirm_symbol="AAPL",
            request_id="rw20-restarted-sec-halt",
        )
    assert refused.value.code == "fresh_risk_failed" or refused.value.code.startswith("gateway_")
    assert client.submitted == []


def test_corrupt_signed_watch_only_installs_global_restart_refusal_without_healing(state):
    service, scenario, client = _service(state)
    BreakerEngine(state).enter_watch_only("claude", "isolated vendor limit", actor="test")
    with state.session_factory() as session, session.begin():
        row = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "watch_only")
        ).scalar_one()
        row.payload = {**row.payload, "state": "ok", "generation": 0}
    restarted, after = _restart(state, service)
    assert restarted.usage.integrity_error
    assert BreakerEngine(restarted).is_watch_only()
    with pytest.raises(PaperExecutionError) as refused:
        after.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve", confirm_symbol="AAPL",
            request_id="rw20-restarted-invalid-ledger",
        )
    assert refused.value.code == "lineage_unverified"
    assert client.submitted == []
    assert not restarted.audit.verify_chain().ok
