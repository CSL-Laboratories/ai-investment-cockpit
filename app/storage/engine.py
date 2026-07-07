"""WP2 — SQLite engine + session factory with the pragmas the audit chain needs.

WAL journal mode gives concurrent readers while a single writer holds the write lock
(OPS-01/OPS-03); busy_timeout makes other writers wait rather than error.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
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
