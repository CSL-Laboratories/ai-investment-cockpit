"""CSL-42 — Rust costs and PnL must match the Python Decimal implementation exactly.

Skipped when the extension is not installed, which is the hosted Python gate.
The Python functions named ``python_*`` are the reference; ``FeeSchedule`` and
``PnLTracker`` dispatch to Rust when the wheel is present.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from quant import costs
from quant.costs import (
    COSTS_BACKEND,
    ZERO_FEES,
    FeeSchedule,
    python_borrow_cost,
    python_cost_bps,
    python_fee_for,
)
from quant.events import Fill, Liquidity, PriceScale, Side
from quant.pnl import PNL_BACKEND, PnLTracker

pytestmark = pytest.mark.skipif(
    COSTS_BACKEND != "rust" or PNL_BACKEND != "rust",
    reason="cockpit_quant costs extension is not installed",
)

PENNY = PriceScale(tick_size=Decimal("0.01"))
SCHEDULE = FeeSchedule()


def _fill(side: Side, qty: int, ticks: int, *, liquidity=Liquidity.MAKER, ts: int = 0) -> Fill:
    return Fill(
        symbol="S", seq=1, exchange_ts=ts, recv_ts=ts, ingest_ts=ts,
        order_id="o", side=side, price_ticks=ticks, qty=qty, liquidity=liquidity,
    )


def _drive(tracker: PnLTracker, steps, rng: random.Random) -> None:
    mid = 10_000.0
    tracker.mark(mid, ts=0)
    for step in steps:
        mid = step["mid"]
        ts = step["ts"]
        tracker.mark(mid, ts=ts)
        fill = step["fill"]
        if fill is not None:
            tracker.on_fill(fill, mid_ticks=step["fill_mid"])


def _script(seed: int, n: int, schedule_name: str) -> list[dict]:
    rng = random.Random(seed)
    mid = 10_000.0
    ts = 0
    steps = []
    for _ in range(n):
        mid += rng.gauss(0.0, 2.0)
        mid = round(mid, 1)
        ts += rng.choice((1, 1_000, 1_000_000, 3_600 * 1_000_000_000, 50_000_000_017))
        fill = None
        fill_mid = None
        if rng.random() < 0.45:
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            ticks = int(round(mid)) + rng.randint(-3, 3)
            liquidity = Liquidity.MAKER if rng.random() < 0.5 else Liquidity.TAKER
            fill = _fill(side, rng.randrange(100, 500, 100), max(ticks, 1), liquidity=liquidity, ts=ts)
            fill_mid = mid if rng.random() < 0.8 else None
        steps.append({"mid": mid, "ts": ts, "fill": fill, "fill_mid": fill_mid, "schedule": schedule_name})
    return steps


def _trackers(schedule: FeeSchedule) -> tuple[PnLTracker, PnLTracker]:
    accelerated = PnLTracker(scale=PENNY, schedule=schedule)
    reference = PnLTracker(scale=PENNY, schedule=schedule)
    assert accelerated._core is not None
    reference._core = None
    return accelerated, reference


def _assert_same_tracker(accelerated: PnLTracker, reference: PnLTracker) -> None:
    assert accelerated.spread_capture == reference.spread_capture
    assert accelerated.inventory_drift == reference.inventory_drift
    assert accelerated.fees == reference.fees
    assert accelerated.realized_notional == reference.realized_notional
    assert accelerated.position == reference.position
    assert accelerated.fills == reference.fills
    assert accelerated.maker_fills == reference.maker_fills
    assert accelerated.taker_fills == reference.taker_fills
    assert accelerated._last_mid == reference._last_mid
    assert accelerated._last_ts == reference._last_ts
    assert accelerated._cash == reference._cash
    assert accelerated.mark_to_market() == reference.mark_to_market()
    assert accelerated.snapshot().gross == accelerated.mark_to_market()
    left = accelerated.snapshot().as_dict()
    right = reference.snapshot().as_dict()
    assert left.keys() == right.keys()
    for key, value in left.items():
        other = right[key]
        if isinstance(value, str):
            assert Decimal(value) == Decimal(other)
        else:
            assert value == other


def test_schedule_methods_match_the_python_reference():
    assert SCHEDULE.fee_for(
        side=Side.BUY, qty=1_000, price=Decimal("100.00"), liquidity=Liquidity.MAKER,
    ) == python_fee_for(
        SCHEDULE, side=Side.BUY, qty=1_000, price=Decimal("100.00"), liquidity=Liquidity.MAKER,
    )
    assert SCHEDULE.borrow_cost(short_notional=Decimal("1000000"), seconds=3600) == python_borrow_cost(
        SCHEDULE, short_notional=Decimal("1000000"), seconds=3600,
    )
    assert costs.cost_bps(Decimal("-1.80"), Decimal("100000")) == python_cost_bps(
        Decimal("-1.80"), Decimal("100000"),
    )


@pytest.mark.parametrize("seed", range(6))
def test_random_fees_borrow_and_bps_match(seed):
    rng = random.Random(seed)
    for _ in range(80):
        qty = rng.choice([1, 7, 100, 1_000, 25_000, 75_000, 10_000_000])
        price = Decimal(rng.randrange(1, 50_000)) / Decimal("100")
        side = Side.SELL if rng.random() < 0.5 else Side.BUY
        liquidity = Liquidity.MAKER if rng.random() < 0.5 else Liquidity.TAKER
        assert SCHEDULE.fee_for(side=side, qty=qty, price=price, liquidity=liquidity) == python_fee_for(
            SCHEDULE, side=side, qty=qty, price=price, liquidity=liquidity,
        )
        seconds = rng.choice([0, -1, 0.001, 1.5, 3600, 7200.5, 30 * 86400, 1e-9])
        notional = Decimal(rng.randrange(-5, 1_000_000)) / Decimal("100")
        assert SCHEDULE.borrow_cost(short_notional=notional, seconds=seconds) == python_borrow_cost(
            SCHEDULE, short_notional=notional, seconds=seconds,
        )
        cost = Decimal(rng.randrange(-10_000, 10_000)) / Decimal("100")
        base = Decimal(rng.randrange(0, 50_000)) / Decimal("10")
        assert costs.cost_bps(cost, base) == python_cost_bps(cost, base)


@pytest.mark.parametrize("seed", range(4))
def test_pnl_stream_matches_with_fees_disabled(seed):
    saved = costs._NATIVE
    costs._NATIVE = None
    try:
        accelerated, reference = _trackers(ZERO_FEES)
        _drive(reference, _script(seed, 120, "free"), random.Random(0))
        _drive(accelerated, _script(seed, 120, "free"), random.Random(0))
    finally:
        costs._NATIVE = saved
    _assert_same_tracker(accelerated, reference)


@pytest.mark.parametrize("seed", range(4))
def test_pnl_stream_matches_with_the_default_schedule(seed):
    saved = costs._NATIVE
    costs._NATIVE = None
    try:
        accelerated, reference = _trackers(SCHEDULE)
        _drive(reference, _script(seed + 20, 80, "default"), random.Random(0))
        _drive(accelerated, _script(seed + 20, 80, "default"), random.Random(0))
    finally:
        costs._NATIVE = saved
    _assert_same_tracker(accelerated, reference)


def test_batched_fee_and_pnl_replay_match_single_calls():
    import cockpit_quant

    rows = []
    expected = []
    rng = random.Random(1)
    for _ in range(200):
        qty = rng.choice([1, 100, 25_000, 10_000_000])
        price = Decimal(rng.randrange(1, 20_000)) / Decimal("100")
        side = "sell" if rng.random() < 0.5 else "buy"
        liquidity = "maker" if rng.random() < 0.5 else "taker"
        rows.append((side, qty, format(price, "f"), liquidity))
        expected.append(python_fee_for(
            SCHEDULE,
            side=Side.SELL if side == "sell" else Side.BUY,
            qty=qty,
            price=price,
            liquidity=Liquidity.MAKER if liquidity == "maker" else Liquidity.TAKER,
        ))
    got = cockpit_quant.fee_for_many(
        format(SCHEDULE.maker_rebate_per_share, "f"),
        format(SCHEDULE.taker_fee_per_share, "f"),
        format(SCHEDULE.sec_fee_rate, "f"),
        format(SCHEDULE.taf_per_share, "f"),
        format(SCHEDULE.taf_cap, "f"),
        format(SCHEDULE.clearing_per_share, "f"),
        rows,
    )
    assert [Decimal(item) for item in got] == expected

    script = _script(3, 40, "default")
    accelerated, _reference = _trackers(SCHEDULE)
    replay = []
    last_ts = None
    accelerated.mark(10_000.0, ts=0)
    replay.append(("m", "10000.0", 0, None, False, 0, 0, False, 0))
    last_ts = 0
    for step in script:
        elapsed = None if last_ts is None else str((step["ts"] - last_ts) / 1_000_000_000)
        accelerated.mark(step["mid"], ts=step["ts"])
        replay.append(("m", str(step["mid"]), step["ts"], elapsed, False, 0, 0, False, 0))
        last_ts = step["ts"]
        fill = step["fill"]
        if fill is None:
            continue
        fill_mid = step["fill_mid"]
        elapsed = None
        mid_text = None
        ts = None
        if fill_mid is not None:
            mid_text = str(fill_mid)
            ts = fill.ingest_ts
            elapsed = None if last_ts is None else str((ts - last_ts) / 1_000_000_000)
            last_ts = ts
        accelerated.on_fill(fill, mid_ticks=fill_mid)
        replay.append((
            "f", mid_text, ts, elapsed, fill.side is Side.SELL, fill.qty, fill.price_ticks,
            fill.liquidity is Liquidity.MAKER, fill.signed_qty,
        ))
    state = cockpit_quant.pnl_replay(
        format(SCHEDULE.maker_rebate_per_share, "f"),
        format(SCHEDULE.taker_fee_per_share, "f"),
        format(SCHEDULE.sec_fee_rate, "f"),
        format(SCHEDULE.taf_per_share, "f"),
        format(SCHEDULE.taf_cap, "f"),
        format(SCHEDULE.clearing_per_share, "f"),
        format(SCHEDULE.borrow_rate_annual, "f"),
        "0.01",
        replay,
    )
    spread, drift, fees, realized, position, fills, maker, taker, last_mid, last_ts, cash = state
    assert Decimal(spread) == accelerated.spread_capture
    assert Decimal(drift) == accelerated.inventory_drift
    assert Decimal(fees) == accelerated.fees
    assert Decimal(realized) == accelerated.realized_notional
    assert position == accelerated.position
    assert fills == accelerated.fills
    assert maker == accelerated.maker_fills
    assert taker == accelerated.taker_fills
    assert (None if last_mid is None else Decimal(last_mid)) == accelerated._last_mid
    assert last_ts == accelerated._last_ts
    assert Decimal(cash) == accelerated._cash
