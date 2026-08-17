"""W-21 execution-cost verification wired into the real proposal pipeline.

These tests prove that proposal drafting consumes injected L1 prices plus RW-14 ADV and
realized volatility, hands the deterministic verdict to risk, and audits the verdict as
evidence. Displayed depth is deliberately non-authoritative: liquidity participation is
measured against median daily dollar volume. Production has a W-23 expected-edge provider,
but absent immutable strategy evidence it fails closed before an Order row can exist.
"""
from __future__ import annotations

from decimal import Decimal

from config.settings import Mode
from ingest.market_data import MarketMetrics, MarketObservation
from orchestrator.mode_ladder import ModeLadder
from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import to_ns
from quant.verify import EdgeEstimate
from tests.workflows.conftest import NOW, FakeTransport, envelope
from tests.workflows.test_proposals import (
    HOLDINGS,
    WATCHLIST,
    _payloads,
    _grounding_body,
    _persist_event,
    _proposal_body,
    _quote_fn,
    _seed_snapshot,
)
from workflows.packets import build_packet
from workflows.proposals import draft_proposal


def _book(*, ask_depth: int = 800, seq: int = 7, bids=None, asks=None,
          ts: int | None = None) -> BookSnapshot:
    """A book around $100.00 on a penny grid, with non-authoritative depth."""
    if asks is None:
        asks = ((10_002, ask_depth),)
    if bids is None:
        bids = ((10_000, 800),)
    return BookSnapshot(
        symbol="AAPL", seq=seq, ts=to_ns(NOW) if ts is None else ts,
        bids=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=3) for p, q in bids),
        asks=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=1) for p, q in asks),
    )


_AUTO = object()


def _metric(*, adv: str = "100000000", volatility: float = 0.20) -> MarketMetrics:
    return MarketMetrics(
        symbol="AAPL", as_of=NOW.date(),
        median_daily_dollar_volume=Decimal(adv),
        realized_volatility=volatility, observations=60,
    )


def _edge(*, gross_bps: str = "1000") -> EdgeEstimate:
    return EdgeEstimate(
        expected_gross_return_bps=Decimal(gross_bps),
        time_horizon="short", source_id="test-strategy-v1",
        evidence_ref="strategy:test:1", computed_at_ns=to_ns(NOW) - 1_000_000_000,
        verified=True,
    )


def _draft(st, clock, tmp_path, *, quantity, notional=None, book_fn=None,
           quote_fn=_quote_fn, metrics_fn=_AUTO, expected_edge=_AUTO):
    if metrics_fn is _AUTO:
        metrics_fn = (lambda _s: _metric()) if book_fn is not None else None
    if expected_edge is _AUTO:
        expected_edge = _edge() if book_fn is not None else None
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(refs=[row.raw_ref], quantity=quantity, notional=notional)),
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    return draft_proposal(
        st, clock, event=row, packet=packet, transport=transport,
        parquet_root=tmp_path / "raw", quote_fn=quote_fn, book_fn=book_fn,
        metrics_fn=metrics_fn, expected_edge=expected_edge,
        holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
    )


def _check(outcome, name: str) -> dict:
    return next(c for c in outcome.risk_decision.checks if c["name"] == name)


def test_notional_quantity_is_resolved_once_from_quote_for_verdict_and_order(
    st, clock, tmp_path,
):
    """BUG-044: a $999 proposal became 10 shares in verification (book mid $99) but
    9 shares in the canonical order (quote $100). The risk fact shown to the operator must
    describe the exact tuple that could later be approved."""
    shifted_book = lambda _s: _book(  # noqa: E731 - tiny injected seam
        bids=((9_899, 800),), asks=((9_901, 800),),
    )
    outcome = _draft(
        st, clock, tmp_path, quantity=None, notional=999, book_fn=shifted_book,
    )

    assert outcome.risk_decision.decision == "approved"
    assert outcome.order is not None
    assert Decimal(str(outcome.order.qty)) == Decimal("9")
    assert outcome.risk_decision.execution_verdict["qty"] == 9


