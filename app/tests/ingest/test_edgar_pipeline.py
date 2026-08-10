"""RW-10 — the in-process EDGAR pipeline, driven end-to-end over recorded SEC fixtures.

This is the test that says the absorption actually WORKS rather than merely imports: real
recorded NVDA/AAPL filing bytes go in at one end and cited, persisted ``Delta`` rows come out
at the other, inside the cockpit's own database, with no network and no model call.

The last test in this file is the one that matters most for the project: it proves the rows
this pipeline writes are readable by ``ingest/edgar_brew.py`` — the hardened reader that
already survived six adversarial rounds — with nothing pointed anywhere new except the
cockpit's own DB. That is what makes the in-process path a drop-in for the cross-process one
instead of a parallel universe that still needs an adapter written for it.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from config.settings import Settings
from core.clock import FrozenClock
from filings.raw_store import RawStore
from ingest.edgar_pipeline import (
    EdgarPipelineResult,
    build_sec_fetcher,
    run_edgar_pipeline,
)
from storage.engine import create_all, make_engine, make_session_factory
from tests.filings._sec_replay import FixtureTransport, StatusTransport, VirtualTime

UA = "Lucas Guerra lucas@example.com (personal research)"
NVDA = "0001045810"
AAPL = "0000320193"
CLOCK = FrozenClock(datetime(2026, 7, 8, 9, 30, tzinfo=UTC))


@pytest.fixture(autouse=True)
def _no_live_sec(monkeypatch):
    """NOTHING in this file may reach sec.gov. Fixtures only — upstream's rule, kept."""
    import urllib.request

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("a live urllib.request.urlopen was attempted in a pipeline test")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def sf(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 'cockpit.db').as_posix()}")
    create_all(eng)
    return make_session_factory(eng)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cockpit.db"


@pytest.fixture
def store(tmp_path):
    return RawStore(tmp_path / "raw")


@pytest.fixture
def settings():
    # The recorded NVDA fixture holds 26 in-scope filings — one MORE than the production
    # default cap of 25, which is exactly the kind of thing you only learn by running it.
    # Raised here so the happy-path tests exercise the pipeline rather than the cap; the cap
    # gets its own tests below.
    return Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                    sec_max_new_filings_per_poll=250)


def make_fetcher(transport):
    from filings.edgar.fetch import SecFetcher

    vt = VirtualTime()
    return SecFetcher(user_agent=UA, rate_per_sec=2.0, transport=transport,
                      monotonic=vt.monotonic, sleep=vt.sleep)


def run(sf, store, settings, ciks=(NVDA,), transport=None):
    return run_edgar_pipeline(
        sf, settings=settings, ciks=list(ciks), raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(transport or FixtureTransport()),
    )


# ---------------------------------------------------------------------------------------
# unprovisioned: one dead feed, never a dead process
# ---------------------------------------------------------------------------------------
def test_no_user_agent_reports_unprovisioned_and_does_not_raise(sf, store):
    """SEC needs no API key, but it does need a declared UA. Missing one must degrade THIS
    feed only — the same per-feed posture alpaca/finnhub/finra already follow."""
    result = run_edgar_pipeline(
        sf, settings=Settings(), ciks=[NVDA], raw_store=store, clock=CLOCK,
    )
    assert result.provisioned is False
    assert result.ok is False
    assert "COCKPIT_SEC_USER_AGENT" in result.unprovisioned_reason
    assert "no API key" in result.unprovisioned_reason
    assert result.filings_ingested == 0


def test_build_sec_fetcher_returns_none_without_a_user_agent():
    assert build_sec_fetcher(Settings()) is None
    assert build_sec_fetcher(Settings(sec_user_agent="   ")) is None, "whitespace is not a UA"


def test_build_sec_fetcher_builds_one_when_provisioned(settings):
    assert build_sec_fetcher(settings, transport=FixtureTransport()) is not None


def test_an_empty_cik_list_is_a_clean_no_op(sf, store, settings):
    result = run(sf, store, settings, ciks=())
    assert result.ok and result.ciks_polled == 0 and result.filings_ingested == 0


# ---------------------------------------------------------------------------------------
# the pipeline itself
# ---------------------------------------------------------------------------------------
def test_pipeline_ingests_filings_and_persists_deltas(sf, store, settings):
    """THE end-to-end proof: recorded SEC bytes -> Filing rows -> cited Delta rows."""
    result = run(sf, store, settings)

    assert result.provisioned is True
    assert result.ciks_polled == 1
    assert result.filings_ingested > 0, "no filings ingested from the NVDA fixtures"
    assert result.deltas_persisted > 0, "filings ingested but no deltas computed"
    assert result.delta_errors == []
    assert result.quiet is False


