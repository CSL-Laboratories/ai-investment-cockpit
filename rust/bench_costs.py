#!/usr/bin/env python3
"""Time Rust costs and PnL against the Python Decimal reference.

The live paper loop marks and fills one event at a time. Those calls stay on
Decimal: formatting strings and parsing them back measured slower, including
``fee_for_many`` and ``pnl_replay``. Every row is informational: the shipped
rows time one function, so no ratio there can regress. The run fails only if
the shipped Decimal path attaches a ``PnlCore`` or calls into ``cockpit_quant``
while it is being timed, which is the regression the parity tests
``test_live_tracker_does_not_attach_a_core`` and
``test_live_methods_do_not_call_the_extension`` lock.

    python -m maturin build --release --manifest-path rust/cockpit-py/Cargo.toml
    python -m pip install --force-reinstall rust/target/wheels/cockpit_quant-*.whl
    python rust/bench_costs.py
"""
from __future__ import annotations

import sys
import time
from decimal import Decimal
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import cockpit_quant  # noqa: E402
from quant.costs import COSTS_BACKEND, FeeSchedule  # noqa: E402
from quant.events import Fill, Liquidity, PriceScale, Side  # noqa: E402
from quant.pnl import PNL_BACKEND, PnLTracker  # noqa: E402

FEE_N = 20_000
PNL_N = 5_000
PENNY = PriceScale(tick_size=Decimal("0.01"))
SCHEDULE = FeeSchedule()


def _fee_rows(n: int):
    rows = []
    for i in range(n):
        rows.append((
            Side.SELL if i % 3 == 0 else Side.BUY,
            100 + (i % 50),
            Decimal(1000 + (i % 700)) / Decimal("100"),
            Liquidity.MAKER if i % 2 == 0 else Liquidity.TAKER,
        ))
    return rows


def _shipped_fees(rows) -> None:
    for side, qty, price, liquidity in rows:
        SCHEDULE.fee_for(side=side, qty=qty, price=price, liquidity=liquidity)


def _rust_fee_batch(rows) -> None:
    payload = [
        (
            "sell" if side is Side.SELL else "buy",
            qty,
            format(price, "f"),
            "maker" if liquidity is Liquidity.MAKER else "taker",
        )
        for side, qty, price, liquidity in rows
    ]
    cockpit_quant.fee_for_many(
        format(SCHEDULE.maker_rebate_per_share, "f"),
        format(SCHEDULE.taker_fee_per_share, "f"),
        format(SCHEDULE.sec_fee_rate, "f"),
        format(SCHEDULE.taf_per_share, "f"),
        format(SCHEDULE.taf_cap, "f"),
        format(SCHEDULE.clearing_per_share, "f"),
        payload,
    )


def _pnl_script(n: int):
    steps = []
    mid = 10_000.0
    ts = 0
    for i in range(n):
        mid += ((i % 7) - 3) * 0.5
        ts += 1_000_000_000
        fill = None
        if i % 3 == 0:
            side = Side.BUY if i % 2 == 0 else Side.SELL
            fill = Fill(
                symbol="S", seq=i, exchange_ts=ts, recv_ts=ts, ingest_ts=ts,
                order_id="o", side=side, price_ticks=int(round(mid)), qty=100,
                liquidity=Liquidity.TAKER if i % 5 == 0 else Liquidity.MAKER,
            )
        steps.append((mid, ts, fill))
    return steps


def _extension_pnl(script) -> None:
    core = cockpit_quant.PnlCore(
        format(SCHEDULE.maker_rebate_per_share, "f"),
        format(SCHEDULE.taker_fee_per_share, "f"),
        format(SCHEDULE.sec_fee_rate, "f"),
        format(SCHEDULE.taf_per_share, "f"),
        format(SCHEDULE.taf_cap, "f"),
        format(SCHEDULE.clearing_per_share, "f"),
        format(SCHEDULE.borrow_rate_annual, "f"),
    )
    last_ts = None
    for mid, ts, fill in script:
        elapsed = None if last_ts is None else str((ts - last_ts) / 1_000_000_000)
        core.mark(str(mid), "0.01", ts, elapsed)
        last_ts = ts
        if fill is None:
            continue
        elapsed = str((fill.ingest_ts - last_ts) / 1_000_000_000)
        core.on_fill(
            fill.side is Side.SELL, fill.qty, fill.price_ticks,
            fill.liquidity is Liquidity.MAKER, fill.signed_qty, "0.01",
            fill.ingest_ts, str(mid), elapsed,
        )
        last_ts = fill.ingest_ts


