"""CSL-236 — lock the strict-freshness ``EdgarPipelineRunError`` messages.

``_CompleteSubmissionsFetcher._enforce_submissions_freshness`` is reached from
``get_json`` only for a strict CIK that already has a saved baseline. The pipeline
swallows the exception into ``poll_errors`` as a type name, so these tests call
``get_json`` directly and require the full message.

The raise ``saved SEC submissions boundary is ambiguous`` is unreachable. A repeated
accession fails the unique-accession check first (``SEC submissions accessions are
invalid or ambiguous``), so the boundary matcher never sees more than one hit.
That path is documented here and is not asserted.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from config.settings import Settings
from core.clock import FrozenClock
from filings.edgar.fetch import submissions_url
from filings.raw_store import RawStore
from ingest.edgar_pipeline import (
    EdgarPipelineRunError,
    _CompleteSubmissionsFetcher,
    _VerifiedPollBaselineStore,
    run_edgar_pipeline,
)
from storage.engine import create_all, make_engine, make_session_factory

UA = "Lucas Guerra lucas@example.com (personal research)"
NVDA = "0001045810"
CLOCK = FrozenClock(datetime(2026, 7, 8, 9, 30, tzinfo=UTC))

# Newest-first. The saved boundary is the oldest row, so the 8-K and 10-Q are
# post-boundary and the allow-list actually runs.
EIGHT_K = "0001045810-26-000012"
TEN_Q = "0001045810-26-000011"
CUTOFF = "0001045810-26-000010"

MALFORMED_ARRAYS = "malformed SEC submissions arrays block strategy freshness"
MISSING_CHRONOLOGY = "SEC submissions response lacks required strategy chronology"
BAD_ACCESSIONS = "SEC submissions accessions are invalid or ambiguous"
MALFORMED_CHRONOLOGY = "SEC submissions chronology is malformed"
BAD_ORDER = "SEC submissions rows are not in unambiguous newest-first order"


def _unsupported(accession: str) -> str:
    return f"unsupported post-boundary SEC filing {accession} blocks freshness"


class _StaticSubmissions:
    """One submissions document. Any other fetch fails closed inside the test."""

    def __init__(self, document):
        self._document = document

    def get_json(self, url):
        expected = submissions_url(NVDA)
        if str(url) != expected:
            raise AssertionError(f"unexpected JSON fetch: {url}")
        return self._document

    def get_bytes(self, url):
        raise AssertionError(f"unexpected byte fetch: {url}")


def _clean_submissions():
    """Columnar ``filings.recent`` shape published by data.sec.gov submissions."""
    rows = (
        (EIGHT_K, "8-K", "2026-07-09", "2026-07-09T16:00:00.000Z", "nvda-8k.htm"),
        (TEN_Q, "10-Q", "2026-07-08", "2026-07-08T16:00:00.000Z", "nvda-10q.htm"),
        (CUTOFF, "10-K", "2026-07-07", "2026-07-07T16:00:00.000Z", "nvda-10k.htm"),
    )
    columns = {
        "accessionNumber": [],
        "form": [],
        "filingDate": [],
        "reportDate": [],
        "acceptanceDateTime": [],
        "primaryDocument": [],
    }
    for accession, form, filed, accepted, primary in rows:
        columns["accessionNumber"].append(accession)
        columns["form"].append(form)
        columns["filingDate"].append(filed)
        columns["reportDate"].append(filed)
        columns["acceptanceDateTime"].append(accepted)
        columns["primaryDocument"].append(primary)
    return {"cik": NVDA, "filings": {"recent": columns}}


def _audit_log(sf):
    from storage.audit import AuditLog

    return AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=CLOCK)


def _strict_fetcher(sf, document, *, cutoff=CUTOFF):
    audit = _audit_log(sf)
    baselines = _VerifiedPollBaselineStore(sf, audit)
    assert baselines.establish(NVDA, cutoff, CLOCK.now()) is True
    fetcher = _CompleteSubmissionsFetcher(
        _StaticSubmissions(document), baselines, strict_ciks={NVDA},
    )
    return fetcher, audit, baselines


def _empty_recent(recent):
    recent.clear()


def _accession_not_a_list(recent):
    recent["accessionNumber"] = EIGHT_K


def _form_not_a_list(recent):
    recent["form"] = "8-K"


def _mismatched_lengths(recent):
    recent["filingDate"] = recent["filingDate"][:-1]


def _drop(column):
    def mutate(recent):
        del recent[column]

    return mutate


def _invalid_accession(recent):
    recent["accessionNumber"][0] = "not-an-accession"


def _duplicate_accessions(recent):
    # Same accession twice, with chronology that would otherwise be well ordered.
    # Uniqueness rejects this before the boundary matcher can call the match ambiguous.
    recent["accessionNumber"][1] = recent["accessionNumber"][0]


def _bad_filing_date(recent):
    recent["filingDate"][0] = "not-a-date"


def _bad_acceptance(recent):
    recent["acceptanceDateTime"][0] = "not-a-timestamp"


def _not_newest_first(recent):
    recent["filingDate"][0] = "2026-07-07"
    recent["acceptanceDateTime"][0] = "2026-07-07T16:00:00.000Z"
    recent["filingDate"][2] = "2026-07-09"
    recent["acceptanceDateTime"][2] = "2026-07-09T16:00:00.000Z"


def _acceptance_not_newest_first(recent):
    recent["filingDate"][1] = recent["filingDate"][0]
    recent["acceptanceDateTime"][1] = "2026-07-09T18:00:00.000Z"


def _acceptance_date_mismatch(recent):
    recent["acceptanceDateTime"][0] = "2026-07-08T16:00:00.000Z"


def _unsupported_form(recent):
    recent["form"][0] = "8-K/A"


def _missing_primary(recent):
    recent["primaryDocument"][0] = ""


@pytest.fixture(autouse=True)
def _no_live_sec(monkeypatch):
    """Nothing in this module may reach sec.gov."""
    import urllib.request

    def _boom(*_args, **_kwargs):
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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(_empty_recent, MALFORMED_ARRAYS, id="empty-recent"),
        pytest.param(_accession_not_a_list, MALFORMED_ARRAYS, id="accession-not-a-list"),
        pytest.param(_form_not_a_list, MALFORMED_ARRAYS, id="form-not-a-list"),
        pytest.param(_mismatched_lengths, MALFORMED_ARRAYS, id="mismatched-lengths"),
        pytest.param(_drop("form"), MISSING_CHRONOLOGY, id="missing-form"),
        pytest.param(_drop("filingDate"), MISSING_CHRONOLOGY, id="missing-filing-date"),
        pytest.param(
            _drop("primaryDocument"), MISSING_CHRONOLOGY, id="missing-primary-column",
        ),
        pytest.param(
            _drop("acceptanceDateTime"), MISSING_CHRONOLOGY, id="missing-acceptance",
        ),
        pytest.param(_invalid_accession, BAD_ACCESSIONS, id="invalid-accession"),
        pytest.param(_duplicate_accessions, BAD_ACCESSIONS, id="duplicate-accession"),
        pytest.param(_bad_filing_date, MALFORMED_CHRONOLOGY, id="malformed-filing-date"),
        pytest.param(_bad_acceptance, MALFORMED_CHRONOLOGY, id="malformed-acceptance"),
        pytest.param(_not_newest_first, BAD_ORDER, id="not-newest-first"),
        pytest.param(
            _acceptance_not_newest_first, BAD_ORDER, id="acceptance-not-newest-first",
        ),
        pytest.param(_acceptance_date_mismatch, BAD_ORDER, id="acceptance-date-mismatch"),
        pytest.param(
            _unsupported_form, _unsupported(EIGHT_K), id="unsupported-post-boundary-form",
        ),
        pytest.param(
            _missing_primary, _unsupported(EIGHT_K), id="missing-primary-document",
        ),
    ],
)
def test_strict_freshness_get_json_locks_each_fault_message(sf, mutate, message):
    """pytest.raises ``match`` is a regex applied with ``re.search``.

    The pattern is anchored and escaped so a substring or a regex metacharacter
    cannot satisfy it, and the exception type must be exactly ``EdgarPipelineRunError``.
    """
    document = _clean_submissions()
    mutate(document["filings"]["recent"])
    fetcher, _audit, _baselines = _strict_fetcher(sf, document)

    with pytest.raises(
        EdgarPipelineRunError, match=f"^{re.escape(message)}$",
    ) as raised:
        fetcher.get_json(submissions_url(NVDA))

    assert type(raised.value) is EdgarPipelineRunError
    assert str(raised.value) == message


def test_clean_in_scope_8k_and_10q_returns_the_same_document_and_one_completion(sf, store):
    """A strict CIK with a saved boundary accepts a clean 8-K and 10-Q.

    ``get_json`` returns that same document. The boundary is then moved to the newest
    in-scope accession so the pipeline poll is caught up and does not download archive
    bytes; that poll records exactly one ``edgar_poll_completed`` row.
    """
    from sqlalchemy import func, select

    from schemas.models.audit_entry import AuditEntry

    document = _clean_submissions()
    fetcher, audit, baselines = _strict_fetcher(sf, document)
    returned = fetcher.get_json(submissions_url(NVDA))

    assert returned is document
    assert document["filings"]["recent"]["form"][:2] == ["8-K", "10-Q"]

    baselines.advance(NVDA, EIGHT_K, CLOCK.now())
    result = run_edgar_pipeline(
        sf,
        settings=Settings(
            sec_user_agent=UA,
            sec_rate_limit_per_sec=2.0,
            sec_max_new_filings_per_poll=25,
        ),
        ciks=[NVDA],
        raw_store=store,
        clock=CLOCK,
        fetcher=_StaticSubmissions(document),
        audit_log=audit,
        process_epoch="csl-236",
        strict_completeness_ciks={NVDA},
    )

    assert result.ok
    assert result.poll_errors == []
    assert result.filings_ingested == 0
    assert result.ciks_polled == 1
    with sf() as session:
        completions = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
        payload = session.execute(
            select(AuditEntry.payload).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
    assert completions == 1
    assert payload["cik"] == NVDA
    assert payload["baseline_accession"] == EIGHT_K
