"""W-23 cash-merger evidence, deterministic math, and fail-closed attacks."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select

from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from filings.raw_store import RawStore
from config.settings import DecisionRuntime, Mode, Settings
from core.clock import FrozenClock
from core.ids import new_id
from ingest.market_data import MarketDataService, MarketMetrics
from orchestrator.breakers import BreakerEngine
from orchestrator.state import (
    EVENT_EDGAR_POLL_COMPLETED,
    EVENT_FILING_INGESTED,
    OrchestratorState,
)
from orchestrator.quant_ws import SnapshotSource
from schemas.models.company import Company
from schemas.models.audit_entry import AuditEntry
from schemas.models.agent_job import AgentJob
from schemas.models.filing import Filing, FormType
from schemas.models.merger_edge import MergerDealEvidence, MergerEdgeEvidence
from schemas.models.proposal_record import ProposalRecord
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
from storage.parquet_store import write_snapshot
from tests.brokers.conftest import build_scenario
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
        "method": "wilson95_heldout_worst_case_prices_v2",
        "frozen": True,
        "frozen_at": NOW.isoformat(),
        "cohort_ref": ref,
        "cohort_content_hash": digest,
        "cohort_as_of": statistics["cohort_as_of"],
        "cohort_size": statistics["cohort_size"],
        "training_cutoff": statistics["training_cutoff"],
        "training_cohort_size": statistics["training_cohort_size"],
        "evaluation_cohort_size": statistics["evaluation_cohort_size"],
        "heldout_field_count": statistics["heldout_field_count"],
        "heldout_correct_fields": statistics["heldout_correct_fields"],
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


class _ResearchOnlyEdgeProvider(CashMergerEdgeProvider):
    """Test-only subclass for exercising controls below the closed production gate."""

    def _verify_semantic_authority(self, *, deal_document, calibration_document):
        assert deal_document["schema_version"] == DEAL_SCHEMA
        assert calibration_document["schema_version"] == CALIBRATION_SCHEMA


def _eligibility(**updates):
    values = {
        "cash_offer": "110",
        "security_type": "common_equity",
        "listing_country": "US",
        "listing_exchange": "nasdaq",
        "agreement_status": "definitive",
        "lifecycle_status": "active",
        "has_cvr": False,
        "has_earnout": False,
        "has_stock": False,
        "uses_options_or_leverage": False,
        "is_rumor_or_competing_bid": False,
        "amendment_reviewed": False,
    }
    values.update(updates)
    return values


def _cohort_rows(identity_offset=0):
    outcomes = ["closed"] * 38 + ["failed", "open"]
    prices = {"closed": "110", "failed": "50", "open": "98"}
    rows = []
    for index, outcome in enumerate(outcomes):
        identity = index + identity_offset
        selected = date(2024, 1, 1) if index < 10 else date(2025, 1, 1)
        cik = f"{1000000000 + identity:010d}"
        symbol = f"T{identity:03d}"
        placeholder = lambda offset: {  # noqa: E731 - compact deterministic fixture identity
            "event_seq": 10_000 + identity * 4 + offset,
            "raw_ref": f"placeholder-{identity}-{offset}",
            "content_hash": "a" * 64,
        }
        rows.append({
            "schema_version": "merger_calibration_cohort_row.v2",
            "deal_id": f"deal-{identity}",
            "symbol": symbol,
            "target_cik": cik,
            "source_accession": f"{cik}-25-{identity:06d}",
            "split": "training" if index < 10 else "evaluation",
            "selected_at": selected.isoformat(),
            "outcome_as_of": (
                selected + timedelta(days=30 if outcome == "open" else 14)
            ).isoformat(),
            "outcome": outcome,
            "entry_ask": "100",
            "outcome_price": prices[outcome],
            "eligibility_prediction": _eligibility(),
            "eligibility_label": _eligibility(),
            "eligibility_source": placeholder(0),
            "entry_price_source": placeholder(1),
            "outcome_source": placeholder(2),
            "outcome_price_source": placeholder(3),
        })
    return rows


def _seed_cohort_sources(st, root, rows):
    raw_store = RawStore(root / "sec", min_free_bytes=0)
    staged = []
    for index, row in enumerate(rows):
        accession = row["source_accession"]
        filing_ref = raw_store.write(accession, f"source filing {index}".encode())
        outcome_accession = f"{accession}-outcome"
        outcome_filing_ref = raw_store.write(
            outcome_accession, f"outcome filing {index}".encode()
        )
        selected = date.fromisoformat(row["selected_at"])
        outcome_as_of = date.fromisoformat(row["outcome_as_of"])
        entry_key = f"{row['symbol']}-cal-entry-{index}"
        outcome_key = f"{row['symbol']}-cal-outcome-{index}"
        entry_record = {
            "symbol": row["symbol"],
            "exchange_ts": f"{selected.isoformat()}T14:00:00+00:00",
            "price": row["entry_ask"],
            "ask": row["entry_ask"],
        }
        outcome_record = {
            "symbol": row["symbol"],
            "exchange_ts": f"{outcome_as_of.isoformat()}T20:00:00+00:00",
            "price": row["outcome_price"],
            "ask": row["outcome_price"],
        }
        _, entry_hash = write_snapshot(
            root / "market-data" / "quotes", selected.isoformat(), entry_key,
            [entry_record],
        )
        _, outcome_hash = write_snapshot(
            root / "market-data" / "quotes", outcome_as_of.isoformat(), outcome_key,
            [outcome_record],
        )
        staged.append((
            row, filing_ref.sha256, outcome_accession, outcome_filing_ref.sha256,
            entry_key, entry_hash, outcome_key, outcome_hash,
        ))
    with st.session_factory() as session, session.begin():
        for index, (
            row, filing_hash, outcome_accession, outcome_filing_hash,
            entry_key, entry_hash, outcome_key, outcome_hash,
        ) in enumerate(staged):
            # Historical imports retain honest local detection time. Calibration chronology
            # is derived from the SEC filing date signed into the event, not this timestamp.
            detection = NOW
            filing = Filing(
                id=f"cal-filing-{row['deal_id']}", cik=row["target_cik"],
                accession_no=row["source_accession"], form_type=FormType.FORM_8K,
                filing_date=date.fromisoformat(row["selected_at"]), detection_ts=detection,
                raw_ref=row["source_accession"], primary_doc=f"cal-{index}.htm",
            )
            session.add(filing)
            session.flush()
            filing_event = st.audit.append_in(
                session, EVENT_FILING_INGESTED, actor="edgar",
                refs={"model": "filing", "row_id": filing.id, "raw_ref": filing.raw_ref},
                payload={
                    "cik": filing.cik, "accession_no": filing.accession_no,
                    "form_type": "8-K", "filing_date": filing.filing_date.isoformat(),
                    "detection_ts": detection.isoformat(),
                    "raw_content_hash": filing_hash,
                },
            )
            outcome_detection = NOW
            outcome_filing = Filing(
                id=f"cal-outcome-filing-{row['deal_id']}", cik=row["target_cik"],
                accession_no=outcome_accession, form_type=FormType.FORM_8K,
                filing_date=date.fromisoformat(row["outcome_as_of"]),
                detection_ts=outcome_detection, raw_ref=outcome_accession,
                primary_doc=f"cal-outcome-{index}.htm",
            )
            session.add(outcome_filing)
            session.flush()
            outcome_filing_event = st.audit.append_in(
                session, EVENT_FILING_INGESTED, actor="edgar",
                refs={
                    "model": "filing", "row_id": outcome_filing.id,
                    "raw_ref": outcome_filing.raw_ref,
                },
                payload={
                    "cik": outcome_filing.cik,
                    "accession_no": outcome_filing.accession_no,
                    "form_type": "8-K",
                    "filing_date": outcome_filing.filing_date.isoformat(),
                    "detection_ts": outcome_detection.isoformat(),
                    "raw_content_hash": outcome_filing_hash,
                },
            )
            entry_day = row["selected_at"]
            entry_event = st.audit.append_in(
                session, "market_data_raw_snapshot", actor="market_data",
                refs={"raw_ref": f"market-data://{entry_day}/{entry_key}"},
                payload={
                    "kind": "quote", "date": entry_day, "raw_key": entry_key,
                    "content_hash": entry_hash, "count": 1,
                },
            )
            outcome_day = row["outcome_as_of"]
            outcome_event = st.audit.append_in(
                session, "market_data_raw_snapshot", actor="market_data",
                refs={"raw_ref": f"market-data://{outcome_day}/{outcome_key}"},
                payload={
                    "kind": "quote", "date": outcome_day, "raw_key": outcome_key,
                    "content_hash": outcome_hash, "count": 1,
                },
            )
            row["eligibility_source"] = {
                "event_seq": filing_event.seq, "raw_ref": filing.raw_ref,
                "content_hash": filing_hash,
            }
            row["entry_price_source"] = {
                "event_seq": entry_event.seq,
                "raw_ref": f"market-data://{entry_day}/{entry_key}",
                "content_hash": entry_hash,
            }
            row["outcome_source"] = {
                "event_seq": outcome_filing_event.seq,
                "raw_ref": outcome_filing.raw_ref,
                "content_hash": outcome_filing_hash,
            }
            row["outcome_price_source"] = {
                "event_seq": outcome_event.seq,
                "raw_ref": f"market-data://{outcome_day}/{outcome_key}",
                "content_hash": outcome_hash,
            }


def _composed_paper_service(merger):
    st, _root, _client, market_data, provider, _raw_hash = merger
    operator = "123456789012345678"
    st.settings = Settings(
        decision_runtime=DecisionRuntime.claude,
        operator_ids_config=operator,
        _env_file=None,
    )
    observation = market_data.observation(SYMBOL)
    verdict, _seq, _qty, _reasons = build_execution_verdict(
        _proposal(quantity=1), quote_price=Decimal(str(observation.quote["price"])),
        clock=st.clock, market_observation=observation,
        observation_fn=market_data.observation, metrics_fn=market_data.metrics,
        verify_params=market_data.verify_params, edge_provider=provider,
    )
    assert verdict is not None and verdict.net_edge_state == "sufficient"
    client = FakeAlpaca(cash="100000", buying_power="200000")
    broker = AlpacaPaperBroker(client=client)
    registry = TokenRegistry()
    gateway = OrderGateway(
        broker, st=st, token_registry=registry, execution_scope="paper"
    )
    ask = Decimal(str(observation.quote["ask"]))
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, qty="1",
        limit_price=str(ask), notional=str(ask),
        execution_verdict=verdict.as_dict(), mint=False,
    )
    job = AgentJob(
        id=new_id("job"), runtime="claude", model="deep", model_pinned="pinned",
        prompt_template="trade_proposal.v1", prompt_hash="h" * 16,
        input_refs={}, schema_id="TradeProposal", status="done", created_at=st.clock.now(),
    )
    st.audit.record_state_change(
        job, "agent_job", actor="agent_calls", payload={"status": "done", "runtime": "claude"}
    )
    payload = {
        **_proposal(quantity=1), "order_type": "limit", "sizing_basis": "capped",
        "notional": str(ask),
    }
    proposal = ProposalRecord(
        id="p1", symbol=SYMBOL, side="buy", status="draft", flagged=False,
        payload=payload, job_id=job.id, risk_decision_id=scenario.risk_decision.id,
        order_id=scenario.order.id, created_at=st.clock.now(), updated_at=st.clock.now(),
    )
    st.audit.record_state_change(
        proposal, "proposal", actor="proposals", payload={"status": "draft", "symbol": SYMBOL}
    )
    service = PaperExecutionService(
        st, broker=broker, gateway=gateway, token_registry=registry,
        quote_fn=market_data.quote, watchlist_fn=lambda: {SYMBOL},
        market_data=market_data, edge_provider=provider,
    )
    fresh, _snapshot = service._fresh_risk(  # noqa: SLF001 - composed test precondition
        payload, SYMBOL, order_qty=scenario.order.qty, require_w21=True
    )
    assert fresh.get("decision") == "approved", fresh
    return service, scenario, client, provider, operator


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
        filing_event = st.audit.append_in(
            session,
            EVENT_FILING_INGESTED,
            actor="edgar",
            refs={"model": "filing", "row_id": filing.id, "raw_ref": ACCESSION},
            payload={
                "cik": CIK,
                "accession_no": ACCESSION,
                "form_type": "8-K",
                "filing_date": filing.filing_date.isoformat(),
                "detection_ts": filing.detection_ts.isoformat(),
                "raw_content_hash": raw_ref.sha256,
            },
        )
        st.audit.append_in(
            session,
            EVENT_EDGAR_POLL_COMPLETED,
            actor="edgar-pipeline",
            refs={"cik": CIK},
            payload={
                "cik": CIK,
                "process_epoch": st.process_epoch,
                "completed_at": (NOW - timedelta(seconds=1)).isoformat(),
                "baseline_accession": ACCESSION,
                "cursor_accession": ACCESSION,
                "filing_event_seq": filing_event.seq,
            },
        )
    cohort_rows = _cohort_rows()
    _seed_cohort_sources(st, root, cohort_rows)
    cohort_ref, cohort_hash = write_calibration_cohort(
        root, (NOW.date() - timedelta(days=1)).isoformat(), "cash-merger-cohort-v1",
        cohort_rows,
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
    provider = _ResearchOnlyEdgeProvider(
        st, raw_root=root, strategy_watchlist={SYMBOL}
    )
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
    restarted = _ResearchOnlyEdgeProvider(
        st, raw_root=_root, strategy_watchlist={SYMBOL}
    )
    assert restarted.estimate(_proposal(), quote=quote) == first
    with st.session_factory() as session:
        assert len(session.execute(select(MergerEdgeEvidence)).scalars().all()) == 1


def test_production_semantic_gate_keeps_operator_authored_claims_unavailable(merger):
    st, root, _client, market_data, _provider, _raw_hash = merger
    production = CashMergerEdgeProvider(
        st, raw_root=root, strategy_watchlist={SYMBOL}
    )

    assert production.estimate(
        _proposal(), quote=market_data.quote(SYMBOL)
    ) is None
    assert "semantic verifier is not provisioned" in production.status_text()


def test_pre_restart_edgar_watermark_cannot_authorize_current_process(merger):
    st, _root, _client, market_data, provider, _raw_hash = merger
    st.process_epoch = "new-process-without-a-poll"

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "this process" in provider.status_text()


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
                "filing_date": filing.filing_date.isoformat(),
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
    rows = _cohort_rows(identity_offset=100)
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


def test_composed_paper_path_recomputes_real_signed_merger_evidence_twice(
    merger, monkeypatch,
):
    service, scenario, client, provider, operator = _composed_paper_service(merger)
    estimate = provider.estimate
    calls = 0

    def recording_estimate(*args, **kwargs):
        nonlocal calls
        calls += 1
        return estimate(*args, **kwargs)

    monkeypatch.setattr(provider, "estimate", recording_estimate)
    outcome = service.decide(
        scenario.order.id, actor=operator, decision="approve", confirm_symbol=SYMBOL,
        request_id="composed-w23-approved",
    )

    assert outcome["status"] == "submitted"
    assert calls >= 2, "approval and gateway must independently recompute the real provider"
    assert len(client.submitted) == 1


def test_late_filing_between_approval_and_gateway_recheck_makes_zero_broker_calls(
    merger, monkeypatch,
):
    service, scenario, client, provider, operator = _composed_paper_service(merger)
    st, root, _client, _market_data, _provider, _raw_hash = merger
    estimate = provider.estimate
    calls = 0

    def insert_before_gateway_recheck(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            accession = "0000320193-26-008888"
            raw = RawStore(root / "sec", min_free_bytes=0).write(
                accession, b"termination filed between approval and gateway"
            )
            with st.session_factory() as session, session.begin():
                filing = Filing(
                    id="gateway-race-filing", cik=CIK, accession_no=accession,
                    form_type=FormType.FORM_8K, filing_date=NOW.date(),
                    detection_ts=NOW, raw_ref=accession, primary_doc="termination.htm",
                )
                session.add(filing)
                session.flush()
                st.audit.append_in(
                    session, EVENT_FILING_INGESTED, actor="edgar",
                    refs={"model": "filing", "row_id": filing.id, "raw_ref": accession},
                    payload={
                        "cik": CIK, "accession_no": accession, "form_type": "8-K",
                        "filing_date": filing.filing_date.isoformat(),
                        "detection_ts": NOW.isoformat(), "raw_content_hash": raw.sha256,
                    },
                )
        return estimate(*args, **kwargs)

    monkeypatch.setattr(provider, "estimate", insert_before_gateway_recheck)
    with pytest.raises(PaperExecutionError):
        service.decide(
            scenario.order.id, actor=operator, decision="approve", confirm_symbol=SYMBOL,
            request_id="composed-w23-late-filing",
        )

    assert calls == 2
    assert client.submitted == []


@pytest.mark.parametrize("reason", [
    "unsupported post-boundary SEC filing 8-K/A blocks freshness",
    "malformed post-boundary SEC submissions row blocks freshness",
    "SEC submissions response CIK mismatches its request",
])
def test_composed_order_path_refuses_unclassified_sec_poll_fault(
    merger, reason,
):
    service, scenario, client, _provider, operator = _composed_paper_service(merger)
    st, _root, _client, _market_data, _provider, _raw_hash = merger
    BreakerEngine(st).enter_watch_only(
        "sec:edgar", reason, clock=st.clock, actor="edgar-pipeline"
    )

    with pytest.raises(PaperExecutionError):
        service.decide(
            scenario.order.id, actor=operator, decision="approve", confirm_symbol=SYMBOL,
            request_id=f"composed-w23-{reason[:9]}",
        )

    assert client.submitted == []


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


def test_signed_filing_arriving_after_deal_verification_blocks_final_edge_persistence(
    merger, monkeypatch,
):
    st, root, _client, market_data, provider, _raw_hash = merger
    verify_calibration = provider._verify_calibration  # noqa: SLF001 - interleaving attack
    inserted = False

    def insert_filing_before_calibration(now):
        nonlocal inserted
        if not inserted:
            inserted = True
            accession = "0000320193-26-009999"
            raw = RawStore(root / "sec", min_free_bytes=0).write(
                accession, b"late signed merger amendment"
            )
            with st.session_factory() as session, session.begin():
                filing = Filing(
                    id="late-race-filing", cik=CIK, accession_no=accession,
                    form_type=FormType.FORM_8K, filing_date=NOW.date(),
                    detection_ts=NOW, raw_ref=accession, primary_doc="late.htm",
                )
                session.add(filing)
                session.flush()
                st.audit.append_in(
                    session, EVENT_FILING_INGESTED, actor="edgar",
                    refs={"model": "filing", "row_id": filing.id, "raw_ref": accession},
                    payload={
                        "cik": CIK, "accession_no": accession, "form_type": "8-K",
                        "filing_date": filing.filing_date.isoformat(),
                        "detection_ts": NOW.isoformat(), "raw_content_hash": raw.sha256,
                    },
                )
        return verify_calibration(now)

    monkeypatch.setattr(provider, "_verify_calibration", insert_filing_before_calibration)

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "not covered" in provider.status_text() or "filing" in provider.status_text()
    with st.session_factory() as session:
        assert session.execute(select(MergerEdgeEvidence)).scalars().all() == []


def test_stale_edgar_watermark_enters_audited_watch_only(merger):
    st, _root, _client, market_data, provider, _raw_hash = merger
    st.clock._at = NOW + timedelta(minutes=11)  # noqa: SLF001 - deterministic stale-feed attack

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert BreakerEngine(st).is_watch_only("sec:edgar")
    with st.session_factory() as session:
        events = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "watch_only")
        ).scalars().all()
    assert any((event.payload or {}).get("vendor") == "sec:edgar" for event in events)


def test_advancing_clock_does_not_make_a_fresh_edge_future_dated(merger):
    st, _root, _client, market_data, provider, _raw_hash = merger

    class AdvancingClock:
        def __init__(self):
            self.at = NOW

        def now(self):
            self.at += timedelta(microseconds=1)
            return self.at

    st.clock = AdvancingClock()
    observation = market_data.observation(SYMBOL)
    verdict, _seq, _qty, _reasons = build_execution_verdict(
        _proposal(), quote_price=Decimal(str(observation.quote["price"])),
        clock=st.clock, market_observation=observation,
        observation_fn=market_data.observation, metrics_fn=market_data.metrics,
        verify_params=market_data.verify_params, edge_provider=provider,
    )

    assert verdict is not None and verdict.net_edge_state == "sufficient"


def test_calibration_precision_is_derived_from_labels_and_split_is_temporal():
    rows = _cohort_rows()
    evaluation = [row for row in rows if row["split"] == "evaluation"]
    for row in evaluation[:2]:
        row["eligibility_prediction"] = _eligibility(
            cash_offer="999", security_type="preferred", listing_country="CA",
            listing_exchange="otc", agreement_status="rumor", lifecycle_status="terminated",
            has_cvr=True, has_earnout=True, has_stock=True, uses_options_or_leverage=True,
            is_rumor_or_competing_bid=True, amendment_reviewed=True,
        )
    with pytest.raises(MergerEvidenceError, match="precision"):
        calibration_statistics(rows)

    rows = _cohort_rows()
    first_evaluation = next(row for row in rows if row["split"] == "evaluation")
    first_evaluation["selected_at"] = "2024-01-01"
    first_evaluation["outcome_as_of"] = "2024-01-15"
    with pytest.raises(MergerEvidenceError, match="training cutoff"):
        calibration_statistics(rows)


def test_calibration_rejects_reused_or_substituted_signed_source_events(merger):
    st, root, _client, _market_data, _provider, _raw_hash = merger
    rows = _cohort_rows(identity_offset=100)
    _seed_cohort_sources(st, root, rows)
    rows[1]["entry_price_source"] = dict(rows[0]["entry_price_source"])
    ref, digest = write_calibration_cohort(
        root, "2026-07-05", "source-substitution", rows
    )
    with pytest.raises(MergerEvidenceError, match="unique per observation"):
        record_calibration_evidence(
            st, _calibration(ref, digest), raw_root=root
        )


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
