"""Offline options report. orders_submitted is always 0."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from sidecar.evalstats.core import EvalStatsError
from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.report import evaluate
from sidecar.harness.backtest import DEFAULT_BARS, load_bars
from sidecar.jsonutil import write_json
from sidecar.options.config import PRIMARY, trial_configs
from sidecar.options.simulate import simulate_book
from sidecar.tca.shortfall import summarize_fills

ROOT = Path(__file__).resolve().parents[2]


def _aligned_bars(path: Path) -> tuple[dict[str, list[float]], list]:
    grouped = load_bars(path)
    symbols = [symbol for symbol in ("SPY", "QQQ", "IWM") if symbol in grouped]
    if not symbols:
        raise ValueError("bar file has none of SPY, QQQ, IWM")
    maps = {symbol: {bar.day: float(bar.adj_close) for bar in grouped[symbol]} for symbol in symbols}
    dates = [
        day
        for day in sorted(maps[symbols[0]])
        if all(day in maps[symbol] and maps[symbol][day] > 0 for symbol in symbols)
    ]
    closes = {symbol: [maps[symbol][day] for day in dates] for symbol in symbols}
    return closes, dates


def _slice_count(width: int) -> int | None:
    for candidate in (16, 8, 4):
        if width >= candidate:
            return candidate
    return None


def build_report(bars_path: Path, ledger_path: Path) -> dict:
    closes, dates = _aligned_bars(bars_path)
    ledger = TrialLedger(ledger_path)
    results = []
    for config in trial_configs():
        result = simulate_book(closes, dates, config)
        ledger.append(kind="strategy_config", name=config.name, config=result["config"])
        results.append(result)
    if results[0]["name"] != PRIMARY.name:
        raise RuntimeError("primary config was not the first trial")
    primary = results[0]
    width = min(len(result["returns"]) for result in results)
    matrix = [result["returns"][-width:] for result in results] if width else None
    n_slices = _slice_count(width) if matrix and len(matrix) >= 2 else None
    n_trials = max(ledger.distinct_config_count(), 1)
    benchmark = primary["spy_returns"][-width:] if width else None
    try:
        stats = evaluate(
            primary["returns"],
            n_trials=n_trials,
            benchmark=benchmark,
            trial_matrix=matrix if n_slices else None,
            n_slices=n_slices or 16,
            periods_per_year=252,
        )
    except EvalStatsError:
        stats = evaluate(primary["returns"], n_trials=n_trials, periods_per_year=252)
    tca = summarize_fills(primary["fills"]) if primary["fills"] else summarize_fills([])
    ending = primary["ending_equity"]
    beats_cash = False
    try:
        beats_cash = float(ending) > float(primary["starting_equity"])
    except ValueError:
        beats_cash = False
    trials = []
    for result in results:
        trials.append(
            {
                "name": result["name"],
                "kind": result["kind"],
                "config_hash": result["config_hash"],
                "ending_equity": result["ending_equity"],
                "n_opens": result["n_opens"],
                "n_signals": result["n_signals"],
                "closes": result["closes"],
                "n_fills": len(result["fills"]),
                "n_return_days": result["n_return_days"],
                "primary": result["name"] == PRIMARY.name,
            }
        )
    digest = hashlib.sha256(Path(bars_path).read_bytes()).hexdigest()
    return {
        "schema_version": "sidecar.options_eval.v1",
        "orders_submitted": 0,
        "live_trading": False,
        "historical_options_tape": False,
        "pricing": "black_scholes_r0_iv_equals_trailing_60d_realized",
        "bars_path": str(bars_path),
        "bars_sha256": digest,
        "primary_name": PRIMARY.name,
        "primary_config_hash": primary["config_hash"],
        "starting_equity": primary["starting_equity"],
        "ending_equity": ending,
        "beats_cash": beats_cash,
        "n_opens": primary["n_opens"],
        "n_signals": primary["n_signals"],
        "closes": primary["closes"],
        "trials": trials,
        "evalstats": stats,
        "tca": tca,
        "ledger_distinct_configs": ledger.distinct_config_count(),
        "ledger_head_hash": ledger.head_hash(),
        "threshold_status": "not_frozen",
        "caveats": [
            "No historical OPRA chain is in this tree. Premiums are Black-Scholes with zero rates and zero dividends.",
            "The volatility input is the trailing 60-session close-to-close realized vol. It is not an implied vol, and the IV-RV spread is 60-day realized minus 10-day realized.",
            "IV rank is the percentile of that proxy against the prior 252 sessions of the same proxy.",
            "Fills sell the bid and buy the ask. The full spread on each leg is max($0.05, 8% of the model mid), then rounded to the penny.",
            "The signal uses that session's close. The spread is filled at the next session's close. Strikes are chosen on the signal day.",
            "Take-profit and stop are judged on the model mid after the entry session. The close itself still pays the bid-ask.",
            "Underlying closes are adjusted daily bars, not a point-in-time options tape.",
            "The primary config was fixed before this run. Other rows are trials. A better trial does not replace the primary.",
            "beats_cash compares ending equity with the starting cash. It is not a qualify decision. threshold_status is not_frozen.",
            "orders_submitted is 0. This file does not place an order.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic defined-risk options backtest. Does not trade.")
    parser.add_argument("--bars", type=Path, default=DEFAULT_BARS)
    parser.add_argument("--out", type=Path, default=ROOT / "examples" / "options_eval.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "examples" / "options_eval.ledger.jsonl")
    args = parser.parse_args(argv)
    report = build_report(args.bars, args.ledger)
    write_json(args.out, report)
    print(f"wrote {args.out}")
    print(
        "ending_equity",
        report["ending_equity"],
        "n_opens",
        report["n_opens"],
        "psr",
        report["evalstats"].get("psr"),
        "orders_submitted",
        report["orders_submitted"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
