"""QT-05 — Tier A execution verification gates.

The properties pinned:

  * IT ANSWERS THE QUESTION NOTHING ELSE IN THE REPO CAN. A 40,000-share order in a name
    showing 3,500 shares passes every check in ``risk/engine.py`` today, because none of them
    can see a book. This is the check that sees the book.
  * AN ORDER EXCEEDING ALL DISPLAYED DEPTH IS UNECONOMIC ON THAT GROUND ALONE, independent of
    the cost figure — which prices only the fillable portion and is therefore systematically
    optimistic exactly when the order is too large.
  * IT PRODUCES NO FILL PROBABILITY FOR A RESTING ORDER. That is the exp(-k*delta) shortcut
    the whole engine exists to replace; ``queue_ahead_shares`` is a measurement instead.
  * IT FAILS CLOSED. One-sided book, stale snapshot, unusable size -> INSUFFICIENT_DATA,
    never a confident OK (Invariant 7).
  * IT IS PURE. Same inputs, same verdict, no clock read — so a risk decision is
    reproducible from an audit record.
"""
from __future__ import annotations

from decimal import Decimal


from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.costs import ZERO_FEES
from quant.events import PriceScale
from quant.verify import Verdict, VerifyParams, proposal_quantity, verify_proposal

PENNY = PriceScale(tick_size=Decimal("0.01"))
PARAMS = VerifyParams(scale=PENNY)
FREE = VerifyParams(scale=PENNY, schedule=ZERO_FEES)


def book(*, bids=((10_000, 800),), asks=((10_002, 800), (10_003, 600), (10_005, 900)),
         seq: int = 10, ts: int = 0) -> BookSnapshot:
    return BookSnapshot(
        symbol="S", seq=seq, ts=ts,
        bids=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=1) for p, q in bids),
        asks=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=1) for p, q in asks),
    )


def proposal(**kwargs) -> dict:
    base = {"symbol": "S", "side": "buy", "quantity": 100, "order_type": "market"}
    base.update(kwargs)
    return base


# ---- the core measurement ---------------------------------------------------------------


def test_a_small_order_at_the_touch_is_cheap_and_actionable():
    verdict = verify_proposal(proposal(quantity=100), book(), PARAMS)
    assert verdict.verdict is Verdict.OK
    assert verdict.is_actionable
    assert verdict.fully_fillable
    assert verdict.expected_cost_bps < Decimal("5")


def test_walking_deeper_levels_costs_more_than_the_touch():
    """The whole point of having a book: cost is a function of SIZE, not just of spread."""
    small = verify_proposal(proposal(quantity=100), book(), FREE)
    large = verify_proposal(proposal(quantity=2_000), book(), FREE)
    assert large.expected_slippage_bps > small.expected_slippage_bps


def test_slippage_is_measured_against_the_mid_and_is_positive_for_both_sides():
    """Unsigned slippage would make every sell look free."""
    buy = verify_proposal(proposal(side="buy", quantity=100), book(), FREE)
    sell = verify_proposal(proposal(side="sell", quantity=100), book(), FREE)
    assert buy.expected_slippage_bps > 0
    assert sell.expected_slippage_bps > 0


def test_a_buy_consumes_asks_and_a_sell_consumes_bids():
    thin_ask = book(bids=((10_000, 50_000),), asks=((10_002, 100),))
    assert verify_proposal(proposal(side="buy", quantity=5_000), thin_ask, PARAMS).fillable_now == 100
    assert verify_proposal(proposal(side="sell", quantity=5_000), thin_ask, PARAMS).fillable_now == 5_000


def test_an_order_exceeding_all_displayed_depth_is_UNECONOMIC_not_merely_marginal():
    """THE headline check. The cost figure prices only the fillable portion, so a 40,000-share
    order into 3,500 of depth reports a comfortable few bps. Escalating on participation
    rather than on that number is what stops the estimate flattering the worst orders."""
    verdict = verify_proposal(proposal(quantity=40_000), book(), PARAMS)
    assert verdict.verdict is Verdict.UNECONOMIC
    assert not verdict.fully_fillable
    assert verdict.participation_rate > 1
    assert any("displayed depth" in r for r in verdict.reasons)


def test_a_high_but_sub_unity_participation_is_flagged_marginal():
    verdict = verify_proposal(proposal(quantity=1_500), book(), FREE)
    assert verdict.verdict is Verdict.MARGINAL
    assert any("participation" in r for r in verdict.reasons)


def test_an_expensive_book_is_uneconomic_even_when_it_can_fill():
    """Depth alone is not enough — a book that can absorb the order at a terrible price is
    still a trade that cannot pay for itself."""
    wide = book(bids=((9_000, 100_000),), asks=((11_000, 100_000),))
    verdict = verify_proposal(proposal(quantity=100), wide, PARAMS)
    assert verdict.verdict is Verdict.UNECONOMIC
    assert verdict.fully_fillable


