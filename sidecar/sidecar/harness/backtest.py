"""Offline replay of a declared sizing rule on daily bars.

Signal at the close of day t, fill at the next session's adjusted open. The primary
lookback is fixed. Other lookbacks are recorded as trials for PBO and the deflated Sharpe;
they are not selected after the fact.

This book is local. Nothing is sent to a broker.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.report import evaluate
from sidecar.jsonutil import sha256_canonical, validate, write_json
from sidecar.sizer.kelly import suggest_notional
from sidecar.tca.impact import fit_impact
from sidecar.tca.shortfall import summarize_fills

SCHEMA = Path(__file__).with_name("daily_report.schema.json")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BARS = ROOT / "fixtures" / "etf_daily.csv"
STRATEGY_ID = "sidecar.inverse_vol_fractional_kelly.v1"
PRIMARY_LOOKBACK = 20
TRIAL_LOOKBACKS = (10, 20, 40, 60)


@dataclass(frozen=True)
class Bar:
    day: date
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: Decimal

    @property
    def open_adj(self) -> Decimal:
        return self.open * self.adj_close / self.close

    @property
    def close_adj(self) -> Decimal:
        return self.adj_close


def load_bars(path: Path) -> dict[str, list[Bar]]:
    text = Path(path).read_text(encoding="utf-8").splitlines()
    if not text or text[0].split(",")[0] != "date":
        raise ValueError("bars CSV must start with a header")
    grouped: dict[str, list[Bar]] = {}
    seen: set[tuple[str, str]] = set()
    for line_no, line in enumerate(text[1:], start=2):
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) != 8:
            raise ValueError(f"line {line_no} does not have 8 columns")
        day_text, symbol, open_, high, low, close, adj, volume = parts
        key = (symbol, day_text)
        if key in seen:
            raise ValueError(f"duplicate bar {symbol} {day_text}")
        seen.add(key)
        bar = Bar(
            day=date.fromisoformat(day_text),
            symbol=symbol,
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            adj_close=Decimal(adj),
            volume=Decimal(volume),
        )
        if min(bar.open, bar.high, bar.low, bar.close, bar.adj_close, bar.volume) <= 0:
            raise ValueError(f"non-positive price on {symbol} {day_text}")
        grouped.setdefault(symbol, []).append(bar)
    for symbol, rows in grouped.items():
        rows.sort(key=lambda item: item.day)
        for prev, nxt in zip(rows, rows[1:], strict=False):
            if nxt.day <= prev.day:
                raise ValueError(f"dates not strictly increasing for {symbol}")
    if len(grouped) < 1:
        raise ValueError("no bars")
    return grouped


def _mean_std(values: list[Decimal]) -> tuple[Decimal, Decimal]:
    count = Decimal(len(values))
    mean = sum(values, Decimal(0)) / count
    variance = sum((item - mean) ** 2 for item in values) / (count - 1)
    return mean, variance.sqrt()


def _targets(
    equity: Decimal,
    symbols: list[str],
    history: dict[str, list[Decimal]],
    *,
    lookback: int,
    target_vol: Decimal,
    kelly_frac: Decimal,
) -> tuple[dict[str, Decimal], Counter]:
    raw: dict[str, Decimal] = {}
    bindings: Counter = Counter()
    for symbol in symbols:
        window = history[symbol][-lookback:]
        edge, vol = _mean_std(window)
        result = suggest_notional(
            edge, vol, target_vol, kelly_frac, equity, equity, max_weight=Decimal("1")
        )
        raw[symbol] = result.suggested
        bindings[result.binding] += 1
    total = sum(raw.values(), Decimal(0))
    if total > equity and total > 0:
        scale = equity / total
        raw = {symbol: notional * scale for symbol, notional in raw.items()}
        bindings["portfolio_scale"] += 1
    return raw, bindings


def _scale_to_cash(
    cash: Decimal,
    shares: dict[str, Decimal],
    opens: dict[str, Decimal],
    targets: dict[str, Decimal],
    cost_rate: Decimal,
) -> Decimal:
    """Largest scale in [0, 1] that leaves non-negative cash after costs."""

    def cash_after(scale: Decimal) -> Decimal:
        remaining = cash
        for symbol, target in targets.items():
            old = shares[symbol] * opens[symbol]
            trade = target * scale - old
            remaining -= trade + abs(trade) * cost_rate
        return remaining

    if cash_after(Decimal(1)) >= 0:
        return Decimal(1)
    lo = Decimal(0)
    hi = Decimal(1)
    best = Decimal(0)
    for _ in range(48):
        mid = (lo + hi) / 2
        if cash_after(mid) >= 0:
            best = mid
            lo = mid
        else:
            hi = mid
    return best


def _simulate(
    grouped: dict[str, list[Bar]],
    *,
    lookback: int,
    rebalance_every: int,
    starting_equity: Decimal,
    cost_bps: Decimal,
    target_vol: Decimal,
    kelly_frac: Decimal,
    collect_fills: bool,
) -> dict:
    symbols = sorted(grouped)
    by_day: dict[str, dict[date, Bar]] = {
        symbol: {bar.day: bar for bar in rows} for symbol, rows in grouped.items()
    }
    common = sorted(set.intersection(*(set(bars) for bars in by_day.values())))
    if len(common) <= lookback + 2:
        raise ValueError("not enough common sessions for the lookback")
    cash = starting_equity
    shares = {symbol: Decimal(0) for symbol in symbols}
    curve: list[tuple[date, Decimal]] = []
    fills: list[dict] = []
    bindings: Counter = Counter()
    rebalances = 0
    gross_samples: list[Decimal] = []
    pending: dict[str, Decimal] | None = None
    signal_closes: dict[str, Decimal] = {}
    signal_sigma: dict[str, Decimal] = {}
    signal_adv: dict[str, Decimal] = {}
    cost_rate = cost_bps / Decimal(10000)
    close_history = {symbol: [] for symbol in symbols}

    for index, day in enumerate(common):
        bars = {symbol: by_day[symbol][day] for symbol in symbols}
        if pending is not None:
            opens = {symbol: bars[symbol].open_adj for symbol in symbols}
            scale = _scale_to_cash(cash, shares, opens, pending, cost_rate)
            for symbol in symbols:
                old = shares[symbol] * opens[symbol]
                target = pending[symbol] * scale
                trade = target - old
                cost = abs(trade) * cost_rate
                cash -= trade + cost
                shares[symbol] = target / opens[symbol]
                if collect_fills and abs(trade) > Decimal("0.01"):
                    fills.append(
                        {
                            "symbol": symbol,
                            "side": "buy" if trade > 0 else "sell",
                            "qty": format(abs(shares[symbol] - (old / opens[symbol])), "f"),
                            "decision_price": format(signal_closes[symbol], "f"),
                            "arrival_mid": format(opens[symbol], "f"),
                            "fill_price": format(opens[symbol], "f"),
                            "fee": format(cost, "f"),
                            "adv": format(signal_adv[symbol], "f"),
                            "sigma": format(signal_sigma[symbol], "f"),
                            "predicted_cost_bps": format(cost_bps, "f"),
                            "venue_label": "simulated_next_open",
                        }
                    )
            if cash < 0:
                raise ValueError("trade plan drove cash negative")
            pending = None
        equity = cash + sum(
            (shares[symbol] * bars[symbol].close_adj for symbol in symbols), Decimal(0)
        )
        if equity <= 0:
            raise ValueError("equity became non-positive; refusing to continue the path")
        curve.append((day, equity))
        for symbol in symbols:
            close_history[symbol].append(bars[symbol].close_adj)
        if index + 1 >= len(common):
            continue
        if len(close_history[symbols[0]]) <= lookback:
            continue
        if (index - lookback) % rebalance_every != 0:
            continue
        returns = {}
        adv = {}
        for symbol in symbols:
            levels = close_history[symbol]
            window = [
                levels[-lookback + offset] / levels[-lookback + offset - 1] - 1
                for offset in range(lookback)
            ]
            returns[symbol] = window
            volumes = [
                by_day[symbol][session].volume
                for session in common[index - lookback + 1 : index + 1]
            ]
            adv[symbol] = sum(volumes, Decimal(0)) / Decimal(len(volumes))
        targets, step_bindings = _targets(
            equity,
            symbols,
            returns,
            lookback=lookback,
            target_vol=target_vol,
            kelly_frac=kelly_frac,
        )
        bindings.update(step_bindings)
        rebalances += 1
        gross_samples.append(sum(targets.values(), Decimal(0)) / equity)
        pending = targets
        for symbol in symbols:
            _edge, sigma = _mean_std(returns[symbol])
            signal_closes[symbol] = bars[symbol].close_adj
            signal_sigma[symbol] = sigma
            signal_adv[symbol] = adv[symbol]
    returns_out = []
    for prev, nxt in zip(curve, curve[1:], strict=False):
        returns_out.append((nxt[0], (nxt[1] - prev[1]) / prev[1]))
    start = 0
    while start < len(returns_out) and returns_out[start][1] == 0:
        start += 1
    return {
        "curve": curve,
        "returns": returns_out[start:],
        "fills": fills,
        "bindings": bindings,
        "rebalances": rebalances,
        "mean_gross": (
            sum(gross_samples, Decimal(0)) / Decimal(len(gross_samples)) if gross_samples else Decimal(0)
        ),
        "shares": dict(shares),
        "cash": cash,
    }


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _spy_benchmark(rows: list[Bar] | None, days: list[date]) -> list[float] | None:
    """Same-day SPY total-return moves aligned to the strategy's mark dates.

    This is a realized beta input, not a signal. A missing session drops the benchmark
    rather than inventing a return.
    """
    if not rows:
        return None
    levels = {bar.day: bar.close_adj for bar in rows}
    ordered = sorted(levels)
    change = {
        nxt: levels[nxt] / levels[prev] - 1
        for prev, nxt in zip(ordered, ordered[1:], strict=False)
    }
    if any(day not in change for day in days):
        return None
    return [float(change[day]) for day in days]


def run_backtest(
    bars_path: Path = DEFAULT_BARS,
    *,
    out: Path | None = None,
    ledger_path: Path | None = None,
    starting_equity: Decimal = Decimal("100000"),
    cost_bps: Decimal = Decimal("1"),
    target_vol: Decimal = Decimal("0.01"),
    kelly_frac: Decimal = Decimal("0.25"),
    rebalance_every: int = 5,
    bootstrap_reps: int = 400,
    n_slices: int = 8,
    seed: int = 0,
) -> dict:
    grouped = load_bars(bars_path)
    digest = hashlib.sha256(Path(bars_path).read_bytes()).hexdigest()
    if ledger_path is None:
        if out is None:
            raise ValueError("pass out or ledger_path so the trial ledger has a home")
        ledger_path = out.with_suffix(".ledger.jsonl")
    ledger = TrialLedger(ledger_path)
    paths = {}
    for lookback in TRIAL_LOOKBACKS:
        config = {
            "strategy_id": STRATEGY_ID,
            "lookback": lookback,
            "rebalance_every": rebalance_every,
            "cost_bps": format(cost_bps, "f"),
            "target_vol": format(target_vol, "f"),
            "kelly_frac": format(kelly_frac, "f"),
            "primary": lookback == PRIMARY_LOOKBACK,
        }
        ledger.append(
            kind="parameter_set",
            name=f"{STRATEGY_ID}:lookback={lookback}",
            config=config,
            recorded_at=datetime.now(timezone.utc),
        )
        paths[lookback] = _simulate(
            grouped,
            lookback=lookback,
            rebalance_every=rebalance_every,
            starting_equity=starting_equity,
            cost_bps=cost_bps,
            target_vol=target_vol,
            kelly_frac=kelly_frac,
            collect_fills=lookback == PRIMARY_LOOKBACK,
        )
    primary = paths[PRIMARY_LOOKBACK]
    by_date = {
        lookback: {day: value for day, value in paths[lookback]["returns"]}
        for lookback in TRIAL_LOOKBACKS
    }
    common_dates = sorted(set.intersection(*(set(series) for series in by_date.values())))
    matrix = [
        [float(by_date[lookback][day]) for day in common_dates] for lookback in TRIAL_LOOKBACKS
    ]
    primary_returns = [float(value) for _day, value in primary["returns"]]
    benchmark = _spy_benchmark(grouped.get("SPY"), [day for day, _value in primary["returns"]])
    n_trials = ledger.distinct_config_count()
    stats = evaluate(
        primary_returns,
        n_trials,
        benchmark,
        trial_matrix=matrix,
        n_slices=n_slices,
        bootstrap_reps=bootstrap_reps,
        seed=seed,
    )
    summary = summarize_fills(primary["fills"])
    if summary.get("fills_with_execution_dispersion", 0) >= 8:
        impact = fit_impact(primary["fills"])
    else:
        impact = {
            "schema_version": "sidecar.cost_params.v1",
            "specification": "almgren_linear_v1",
            "status": "insufficient_sample",
            "n_fills": summary.get("n_fills", 0),
            "recommended": None,
            "does_not_replace_fee_schedule": True,
            "paper_venue_note": (
                "Fills are the next adjusted open, so arrival and fill match. There is no "
                "intraday execution dispersion to identify temporary impact. Implementation "
                "shortfall versus the decision close is the overnight gap plus the flat fee."
            ),
        }
    report = {
        "schema_version": "sidecar.daily_report.v1",
        "mode": "offline_backtest",
        "strategy_id": STRATEGY_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bars_path": _display_path(Path(bars_path)),
        "bars_sha256": digest,
        "orders_submitted": 0,
        "broker_writes": False,
        "primary_config": {
            "lookback": PRIMARY_LOOKBACK,
            "rebalance_every": rebalance_every,
            "cost_bps": format(cost_bps, "f"),
            "target_vol": format(target_vol, "f"),
            "kelly_frac": format(kelly_frac, "f"),
            "starting_equity": format(starting_equity, "f"),
            "config_hash": sha256_canonical(
                {
                    "strategy_id": STRATEGY_ID,
                    "lookback": PRIMARY_LOOKBACK,
                    "rebalance_every": rebalance_every,
                    "cost_bps": format(cost_bps, "f"),
                    "target_vol": format(target_vol, "f"),
                    "kelly_frac": format(kelly_frac, "f"),
                    "primary": True,
                }
            ),
        },
        "caveats": [
            "Simulated next-open fills on Yahoo Finance adjusted daily bars. Not exchange prints.",
            "Adjustment uses the download-time backward factor recorded in fixtures/SOURCE.json.",
            "Universe is SPY, QQQ, and IWM, chosen because those ETFs existed for the whole window. This is not a survivorship-free stock universe.",
            "Primary lookback is fixed at 20 sessions. Lookbacks 10, 40, and 60 are trials for PBO and DSR only and were not used to pick the reported path.",
            "No numeric threshold in this sidecar is a frozen qualify gate.",
            "The harness submits no broker orders. orders_submitted is 0.",
            "The flat cost is an assumption. Predicted-minus-realized cost is that assumption minus the overnight gap, not a measured live impact.",
            "Trailing mean return is a naive edge input to the sizer, not a validated forecast.",
        ],
        "equity_curve": [
            {"date": day.isoformat(), "equity": format(equity, "f")} for day, equity in primary["curve"]
        ],
        "evalstats": stats,
        "tca": {"summary": summary, "impact": impact},
        "sizer": {
            "rebalances": primary["rebalances"],
            "binding_counts": dict(primary["bindings"]),
            "mean_gross_exposure": format(primary["mean_gross"], "f"),
        },
        "calibration": None,
        "llm_audit": None,
        "trial_ledger": {
            "path": _display_path(ledger_path),
            "distinct_configs": n_trials,
            "entries": len(ledger.entries()),
            "head_hash": ledger.head_hash(),
        },
    }
    validate(report, SCHEMA)
    if out is not None:
        write_json(out, report)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the offline sidecar backtest")
    parser.add_argument("--bars", type=Path, default=DEFAULT_BARS)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "offline_report.json")
    parser.add_argument("--bootstrap-reps", type=int, default=400)
    parser.add_argument("--slices", type=int, default=8)
    args = parser.parse_args(argv)
    report = run_backtest(
        args.bars,
        out=args.out,
        bootstrap_reps=args.bootstrap_reps,
        n_slices=args.slices,
    )
    stats = report["evalstats"]
    print(f"wrote {args.out}")
    print(f"status {stats['status']} observations {stats['n_observations']} trials {stats['n_trials']}")
    if stats["status"] == "ok":
        print(
            "sharpe_annualized",
            stats["sharpe_annualized"],
            "psr",
            stats["psr"],
            "dsr",
            stats["dsr"],
            "max_drawdown",
            stats["drawdown"]["max_drawdown"],
        )
        if stats.get("pbo") and stats["pbo"].get("status") == "ok":
            print("pbo", stats["pbo"]["pbo"])
    print("orders_submitted", report["orders_submitted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
