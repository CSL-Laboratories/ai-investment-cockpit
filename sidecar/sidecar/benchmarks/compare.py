"""Compare the current sidecar book with the three benchmark ports.

All four read one bar file. The current book is the harness inverse-vol rule.
Benchmark orders are not submitted. Published QuantJourney figures are quoted
beside the rows and are not this run.
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path

from sidecar.benchmarks.book import COST_BPS, STARTING_EQUITY, simulate_benchmark, symbols_for
from sidecar.benchmarks.o17 import PUBLISHED as O17_PUBLISHED
from sidecar.benchmarks.o17 import PUBLISHED_SYMBOLS as O17_SYMBOLS
from sidecar.benchmarks.w18 import PUBLISHED as W18_PUBLISHED
from sidecar.benchmarks.w18 import PUBLISHED_SYMBOLS as W18_SYMBOLS
from sidecar.benchmarks.w25 import PROXY_SYMBOLS, PUBLISHED as W25_PUBLISHED, SUBSTITUTION_NOTE
from sidecar.evalstats.report import evaluate
from sidecar.harness.backtest import (
    PRIMARY_LOOKBACK,
    STRATEGY_ID,
    load_bars,
    _simulate,
)
from sidecar.jsonutil import write_json
from sidecar.tca.shortfall import summarize_fills

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BARS = ROOT / "fixtures" / "etf_daily.csv"
DEFAULT_OUT = ROOT / "artifacts" / "benchmark_comparison.md"
FILL_NOTE = (
    "Signal dates follow the source. Trades fill at the next adjusted open with "
    "1 bp one-way, the same cost model as the current harness book. QuantJourney "
    "marks a weight rebalance at the signal close, so this book leaves the "
    "overnight gap on the old shares. O17's source slippage is 3 bp plus "
    "$0.005 per share with a $1 minimum; that schedule is not charged here."
)


def _cagr(start: Decimal, end: Decimal, periods: int) -> float:
    if start <= 0 or end <= 0 or periods < 1:
        return float("nan")
    return float((end / start) ** (Decimal(252) / Decimal(periods)) - 1)


def _turnover(result: dict) -> float:
    traded = Decimal(0)
    for fill in result["fills"]:
        traded += Decimal(fill["qty"]) * Decimal(fill["fill_price"])
    curve = result["curve"]
    if not curve or traded == 0:
        return 0.0
    mean_equity = sum((value for _day, value in curve), Decimal(0)) / Decimal(len(curve))
    years = Decimal(max(len(curve) - 1, 1)) / Decimal(252)
    if mean_equity <= 0 or years <= 0:
        return float("nan")
    return float(traded / mean_equity / years)


def _row(name: str, strategy_id: str, symbols: list[str], result: dict, *, n_trials: int) -> dict:
    curve = result["curve"]
    returns = [float(value) for _day, value in result["returns"]]
    start = Decimal(str(STARTING_EQUITY))
    end = curve[-1][1] if curve else start
    periods = max(len(curve) - 1, 1)
    stats = None
    stats_error = None
    try:
        if len(returns) >= 8:
            stats = evaluate(returns, n_trials=n_trials, bootstrap_reps=50, n_slices=8, seed=0)
    except Exception as exc:
        stats_error = str(exc)
    summary = summarize_fills(result["fills"]) if result["fills"] else {"status": "no_fills", "n_fills": 0}
    sharpe = None if stats is None else float(stats["sharpe_annualized"])
    drawdown = None if stats is None else float(stats["drawdown"]["max_drawdown"])
    return {
        "name": name,
        "strategy_id": strategy_id,
        "symbols": symbols,
        "sessions": len(curve),
        "start": curve[0][0].isoformat() if curve else None,
        "end": curve[-1][0].isoformat() if curve else None,
        "ending_equity": float(end),
        "cagr": _cagr(start, end, periods),
        "sharpe_annualized": sharpe,
        "max_drawdown": drawdown,
        "turnover_per_year": _turnover(result),
        "rebalances": result["rebalances"],
        "orders_submitted": 0,
        "tca": summary,
        "evalstats_status": None if stats is None else stats["status"],
        "evalstats_error": stats_error,
        "threshold_status": "not_frozen",
    }


def _fmt_pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def _fmt_num(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:.3f}"


def _fmt_equity(value: float) -> str:
    return f"{value:.2f}"


def render_markdown(document: dict) -> str:
    lines = [
        "# Benchmark comparison",
        "",
        "Local daily-bar book. `orders_submitted` is 0 on every row. "
        "This is not a reproduction of the published QuantJourney figures.",
        "",
        f"Bars: `{document['bars_path']}` ({document['start']} to {document['end']}).",
        "",
        FILL_NOTE,
        "",
        "| Strategy | Symbols | CAGR | Sharpe | Max drawdown | Turnover / year | Median shortfall bps | Ending equity |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in document["rows"]:
        tca = row["tca"]
        shortfall = tca.get("median_implementation_shortfall_bps")
        if shortfall is None:
            shortfall_text = "n/a"
        else:
            shortfall_text = f"{float(shortfall):.2f}"
        lines.append(
            "| {name} | {symbols} | {cagr} | {sharpe} | {dd} | {turn} | {cost} | {equity} |".format(
                name=row["name"],
                symbols=",".join(row["symbols"]),
                cagr=_fmt_pct(row["cagr"]),
                sharpe=_fmt_num(row["sharpe_annualized"]),
                dd=_fmt_pct(None if row["max_drawdown"] is None else -row["max_drawdown"]),
                turn=_fmt_num(row["turnover_per_year"]),
                cost=shortfall_text,
                equity=_fmt_equity(row["ending_equity"]),
            )
        )
    lines.extend(
        [
            "",
            "## Published figures, not this run",
            "",
            "| Strategy | Published CAGR | Published Sharpe | Published max drawdown | Source window |",
            "|---|---:|---:|---:|---|",
            f"| W18 | {W18_PUBLISHED['cagr'] * 100:.2f}% | {W18_PUBLISHED['sharpe']:.2f} | {W18_PUBLISHED['max_drawdown'] * 100:.1f}% | 2007-01-03 to 2026-01-01, eight ETFs |",
            f"| O17 | {O17_PUBLISHED['cagr'] * 100:.2f}% | {O17_PUBLISHED['sharpe']:.2f} | {O17_PUBLISHED['max_drawdown'] * 100:.1f}% | 2007-01-03 to 2026-01-01, eight ETFs |",
            f"| W25 | {W25_PUBLISHED['cagr'] * 100:.2f}% | {W25_PUBLISHED['sharpe']:.2f} | {W25_PUBLISHED['max_drawdown'] * 100:.1f}% | 2019-05-06 to 2026-01-01, six continuous futures |",
            "",
            "## Universe",
            "",
        ]
    )
    lines.extend(f"- {note}" for note in document["notes"])
    lines.extend(["", "`threshold_status` is `not_frozen`. These rows are not an edge.", ""])
    return "\n".join(lines)


def run_comparison(
    bars_path: Path = DEFAULT_BARS,
    *,
    out: Path = DEFAULT_OUT,
    n_trials: int = 4,
) -> dict:
    grouped = load_bars(bars_path)
    available = tuple(sorted(grouped))
    current_symbols = available
    current = _simulate(
        {symbol: grouped[symbol] for symbol in current_symbols},
        lookback=PRIMARY_LOOKBACK,
        rebalance_every=5,
        starting_equity=Decimal(str(int(STARTING_EQUITY))),
        cost_bps=Decimal(str(int(COST_BPS))),
        target_vol=Decimal("0.01"),
        kelly_frac=Decimal("0.25"),
        collect_fills=True,
    )
    current["orders_submitted"] = 0
    rows = [
        _row("inverse_vol", STRATEGY_ID, list(current_symbols), current, n_trials=n_trials),
    ]
    notes = [
        "inverse_vol is the current harness rule: 20-day fractional Kelly, 1% daily vol target, rebalance every 5 sessions, long only, SPY/QQQ/IWM when those bars exist.",
        FILL_NOTE,
        SUBSTITUTION_NOTE,
        (
            "W18 and O17 are published on "
            + ", ".join(W18_SYMBOLS)
            + ". O17 uses the same list."
        ),
    ]
    if list(W18_SYMBOLS) != list(O17_SYMBOLS):
        notes.append("O17 published symbols differ from W18; see the source files.")
    for name in ("w18", "o17", "w25"):
        symbols, missing = symbols_for(name, available)
        result = simulate_benchmark(name, grouped, symbols=symbols)
        label = name
        if name == "w25":
            label = "w25_long_short"
        rows.append(_row(label, result["strategy_id"], list(result["symbols"]), result, n_trials=n_trials))
        if missing and name in {"w18", "o17"} and list(symbols) == list(available):
            notes.append(
                f"{name} was scored on {', '.join(symbols)} because the bar file does not contain "
                f"{', '.join(missing)}. That is the cached panel, not the published eight-ETF universe."
            )
        elif missing:
            notes.append(
                f"{name} is missing {', '.join(missing)} in this bar file. "
                f"Scored symbols: {', '.join(result['symbols'])}."
            )
        else:
            notes.append(f"{name} scored on its full requested list: {', '.join(result['symbols'])}.")
    if any(row["name"] == "w25_long_short" for row in rows):
        from sidecar.benchmarks.book import aligned_panel, business_month_end_flags, run_weight_book
        from sidecar.benchmarks.w25 import STRATEGY_ID as W25_ID
        from sidecar.benchmarks.w25 import long_only, target_weights

        symbols, _missing = symbols_for("w25", available)
        dates, closes, opens = aligned_panel(grouped, symbols)
        cash_targets = long_only(target_weights(closes))
        cash_book = run_weight_book(dates, closes, opens, cash_targets, business_month_end_flags(dates))
        cash_book["strategy_id"] = W25_ID + ".long_only_cash"
        rows.append(
            _row(
                "w25_long_only_cash",
                cash_book["strategy_id"],
                list(symbols),
                cash_book,
                n_trials=n_trials,
            )
        )
        notes.append(
            "w25_long_only_cash is the same signal with negative weights set to zero. "
            "Shorts are not replaced by an inverse ETF. It is not a fifth published strategy."
        )
    dates = sorted({bar.day for rows_ in grouped.values() for bar in rows_})
    document = {
        "schema_version": "sidecar.benchmark_comparison.v1",
        "orders_submitted": 0,
        "threshold_status": "not_frozen",
        "bars_path": str(bars_path),
        "start": dates[0].isoformat() if dates else None,
        "end": dates[-1].isoformat() if dates else None,
        "cost_bps": COST_BPS,
        "starting_equity": STARTING_EQUITY,
        "proxy_symbols": list(PROXY_SYMBOLS),
        "rows": rows,
        "notes": notes,
        "yield_claim": False,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(document), encoding="utf-8")
    write_json(out.with_suffix(".json"), _json_ready(document))
    return document


def _json_ready(document: dict) -> dict:
    ready = dict(document)
    ready_rows = []
    for row in document["rows"]:
        item = dict(row)
        for key in ("cagr", "sharpe_annualized", "max_drawdown", "turnover_per_year", "ending_equity"):
            value = item[key]
            item[key] = None if value is None or not math.isfinite(value) else value
        ready_rows.append(item)
    ready["rows"] = ready_rows
    return ready


def main() -> None:
    document = run_comparison()
    print(f"wrote {DEFAULT_OUT}")
    for row in document["rows"]:
        print(row["name"], row["ending_equity"], row["cagr"], row["sharpe_annualized"])
