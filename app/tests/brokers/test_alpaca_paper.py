"""WP7 unit tests — the Alpaca paper broker adapter (injectable seam, no network)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.order_tuple import canonical_order_tuple
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState
from schemas.models.audit_entry import AuditEntry
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from storage.engine import create_all, make_engine, make_session_factory

CLK = FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'alp.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"alp", settings=Settings(), clock=CLK)


def test_submit_order_returns_ack_shape():
    broker = AlpacaPaperBroker()   # default in-process FakeAlpaca; no network
    canon = canonical_order_tuple(symbol="AAPL", side="buy", qty="3", order_type="market",
                                  tif="day")
    res = broker.submit_order(canon)
    assert set(res) == {"broker_order_id", "status", "filled_qty", "avg_fill_price"}
    assert res["broker_order_id"] and res["status"] == "accepted"


def test_client_is_injectable():
    fake = FakeAlpaca(account="paper", cash="42.00")
    broker = AlpacaPaperBroker(client=fake)
    broker.submit_order(canonical_order_tuple(symbol="MSFT", side="buy", qty="1",
                                              order_type="market", tif="day"))
    assert len(fake.submitted) == 1 and fake.submitted[0]["symbol"] == "MSFT"


def test_get_account_persists_snapshot_when_st_given(st):
    broker = AlpacaPaperBroker(client=FakeAlpaca(cash="123.45", buying_power="200.00"))
    snap = broker.get_account(st=st, clock=CLK)
    with st.session_factory() as s:
        row = s.get(PortfolioSnapshot, snap.id)
        assert row is not None and Decimal(str(row.cash)) == Decimal("123.45")
        # covered by an audited state change (Inv 6); the chain verifies.
        entries = s.execute(select(AuditEntry)).scalars().all()
    assert any((e.refs or {}).get("row_id") == snap.id for e in entries)
    assert st.audit.verify_chain().ok


def test_get_account_without_st_does_not_persist(st):
    broker = AlpacaPaperBroker(client=FakeAlpaca())
    snap = broker.get_account(clock=CLK)   # no st -> unpersisted
    with st.session_factory() as s:
        assert s.get(PortfolioSnapshot, snap.id) is None


def test_get_positions_populates_holdings(st):
    fake = FakeAlpaca(positions=[{"symbol": "AAPL", "qty": "5"}])
    broker = AlpacaPaperBroker(client=fake)
    snap = broker.get_positions(st=st, clock=CLK)
    assert snap.holdings == [{"symbol": "AAPL", "qty": "5"}]
