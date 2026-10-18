"""Event study of alert dates against underlying closes.

Entry is the adjusted close of the alert session. The 1-session and 5-session
results are both trials. Nothing here places an order.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.report import evaluate
from sidecar.flow.config import HORIZONS, PRIMARY_HORIZON, FlowError
from sidecar.harness.backtest import Bar, load_bars
from sidecar.jsonutil import sha256_canonical, write_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BARS = ROOT / "fixtures" / "etf_daily.csv"
N_TRIALS = len(HORIZONS)
NULL_START = 252
NULL_EVERY = 21
NULL_SYMBOL = "SPY"
QUESTION = "Do flagged unusual-options alerts predict underlying returns at 1 and 5 sessions?"


def dedupe_events(events: list[dict]) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for event in events:
        symbol = str(event["symbol"])
        day = str(event["date"])
        score = float(event["score"])
        key = (symbol, day)
        current = best.get(key)
        if current is None or score > float(current["score"]):
            best[key] = {
                "date": day,
                "symbol": symbol,
                "score": score,
                "source": str(event.get("source", "unspecified")),
            }
    return [best[key] for key in sorted(best)]


def session_return(rows: list[Bar], day: date, horizon: int) -> float | None:
    if horizon < 1:
        raise FlowError("horizon must be at least 1 session")
    index = {bar.day: pos for pos, bar in enumerate(rows)}
    pos = index.get(day)
    if pos is None:
        return None
    exit_pos = pos + horizon
    if exit_pos >= len(rows):
        return None
    start = rows[pos].adj_close
    end = rows[exit_pos].adj_close
    return float(end / start - 1)


def _horizon_block(bars: dict[str, list[Bar]], events: list[dict], horizon: int) -> dict:
    returns: list[float] = []
    used: list[dict] = []
    dropped = 0
    for event in events:
        rows = bars.get(event["symbol"])
        if rows is None:
            dropped += 1
            continue
        value = session_return(rows, date.fromisoformat(event["date"]), horizon)
        if value is None:
            dropped += 1
            continue
        returns.append(value)
        used.append(event)
    stats = evaluate(returns, n_trials=N_TRIALS, periods_per_year=252, bootstrap_reps=200, seed=0)
    hit = None if not returns else sum(1 for value in returns if value > 0) / len(returns)
    mean = None if not returns else sum(returns) / len(returns)
    psr = stats.get("psr")
    alpha = 0.05 / N_TRIALS
    clears = False
    if isinstance(psr, str):
        clears = (1.0 - float(psr)) < alpha
    return {
        "horizon_sessions": horizon,
        "primary": horizon == PRIMARY_HORIZON,
        "n_events": len(used),
        "n_dropped": dropped,
        "hit_rate": None if hit is None else format(hit, ".12g"),
        "mean_return": None if mean is None else format(mean, ".12g"),
        "bonferroni_alpha": format(alpha, ".12g"),
        "clears_bonferroni": clears,
        "dates": [event["date"] for event in used],
        "eval": stats,
    }


def event_study(bars: dict[str, list[Bar]], events: list[dict]) -> dict:
    cleaned = dedupe_events(events)
    if any(event["source"] != cleaned[0]["source"] for event in cleaned):
        raise FlowError("an event study takes one source at a time")
    horizons = {str(horizon): _horizon_block(bars, cleaned, horizon) for horizon in HORIZONS}
    return {
        "n_trials": N_TRIALS,
        "trial_note": (
            "n_trials is the two predeclared horizons. It is not a count of ledger reruns. "
            "Event returns are one per alert, not a contiguous daily book. Five-session "
            "returns overlap, so the observations are dependent and PSR and DSR overstate evidence. "
            "Annualized Sharpe is a display scaling."
        ),
        "entry": "adjusted close of the alert session to the adjusted close horizon sessions later",
        "primary_horizon": PRIMARY_HORIZON,
        "events_sha256": sha256_canonical(cleaned),
        "n_events_input": len(cleaned),
        "horizons": horizons,
    }


def calendar_null_events(bars: dict[str, list[Bar]]) -> list[dict]:
    """Fixed calendar. Not an options-flow history."""
    rows = bars[NULL_SYMBOL]
    events = []
    for index in range(NULL_START, len(rows) - max(HORIZONS), NULL_EVERY):
        events.append(
            {
                "date": rows[index].day.isoformat(),
                "symbol": NULL_SYMBOL,
                "score": 3.0,
                "source": "calendar_null",
            }
        )
    return events


def flow_event_report(bars: dict[str, list[Bar]], events: list[dict], *, source: str) -> dict:
    if source == "calendar_null":
        answered = False
        reason = (
            "No historical options chain, trades, or open-interest panel is in this tree. "
            "Indicative and OPRA history were not downloaded. These events are a fixed "
            "calendar on SPY (every 21st session, starting at session index 252). "
            "They are not unusual options activity, so this run does not answer the question."
        )
    elif source == "options_flow":
        answered = True
        reason = (
            "Events came from the flow alert rule. Dependent overlapping returns and a "
            "short sample still do not make PSR or DSR a frozen gate."
        )
    else:
        raise FlowError("unknown event source")
    body = event_study(bars, events)
    if events and dedupe_events(events)[0]["source"] != source:
        raise FlowError("event source does not match the report source")
    return {
        "schema_version": "sidecar.flow.event_study.v1",
        "orders_submitted": 0,
        "market_question": QUESTION,
        "market_question_answered": answered,
        "event_source": source,
        "reason": reason,
        "threshold_status": "not_frozen",
        **body,
    }


def write_null_study(bars_path: Path, out: Path, ledger_path: Path) -> dict:
    bars = load_bars(bars_path)
    events = calendar_null_events(bars)
    report = flow_event_report(bars, events, source="calendar_null")
    ledger = TrialLedger(ledger_path)
    for horizon in HORIZONS:
        ledger.append(
            kind="parameter_set",
            name=f"flow.calendar_null.horizon_{horizon}",
            config={
                "source": "calendar_null",
                "symbol": NULL_SYMBOL,
                "start_index": NULL_START,
                "every": NULL_EVERY,
                "horizon": horizon,
                "entry": "adj_close_to_adj_close",
            },
        )
    write_json(out, report)
    return report
