"""Decision adapters for the combined stand-in and the synthetic-news catalog.

Neither adapter calls a model or places an order. The thesis text is a rule id
or a catalog kind, not a generated sentence. Headline text stays out of the log.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from sidecar.llm_audit.showdown import ShowdownLog, render_weekly, score_showdown

STAND_IN_THESIS = "combined.deterministic_stand_in.v1"
STAND_IN_DRIVER = "trailing_60d_trend"
_CLOSE = time(20, 0, tzinfo=timezone.utc)


def standin_confidence(signal: float) -> float:
    """Declared map from a trailing return to a confidence. Not fit to outcomes."""
    return float(min(0.99, max(0.5, 0.5 + abs(signal))))


def log_standin_closes(
    log: ShowdownLog,
    *,
    symbol: str,
    closes: list[float],
    market_closes: list[float],
    beta: float,
    as_of_start: datetime,
    lookback: int = 60,
    horizon_sessions: int = 5,
    stride: int = 1,
    route: str = "combined_standin",
) -> int:
    """Log a trend-rule decision at each stride, then the later realized return.

    ``as_of`` is the close of the signal session. ``horizon_end`` is the close
    ``horizon_sessions`` later. The outcome is recorded one second after that.
    A zero trailing return is not a call.
    """
    if as_of_start.tzinfo is None:
        raise ValueError("as_of_start must be timezone-aware")
    if len(closes) != len(market_closes):
        raise ValueError("closes and market_closes must be aligned")
    if lookback < 1 or horizon_sessions < 1:
        raise ValueError("lookback and horizon must be positive")
    written = 0
    index = lookback
    while index + horizon_sessions < len(closes):
        base = closes[index - lookback]
        if base > 0 and closes[index] > 0 and market_closes[index] > 0 and market_closes[index + horizon_sessions] > 0:
            signal = closes[index] / base - 1.0
            if signal != 0.0:
                start = closes[index]
                realized = closes[index + horizon_sessions] / start - 1.0
                market = market_closes[index + horizon_sessions] / market_closes[index] - 1.0
                as_of = as_of_start + timedelta(days=index)
                horizon_end = as_of + timedelta(days=horizon_sessions)
                judgment_id = f"standin-{symbol}-{index}"
                direction = "up" if signal > 0.0 else "down"
                log.append_decision(
                    {
                        "judgment_id": judgment_id,
                        "route": route,
                        "as_of": as_of.isoformat(),
                        "horizon_end": horizon_end.isoformat(),
                        "symbol": symbol,
                        "horizon": f"{horizon_sessions}d",
                        "thesis": STAND_IN_THESIS,
                        "driver": STAND_IN_DRIVER,
                        "driver_class": "process",
                        "expected_direction": direction,
                        "expected_magnitude": signal * horizon_sessions / lookback,
                        "confidence": standin_confidence(signal),
                        "process_supported": True,
                        "model_id": STAND_IN_THESIS,
                    },
                    recorded_at=as_of,
                )
                log.append_outcome(
                    {
                        "judgment_id": judgment_id,
                        "outcome_at": horizon_end + timedelta(seconds=1),
                        "realized_return": realized,
                        "market_return": market,
                        "beta": beta,
                        "catalyst_occurred": False,
                    },
                    recorded_at=horizon_end + timedelta(seconds=1),
                )
                written += 1
        index += stride
    return written


def _beta(cal: object, symbol: str) -> float:
    if symbol == "SPY":
        return 1.0
    return float(cal.beta[symbol])  # type: ignore[attr-defined]


def log_news_catalog(
    log: ShowdownLog,
    *,
    seed: int = 0,
    noise_sigma: float = 0.0,
    specs: tuple[dict, ...] | None = None,
) -> dict:
    """Score catalog events from the synthetic tape. No model call and no order.

    The decision thesis is the catalog kind. The lexicon sign is the direction.
    Whether the catalyst occurred is written only on the outcome row, from the
    catalog's truth flag, which is the news log for these runs.
    """
    from combined.synth.calibrate import fit
    from combined.synth.news import CATALOG, EVENT_AT, HOLD, build_tape
    from sidecar.sentiment.lexicon import lexicon_score

    chosen = CATALOG if specs is None else specs
    cal = fit()
    written = 0
    for offset, spec in enumerate(chosen):
        sentiment = float(lexicon_score(spec["headline"]))
        if sentiment == 0.0:
            continue
        path = build_tape(spec, seed=seed + offset, noise_sigma=noise_sigma)
        symbol = spec["symbol"]
        start = path.grouped[symbol][EVENT_AT].close_adj
        end = path.grouped[symbol][EVENT_AT + HOLD].close_adj
        spy_start = path.grouped["SPY"][EVENT_AT].close_adj
        spy_end = path.grouped["SPY"][EVENT_AT + HOLD].close_adj
        realized = float(end / start - 1)
        market = float(spy_end / spy_start - 1)
        as_of = datetime.combine(path.sessions[EVENT_AT], _CLOSE)
        horizon_end = datetime.combine(path.sessions[EVENT_AT + HOLD], _CLOSE)
        judgment_id = f"news-{spec['kind']}-{seed}-{offset}"
        direction = "up" if sentiment > 0.0 else "down"
        confidence = float(min(0.99, max(0.5, 0.5 + 0.5 * abs(sentiment))))
        driver_class = "market" if spec["kind"] in {"macro_print"} else "catalyst"
        log.append_decision(
            {
                "judgment_id": judgment_id,
                "route": "synthetic_news",
                "as_of": as_of.isoformat(),
                "horizon_end": horizon_end.isoformat(),
                "symbol": symbol,
                "horizon": f"{HOLD}d",
                "thesis": spec["kind"],
                "driver": spec["kind"],
                "driver_class": driver_class,
                "expected_direction": direction,
                "confidence": confidence,
                "model_id": "sidecar.sentiment.lexicon",
            },
            recorded_at=as_of,
        )
        log.append_outcome(
            {
                "judgment_id": judgment_id,
                "outcome_at": (horizon_end + timedelta(seconds=1)).isoformat(),
                "realized_return": realized,
                "market_return": market,
                "beta": _beta(cal, symbol),
                "catalyst_occurred": spec["truth"] == "true",
                "catalyst_already_priced": bool(spec["priced_in"]),
            },
            recorded_at=horizon_end + timedelta(seconds=1),
        )
        written += 1
    return {
        "written": written,
        "orders_submitted": 0,
        "llm_calls": 0,
        "cost_usd": 0,
        "completion_invented": False,
    }


def write_weekly_example(markdown_path: Path, json_path: Path, log_path: Path) -> dict:
    """A small deterministic report: one trend window and the news catalog.

    Noise is zero. The dollar cost of the language step is zero because no
    model is called.
    """
    from sidecar.jsonutil import write_json

    if log_path.exists():
        log_path.unlink()
    log = ShowdownLog(log_path)
    closes = [100.0]
    for _ in range(65):
        closes.append(closes[-1] * 1.002)
    for _ in range(8):
        closes.append(closes[-1] * 0.99)
    market = [100.0 + index * 0.01 for index in range(len(closes))]
    log_standin_closes(
        log,
        symbol="SPY",
        closes=closes,
        market_closes=market,
        beta=1.0,
        as_of_start=datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc),
        lookback=60,
        horizon_sessions=5,
        stride=5,
    )
    news = log_news_catalog(log, seed=0, noise_sigma=0.0)
    scored = score_showdown(log.entries())
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_weekly(scored), encoding="utf-8")
    write_json(
        json_path,
        {
            "schema_version": "sidecar.showdown.v1",
            "threshold_status": "not_frozen",
            "yield_claim": False,
            "orders_submitted": 0,
            "llm_calls": news["llm_calls"],
            "cost_usd": news["cost_usd"],
            "completion_invented": False,
            "n_scored": scored["n_scored"],
            "cells": scored["cells"],
            "brier": scored["brier"],
            "weeks": [row["week"] for row in scored["weeks"]],
            "notes": [
                "Stand-in thesis is the rule id combined.deterministic_stand_in.v1.",
                "News theses are catalog kinds. Headlines are not copied into the log.",
                "No model was called. Cost is $0.",
            ],
        },
    )
    return scored