def test_rw14_production_caller_passes_now_ns_and_rejects_a_stale_book(st, clock, tmp_path):
    stale = _book(ts=to_ns(NOW) - 60_000_000_000)
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: stale)
    assert outcome.risk_decision.decision == "needs_human"
    assert outcome.risk_decision.execution_verdict["verdict"] == "insufficient_data"
    assert any("stale" in reason for reason in outcome.risk_decision.execution_verdict["reasons"])
    assert outcome.order is None


def test_rw14_quote_cache_fault_degrades_to_risk_rejection_without_aborting(st, clock, tmp_path):
    def unavailable(_symbol):
        raise RuntimeError("cache not ready")

    outcome = _draft(
        st, clock, tmp_path, quantity=10, quote_fn=unavailable, book_fn=lambda _s: None
    )

    assert outcome.risk_decision.decision == "rejected"
    assert not _check(outcome, "quote_sane")["passed"]
    assert outcome.order is None
    assert any("market quote unavailable" in reason for reason in outcome.reasons)


def test_fractional_explicit_quantity_fails_to_human_without_an_order(st, clock, tmp_path):
    outcome = _draft(st, clock, tmp_path, quantity=5.7, book_fn=None)

    assert outcome.status == "needs_human"
    assert outcome.order is None
    assert any("quantity unresolvable" in reason for reason in outcome.reasons)


# ---- ADV replaces displayed-depth participation -----------------------------------------------


def test_displayed_depth_does_not_change_the_authority_bearing_cost(st, clock, tmp_path):
    """Three displayed shares cannot block a ten-share order with valid ADV capacity."""
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=3))

    verdict = outcome.risk_decision.execution_verdict
    assert outcome.risk_decision.decision == "approved"
    assert verdict["adv_participation_rate"] == "0.000010"
    assert "fillable_now" not in verdict
    assert "participation_rate" not in verdict


def test_the_identical_legacy_proposal_is_approved_when_no_quant_seam_is_attached(
    st, clock, tmp_path,
):
    """Uncomposed unit callers retain the additive pre-W-21 behavior."""
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=None)

    assert outcome.risk_decision.decision == "approved"
    assert _check(outcome, "execution_cost_sane")["passed"]
    assert _check(outcome, "execution_quality")["passed"]


def test_adv_participation_above_ten_percent_is_hard_blocked(st, clock, tmp_path):
    outcome = _draft(
        st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=800),
        metrics_fn=lambda _s: _metric(adv="5000"),
    )

    assert outcome.risk_decision.decision == "rejected"
    assert outcome.order is None
    assert "execution_cost_sane" in outcome.risk_decision.failed_checks
    assert any("ADV participation" in reason for reason in outcome.reasons)


def test_a_marginal_proposal_goes_to_a_human_rather_than_through_or_away(st, clock, tmp_path):
    """A cost above the marginal threshold is routed to human review, not hard-blocked."""
    outcome = _draft(
        st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=30),
        metrics_fn=lambda _s: _metric(adv="1000000", volatility=0.40),
    )

    assert outcome.risk_decision.decision == "needs_human"
    assert "execution_quality" in outcome.risk_decision.warnings
    assert outcome.risk_decision.human_required is True
    assert _check(outcome, "execution_cost_sane")["passed"], "marginal must not hard-block"


# ---- fail-closed data availability -------------------------------------------------------------


def test_a_composed_book_fn_returning_none_fails_closed(st, clock, tmp_path):
    with_none = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: None)
    assert with_none.risk_decision.decision == "needs_human"
    assert with_none.order is None
    assert "execution_quality" in with_none.risk_decision.warnings
    assert "net_edge_available" in with_none.risk_decision.warnings


def test_a_one_sided_book_degrades_to_human_review_not_to_approval(st, clock, tmp_path):
    """Fail closed (Invariant 7): the book could not answer, which is not permission."""
    one_sided = _book(asks=())
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: one_sided)

    assert outcome.risk_decision.decision == "needs_human"
    assert "execution_quality" in outcome.risk_decision.warnings


# ---- evidence ---------------------------------------------------------------------------------------------


