"""WP1a G1a gate — the whole schema migrates: importing every model registers its
table on the shared Base.metadata, and create_all builds all 10 tables on SQLite."""
from __future__ import annotations

import importlib

from sqlalchemy import create_engine, inspect

from schemas.models.base import Base

MODEL_MODULES = [
    "market_event", "portfolio_snapshot", "agent_job", "risk_decision",
    "approval_record", "audit_entry", "automation_rule", "mode_state",
    "breaker_state", "usage_window",
]
EXPECTED_TABLES = {
    "market_event", "portfolio_snapshot", "agent_job", "risk_decision",
    "approval_record", "audit_entry", "automation_rule", "mode_state",
    "breaker_state", "usage_window",
}


def test_all_models_migrate():
    for m in MODEL_MODULES:
        importlib.import_module(f"schemas.models.{m}")
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    tables = set(inspect(eng).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}; got {sorted(tables)}"
