"""CSL-42 — Rust costs and PnL must match the Python Decimal implementation exactly.

Skipped when the extension is not installed, which is the hosted Python gate.
The Python functions named ``python_*`` are the reference. ``FeeSchedule.fee_for``
and ``PnLTracker.mark`` / ``on_fill`` stay on that path. The extension functions
are what this module compares against it.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import textwrap
from decimal import ROUND_DOWN, Decimal, InvalidOperation, localcontext
from pathlib import Path

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
from quant.pnl import PNL_BACKEND, PnLTracker, _make_pnl_core, _rust_mark, _rust_on_fill

# The rust.yml job sets this so a wheel that installs but does not activate
# fails the job. Hosted Python gates leave it unset and skip.
_REQUIRE_RUST = os.environ.get("COCKPIT_REQUIRE_RUST") == "1"
if _REQUIRE_RUST and (COSTS_BACKEND != "rust" or PNL_BACKEND != "rust"):
    raise RuntimeError("COCKPIT_REQUIRE_RUST=1 but the costs backend is python")

pytestmark = pytest.mark.skipif(
    not _REQUIRE_RUST and (COSTS_BACKEND != "rust" or PNL_BACKEND != "rust"),
    reason="cockpit_quant costs extension is not installed",
)

PENNY = PriceScale(tick_size=Decimal("0.01"))
SCHEDULE = FeeSchedule()


def _fill(side: Side, qty: int, ticks: int, *, liquidity=Liquidity.MAKER, ts: int = 0) -> Fill:
    return Fill(
        symbol="S", seq=1, exchange_ts=ts, recv_ts=ts, ingest_ts=ts,
        order_id="o", side=side, price_ticks=ticks, qty=qty, liquidity=liquidity,
    )


def _drive(tracker: PnLTracker, steps, rng: random.Random, *, core: bool = False) -> None:
    if core:
        tracker._core = _make_pnl_core(tracker.schedule)
        assert tracker._core is not None
        _rust_mark(tracker, 10_000.0, 0)
        for step in steps:
            _rust_mark(tracker, step["mid"], step["ts"])
            fill = step["fill"]
            if fill is not None:
                _rust_on_fill(tracker, fill, step["fill_mid"])
        return
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
    assert accelerated._core is None
    assert reference._core is None
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
    accelerated = _trackers(ZERO_FEES)[0]
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


def _extension_fee(side: Side, qty: int, price: Decimal, liquidity: Liquidity) -> Decimal:
    import cockpit_quant
    return Decimal(cockpit_quant.fee_for(
        format(SCHEDULE.maker_rebate_per_share, "f"),
        format(SCHEDULE.taker_fee_per_share, "f"),
        format(SCHEDULE.sec_fee_rate, "f"),
        format(SCHEDULE.taf_per_share, "f"),
        format(SCHEDULE.taf_cap, "f"),
        format(SCHEDULE.clearing_per_share, "f"),
        "sell" if side is Side.SELL else "buy",
        qty,
        format(price, "f"),
        "maker" if liquidity is Liquidity.MAKER else "taker",
    ))


def _extension_borrow(notional: Decimal, seconds) -> Decimal:
    import cockpit_quant
    return Decimal(cockpit_quant.borrow_cost(
        format(SCHEDULE.borrow_rate_annual, "f"), format(notional, "f"), str(seconds),
    ))


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
    # Python's Decimal keeps the price's scale: 100.01 * 300 is "30003.00".
    assert str(default.realized_notional) == "30003.00"
    assert str(default._cash) == "30003.00"

    # The PnlCore path must hand back the same text, not a normalized "30003".
    core = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    core._core = _make_pnl_core(core.schedule)
    assert core._core is not None
    _rust_mark(core, 10_000.5, 0)
    _rust_on_fill(
        core, _fill(Side.SELL, 300, 10_001, liquidity=Liquidity.TAKER, ts=0), 10_000.5,
    )
    _rust_mark(core, 9_999.5, 3_600 * 1_000_000_000)
    assert str(core.realized_notional) == "30003.00"
    assert str(core._cash) == "30003.00"
    assert core.fees == default.fees


def _assert_same_text(accelerated: PnLTracker, reference: PnLTracker) -> None:
    """Ledger text, not just ``Decimal`` equality. ``0`` and ``0.00`` compare equal."""
    names = ("spread_capture", "inventory_drift", "fees", "realized_notional", "_cash")
    for name in names:
        got = str(getattr(accelerated, name))
        expected = str(getattr(reference, name))
        assert got == expected, (name, got, expected)


def test_exact_zero_keeps_python_scale():
    """Flat round-trip zero keeps its scale, including a negative zero.

    Buy 100 at 100.00, then sell 100 at 100.00. The sell's spread edge is
    ``0.00 * -100``, a negative zero, and both that edge and cash cancel to
    ``0.00``. The next fill is priced at ``100.0``; the sum is ``-100.00``
    because the cancelled cash still has exponent -2. ``cost_bps`` checks a
    negative zero that stays negative on the way back across the bridge.
    """
    import cockpit_quant

    negative = Decimal("-0.00")
    two = Decimal("2")
    got_negative = cockpit_quant.cost_bps(format(negative, "f"), format(two, "f"))
    assert got_negative == str(python_cost_bps(negative, two)) == "-0.00"
    positive = Decimal("0.00")
    got_positive = cockpit_quant.cost_bps(format(positive, "f"), format(two, "f"))
    assert got_positive == str(python_cost_bps(positive, two)) == "0.00"

    accelerated, reference = _trackers(ZERO_FEES)
    buy = _fill(Side.BUY, 100, 10_000)
    sell = _fill(Side.SELL, 100, 10_000)
    reference.mark(10000, ts=0)
    reference.on_fill(buy, mid_ticks=10000)
    reference.on_fill(sell, mid_ticks=10000)
    accelerated._core = _make_pnl_core(accelerated.schedule)
    assert accelerated._core is not None
    _rust_mark(accelerated, 10000, 0)
    _rust_on_fill(accelerated, buy, 10000)
    _rust_on_fill(accelerated, sell, 10000)
    _assert_same_text(accelerated, reference)
    assert str(accelerated._cash) == "0.00"
    assert str(accelerated.spread_capture) == "0.00"
    assert str(accelerated.inventory_drift) == "0.00"
    assert str(accelerated.fees) == "0.00"
    assert str(accelerated.realized_notional) == "20000.00"

    coarse = PriceScale(tick_size=Decimal("1.0"))
    later = _fill(Side.BUY, 1, 100, ts=1)
    reference.scale = coarse
    accelerated.scale = coarse
    reference.on_fill(later, mid_ticks=100)
    _rust_on_fill(accelerated, later, 100)
    _assert_same_text(accelerated, reference)
    assert str(accelerated._cash) == "-100.00"
    assert str(accelerated.realized_notional) == "20100.00"


def _extension_bps(cost: Decimal, notional: Decimal) -> Decimal:
    import cockpit_quant
    return Decimal(cockpit_quant.cost_bps(format(cost, "f"), format(notional, "f")))


def test_schedule_methods_match_the_python_reference():
    assert SCHEDULE.fee_for(
        side=Side.BUY, qty=1_000, price=Decimal("100.00"), liquidity=Liquidity.MAKER,
    ) == python_fee_for(
        SCHEDULE, side=Side.BUY, qty=1_000, price=Decimal("100.00"), liquidity=Liquidity.MAKER,
    )
    assert _extension_fee(Side.BUY, 1_000, Decimal("100.00"), Liquidity.MAKER) == python_fee_for(
        SCHEDULE, side=Side.BUY, qty=1_000, price=Decimal("100.00"), liquidity=Liquidity.MAKER,
    )
    assert SCHEDULE.borrow_cost(short_notional=Decimal("1000000"), seconds=3600) == python_borrow_cost(
        SCHEDULE, short_notional=Decimal("1000000"), seconds=3600,
    )
    assert _extension_borrow(Decimal("1000000"), 3600) == python_borrow_cost(
        SCHEDULE, short_notional=Decimal("1000000"), seconds=3600,
    )
    assert costs.cost_bps(Decimal("-1.80"), Decimal("100000")) == python_cost_bps(
        Decimal("-1.80"), Decimal("100000"),
    )
    assert _extension_bps(Decimal("-1.80"), Decimal("100000")) == python_cost_bps(
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
        assert _extension_fee(side, qty, price, liquidity) == python_fee_for(
            SCHEDULE, side=side, qty=qty, price=price, liquidity=liquidity,
        )
        seconds = rng.choice([0, -1, 0.001, 1.5, 3600, 7200.5, 30 * 86400, 1e-9])
        notional = Decimal(rng.randrange(-5, 1_000_000)) / Decimal("100")
        assert SCHEDULE.borrow_cost(short_notional=notional, seconds=seconds) == python_borrow_cost(
            SCHEDULE, short_notional=notional, seconds=seconds,
        )
        assert _extension_borrow(notional, seconds) == python_borrow_cost(
            SCHEDULE, short_notional=notional, seconds=seconds,
        )
        cost = Decimal(rng.randrange(-10_000, 10_000)) / Decimal("100")
        base = Decimal(rng.randrange(0, 50_000)) / Decimal("10")
        assert costs.cost_bps(cost, base) == python_cost_bps(cost, base)
        assert _extension_bps(cost, base) == python_cost_bps(cost, base)


@pytest.mark.parametrize("seed", range(4))
def test_pnl_stream_matches_with_fees_disabled(seed):
    accelerated, reference = _trackers(ZERO_FEES)
    script = _script(seed, 120, "free")
    _drive(reference, script, random.Random(0))
    _drive(accelerated, script, random.Random(0), core=True)
    _assert_same_tracker(accelerated, reference)


@pytest.mark.parametrize("seed", range(4))
def test_pnl_stream_matches_with_the_default_schedule(seed):
    accelerated, reference = _trackers(SCHEDULE)
    script = _script(seed + 20, 80, "default")
    _drive(reference, script, random.Random(0))
    _drive(accelerated, script, random.Random(0), core=True)
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


def test_nonfinite_mids_accelerated_matches_reference():
    """NaN and ±Inf stay on Decimal. PnlCore rejects them, so the live call does not use it."""
    for mid in (float("nan"), float("inf"), float("-inf")):
        accelerated, reference = _trackers(ZERO_FEES)
        try:
            reference.mark(mid, ts=0)
        except Exception as exc:
            with pytest.raises(type(exc)):
                accelerated.mark(mid, ts=0)
            assert accelerated._core is None
            continue
        accelerated.mark(mid, ts=0)
        assert accelerated._core is None
        assert reference._last_mid is not None and accelerated._last_mid is not None
        if reference._last_mid.is_nan():
            assert accelerated._last_mid.is_nan()
        else:
            assert accelerated._last_mid == reference._last_mid
        accelerated.on_fill(_fill(Side.BUY, 100, 10_000), mid_ticks=mid)
        reference.on_fill(_fill(Side.BUY, 100, 10_000), mid_ticks=mid)
        assert accelerated.position == reference.position == 100
        assert accelerated.fills == reference.fills == 1


def test_pnl_core_rejects_a_nonfinite_mid():
    core = _make_pnl_core(ZERO_FEES)
    assert core is not None
    with pytest.raises(ValueError):
        core.mark("nan", "0.01", 0, None)


def test_huge_integer_mid_stays_on_decimal():
    """``math.isfinite`` raises OverflowError for ints past the float range."""
    accelerated, reference = _trackers(ZERO_FEES)
    mid = 10**309
    accelerated.mark(mid, ts=0)
    reference.mark(mid, ts=0)
    _assert_same_tracker(accelerated, reference)
    assert accelerated._core is None


def test_position_past_i64_stays_on_the_decimal_ledger():
    """A fill past i64 stays on Decimal. The live tracker never attaches a core."""
    qty = 2**62
    accelerated, reference = _trackers(SCHEDULE)
    accelerated.mark(100.0, ts=0)
    reference.mark(100.0, ts=0)
    accelerated.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=100.0)
    reference.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=100.0)
    accelerated.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=102.0)
    reference.on_fill(_fill(Side.BUY, qty, 100), mid_ticks=102.0)
    _assert_same_tracker(accelerated, reference)
    assert accelerated.position == 2**63
    assert accelerated._core is None
    accelerated.mark(102.0, ts=1)
    reference.mark(102.0, ts=1)
    _assert_same_tracker(accelerated, reference)


def test_pnl_custom_decimal_context_live_path_matches_reference():
    """PnLTracker honours a non-default context. The live call does not use 28-digit Rust."""
    with localcontext() as ctx:
        ctx.prec = 6
        accelerated, reference = _trackers(SCHEDULE)
        accelerated.mark(10_000.5, ts=0)
        reference.mark(10_000.5, ts=0)
        accelerated.on_fill(_fill(Side.BUY, 100, 10_000), mid_ticks=10_000.5)
        reference.on_fill(_fill(Side.BUY, 100, 10_000), mid_ticks=10_000.5)
        _assert_same_tracker(accelerated, reference)
        assert accelerated._core is None


def test_nonfinite_borrow_seconds_stay_on_the_python_path():
    """Non-finite seconds stay on Decimal and do not hit Rust's parser."""
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
    """A narrowed, re-rounded, or untrapped context stays on Decimal."""
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
        assert borrowed.is_nan() and reference.is_nan()
    assert costs.cost_bps(one, seven) == python_cost_bps(one, seven)
    assert costs.cost_bps(one, seven) == Decimal("1428.571428571428571428571429")


