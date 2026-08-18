"""W-24 prospective semantic authority and composed-path reachability."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bridge.witness import DiscordSemanticWitness
from config.settings import Settings
from core.clock import FrozenClock
from filings.raw_store import RawStore
from ingest.market_data import MarketDataService
from orchestrator.quant_ws import SnapshotSource
from orchestrator.state import (
    EVENT_EDGAR_POLL_COMPLETED,
    EVENT_FILING_INGESTED,
    OrchestratorState,
)
from schemas.models.audit_entry import AuditEntry
from schemas.models.company import Company
from schemas.models.filing import Filing, FormType
from schemas.models.merger_edge import MergerEdgeEvidence, MergerSelectionCommitment
from storage.engine import create_all, make_engine, make_session_factory
from storage.parquet_store import write_snapshot
from strategies.merger_edge import (
    CashMergerEdgeProvider,
    calibration_statistics,
    record_calibration_evidence,
    record_deal_evidence,
    write_calibration_cohort,
)
from strategies.merger_semantics import (
    CashMergerSemanticVerifier,
    SemanticEvidenceUnavailable,
    _combined_source_text,
    _derive_eligibility,
    record_selection_commitment,
)
from tests.strategies.test_merger_edge import (
    ACCESSION,
    CIK,
    SYMBOL,
    _Client,
    _calibration,
    _cohort_rows,
    _composed_paper_service,
    _deal,
    _proposal,
)
from tests.workflows.conftest import NOW


SELECTION_NOW = NOW - timedelta(days=45)


def _eligibility_text(cash_offer: str) -> bytes:
    return (
        "The registrant entered into a definitive merger. "
        f"Each share of common stock listed on Nasdaq will receive ${cash_offer} in cash per share."
    ).encode()


def _deal_text() -> bytes:
    expected = (NOW.date() + timedelta(days=15)).isoformat()
    outside = (NOW.date() + timedelta(days=20)).isoformat()
    return (
        "The registrant entered into a definitive merger. Each share of common stock listed "
        "on Nasdaq will receive $120 in cash per share. The closing is subject to shareholder "
        f"approval and regulatory clearance. Expected closing date: {expected}. "
        f"Outside date: {outside}."
    ).encode()


def _outcome_text(outcome: str) -> bytes:
    return {
        "closed": b"The merger has been completed.",
        "failed": b"The parties have terminated the merger agreement.",
        "open": b"The transaction remains pending.",
    }[outcome]


def _semantic_witness(clock):
    messages = {}

    def post(content):
        message = {
            "id": str(900_000 + len(messages)),
            "channel_id": "42",
            "timestamp": (clock.now() + timedelta(seconds=1)).isoformat(),
            "edited_timestamp": None,
            "author": {"id": "987654321", "bot": True},
            "content": content,
        }
        messages[message["id"]] = message
        return dict(message)

    witness = DiscordSemanticWitness(
        post_fn=post,
        fetch_fn=lambda message_id: dict(messages[message_id]),
        identity_fn=lambda: {"id": "987654321", "bot": True},
        channel_id=42,
    )
    return witness, messages


def _append_filing(st, *, root, row_id, cik, accession, filed_on, detected_at, raw):
    ref = RawStore(root / "sec", min_free_bytes=0).write(accession, raw)
    with st.session_factory() as session, session.begin():
        filing = Filing(
            id=row_id,
            cik=cik,
            accession_no=accession,
            form_type=FormType.FORM_8K,
            filing_date=filed_on,
            detection_ts=detected_at,
            raw_ref=accession,
            primary_doc=f"{row_id}.htm",
        )
        session.add(filing)
        session.flush()
        event = st.audit.append_in(
            session,
            EVENT_FILING_INGESTED,
            actor="edgar",
            refs={"model": "filing", "row_id": filing.id, "raw_ref": accession},
            payload={
                "cik": cik,
                "accession_no": accession,
                "form_type": "8-K",
                "filing_date": filed_on.isoformat(),
                "detection_ts": detected_at.isoformat(),
                "raw_content_hash": ref.sha256,
            },
        )
        return event.seq, ref.sha256


def _append_quote(st, *, root, row, key, observed_on, price, observed_at=None):
    record = {
        "symbol": row["symbol"],
        "exchange_ts": (
            observed_at
            if observed_at is not None
            else datetime.combine(observed_on, time(20), tzinfo=timezone.utc)
        ).isoformat(),
        "price": price,
        "ask": price,
    }
    _, digest = write_snapshot(
        root / "market-data" / "quotes", observed_on.isoformat(), key, [record]
    )
    with st.session_factory() as session, session.begin():
        event = st.audit.append_in(
            session,
            "market_data_raw_snapshot",
            actor="market_data",
            refs={"raw_ref": f"market-data://{observed_on.isoformat()}/{key}"},
            payload={
                "kind": "quote",
                "date": observed_on.isoformat(),
                "raw_key": key,
                "content_hash": digest,
                "count": 1,
            },
        )
        return {
            "event_seq": event.seq,
            "raw_ref": f"market-data://{observed_on.isoformat()}/{key}",
            "content_hash": digest,
        }


def _append_poll(
    st, *, cik, completed_at, filing_head, baseline_accession=None, cursor_accession=None
):
    with st.session_factory() as session, session.begin():
        return st.audit.append_in(
            session,
            EVENT_EDGAR_POLL_COMPLETED,
            actor="edgar-pipeline",
            refs={"cik": cik},
            payload={
                "cik": cik,
                "process_epoch": st.process_epoch,
                "completed_at": completed_at.isoformat(),
                "baseline_accession": baseline_accession,
                "cursor_accession": cursor_accession,
                "filing_event_seq": filing_head,
            },
        ).seq


def _state(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'semantic.db').as_posix()}")
    create_all(engine)
    clock = FrozenClock(SELECTION_NOW)
    st = OrchestratorState.build(
        make_session_factory(engine),
        hmac_key=b"w24-semantic-test",
        settings=Settings(_env_file=None),
        clock=clock,
    )
    return st, clock, tmp_path / "raw"


def _seed_selection(st, root, rows, *, evaluation_quote_age=timedelta(minutes=1)):
    for index, row in enumerate(rows):
        selected = datetime.fromisoformat(row["selected_at"]).date()
        eligibility_raw = _eligibility_text(row["eligibility_label"]["cash_offer"])
        if row["eligibility_label"]["has_stock"]:
            eligibility_raw += b" The transaction includes stock consideration."
        filing_seq, filing_hash = _append_filing(
            st,
            root=root,
            row_id=f"semantic-selection-{index}",
            cik=row["target_cik"],
            accession=row["source_accession"],
            filed_on=(
                selected - timedelta(days=10)
                if row["split"] == "evaluation"
                else selected
            ),
            detected_at=(
                SELECTION_NOW - timedelta(minutes=2)
                if row["split"] == "evaluation"
                else datetime.combine(selected, time(14), tzinfo=timezone.utc)
            ),
            raw=eligibility_raw,
        )
        row["eligibility_source"] = {
            "event_seq": filing_seq,
            "raw_ref": row["source_accession"],
            "content_hash": filing_hash,
        }
        row["entry_price_source"] = _append_quote(
            st,
            root=root,
            row=row,
            key=f"semantic-entry-{index}",
            observed_on=selected,
            price=row["entry_ask"],
            observed_at=(
                SELECTION_NOW - evaluation_quote_age
                if row["split"] == "evaluation"
                else None
            ),
        )
        if row["split"] == "evaluation":
            _append_poll(
                st,
                cik=row["target_cik"],
                completed_at=SELECTION_NOW - timedelta(seconds=1),
                filing_head=filing_seq,
                baseline_accession=row["source_accession"],
                cursor_accession=row["source_accession"],
            )


def _seed_outcomes(st, root, rows):
    for index, row in enumerate(rows):
        outcome_on = datetime.fromisoformat(row["outcome_as_of"]).date()
        outcome_accession = f"{row['source_accession']}-outcome"
        filing_seq, filing_hash = _append_filing(
            st,
            root=root,
            row_id=f"semantic-outcome-{index}",
            cik=row["target_cik"],
            accession=outcome_accession,
            filed_on=outcome_on,
            detected_at=datetime.combine(outcome_on, time(18), tzinfo=timezone.utc),
            raw=_outcome_text(row["outcome"]),
        )
        row["outcome_source"] = {
            "event_seq": filing_seq,
            "raw_ref": outcome_accession,
            "content_hash": filing_hash,
        }
        row["outcome_price_source"] = _append_quote(
            st,
            root=root,
            row=row,
            key=f"semantic-outcome-price-{index}",
            observed_on=outcome_on,
            price=row["outcome_price"],
        )
        if row["split"] == "evaluation":
            _append_poll(
                st,
                cik=row["target_cik"],
                completed_at=NOW - timedelta(seconds=1),
                filing_head=filing_seq,
                baseline_accession=outcome_accession,
                cursor_accession=outcome_accession,
            )


def _seed_current_deal(st, root):
    raw_seq, raw_hash = _append_filing(
        st,
        root=root,
        row_id="semantic-current-deal",
        cik=CIK,
        accession=ACCESSION,
        filed_on=NOW.date() - timedelta(days=10),
        detected_at=NOW - timedelta(days=10),
        raw=_deal_text(),
    )
    with st.session_factory() as session, session.begin():
        company = Company(
            id="semantic-current-company",
            ticker=SYMBOL,
            cik=CIK,
            name="Apple",
            active=True,
            created_at=NOW,
        )
        session.add(company)
        session.flush()
        st.audit.append_in(
            session,
            "edgar-company-attribution",
            actor="edgar-pipeline",
            refs={"row_id": company.id, "cik": CIK},
            payload={"action": "created", "ticker": SYMBOL, "cik": CIK},
        )
    _append_poll(
        st, cik=CIK, completed_at=NOW - timedelta(seconds=1), filing_head=raw_seq,
        baseline_accession=ACCESSION, cursor_accession=ACCESSION,
    )
    record_deal_evidence(st, _deal(raw_hash), raw_root=root)


def _semantic_system(
    tmp_path, *, mutate_before_selection=None, mutate_candidates=None,
    mutate_rows=None, mutate_system=None,
):
    st, clock, root = _state(tmp_path)
    rows = _cohort_rows()
    for index, row in enumerate(rows):
        if row["split"] == "evaluation":
            selected = SELECTION_NOW.date()
            row["selected_at"] = selected.isoformat()
            row["outcome_as_of"] = (
                selected + timedelta(days=30 if row["outcome"] == "open" else 14)
            ).isoformat()
    if mutate_before_selection is not None:
        mutate_before_selection(rows)
    _seed_selection(st, root, rows)
    witness, messages = _semantic_witness(clock)
    candidates = [_candidate(row) for row in rows if row["split"] == "evaluation"]
    if mutate_candidates is not None:
        mutate_candidates(st, root, rows, candidates)
    commitment = record_selection_commitment(
        st,
        candidates,
        training_cutoff="2024-01-01",
        raw_root=root,
        witness=witness,
    )

    clock._at = NOW  # noqa: SLF001 - deterministic prospective-horizon fixture
    _seed_outcomes(st, root, rows)
    if mutate_system is not None:
        mutate_system(st, root, rows)
    if mutate_rows is not None:
        mutate_rows(rows)
    ref, digest = write_calibration_cohort(
        root, NOW.date().isoformat(), "semantic-cohort", rows
    )
    calibration = _calibration(
        ref,
        digest,
        selection_manifest_id=commitment.id,
        frozen_at=NOW.isoformat(),
        **calibration_statistics(rows),
    )
    # _calibration computes statistics from its stock fixture; these prospective rows have the
    # same outcomes/predictions and therefore the same pinned statistics.
    record_calibration_evidence(st, calibration, raw_root=root)
    _seed_current_deal(st, root)

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
    verifier = CashMergerSemanticVerifier(st, raw_root=root, witness=witness)
    provider = CashMergerEdgeProvider(
        st,
        raw_root=root,
        strategy_watchlist={SYMBOL},
        semantic_verifier=verifier,
    )
    return st, root, client, market_data, provider, raw_hash_for(root), witness, messages


def raw_hash_for(root):
    return RawStore(root / "sec", min_free_bytes=0).stored_hash(ACCESSION)


def test_prospective_source_derived_semantics_reach_composed_paper_approval(tmp_path):
    merger = _semantic_system(tmp_path)
    st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger
    estimate = provider.estimate(_proposal(), quote=market_data.quote(SYMBOL))

    assert estimate is not None and estimate.verified
    with st.session_factory() as session:
        edge = session.execute(select(MergerEdgeEvidence)).scalar_one()
        commitment = session.execute(select(MergerSelectionCommitment)).scalar_one()
    assert edge.document["semantic_authority_hash"]
    assert len(commitment.document["deals"]) == 30

    service, scenario, broker, _provider, operator = _composed_paper_service(merger[:6])
    result = service.decide(
        scenario.order.id,
        actor=operator,
        decision="approve",
        confirm_symbol=SYMBOL,
        request_id="w24-semantic-composed",
    )
    assert result["status"] == "submitted"
    assert len(broker.submitted) == 1


def test_later_ordinary_filing_is_bound_without_replacing_semantic_source(tmp_path):
    def add_current_filing(st, root, rows, candidates):
        row = next(item for item in rows if item["split"] == "evaluation")
        candidate = next(item for item in candidates if item["deal_id"] == row["deal_id"])
        current = f"{row['source_accession']}-current"
        filing_seq, _digest = _append_filing(
            st,
            root=root,
            row_id="semantic-selection-current-filing",
            cik=row["target_cik"],
            accession=current,
            filed_on=SELECTION_NOW.date(),
            detected_at=SELECTION_NOW - timedelta(seconds=30),
            raw=b"The registrant filed its quarterly report. The transaction remains pending.",
        )
        _append_poll(
            st,
            cik=row["target_cik"],
            completed_at=SELECTION_NOW - timedelta(seconds=1),
            filing_head=filing_seq,
            baseline_accession=current,
            cursor_accession=current,
        )
        candidate["source_accessions"] = [row["source_accession"], current]
        candidate["current_accession"] = current

    merger = _semantic_system(tmp_path, mutate_candidates=add_current_filing)
    st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is not None
    with st.session_factory() as session:
        commitment = session.execute(select(MergerSelectionCommitment)).scalar_one()
    current_row = next(
        row for row in commitment.document["deals"]
        if len(row["source_accessions"]) == 2
    )
    assert current_row["source_accession"] == current_row["source_accessions"][0]
    assert current_row["current_accession"] == current_row["source_accessions"][-1]
    assert commitment.document["schema_version"] == "merger_selection_manifest.v2"


def test_dropped_committed_deal_fails_closed_even_when_w23_statistics_still_match(tmp_path):
    def replace_identity(rows):
        evaluation = next(row for row in rows if row["split"] == "evaluation")
        evaluation["deal_id"] = "post-outcome-substitution"

    merger = _semantic_system(tmp_path, mutate_rows=replace_identity)
    _st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "dropped or added" in provider.status_text()


def test_operator_label_cannot_override_deterministic_source_semantics(tmp_path):
    def forge_label(rows):
        evaluation = next(row for row in rows if row["split"] == "evaluation")
        evaluation["eligibility_label"] = {
            **evaluation["eligibility_label"], "cash_offer": "999"
        }

    merger = _semantic_system(tmp_path, mutate_rows=forge_label)
    _st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "label is not source-derived" in provider.status_text()


def test_source_derived_but_ineligible_deal_cannot_enter_probability_cohort(tmp_path):
    def add_mixed_consideration(rows):
        evaluation = next(row for row in rows if row["split"] == "evaluation")
        evaluation["eligibility_label"] = {
            **evaluation["eligibility_label"], "has_stock": True
        }
        evaluation["eligibility_prediction"] = dict(evaluation["eligibility_label"])

    merger = _semantic_system(tmp_path, mutate_before_selection=add_mixed_consideration)
    _st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "outside the pinned" in provider.status_text()


def test_deleted_or_rewritten_external_commitment_fails_closed(tmp_path):
    merger = _semantic_system(tmp_path)
    _st, _root, _client, market_data, provider, _raw_hash, _witness, messages = merger
    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is not None
    message = next(iter(messages.values()))
    message["content"] = "merger-selection-commitment " + "0" * 64

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "does not bind" in provider.status_text()


def test_conflicting_resolution_in_poll_complete_filing_universe_fails_closed(tmp_path):
    def add_conflict(st, root, rows):
        row = next(
            item for item in rows
            if item["split"] == "evaluation" and item["outcome"] == "closed"
        )
        filed_on = datetime.fromisoformat(row["outcome_as_of"]).date()
        filing_seq, _digest = _append_filing(
            st,
            root=root,
            row_id="semantic-conflicting-termination",
            cik=row["target_cik"],
            accession=f"{row['source_accession']}-conflict",
            filed_on=filed_on,
            detected_at=datetime.combine(filed_on, time(19), tzinfo=timezone.utc),
            raw=b"The parties have terminated the merger agreement.",
        )
        _append_poll(
            st, cik=row["target_cik"], completed_at=NOW, filing_head=filing_seq,
            baseline_accession=f"{row['source_accession']}-conflict",
            cursor_accession=f"{row['source_accession']}-conflict",
        )

    merger = _semantic_system(tmp_path, mutate_system=add_conflict)
    _st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "filing universe conflicts" in provider.status_text()


def test_unsigned_domain_row_cannot_hide_outside_signed_filing_universe(tmp_path):
    def insert_unsigned(st, _root, rows):
        row = next(item for item in rows if item["split"] == "evaluation")
        filed_on = datetime.fromisoformat(row["outcome_as_of"]).date()
        with st.session_factory() as session, session.begin():
            session.add(Filing(
                id="semantic-unsigned-domain-row",
                cik=row["target_cik"],
                accession_no=f"{row['source_accession']}-unsigned",
                form_type=FormType.FORM_8K,
                filing_date=filed_on,
                detection_ts=NOW,
                raw_ref=f"{row['source_accession']}-unsigned",
                primary_doc="unsigned.htm",
            ))

    merger = _semantic_system(tmp_path, mutate_system=insert_unsigned)
    _st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "unsigned or conflicting" in provider.status_text()


def test_mixed_cash_and_stock_language_is_never_classified_cash_only():
    text = (
        "entered into a definitive merger. each share of common stock listed on nasdaq "
        "will receive $10 in cash per share plus 0.5 shares of buyer common stock"
    )
    assert _derive_eligibility(text, amended=False)["has_stock"] is True


def test_negated_or_nonbinding_language_cannot_satisfy_definitive_agreement():
    text = (
        "the issuer has not entered into a definitive merger. each share of common stock "
        "listed on nasdaq will receive $10 in cash per share under a non-binding proposal"
    )
    with pytest.raises(SemanticEvidenceUnavailable, match="definitive"):
        _derive_eligibility(text, amended=False)


def test_negated_agreement_and_plan_language_cannot_satisfy_definitive_agreement():
    text = (
        "the issuer has not entered into a definitive agreement and plan of merger. "
        "each share of common stock listed on nasdaq will receive $10 in cash per share"
    )
    with pytest.raises(SemanticEvidenceUnavailable, match="definitive"):
        _derive_eligibility(text, amended=False)


def test_real_crnx_agreement_and_cancellation_grammar_is_eligible():
    # BUG-109: minimal frozen grammar from CRNX accession 0001140361-26-027642.  The old
    # matcher stopped at the decimal par value and missed both the cancellation/conversion
    # ordering and the standard Agreement and Plan of Merger assertion.
    text = (
        "item 1.01 entry into a material definitive agreement. the company entered into an "
        "agreement and plan of merger. each share of common stock of the company, par value "
        "$0.001 per share, issued and outstanding immediately prior to the effective time, "
        "other than excluded shares, will be canceled and automatically converted into the "
        "right to receive $85.00 per share in cash, without interest. the common stock is "
        "listed on nasdaq."
    )

    assert _derive_eligibility(text, amended=False) == {
        "cash_offer": "85.00",
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


def test_negated_plain_agreement_and_plan_language_remains_refused():
    text = (
        "the issuer has not entered into an agreement and plan of merger. each share of "
        "common stock, par value $0.001 per share, issued and outstanding will be canceled "
        "and automatically converted into the right to receive $85 per share in cash. "
        "the common stock is listed on nasdaq."
    )

    with pytest.raises(SemanticEvidenceUnavailable, match="definitive"):
        _derive_eligibility(text, amended=False)


def test_never_entered_plain_agreement_and_plan_language_remains_refused():
    text = (
        "the issuer has never entered into an agreement and plan of merger. each share of "
        "common stock listed on nasdaq will receive $85 in cash per share."
    )

    with pytest.raises(SemanticEvidenceUnavailable, match="definitive"):
        _derive_eligibility(text, amended=False)


def test_decimal_par_value_does_not_allow_cross_sentence_cash_synthesis():
    text = (
        "the issuer entered into an agreement and plan of merger. each share of common stock, "
        "par value $0.001 per share, is issued and outstanding. employee options will be "
        "canceled and automatically converted into the right to receive $85 per share in cash. "
        "the common stock is listed on nasdaq."
    )

    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(text, amended=False)


def test_real_grammar_cannot_misattribute_same_sentence_option_payout():
    text = (
        "the issuer entered into an agreement and plan of merger. each share of common stock, "
        "par value $0.001 per share, issued and outstanding, is unaffected while employee "
        "options will be canceled and automatically converted into the right to receive $85 "
        "per share in cash. the common stock is listed on nasdaq."
    )

    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(text, amended=False)


@pytest.mark.parametrize(
    "foreign_subject",
    [
        "each share of preferred stock",
        "notes",
    ],
)
def test_real_grammar_cannot_misattribute_other_security_payout(foreign_subject):
    text = (
        "the issuer entered into an agreement and plan of merger. each share of common stock, "
        "par value $0.001 per share, remains outstanding and "
        f"{foreign_subject} will receive $85 in cash per share. "
        "the common stock is listed on nasdaq."
    )

    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(text, amended=False)


def test_real_conversion_grammar_without_explicit_cash_remains_refused():
    text = (
        "the issuer entered into an agreement and plan of merger. each share of common stock, "
        "par value $0.001 per share, issued and outstanding will be canceled and automatically "
        "converted into the right to receive $85 per share. the common stock is listed on nasdaq."
    )

    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(text, amended=False)


def test_unrelated_cash_per_share_language_cannot_define_target_consideration():
    text = (
        "the issuer entered into a definitive merger involving common stock listed on nasdaq. "
        "employee options will receive $10 in cash per share."
    )
    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(text, amended=False)


def test_written_fraction_stock_consideration_is_not_classified_cash_only():
    text = (
        "the issuer entered into a definitive merger. each share of common stock listed on "
        "nasdaq will receive $10 in cash per share plus one-half of a share of buyer common stock"
    )
    assert _derive_eligibility(text, amended=False)["has_stock"] is True


def test_separate_filings_cannot_synthesize_cash_consideration_language():
    fragments = [
        "the issuer entered into a definitive merger. each share of common stock listed "
        "on nasdaq will receive",
        "$10 in cash per share",
    ]
    assert _derive_eligibility(" ".join(fragments), amended=False)["cash_offer"] == "10"
    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(_combined_source_text(fragments), amended=False)


def test_conditional_termination_boilerplate_is_not_an_observed_failure():
    from strategies.merger_semantics import _derive_outcome

    with pytest.raises(SemanticEvidenceUnavailable, match="absent or semantically ambiguous"):
        _derive_outcome(
            "if the merger agreement has been terminated, the company may owe a fee"
        )


def test_stale_same_day_entry_quote_is_refused_before_witness_publication(tmp_path):
    st, clock, root = _state(tmp_path)
    rows = _cohort_rows()
    evaluation = [row for row in rows if row["split"] == "evaluation"]
    for row in evaluation:
        row["selected_at"] = SELECTION_NOW.date().isoformat()
    _seed_selection(
        st, root, rows, evaluation_quote_age=MAX_SELECTION_STALE_QUOTE_AGE
    )
    witness, messages = _semantic_witness(clock)
    candidates = [_candidate(row) for row in evaluation]

    with pytest.raises(SemanticEvidenceUnavailable, match="fresh point-in-time"):
        record_selection_commitment(
            st,
            candidates,
            training_cutoff="2024-01-01",
            raw_root=root,
            witness=witness,
        )
    assert messages == {}


def test_retrospective_server_timestamp_is_refused_before_manifest_persistence(tmp_path):
    st, clock, root = _state(tmp_path)
    rows = _cohort_rows()
    evaluation = [row for row in rows if row["split"] == "evaluation"]
    for row in evaluation:
        row["selected_at"] = SELECTION_NOW.date().isoformat()
    _seed_selection(st, root, rows)
    witness, _messages = _semantic_witness(clock)
    original_post = witness._post  # noqa: SLF001 - adversarial server-receipt fixture

    def retrospective(content):
        message = original_post(content)
        message["timestamp"] = NOW.isoformat()
        return message

    witness._post = retrospective  # noqa: SLF001
    candidates = [_candidate(row) for row in evaluation]

    with pytest.raises(SemanticEvidenceUnavailable, match="clock-skew"):
        record_selection_commitment(
            st,
            candidates,
            training_cutoff="2024-01-01",
            raw_root=root,
            witness=witness,
        )
    with st.session_factory() as session:
        assert session.execute(select(MergerSelectionCommitment)).scalars().all() == []
        assert not session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "merger_selection_commitment")
        ).scalars().all()


def test_later_selection_filing_cannot_be_hidden_behind_an_older_candidate_source(tmp_path):
    st, clock, root = _state(tmp_path)
    rows = _cohort_rows()
    evaluation = [row for row in rows if row["split"] == "evaluation"]
    for row in evaluation:
        row["selected_at"] = SELECTION_NOW.date().isoformat()
    _seed_selection(st, root, rows)
    target = evaluation[0]
    later_accession = f"{target['source_accession']}-later"
    filing_seq, _digest = _append_filing(
        st,
        root=root,
        row_id="semantic-later-selection-filing",
        cik=target["target_cik"],
        accession=later_accession,
        filed_on=SELECTION_NOW.date(),
        detected_at=SELECTION_NOW - timedelta(seconds=30),
        raw=b"The merger agreement was amended.",
    )
    _append_poll(
        st,
        cik=target["target_cik"],
        completed_at=SELECTION_NOW - timedelta(seconds=1),
        filing_head=filing_seq,
        baseline_accession=later_accession,
        cursor_accession=later_accession,
    )
    witness, messages = _semantic_witness(clock)
    candidates = [_candidate(row) for row in evaluation]

    with pytest.raises(SemanticEvidenceUnavailable, match="current accession"):
        record_selection_commitment(
            st,
            candidates,
            training_cutoff="2024-01-01",
            raw_root=root,
            witness=witness,
        )
    assert messages == {}, "invalid local authority must be rejected before Discord publication"


def test_selection_source_set_cannot_omit_an_intervening_signed_filing(tmp_path):
    st, clock, root = _state(tmp_path)
    rows = _cohort_rows()
    evaluation = [row for row in rows if row["split"] == "evaluation"]
    for row in evaluation:
        row["selected_at"] = SELECTION_NOW.date().isoformat()
    _seed_selection(st, root, rows)
    target = evaluation[0]
    candidate = _candidate(target)
    latest_seq = 0
    added = []
    for suffix in ("a-intermediate", "z-current"):
        accession = f"{target['source_accession']}-{suffix}"
        latest_seq, _digest = _append_filing(
            st,
            root=root,
            row_id=f"semantic-selection-{suffix}",
            cik=target["target_cik"],
            accession=accession,
            filed_on=SELECTION_NOW.date(),
            detected_at=SELECTION_NOW - timedelta(seconds=30),
            raw=b"The registrant filed an ordinary periodic report.",
        )
        added.append(accession)
    _append_poll(
        st,
        cik=target["target_cik"],
        completed_at=SELECTION_NOW - timedelta(seconds=1),
        filing_head=latest_seq,
        baseline_accession=added[-1],
        cursor_accession=added[-1],
    )
    candidate["source_accessions"] = [target["source_accession"], added[-1]]
    candidate["current_accession"] = added[-1]
    candidates = [candidate] + [_candidate(row) for row in evaluation[1:]]
    witness, messages = _semantic_witness(clock)

    with pytest.raises(SemanticEvidenceUnavailable, match="omits, adds, or reorders"):
        record_selection_commitment(
            st,
            candidates,
            training_cutoff="2024-01-01",
            raw_root=root,
            witness=witness,
        )
    assert messages == {}


def test_selection_source_set_cannot_reorder_signed_filings(tmp_path):
    st, clock, root = _state(tmp_path)
    rows = _cohort_rows()
    evaluation = [row for row in rows if row["split"] == "evaluation"]
    for row in evaluation:
        row["selected_at"] = SELECTION_NOW.date().isoformat()
    _seed_selection(st, root, rows)
    target = evaluation[0]
    added = []
    latest_seq = 0
    for offset, suffix in ((-2, "a"), (-1, "b"), (0, "current")):
        accession = f"{target['source_accession']}-{suffix}"
        latest_seq, _digest = _append_filing(
            st,
            root=root,
            row_id=f"semantic-selection-order-{suffix}",
            cik=target["target_cik"],
            accession=accession,
            filed_on=SELECTION_NOW.date() + timedelta(days=offset),
            detected_at=SELECTION_NOW - timedelta(seconds=30),
            raw=b"The registrant filed an ordinary current report.",
        )
        added.append(accession)
    _append_poll(
        st,
        cik=target["target_cik"],
        completed_at=SELECTION_NOW - timedelta(seconds=1),
        filing_head=latest_seq,
        baseline_accession=added[-1],
        cursor_accession=added[-1],
    )
    candidate = _candidate(target)
    candidate["source_accessions"] = [
        target["source_accession"], added[1], added[0], added[2]
    ]
    candidate["current_accession"] = added[2]
    candidates = [candidate] + [_candidate(row) for row in evaluation[1:]]
    witness, messages = _semantic_witness(clock)

    with pytest.raises(SemanticEvidenceUnavailable, match="omits, adds, or reorders"):
        record_selection_commitment(
            st,
            candidates,
            training_cutoff="2024-01-01",
            raw_root=root,
            witness=witness,
        )
    assert messages == {}


def test_legacy_selection_manifest_v1_fails_closed(tmp_path):
    merger = _semantic_system(tmp_path)
    st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger
    with st.session_factory() as session, session.begin():
        commitment = session.execute(select(MergerSelectionCommitment)).scalar_one()
        commitment.document = {
            **commitment.document,
            "schema_version": "merger_selection_manifest.v1",
        }

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "unsupported selection manifest" in provider.status_text()


def test_witnessed_source_set_tampering_fails_closed(tmp_path):
    merger = _semantic_system(tmp_path)
    st, _root, _client, market_data, provider, _raw_hash, _witness, _messages = merger
    with st.session_factory() as session, session.begin():
        commitment = session.execute(select(MergerSelectionCommitment)).scalar_one()
        changed = dict(commitment.document)
        changed["deals"] = [dict(row) for row in changed["deals"]]
        changed["deals"][0]["source_accessions"] = [
            *changed["deals"][0]["source_accessions"],
            "tampered-accession",
        ]
        changed["deals"][0]["current_accession"] = "tampered-accession"
        commitment.document = changed

    assert provider.estimate(_proposal(), quote=market_data.quote(SYMBOL)) is None
    assert "audit binding failed" in provider.status_text()


def test_duplicate_identity_cannot_be_hidden_by_manifest_dictionary_collapse(tmp_path):
    st, clock, root = _state(tmp_path)
    rows = _cohort_rows()
    evaluation = [row for row in rows if row["split"] == "evaluation"]
    for row in evaluation:
        row["selected_at"] = SELECTION_NOW.date().isoformat()
    _seed_selection(st, root, rows)
    witness, messages = _semantic_witness(clock)
    candidates = [_candidate(row) for row in evaluation]
    candidates[1]["deal_id"] = candidates[0]["deal_id"]

    with pytest.raises(SemanticEvidenceUnavailable, match="duplicate or incomplete"):
        record_selection_commitment(
            st,
            candidates,
            training_cutoff="2024-01-01",
            raw_root=root,
            witness=witness,
        )
    assert messages == {}, "invalid universe must be rejected before an external post"


_CANDIDATE_NAMES = (
    "deal_id", "symbol", "target_cik", "source_accession", "selected_at", "entry_ask",
    "eligibility_prediction", "eligibility_source", "entry_price_source",
)


def _candidate(row):
    return {
        **{name: row[name] for name in _CANDIDATE_NAMES},
        "source_accessions": [row["source_accession"]],
        "current_accession": row["source_accession"],
    }

MAX_SELECTION_STALE_QUOTE_AGE = timedelta(minutes=6)
