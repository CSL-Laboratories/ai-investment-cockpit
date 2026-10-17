"""CSL-42 — Rust costs and PnL must match the Python Decimal implementation exactly.

Skipped when the extension is not installed, which is the hosted Python gate.
The Python functions named ``python_*`` are the reference; ``FeeSchedule`` and
``PnLTracker`` dispatch to Rust when the wheel is present.
"""
from __future__ import annotations

import random
from decimal import ROUND_DOWN, Decimal, InvalidOperation, localcontext

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


def _assert_nonfinite(got: Decimal, expected: Decimal) -> None:
    """``Decimal("NaN") == Decimal("NaN")`` is False, so NaN is matched by class."""
    if expected.is_nan():
        assert got.is_nan(), got
    else:
        assert got == expected, (got, expected)


@pytest.mark.parametrize(
    ("mid", "expected_mid"),
    [
        (float("nan"), Decimal("NaN")),
        (float("inf"), Decimal("Infinity")),
        (float("-inf"), Decimal("-Infinity")),
    ],
    ids=["nan", "inf", "-inf"],
)
def test_nonfinite_mids_stay_on_the_python_path(mid, expected_mid):
    """NaN and ±Inf must not reach PnlCore, which rejects them as ValueError.

    The ledger is pinned from the Decimal rules rather than read off a second
    tracker driven through the same methods: ``Decimal(str(mid)) * tick`` keeps
    the NaN or the infinity, spread capture inherits it through
    ``(mid - price) * signed_qty``, and the fill still lands on the cash,
    notional and position ledgers at the fill price.
    """
    accelerated, _reference = _trackers(ZERO_FEES)
    accelerated.mark(mid, ts=0)
    assert accelerated._core is None
    assert accelerated._last_mid is not None
    _assert_nonfinite(accelerated._last_mid, expected_mid)
    assert accelerated._last_ts == 0
    assert accelerated.inventory_drift == 0

    accelerated.on_fill(_fill(Side.BUY, 100, 10_000), mid_ticks=mid)
    assert accelerated._core is None
    _assert_nonfinite(accelerated._last_mid, expected_mid)
    _assert_nonfinite(accelerated.spread_capture, expected_mid)
    assert accelerated.inventory_drift == 0
    assert accelerated.fees == 0
    assert accelerated.realized_notional == Decimal("10000.00")
    assert accelerated._cash == Decimal("-10000.00")
    assert accelerated.position == 100
    assert accelerated.fills == accelerated.maker_fills == 1
    _assert_nonfinite(accelerated.mark_to_market(), expected_mid)
    _assert_nonfinite(accelerated.snapshot().total, expected_mid)


def test_huge_integer_mid_does_not_overflow_the_finite_check():
    """``math.isfinite`` raises OverflowError for ints past the float range."""
    accelerated, reference = _trackers(ZERO_FEES)
    mid = 10**309
    accelerated.mark(mid, ts=0)
    reference.mark(mid, ts=0)
    _assert_same_tracker(accelerated, reference)


def test_position_overflow_keeps_the_python_ledger():
    """A fill that would overflow i64 must not half-apply fees in the native core."""
    qty = 2**62
    accelerated, reference = _trackers(SCHEDULE)
    accelerated.mark(100.0, ts=0)
    reference.mark(100.0, ts=0)
    accelerated.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=100.0)
    reference.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=100.0)
    assert accelerated.position == qty
    assert accelerated._core is not None
    accelerated.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=102.0)
    reference.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=102.0)
    _assert_same_tracker(accelerated, reference)
    assert accelerated.position == 2**63
    assert accelerated._core is None
    accelerated.mark(102.0, ts=1)
    reference.mark(102.0, ts=1)
    _assert_same_tracker(accelerated, reference)


