"""Day-by-day replay of a stand-in proposal through the cockpit risk gate.

The live cockpit decides with a model, then a human, then the order gateway. This
replay does not call a model and does not construct that gateway. It loads the pure
risk function, applies the shipped dollar limits, and fills a limit only when the
next open is marketable. ``orders_submitted`` stays 0.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from combined.broker import APPROVAL_POLICY, MAX_NOTIONAL, Book, admit, try_fill
from combined.charts import write_charts
from combined.standin import (
    STAND_IN_ID,
    TREND_LOOKBACK,
    VOL_LOOKBACK,
    daily_returns,
    mean_std,
    proposal_for,
)
from sidecar.evalstats.core import EvalStatsError, moments
from sidecar.evalstats.report import evaluate
from sidecar.harness.backtest import load_bars
from sidecar.tca.impact import fit_impact
from sidecar.tca.shortfall import summarize_fills

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BARS = ROOT / "fixtures" / "etf_daily.csv"
KRONOS_EVAL = ROOT / "examples" / "kronos_eval.json"
OUT_DIR = Path(__file__).resolve().parent
VARIANTS = ("cockpit", "sizer", "combined", "tca")
STARTING = Decimal("25000")
REBALANCE_EVERY = 5
OPTIONS_SLEEVE = Decimal("1250")
KRONOS_SLEEVE = Decimal("1250")
KRONOS_BASE = Decimal("100000")
N_TRIALS = len(VARIANTS)


def _symbols(grouped: dict) -> list[str]:
    present = [symbol for symbol in ("SPY", "QQQ", "IWM") if symbol in grouped]
    if not present:
        raise ValueError("bars must include SPY, QQQ, or IWM")
    return present


def _calendar(grouped: dict, symbols: list[str]) -> list[date]:
    sets = [set(bar.day for bar in grouped[symbol]) for symbol in symbols]
    return sorted(set.intersection(*sets))


def option_levels(days: list[date], returns_by_day: dict[date, float]) -> dict[date, Decimal]:
    level = Decimal(1)
    out = {}
    for day in days:
        raw = returns_by_day.get(day)
        if raw is not None:
            level *= Decimal(1) + Decimal(str(raw))
        out[day] = level
    return out


def kronos_ratios(days: list[date], curve: list[tuple[date, Decimal]]) -> dict[date, Decimal]:
    ratio = Decimal(1)
    cursor = 0
    out = {}
    ordered = sorted(curve)
    for day in days:
        while cursor < len(ordered) and ordered[cursor][0] <= day:
            ratio = ordered[cursor][1] / KRONOS_BASE
            cursor += 1
        out[day] = ratio
    return out


def _adv(grouped: dict, symbol: str, days: list[date], index: int) -> Decimal:
    start = max(0, index - VOL_LOOKBACK + 1)
    window = days[start : index + 1]
    total = sum((grouped[symbol][day].volume for day in window), Decimal(0))
    return total / Decimal(len(window))


def _sigma(closes: list[Decimal]) -> Decimal:
    series = daily_returns(closes, VOL_LOOKBACK)
    if series is None:
        return Decimal(0)
    _edge, vol = mean_std(series)
    return vol


def run_variant(
    grouped: dict,
    variant: str,
    *,
    levels: dict[date, Decimal],
    ratios: dict[date, Decimal],
    symbols: list[str] | None = None,
    micro: dict | None = None,
) -> dict:
    if symbols is None:
        symbols = _symbols(grouped)
    else:
        symbols = list(symbols)
        missing = [symbol for symbol in symbols if symbol not in grouped]
        if missing:
            raise ValueError(f"bars missing {missing}")
    by_day = {symbol: {bar.day: bar for bar in grouped[symbol]} for symbol in symbols}
    days = _calendar(grouped, symbols)
    watchlist = set(symbols)
    sleeve = variant == "combined"
    locked = (OPTIONS_SLEEVE + KRONOS_SLEEVE) if sleeve else Decimal(0)
    book = Book(cash=STARTING)
    queue: list[dict] = []
    curve: list[tuple[date, Decimal]] = []
    fills: list[dict] = []
    cost_model = "tca" if variant == "tca" else "flat"

    for index, day in enumerate(days):
        bars = {symbol: by_day[symbol][day] for symbol in symbols}
        opens = {symbol: bars[symbol].open_adj for symbol in symbols}
        closes = {symbol: bars[symbol].close_adj for symbol in symbols}
        remaining = list(queue)
        queue = []
        while remaining:
            plan = remaining.pop(0)
            proposal = plan["proposal"]
            pending_notional = sum(
                (item["proposal"]["notional"] for item in remaining if item["proposal"]["side"] == "buy"),
                Decimal(0),
            )
            pending_sells: dict[str, Decimal] = {}
            for item in remaining:
                if item["proposal"]["side"] != "sell":
                    continue
                name = item["proposal"]["symbol"]
                pending_sells[name] = pending_sells.get(name, Decimal(0)) + item["proposal"]["quantity"]
            adv = plan["adv"]
            half_spread = None
            if micro is not None:
                slot = micro.get(day, {}).get(proposal["symbol"])
                if slot is not None:
                    half_spread = slot["half_spread"]
                    adv = adv * slot["depth_scale"]
            record = try_fill(
                book,
                proposal,
                arrival=opens[proposal["symbol"]],
                prices=opens,
                watchlist=watchlist,
                pending_notional=pending_notional,
                pending_sells=pending_sells,
                drawdown=Decimal(0),
                buying_power=book.cash - locked - pending_notional,
                sigma=plan["sigma"],
                adv=adv,
                cost_model=cost_model,
                decision_price=plan["decision_price"],
                half_spread=half_spread,
            )
            if record is not None:
                record["date"] = day.isoformat()
                fills.append(record)
        equity_open = book.equity(opens)
        if sleeve:
            target = OPTIONS_SLEEVE * levels[day] + KRONOS_SLEEVE * ratios[day]
            book.cash += target - locked
            locked = target
        equity_close = book.equity(closes)
        if equity_close <= 0:
            raise ValueError("equity became non-positive")
        curve.append((day, equity_close))
        if index < TREND_LOOKBACK or index + 1 >= len(days):
            continue
        if (index - TREND_LOOKBACK) % REBALANCE_EVERY != 0:
            continue
        drawdown = equity_open - equity_close
        if drawdown < 0:
            drawdown = Decimal(0)
        reserved_buy = Decimal(0)
        reserved_sell: dict[str, Decimal] = {}
        for symbol in symbols:
            history = [by_day[symbol][session].close_adj for session in days[: index + 1]]
            proposal = proposal_for(
                symbol=symbol,
                closes=history,
                held_shares=book.position(symbol),
                price=closes[symbol],
                variant=variant,
                cap=MAX_NOTIONAL,
                equity=equity_close,
            )
            if proposal is None:
                continue
            admission, _verdict = admit(
                book,
                proposal,
                prices=closes,
                watchlist=watchlist,
                pending_notional=reserved_buy,
                pending_sells=reserved_sell,
                drawdown=drawdown,
                buying_power=book.cash - locked - reserved_buy,
            )
            if not admission.admitted:
                book.refusals[admission.reason] += 1
                for name in admission.failed:
                    book.failed_checks[name] += 1
                continue
            book.admitted += 1
            if proposal["side"] == "buy":
                reserved_buy += proposal["notional"]
            else:
                reserved_sell[symbol] = reserved_sell.get(symbol, Decimal(0)) + proposal["quantity"]
            queue.append(
                {
                    "proposal": proposal,
                    "decision_price": closes[symbol],
                    "sigma": _sigma(history),
                    "adv": _adv(by_day, symbol, days, index),
                }
            )
    scored = curve[TREND_LOOKBACK:]
    return {
        "curve": curve,
        "scored": scored,
        "fills": fills,
        "admitted": book.admitted,
        "simulated_fills": book.simulated_fills,
        "expired_limits": book.expired_limits,
        "refusals": dict(book.refusals),
        "failed_checks": dict(book.failed_checks),
        "orders_submitted": book.orders_submitted,
        "ending_equity": scored[-1][1] if scored else curve[-1][1],
        "cash": book.cash,
        "shares": {symbol: format(qty, "f") for symbol, qty in book.shares.items()},
    }


def _returns(scored: list[tuple[date, Decimal]]) -> tuple[list[date], list[float]]:
    days = []
    values = []
    for (prev_day, prev_equity), (day, equity) in zip(scored, scored[1:], strict=False):
        del prev_day
        values.append(float((equity - prev_equity) / prev_equity))
        days.append(day)
    return days, values


def _cagr(start: Decimal, end: Decimal, n_returns: int) -> str | None:
    if start <= 0 or end <= 0 or n_returns < 1:
        return None
    ratio = float(end / start)
    grown = ratio ** (252 / n_returns) - 1
    if not math.isfinite(grown):
        return None
    return format(grown, ".12g")


def _spy_closes(grouped: dict, symbols: list[str]) -> dict[date, Decimal]:
    if "SPY" not in symbols:
        return {}
    return {bar.day: bar.close_adj for bar in grouped["SPY"]}


def _regime_labels(spy: dict[date, Decimal], days: list[date]) -> list[dict[str, str]]:
    ordered = sorted(spy)
    position = {day: index for index, day in enumerate(ordered)}
    labels = []
    for day in days:
        index = position[day] - 1
        if index < TREND_LOOKBACK:
            labels.append({"trend": "warming", "vol": "warming"})
            continue
        trend = spy[ordered[index]] / spy[ordered[index - TREND_LOOKBACK]] - 1
        vol_name = "unclassified"
        if index >= 252:
            def _vol(length: int) -> float:
                window = [
                    float(spy[ordered[index - length + offset + 1]] / spy[ordered[index - length + offset]] - 1)
                    for offset in range(length)
                ]
                mean = sum(window) / len(window)
                var = sum((item - mean) ** 2 for item in window) / (len(window) - 1)
                return var ** 0.5

            vol_name = "high_vol" if _vol(20) > _vol(252) else "low_vol"
        labels.append({"trend": "bull" if trend > 0 else "bear", "vol": vol_name})
    return labels


def _bucket(returns: list[float], labels: list[dict[str, str]]) -> dict:
    groups: dict[str, list[float]] = {}
    for value, label in zip(returns, labels, strict=True):
        groups.setdefault(label["trend"], []).append(value)
        groups.setdefault(label["vol"], []).append(value)
    out = {}
    for name, values in groups.items():
        row = {"n": len(values), "mean_return": None, "sharpe_annualized": None}
        if values:
            row["mean_return"] = format(sum(values) / len(values), ".12g")
        try:
            stats = moments(values)
        except EvalStatsError:
            out[name] = row
            continue
        row["sharpe_annualized"] = format(stats.sharpe * math.sqrt(252), ".12g")
        out[name] = row
    return out


def _spy_path(spy: dict[date, Decimal], scored: list[tuple[date, Decimal]]) -> list[tuple[date, Decimal]]:
    if not scored or not spy:
        return []
    level = STARTING
    path = [(scored[0][0], level)]
    previous = scored[0][0]
    for day, _equity in scored[1:]:
        if spy[previous] <= 0:
            change = Decimal(0)
        else:
            change = spy[day] / spy[previous] - 1
        level *= Decimal(1) + change
        path.append((day, level))
        previous = day
    return path


def composed_probe() -> dict:
    """The production composition arms both verdict requirements and then cannot approve."""
    book = Book(cash=STARTING)
    proposal = {
        "symbol": "SPY",
        "side": "buy",
        "asset_type": "equity",
        "order_type": "limit",
        "quantity": Decimal("1"),
        "notional": Decimal("100"),
        "sizing_basis": "fixed-notional",
    }
    admission, verdict = admit(
        book,
        proposal,
        prices={"SPY": Decimal("100")},
        watchlist={"SPY"},
        pending_notional=Decimal(0),
        pending_sells={},
        drawdown=Decimal(0),
        buying_power=STARTING,
        require_quant_verdict=True,
        require_net_edge=True,
    )
    return {
        "require_quant_verdict": True,
        "require_net_edge": True,
        "quant_verdict": None,
        "decision": admission.decision,
        "admitted": admission.admitted,
        "warnings": list(admission.warnings),
        "failed_checks": list(admission.failed),
        "simulated_fills": book.simulated_fills,
        "orders_submitted": 0,
        "max_allowed_notional": verdict["max_allowed_notional"],
    }


def run_study(
    grouped: dict,
    *,
    options_returns: dict[date, float] | None = None,
    kronos_curve: list[tuple[date, Decimal]] | None = None,
    bootstrap_reps: int = 400,
    n_slices: int = 16,
    seed: int = 0,
    bars_sha256: str | None = None,
) -> dict:
    symbols = _symbols(grouped)
    days = _calendar(grouped, symbols)
    levels = option_levels(days, options_returns or {})
    ratios = kronos_ratios(days, kronos_curve or [])
    paths = {
        variant: run_variant(grouped, variant, levels=levels, ratios=ratios) for variant in VARIANTS
    }
    spy = _spy_closes(grouped, symbols)
    aligned_days = None
    returns = {}
    for variant, path in paths.items():
        variant_days, variant_returns = _returns(path["scored"])
        if aligned_days is None:
            aligned_days = variant_days
        elif variant_days != aligned_days:
            raise ValueError("variant calendars diverged")
        returns[variant] = variant_returns
    if not aligned_days:
        raise ValueError("no scored sessions")
    matrix = [returns[variant] for variant in VARIANTS]
    benchmark = None
    if spy and all(day in spy for day in aligned_days):
        benchmark = []
        previous = paths["cockpit"]["scored"][0][0]
        for day in aligned_days:
            benchmark.append(float(spy[day] / spy[previous] - 1))
            previous = day
    labels = _regime_labels(spy, aligned_days) if spy else []
    variants = {}
    for variant in VARIANTS:
        path = paths[variant]
        start_equity = path["scored"][0][1]
        stats = evaluate(
            returns[variant],
            n_trials=N_TRIALS,
            benchmark=benchmark,
            trial_matrix=matrix,
            n_slices=n_slices,
            bootstrap_reps=bootstrap_reps,
            seed=seed,
        )
        variants[variant] = {
            "ending_equity": format(path["ending_equity"], "f"),
            "start_equity": format(start_equity, "f"),
            "cagr": _cagr(start_equity, path["ending_equity"], len(returns[variant])),
            "total_return": format((path["ending_equity"] / start_equity) - 1, "f"),
            "admitted": path["admitted"],
            "simulated_fills": path["simulated_fills"],
            "expired_limits": path["expired_limits"],
            "refusals": path["refusals"],
            "orders_submitted": path["orders_submitted"],
            "ending_shares": path["shares"],
            "evalstats": stats,
            "regimes": _bucket(returns[variant], labels) if labels else {},
            "tca": summarize_fills(path["fills"]),
            "impact": fit_impact(path["fills"]) if path["fills"] else None,
            "equity_curve": [
                {"date": day.isoformat(), "equity": format(equity, "f")} for day, equity in path["scored"]
            ],
        }
    spy_curve = _spy_path(spy, paths["cockpit"]["scored"])
    spy_days, spy_returns = _returns(spy_curve) if spy_curve else ([], [])
    spy_stats = evaluate(spy_returns, n_trials=1, bootstrap_reps=bootstrap_reps, seed=seed) if spy_returns else None
    probe = composed_probe()
    return {
        "schema_version": "combined.replay.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_source": STAND_IN_ID,
        "approval_policy": APPROVAL_POLICY,
        "orders_submitted": 0,
        "broker_writes": False,
        "n_trials": N_TRIALS,
        "variants_counted_in_dsr": list(VARIANTS),
        "bars_sha256": bars_sha256,
        "universe": symbols,
        "starting_equity": format(STARTING, "f"),
        "max_notional": format(MAX_NOTIONAL, "f"),
        "rebalance_every": REBALANCE_EVERY,
        "trend_lookback": TREND_LOOKBACK,
        "sessions": len(days),
        "scored_sessions": len(aligned_days),
        "options_sleeve": format(OPTIONS_SLEEVE, "f"),
        "kronos_sleeve": format(KRONOS_SLEEVE, "f"),
        "options_days_applied": sum(1 for day in days if options_returns and day in options_returns),
        "kronos_marks_applied": sum(1 for _day, equity in (kronos_curve or []) if equity != KRONOS_BASE),
        "composed_production": probe,
        "variants": variants,
        "spy": {
            "ending_equity": format(spy_curve[-1][1], "f") if spy_curve else None,
            "cagr": _cagr(spy_curve[0][1], spy_curve[-1][1], len(spy_returns)) if spy_curve else None,
            "evalstats": spy_stats,
            "equity_curve": [
                {"date": day.isoformat(), "equity": format(equity, "f")} for day, equity in spy_curve
            ],
        },
        "curves": {
            **{variant: paths[variant]["scored"] for variant in VARIANTS},
            "SPY": spy_curve,
        },
    }


def _money(value: str | None) -> str:
    if value is None:
        return "n/a"
    return value


def _stat(block: dict | None, key: str) -> str:
    if not block:
        return "n/a"
    value = block.get(key)
    return "n/a" if value is None else str(value)


def render_report(study: dict) -> str:
    probe = study["composed_production"]
    rows = []
    for name in VARIANTS:
        block = study["variants"][name]
        stats = block["evalstats"]
        drawdown = (stats.get("drawdown") or {}).get("max_drawdown", "n/a")
        rows.append(
            "| {name} | {end} | {cagr} | {sharpe} | {psr} | {dsr} | {dd} | {fills} | {expired} |".format(
                name=name,
                end=block["ending_equity"],
                cagr=block["cagr"] or "n/a",
                sharpe=_stat(stats, "sharpe_annualized"),
                psr=_stat(stats, "psr"),
                dsr=_stat(stats, "dsr"),
                dd=drawdown,
                fills=block["simulated_fills"],
                expired=block["expired_limits"],
            )
        )
    spy = study["spy"]
    spy_stats = spy.get("evalstats") or {}
    regime_lines = []
    for name in VARIANTS:
        regimes = study["variants"][name]["regimes"]
        bits = []
        for key in ("bull", "bear", "high_vol", "low_vol", "unclassified"):
            row = regimes.get(key) or {}
            bits.append(
                f"{key} n={row.get('n', 0)} sharpe={row.get('sharpe_annualized') or 'n/a'}"
            )
        regime_lines.append(f"- {name}: " + "; ".join(bits))
    cockpit = study["variants"]["cockpit"]
    sizer = study["variants"]["sizer"]
    combined = study["variants"]["combined"]
    tca_block = study["variants"]["tca"]
    tca_gap = Decimal(tca_block["ending_equity"]) - Decimal(cockpit["ending_equity"])
    relatives = (
        f"Sizer ends at {sizer['ending_equity']} and the combined sleeves end at "
        f"{combined['ending_equity']}, both below the cockpit stand-in at {cockpit['ending_equity']}. "
        f"TCA ends {format(tca_gap, 'f')} dollars away from that stand-in."
    )
    excess = _stat((cockpit["evalstats"].get("benchmark") or {}), "excess_sharpe_annualized")
    beta = _stat((cockpit["evalstats"].get("benchmark") or {}), "beta")
    shortfall = _stat(cockpit.get("tca"), "median_implementation_shortfall_bps")
    impact = tca_block.get("impact") or {}
    impact_note = (
        f"TCA impact fit status={impact.get('status', 'n/a')}, "
        f"recommended={impact.get('recommended')}, n_fills={impact.get('n_fills', 'n/a')}."
    )
    pbo = (study["variants"]["cockpit"]["evalstats"].get("pbo") or {})
    sharpes = []
    for name in VARIANTS:
        text = _stat(study["variants"][name]["evalstats"], "sharpe_annualized")
        try:
            sharpes.append((float(text), name))
        except ValueError:
            continue
    best = max(sharpes) if sharpes else None
    spy_sharpe = _stat(spy_stats, "sharpe_annualized")
    try:
        spy_value = float(spy_sharpe)
    except ValueError:
        spy_value = None
    if best is None:
        comparison = "The scored variants did not produce a finite annualized Sharpe."
    elif spy_value is None:
        comparison = (
            f"The highest annualized Sharpe among the four variants is {best[1]} at {best[0]:.6g}."
        )
    else:
        ahead = [name for value, name in sharpes if value > spy_value]
        comparison = (
            f"SPY annualized Sharpe on the same sessions is {spy_sharpe}. "
            f"The highest variant is {best[1]} at {best[0]:.6g}. "
            f"Variants above SPY on that display Sharpe: {', '.join(ahead) if ahead else 'none'}. "
            f"Cockpit excess Sharpe versus SPY is {excess} (beta {beta}). "
            f"{relatives}"
        )
    lines = [
        "# Combined historical replay",
        "",
        comparison,
        "",
        "This is a simulation under shipped dollar limits. It is not a qualify decision, and it is not evidence the live cockpit would have traded these names.",
        "",
        "## What ran",
        "",
        f"- Decision source: `{study['decision_source']}`. A 60-session trailing-return sign, long only, sized at the shipped $1,000 max notional. No thesis text was generated.",
        f"- Approval stand-in: `{study['approval_policy']}`. A paper-mode order is queued only when `risk.engine.evaluate` returns `approved`. There is no historical click log to replay.",
        "- Fill: next adjusted open, and only when that price is still inside the prior-close limit. Flat variants pay 1 bp. The TCA variant pays a predeclared half-spread of 0.5 bp plus `0.10 * sigma * sqrt(qty / ADV)`.",
        f"- Universe: {', '.join(study['universe'])}. Starting equity ${study['starting_equity']}. Rebalance every {study['rebalance_every']} sessions after {study['trend_lookback']} closes.",
        f"- Scored sessions: {study['scored_sessions']}. DSR and PBO use n_trials={study['n_trials']} ({', '.join(study['variants_counted_in_dsr'])}).",
        f"- `orders_submitted` is {study['orders_submitted']}. The simulated book is local.",
        "",
        "## What did not run",
        "",
        f"- Composed production posture (`require_quant_verdict` and `require_net_edge`, no book and no edge estimate) returned `{probe['decision']}` with warnings {probe['warnings']}. Simulated fills under that probe: {probe['simulated_fills']}. Live drafting wires those flags whenever a book or metrics function is attached, so this daily tape would not mint an order.",
        "- No `llm_audit` judgment log and no stored TradeProposal tape exist in the tree. The model was not called. Outputs were not invented.",
        "- Kronos weights are not in the tree. The combined sleeve marks the committed `examples/kronos_eval.json` equity curve on and after its first point (2024-07-02), scaled to a $1,250 sleeve. That curve is a recorded walk-forward book, not a new forecast, and it is not turned into per-symbol proposals.",
        f"- Options VRP is the existing Black-Scholes stand-in on adjusted closes (`options_days_applied`={study['options_days_applied']}), scaled to a $1,250 sleeve. `risk.engine` admits `equity` only, so the sleeve never becomes an order. Adjusted closes are not historical option spots.",
        "- `sidecar.merger` scores cash-deal completion from EDGAR observations. This tape has no deal observations, so that model contributes no weight.",
        "- Quant momentum, pairs, and the market maker need a limit-order book. This tree has no historical book, so those strategies are not stepped.",
        "- The order gateway's SQL audit chain, HMAC approval, halt file, and calendar coverage are not instantiated. The simulated path is risk, then paper mode, then the approval stand-in, then a local fill.",
        "",
        "## Results",
        "",
        "| variant | ending equity | CAGR | ann. Sharpe | PSR | DSR | max DD | fills | limits expired |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *rows,
        f"| SPY | {_money(spy.get('ending_equity'))} | {spy.get('cagr') or 'n/a'} | {_stat(spy_stats, 'sharpe_annualized')} | {_stat(spy_stats, 'psr')} | {_stat(spy_stats, 'dsr')} | {(spy_stats.get('drawdown') or {}).get('max_drawdown', 'n/a')} |  |  |",
        "",
        f"PBO on the four aligned variants: status={pbo.get('status', 'n/a')}, pbo={pbo.get('pbo', 'n/a')}, splits={pbo.get('n_splits', 'n/a')}. PBO ranks those four books against each other. Cockpit and TCA are almost the same path, so a low PBO does not mean either one beat SPY.",
        "",
        "SPY's DSR uses n_trials=1 because it is the benchmark path, not one of the four searched variants.",
        "",
        "## By regime",
        "",
        "Regime uses SPY data through the prior close. Bull means the trailing 60-session SPY return is positive. High vol means the trailing 20-session standard deviation exceeds the trailing 252-session standard deviation. Unclassified is the stretch before that 252-session baseline exists.",
        "",
        *regime_lines,
        "",
        "## Costs and the sizer",
        "",
        "The shipped cap is $1,000 on a $25,000 book. Daily ETF volatility is near the sizer's 1% target, so the vol budget wants more than $1,000 and the cap binds. The sizer changes the book when the 20-session mean return and the 60-session trend disagree, because a non-positive edge sizes to zero.",
        "",
        f"Median implementation shortfall versus the decision close is {shortfall} bp on the cockpit book. Buys fill only when the next open is at or below that close, so the delay versus the decision price is favorable by construction. It is the limit filter, not a measured saving. Flat fills have no gap between arrival and the fill price. {impact_note} The schedule used to worsen TCA fills was declared ahead of the run. A null recommendation means those fills did not identify a usable impact coefficient.",
        "",
        "## Lookahead",
        "",
        "- Equity signals and ADV use closes and volume through the decision session only. The fill is the next session's adjusted open.",
        "- The options and Kronos sleeves add that session's already-recorded mark to cash. They do not change the equity target decided from the price window.",
        "- Yahoo bars are backward-adjusted as of the fixture download. A split or dividend after a historical session is in the earlier prices. That is a point-in-time leak in the tape itself.",
        "- An LLM call on these dates would be a second leak. None was made.",
        "- The Kronos sleeve starts flat and steps to the recorded curve on 2024-07-02. Pre-cutoff Kronos forecasts are not used.",
        "",
        "## Charts",
        "",
        "![Equity](charts/equity.svg)",
        "",
        "![Drawdown](charts/drawdown.svg)",
        "",
        "Thresholds in the eval report are `not_frozen`. Nothing here is a pass, a live-capital permission, or a claim that the combination improves the cockpit.",
        "",
    ]
    return "\n".join(lines)


def load_kronos_curve(path: Path = KRONOS_EVAL) -> list[tuple[date, Decimal]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        (date.fromisoformat(row["date"]), Decimal(row["equity"]))
        for row in payload["equity_curve"]
    ]


def load_options_returns(grouped: dict) -> dict[date, float]:
    from sidecar.options.config import PRIMARY
    from sidecar.options.simulate import simulate_book

    symbols = _symbols(grouped)
    days = _calendar(grouped, symbols)
    maps = {symbol: {bar.day: float(bar.adj_close) for bar in grouped[symbol]} for symbol in symbols}
    aligned = [day for day in days if all(maps[symbol][day] > 0 for symbol in symbols)]
    series = {symbol: [maps[symbol][day] for day in aligned] for symbol in symbols}
    result = simulate_book(series, aligned, PRIMARY)
    return {
        date.fromisoformat(day): value
        for day, value in zip(result["return_dates"], result["returns"], strict=True)
    }


def write_outputs(study: dict, directory: Path = OUT_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    curves = study["curves"]
    write_charts(curves, directory / "charts")
    payload = {key: value for key, value in study.items() if key != "curves"}
    serializable = json.loads(json.dumps(payload, default=str))
    (directory / "study.json").write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")
    (directory / "REPORT.md").write_text(render_report(serializable), encoding="utf-8")


def main() -> None:
    grouped = load_bars(DEFAULT_BARS)
    digest = hashlib.sha256(DEFAULT_BARS.read_bytes()).hexdigest()
    options = load_options_returns(grouped)
    kronos = load_kronos_curve()
    study = run_study(
        grouped,
        options_returns=options,
        kronos_curve=kronos,
        bars_sha256=digest,
    )
    write_outputs(study)


if __name__ == "__main__":
    main()
