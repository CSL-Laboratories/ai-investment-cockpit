"""EDGAR pipeline: an SEC halt during a run counts as a failure and grants no completion.

Real ORM on a scratch SQLite, real AuditLog, recorded fixtures through an in-memory
transport: no request leaves the process.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from config.settings import Settings
from core.clock import FrozenClock
from filings.edgar.fetch import (
    SecFetcher, SecRunByteBudgetExceeded, companyfacts_url, submissions_url,
)
from filings.raw_store import RawStore
from ingest.edgar_pipeline import run_edgar_pipeline
from schemas.models.audit_entry import AuditEntry
from schemas.models.filing import Filing, FormType
from storage.audit import AuditLog
from storage.engine import create_all, make_engine, make_session_factory
from tests.filings._sec_replay import FixtureTransport, VirtualTime

UA = "CSL Laboratories test contact@example.invalid"
NVDA = "0001045810"
AAPL = "0000320193"
CLOCK = FrozenClock(datetime(2026, 7, 8, 9, 30, tzinfo=UTC))
PROD_CAP = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                    sec_max_new_filings_per_poll=25)


@pytest.fixture(autouse=True)
def _no_live_sec(monkeypatch):
    import urllib.request

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("a live urllib.request.urlopen was attempted")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def sf(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 'cockpit.db').as_posix()}")
    create_all(eng)
    return make_session_factory(eng)


@pytest.fixture
def store(tmp_path):
    return RawStore(tmp_path / "raw")


class HaltingTransport(FixtureTransport):
    """Fixture replay that answers ONE url with a halt (an HTTP status, or a raised
    resource-limit halt, which SecFetcher latches)."""

    def __init__(self, halt_url, halt):
        super().__init__()
        self.halt_url = halt_url
        self.halt = halt
        if isinstance(halt, int):
            self.registry[halt_url] = (halt, b"")

    def __call__(self, method, url, headers):
        if url == self.halt_url and not isinstance(self.halt, int):
            self.requests.append((method, url, dict(headers)))
            raise SecRunByteBudgetExceeded(url, 10**9, 1)
        return super().__call__(method, url, headers)


def _fetcher(transport):
    vt = VirtualTime()
    return SecFetcher(user_agent=UA, rate_per_sec=2.0, transport=transport,
                      monotonic=vt.monotonic, sleep=vt.sleep)


def _run(sf, store, audit, transport, ciks=(AAPL, NVDA)):
    return run_edgar_pipeline(
        sf, settings=PROD_CAP, ciks=list(ciks), raw_store=store, clock=CLOCK,
        fetcher=_fetcher(transport), audit_log=audit, process_epoch="f1-process",
    )


def _grants(sf):
    with sf() as session:
        return session.execute(
            select(func.count()).select_from(AuditEntry)
            .where(AuditEntry.event_type == "edgar_poll_completed")
        ).scalar_one()


def _pending_10q(sf, cik, accession, filed):
    """An ingested 10-Q with no Delta and no ledger row: an extraction candidate whose
    compute_deltas raises UnpairedPeriodicFiling before any raw read (a clean baseline)."""
    with sf() as session, session.begin():
        session.add(Filing(
            cik=cik, accession_no=accession, form_type=FormType.FORM_10Q,
            filing_date=filed, detection_ts=CLOCK.now(), raw_ref=accession,
            primary_doc="q.htm",
        ))


def _baselined_with_pending_extraction(sf, store):
    audit = AuditLog(sf, hmac_key=b"f1-key", clock=CLOCK)
    first = _run(sf, store, audit, FixtureTransport())
    assert first.ok and first.poll_errors == [] and _grants(sf) == 2
    # AAPL's candidate sorts first, so its companyfacts is the first enrichment fetch;
    # NVDA's candidate is never reached after a halt there.
    _pending_10q(sf, AAPL, "0000320193-26-900001", date(2026, 7, 1))
    _pending_10q(sf, NVDA, "0001045810-26-900001", date(2026, 7, 2))
    return audit


def test_control_no_halt_grants_both_ciks(sf, store):
    audit = _baselined_with_pending_extraction(sf, store)
    result = _run(sf, store, audit, FixtureTransport())
    assert result.ok, result.summary()
    assert result.baselines == 2
    assert _grants(sf) == 4


@pytest.mark.parametrize("halt", [429, 403, "resource"])
def test_companyfacts_halt_withholds_every_completion_grant(sf, store, halt):
    audit = _baselined_with_pending_extraction(sf, store)
    transport = HaltingTransport(companyfacts_url(AAPL), halt)
    result = _run(sf, store, audit, transport)

    assert result.ok is False
    assert transport.requests[-1][1] == companyfacts_url(AAPL), "fetched after the halt"
    # NVDA polled cleanly but its pending extraction never ran; AAPL halted. A halt is
    # global (edgar_pipeline.py:1373): no CIK from this run may receive a grant.
    assert _grants(sf) == 2, "a halted run recorded an edgar_poll_completed grant"
    assert audit.verify_chain().ok


@pytest.mark.parametrize("halt", [429, 403, "resource"])
def test_first_contact_halt_is_a_counted_failure_with_no_grant(sf, store, halt):
    audit = AuditLog(sf, hmac_key=b"f1-key", clock=CLOCK)
    # AAPL baselines first; NVDA's first-contact submissions read halts.
    transport = HaltingTransport(submissions_url(NVDA), halt)
    result = _run(sf, store, audit, transport)

    assert transport.requests[-1][1] == submissions_url(NVDA), "fetched after the halt"
    assert _grants(sf) == 0
    assert result.ciks_polled == 0
    assert result.ok is False, "a halted first contact reported ok"
    assert result.quiet is False
    assert result.poll_errors, "a halted first contact recorded no error"
    assert audit.verify_chain().ok