def _rust_pnl_batch(script) -> None:
    rows = []
    last_ts = None
    for mid, ts, fill in script:
        elapsed = None if last_ts is None else str((ts - last_ts) / 1_000_000_000)
        rows.append(("m", str(mid), ts, elapsed, False, 0, 0, False, 0))
        last_ts = ts
        if fill is None:
            continue
        elapsed = str((fill.ingest_ts - last_ts) / 1_000_000_000)
        rows.append((
            "f", str(mid), fill.ingest_ts, elapsed, fill.side is Side.SELL, fill.qty,
            fill.price_ticks, fill.liquidity is Liquidity.MAKER, fill.signed_qty,
        ))
        last_ts = fill.ingest_ts
    cockpit_quant.pnl_replay(
        format(SCHEDULE.maker_rebate_per_share, "f"),
        format(SCHEDULE.taker_fee_per_share, "f"),
        format(SCHEDULE.sec_fee_rate, "f"),
        format(SCHEDULE.taf_per_share, "f"),
        format(SCHEDULE.taf_cap, "f"),
        format(SCHEDULE.clearing_per_share, "f"),
        format(SCHEDULE.borrow_rate_annual, "f"),
        "0.01",
        rows,
    )


def _python_pnl(script) -> None:
    tracker = PnLTracker(scale=PENNY, schedule=SCHEDULE)
    for mid, ts, fill in script:
        tracker.mark(mid, ts=ts)
        if fill is not None:
            tracker.on_fill(fill, mid_ticks=mid)


def _median(fn, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


_EXTENSION_SYMBOLS = ("fee_for", "borrow_cost", "cost_bps", "fee_for_many", "PnlCore", "pnl_replay")


class _ExtensionGuard:
    """Record every call into ``cockpit_quant`` while the shipped path is timed."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._saved: dict[str, object] = {}

    def __enter__(self):
        for name in _EXTENSION_SYMBOLS:
            real = getattr(cockpit_quant, name)
            self._saved[name] = real

            def shim(*args, _name=name, _real=real, **kwargs):
                self.calls.append(_name)
                return _real(*args, **kwargs)

            setattr(cockpit_quant, name, shim)
        return self

    def __exit__(self, *_exc) -> None:
        for name, real in self._saved.items():
            setattr(cockpit_quant, name, real)


def main() -> None:
    if COSTS_BACKEND != "rust" or PNL_BACKEND != "rust":
        print("cockpit_quant costs are not installed; build the release extension first")
        sys.exit(1)
    fee_rows = _fee_rows(FEE_N)
    script = _pnl_script(PNL_N)
    # One untimed pass so construction and imports are not in the samples.
    _shipped_fees(fee_rows[:8])
    _rust_fee_batch(fee_rows[:8])
    _python_pnl(script[:8])
    _extension_pnl(script[:8])
    _rust_pnl_batch(script[:8])
    if PnLTracker(scale=PENNY, schedule=SCHEDULE)._core is not None:
        sys.exit("per-call PnL attached a native core")

    with _ExtensionGuard() as guard:
        shipped_fee = _median(lambda: _shipped_fees(fee_rows))
        shipped_pnl = _median(lambda: _python_pnl(script))
        if PnLTracker(scale=PENNY, schedule=SCHEDULE)._core is not None:
            sys.exit("per-call PnL attached a native core")
    if guard.calls:
        sys.exit(
            "shipped Decimal path called the extension: " + ", ".join(sorted(set(guard.calls)))
        )
    batch_fee = _median(lambda: _rust_fee_batch(fee_rows))
    ext_pnl = _median(lambda: _extension_pnl(script))
    batch_pnl = _median(lambda: _rust_pnl_batch(script))

    def row(name: str, shipped: float, extension: float | None = None) -> str:
        if extension is None:
            return f"| {name} | {shipped * 1e3:.1f} ms | shipped | informational |"
        ratio = shipped / extension if extension else float("inf")
        return (
            f"| {name} | {shipped * 1e3:.1f} ms | {extension * 1e3:.1f} ms |"
            f" {ratio:.2f}x informational |"
        )

    print(f"fees={FEE_N}  pnl steps={PNL_N}  (median of 5; no row is a gate)")
    print("| workload | Decimal (shipped) | extension | Decimal/extension |")
    print("|---|---:|---:|---:|")
    print(row(f"per-call fee_for ({FEE_N})", shipped_fee))
    print(row(f"fee_for_many, not shipped ({FEE_N})", shipped_fee, batch_fee))
    print(row(f"per-call PnL ({PNL_N})", shipped_pnl))
    print(row(f"per-call PnL extension, not shipped ({PNL_N})", shipped_pnl, ext_pnl))
    print(row(f"pnl_replay, not shipped ({PNL_N})", shipped_pnl, batch_pnl))
    print("exit 0: the shipped path attached no core and made no extension call while timed")


if __name__ == "__main__":
    main()
