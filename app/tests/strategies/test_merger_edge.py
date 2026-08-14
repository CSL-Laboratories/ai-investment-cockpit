"""W-23 cash-merger evidence, deterministic math, and fail-closed attacks."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select

from filings.raw_store import RawStore
from config.settings import Settings
from core.clock import FrozenClock
from ingest.market_data import MarketDataService, MarketMetrics
from orchestrator.state import EVENT_FILING_INGESTED, OrchestratorState
from orchestrator.quant_ws import SnapshotSource
from schemas.models.company import Company
from schemas.models.audit_entry import AuditEntry
from schemas.models.filing import Filing, FormType
from schemas.models.merger_edge import MergerDealEvidence, MergerEdgeEvidence
from strategies.merger_edge import (
    CALIBRATION_SCHEMA,
    DEAL_SCHEMA,
    EVENT_DEAL,
    SOURCE_ID,
    CashMergerEdgeProvider,
    MergerEvidenceError,
    _canonical_hash,
    calibration_statistics,
    record_calibration_evidence,
    record_deal_evidence,
    write_calibration_cohort,
)
from storage.engine import create_all, make_engine, make_session_factory
from tests.workflows.conftest import NOW
from workflows.proposals import build_execution_verdict

SYMBOL = "AAPL"
CIK = "0000320193"
ACCESSION = "0000320193-26-000001"


@pytest.fixture
def clock():
    return FrozenClock(NOW)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'merger.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"w23-merger-test", settings=Settings(_env_file=None),
        clock=clock,
    )


class _Client:
    def __init__(self, clock):
        self.clock = clock
        self.ask = Decimal("100.01")
        self.quote_age = Decimal("1")

    def latest_quote(self, symbol):
        return {
            "symbol": symbol,
            "price": Decimal("100"),
            "spread_pct": Decimal("0.02"),
            "bid": self.ask - Decimal("0.02"),
            "ask": self.ask,
            "bid_size": 100,
            "ask_size": 100,
            "ts": (
                self.clock.now() - timedelta(seconds=float(self.quote_age))
            ).isoformat(),
            "feed": "iex",
        }

    def daily_bars(self, symbol, *, limit):
        assert symbol == SYMBOL and limit == 60
        return [
            {
                "t": (self.clock.now() - timedelta(days=30 - offset)).isoformat(),
                "c": str(99 + Decimal(offset) / Decimal("10")),
                "v": "1000000",
            }
            for offset in range(25)
        ]


def _deal(raw_hash, **updates):
    document = {
        "schema_version": DEAL_SCHEMA,
        "symbol": SYMBOL,
        "target_cik": CIK,
        "cash_offer": "120",
        "security_type": "common_equity",
        "listing_country": "US",
        "listing_exchange": "nasdaq",
        "announcement_accession": ACCESSION,
        "current_accession": ACCESSION,
        "agreement_status": "definitive",
        "lifecycle_status": "active",
        "conditions": ["shareholder approval", "regulatory clearance"],
        "announcement_at": (NOW - timedelta(days=10)).isoformat(),
        "observed_at": (NOW - timedelta(seconds=2)).isoformat(),
        "expected_resolution_date": (NOW.date() + timedelta(days=15)).isoformat(),
        "outside_date": (NOW.date() + timedelta(days=20)).isoformat(),
        "source_accessions": [ACCESSION],
        "filing_hashes": {ACCESSION: raw_hash},
        "has_cvr": False,
        "has_earnout": False,
        "has_stock": False,
        "uses_options_or_leverage": False,
        "is_rumor_or_competing_bid": False,
        "amendment_reviewed": False,
    }
    document.update(updates)
    return document


def _calibration(ref, digest, **updates):
    statistics = calibration_statistics(_cohort_rows())
    document = {
        "schema_version": CALIBRATION_SCHEMA,
        "version": SOURCE_ID,
        "method": "wilson95_worst_case_prices_v1",
        "frozen": True,
        "frozen_at": (NOW - timedelta(days=1)).isoformat(),
        "cohort_ref": ref,
        "cohort_content_hash": digest,
        "cohort_as_of": statistics["cohort_as_of"],
        "cohort_size": statistics["cohort_size"],
        "heldout_field_count": statistics["heldout_field_count"],
        "heldout_correct_fields": statistics["heldout_correct_fields"],
        "survivorship_safe": True,
        "includes_failed_delisted": True,
        "p_close": statistics["p_close"],
        "p_fail": statistics["p_fail"],
        "p_open": statistics["p_open"],
        "break_price_ratio": statistics["break_price_ratio"],
        "open_price_ratio": statistics["open_price_ratio"],
    }
    document.update(updates)
    return document


def _proposal(**updates):
    payload = {
        "proposal_id": "prop-merger-1",
        "symbol": SYMBOL,
        "side": "buy",
        "asset_type": "equity",
        "quantity": 10,
        "notional": None,
        "time_horizon": "long",
    }
    payload.update(updates)
    return payload


def _cohort_rows():
    outcomes = ["closed"] * 80 + ["failed"] * 10 + ["open"] * 10
    prices = {"closed": "110", "failed": "50", "open": "98"}
    return [
        {
            "schema_version": "merger_calibration_cohort_row.v1",
            "deal_id": f"deal-{index}",
            "selected_at": "2025-01-01",
            "outcome_as_of": (
                date(2025, 1, 31) if outcome == "open" else date(2025, 1, 15)
            ).isoformat(),
            "outcome": outcome,
            "entry_ask": "100",
            "outcome_price": prices[outcome],
            "heldout_fields": 1,
            "heldout_correct_fields": 1 if index < 97 else 0,
            "included_point_in_time": True,
            "failure_or_delisting_captured": outcome == "failed",
        }
        for index, outcome in enumerate(outcomes)
    ]


@pytest.fixture
def merger(st, clock, tmp_path):
    root = tmp_path / "raw"
    raw_store = RawStore(root / "sec", min_free_bytes=0)
    raw_ref = raw_store.write(ACCESSION, b"signed definitive merger filing")
    with st.session_factory() as session, session.begin():
        company = Company(
            id="company-aapl", ticker=SYMBOL, cik=CIK, name="Apple", active=True,
            created_at=NOW,
        )
        filing = Filing(
            id="filing-merger", cik=CIK, accession_no=ACCESSION,
            form_type=FormType.FORM_8K, filing_date=NOW.date() - timedelta(days=10),
            detection_ts=NOW - timedelta(days=10), raw_ref=ACCESSION,
            primary_doc="merger.htm",
        )
        session.add_all([company, filing])
        session.flush()
        st.audit.append_in(
            session,
            "edgar-company-attribution",
            actor="edgar-pipeline",
            refs={"row_id": company.id, "cik": CIK},
            payload={"action": "created", "ticker": SYMBOL, "cik": CIK},
        )
        st.audit.append_in(
            session,
            EVENT_FILING_INGESTED,
            actor="edgar",
            refs={"model": "filing", "row_id": filing.id, "raw_ref": ACCESSION},
            payload={
                "cik": CIK,
                "accession_no": ACCESSION,
                "form_type": "8-K",
                "detection_ts": filing.detection_ts.isoformat(),
                "raw_content_hash": raw_ref.sha256,
            },
        )
    cohort_ref, cohort_hash = write_calibration_cohort(
        root, (NOW.date() - timedelta(days=1)).isoformat(), "cash-merger-cohort-v1",
        _cohort_rows(),
    )
    record_calibration_evidence(st, _calibration(cohort_ref, cohort_hash), raw_root=root)
    record_deal_evidence(st, _deal(raw_ref.sha256), raw_root=root)

    client = _Client(clock)
    market_data = MarketDataService(
        client,
        symbols={SYMBOL},
        clock=clock,
        raw_root=root / "market-data",
        snapshot_source=SnapshotSource(),
        audit_log=st.audit,
    )
    st.market_data = market_data
    assert market_data.poll_quotes()["failed"] == []
    assert market_data.refresh_daily_bars()["failed"] == []
    provider = CashMergerEdgeProvider(st, raw_root=root, strategy_watchlist={SYMBOL})
    return st, root, client, market_data, provider, raw_ref.sha256


def test_verified_edge_uses_frozen_inputs_and_persists_immutable_evidence(merger):
    st, _root, _client, market_data, provider, _raw_hash = merger
    quote = market_data.quote(SYMBOL)
    first = provider.estimate(_proposal(), quote=quote)
    second = provider.estimate(_proposal(), quote=quote)

    ask = Decimal("100.01")
    statistics = calibration_statistics(_cohort_rows())
    expected = Decimal("10000") * (
        Decimal(statistics["p_close"]) * (Decimal("120") / ask - 1)
        + Decimal(statistics["p_fail"]) * (Decimal(statistics["break_price_ratio"]) - 1)
        + Decimal(statistics["p_open"]) * (Decimal(statistics["open_price_ratio"]) - 1)
    )
    assert first is not None and first.expected_gross_return_bps == expected
    assert second == first
    assert first.source_id == SOURCE_ID and first.time_horizon == "long" and first.verified
    assert provider.status_text().startswith(f"available — {SOURCE_ID}")
    with st.session_factory() as session:
        rows = session.execute(select(MergerEdgeEvidence)).scalars().all()
    assert len(rows) == 1
    assert rows[0].document["quote_content_hash"]
    assert rows[0].document["deal_evidence_id"].startswith("mdeal_")
    assert st.audit.verify_chain().ok

    # A reconstructed provider after a process restart reuses the same signed input record;
    # it does not mint a conflicting edge or fall back to mutable "latest row" state.
    restarted = CashMergerEdgeProvider(st, raw_root=_root, strategy_watchlist={SYMBOL})
    assert restarted.estimate(_proposal(), quote=quote) == first
    with st.session_factory() as session:
        assert len(session.execute(select(MergerEdgeEvidence)).scalars().all()) == 1


@pytest.mark.parametrize(("change", "reason"), [
    ({"side": "sell"}, "long-target buys"),
    ({"asset_type": "crypto"}, "common-equity proposals"),
    ({"time_horizon": "short"}, "horizon is not long"),
    ({"proposal_id": ""}, "proposal id is missing"),
    ({"expected_return_bps": "999999"}, "model-authored"),
])
def test_scope_and_model_authored_return_fields_fail_closed(merger, change, reason):
    _st, _root, _client, market_data, provider, _raw_hash = merger
    assert provider.estimate(_proposal(**change), quote=market_data.quote(SYMBOL)) is None
    assert provider.status_text().startswith(f"available — {SOURCE_ID}")
    assert reason in provider.status_text()


def test_general_watchlist_cannot_expand_strategy_authority(merger):
    st, root, _client, market_data, _provider, _raw_hash = merger
    provider = CashMergerEdgeProvider(st, raw_root=root, strategy_watchlist=set())
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "strategy watchlist is empty" in provider.status_text()


def test_unsigned_source_filing_cannot_bootstrap_deal_authority(st, tmp_path):
    root = tmp_path / "unsigned-raw"
    raw_ref = RawStore(root / "sec", min_free_bytes=0).write(
        ACCESSION, b"attacker-preseeded filing"
    )
    with st.session_factory() as session, session.begin():
        session.add(Company(
            id="unsigned-company", ticker=SYMBOL, cik=CIK, name=None, active=True,
            created_at=NOW,
        ))
        session.add(Filing(
            id="unsigned-filing", cik=CIK, accession_no=ACCESSION,
            form_type=FormType.FORM_8K, filing_date=NOW.date() - timedelta(days=10),
            detection_ts=NOW - timedelta(days=10), raw_ref=ACCESSION,
            primary_doc="attacker.htm",
        ))

    with pytest.raises(MergerEvidenceError, match="signed|provenance"):
        record_deal_evidence(st, _deal(raw_ref.sha256), raw_root=root)


def test_signed_deal_row_tamper_and_calibration_raw_tamper_fail_closed(merger):
    st, root, _client, market_data, provider, _raw_hash = merger
    with st.session_factory() as session, session.begin():
        row = session.execute(select(MergerDealEvidence)).scalar_one()
        row.document = {**row.document, "cash_offer": "999"}
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "hash binding" in provider.status_text()

    # Restore the row only to isolate the second negative control; the audit payload remains
    # the original authority and the exact original document re-verifies.
    with st.session_factory() as session, session.begin():
        row = session.execute(select(MergerDealEvidence)).scalar_one()
        row.document = {**row.document, "cash_offer": "120"}
    cohort_path = next((root / "strategy-calibration").rglob("*.parquet"))
    pq.write_table(pa.Table.from_pylist([{"forged": True}]), cohort_path)
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "cohort" in provider.status_text()


def test_quote_move_recomputes_and_termination_supersedes_prior_active_record(merger):
    st, root, client, market_data, provider, raw_hash = merger
    first = provider.estimate(_proposal(), quote=market_data.quote(SYMBOL))
    client.ask = Decimal("105.01")
    client.quote_age = Decimal("0.5")
    assert market_data.poll_quotes()["failed"] == []
    second = provider.estimate(_proposal(), quote=market_data.quote(SYMBOL))
    assert first is not None and second is not None
    assert first.expected_gross_return_bps != second.expected_gross_return_bps
    assert first.evidence_ref != second.evidence_ref

    record_deal_evidence(
        st,
        _deal(raw_hash, lifecycle_status="terminated", observed_at=NOW.isoformat()),
        raw_root=root,
    )
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "terminated" in provider.status_text()


def test_unreviewed_new_filing_and_quote_raw_substitution_fail_closed(merger):
    st, root, _client, market_data, provider, _raw_hash = merger
    quote = market_data.quote(SYMBOL)
    forged = {**quote, "symbol": "MSFT"}
    assert provider.estimate(_proposal(), quote=forged) is None
    assert "quote symbol mismatch" in provider.status_text()
    forged = {**quote, "ask": Decimal(str(quote["ask"])) + Decimal("0.01")}
    assert provider.estimate(_proposal(), quote=forged) is None
    assert "derived fields diverge" in provider.status_text()

    extra = "0000320193-26-000002"
    extra_ref = RawStore(root / "sec", min_free_bytes=0).write(extra, b"later amendment")
    with st.session_factory() as session, session.begin():
        filing = Filing(
            id="filing-later", cik=CIK, accession_no=extra, form_type=FormType.FORM_8K,
            filing_date=NOW.date(), detection_ts=NOW - timedelta(seconds=1), raw_ref=extra,
            primary_doc="later.htm",
        )
        session.add(filing)
        session.flush()
        st.audit.append_in(
            session,
            EVENT_FILING_INGESTED,
            actor="edgar",
            refs={"model": "filing", "row_id": filing.id, "raw_ref": extra},
            payload={
                "cik": CIK,
                "accession_no": extra,
                "form_type": "8-K",
                "detection_ts": filing.detection_ts.isoformat(),
                "raw_content_hash": extra_ref.sha256,
            },
        )
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "requires deal review" in provider.status_text()

    # Deleting the domain row cannot make authority fall back to the old reviewed accession;
    # the signed ingestion event remains and fails its row/raw binding.
    with st.session_factory() as session, session.begin():
        later = session.get(Filing, "filing-later")
        session.delete(later)
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "row/raw binding failed" in provider.status_text()


def test_cached_metric_substitution_cannot_replace_signed_daily_bars(merger):
    _st, _root, _client, market_data, provider, _raw_hash = merger
    original = market_data.metrics(SYMBOL)
    market_data._metrics[SYMBOL] = MarketMetrics(  # noqa: SLF001 - adversarial substitution
        symbol=SYMBOL,
        as_of=original.as_of,
        median_daily_dollar_volume=Decimal("999999999999"),
        realized_volatility=original.realized_volatility,
        observations=original.observations,
    )
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "diverge" in provider.status_text()


def test_cached_volatility_substitution_cannot_replace_signed_daily_bars(merger):
    _st, _root, _client, market_data, provider, _raw_hash = merger
    original = market_data.metrics(SYMBOL)
    market_data._metrics[SYMBOL] = MarketMetrics(  # noqa: SLF001 - adversarial substitution
        symbol=SYMBOL,
        as_of=original.as_of,
        median_daily_dollar_volume=original.median_daily_dollar_volume,
        realized_volatility=0.000001,
        observations=original.observations,
    )

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "diverge" in provider.status_text()


@pytest.mark.parametrize("horizon_days", [0, 1, 31])
def test_open_calibration_rows_require_the_exact_30_day_horizon(horizon_days):
    rows = _cohort_rows()
    selected = date(2025, 1, 1)
    for row in rows:
        if row["outcome"] == "open":
            row["selected_at"] = selected.isoformat()
            row["outcome_as_of"] = (selected + timedelta(days=horizon_days)).isoformat()

    with pytest.raises(MergerEvidenceError, match="30-day|strictly after"):
        calibration_statistics(rows)


def test_split_quote_and_book_seams_cannot_invoke_the_edge_provider(merger):
    st, _root, client, market_data, provider, _raw_hash = merger
    old_quote = market_data.quote(SYMBOL)
    client.ask = Decimal("139.99")
    client.quote_age = Decimal("0.5")
    assert market_data.poll_quotes()["failed"] == []

    verdict, _seq, _qty, _reasons = build_execution_verdict(
        _proposal(),
        quote_price=Decimal(str(old_quote["price"])),
        clock=st.clock,
        book_fn=market_data.book,
        metrics_fn=market_data.metrics,
        verify_params=market_data.verify_params,
        edge_provider=provider,
        quote=old_quote,
    )

    assert verdict is None


def test_atomic_market_observation_reaches_w21_with_the_verified_metric(merger):
    st, _root, _client, market_data, provider, _raw_hash = merger
    observation = market_data.observation(SYMBOL)

    verdict, seq, qty, reasons = build_execution_verdict(
        _proposal(),
        quote_price=Decimal(str(observation.quote["price"])),
        clock=st.clock,
        market_observation=observation,
        observation_fn=market_data.observation,
        metrics_fn=market_data.metrics,
        verify_params=market_data.verify_params,
        edge_provider=provider,
    )

    assert verdict is not None and verdict.net_edge_state == "sufficient"
    assert (seq, qty, reasons) == (observation.book.seq, 10, ())


def test_verified_snapshot_blocks_transient_audit_event_repoint(merger, monkeypatch):
    st, _root, _client, market_data, provider, _raw_hash = merger
    quote = market_data.quote(SYMBOL)
    baseline = provider.estimate(_proposal(), quote=quote)
    assert baseline is not None

    with st.session_factory() as session, session.begin():
        original = session.execute(select(MergerDealEvidence)).scalars().first()
        forged_document = {**original.document, "cash_offer": "999"}
        forged_hash = _canonical_hash(forged_document)
        forged = MergerDealEvidence(
            id=f"mdeal_{forged_hash}", symbol=SYMBOL, target_cik=CIK,
            document=forged_document, content_hash=forged_hash,
            observed_at=original.observed_at, created_at=original.created_at,
        )
        session.add(forged)
        event = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_DEAL)
        ).scalars().first()
        original_refs = dict(event.refs)
        original_payload = dict(event.payload)

    verify_chain = st.audit.verify_chain
    calls = 0

    def interleaved_verify():
        nonlocal calls
        if calls == 0:
            result = verify_chain()
            assert result.ok
            with st.session_factory() as session, session.begin():
                event = session.execute(
                    select(AuditEntry).where(AuditEntry.event_type == EVENT_DEAL)
                ).scalars().first()
                event.refs = {**original_refs, "row_id": forged.id}
                event.payload = {**original_payload, "content_hash": forged_hash}
            calls += 1
            return result
        if calls == 1:
            with st.session_factory() as session, session.begin():
                event = session.execute(
                    select(AuditEntry).where(AuditEntry.event_type == EVENT_DEAL)
                ).scalars().first()
                event.refs = original_refs
                event.payload = original_payload
            calls += 1
        return verify_chain()

    monkeypatch.setattr(st.audit, "verify_chain", interleaved_verify)
    attacked = provider.estimate(_proposal(), quote=quote)

    assert attacked is not None
    assert attacked.expected_gross_return_bps == baseline.expected_gross_return_bps
    assert calls == 0


def test_deal_superseded_during_estimate_cannot_persist_or_return_edge(merger, monkeypatch):
    st, root, _client, market_data, provider, raw_hash = merger
    verify_quote = provider._verify_quote  # noqa: SLF001 - deterministic interleaving attack

    def supersede_before_quote(symbol, quote, now):
        record_deal_evidence(
            st,
            _deal(raw_hash, lifecycle_status="terminated", observed_at=NOW.isoformat()),
            raw_root=root,
        )
        return verify_quote(symbol, quote, now)

    monkeypatch.setattr(provider, "_verify_quote", supersede_before_quote)

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    with st.session_factory() as session:
        assert session.execute(select(MergerEdgeEvidence)).scalars().all() == []


def test_calibration_rejects_weak_precision_and_non_empirical_probabilities(merger):
    st, root, _client, _market_data, _provider, _raw_hash = merger
    ref, digest = write_calibration_cohort(
        root, date(2026, 7, 6).isoformat(), "weak-cohort",
        [{"row": 1}],
    )
    with pytest.raises(ValueError, match="precision"):
        record_calibration_evidence(
            st, _calibration(ref, digest, heldout_correct_fields=94), raw_root=root
        )
    with pytest.raises(ValueError, match="sum exactly"):
        record_calibration_evidence(
            st, _calibration(ref, digest, p_close="0.9"), raw_root=root
        )
    proper_ref, proper_digest = write_calibration_cohort(
        root, date(2026, 7, 6).isoformat(), "mismatched-statistics", _cohort_rows()
    )
    with pytest.raises(ValueError, match="do not match"):
        record_calibration_evidence(
            st,
            _calibration(proper_ref, proper_digest, break_price_ratio="0.51"),
            raw_root=root,
        )
