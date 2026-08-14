"""WP1a G1a gate — the whole schema migrates: importing every model registers its
table on the shared Base.metadata, and create_all builds all 10 tables on SQLite."""
from __future__ import annotations

import importlib

from sqlalchemy import create_engine, inspect, text

from schemas.models.base import Base
from storage.engine import create_all, make_engine

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


def test_w22_additive_migrations_preserve_legacy_rows_and_are_idempotent(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with eng.begin() as connection:
        connection.execute(text(
            "CREATE TABLE portfolio_snapshot (id VARCHAR PRIMARY KEY, account VARCHAR)"
        ))
        connection.execute(text(
            "CREATE TABLE risk_decision (id VARCHAR PRIMARY KEY, proposal_id VARCHAR)"
        ))
        connection.execute(text(
            "INSERT INTO portfolio_snapshot (id, account) "
            "VALUES ('snapshot-legacy', 'paper')"
        ))
        connection.execute(text(
            "INSERT INTO risk_decision (id, proposal_id) "
            "VALUES ('risk-legacy', 'proposal-legacy')"
        ))

    create_all(eng)
    create_all(eng)

    inspector = inspect(eng)
    assert "equity" in {
        column["name"] for column in inspector.get_columns("portfolio_snapshot")
    }
    assert "execution_verdict" in {
        column["name"] for column in inspector.get_columns("risk_decision")
    }
    with eng.connect() as connection:
        assert connection.execute(text(
            "SELECT id FROM portfolio_snapshot"
        )).scalar_one() == "snapshot-legacy"
        assert connection.execute(text(
            "SELECT id FROM risk_decision"
        )).scalar_one() == "risk-legacy"
