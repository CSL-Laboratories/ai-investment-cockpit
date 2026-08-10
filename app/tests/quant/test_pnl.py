"""QT-04 — PnL attribution and cost-model gates.

The properties pinned:

  * THE DECOMPOSITION IS EXACT. spread_capture + inventory_drift == mark-to-market, proven
    against a randomised fill and mid stream. An attribution that only approximately
    reconciles has a residual that absorbs every bookkeeping error, and the residual is
    never inspected.
  * A MAKER REBATE IS INCOME, and reads as a NEGATIVE fee. A rebate that arrives positive
    and gets subtracted is a sign error worth twice the rebate and makes passive strategies
    look unprofitable.
  * EXCHANGE FEES ARE PER SHARE, NOT PER NOTIONAL. Modelling them in bps makes low-priced
    names look tradeable when they are not — exactly where a naive HFT strategy would
    concentrate.
  * REGULATORY FEES ARE SELL-SIDE ONLY. Charging them on buys silently doubles the cost of
    every round trip, which reads as a strategy problem rather than a fee-model one.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from quant.costs import ZERO_FEES, FeeSchedule, cost_bps
from quant.events import Fill, Liquidity, PriceScale, Side
from quant.pnl import PnLTracker

PENNY = PriceScale(tick_size=Decimal("0.01"))
SCHEDULE = FeeSchedule()


def _fill(side: Side, qty: int, ticks: int, *, liquidity=Liquidity.MAKER, ts: int = 0) -> Fill:
    return Fill(symbol="S", seq=1, exchange_ts=ts, recv_ts=ts, ingest_ts=ts,
                order_id="o", side=side, price_ticks=ticks, qty=qty, liquidity=liquidity)


# ---- the cost schedule ------------------------------------------------------------------


def test_a_maker_fill_is_PAID_and_the_fee_is_negative():
    fee = SCHEDULE.fee_for(side=Side.BUY, qty=1_000, price=Decimal("100.00"),
                           liquidity=Liquidity.MAKER)
    assert fee < 0
    # rebate 1000 * 0.0020 = 2.00 credited, clearing 1000 * 0.0002 = 0.20 charged
    assert fee == Decimal("-1.80")


def test_a_taker_fill_is_charged():
    fee = SCHEDULE.fee_for(side=Side.BUY, qty=1_000, price=Decimal("100.00"),
                           liquidity=Liquidity.TAKER)
    assert fee == Decimal("3.20")      # 3.00 taker + 0.20 clearing


def test_exchange_fees_are_per_share_so_they_bite_hardest_on_cheap_stock():
    """THE modelling error this schedule exists to avoid. The same $0.30 taker fee is
    0.075 bps on a $400 name and 7.5 bps on a $4 one — a bps model calls both negligible."""
    dear = SCHEDULE.fee_for(side=Side.BUY, qty=100, price=Decimal("400.00"),
                            liquidity=Liquidity.TAKER)
    cheap = SCHEDULE.fee_for(side=Side.BUY, qty=100, price=Decimal("4.00"),
                             liquidity=Liquidity.TAKER)
    assert dear == cheap                                      # identical in dollars
    assert cost_bps(cheap, Decimal("400")) > cost_bps(dear, Decimal("40000")) * 50


def test_regulatory_fees_apply_to_sells_only():
    buy = SCHEDULE.fee_for(side=Side.BUY, qty=1_000, price=Decimal("100.00"),
                           liquidity=Liquidity.TAKER)
    sell = SCHEDULE.fee_for(side=Side.SELL, qty=1_000, price=Decimal("100.00"),
                            liquidity=Liquidity.TAKER)
    assert sell > buy
    # SEC 100,000 * 0.0000278 = 2.78 ; TAF 1000 * 0.000166 = 0.166
    assert sell - buy == Decimal("2.78") + Decimal("0.166")


def test_the_taf_is_capped():
    """Isolate the regulatory component as (sell - buy): the per-share taker and clearing
    legs are identical on both sides, so the difference is exactly SEC + TAF."""
    qty, price = 10_000_000, Decimal("1.00")
    sell = SCHEDULE.fee_for(side=Side.SELL, qty=qty, price=price, liquidity=Liquidity.TAKER)
    buy = SCHEDULE.fee_for(side=Side.BUY, qty=qty, price=price, liquidity=Liquidity.TAKER)
    sec = (price * Decimal(qty) * SCHEDULE.sec_fee_rate).quantize(Decimal("0.01"))

    uncapped_taf = SCHEDULE.taf_per_share * Decimal(qty)
    assert uncapped_taf > SCHEDULE.taf_cap          # the cap is actually binding here
    assert sell - buy == sec + SCHEDULE.taf_cap


def test_borrow_accrues_on_elapsed_time_not_per_day():
    """A daily accrual rounds a seconds-long short's borrow cost to zero, which is the
    assumption that makes an intraday short look free."""
    cost = SCHEDULE.borrow_cost(short_notional=Decimal("1000000"), seconds=3600)
    assert cost > 0
    assert SCHEDULE.borrow_cost(short_notional=Decimal("1000000"), seconds=7200) == cost * 2


def test_a_long_or_flat_book_pays_no_borrow():
    assert SCHEDULE.borrow_cost(short_notional=Decimal("0"), seconds=3600) == 0
    assert SCHEDULE.borrow_cost(short_notional=Decimal("-500"), seconds=3600) == 0


def test_the_zero_schedule_is_actually_free():
    """Used to measure the cost of costs. If it were not exactly zero, the difference
    between a frictionless and a realistic run would not isolate the fee assumption."""
    assert ZERO_FEES.fee_for(side=Side.SELL, qty=1_000, price=Decimal("100.00"),
                             liquidity=Liquidity.TAKER) == 0


def test_cost_bps_preserves_the_sign_so_a_rebate_reads_negative():
    assert cost_bps(Decimal("-1.80"), Decimal("100000")) < 0


# ---- attribution ------------------------------------------------------------------------------


def test_buying_below_the_mid_is_positive_spread_capture():
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    tracker.mark(10_001.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 100, 10_000))
    assert tracker.spread_capture > 0


def test_selling_above_the_mid_is_positive_spread_capture():
    """The mirror. A sign error keyed on side alone would pass the buy case only."""
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    tracker.mark(10_001.0, ts=0)
    tracker.on_fill(_fill(Side.SELL, 100, 10_002))
    assert tracker.spread_capture > 0


def test_crossing_the_spread_is_NEGATIVE_spread_capture():
    """Which is how the attribution says 'this market maker is not making markets'."""
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    tracker.mark(10_001.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 100, 10_002, liquidity=Liquidity.TAKER))
    assert tracker.spread_capture < 0


def test_a_long_position_gains_when_the_mid_rises():
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    tracker.mark(10_000.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 100, 10_000))
    tracker.mark(10_010.0, ts=1)
    assert tracker.inventory_drift == Decimal("100") * Decimal("0.10")


def test_a_short_position_loses_when_the_mid_rises():
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    tracker.mark(10_000.0, ts=0)
    tracker.on_fill(_fill(Side.SELL, 100, 10_000))
    tracker.mark(10_010.0, ts=1)
    assert tracker.inventory_drift < 0


def test_a_flat_book_accrues_no_inventory_drift_however_far_the_mid_moves():
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    for mid in (10_000.0, 10_500.0, 9_000.0, 10_000.0):
        tracker.mark(mid, ts=1)
    assert tracker.inventory_drift == 0


def test_the_decomposition_is_EXACT_against_an_independent_mark_to_market():
    """The identity that makes the attribution trustworthy, on a hand-built sequence."""
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    tracker.mark(10_000.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 200, 9_999))
    tracker.mark(10_004.0, ts=1)
    tracker.on_fill(_fill(Side.SELL, 100, 10_006))
    tracker.mark(10_002.0, ts=2)

    snap = tracker.snapshot()
    assert snap.gross == tracker.mark_to_market()


@pytest.mark.parametrize("seed", range(8))
def test_the_decomposition_is_exact_under_a_random_fill_and_mid_stream(seed):
    """The strong version. Two separately-computed totals must agree to the cent over
    hundreds of interleaved fills and marks — no residual is permitted to absorb an error."""
    rng = random.Random(seed)
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    mid = 10_000.0
    tracker.mark(mid, ts=0)

    for step in range(400):
        mid += rng.gauss(0.0, 2.0)
        tracker.mark(round(mid, 1), ts=step)
        if rng.random() < 0.3:
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            ticks = int(round(mid)) + rng.randint(-3, 3)
            tracker.on_fill(_fill(side, rng.randrange(100, 500, 100), ticks, ts=step))

    snap = tracker.snapshot()
    assert snap.gross == tracker.mark_to_market(), (
        f"attribution does not reconcile at seed={seed}: "
        f"spread={snap.spread_capture} drift={snap.inventory_drift} mtm={tracker.mark_to_market()}"
    )


def test_fees_are_reported_separately_and_subtracted_from_gross():
    tracker = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    tracker.mark(10_001.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 1_000, 10_000, liquidity=Liquidity.MAKER))
    snap = tracker.snapshot()
    assert snap.fees < 0                       # a rebate: we were paid
    assert snap.total > snap.gross             # so the net beats the gross


def test_a_taker_strategy_pays_and_the_net_is_worse_than_gross():
    """CONTROL for the rebate test — the sign must go both ways."""
    tracker = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    tracker.mark(10_001.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 1_000, 10_002, liquidity=Liquidity.TAKER))
    snap = tracker.snapshot()
    assert snap.fees > 0
    assert snap.total < snap.gross


def test_maker_ratio_identifies_a_market_maker_that_is_actually_crossing():
    tracker = PnLTracker(scale=PENNY, schedule=ZERO_FEES)
    tracker.mark(10_000.0, ts=0)
    for _ in range(3):
        tracker.on_fill(_fill(Side.BUY, 100, 10_000, liquidity=Liquidity.TAKER))
    tracker.on_fill(_fill(Side.SELL, 100, 10_000, liquidity=Liquidity.MAKER))
    assert tracker.snapshot().maker_ratio == 0.25


def test_a_short_position_accrues_borrow_over_time():
    tracker = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    tracker.mark(10_000.0, ts=0)
    tracker.on_fill(_fill(Side.SELL, 10_000, 10_000, liquidity=Liquidity.MAKER, ts=0))
    before = tracker.fees
    tracker.mark(10_000.0, ts=3_600 * 1_000_000_000)
    assert tracker.fees > before


def test_attribution_serialises_for_the_tearsheet_and_the_websocket():
    tracker = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    tracker.mark(10_000.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 100, 9_999))
    payload = tracker.snapshot().as_dict()
    assert set(payload) >= {"spread_capture", "inventory_drift", "fees", "total", "maker_ratio"}
    assert all(isinstance(v, (str, int, float)) for v in payload.values())