def test_the_verdict_is_audited_as_evidence_on_the_risk_decision(st, clock, tmp_path):
    """So a later reader can reconstruct WHY a decision went the way it did, rather than
    seeing only that it did."""
    _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=3))

    payloads = _payloads(st, "risk_decision")
    assert payloads, "the risk decision must be audited"
    verdicts = [p["execution_verdict"] for p in payloads if "execution_verdict" in p]
    assert verdicts, "the execution verdict must ride in the audit payload"
    recorded = verdicts[-1]
    assert recorded["verdict"] == "ok"
    assert recorded["book_seq"] == 7
    assert recorded["qty"] == 10
    assert recorded["adv_participation_rate"] == "0.000010"
    assert recorded["net_edge_state"] == "sufficient"
    assert recorded["expected_impact_bps"] is not None


def test_the_audit_chain_still_verifies_with_a_verdict_in_the_payload(st, clock, tmp_path):
    """The chain's canonical encoder has no ``default=`` — it fails closed on anything it
    cannot serialise. A Decimal or an Enum leaking into the payload would break the chain,
    which is why ``QuantVerdict.as_dict`` is JSON-native only."""
    _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=3))
    assert st.audit.verify_chain().ok


def test_no_verdict_key_is_written_when_no_book_was_consulted(st, clock, tmp_path):
    """An absent measurement must be absent from the record, not present-and-empty — a
    reader must be able to tell 'we did not look' from 'we looked and found nothing'."""
    _draft(st, clock, tmp_path, quantity=10, book_fn=None)

    payloads = _payloads(st, "risk_decision")
    assert payloads
    assert all("execution_verdict" not in p for p in payloads)


# ---- the seam ------------------------------------------------------------------------------------------------


def test_book_fn_is_injected_and_the_workflow_never_fetches(st, clock, tmp_path):
    """Same discipline as ``quote_fn``: no fetcher exists in the tree, and the workflow is
    not where one would go."""
    calls: list[str] = []

    def recording_book_fn(symbol):
        calls.append(symbol)
        return _book()

    _draft(st, clock, tmp_path, quantity=10, book_fn=recording_book_fn)
    # TWICE, deliberately, and only ever with the proposal's own symbol. The first read is
    # what the verdict is computed from; the second is taken at gate time so the Invariant 6
    # staleness check has two observations to compare. One read made that check `seq > seq`,
    # which is never true — see
    # test_a_book_that_moved_between_verification_and_the_gate_is_reported_stale.
    # The property this test exists for is unchanged: the seam is INJECTED, and nothing in
    # the workflow fetches a book itself.
    assert calls == ["AAPL", "AAPL"], "the seam is read twice, both times for the proposal's symbol"


def test_build_services_threads_the_book_seam_through_to_draft_proposal():
    """The registry is the established extension point; a seam that stops at the workflow
    boundary would never reach a scheduled run."""
    import inspect

    from workflows.services import build_services

    assert "book_fn" in inspect.signature(build_services).parameters
    source = inspect.getsource(build_services)
    assert "book_fn=book_fn" in source


def test_w21_build_services_threads_rw14_metrics_to_the_proposal_path():
    """A pure ADV model with no production caller would leave displayed depth in authority."""
    import inspect

    from workflows.services import build_services

    assert "metrics_fn" in inspect.signature(build_services).parameters
    source = inspect.getsource(build_services)
    assert "metrics_fn=metrics_fn" in source


def test_w23_builder_invokes_provider_with_resolved_quantity_and_exact_quote(st, clock):
    from workflows.proposals import build_execution_verdict

    seen = {}

    class Provider:
        def estimate(self, proposal, *, quote, market_metric):
            seen["proposal"] = proposal
            seen["quote"] = quote
            seen["metric"] = market_metric
            return _edge()

    quote = {"symbol": "AAPL", "price": "100", "ask": "100.02", "bid": "100"}
    metric = _metric()
    observation = MarketObservation(
        "AAPL", quote, _book(ask_depth=800), "market-data://day/key", "c" * 64
    )
    proposal = {
        "proposal_id": "p-w23", "symbol": "AAPL", "side": "buy",
        "quantity": None, "notional": "999", "time_horizon": "short",
    }
    verdict, _seq, qty, _reasons = build_execution_verdict(
        proposal,
        quote_price=Decimal("100"),
        clock=clock,
        market_observation=observation,
        observation_fn=lambda _s: observation,
        metrics_fn=lambda _s: metric,
        edge_provider=Provider(),
    )
    assert qty == 9 and seen["proposal"]["quantity"] == 9
    assert seen["proposal"]["notional"] is None
    assert seen["quote"] == quote and seen["metric"] is metric
    assert verdict is not None and verdict.net_edge_state == "sufficient"


