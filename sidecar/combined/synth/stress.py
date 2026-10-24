"""Monte Carlo of the combined book on synthetic paths, plus the news reaction.

Synthetic paths can show how the book fails. They cannot show that it has an edge.
The historical study in ``REPORT.md`` is a different artifact and is not rewritten here.

Run from ``sidecar/``::

    python -m combined.synth.stress
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np

from combined.charts import COLORS
from combined.replay import STARTING, VARIANTS, run_variant
from combined.synth.calibrate import fit
from combined.synth.market import SYMBOLS, generate_bootstrap, generate_markov
from combined.synth.news import flow_demonstration, mechanism_table, news_monte_carlo, summarize_news
from combined.synth.scenarios import SHOCK_AT, library
from combined.synth.surface import snapshot, variance_swap_pnl

OUT_DIR = Path(__file__).resolve().parents[1]
CHARTS = OUT_DIR / "charts"
GENERATORS = {
    "markov": generate_markov,
    "block_bootstrap": generate_bootstrap,
}
PROBE_DRAWDOWN = 0.10
DAILY_LOSS = 500.0


def _flat(sessions) -> tuple[dict, dict]:
    ones = {day: Decimal(1) for day in sessions}
    return ones, dict(ones)


def _cagr(start: float, end: float, n_returns: int) -> float | None:
    if start <= 0 or end <= 0 or n_returns < 1:
        return None
    grown = (end / start) ** (252 / n_returns) - 1
    if not math.isfinite(grown):
        return None
    return float(grown)


def _sharpe(equity: np.ndarray) -> float | None:
    if len(equity) < 9:
        return None
    returns = np.diff(equity) / equity[:-1]
    vol = float(returns.std(ddof=1))
    if vol <= 0 or not math.isfinite(vol):
        return None
    return float(returns.mean() / vol * math.sqrt(252))


def _max_dd(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    drawdown = np.where(peak > 0, (peak - equity) / peak, 0.0)
    return float(drawdown.max())


def _dist(values: list[float], *, worst: str) -> dict | None:
    if not values:
        return None
    array = np.array(values, dtype=float)
    quantiles = np.quantile(array, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    worst_1 = float(quantiles[0] if worst == "low" else quantiles[6])
    worst_5 = float(quantiles[1] if worst == "low" else quantiles[5])
    return {
        "n": int(array.size),
        "mean": float(array.mean()),
        "median": float(quantiles[3]),
        "p01": float(quantiles[0]),
        "p05": float(quantiles[1]),
        "p25": float(quantiles[2]),
        "p75": float(quantiles[4]),
        "p95": float(quantiles[5]),
        "p99": float(quantiles[6]),
        "worst_1pct": worst_1,
        "worst_5pct": worst_5,
    }


def walk_variant(path, variant: str) -> dict:
    levels, ratios = _flat(path.sessions)
    result = run_variant(
        path.grouped,
        variant,
        levels=levels,
        ratios=ratios,
        symbols=list(SYMBOLS),
        micro=path.micro,
    )
    equity = np.array([float(value) for _day, value in result["curve"]], dtype=float)
    scored_equity = np.array([float(value) for _day, value in result["scored"]], dtype=float)
    scored_days = [day for day, _value in result["scored"]]
    spy = {bar.day: float(bar.close_adj) for bar in path.grouped["SPY"]}
    spy_scored = np.array([spy[day] for day in scored_days], dtype=float)
    spy_equity = float(STARTING) * spy_scored / spy_scored[0]
    n_returns = len(scored_equity) - 1
    last = {symbol: path.grouped[symbol][-1].close_adj for symbol in path.grouped}
    exposure = sum(
        (Decimal(qty) * last[symbol] for symbol, qty in result["shares"].items() if symbol in last),
        Decimal(0),
    )
    mtm_big = int(np.sum(np.diff(equity) < -DAILY_LOSS))
    return {
        "cagr": _cagr(float(scored_equity[0]), float(scored_equity[-1]), n_returns),
        "sharpe": _sharpe(scored_equity),
        "max_drawdown": _max_dd(equity),
        "ending": float(scored_equity[-1]),
        "beat_spy": float(scored_equity[-1] / scored_equity[0]) > float(spy_equity[-1] / spy_equity[0]),
        "spy_cagr": _cagr(float(spy_equity[0]), float(spy_equity[-1]), n_returns),
        "spy_sharpe": _sharpe(spy_equity),
        "spy_max_drawdown": _max_dd(spy_equity),
        "exposure": float(exposure / result["ending_equity"]),
        "refusals": result["refusals"],
        "failed_checks": result["failed_checks"],
        "orders_submitted": result["orders_submitted"],
        "mtm_loss_over_500": mtm_big,
        "simulated_fills": result["simulated_fills"],
        "expired_limits": result["expired_limits"],
        "variance_swap_pnl": variance_swap_pnl(path.returns["SPY"], path.atm_iv),
        "equity": equity,
    }


def _accumulate(rows: list[dict]) -> dict:
    refusals: Counter = Counter()
    failed: Counter = Counter()
    for row in rows:
        refusals.update(row["refusals"])
        failed.update(row["failed_checks"])
    spy_rows = rows
    return {
        "cagr": _dist([row["cagr"] for row in rows], worst="low"),
        "sharpe": _dist([row["sharpe"] for row in rows if row["sharpe"] is not None], worst="low"),
        "max_drawdown": _dist([row["max_drawdown"] for row in rows], worst="high"),
        "ending": _dist([row["ending"] for row in rows], worst="low"),
        "prob_beat_spy": float(np.mean([row["beat_spy"] for row in rows])),
        "mean_exposure": float(np.mean([row["exposure"] for row in rows])),
        "probe_drawdown_10pct_paths": int(sum(row["max_drawdown"] >= PROBE_DRAWDOWN for row in rows)),
        "sessions_mtm_loss_over_500": int(sum(row["mtm_loss_over_500"] for row in rows)),
        "daily_loss_limit_hits": int(failed.get("daily_loss_limit", 0)),
        "refusals": dict(refusals),
        "failed_checks": dict(failed),
        "orders_submitted": int(sum(row["orders_submitted"] for row in rows)),
        "mean_fills": float(np.mean([row["simulated_fills"] for row in rows])),
        "mean_expired_limits": float(np.mean([row["expired_limits"] for row in rows])),
        "variance_swap_pnl": _dist([row["variance_swap_pnl"] for row in rows], worst="low"),
        "spy_cagr": _dist([row["spy_cagr"] for row in spy_rows], worst="low"),
        "spy_sharpe": _dist([row["spy_sharpe"] for row in spy_rows if row["spy_sharpe"] is not None], worst="low"),
        "spy_max_drawdown": _dist([row["spy_max_drawdown"] for row in spy_rows], worst="high"),
    }


def correlation_diagnostic(n_sessions: int = 4000, seed: int = 7) -> dict:
    path = generate_markov(fit(), n_sessions, seed)
    spy = np.array(path.returns["SPY"])
    name = np.array(path.returns["NEXA"])
    regimes = np.array(path.regimes[1:])

    def corr(label: str) -> dict:
        mask = regimes == label
        count = int(mask.sum())
        if count < 30:
            return {"n": count, "corr_nexa_spy": None}
        return {"n": count, "corr_nexa_spy": float(np.corrcoef(spy[mask], name[mask])[0, 1])}

    return {"bull": corr("bull"), "crisis": corr("crisis"), "sideways": corr("sideways")}


def run_monte_carlo(
    *,
    n_paths: int,
    n_sessions: int,
    generators: tuple[str, ...] = ("markov", "block_bootstrap"),
    variants: tuple[str, ...] = VARIANTS,
    seed_base: dict[str, int] | None = None,
    keep_equity: str | None = "markov",
) -> dict:
    bases = seed_base or {"markov": 1000, "block_bootstrap": 5000}
    out: dict = {"n_paths": n_paths, "n_sessions": n_sessions, "generators": {}}
    for name in generators:
        builder = GENERATORS[name]
        summary_rows = {variant: [] for variant in variants}
        curves = []
        for index in range(n_paths):
            path = builder(n_sessions=n_sessions, seed=bases[name] + index)
            for variant in variants:
                row = walk_variant(path, variant)
                if keep_equity == name and variant == "cockpit":
                    curves.append(row["equity"])
                row.pop("equity")
                summary_rows[variant].append(row)
        summary = {variant: _accumulate(summary_rows[variant]) for variant in variants}
        block = {"variants": summary}
        if curves:
            stacked = np.vstack(curves)
            block["cockpit_equity_p05"] = np.quantile(stacked, 0.05, axis=0).tolist()
            block["cockpit_equity_p50"] = np.quantile(stacked, 0.50, axis=0).tolist()
            block["cockpit_equity_p95"] = np.quantile(stacked, 0.95, axis=0).tolist()
        out["generators"][name] = block
    return out


def scenario_results(variants: tuple[str, ...] = VARIANTS) -> dict:
    results = {}
    for name, path in library().items():
        spy = np.array(path.returns["SPY"], dtype=float)
        shock = spy[SHOCK_AT : SHOCK_AT + 15]
        results[name] = {
            "generator": path.generator,
            "n_sessions": len(path.sessions),
            "spy_total_return": float(np.prod(1.0 + spy) - 1.0),
            "spy_min_day": float(spy.min()),
            "spy_max_day": float(spy.max()),
            "crisis_sessions": int(path.regimes.count("crisis")),
            "shock_window_std": float(shock.std(ddof=1)) if len(shock) > 2 else None,
            "variants": {},
        }
        for variant in variants:
            row = walk_variant(path, variant)
            row.pop("equity")
            results[name]["variants"][variant] = {
                "cagr": row["cagr"],
                "sharpe": row["sharpe"],
                "max_drawdown": row["max_drawdown"],
                "ending": row["ending"],
                "beat_spy": row["beat_spy"],
                "spy_total_on_scored_window": row["spy_cagr"],
                "exposure": row["exposure"],
                "refusals": row["refusals"],
                "failed_checks": row["failed_checks"],
                "orders_submitted": row["orders_submitted"],
                "fills": row["simulated_fills"],
                "expired_limits": row["expired_limits"],
                "variance_swap_pnl": row["variance_swap_pnl"],
            }
    return results


def surface_table(cal) -> list[dict]:
    return [snapshot(cal.median_rv[regime], regime) for regime in ("bull", "sideways", "bear", "crisis")]


def _series(values: list[float], width: float, height: float, lo: float, hi: float, left: float, top: float) -> str:
    span = hi - lo or 1.0
    last = max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = left + width * index / last
        y = top + height * (1.0 - (value - lo) / span)
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def write_charts(payload: dict) -> list[str]:
    written = []
    markov = payload["monte_carlo"]["generators"].get("markov") or {}
    if "cockpit_equity_p50" in markov:
        p05 = markov["cockpit_equity_p05"]
        p50 = markov["cockpit_equity_p50"]
        p95 = markov["cockpit_equity_p95"]
        width, height = 920, 420
        left, top, plot_w, plot_h = 64, 36, 760, 320
        lo = min(p05)
        hi = max(p95)
        pad = (hi - lo) * 0.08 or 1.0
        upper = _series(p95, plot_w, plot_h, lo - pad, hi + pad, left, top).split(" ")
        lower = _series(p05, plot_w, plot_h, lo - pad, hi + pad, left, top).split(" ")
        polygon = " ".join(upper + list(reversed(lower)))
        median = _series(p50, plot_w, plot_h, lo - pad, hi + pad, left, top)
        body = "\n".join(
            [
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                '<rect width="100%" height="100%" fill="#fbfbfb"/>',
                f'<text x="{left}" y="22" font-family="sans-serif" font-size="16">Markov cockpit equity, 5th to 95th percentile</text>',
                f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="#ddd"/>',
                f'<polygon points="{polygon}" fill="{COLORS["cockpit"]}" opacity="0.18"/>',
                f'<polyline fill="none" stroke="{COLORS["cockpit"]}" stroke-width="1.6" points="{median}"/>',
                "</svg>",
            ]
        )
        destination = CHARTS / "stress_equity_band.svg"
        _write(destination, body)
        written.append(str(destination.relative_to(OUT_DIR)))
    kinds = list((payload["news"]["summary"]["by_kind"] or {}).items())
    if kinds:
        width, height = 920, 520
        left, top, plot_w, plot_h = 180, 36, 700, 300
        values = [row["mean_chase_pnl"] for _name, row in kinds]
        labels = [name for name, _row in kinds]
        lo = min(0.0, min(values))
        hi = max(0.0, max(values))
        span = hi - lo or 1.0
        zero = top + plot_h * (1.0 - (0.0 - lo) / span)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fbfbfb"/>',
            f'<text x="{left}" y="22" font-family="sans-serif" font-size="16">Chase sleeve mean P&amp;L by headline class</text>',
            f'<line x1="{left}" y1="{zero:.2f}" x2="{left + plot_w}" y2="{zero:.2f}" stroke="#999"/>',
        ]
        bar_w = plot_w / max(len(values), 1)
        for index, value in enumerate(values):
            y = top + plot_h * (1.0 - (value - lo) / span)
            y0 = min(y, zero)
            h = max(abs(zero - y), 1.0)
            color = COLORS["sizer"] if value >= 0 else COLORS["tca"]
            parts.append(f'<rect x="{left + index * bar_w + 8:.2f}" y="{y0:.2f}" width="{bar_w - 16:.2f}" height="{h:.2f}" fill="{color}"/>')
            parts.append(
                f'<text x="{left + index * bar_w + 8:.2f}" y="{top + plot_h + 28}" font-family="sans-serif" font-size="11" transform="rotate(40 {left + index * bar_w + 8:.2f},{top + plot_h + 28})">{labels[index]}</text>'
            )
        parts.append("</svg>")
        destination = CHARTS / "stress_news_pnl.svg"
        _write(destination, "\n".join(parts))
        written.append(str(destination.relative_to(OUT_DIR)))
    drawdowns = []
    names = []
    for generator, block in payload["monte_carlo"]["generators"].items():
        cockpit = block["variants"].get("cockpit")
        if cockpit is None:
            continue
        names.append(generator)
        drawdowns.append(cockpit["max_drawdown"]["median"])
    if names:
        width, height = 920, 280
        left, top, plot_w, plot_h = 64, 36, 760, 180
        hi = max(drawdowns + [0.01]) * 1.4
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fbfbfb"/>',
            f'<text x="{left}" y="22" font-family="sans-serif" font-size="16">Median cockpit max drawdown by generator</text>',
        ]
        bar_w = plot_w / len(names)
        for index, value in enumerate(drawdowns):
            h = plot_h * value / hi
            parts.append(
                f'<rect x="{left + index * bar_w + 40:.2f}" y="{top + plot_h - h:.2f}" width="{bar_w - 80:.2f}" height="{h:.2f}" fill="{COLORS["cockpit"]}"/>'
            )
            parts.append(
                f'<text x="{left + index * bar_w + 40:.2f}" y="{top + plot_h + 24}" font-family="sans-serif" font-size="12">{names[index]} {value:.3f}</text>'
            )
        parts.append("</svg>")
        destination = CHARTS / "stress_drawdown.svg"
        _write(destination, "\n".join(parts))
        written.append(str(destination.relative_to(OUT_DIR)))
    return written


def _fmt(value, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    return f"{float(value):.{digits}f}"


def render_report(payload: dict) -> str:
    cal = payload["calibration"]
    lines = [
        "# Synthetic stress of the combined book",
        "",
        "Synthetic paths test robustness and failure modes. They do not prove an edge. "
        "A path that loses less than a fully invested SPY book is mostly describing the $1,000 position cap and the cash balance, not a forecasting ability. "
        "Nothing in this file was sent to a broker. `orders_submitted` is 0. No model completion was invented.",
        "",
        "## What was fit, and what was not",
        "",
        f"The requested span was {cal['requested_span']}. The bars actually in the tree run from {cal['first_date']} to {cal['last_date']}, "
        f"{cal['n_sessions']} sessions of {', '.join(cal['symbols_in_tape'])}. "
        "There is no 2016–2021 tape here, and there are no single-name prices. "
        "NEXA (Nexa Biolabs) and BRKQ (Brickline Holdings) use assumed betas and idiosyncratic volatility. "
        "Those names are fictional on purpose.",
        "",
        "ETF betas, regime frequencies, the GARCH grid, the Student-t degrees of freedom, jump frequency, overnight-gap share, and the range proxy are in-sample moments of that fixture. "
        "The half-spread schedule and the crisis depth multiplier are structural: the bars contain no quotes. "
        "Printed volume is not cut in a crisis. The impact formula's depth input is. "
        "At a $1,000 order the participation rate versus ETF volume is dust, so the spread, not the square-root impact, is the liquidity effect that can move a fill.",
        "",
        "Dates on a generated path are weekdays from 2024-01-02. They are session indexes. They are not a claim that those historical sessions were replayed. "
        "The block bootstrap draws joint length-20 blocks of SPY, QQQ, and IWM from the post-warmup panel, wrapping at the ends. "
        "Single-name bootstrap returns are the SPY factor plus fresh noise, not a block of a stock that was never loaded.",
        "",
        f"GARCH(1,1) grid pick: alpha={_fmt(cal['garch_alpha'], 2)}, beta={_fmt(cal['garch_beta'], 2)}, omega={_fmt(cal['garch_omega'], 6)}. "
        f"Student-t nu={_fmt(cal['student_nu'], 2)} from GARCH-residual excess kurtosis {_fmt(cal['excess_kurtosis'], 2)}. "
        f"Raw SPY excess kurtosis is {_fmt(cal['raw_excess_kurtosis'], 2)}. "
        "Residual kurtosis is not positive, so the Student-t sits at the Gaussian cap. "
        "Fat tails in the generator are the jump term and the higher crisis variance. "
        f"Jump probability={_fmt(cal['jump_prob'], 4)}, jump size={_fmt(cal['jump_size'], 4)}. "
        f"SPY gap-variance share={_fmt(cal['gap_share'], 3)}.",
        "",
        "Regime means (SPY daily simple return) and counts:",
        "",
    ]
    for regime, count in cal["regime_count"].items():
        fallback = " unconditional fallback" if cal["regime_mean_fallback"][regime] else ""
        lines.append(
            f"- {regime}: n={count}, mean={_fmt(cal['regime_mean'][regime], 5)}, variance={_fmt(cal['regime_var'][regime], 6)}{fallback}, "
            f"half-spread={cal['spread'][regime]}, depth scale={cal['depth_scale'][regime]}"
        )
    diag = payload["correlation"]
    lines.extend(
        [
            "",
            f"On one {payload['correlation_sessions']}-session Markov path, corr(NEXA, SPY) is "
            f"{_fmt((diag['bull'] or {}).get('corr_nexa_spy'))} in bull (n={diag['bull']['n']}) and "
            f"{_fmt((diag['crisis'] or {}).get('corr_nexa_spy'))} in crisis (n={diag['crisis']['n']}). "
            "The crisis state shrinks idiosyncratic volatility, which is what pulls single names toward the factor. "
            f"In the fixture itself, corr(QQQ, IWM) is {_fmt(cal['corr_etf_calm'])} outside crisis labels and {_fmt(cal['corr_etf_crisis'])} inside them.",
            "",
            "## Book under the generators",
            "",
            "Each variant is the same stand-in as the historical replay: long-only, limit at the prior close, fill at the next open only if the limit is still marketable, "
            "and the shipped risk gate. The stress runner passes the day's half-spread into that fill. "
            "Flat-cost variants add that spread on top of the 1 bp fee. The TCA variant puts it in the price and lowers the depth input. "
            "The historical study does not pass a spread, and its numbers are unchanged. "
            "The combined variant's Kronos and options sleeves are flat on these dates. The recorded Kronos curve is a different market, and it is not applied. "
            "The separate variance-swap column is a mark of trailing realized variance against this surface. It is not `simulate_book` and it is not an order.",
            "",
            f"Paths per generator: {payload['monte_carlo']['n_paths']}. Sessions per path: {payload['monte_carlo']['n_sessions']}. "
            "CAGR and Sharpe use the scored window, which starts after 60 closes. "
            "Beating SPY means the scored-window growth of the book exceeded SPY's growth over those same sessions. "
            "The 10% drawdown probe is not a cockpit control. The cockpit's daily-loss limit is $500 of realized loss. "
            "This replay still feeds it the open-to-close mark, and a $1,000 position rarely produces a $500 day.",
            "",
        ]
    )
    for generator, block in payload["monte_carlo"]["generators"].items():
        lines.append(f"### {generator}")
        lines.append("")
        lines.append("| variant | median CAGR | worst 5% CAGR | worst 1% CAGR | median Sharpe | median max DD | worst 5% max DD | P(beat SPY) | mean exposure | 10% probe paths | mtm days under -$500 | daily-loss gate hits |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for variant in [name for name in VARIANTS if name in block["variants"]]:
            row = block["variants"][variant]
            lines.append(
                "| {variant} | {cagr} | {cagr5} | {cagr1} | {sharpe} | {dd} | {dd5} | {beat} | {exp} | {probe} | {mtm} | {loss} |".format(
                    variant=variant,
                    cagr=_fmt(row["cagr"]["median"]),
                    cagr5=_fmt(row["cagr"]["worst_5pct"]),
                    cagr1=_fmt(row["cagr"]["worst_1pct"]),
                    sharpe=_fmt(row["sharpe"]["median"] if row["sharpe"] else None),
                    dd=_fmt(row["max_drawdown"]["median"]),
                    dd5=_fmt(row["max_drawdown"]["worst_5pct"]),
                    beat=_fmt(row["prob_beat_spy"]),
                    exp=_fmt(row["mean_exposure"]),
                    probe=row["probe_drawdown_10pct_paths"],
                    mtm=row["sessions_mtm_loss_over_500"],
                    loss=row["daily_loss_limit_hits"],
                )
            )
        spy = block["variants"]["cockpit"]
        lines.extend(
            [
                "",
                f"SPY-equivalent on the same scored windows: median CAGR {_fmt(spy['spy_cagr']['median'])}, "
                f"median Sharpe {_fmt(spy['spy_sharpe']['median'] if spy['spy_sharpe'] else None)}, "
                f"median max drawdown {_fmt(spy['spy_max_drawdown']['median'])}, "
                f"worst 5% max drawdown {_fmt(spy['spy_max_drawdown']['worst_5pct'])}.",
                f"Variance-swap mark on the cockpit paths, median P&L {_fmt(spy['variance_swap_pnl']['median'], 2)} dollars, "
                f"worst 5% {_fmt(spy['variance_swap_pnl']['worst_5pct'], 2)}. "
                + (
                    "The worst 5% is still positive: the structural premium was wider than the variance these paths realized. That is the premium assumption, not a fitted variance risk premium."
                    if spy["variance_swap_pnl"]["worst_5pct"] > 0
                    else "The worst 5% is negative: realized variance ran through the assumed premium on those paths."
                ),
                f"Orders submitted: {spy['orders_submitted']}.",
                "",
            ]
        )
    lines.extend(
        [
            "## Scenarios",
            "",
            "These are forced shapes. flash_crash, crash_2020, grind_2022, and volmageddon are named for the kind of path, not because those episodes were loaded. "
            "The fixture does not contain February 2018 or the spring of 2020. The grind is a constant drift, not the 2022 tape.",
            "",
            "| scenario | SPY total | SPY worst day | cockpit ending | cockpit exposure | cockpit max DD | beat SPY | sizer ending | tca ending | variance-swap |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, row in payload["scenarios"].items():
        cockpit = row["variants"].get("cockpit") or next(iter(row["variants"].values()))
        sizer = row["variants"].get("sizer")
        tca = row["variants"].get("tca")
        lines.append(
            "| {name} | {total} | {worst} | {end} | {exposure} | {dd} | {beat} | {sizer} | {tca} | {swap} |".format(
                name=name,
                total=_fmt(row["spy_total_return"]),
                worst=_fmt(row["spy_min_day"]),
                end=_fmt(cockpit["ending"], 2),
                exposure=_fmt(cockpit["exposure"]),
                dd=_fmt(cockpit["max_drawdown"]),
                beat=_fmt(cockpit["beat_spy"], 0),
                sizer=_fmt(None if sizer is None else sizer["ending"], 2),
                tca=_fmt(None if tca is None else tca["ending"], 2),
                swap=_fmt(cockpit["variance_swap_pnl"], 2),
            )
        )
    lines.extend(
        [
            "",
            "A path that is flat until the shock never bids, because the 60-session return is not positive, so ending equity stays 25,000 and the drawdown stays 0. "
            "A path that rises every day still may not fill: part of the rise is the open gap, and the limit is the prior close, so the open leaves the bid behind. "
            "The melt-up is that case. Beating SPY on a down scenario with exposure near 0 is the cash balance. It is not a short and it does not harvest the decline.",
            "",
            "## Headlines",
            "",
            "Each headline is text. The cockpit normalizer is loaded from `app/ingest/normalize.py` without starting the feed package. "
            "The source `synthetic:stress` is not on the allowlist, so `python_to_market_event` refuses it and the item never becomes a MarketEvent. "
            "No language-model call is made. Cost is $0. "
            f"Provider credential variables present and unused: {', '.join(payload['news']['summary']['llm']['provider_env_present']) or 'none'}.",
            "",
            "SPY text is scored with `score_session`. NEXA and BRKQ are not on that allowlist, so `score_session` drops them. "
            "They are then scored with the same buzz formula, the same lexicon, and the same 20-session mention baseline. "
            "The lexicon is long-only in effect: an alert requires positive sentiment. A bearish headline does not create a sell. "
            "Flow does not read the headline. One synthetic SPY chain was passed to `score_book` "
            f"({payload['flow']['scanned_contracts']} contracts, raw alerts {payload['flow']['raw_alert_count']}, orders {payload['flow']['orders_submitted']}).",
            "",
            "The cockpit-limit sleeve uses the prior close as the limit, which is the replay's rule. A gap up makes that limit unmarketable, so the book does not chase a beat. "
            "The chase sleeve sets the limit at the next open. It is a counterfactual, labeled as such, so the P&L of believing the headline can be seen. "
            "Bearish headlines, whether the ground truth is real or a hoax, are shown with a probe position that was already long. The sentiment rule does not exit it. "
            "The holding loss is the gap the probe kept. On a hoax with no price impact that loss is about zero, which is the same mechanism that fails to sell a real miss.",
            "",
            "Zero-noise mechanism, one path per class:",
            "",
            "| class | truth | alert | scored by | cockpit filled | cockpit P&L | chase filled | chase P&L | probe holding loss | market event |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["news"]["mechanism"]:
        probe = row["probe"]["holding_loss_through_gap"] if row["probe"] else None
        lines.append(
            "| {kind} | {truth} | {alert} | {scored} | {cfill} | {cpnl} | {hfill} | {hpnl} | {probe} | {event} |".format(
                kind=row["kind"],
                truth=row["truth"],
                alert="yes" if row["alert"] else "no",
                scored=row["scored_by"],
                cfill="yes" if row["cockpit_limit"]["buy_filled"] else "no",
                cpnl=_fmt(row["cockpit_limit"]["pnl"], 2),
                hfill="yes" if row["chase"]["buy_filled"] else "no",
                hpnl=_fmt(row["chase"]["pnl"], 2),
                probe=_fmt(probe, 2),
                event="accepted" if row["market_event_accepted"] else "refused",
            )
        )
    summary = payload["news"]["summary"]
    lines.extend(
        [
            "",
            f"Noisy Monte Carlo: {summary['n_paths']} paths, one class drawn per path, 40 bp daily background noise, the declared impact added on top. "
            f"Orders submitted: {summary['orders_submitted']}. Market events accepted: {summary['market_event_accepted']}.",
            "",
            "| class | n | alert rate | cockpit fill rate | mean cockpit P&L | chase fill rate | mean chase P&L | mean probe holding loss |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for kind, row in summary["by_kind"].items():
        lines.append(
            "| {kind} | {n} | {alert} | {cfill} | {cpnl} | {hfill} | {hpnl} | {probe} |".format(
                kind=kind,
                n=row["n"],
                alert=_fmt(row["alert_rate"], 2),
                cfill=_fmt(row["cockpit_fill_rate"], 2),
                cpnl=_fmt(row["mean_cockpit_pnl"], 2),
                hfill=_fmt(row["chase_fill_rate"], 2),
                hpnl=_fmt(row["mean_chase_pnl"], 2),
                probe=_fmt(row["mean_probe_holding_loss"], 2),
            )
        )
    lines.extend(
        [
            "",
            "Latency of an alert is zero sessions when the text is scored on the session it is timestamped, and the fill is attempted at the next open. "
            "A bearish headline has no alert, so it has no reaction latency. That includes true misses, guidance cuts, the macro print, and the bearish hoax. "
            "The hoax is escaped by the normalizer (`system:` becomes a role-marker) and still refused as a market event.",
            "",
            "## Implied vol",
            "",
            "ATM IV is the regime's median 20-day realized vol times one plus a structural premium. The put wing is richer. `bs_price` prices a 95-strike put and a 105-strike call at 30 days.",
            "",
            "| regime | realized | premium | ATM IV | 95-put IV | 105-call IV | put price at skew | put price at ATM vol |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["surface"]:
        lines.append(
            "| {regime} | {rv} | {premium} | {atm} | {put} | {call} | {skew_px} | {flat_px} |".format(
                regime=row["regime"],
                rv=_fmt(row["realized_ann"]),
                premium=_fmt(row["premium"]),
                atm=_fmt(row["atm_iv"]),
                put=_fmt(row["put_iv_95"]),
                call=_fmt(row["call_iv_105"]),
                skew_px=_fmt(row["put_price_skew"], 3),
                flat_px=_fmt(row["put_price_atm_vol"], 3),
            )
        )
    lines.extend(
        [
            "",
            "## Reading this",
            "",
            "If the capped book has a smaller drawdown than SPY, that is the cash balance. If it beats SPY in a forced bear scenario, that is the stand-in going flat. "
            "If the chase sleeve loses money on a false rumor and the cockpit limit does not, the limit is what prevented the chase, not the lexicon. "
            "The lexicon still alerts on the bullish false rumor and on a bullish line that has no price impact. It does not alert on a true miss. "
            "None of those statements is a claim about live trading, and none of them is an edge.",
            "",
        ]
    )
    for note in cal["notes"]:
        lines.append(f"- {note}")
    lines.extend(["", f"Generated {payload['generated_at']}.", ""])
    return "\n".join(lines)


def run_stress(
    *,
    n_paths: int = 500,
    n_sessions: int = 252,
    news_paths: int = 500,
    generators: tuple[str, ...] = ("markov", "block_bootstrap"),
    variants: tuple[str, ...] = VARIANTS,
) -> dict:
    cal = fit()
    monte = run_monte_carlo(
        n_paths=n_paths,
        n_sessions=n_sessions,
        generators=generators,
        variants=variants,
        keep_equity="markov" if "markov" in generators else None,
    )
    mechanism = mechanism_table()
    noisy = news_monte_carlo(news_paths) if news_paths else []
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "calibration": cal.public(),
        "correlation_sessions": 4000,
        "correlation": correlation_diagnostic(),
        "monte_carlo": monte,
        "scenarios": scenario_results(variants),
        "news": {"mechanism": mechanism, "summary": summarize_news(noisy if noisy else mechanism)},
        "flow": flow_demonstration(),
        "surface": surface_table(cal),
        "orders_submitted": 0,
    }
    payload["orders_submitted"] = _orders(payload)
    return payload


def _orders(payload: dict) -> int:
    total = payload["flow"]["orders_submitted"]
    total += payload["news"]["summary"]["orders_submitted"]
    for row in payload["news"]["mechanism"]:
        total += row["orders_submitted"]
    for block in payload["monte_carlo"]["generators"].values():
        for variant in block["variants"].values():
            total += variant["orders_submitted"]
    for row in payload["scenarios"].values():
        for variant in row["variants"].values():
            total += variant["orders_submitted"]
    return int(total)


def write_outputs(payload: dict, directory: Path | None = None) -> dict:
    directory = directory or OUT_DIR
    charts = write_charts(payload)
    payload = dict(payload)
    payload["charts"] = charts
    report = render_report(payload)
    (directory / "STRESS.md").write_text(report, encoding="utf-8")
    (directory / "stress.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {"report": str(directory / "STRESS.md"), "json": str(directory / "stress.json"), "charts": charts}


def main() -> None:
    payload = run_stress()
    written = write_outputs(payload)
    print(json.dumps({"orders_submitted": payload["orders_submitted"], **written}))


if __name__ == "__main__":
    main()
