"""WP3 acceptance tests — ``edgar/submissions_poll.py``: the per-CIK submissions poller.

In-scope invariants: 4 (all SEC traffic through the fetcher; halts on fair-access) and
11 (idempotent ingestion: cursor + UNIQUE accession -> zero duplicates; cursor survives
restart). Every SEC read is served from the recorded fixtures through an injected
transport — ZERO live sec.gov calls (an autouse guard also blocks any live ``urlopen``).

Covers the WP3 ACCEPT lines that live at the poll layer:

* recorded-fixture replay ingests the fixture filings with zero live calls;
* double-replay of the same feed ingests ZERO duplicate accessions;
* the ``PollCursor`` survives a restart (a fresh poller reads it from the DB);
* per-item isolation — filings whose documents 404 are skipped, the batch is NOT aborted;
* a quiet day (empty recent) returns ``emitted == 0`` WITHOUT raising, distinct from a
  poll failure (transport error) which RAISES;
* a 403/429 on the submissions read OR a document download HALTS the poll (no swallow).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from core.clock import FrozenClock
from filings.edgar.fetch import (
    HttpResponse,
    SecFairAccessHalt,
    SecFetcher,
    SecTransportError,
    archive_document_url,
    submissions_url,
)
from filings.edgar.submissions_poll import (
    PollBaselineBoundaryMissing,
    PollBaselineStore,
    PollCursorStore,
    PollFilingLimitExceeded,
    SubmissionsPoller,
    iter_recent_filings,
)
from schemas.models.audit_entry import AuditEntry
from schemas.models.filing import Filing
from storage.audit import AuditLog
from storage.engine import create_all, make_engine, make_session_factory
from filings.raw_store import RawStore
from tests.filings._sec_replay import FixtureTransport, RaisingTransport, VirtualTime

UA = "Lucas Salvador lucassalvador987@gmail.com (personal research)"
NVDA = "0001045810"
AAPL = "0000320193"
BASELINE_AUDIT_KEY = b"wp3-baseline-test-key"

# The in-scope (10-K/10-Q/8-K) NVDA filings that HAVE a fixture document — the only ones
# the replay can actually ingest. Newest-first (recent[] order).
NVDA_FIXTURE_ACCESSIONS = {
    "0001045810-26-000060",  # 8-K, filed 2026-07-02 (newest)
    "0001045810-26-000056",  # 8-K, filed 2026-06-30
    "0001045810-26-000052",  # 10-Q, filed 2026-05-20
    "0001045810-25-000230",  # 10-Q, filed 2025-11-19
}
NVDA_NEWEST = "0001045810-26-000060"
AAPL_FIXTURE_ACCESSIONS = {"0000320193-26-000013", "0000320193-26-000006"}


@pytest.fixture(autouse=True)
def _no_live_sec(monkeypatch):
    import urllib.request

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("a live urllib.request.urlopen was attempted in a WP3 test")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def sf(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 'wp3.db').as_posix()}")
    create_all(eng)
    return make_session_factory(eng)


@pytest.fixture
def raw_store(tmp_path):
    return RawStore(tmp_path / "raw")


def make_fetcher(transport):
    vt = VirtualTime()
    return SecFetcher(
        user_agent=UA, rate_per_sec=2.0, transport=transport,
        monotonic=vt.monotonic, sleep=vt.sleep,
    )


def make_poller(
    transport,
    sf,
    raw_store,
    clock=None,
    *,
    baseline_on_first_poll=False,
    audit_log=None,
    max_new_filings_per_poll=250,
):
    effective_clock = clock or FrozenClock(datetime(2026, 7, 8, 9, 30, tzinfo=UTC))
    if baseline_on_first_poll and audit_log is None:
        audit_log = AuditLog(
            sf,
            hmac_key=BASELINE_AUDIT_KEY,
            clock=effective_clock,
        )
    return SubmissionsPoller(
        make_fetcher(transport), raw_store, sf,
        clock=effective_clock,
        baseline_on_first_poll=baseline_on_first_poll,
        audit_log=audit_log,
        max_new_filings_per_poll=max_new_filings_per_poll,
    )


def _accessions(result):
    return {f.accession_no for f in result.filings}


def _registry_with_new_rows(*rows):
    """Prefix recorded NVDA submissions with ``(accession, form, document)`` rows."""
    import json

    fixture = FixtureTransport()
    registry = dict(fixture.registry)
    payload = json.loads(registry[submissions_url(NVDA)][1])
    recent = payload["filings"]["recent"]
    for accession, form, document in reversed(rows):
        recent["accessionNumber"].insert(0, accession)
        recent["form"].insert(0, form)
        recent["filingDate"].insert(0, "2026-07-03")
        recent["reportDate"].insert(0, "2026-06-30")
        recent["primaryDocument"].insert(0, document)
    registry[submissions_url(NVDA)] = (200, json.dumps(payload).encode())
    return registry


# --------------------------------------------------------------------------------------
# iter_recent_filings — parallel-array zip + processing filter is data-driven
# --------------------------------------------------------------------------------------
def test_iter_recent_filings_zips_and_parses(sf):
    import json

    from tests.filings._sec_replay import FIXTURES

    subs = json.loads((FIXTURES / "NVDA" / "submissions.json").read_text())
    rows = list(iter_recent_filings(subs, NVDA))
    assert rows, "expected filings"
    # every row carries a padded cik, a real accession, a parsed date, a primary doc
    r = next(r for r in rows if r.accession == NVDA_NEWEST)
    assert r.cik == NVDA and r.form == "8-K"
    assert r.filing_date.isoformat() == "2026-07-02"
    assert r.primary_document == "nvda-20260628.htm"


# --------------------------------------------------------------------------------------
# ACCEPT: recorded-fixture replay ingests the fixture filings with zero live calls
# --------------------------------------------------------------------------------------
def test_replay_ingests_fixture_filings(sf, raw_store):
    transport = FixtureTransport()
    poller = make_poller(transport, sf, raw_store)
    result = poller.poll(NVDA)

    assert _accessions(result) == NVDA_FIXTURE_ACCESSIONS
    assert result.emitted == 4
    # raw bytes are stored write-once, byte-identical, keyed by accession
    for acc in NVDA_FIXTURE_ACCESSIONS:
        assert raw_store.exists(acc)
        assert raw_store.verify(acc) is True
    # Filing rows persisted with detection_ts + raw_ref pointer (not the body)
    with sf() as s:
        rows = s.execute(select(Filing)).scalars().all()
    assert {f.accession_no for f in rows} == NVDA_FIXTURE_ACCESSIONS
    assert all(f.detection_ts is not None and f.raw_ref == f.accession_no for f in rows)
    # every SEC read carried the declared User-Agent
    assert all(h["User-Agent"] == UA for _m, _u, h in transport.requests)


def test_second_registrant_format_also_ingests(sf, raw_store):
    poller = make_poller(FixtureTransport(), sf, raw_store)
    result = poller.poll(AAPL)
    assert _accessions(result) == AAPL_FIXTURE_ACCESSIONS


# --------------------------------------------------------------------------------------
# ACCEPT: per-item isolation — a doc that 404s is skipped, the batch is NOT aborted
# --------------------------------------------------------------------------------------
def test_live_baseline_is_durable_and_stops_before_historical_form_filtering(sf, raw_store):
    transport = FixtureTransport()
    poller = make_poller(transport, sf, raw_store, baseline_on_first_poll=True)

    assert poller.baseline_missing([NVDA]) == (NVDA,)
    baseline = PollBaselineStore(sf).load(NVDA)
    assert baseline is not None
    # The recorded feed starts with a Form 4. It must still be the boundary, otherwise
    # filtering before the comparison would download the historical 8-K immediately below.
    assert baseline.cutoff_accession_no == "0001197647-26-000005"
    assert baseline.cutoff_accession_no not in NVDA_FIXTURE_ACCESSIONS
    assert [url for _method, url, _headers in transport.requests] == [submissions_url(NVDA)]
    with sf() as s:
        assert s.execute(select(func.count()).select_from(Filing)).scalar_one() == 0
        events = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
    assert [event.event_type for event in events] == ["poll-baseline-established"]
    assert events[0].refs["row_id"] == baseline.id
    assert AuditLog(sf, hmac_key=BASELINE_AUDIT_KEY).verify_chain().ok is True

    # A new process sees the durable boundary and still makes no archive-document request.
    replay = make_poller(transport, sf, raw_store, baseline_on_first_poll=True).poll(NVDA)
    assert replay.emitted == 0
    assert replay.errors == []
    assert all(url == submissions_url(NVDA) for _method, url, _headers in transport.requests)
    with sf() as s:
        event_types = s.execute(
            select(AuditEntry.event_type).order_by(AuditEntry.seq)
        ).scalars().all()
    assert event_types == ["poll-baseline-established", "poll-baseline-touched"]


def test_baseline_ingests_only_a_new_filing_above_the_saved_boundary(sf, raw_store):
    seed = make_poller(FixtureTransport(), sf, raw_store, baseline_on_first_poll=True)
    assert seed.baseline_missing([NVDA]) == (NVDA,)

    new_accession = "0001045810-26-999999"
    document = "nvda-20260628.htm"
    registry = _registry_with_new_rows((new_accession, "8-K", document))
    source_url = archive_document_url(NVDA, NVDA_NEWEST, document)
    registry[archive_document_url(NVDA, new_accession, document)] = registry[source_url]
    transport = FixtureTransport(registry=registry)

    result = make_poller(transport, sf, raw_store, baseline_on_first_poll=True).poll(NVDA)
    assert _accessions(result) == {new_accession}
    assert raw_store.exists(new_accession)
    assert not raw_store.exists(NVDA_NEWEST)
    document_urls = [
        url for _method, url, _headers in transport.requests if url != submissions_url(NVDA)
    ]
    assert document_urls == [archive_document_url(NVDA, new_accession, document)]
    assert PollBaselineStore(sf).load(NVDA).cutoff_accession_no == new_accession
    with sf() as s:
        event_types = s.execute(
            select(AuditEntry.event_type).order_by(AuditEntry.seq)
        ).scalars().all()
    assert event_types == ["poll-baseline-established", "poll-baseline-advanced"]
    assert AuditLog(sf, hmac_key=BASELINE_AUDIT_KEY).verify_chain().ok is True


def test_baseline_frontier_never_skips_a_failed_new_filing(sf, raw_store):
    seed = make_poller(FixtureTransport(), sf, raw_store, baseline_on_first_poll=True)
    assert seed.baseline_missing([NVDA]) == (NVDA,)
    original_cutoff = PollBaselineStore(sf).load(NVDA).cutoff_accession_no

    success = "0001045810-26-999998"
    failed = "0001045810-26-999997"
    document = "nvda-20260628.htm"
    registry = _registry_with_new_rows(
        (success, "8-K", document),
        (failed, "8-K", "temporarily-missing.htm"),
    )
    source_url = archive_document_url(NVDA, NVDA_NEWEST, document)
    registry[archive_document_url(NVDA, success, document)] = registry[source_url]
    transport = FixtureTransport(registry=registry)

    first = make_poller(transport, sf, raw_store, baseline_on_first_poll=True).poll(NVDA)
    assert _accessions(first) == {success}
    assert [accession for accession, _error in first.errors] == [failed]
    assert PollBaselineStore(sf).load(NVDA).cutoff_accession_no == original_cutoff

    # The next poll has the missing document, and the failed filing remains before the
    # retained boundary rather than being silently skipped behind the successful one.
    failed_url = archive_document_url(NVDA, failed, "temporarily-missing.htm")
    registry[failed_url] = registry[source_url]
    retry = make_poller(transport, sf, raw_store, baseline_on_first_poll=True).poll(NVDA)
    assert _accessions(retry) == {failed}
    assert retry.errors == []
    assert PollBaselineStore(sf).load(NVDA).cutoff_accession_no == success


def test_missing_baseline_boundary_fails_closed_before_document_download(sf, raw_store):
    seed = make_poller(FixtureTransport(), sf, raw_store, baseline_on_first_poll=True)
    assert seed.baseline_missing([NVDA]) == (NVDA,)

    import json

    registry = dict(FixtureTransport().registry)
    replacement = {
        "cik": int(NVDA),
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-26-999996"],
                "form": ["8-K"],
                "filingDate": ["2026-07-03"],
                "reportDate": ["2026-06-30"],
                "primaryDocument": ["nvda-20260628.htm"],
            }
        },
    }
    registry[submissions_url(NVDA)] = (200, json.dumps(replacement).encode())
    transport = FixtureTransport(registry=registry)

    with pytest.raises(PollBaselineBoundaryMissing):
        make_poller(transport, sf, raw_store, baseline_on_first_poll=True).poll(NVDA)
    assert [url for _method, url, _headers in transport.requests] == [submissions_url(NVDA)]
    with sf() as s:
        assert s.execute(select(func.count()).select_from(Filing)).scalar_one() == 0


def test_live_baseline_does_not_reinitialize_a_legacy_cursor(sf, raw_store):
    clock = FrozenClock(datetime(2026, 7, 8, 9, 30, tzinfo=UTC))
    PollCursorStore(sf).advance(NVDA, NVDA_NEWEST, clock.now())
    transport = FixtureTransport()

    result = make_poller(
        transport, sf, raw_store, clock, baseline_on_first_poll=True
    ).baseline_missing([NVDA])
    assert result == ()
    assert PollBaselineStore(sf).load(NVDA) is None
    assert transport.requests == []


def test_baseline_mode_refuses_to_construct_without_an_audit_log(sf, raw_store):
    transport = FixtureTransport()
    with pytest.raises(ValueError, match="requires an injected audit_log"):
        SubmissionsPoller(
            make_fetcher(transport),
            raw_store,
            sf,
            baseline_on_first_poll=True,
        )
    assert transport.requests == []


def test_baseline_mutations_emit_establish_advance_and_touch_events(sf):
    clock = FrozenClock(datetime(2026, 7, 8, 9, 30, tzinfo=UTC))
    audit_log = AuditLog(sf, hmac_key=BASELINE_AUDIT_KEY, clock=clock)
    store = PollBaselineStore(sf, audit_log)

    assert store.establish(NVDA, "cutoff-1", clock.now()) is True
    store.advance(NVDA, "cutoff-2", clock.now())
    store.touch(NVDA, clock.now())

    with sf() as s:
        events = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
    assert [event.event_type for event in events] == [
        "poll-baseline-established",
        "poll-baseline-advanced",
        "poll-baseline-touched",
    ]
    assert events[1].payload == {
        "previous_cutoff_accession_no": "cutoff-1",
        "cutoff_accession_no": "cutoff-2",
        "_ts": events[1].created_at.isoformat(),
    }
    assert audit_log.verify_chain().ok is True


def test_baseline_state_rolls_back_when_atomic_audit_append_fails(sf):
    class FailingAudit:
        def append_in(self, *_args, **_kwargs):
            raise RuntimeError("audit unavailable")

    store = PollBaselineStore(sf, FailingAudit())
    now = datetime(2026, 7, 8, 9, 30, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        store.establish(NVDA, "cutoff", now)
    assert PollBaselineStore(sf).load(NVDA) is None


def test_new_filing_cap_fails_before_any_document_download(sf, raw_store):
    seed = make_poller(FixtureTransport(), sf, raw_store, baseline_on_first_poll=True)
    assert seed.baseline_missing([NVDA]) == (NVDA,)
    original_cutoff = PollBaselineStore(sf).load(NVDA).cutoff_accession_no

    registry = _registry_with_new_rows(
        ("0001045810-26-999991", "8-K", "one.htm"),
        ("0001045810-26-999992", "8-K", "two.htm"),
        ("0001045810-26-999993", "8-K", "three.htm"),
    )
    transport = FixtureTransport(registry=registry)
    poller = make_poller(
        transport,
        sf,
        raw_store,
        baseline_on_first_poll=True,
        max_new_filings_per_poll=2,
    )

    with pytest.raises(PollFilingLimitExceeded) as exc_info:
        poller.poll(NVDA)
    assert exc_info.value.observed == 3
    assert [url for _method, url, _headers in transport.requests] == [submissions_url(NVDA)]
    assert PollBaselineStore(sf).load(NVDA).cutoff_accession_no == original_cutoff
    with sf() as s:
        assert s.execute(select(func.count()).select_from(Filing)).scalar_one() == 0


def test_per_item_isolation_bad_items_do_not_abort_the_batch(sf, raw_store):
    # NVDA recent has many in-scope filings WITHOUT a fixture doc (they 404) alongside the
    # 4 that resolve. The batch must still emit the 4 and record the rest as skips.
    result = make_poller(FixtureTransport(), sf, raw_store).poll(NVDA)
    assert result.emitted == 4
    assert len(result.errors) > 0  # the non-fixture in-scope filings were isolated, not fatal


def test_retry_reuses_integrity_checked_raw_bytes_published_before_db_commit(sf, raw_store):
    transport = FixtureTransport()
    document_url = archive_document_url(
        NVDA, NVDA_NEWEST, "nvda-20260628.htm"
    )
    _status, body = transport.registry[document_url]
    raw_store.write(NVDA_NEWEST, body)

    # Simulate a crash after raw-directory publication but before the Filing transaction.
    result = make_poller(transport, sf, raw_store).poll(NVDA)
    assert NVDA_NEWEST in _accessions(result)
    assert all(accession != NVDA_NEWEST for accession, _error in result.errors)
    with sf() as s:
        assert s.execute(
            select(func.count()).select_from(Filing).where(
                Filing.accession_no == NVDA_NEWEST
            )
        ).scalar_one() == 1


def test_retry_rejects_a_preexisting_raw_body_that_disagrees_with_sec(sf, raw_store):
    raw_store.write(NVDA_NEWEST, b"not the SEC filing body")
    result = make_poller(FixtureTransport(), sf, raw_store).poll(NVDA)

    assert NVDA_NEWEST not in _accessions(result)
    assert any(accession == NVDA_NEWEST for accession, _error in result.errors)
    assert raw_store.resolve(NVDA_NEWEST) == b"not the SEC filing body"
    with sf() as s:
        assert s.execute(
            select(func.count()).select_from(Filing).where(
                Filing.accession_no == NVDA_NEWEST
            )
        ).scalar_one() == 0


# --------------------------------------------------------------------------------------
# ACCEPT: double-replay ingests ZERO duplicate accessions (Invariant 11)
# --------------------------------------------------------------------------------------
def test_double_replay_ingests_zero_duplicates(sf, raw_store):
    poller = make_poller(FixtureTransport(), sf, raw_store)
    first = poller.poll(NVDA)
    assert first.emitted == 4

    second = poller.poll(NVDA)  # replay the SAME feed
    assert second.emitted == 0
    assert second.duplicates >= 4

    with sf() as s:
        count = s.execute(select(func.count()).select_from(Filing)).scalar_one()
    assert count == 4  # no duplicate Filing rows
    # raw store still has exactly the 4 accession keys
    stored = {p.name for p in Path(raw_store._root).iterdir() if p.is_dir()}
    assert stored == NVDA_FIXTURE_ACCESSIONS


def test_replay_across_a_fresh_poller_still_dedups(sf, raw_store):
    # a fresh poller has an EMPTY in-memory dedup — persistence (DB) must carry idempotency
    make_poller(FixtureTransport(), sf, raw_store).poll(NVDA)
    fresh = make_poller(FixtureTransport(), sf, raw_store)  # new _Dedup, same DB + store
    result = fresh.poll(NVDA)
    assert result.emitted == 0  # still deduped via the persisted Filing.accession_no
    with sf() as s:
        assert s.execute(select(func.count()).select_from(Filing)).scalar_one() == 4


# --------------------------------------------------------------------------------------
# ACCEPT: PollCursor survives restart
# --------------------------------------------------------------------------------------
def test_pollcursor_persists_and_survives_restart(sf, raw_store):
    make_poller(FixtureTransport(), sf, raw_store).poll(NVDA)

    # a brand-new cursor store (simulating a process restart) reads the persisted row
    cur = PollCursorStore(sf).load(NVDA)
    assert cur is not None
    assert cur.cik == NVDA
    assert cur.last_accession_no == NVDA_NEWEST  # advanced to the newest EMITTED accession
    assert cur.last_polled_at is not None


def test_cursor_advances_only_from_emitted_items(sf, raw_store):
    # even though recent[] is full of Form-4s and non-fixture filings, the cursor lands on
    # an EMITTED accession, never on a skipped one.
    make_poller(FixtureTransport(), sf, raw_store).poll(NVDA)
    cur = PollCursorStore(sf).load(NVDA)
    assert cur.last_accession_no in NVDA_FIXTURE_ACCESSIONS


# --------------------------------------------------------------------------------------
# ACCEPT: quiet day (emitted 0, no raise) is DISTINCT from a poll failure (raises)
# --------------------------------------------------------------------------------------
def test_quiet_day_returns_empty_without_raising(sf, raw_store):
    import json

    empty_subs = json.dumps(
        {"cik": 1045810, "filings": {"recent": {k: [] for k in (
            "accessionNumber", "form", "filingDate", "primaryDocument")}}}
    ).encode()
    registry = {submissions_url(NVDA): (200, empty_subs)}
    result = make_poller(FixtureTransport(registry=registry), sf, raw_store).poll(NVDA)
    assert result.emitted == 0
    assert result.errors == []
    # a poll STILL happened — the cursor row is touched (survives restart), distinguishing
    # a quiet day from a run that never completed.
    assert PollCursorStore(sf).load(NVDA) is not None


def test_transport_failure_raises_not_empty(sf, raw_store):
    # a network failure on the submissions read RAISES a transport error — a throttled or
    # unreachable EDGAR must NEVER masquerade as a quiet day.
    poller = make_poller(RaisingTransport(), sf, raw_store)
    with pytest.raises(SecTransportError):
        poller.poll(NVDA)


# --------------------------------------------------------------------------------------
# ACCEPT: a 403/429 HALTS the poll (never swallowed) — on submissions OR on a document
# --------------------------------------------------------------------------------------
def test_fair_access_halt_on_submissions_propagates(sf, raw_store):
    from tests.filings._sec_replay import StatusTransport

    poller = make_poller(StatusTransport(429), sf, raw_store)
    with pytest.raises(SecFairAccessHalt):
        poller.poll(NVDA)


def test_fair_access_halt_on_document_download_propagates(sf, raw_store):
    # submissions read succeeds (real fixture) but a document download returns 403 — the
    # halt must propagate THROUGH the per-item isolation, not be swallowed as a skip.
    real = FixtureTransport()

    class HaltOnDocs:
        def __init__(self):
            self.requests = []

        def __call__(self, method, url, headers):
            self.requests.append((method, url, dict(headers)))
            if url == submissions_url(NVDA):
                status, body = real.registry[url]
                return HttpResponse(status=status, body=body, url=url)
            return HttpResponse(status=403, body=b"", url=url)  # every document -> 403

    poller = make_poller(HaltOnDocs(), sf, raw_store)
    with pytest.raises(SecFairAccessHalt):
        poller.poll(NVDA)
