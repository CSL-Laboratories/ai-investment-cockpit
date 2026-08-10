"""BUG-095 — the Inv-11 idempotency backstop must cover INSTANT facts.

The old plain UniqueConstraint included nullable ``period_start``; SQLite treats NULLs as
distinct, so the commonest fact shape (a balance-sheet instant, NULL start) was never
deduplicated — a feed replay inserted a duplicate row per pass. Latent until fact
persistence is wired; pinned here before it ships looking idempotent.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from schemas.models.xbrlfact import XbrlFact
from storage.engine import create_all, make_engine, make_session_factory


def _fact(**over):
    base = dict(cik="0000000001", accession_no="0000000001-26-000001",
                taxonomy="us-gaap", concept="Assets", unit="USD",
                value=Decimal("100"), period_start=None,
                period_end=date(2026, 4, 26), raw_ref="companyfacts:0000000001")
    base.update(over)
    return XbrlFact(**base)


@pytest.fixture
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'xbrl.db').as_posix()}")
    create_all(engine)
    return make_session_factory(engine)


def test_duplicate_instant_facts_are_refused(sf):
    with sf() as s, s.begin():
        s.add(_fact())
    with pytest.raises(IntegrityError):
        with sf() as s, s.begin():
            s.add(_fact())


def test_duplicate_duration_facts_are_still_refused(sf):
    with sf() as s, s.begin():
        s.add(_fact(period_start=date(2026, 1, 27)))
    with pytest.raises(IntegrityError):
        with sf() as s, s.begin():
            s.add(_fact(period_start=date(2026, 1, 27)))


def test_distinct_periods_coexist(sf):
    with sf() as s, s.begin():
        s.add(_fact())
        s.add(_fact(period_end=date(2026, 1, 26)))
        s.add(_fact(period_start=date(2026, 1, 27)))