def test_fees_are_included_in_the_total_and_are_visible_separately():
    charged = verify_proposal(proposal(quantity=100), book(), PARAMS)
    free = verify_proposal(proposal(quantity=100), book(), FREE)
    assert charged.expected_fee_bps > 0
    assert free.expected_fee_bps == 0
    assert charged.expected_cost_bps > free.expected_cost_bps
    assert charged.expected_cost_bps == charged.expected_slippage_bps + charged.expected_fee_bps


def test_a_crossing_proposal_is_priced_as_a_TAKER():
    """Assuming maker treatment would credit a rebate the order will not earn and understate
    the cost by the full maker/taker spread."""
    verdict = verify_proposal(proposal(quantity=100), book(), PARAMS)
    assert verdict.expected_fee_bps > 0        # a maker assumption would make this negative


# ---- what it refuses to invent -------------------------------------------------------------


def test_it_reports_queue_depth_rather_than_a_fill_probability():
    """No probability is produced for a resting order anywhere in this module — that is the
    exp(-k*delta) shortcut, and in a risk check it would carry more authority than in a
    backtest."""
    verdict = verify_proposal(proposal(quantity=100), book(bids=((10_000, 4_000),)), PARAMS)
    assert verdict.queue_ahead_shares == 4_000
    assert not hasattr(verdict, "fill_probability")


# ---- failing closed ---------------------------------------------------------------------------


def test_a_one_sided_book_yields_insufficient_data_not_a_confident_ok():
    verdict = verify_proposal(proposal(), book(asks=()), PARAMS)
    assert verdict.verdict is Verdict.INSUFFICIENT_DATA
    assert not verdict.is_actionable


def test_a_stale_snapshot_yields_insufficient_data():
    verdict = verify_proposal(proposal(), book(ts=0), PARAMS, now_ns=60_000_000_000)
    assert verdict.verdict is Verdict.INSUFFICIENT_DATA
    assert any("stale" in r for r in verdict.reasons)


def test_a_fresh_snapshot_is_accepted():
    """CONTROL for the staleness test — a verifier that rejected everything would pass it."""
    verdict = verify_proposal(proposal(), book(ts=0), PARAMS, now_ns=1_000_000)
    assert verdict.verdict is Verdict.OK


def test_an_unknown_side_is_refused():
    assert verify_proposal(proposal(side="hodl"), book(), PARAMS).verdict is Verdict.INSUFFICIENT_DATA


def test_a_proposal_with_neither_quantity_nor_notional_is_refused_not_treated_as_zero():
    """A zero-share order would sail through every cost check."""
    verdict = verify_proposal(proposal(quantity=None, notional=None), book(), PARAMS)
    assert verdict.verdict is Verdict.INSUFFICIENT_DATA


def test_quantity_is_derived_from_notional_when_the_schema_sizes_that_way():
    """TradeProposal makes both nullable — sizing may legitimately be expressed either way."""
    assert proposal_quantity({"notional": 10_000}, Decimal("100.00")) == 100
    assert proposal_quantity({"quantity": 250}, Decimal("100.00")) == 250
    assert proposal_quantity({"quantity": 5.7}, Decimal("100.00")) is None
    huge_whole = 123_456_789_012_345_678_901_234_567_890
    assert proposal_quantity({"quantity": huge_whole}, Decimal("100.00")) == huge_whole
    assert proposal_quantity({"quantity": None, "notional": None}, Decimal("100.00")) is None
    assert proposal_quantity({"quantity": 0}, Decimal("100.00")) is None
    assert proposal_quantity({"quantity": "garbage"}, Decimal("100.00")) is None


# ---- purity and provenance ----------------------------------------------------------------------


def test_the_verdict_is_deterministic():
    """A risk decision must be reproducible from an audit record."""
    first = verify_proposal(proposal(quantity=1_200), book(), PARAMS, now_ns=5)
    second = verify_proposal(proposal(quantity=1_200), book(), PARAMS, now_ns=5)
    assert first == second


def test_the_verdict_carries_the_book_sequence_it_describes():
    """Without it a stale verdict is indistinguishable from a current one — and a stale
    verdict reading OK is worse than no verdict at all."""
    assert verify_proposal(proposal(), book(seq=99), PARAMS).book_seq == 99


def test_the_verdict_serialises_to_json_native_types_only():
    """It lands in an audit payload, and the chain's canonical encoder has no ``default=``:
    it fails closed on any non-JSON-native value rather than guessing."""
    import json

    payload = verify_proposal(proposal(quantity=1_200), book(), PARAMS).as_dict()
    assert json.loads(json.dumps(payload)) == payload


def test_verify_imports_nothing_that_would_violate_invariant_5():
    """``risk/`` imports this module. A numpy or requests import here would propagate
    straight into the risk engine, which Invariant 5 requires to stay pure."""
    import quant.verify as module

    source = __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
    for banned in ("import numpy", "import pandas", "import requests", "import httpx",
                   "import subprocess", "open(", "urllib"):
        assert banned not in source, f"quant/verify.py must not contain {banned!r}"