def test_w21_rw14_metrics_are_consumed_and_unavailable_edge_creates_no_order(
    st, clock, tmp_path,
):
    """The composed leaf supplies real ADV/volatility, but edge intentionally has no source."""
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(refs=[row.raw_ref], quantity=10, notional=None)),
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    metric = MarketMetrics(
        symbol="AAPL", as_of=NOW.date(),
        median_daily_dollar_volume=Decimal("100000000"),
        realized_volatility=0.20, observations=60,
    )

    outcome = draft_proposal(
        st, clock, event=row, packet=packet, transport=transport,
        parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
        book_fn=lambda _s: _book(ask_depth=1), metrics_fn=lambda _s: metric,
        holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
    )

    assert outcome.risk_decision.execution_verdict["adv_participation_rate"] == "0.000010"
    assert outcome.risk_decision.execution_verdict["net_edge_state"] == "unavailable"
    assert outcome.risk_decision.decision == "needs_human"
    assert "net_edge_available" in outcome.risk_decision.warnings
    assert outcome.order is None


def test_the_default_verify_params_use_a_penny_grid(st, clock, tmp_path):
    """A verdict computed on the wrong tick scale misreads every price. The default is
    stated once and is overridable per call for finer-grid names."""
    from workflows.proposals import DEFAULT_VERIFY_PARAMS

    assert DEFAULT_VERIFY_PARAMS.scale.tick_size == Decimal("0.01")


# ---- the staleness control is actually reachable --------------------------------------------


def test_a_book_that_moved_between_verification_and_the_gate_is_reported_stale(st, clock,
                                                                               tmp_path):
    """INVARIANT 6, WIRED SHUT. ``_verdict_state`` calls a verdict stale when
    ``ctx.quant_book_seq > verdict.book_seq`` — the book the order would trade against has
    moved past the one the verdict describes.

    ``draft_proposal`` read the book ONCE and passed that same snapshot's seq as both sides
    of the comparison, so the condition was ``seq > seq``: never true, however far the book
    moved. A fail-closed control that cannot fire is worse than none, because
    ``/quant/status`` reports it as wired. The existing stale-verdict test passes because it
    hand-builds a RiskContext and never goes through this path.
    """
    seqs = iter([7, 99])          # verify against 7; the book has moved to 99 by the gate

    def moving_book(_symbol):
        return _book(ask_depth=800, seq=next(seqs, 99))

    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=moving_book)
    rd = outcome.risk_decision
    blob = " ".join(str(x) for x in (rd.warnings, rd.failed_checks, rd.checks))
    assert "stale" in blob.lower(), f"a book that advanced 7 -> 99 was not reported stale: {blob}"


def test_a_still_book_is_not_reported_stale(st, clock, tmp_path):
    """CONTROL. Re-reading the seam must not make every verdict stale — the common case is a
    book that has not moved, and crying wolf on it would make the control useless."""
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=800))
    rd = outcome.risk_decision
    blob = " ".join(str(x) for x in (rd.warnings, rd.failed_checks))
    assert "stale" not in blob.lower(), blob


def test_a_raising_book_fn_degrades_to_no_verdict_instead_of_killing_the_proposal(st, clock,
                                                                                  tmp_path):
    """The comment above this call says a failure here 'must never take down the proposal
    pipeline, because an advisory measurement that can halt the thing it advises on is a
    liability rather than a control' — and then there was no try/except. A book feed doing
    real I/O raises for ordinary reasons, and every one of them would have killed the draft.
    """
    def exploding_book(_symbol):
        raise RuntimeError("book feed went away mid-draft")

    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=exploding_book)
    assert outcome.risk_decision.decision == "needs_human", outcome.risk_decision
    assert outcome.order is None
    assert "execution_quality" in outcome.risk_decision.warnings
