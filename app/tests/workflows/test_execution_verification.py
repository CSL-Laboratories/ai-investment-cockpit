"""QT-05/QT-06 — execution verification wired into the REAL proposal pipeline.

The tier tests elsewhere prove the verifier computes correctly (``tests/quant/test_verify``)
and that the risk engine acts on a verdict (``tests/risk/test_execution_gate``). This file
proves the two are actually CONNECTED — that ``draft_proposal`` builds a verdict from the
injected book seam, hands it to ``evaluate``, and audits it as evidence. A correct verifier
with no caller changes nothing.

THE GAP THIS CLOSES, STATED PRECISELY
--------------------------------------
It is NOT "the cockpit would allow a huge order" — it would not. ``max_notional`` defaults to
$1,000 and catches size in dollars perfectly well.

The gap is that EVERY pre-existing check is denominated in DOLLARS, and none of them can see
LIQUIDITY. ``max_notional``, ``buying_power``, ``concentration``, ``pending_exposure`` — all
of them price the order and none of them ask whether the market can absorb it. So a $1,000
order in a name showing three shares at the touch passes every single check, because $1,000
is a perfectly reasonable amount of money and nothing in the tree knows what three shares
means.

That is the case exercised below, and it is a more interesting failure than a large order:
it is small, well-funded, well-diversified, and undeployable.

The properties pinned:

  * WITH A BOOK, a fully-funded proposal that exceeds displayed depth is REJECTED.
  * WITHOUT A BOOK, the identical proposal is APPROVED — which is exactly the pre-QT
    behaviour. The tree has no book feed, and an additive check that changed existing
    outcomes would be a regression wearing a feature's name.
  * THE VERDICT IS AUDITED as evidence on the risk decision, so a later reader can
    reconstruct why a decision went the way it did.
  * ``book_fn`` IS AN INJECTED SEAM, exactly like ``quote_fn``. Nothing in the workflow
    fetches a book.
"""
from __future__ import annotations

from decimal import Decimal

from config.settings import Mode
from orchestrator.mode_ladder import ModeLadder
from quant.book.lob import BookSnapshot, LevelSnapshot
from tests.workflows.conftest import FakeTransport, envelope
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


def _book(*, ask_depth: int = 800, seq: int = 7, bids=None, asks=None) -> BookSnapshot:
    """A book around $100.00 on a penny grid — matching ``_quote_fn``'s price of 100.

    ``ask_depth`` is the knob that matters here. Every proposal in this file is TEN SHARES,
    i.e. exactly the $1,000 that ``max_notional`` permits; what changes between tests is how
    much liquidity exists to fill it. That isolates the new check from every pre-existing
    dollar-denominated one.
    """
    if asks is None:
        asks = ((10_002, ask_depth),)
    if bids is None:
        bids = ((10_000, 800),)
    return BookSnapshot(
        symbol="AAPL", seq=seq, ts=0,
        bids=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=3) for p, q in bids),
        asks=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=1) for p, q in asks),
    )


def _draft(st, clock, tmp_path, *, quantity, notional=None, book_fn=None,
           quote_fn=_quote_fn):
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


def test_fractional_explicit_quantity_fails_to_human_without_an_order(st, clock, tmp_path):
    outcome = _draft(st, clock, tmp_path, quantity=5.7, book_fn=None)

    assert outcome.status == "needs_human"
    assert outcome.order is None
    assert any("quantity unresolvable" in reason for reason in outcome.reasons)


# ---- the case the cockpit could not previously see -------------------------------------------


def test_a_fully_funded_proposal_is_REJECTED_when_the_book_cannot_absorb_it(st, clock, tmp_path):
    """Ten shares — exactly the $1,000 ``max_notional`` allows — into a book showing THREE.

    Every dollar-denominated check passes: the notional is at the limit, buying power is
    $200k, concentration is fine. Only the depth check can see that this order is three times
    the entire displayed offer.
    """
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=3))

    assert outcome.risk_decision.decision == "rejected"
    assert "execution_cost_sane" in outcome.risk_decision.failed_checks
    assert outcome.order is None, "a rejected proposal must never resolve an order tuple"
    assert any("displayed depth" in r for r in outcome.reasons)
    # And prove it was DEPTH that rejected it, not a dollar limit that happened to fire.
    assert "max_notional" not in outcome.risk_decision.failed_checks
    assert "buying_power" not in outcome.risk_decision.failed_checks


def test_the_IDENTICAL_proposal_is_APPROVED_when_no_book_is_attached(st, clock, tmp_path):
    """THE control, and the most important test in this file.

    Same ten shares, same everything, no ``book_fn``. Approved — which is exactly the pre-QT
    behaviour, and proves the rejection above is caused by the book rather than by the order
    tripping some pre-existing limit. It is also the gap, stated as an experiment: without
    depth, an order for three times the available liquidity is indistinguishable from a good
    one.
    """
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=None)

    assert outcome.risk_decision.decision == "approved"
    assert _check(outcome, "execution_cost_sane")["passed"]
    assert _check(outcome, "execution_quality")["passed"]


def test_the_same_proposal_passes_when_the_book_is_deep_enough(st, clock, tmp_path):
    """CONTROL for the rejection: a verifier that rejected everything would pass that test.
    Only ``ask_depth`` changed."""
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=800))

    assert outcome.risk_decision.decision == "approved"
    assert outcome.order is not None
    assert _check(outcome, "execution_cost_sane")["passed"]


def test_a_marginal_proposal_goes_to_a_human_rather_than_through_or_away(st, clock, tmp_path):
    """Ten shares into a book of thirty: 33% participation. Inside the displayed depth, so
    not fatal — but above the 25% ceiling at which walk-the-book slippage stops being a
    reliable estimate. Not fatal, not fine, so: a human."""
    outcome = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: _book(ask_depth=30))

    assert outcome.risk_decision.decision == "needs_human"
    assert "execution_quality" in outcome.risk_decision.warnings
    assert outcome.risk_decision.human_required is True
    assert _check(outcome, "execution_cost_sane")["passed"], "marginal must not hard-block"


# ---- additive, not a new requirement --------------------------------------------------------------


def test_a_book_fn_returning_None_behaves_exactly_like_no_book_fn(st, clock, tmp_path):
    """A symbol with no book must not be penalised. ``require_quant_verdict`` is off by
    default precisely so a partial feed does not send every uncovered name to review."""
    with_none = _draft(st, clock, tmp_path, quantity=10, book_fn=lambda _s: None)
    assert with_none.risk_decision.decision == "approved"


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
    assert recorded["verdict"] == "uneconomic"
    assert recorded["book_seq"] == 7
    assert recorded["qty"] == 10


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
    # Degrades to exactly the no-book case, which this file already pins as APPROVED.
    assert outcome.risk_decision.decision == "approved", outcome.risk_decision
