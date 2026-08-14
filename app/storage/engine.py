"""WP2 — SQLite engine + session factory with the pragmas the audit chain needs.

WAL journal mode gives concurrent readers while a single writer holds the write lock
(OPS-01/OPS-03); busy_timeout makes other writers wait rather than error.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from schemas.models import Base, load_all


def make_engine(url: str = "sqlite:///cockpit.db", echo: bool = False):
    engine = create_engine(url, echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial pragma setup
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")       # OPS-03: concurrent readers
        cur.execute("PRAGMA busy_timeout=5000")      # OPS-03: wait on the single writer
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


def create_all(engine) -> None:
    load_all()                       # register every model on Base.metadata
    Base.metadata.create_all(engine)
    _apply_additive_migrations(engine)


def _apply_additive_migrations(engine) -> None:
    """Apply the deliberately small set of backwards-compatible SQLite migrations.

    The project predates a migration framework and production uses a persistent SQLite DB;
    ``create_all`` does not add columns to an existing table. W-22 therefore owns this explicit,
    idempotent migration rather than requiring an operator to delete broker/audit history.
    """
    if engine.dialect.name != "sqlite":
        return
    columns = {
        column["name"] for column in inspect(engine).get_columns("portfolio_snapshot")
    }
    if "equity" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE portfolio_snapshot ADD COLUMN equity NUMERIC(18, 2)"
            ))
