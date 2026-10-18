"""Walk-forward Kronos evaluation after the June 2024 pretraining cutoff.

The primary config is fixed below. Other model sizes, lookbacks, and temperatures
are trials for PBO and the deflated Sharpe. They are not used to pick the reported
path after seeing the result.

Nothing here places an order. The paper runner reads the written report and still
refuses to trade this strategy until that file exists.
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.report import evaluate
from sidecar.harness.backtest import DEFAULT_BARS, ROOT, _display_path, _scale_to_cash, load_bars
from sidecar.jsonutil import sha256_canonical, write_json
from sidecar.kronos_signal.cutoff import EVALUATION_START, PRETRAINING_THROUGH
from sidecar.kronos_signal.forecast import forecast_returns
from sidecar.kronos_signal.model_loader import MODELS, load_predictor
from sidecar.kronos_signal.windows import decision_indexes, make_window, placeholder_future
from sidecar.llm_audit.score import spearman
from sidecar.sizer.kelly import suggest_notional

STRATEGY_ID = "sidecar.kronos_signal.v1"
DEFAULT_OUT = ROOT / "examples" / "kronos_eval.json"
COST_BPS = Decimal("1")
KELLY = Decimal("0.25")
DAILY_TARGET = Decimal("0.01")
STARTING = Decimal("100000")

PROFILES = {
    "smoke": {
        "models": ("mini",),
        "lookbacks": (32,),
        "temperatures": (1.0, 0.8),
        "pred_len": 1,
        "stride": 1,
        "sample_count": 2,
        "max_decisions": 8,
        "symbols": ("SPY",),
        "top_p": 0.9,
        "seed": 0,
        "bootstrap_reps": 60,
        "n_slices": 4,
    },
    "report": {
        "models": ("mini", "small"),
        "lookbacks": (64, 128),
        "temperatures": (1.0, 0.7),
        "pred_len": 5,
        "stride": 5,
        "sample_count": 4,
        "max_decisions": 36,
        "symbols": ("SPY", "QQQ", "IWM"),
        "top_p": 0.9,
        "seed": 0,
        "bootstrap_reps": 80,
        "n_slices": 4,
    },
}


def _configs(profile: dict) -> list[dict]:
    rows = []
    for model in profile["models"]:
        for lookback in profile["lookbacks"]:
            for temperature in profile["temperatures"]:
                primary = model == "mini" and lookback == profile["lookbacks"][0] and temperature == profile["temperatures"][0]
                rows.append(
                    {
                        "model": model,
                        "lookback": lookback,
                        "temperature": temperature,
                        "pred_len": profile["pred_len"],
                        "stride": profile["stride"],
                        "sample_count": profile["sample_count"],
                        "top_p": profile["top_p"],
                        "primary": primary,
                    }
                )
    rows.sort(key=lambda row: (not row["primary"], row["model"], row["lookback"], row["temperature"]))
    return rows


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    var_x = sum((item - x_mean) ** 2 for item in xs)
    var_y = sum((item - y_mean) ** 2 for item in ys)
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    cov = sum((left - x_mean) * (right - y_mean) for left, right in zip(xs, ys, strict=True))
    return cov / math.sqrt(var_x * var_y)


def _quality(mean_returns: list[float], probabilities: list[float], realized: list[float]) -> dict:
    paired = [
        (forecast, probability, outcome)
        for forecast, probability, outcome in zip(mean_returns, probabilities, realized, strict=True)
        if outcome != 0.0
    ]
    forecasts = [item[0] for item in paired]
    outcomes = [item[2] for item in paired]
    hits = [1.0 if (forecast > 0) == (outcome > 0) else 0.0 for forecast, outcome in zip(forecasts, outcomes, strict=True) if forecast != 0.0]
    labels = [(probability, 1 if outcome > 0 else 0) for _forecast, probability, outcome in paired]
    brier = None if not labels else sum((prob - label) ** 2 for prob, label in labels) / len(labels)
    return {
        "n": len(paired),
        "information_coefficient": _pearson(forecasts, outcomes),
        "rank_ic": spearman(forecasts, outcomes) if len(forecasts) >= 3 else None,
        "directional_hit_rate": None if not hits else sum(hits) / len(hits),
        "brier_direction": brier,
        "mean_forecast": None if not forecasts else sum(forecasts) / len(forecasts),
        "mean_realized": None if not outcomes else sum(outcomes) / len(outcomes),
    }


def _horizon_target(pred_len: int) -> Decimal:
    return (DAILY_TARGET ** 2 * Decimal(pred_len)).sqrt()


def _as_decimal(value: float) -> Decimal:
    return Decimal(format(value, ".8f"))


def _trailing(bars, index: int, pred_len: int, samples: int) -> tuple[float, float]:
    window = []
    cursor = index
    while len(window) < samples and cursor - pred_len >= 0:
        start = bars[cursor - pred_len].close
        end = bars[cursor].close
        window.append(float(end / start - 1))
        cursor -= pred_len
    if len(window) < 2:
        return 0.0, 0.0
    mean = sum(window) / len(window)
    variance = sum((item - mean) ** 2 for item in window) / (len(window) - 1)
    return mean, math.sqrt(variance)


def _book(
    aligned: dict,
    symbols: list[str],
    signal_indexes: list[int],
    edge_vol: dict,
    *,
    pred_len: int,
    buy_and_hold: bool = False,
):
    """Signal at the close of ``index``, fill at the next session's adjusted open, 1 bp.

    Marks stop at the open ``pred_len`` sessions after the last fill. Later fixture
    bars are not part of this trial, so a short decision grid cannot collect a
    multi-year residual hold.
    """
    empty = {"curve": [], "step_returns": [], "ending_equity": None, "n_fills": 0}
    if not signal_indexes:
        return empty
    series = aligned[symbols[0]]
    first_fill = signal_indexes[0] + 1
    last_fill = signal_indexes[-1] + 1
    if first_fill >= len(series) or last_fill >= len(series):
        return empty
    final_open = last_fill + pred_len
    if final_open < len(series):
        end_index = final_open
        end_on_open = True
    else:
        end_index = len(series) - 1
        end_on_open = False
    cost_rate = COST_BPS / Decimal(10000)
    target_vol = _horizon_target(pred_len)
    cash = STARTING
    shares = {symbol: Decimal(0) for symbol in symbols}
    curve = []
    step_returns = []
    fills = []
    entry = None
    held = False
    rebalance = {index + 1: index for index in signal_indexes if index + 1 <= end_index}
    for index in range(first_fill, end_index + 1):
        opens = {symbol: aligned[symbol][index].open_adj for symbol in symbols}
        mark_on_open = index in rebalance or (index == end_index and end_on_open)
        if mark_on_open:
            equity_open = cash + sum((shares[symbol] * opens[symbol] for symbol in symbols), Decimal(0))
            if entry is not None and entry > 0:
                step_returns.append(float(equity_open / entry - 1))
            trade_now = index in rebalance and (not buy_and_hold or not held)
            if trade_now:
                if equity_open <= 0:
                    break
                signal_index = rebalance[index]
                raw = {}
                if buy_and_hold:
                    budget = equity_open / Decimal(len(symbols))
                    for symbol in symbols:
                        raw[symbol] = budget / (Decimal(1) + cost_rate)
                else:
                    for symbol in symbols:
                        edge, vol = edge_vol[signal_index][symbol]
                        if edge > 0 and vol > 0:
                            raw[symbol] = suggest_notional(
                                edge, vol, target_vol, KELLY, equity_open, equity_open
                            ).suggested
                        else:
                            raw[symbol] = Decimal(0)
                scale = _scale_to_cash(cash, shares, opens, raw, cost_rate)
                raw = {symbol: value * scale for symbol, value in raw.items()}
                for symbol in symbols:
                    old = shares[symbol] * opens[symbol]
                    trade = raw[symbol] - old
                    cost = abs(trade) * cost_rate
                    cash -= trade + cost
                    shares[symbol] = Decimal(0) if opens[symbol] == 0 else raw[symbol] / opens[symbol]
                    if abs(trade) > Decimal("0.01"):
                        decision_bar = aligned[symbol][signal_index]
                        fills.append(
                            {
                                "symbol": symbol,
                                "side": "buy" if trade > 0 else "sell",
                                "qty": format(abs(shares[symbol] - (old / opens[symbol])), "f"),
                                "decision_price": format(decision_bar.close_adj, "f"),
                                "arrival_mid": format(opens[symbol], "f"),
                                "fill_price": format(opens[symbol], "f"),
                                "fee": format(cost, "f"),
                                "venue_label": "simulated_next_open",
                            }
                        )
                held = True
                entry = cash + sum((shares[symbol] * opens[symbol] for symbol in symbols), Decimal(0))
            else:
                entry = equity_open
        if index == end_index and end_on_open and index not in rebalance:
            equity_mark = cash + sum((shares[symbol] * opens[symbol] for symbol in symbols), Decimal(0))
            curve.append((series[index].day, equity_mark))
        else:
            equity_close = cash + sum(
                (shares[symbol] * aligned[symbol][index].close_adj for symbol in symbols), Decimal(0)
            )
            curve.append((series[index].day, equity_close))
    return {
        "curve": [{"date": day.isoformat(), "equity": format(value, "f")} for day, value in curve],
        "step_returns": step_returns,
        "ending_equity": None if not curve else format(curve[-1][1], "f"),
        "n_fills": len(fills),
    }


def _align(grouped, symbols: tuple[str, ...]):
    by_day = {symbol: {bar.day: bar for bar in grouped[symbol]} for symbol in symbols}
    common = sorted(set.intersection(*(set(rows) for rows in by_day.values())))
    return {symbol: [by_day[symbol][day] for day in common] for symbol in symbols}


def _score_config(predictor, aligned, symbols, indexes, config, profile) -> dict:
    pred_len = config["pred_len"]
    means = {symbol: [] for symbol in symbols}
    probs = {symbol: [] for symbol in symbols}
    realized = {symbol: [] for symbol in symbols}
    edge_vol = {}
    for index in indexes:
        windows = {
            symbol: make_window(aligned[symbol], index, lookback=config["lookback"], pred_len=pred_len)
            for symbol in symbols
        }
        forecasts = forecast_returns(
            predictor,
            [windows[symbol]["context"] for symbol in symbols],
            [bar.day for bar in windows[symbols[0]]["future"]],
            sample_count=config["sample_count"],
            temperature=config["temperature"],
            top_p=config["top_p"],
            seed=profile["seed"] + index,
        )
        edge_vol[index] = {}
        for symbol, forecast in zip(symbols, forecasts, strict=True):
            outcome = float(
                aligned[symbol][index + pred_len].close / aligned[symbol][index].close - 1
            )
            means[symbol].append(forecast["mean_return"])
            probs[symbol].append(forecast["p_up"])
            realized[symbol].append(outcome)
            vol = forecast["dispersion"]
            if vol <= 0:
                _edge, vol = _trailing(aligned[symbol], index, pred_len, samples=8)
            edge_vol[index][symbol] = (_as_decimal(forecast["mean_return"]), _as_decimal(vol))
    flat = []
    trailing = []
    trail_prob = []
    for index in indexes:
        flat.append(0.0)
        symbol_edges = []
        for symbol in symbols:
            edge, vol = _trailing(aligned[symbol], index, pred_len, samples=max(4, config["lookback"] // pred_len))
            symbol_edges.append((edge, vol))
        trailing.append(sum(item[0] for item in symbol_edges) / len(symbol_edges))
        trail_prob.append(1.0 if trailing[-1] > 0 else 0.0)
    pooled_mean = [sum(means[symbol][pos] for symbol in symbols) / len(symbols) for pos in range(len(indexes))]
    pooled_prob = [sum(probs[symbol][pos] for symbol in symbols) / len(symbols) for pos in range(len(indexes))]
    pooled_realized = [
        sum(realized[symbol][pos] for symbol in symbols) / len(symbols) for pos in range(len(indexes))
    ]
    book = _book(aligned, list(symbols), indexes, edge_vol, pred_len=pred_len)
    zero_edges = {
        index: {symbol: (Decimal(0), Decimal("0.01")) for symbol in symbols} for index in indexes
    }
    trail_edges = {}
    for index in indexes:
        trail_edges[index] = {}
        for symbol in symbols:
            edge, vol = _trailing(aligned[symbol], index, pred_len, samples=max(4, config["lookback"] // pred_len))
            trail_edges[index][symbol] = (_as_decimal(edge), _as_decimal(vol if vol > 0 else 0.0))
    return {
        "quality": _quality(pooled_mean, pooled_prob, pooled_realized),
        "per_symbol": {
            symbol: _quality(means[symbol], probs[symbol], realized[symbol]) for symbol in symbols
        },
        "book": book,
        "zero_book": _book(aligned, list(symbols), indexes, zero_edges, pred_len=pred_len),
        "trailing_book": _book(aligned, list(symbols), indexes, trail_edges, pred_len=pred_len),
        "trailing_quality": _quality(trailing, trail_prob, pooled_realized),
        "zero_quality": _quality(flat, [0.5] * len(indexes), pooled_realized),
        # CSCV cannot rank a long-only book that sits in cash. This series is the
        # predeclared ranking input: pooled forecast times pooled realized return.
        "ranking_returns": [
            forecast * outcome for forecast, outcome in zip(pooled_mean, pooled_realized, strict=True)
        ],
    }


def _tradable(predictor, aligned, symbols, config, profile) -> list[dict]:
    last = len(aligned[symbols[0]]) - 1
    if aligned[symbols[0]][last].day < EVALUATION_START:
        raise RuntimeError("the last bar is still inside the pretraining window")
    lookback = config["lookback"]
    if last - lookback + 1 < 0:
        raise RuntimeError("not enough bars for a live context")
    contexts = [aligned[symbol][last - lookback + 1 : last + 1] for symbol in symbols]
    future = placeholder_future(aligned[symbols[0]][last].day, config["pred_len"])
    forecasts = forecast_returns(
        predictor,
        contexts,
        future,
        sample_count=config["sample_count"],
        temperature=config["temperature"],
        top_p=config["top_p"],
        seed=profile["seed"] + 10_000,
    )
    rows = []
    for symbol, forecast in zip(symbols, forecasts, strict=True):
        vol = forecast["dispersion"]
        if vol <= 0:
            _edge, vol = _trailing(aligned[symbol], last, config["pred_len"], samples=8)
        rows.append(
            {
                "symbol": symbol,
                "as_of": aligned[symbol][last].day.isoformat(),
                "edge": format(_as_decimal(forecast["mean_return"]), "f"),
                "vol": format(_as_decimal(vol), "f"),
                "p_up": format(forecast["p_up"], ".6f"),
                "horizon_bars": config["pred_len"],
                "scored": False,
            }
        )
    return rows


def _json_quality(block: dict) -> dict:
    def num(value):
        return None if value is None else format(value, ".8f")

    return {
        "n": block["n"],
        "information_coefficient": num(block["information_coefficient"]),
        "rank_ic": num(block["rank_ic"]),
        "directional_hit_rate": num(block["directional_hit_rate"]),
        "brier_direction": num(block["brier_direction"]),
        "mean_forecast": num(block["mean_forecast"]),
        "mean_realized": num(block["mean_realized"]),
    }


def run_profile(name: str, *, out: Path, ledger_path: Path, bars_path: Path = DEFAULT_BARS) -> dict:
    if name not in PROFILES:
        raise SystemExit(f"unknown profile {name}")
    profile = PROFILES[name]
    grouped = load_bars(bars_path)
    symbols = profile["symbols"]
    aligned = _align(grouped, symbols)
    indexes = decision_indexes(
        aligned[symbols[0]],
        pred_len=profile["pred_len"],
        stride=profile["stride"],
        max_decisions=profile["max_decisions"],
    )
    if len(indexes) < 8:
        raise RuntimeError("not enough post-cutoff decisions for PSR")
    configs = _configs(profile)
    ledger = TrialLedger(ledger_path)
    loaded = {}
    scored = []
    for config in configs:
        ledger.append(
            kind="parameter_set",
            name=f"{STRATEGY_ID}:{config['model']}:lb={config['lookback']}:T={config['temperature']}",
            config={
                "strategy_id": STRATEGY_ID,
                "profile": name,
                "model_id": MODELS[config["model"]]["model_id"],
                "tokenizer_id": MODELS[config["model"]]["tokenizer_id"],
                "kronos_commit": "67b630e67f6a18c9e9be918d9b4337c960db1e9a",
                **{key: value for key, value in config.items() if key != "primary"},
                "primary": config["primary"],
                "evaluation_start": EVALUATION_START.isoformat(),
                "live_eligible": False,
            },
        )
        if config["model"] not in loaded:
            loaded[config["model"]] = load_predictor(config["model"], device="cpu")
        print(
            f"scoring {config['model']} lookback={config['lookback']} T={config['temperature']}",
            flush=True,
        )
        scored.append(
            _score_config(loaded[config["model"]], aligned, symbols, indexes, config, profile)
        )
    primary_config = configs[0]
    primary = scored[0]
    ranking = [row["ranking_returns"] for row in scored]
    width = min(len(row) for row in ranking)
    ranking = [row[-width:] for row in ranking]
    n_trials = ledger.distinct_config_count()
    periods = max(1, int(round(252 / profile["stride"])))
    stats = evaluate(
        primary["book"]["step_returns"],
        n_trials,
        trial_matrix=ranking if len(ranking) >= 2 else None,
        n_slices=profile["n_slices"],
        bootstrap_reps=profile["bootstrap_reps"],
        seed=0,
        periods_per_year=periods,
    )
    stats["notes"] = list(stats.get("notes") or []) + [
        "PSR, DSR, and drawdown use the long-only cost-aware step returns. "
        "PBO ranks forecast-weighted realized returns (pooled mean forecast times pooled realized return)."
    ]
    baseline_stats = {}
    for label, key in (
        ("zero_return", "zero_book"),
        ("trailing_mean", "trailing_book"),
    ):
        series = primary[key]["step_returns"]
        if len(series) >= 8 and (max(series) - min(series)) > 0:
            baseline_stats[label] = evaluate(
                series,
                n_trials,
                bootstrap_reps=profile["bootstrap_reps"],
                seed=1,
                periods_per_year=periods,
            )
        else:
            baseline_stats[label] = {
                "status": "insufficient_sample",
                "reason": "baseline step returns are missing or have no variance",
                "n": len(series),
            }
    hold = _book(aligned, list(symbols), indexes, {}, pred_len=profile["pred_len"], buy_and_hold=True)
    if len(hold["step_returns"]) >= 8 and (max(hold["step_returns"]) - min(hold["step_returns"])) > 0:
        baseline_stats["buy_and_hold"] = evaluate(
            hold["step_returns"],
            n_trials,
            bootstrap_reps=profile["bootstrap_reps"],
            seed=2,
            periods_per_year=periods,
        )
    else:
        baseline_stats["buy_and_hold"] = {
            "status": "insufficient_sample",
            "n": len(hold["step_returns"]),
        }
    tradable = _tradable(loaded[primary_config["model"]], aligned, symbols, primary_config, profile)
    protocol_ok = (
        len(primary["book"]["step_returns"]) >= 8
        and all(aligned[symbols[0]][index].day >= EVALUATION_START for index in indexes)
        and stats.get("threshold_status") == "not_frozen"
    )
    report = {
        "schema_version": "sidecar.kronos_eval.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": STRATEGY_ID,
        "profile": name,
        "live_eligible": protocol_ok,
        "beats_baselines": False,
        "pretraining_through": PRETRAINING_THROUGH.isoformat(),
        "evaluation_start": EVALUATION_START.isoformat(),
        "paper_citation": "arXiv:2508.02739",
        "kronos_commit": "67b630e67f6a18c9e9be918d9b4337c960db1e9a",
        "bars_path": _display_path(Path(bars_path)),
        "symbols": list(symbols),
        "primary_config": {
            **primary_config,
            "config_hash": sha256_canonical(
                {
                    "strategy_id": STRATEGY_ID,
                    "model": primary_config["model"],
                    "lookback": primary_config["lookback"],
                    "temperature": primary_config["temperature"],
                    "pred_len": primary_config["pred_len"],
                }
            ),
        },
        "signal_quality": _json_quality(primary["quality"]),
        "per_symbol": {symbol: _json_quality(block) for symbol, block in primary["per_symbol"].items()},
        "baselines": {
            "zero_return": {
                "quality": _json_quality(primary["zero_quality"]),
                "ending_equity": primary["zero_book"]["ending_equity"],
                "evalstats": baseline_stats["zero_return"],
            },
            "trailing_mean": {
                "quality": _json_quality(primary["trailing_quality"]),
                "ending_equity": primary["trailing_book"]["ending_equity"],
                "evalstats": baseline_stats["trailing_mean"],
            },
            "buy_and_hold": {
                "ending_equity": hold["ending_equity"],
                "evalstats": baseline_stats["buy_and_hold"],
            },
        },
        "pbo_input": "forecast_weighted_realized",
        "strategy_returns": primary["book"]["step_returns"],
        "equity_curve": primary["book"]["curve"],
        "ending_equity": primary["book"]["ending_equity"],
        "evalstats": stats,
        "trials": [
            {
                "model": config["model"],
                "lookback": config["lookback"],
                "temperature": config["temperature"],
                "primary": config["primary"],
                "mean_step_return": (
                    None
                    if not row["book"]["step_returns"]
                    else format(sum(row["book"]["step_returns"]) / len(row["book"]["step_returns"]), ".8f")
                ),
                "ending_equity": row["book"]["ending_equity"],
                "information_coefficient": _json_quality(row["quality"])["information_coefficient"],
                "directional_hit_rate": _json_quality(row["quality"])["directional_hit_rate"],
                "brier_direction": _json_quality(row["quality"])["brier_direction"],
            }
            for config, row in zip(configs, scored, strict=True)
        ],
        "tradable_signals": tradable,
        "trial_ledger": {
            "path": _display_path(ledger_path),
            "distinct_configs": n_trials,
            "entries": len(ledger.entries()),
            "head_hash": ledger.head_hash(),
        },
        "orders_submitted": 0,
        "broker_writes": False,
        "caveats": [
            "Kronos pretraining runs through 2024-06-30 (arXiv 2508.02739: data extends up to June 2024; the paper's test period starts in July 2024). Scored decisions are on or after 2024-07-01.",
            "A repository comment also describes training through February 2024 and validation through May 2024. July 2024 is after that validation window as well.",
            "Walk-forward only. The context ends on the decision close. The label is the close pred_len sessions later. Future prices are not passed to the model.",
            "The last tradable_signals row is an unscored forecast from the final bar. Weekday dates after that bar are time features, not observed sessions.",
            "Yahoo daily bars are backward-adjusted as of the fixture download. They are not a point-in-time tape. Amount is volume times close, a proxy Kronos's amount field was not given.",
            "This run does not fine-tune Kronos. The weights are the public Hugging Face checkpoints.",
            "The sizer horizon target is 1% times the square root of pred_len, so edge and volatility share the forecast horizon. A non-positive edge sizes to zero.",
            "Fills are the next adjusted open plus 1 bp. That is the harness convention, not an exchange print.",
            "Each step return runs from one fill open to the next, and the last step ends at the open pred_len sessions after the final fill. Bars after that horizon are not in the trial. Annualized Sharpe uses 252/stride periods per year.",
            "PBO ranks the predeclared grid on forecast-weighted realized returns (pooled mean forecast times pooled N-bar realized return), one point per decision. It does not rank the long-only cash book: a non-positive edge is a zero weight, and a flat book has no variance to split. CSCV is not purged and it is not a qualify gate.",
            "threshold_status is not_frozen. live_eligible means the post-cutoff protocol finished. It does not mean the forecast beat the nulls.",
            "Zero-return is a constant probability of 0.5 and a flat book. Trailing mean uses only N-bar returns that end on or before the decision.",
        ],
    }
    primary_mean = report["trials"][0]["mean_step_return"]
    baseline_means = []
    for key in ("zero_book", "trailing_book"):
        series = primary[key]["step_returns"]
        baseline_means.append(None if not series else sum(series) / len(series))
    hold_series = hold["step_returns"]
    baseline_means.append(None if not hold_series else sum(hold_series) / len(hold_series))
    if primary_mean is not None and all(item is not None for item in baseline_means):
        report["beats_baselines"] = float(primary_mean) > max(baseline_means)
    if report["orders_submitted"] != 0 or report["broker_writes"] is not False:
        raise RuntimeError("kronos evaluation tried to describe a broker write")
    write_json(out, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward Kronos forecast eval. Does not place orders.")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="smoke")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--bars", type=Path, default=DEFAULT_BARS)
    args = parser.parse_args(argv)
    out = args.out
    if out is None:
        out = DEFAULT_OUT if args.profile == "report" else ROOT / "artifacts" / "kronos_smoke.json"
    ledger = args.ledger or out.with_suffix(".ledger.jsonl")
    report = run_profile(args.profile, out=out, ledger_path=ledger, bars_path=args.bars)
    quality = report["signal_quality"]
    print(f"wrote {out}")
    print("live_eligible", report["live_eligible"], "beats_baselines", report["beats_baselines"])
    print("ic", quality["information_coefficient"], "hit", quality["directional_hit_rate"], "brier", quality["brier_direction"])
    print("ending_equity", report["ending_equity"], "orders_submitted", report["orders_submitted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