def test_persisted_deltas_carry_a_resolvable_byte_anchor(sf, store, settings):
    """A delta whose snippet cannot be reproduced from the stored bytes is an unverifiable
    claim, which is the whole thing this pipeline exists NOT to produce."""
    from sqlalchemy import select

    from schemas.models.delta import Delta

    run(sf, store, settings)
    with sf() as s:
        deltas = s.execute(select(Delta)).scalars().all()
    assert deltas
    checked = 0
    for d in deltas[:25]:
        if not d.raw_ref or d.snippet is None:
            continue
        blob = store.resolve(d.raw_ref)
        assert blob[d.snippet_start_offset:d.snippet_end_offset].decode("utf-8") == d.snippet
        checked += 1
    assert checked > 0, "no delta carried a resolvable anchor to check"


def test_a_second_run_persists_no_duplicate_deltas(sf, store, settings):
    """IDEMPOTENCY. A scheduler retry, or a crash-and-restart, must not double the evidence
    the morning brief then reads."""
    first = run(sf, store, settings)
    second = run(sf, store, settings)

    assert first.deltas_persisted > 0
    assert second.deltas_persisted == 0, "a re-run duplicated deltas"
    assert second.filings_ingested == 0, "a re-run re-ingested filings"
    # NOT asserting second.quiet here, and the reason is worth stating: the recorded fixture
    # set deliberately holds documents for only a few accessions, so the poller records a
    # per-item 404 skip for the rest. Those skips are REAL — evidence SEC listed and we could
    # not fetch — so `quiet` is correctly False. `quiet` means "we reached SEC and there was
    # genuinely nothing new", which is a stronger claim than "we ingested nothing".
    assert second.poll_errors, "expected the fixture's unrecorded documents to be counted"


def test_quiet_means_nothing_new_not_merely_nothing_ingested():
    """The distinction that keeps a broken feed from reading like a calm market."""
    assert EdgarPipelineResult(ciks_polled=1).quiet is True
    assert EdgarPipelineResult(ciks_polled=1, filings_ingested=3).quiet is False
    assert EdgarPipelineResult(ciks_polled=1, poll_errors=[("x", "SecHttpError")]).quiet is False
    assert EdgarPipelineResult(ciks_polled=1, delta_errors=[("x", "ValueError")]).quiet is False
    assert EdgarPipelineResult(provisioned=False).quiet is False, "unprovisioned is not quiet"
    assert EdgarPipelineResult(ciks_polled=0).quiet is False, "polling nothing is not a quiet day"


def test_two_ciks_are_both_polled(sf, store, settings):
    result = run(sf, store, settings, ciks=(NVDA, AAPL))
    assert result.ciks_polled == 2
    assert result.filings_ingested > 0


def test_the_declared_user_agent_is_actually_sent(sf, store, settings):
    """SEC fair access: a suppressed UA is empirically a 403, and the UA is how they reach us."""
    transport = FixtureTransport()
    run(sf, store, settings, transport=transport)
    assert transport.requests, "no SEC request was made at all"
    for _method, _url, headers in transport.requests:
        assert headers.get("User-Agent") == UA


# ---------------------------------------------------------------------------------------
# failure isolation — a bad day must stay countable, never silent
# ---------------------------------------------------------------------------------------
def test_a_403_is_reported_not_swallowed(sf, store, settings):
    """A throttled or blocked SEC must never look like a quiet day."""
    result = run(sf, store, settings, transport=StatusTransport(403))
    assert result.filings_ingested == 0
    assert result.poll_errors, "a blocked feed recorded no error"
    assert result.quiet is False, "a blocked feed reported itself as a quiet day"
    assert result.ok is False


def test_a_fair_access_halt_STOPS_the_fan_out_rather_than_isolating_it(sf, store, settings):
    """NOT A ROBUSTNESS BUG — the one failure that must NOT be isolated.

    Upstream's ``poll_all`` docstring is explicit: "a fair-access halt stops the whole
    fan-out (fail-closed)". When SEC answers 403/429, continuing on to the next issuer is
    exactly the behaviour fair access forbids, so a blanket ``except`` in the runner would
    turn an upstream safety control into a politeness violation committed at speed. This
    pins that only the FIRST CIK is attempted.
    """
    transport = StatusTransport(403)
    result = run(sf, store, settings, ciks=(NVDA, AAPL), transport=transport)

    assert result.ciks_polled == 0, "a halted run recorded a successful poll"
    assert len(result.poll_errors) == 1, "the fan-out continued past a fair-access halt"
    requested_ciks = {url for _m, url, _h in transport.requests}
    assert not any(AAPL in u for u in requested_ciks), "polled the second CIK after a halt"


