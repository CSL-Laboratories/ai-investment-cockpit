"""WP6 bridge test fixtures.

Isolates the SEC-04 HALT sentinel per-test (``COCKPIT_HALT_PATH``) and pins a single
allowlisted operator (``COCKPIT_OPERATOR_IDS``) so ``bridge.operators.is_authorized`` is
deterministic. Builds an ``OrchestratorState`` on a tmp SQLite DB + a ``FrozenClock`` — no
wall-clock, no network.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"wp6-unit-key"
OPERATOR = "123456789012345678"
FOREIGN = "234567890123456789"
NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)
    yield


@pytest.fixture
def clock():
    return FrozenClock(NOW)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'wp6.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=clock)


def make_risk(*, rid="rd-1", proposal_id="prop-1", decision="approved",
              evaluated_at=NOW, checks=None, failed_checks=None, warnings=None,
              max_allowed_notional=None, human_required=False) -> RiskDecision:
    return RiskDecision(
        id=rid,
        proposal_id=proposal_id,
        decision=decision,
        checks=checks if checks is not None else {"buying_power": True, "concentration": True},
        failed_checks=failed_checks if failed_checks is not None else [],
        warnings=warnings if warnings is not None else [],
        max_allowed_notional=max_allowed_notional,
        human_required=human_required,
        evaluated_at=evaluated_at,
        created_at=evaluated_at,
    )


def make_order(*, symbol="AAPL", side="buy", qty="10", order_type="market",
               limit_price=None, tif="day") -> dict:
    return {"symbol": symbol, "side": side, "qty": qty, "order_type": order_type,
            "limit_price": limit_price, "tif": tif}
