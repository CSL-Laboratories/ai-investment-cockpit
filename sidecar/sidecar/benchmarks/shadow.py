"""Paper-shadow marks for the three benchmarks.

Each close appends hypothetical weights and P&L. The ledger is not an order
log. ``orders_submitted`` on every row is 0.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sidecar.benchmarks.book import BENCHMARK_NAMES, simulate_benchmark, symbols_for
from sidecar.benchmarks.w25 import long_only
from sidecar.chainlog import ChainLog
from sidecar.harness.backtest import Bar

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "artifacts" / "benchmark_shadow.jsonl"


def _alpaca_weights(name: str, result: dict) -> dict[str, float]:
    raw = {symbol: float(value) for symbol, value in result["last_targets"].items()}
    if name != "w25":
        return raw
    return long_only([raw])[0]


def append_shadow(
    grouped: dict[str, list[Bar]],
    *,
    ledger_path: Path = DEFAULT_LEDGER,
    names: tuple[str, ...] = BENCHMARK_NAMES,
) -> dict:
    """Append one hypothetical mark per benchmark. Does not place an order."""
    log = ChainLog(ledger_path)
    available = tuple(sorted(grouped))
    marks = []
    for name in names:
        symbols, missing = symbols_for(name, available)
        result = simulate_benchmark(name, grouped, symbols=symbols)
        curve = result["curve"]
        day = curve[-1][0]
        equity = curve[-1][1]
        if len(curve) >= 2 and curve[-2][1] > 0:
            day_return = (equity - curve[-2][1]) / curve[-2][1]
        else:
            day_return = equity - equity
        record = {
            "kind": "shadow_mark",
            "strategy": name,
            "strategy_id": result["strategy_id"],
            "session_date": day.isoformat(),
            "symbols": list(result["symbols"]),
            "missing_symbols": missing,
            "target_weights": result["last_targets"],
            "alpaca_weights": {
                symbol: format(value, ".10g") for symbol, value in _alpaca_weights(name, result).items()
            },
            "hypothetical_equity": format(equity, "f"),
            "hypothetical_return": format(day_return, "f"),
            "orders_submitted": 0,
            "live_orders": False,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        log.append(record)
        marks.append(
            {
                "strategy": name,
                "session_date": day.isoformat(),
                "hypothetical_equity": format(equity, "f"),
                "orders_submitted": 0,
            }
        )
    return {
        "orders_submitted": 0,
        "live_orders": False,
        "ledger": str(ledger_path),
        "marks": marks,
    }