def _short_one_hour(tracker: PnLTracker) -> None:
    """Sell 300 taker at 100.01 against a 100.005 mid, then mark 99.995 an hour later."""
    tracker.mark(10_000.5, ts=0)
    tracker.on_fill(
        _fill(Side.SELL, 300, 10_001, liquidity=Liquidity.TAKER, ts=0), mid_ticks=10_000.5,
    )
    tracker.mark(9_999.5, ts=3_600 * 1_000_000_000)


def test_pnl_custom_decimal_context_matches_python():
    """PnLTracker must honour a non-default context instead of 28-digit Rust arithmetic.

    The ledger is pinned to values only a precision-6 context produces, the way the
    cost-bps test pins ``1428.57``. The fees are 0.9 taker + 0.83 Section 31 + 0.0498
    TAF + 0.06 clearing + 0.0171225... borrow; precision 6 rounds that to ``1.85692``
    where the default context keeps ``1.856922431506849315068493151``. A tracker that
    ignored the caller's context would fail these equalities on either path.
    """
    with localcontext() as ctx:
        ctx.prec = 6
        accelerated, reference = _trackers(SCHEDULE)
        _short_one_hour(accelerated)
        _short_one_hour(reference)
        _assert_same_tracker(accelerated, reference)
        assert accelerated._core is None
        assert accelerated.spread_capture == Decimal("1.500")
        assert accelerated.inventory_drift == Decimal("3.000")
        assert accelerated.fees == Decimal("1.85692")
        assert accelerated.mark_to_market() == Decimal("4.5")
        assert accelerated.snapshot().total == Decimal("2.64308")
        # 100.01 * 300 is seven digits, so precision 6 drops the last place.
        assert str(accelerated.realized_notional) == "30003.0"
        assert str(accelerated._cash) == "30003.0"

    default = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    _short_one_hour(default)
    assert default.fees == Decimal("1.856922431506849315068493151")
    assert default.snapshot().total == Decimal("2.643077568493150684931506849")
    assert str(default.realized_notional) == "30003.00"


def test_nonfinite_borrow_seconds_stay_on_the_python_path():
    """Non-finite seconds must not hit Rust's Dec::parse ValueError."""
    for seconds in (
        float("nan"), float("inf"), float("-inf"),
        Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"),
    ):
        try:
            expected = python_borrow_cost(
                SCHEDULE, short_notional=Decimal("1000000"), seconds=seconds,
            )
        except Exception as exc:
            with pytest.raises(type(exc)):
                SCHEDULE.borrow_cost(short_notional=Decimal("1000000"), seconds=seconds)
            continue
        got = SCHEDULE.borrow_cost(short_notional=Decimal("1000000"), seconds=seconds)
        if expected.is_nan():
            assert got.is_nan()
        else:
            assert got == expected


def test_custom_decimal_context_matches_python():
    """A narrowed, re-rounded, or untrapped context must not keep Rust's fixed result."""
    one = Decimal("1")
    seven = Decimal("7")
    with localcontext() as ctx:
        ctx.prec = 6
        assert costs.cost_bps(one, seven) == python_cost_bps(one, seven) == Decimal("1428.57")
        assert SCHEDULE.borrow_cost(short_notional=one, seconds=1) == python_borrow_cost(
            SCHEDULE, short_notional=one, seconds=1,
        )
    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        assert costs.cost_bps(one, seven) == python_cost_bps(one, seven)
        assert costs.cost_bps(one, seven) == Decimal("1428.571428571428571428571428")
    with localcontext() as ctx:
        ctx.traps[InvalidOperation] = False
        borrowed = SCHEDULE.borrow_cost(short_notional=Decimal("NaN"), seconds=1)
        reference = python_borrow_cost(SCHEDULE, short_notional=Decimal("NaN"), seconds=1)
        # Decimal NaN is not equal to itself; both paths must return NaN rather than raise.
        assert borrowed.is_nan() and reference.is_nan()
    assert costs.cost_bps(one, seven) == python_cost_bps(one, seven)
    assert costs.cost_bps(one, seven) == Decimal("1428.571428571428571428571429")


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