def test_the_new_filing_cap_is_a_per_cik_fault_that_does_not_abort_the_fan_out(sf, store):
    """The OTHER side of the fair-access coin: a per-issuer fault is isolated and counted.

    Both recorded issuers hold more than 25 in-scope filings, so a cap of 25 trips on each —
    which makes this a clean test of the isolation itself: the loop must ATTEMPT every CIK
    and record an error per CIK, rather than abandoning the fan-out at the first one (which
    is what a fair-access halt, and only a fair-access halt, is allowed to do).
    """
    low_cap = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                       sec_max_new_filings_per_poll=25)
    result = run(sf, store, low_cap, ciks=(NVDA, AAPL))

    assert len(result.poll_errors) == 2, "the fan-out stopped at the first capped issuer"
    assert all("PollFilingLimitExceeded" in reason for _c, reason in result.poll_errors)
    assert result.ok is False, "a capped run must not report itself as clean"
    assert result.quiet is False, "a capped run must not read as a quiet day"


def test_result_summary_names_every_outcome(sf, store, settings):
    result = run(sf, store, settings)
    summary = result.summary()
    for token in ("cik(s)", "filing(s)", "delta(s)", "baseline(s)", "error(s)"):
        assert token in summary


def test_unprovisioned_summary_says_so():
    r = EdgarPipelineResult(provisioned=False, unprovisioned_reason="set COCKPIT_SEC_USER_AGENT")
    assert "NOT PROVISIONED" in r.summary()


# ---------------------------------------------------------------------------------------
# THE SEAM: the hardened reader consumes what this pipeline writes
# ---------------------------------------------------------------------------------------
def test_the_existing_brew_reader_can_read_the_rows_this_pipeline_writes(
    tmp_path, settings, store
):
    """The in-process path is a DROP-IN for the cross-process one.

    ``ingest/edgar_brew.make_brew_fetch`` is ~1000 hardened lines that survived six
    adversarial rounds reading a FOREIGN SQLite. Because RW-10 rebased the brew's models onto
    the cockpit's own ``Base``, the identical reader — unchanged, still opening a read-only
    URI — now reads the cockpit's own database. That is what makes this an absorption rather
    than a rewrite, and it is why RW-10 did not have to touch that module at all.
    """
    from ingest.edgar_brew import make_brew_fetch

    db = tmp_path / "cockpit.db"
    eng = make_engine(f"sqlite:///{db.as_posix()}")
    create_all(eng)
    sf_local = make_session_factory(eng)

    result = run_edgar_pipeline(
        sf_local, settings=settings, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()),
    )
    assert result.deltas_persisted > 0

    fetch = make_brew_fetch(str(db))
    rows = fetch(None, 0.0)

    assert rows, "the hardened reader saw nothing the pipeline had just written"
    row = rows[0]
    for key in ("delta_id", "accession_no", "cik", "created_at"):
        assert key in row, f"reader row is missing {key}"


# ---------------------------------------------------------------------------------------
# BUG-053: first contact with an established CIK must baseline, not fault forever
# ---------------------------------------------------------------------------------------
def _audit_log(sf):
    from storage.audit import AuditLog

    return AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=CLOCK)


def test_first_contact_with_an_established_cik_baselines_instead_of_faulting(sf, store):
    """BUG-053: the recorded NVDA window holds 26 in-scope filings — one more than the
    production cap of 25. Before the fix, the first audited poll of any established issuer
    raised PollFilingLimitExceeded and did so again on EVERY subsequent run: the baseline
    machinery built for exactly this was dead wiring (baseline_on_first_poll defaulted
    False and nothing called baseline_missing). First contact must record the audited feed
    position, ingest nothing, and leave the next run quiet."""
    prod_cap = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                        sec_max_new_filings_per_poll=25)
    result = run_edgar_pipeline(
        sf, settings=prod_cap, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=_audit_log(sf),
    )
    assert result.poll_errors == [], "first contact must baseline, not trip the cap"
    assert result.ok is True
    assert result.filings_ingested == 0, "first contact records position; it never backfills"

    result2 = run_edgar_pipeline(
        sf, settings=prod_cap, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=_audit_log(sf),
    )
    assert result2.poll_errors == [] and result2.filings_ingested == 0
    assert result2.ok is True


def test_unaudited_runs_keep_the_visible_cap_fault(sf, store):
    """Without an audit log a production-shaped baseline may not be written (the store
    refuses unaudited baseline writes), so the cap fault stays — and stays VISIBLE as a
    per-CIK error rather than becoming a silent unaudited baseline."""
    low_cap = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                       sec_max_new_filings_per_poll=25)
    result = run(sf, store, low_cap)
    assert any("PollFilingLimitExceeded" in reason for _c, reason in result.poll_errors)