def _python(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    app = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = app + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=app,
        env=env,
        check=False,
    )


def test_broken_costs_extension_import_is_not_swallowed():
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                raise ImportError("broken extension")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        import quant.costs
        """
    )
    ran = _python(script)
    assert ran.returncode != 0
    assert "broken extension" in ran.stderr


def test_partial_costs_extension_is_not_a_silent_python_fallback():
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util
        import types

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                return types.ModuleType("cockpit_quant")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        import quant.costs
        """
    )
    ran = _python(script)
    assert ran.returncode != 0
    assert "fee_for" in ran.stderr


def test_partial_pnl_extension_is_not_a_silent_python_fallback():
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util
        import types

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                module = types.ModuleType("cockpit_quant")
                for attr in ("fee_for", "borrow_cost", "cost_bps", "fee_for_many"):
                    setattr(module, attr, lambda *a, **k: None)
                return module
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        import quant.pnl
        """
    )
    ran = _python(script)
    assert ran.returncode != 0, ran.stderr
    assert "PnlCore" in ran.stderr


def test_missing_costs_extension_stays_on_python():
    script = textwrap.dedent(
        """
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return None
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        import quant.costs as costs
        import quant.pnl as pnl
        assert costs.COSTS_BACKEND == "python", costs.COSTS_BACKEND
        assert pnl.PNL_BACKEND == "python", pnl.PNL_BACKEND
        """
    )
    ran = _python(script)
    assert ran.returncode == 0, ran.stderr


def test_live_tracker_does_not_attach_a_core():
    tracker = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    assert tracker._core is None
    tracker.mark(10_000.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 100, 10_000), mid_ticks=10_000.0)
    assert tracker._core is None
    assert tracker.fills == 1


def test_live_methods_do_not_call_the_extension(monkeypatch):
    import cockpit_quant

    def _boom(*_args, **_kwargs):
        raise AssertionError("extension called")

    monkeypatch.setattr(cockpit_quant, "fee_for", _boom)
    monkeypatch.setattr(cockpit_quant, "borrow_cost", _boom)
    monkeypatch.setattr(cockpit_quant, "cost_bps", _boom)
    assert SCHEDULE.fee_for(
        side=Side.SELL, qty=100, price=Decimal("4.00"), liquidity=Liquidity.TAKER,
    ) == python_fee_for(
        SCHEDULE, side=Side.SELL, qty=100, price=Decimal("4.00"), liquidity=Liquidity.TAKER,
    )
    assert SCHEDULE.borrow_cost(short_notional=Decimal("1000"), seconds=2) == python_borrow_cost(
        SCHEDULE, short_notional=Decimal("1000"), seconds=2,
    )
    assert costs.cost_bps(Decimal("1"), Decimal("100")) == python_cost_bps(Decimal("1"), Decimal("100"))
    tracker = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    tracker.mark(10_000.0, ts=0)
    tracker.on_fill(_fill(Side.BUY, 100, 10_000), mid_ticks=10_000.0)
    assert tracker.fills == 1
