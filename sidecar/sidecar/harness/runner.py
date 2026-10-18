"""Experimental daily paper runner for sidecar strategies.

Dry-run is the default. ``--execute`` is the only way this process posts an order, and
that post goes to the sidecar's own Alpaca paper account. It does not read the cockpit
credential pair, and it is not the cockpit's single order path. Reconcile the two paths
at integration time; do not point this client at the cockpit account.

The Avellaneda-Stoikov market maker is logged on the trial ledger as a simulation-only
config. This runner never sends its quotes to a broker.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections import Counter
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

from sidecar.chainlog import ChainLog
from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.report import evaluate
from sidecar.harness.backtest import (
    DEFAULT_BARS,
    PRIMARY_LOOKBACK,
    ROOT,
    SCHEMA,
    STRATEGY_ID,
    TRIAL_LOOKBACKS,
    _display_path,
    _simulate,
    _spy_benchmark,
    _targets,
    load_bars,
)
from sidecar.harness.paper_credentials import (
    SidecarCredentialsMissing,
    load_sidecar_paper_credentials,
)
from sidecar.harness.trade import (
    PAPER_BASE_URL,
    DATA_BASE_URL,
    LimitBreach,
    OrderIntent,
    PaperLimits,
    PaperTradeClient,
    PaperTradeError,
    check_limits,
    make_client_order_id,
)
from sidecar.jsonutil import sha256_canonical, validate, write_json
from sidecar.tca.shortfall import summarize_fills

ET = ZoneInfo("America/New_York")
OPEN_START = time(9, 30)
OPEN_END = time(10, 5)
CLOSE_START = time(15, 40)
CLOSE_END = time(16, 5)
SPY_ONLY_ID = "sidecar.spy_only_fractional_kelly.v1"
AS_SIM_ID = "sidecar.avellaneda_stoikov.sim_only.v1"
KRONOS_ID = "sidecar.kronos_signal.v1"
DEFAULT_KRONOS_REPORT = ROOT / "examples" / "kronos_eval.json"
DEFAULT_KILL = ROOT / "artifacts" / "KILL"
DEFAULT_OUT = ROOT / "artifacts" / "paper_runner_report.json"
DEFAULT_LEDGER = ROOT / "artifacts" / "paper_runner.ledger.jsonl"
DEFAULT_ORDER_LOG = ROOT / "artifacts" / "paper_orders.jsonl"


class RunnerError(RuntimeError):
    """The daily runner refused to continue."""


def session_phase(moment: datetime) -> str:
    """Return open, close, weekend, or outside for an America/New_York clock."""
    local = moment.astimezone(ET)
    if local.weekday() >= 5:
        return "weekend"
    clock = local.timetz().replace(tzinfo=None)
    if OPEN_START <= clock < OPEN_END:
        return "open"
    if CLOSE_START <= clock < CLOSE_END:
        return "close"
    return "outside"


def resolve_when(when: str, moment: datetime) -> str:
    if when in {"open", "close"}:
        return when
    if when != "auto":
        raise RunnerError("when must be auto, open, or close")
    phase = session_phase(moment)
    if phase in {"open", "close"}:
        return phase
    raise RunnerError(
        "auto is outside both rebalance windows (09:30–10:05 and 15:40–16:05 America/New_York)"
    )


def window_allows(when: str, moment: datetime) -> bool:
    return session_phase(moment) == when


def _parse_now(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise RunnerError("--now must include a timezone offset")
    return parsed


def _kill_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    env = os.environ.get("SIDECAR_KILL_FILE")
    if env:
        return Path(env)
    return DEFAULT_KILL


def _history_window(rows, lookback: int) -> list[Decimal]:
    closes = [bar.close_adj for bar in rows]
    if len(closes) <= lookback:
        raise RunnerError("not enough bars to size the book")
    return [
        closes[index] / closes[index - 1] - 1
        for index in range(len(closes) - lookback, len(closes))
    ]


def _quantize_notional(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _configs(strategy: str, *, target_vol: str, kelly_frac: str, rebalance_every: int) -> list[dict]:
    rows = []
    for lookback in TRIAL_LOOKBACKS:
        rows.append(
            {
                "kind": "parameter_set",
                "name": f"{STRATEGY_ID}:lookback={lookback}",
                "config": {
                    "strategy_id": STRATEGY_ID,
                    "lookback": lookback,
                    "rebalance_every": rebalance_every,
                    "target_vol": target_vol,
                    "kelly_frac": kelly_frac,
                    "primary": lookback == PRIMARY_LOOKBACK,
                    "live_eligible": strategy == "inverse_vol",
                    "traded_this_run": strategy == "inverse_vol" and lookback == PRIMARY_LOOKBACK,
                },
            }
        )
    rows.append(
        {
            "kind": "strategy_config",
            "name": SPY_ONLY_ID,
            "config": {
                "strategy_id": SPY_ONLY_ID,
                "symbols": ["SPY"],
                "lookback": PRIMARY_LOOKBACK,
                "target_vol": target_vol,
                "kelly_frac": kelly_frac,
                "live_eligible": True,
                "traded_this_run": strategy == "spy_only",
            },
        }
    )
    rows.append(
        {
            "kind": "strategy_config",
            "name": KRONOS_ID,
            "config": {
                "strategy_id": KRONOS_ID,
                "live_eligible": False,
                "traded_this_run": strategy == "kronos",
                "reason": "trades only when examples/kronos_eval.json exists and records the July 2024 cutoff",
            },
        }
    )
    rows.append(
        {
            "kind": "strategy_config",
            "name": AS_SIM_ID,
            "config": {
                "strategy_id": AS_SIM_ID,
                "live_eligible": False,
                "traded_this_run": False,
                "reason": "simulation only; the market-making module never sends quotes to a broker",
            },
        }
    )
    return rows


def _append_trials(ledger: TrialLedger, strategy: str, *, target_vol: str, kelly_frac: str) -> None:
    for row in _configs(strategy, target_vol=target_vol, kelly_frac=kelly_frac, rebalance_every=5):
        ledger.append(kind=row["kind"], name=row["name"], config=row["config"])


def plan_orders(
    *,
    grouped: dict,
    symbols: tuple[str, ...],
    strategy_id: str,
    equity: Decimal,
    positions: list[dict],
    session_day: date,
    limits: PaperLimits,
    lookback: int = PRIMARY_LOOKBACK,
    target_vol: Decimal = Decimal("0.01"),
    kelly_frac: Decimal = Decimal("0.25"),
    targets: dict[str, Decimal] | None = None,
) -> tuple[list[OrderIntent], dict]:
    """Diff vol-targeted fractional-Kelly targets against the current book.

    Notionals are rounded down to the cent before the client order id is hashed, and
    then shrunk again by the per-order and gross-exposure caps. The sizer itself only
    shrinks; this function does not raise its suggestion.
    """
    if targets is None:
        history = {symbol: _history_window(grouped[symbol], lookback) for symbol in symbols}
        targets, bindings = _targets(
            equity,
            list(symbols),
            history,
            lookback=lookback,
            target_vol=target_vol,
            kelly_frac=kelly_frac,
        )
    else:
        bindings = Counter()
        missing = [symbol for symbol in symbols if symbol not in targets]
        if missing:
            raise RunnerError(f"kronos targets are missing {missing}")
    held_value: dict[str, Decimal] = {}
    held_qty: dict[str, Decimal] = {}
    for row in positions:
        symbol = str(row.get("symbol", "")).upper()
        qty = Decimal(str(row.get("qty", "0")))
        value = Decimal(str(row.get("market_value", "0")))
        held_qty[symbol] = qty
        held_value[symbol] = value
    prices = {symbol: grouped[symbol][-1].close_adj for symbol in symbols}
    gross = sum((abs(value) for value in held_value.values()), Decimal(0))
    intents: list[OrderIntent] = []
    skipped: list[dict] = []
    for symbol in symbols:
        target = targets[symbol]
        current = held_value.get(symbol, Decimal(0))
        delta = target - current
        price = prices[symbol]
        if abs(delta) < limits.min_order_notional:
            skipped.append({"symbol": symbol, "reason": "within_min_notional"})
            continue
        if delta > 0:
            room = limits.max_gross_exposure - gross
            notional = _quantize_notional(min(delta, limits.max_notional_per_order, max(room, Decimal(0))))
            if notional < limits.min_order_notional:
                skipped.append({"symbol": symbol, "reason": "shrunk_below_min"})
                continue
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side="buy",
                    notional=notional,
                    qty=None,
                    client_order_id=make_client_order_id(
                        day=session_day.isoformat(), symbol=symbol, side="buy", notional=notional
                    ),
                    strategy_id=strategy_id,
                )
            )
            gross += notional
            continue
        sell_notional = min(-delta, limits.max_notional_per_order)
        qty = (sell_notional / price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        owned = held_qty.get(symbol, Decimal(0))
        if qty > owned:
            qty = owned
        if qty <= 0:
            skipped.append({"symbol": symbol, "reason": "sell_without_position"})
            continue
        notional = _quantize_notional(qty * price)
        if notional < limits.min_order_notional:
            skipped.append({"symbol": symbol, "reason": "shrunk_below_min"})
            continue
        intents.append(
            OrderIntent(
                symbol=symbol,
                side="sell",
                notional=notional,
                qty=qty,
                client_order_id=make_client_order_id(
                    day=session_day.isoformat(), symbol=symbol, side="sell", notional=notional
                ),
                strategy_id=strategy_id,
            )
        )
    return intents, {
        "targets": {symbol: format(value, "f") for symbol, value in targets.items()},
        "binding_counts": dict(bindings),
        "skipped": skipped,
    }


def _positions_from_sim(simulated: dict, grouped: dict, symbols: tuple[str, ...]) -> list[dict]:
    rows = []
    for symbol in symbols:
        qty = simulated["shares"].get(symbol, Decimal(0))
        price = grouped[symbol][-1].close_adj
        rows.append(
            {
                "symbol": symbol,
                "qty": format(qty, "f"),
                "market_value": format(qty * price, "f"),
            }
        )
    return rows


def _paper_fills(orders: list[dict], grouped: dict) -> list[dict]:
    """Join filled paper orders to the local daily bars. Unaligned orders are dropped."""
    fills = []
    for order in orders:
        if str(order.get("status", "")).lower() != "filled":
            continue
        symbol = str(order.get("symbol", "")).upper()
        rows = grouped.get(symbol)
        if not rows:
            continue
        submitted = str(order.get("submitted_at") or order.get("filled_at") or "")
        if len(submitted) < 10:
            continue
        try:
            day = date.fromisoformat(submitted[:10])
        except ValueError:
            continue
        match = next((bar for bar in rows if bar.day == day), None)
        if match is None:
            continue
        previous = [bar for bar in rows if bar.day < day]
        if not previous:
            continue
        qty = order.get("filled_qty")
        price = order.get("filled_avg_price")
        if not qty or not price:
            continue
        fills.append(
            {
                "symbol": symbol,
                "side": str(order.get("side", "")).lower(),
                "qty": str(qty),
                "decision_price": format(previous[-1].close_adj, "f"),
                "arrival_mid": format(match.open_adj, "f"),
                "fill_price": str(price),
                "fee": "0",
                "venue_label": "alpaca_paper",
            }
        )
    return fills


def _equity_returns(history: dict) -> tuple[list[dict], list[float]] | None:
    stamps = history.get("timestamp") or []
    equity = history.get("equity") or []
    if len(stamps) != len(equity) or len(equity) < 2:
        return None
    points = []
    for stamp, value in zip(stamps, equity, strict=True):
        if value is None:
            continue
        day = datetime.fromtimestamp(int(stamp), timezone.utc).date()
        points.append((day, Decimal(str(value))))
    if len(points) < 2:
        return None
    curve = [{"date": day.isoformat(), "equity": format(value, "f")} for day, value in points]
    returns = [
        float((nxt - prev) / prev)
        for (_day, prev), (_nxt, nxt) in zip(points, points[1:], strict=False)
        if prev > 0
    ]
    return curve, returns


def _log_order(log: ChainLog, record: dict) -> None:
    log.append(record)


def run_runner(
    *,
    bars_path: Path = DEFAULT_BARS,
    out: Path = DEFAULT_OUT,
    ledger_path: Path = DEFAULT_LEDGER,
    order_log_path: Path = DEFAULT_ORDER_LOG,
    kill_file: Path | None = None,
    strategy: str = "inverse_vol",
    kronos_report: Path = DEFAULT_KRONOS_REPORT,
    when: str = "auto",
    execute: bool = False,
    force_window: bool = False,
    now: datetime | None = None,
    client: PaperTradeClient | None = None,
    limits: PaperLimits | None = None,
    bootstrap_reps: int = 200,
) -> dict:
    if strategy not in {"inverse_vol", "spy_only", "kronos"}:
        raise RunnerError("strategy must be inverse_vol, spy_only, or kronos")
    moment = now or datetime.now(timezone.utc)
    phase = session_phase(moment)
    try:
        scheduled = resolve_when(when, moment)
    except RunnerError:
        if execute and not force_window:
            raise
        scheduled = when if when in {"open", "close"} else "unscheduled"
    if execute and not force_window and not window_allows(scheduled, moment):
        raise RunnerError(
            f"refusing to execute {scheduled} outside its America/New_York window; "
            "pass --force-window only for a supervised test"
        )
    limits = limits or PaperLimits()
    kill = _kill_path(kill_file)
    grouped = load_bars(bars_path)
    symbols = ("SPY",) if strategy == "spy_only" else ("SPY", "QQQ", "IWM")
    kronos_gate = None
    if strategy == "kronos":
        from sidecar.kronos_signal.gate import KronosGateError, load_kronos_gate

        try:
            kronos_gate = load_kronos_gate(kronos_report)
        except KronosGateError as exc:
            raise RunnerError(str(exc)) from exc
    missing = [symbol for symbol in symbols if symbol not in grouped]
    if missing:
        raise RunnerError(f"bars are missing {missing}")
    strategy_id = {"spy_only": SPY_ONLY_ID, "kronos": KRONOS_ID}.get(strategy, STRATEGY_ID)
    ledger = TrialLedger(ledger_path)
    _append_trials(ledger, strategy, target_vol="0.01", kelly_frac="0.25")
    order_log = ChainLog(order_log_path)
    simulated = _simulate(
        {symbol: grouped[symbol] for symbol in symbols},
        lookback=PRIMARY_LOOKBACK,
        rebalance_every=5,
        starting_equity=Decimal("100000"),
        cost_bps=Decimal("1"),
        target_vol=Decimal("0.01"),
        kelly_frac=Decimal("0.25"),
        collect_fills=True,
    )
    owns_client = False
    account = None
    if strategy == "kronos" and execute:
        from sidecar.kronos_signal.gate import KronosGateError, targets_from_report

        try:
            targets_from_report(
                kronos_gate,
                equity=Decimal("100000"),
                symbols=symbols,
                session_day=moment.astimezone(ET).date(),
                enforce_age=True,
            )
        except KronosGateError as exc:
            raise RunnerError(str(exc)) from exc
    if execute:
        if client is None:
            key, secret = load_sidecar_paper_credentials()
            client = PaperTradeClient(key, secret)
            owns_client = True
        account = client.account()
        broker_positions = client.positions()
        equity = Decimal(str(account["equity"]))
        session_day = moment.astimezone(ET).date()
    else:
        if strategy == "kronos":
            broker_positions = [
                {"symbol": symbol, "qty": "0", "market_value": "0"} for symbol in symbols
            ]
            equity = Decimal("100000")
        else:
            broker_positions = _positions_from_sim(simulated, grouped, symbols)
            equity = simulated["curve"][-1][1]
        session_day = grouped[symbols[0]][-1].day
    kronos_targets = None
    if kronos_gate is not None:
        from sidecar.kronos_signal.gate import KronosGateError, targets_from_report

        try:
            kronos_targets = targets_from_report(
                kronos_gate,
                equity=equity,
                symbols=symbols,
                session_day=session_day,
                enforce_age=execute,
            )
        except KronosGateError as exc:
            raise RunnerError(str(exc)) from exc
    intents, plan = plan_orders(
        grouped=grouped,
        symbols=symbols,
        strategy_id=strategy_id,
        equity=equity,
        positions=broker_positions,
        session_day=session_day,
        limits=limits,
        targets=kronos_targets,
    )
    submitted = 0
    broker_writes = False
    results: list[dict] = []
    killed = kill.exists()
    _log_order(
        order_log,
        {
            "kind": "session",
            "execute": execute,
            "strategy_id": strategy_id,
            "phase": phase,
            "kill_switch": killed,
        },
    )
    if killed:
        for intent in intents:
            record = {
                "kind": "order",
                "result": "refused",
                "reason": "kill_switch",
                "execute": execute,
                "symbol": intent.symbol,
                "side": intent.side,
                "notional": format(intent.notional, "f"),
                "client_order_id": intent.client_order_id,
                "strategy_id": intent.strategy_id,
            }
            _log_order(order_log, record)
            results.append(record)
        if execute and client is not None:
            cancelled = client.cancel_sidecar_orders()
            broker_writes = broker_writes or bool(cancelled)
            _log_order(
                order_log,
                {"kind": "kill_switch", "cancelled_client_order_ids": cancelled, "execute": True},
            )
    else:
        last_equity = None
        if execute and client is not None:
            history = client.portfolio_history()
            parsed = _equity_returns(history) if isinstance(history, dict) else None
            if parsed and len(parsed[0]) >= 2:
                last_equity = Decimal(parsed[0][-2]["equity"])
        for intent in intents:
            try:
                check_limits(
                    intent,
                    equity=equity,
                    last_equity=last_equity,
                    positions=broker_positions,
                    limits=limits,
                    kill_file=kill,
                )
            except LimitBreach as exc:
                record = {
                    "kind": "order",
                    "result": "refused",
                    "reason": exc.reason,
                    "execute": execute,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "notional": format(intent.notional, "f"),
                    "client_order_id": intent.client_order_id,
                    "strategy_id": intent.strategy_id,
                }
                _log_order(order_log, record)
                results.append(record)
                continue
            if not execute:
                record = {
                    "kind": "order",
                    "result": "dry_run",
                    "execute": False,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "notional": format(intent.notional, "f"),
                    "qty": None if intent.qty is None else format(intent.qty, "f"),
                    "client_order_id": intent.client_order_id,
                    "strategy_id": intent.strategy_id,
                }
                _log_order(order_log, record)
                results.append(record)
                continue
            placed = client.place_order(intent)
            if placed.get("result") == "submitted":
                submitted += 1
                broker_writes = True
            record = {
                "kind": "order",
                "result": str(placed.get("result")),
                "execute": True,
                "symbol": intent.symbol,
                "side": intent.side,
                "notional": format(intent.notional, "f"),
                "client_order_id": intent.client_order_id,
                "strategy_id": intent.strategy_id,
            }
            _log_order(order_log, record)
            results.append(record)
    equity_source = "simulated_local_book"
    curve = [
        {"date": day.isoformat(), "equity": format(value, "f")} for day, value in simulated["curve"]
    ]
    returns = [float(value) for _day, value in simulated["returns"]]
    tca_fills = simulated["fills"]
    tca_note = "TCA uses the local next-open simulation. It is not a paper fill."
    caveats = [
        "This runner is an experimental sidecar paper path. It is not the cockpit single order path and must be reconciled at integration time.",
        "Dry-run is the default. Orders are posted only when --execute is set, and only to https://paper-api.alpaca.markets on the sidecar paper account.",
        "The sidecar credential loader does not read the cockpit keyring service or the cockpit env names.",
        "Pre-trade limits can only shrink or refuse a sizer suggestion.",
        "The Avellaneda-Stoikov config on the trial ledger is simulation-only and was not sent to the broker.",
        "No numeric threshold in this sidecar is a frozen qualify gate.",
        "Trailing mean return is a naive edge input to the sizer, not a validated forecast.",
        "Targets are sized from the local daily bar file passed to this process, not from a live quote.",
    ]
    if strategy == "kronos" and kronos_gate is not None:
        stored_curve = kronos_gate.get("equity_curve") or []
        stored_returns = [float(value) for value in kronos_gate.get("strategy_returns") or []]
        if stored_curve and len(stored_returns) >= 8:
            curve = stored_curve
            returns = stored_returns
            equity_source = "kronos_offline_eval"
            tca_fills = []
            tca_note = "The equity curve is the offline Kronos walk-forward. This runner does not recompute it."
        caveats.append(
            "Kronos is eligible only because an offline report exists. live_eligible means the July 2024 protocol finished, not that the forecast beat its nulls."
        )
        caveats.append(
            "The order plan uses the report's last unscored forecast. It does not refit Kronos in this process."
        )
    if execute and client is not None:
        history = client.portfolio_history()
        parsed = _equity_returns(history) if isinstance(history, dict) else None
        if parsed and len(parsed[1]) >= 8:
            curve, returns = parsed
            equity_source = "alpaca_paper_portfolio_history"
        else:
            caveats.append(
                "Broker portfolio history had fewer than 8 returns, so the equity curve is the local simulation, not paper marks."
            )
        closed = client.closed_orders()
        paper_fills = _paper_fills(closed, grouped)
        if paper_fills:
            tca_fills = paper_fills
            tca_note = "TCA uses filled sidecar paper orders joined to local daily bars."
        else:
            caveats.append("No aligned paper fills, so TCA is the local simulation.")
        caveats.append(
            "Alpaca paper fills are simulated by the broker. They are not live-venue impact."
        )
    else:
        caveats.append("orders_submitted is 0 because this run did not post.")
    if killed:
        caveats.append("Kill switch file was present. No new order was allowed.")
    benchmark = _spy_benchmark(grouped.get("SPY"), [day for day, _value in simulated["returns"]])
    periods_per_year = 252
    if equity_source == "kronos_offline_eval":
        benchmark = None
        stored_periods = (kronos_gate.get("evalstats") or {}).get("periods_per_year")
        if isinstance(stored_periods, int) and stored_periods >= 1:
            periods_per_year = stored_periods
        caveats.append(
            "SPY benchmark was omitted because the equity curve is the offline Kronos trial, not this process's inverse-vol simulation."
        )
    elif equity_source != "simulated_local_book":
        benchmark = None
        caveats.append(
            "SPY benchmark was omitted because the equity curve came from the broker and is not aligned to the local bar dates."
        )
    n_trials = ledger.distinct_config_count()
    stats = evaluate(
        returns,
        n_trials,
        benchmark,
        bootstrap_reps=bootstrap_reps,
        n_slices=8,
        seed=0,
        periods_per_year=periods_per_year,
    )
    summary = summarize_fills(tca_fills) if tca_fills else {"n_fills": 0, "note": "no fills"}
    report = {
        "schema_version": "sidecar.daily_report.v1",
        "mode": "sidecar_paper_execute" if execute else "sidecar_paper_dry_run",
        "strategy_id": strategy_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bars_path": _display_path(Path(bars_path)),
        "bars_sha256": hashlib.sha256(Path(bars_path).read_bytes()).hexdigest(),
        "paper_base_url": PAPER_BASE_URL,
        "data_base_url": DATA_BASE_URL,
        "orders_submitted": submitted,
        "broker_writes": broker_writes if execute else False,
        "session_phase": phase,
        "equity_source": equity_source,
        "kill_switch": killed,
        "experimental_order_path": True,
        "account": None
        if account is None
        else {
            "status": account.get("status"),
            "equity": str(account.get("equity")),
        },
        "primary_config": {
            "strategy": strategy,
            "scheduled_when": scheduled,
            "lookback": PRIMARY_LOOKBACK,
            "config_hash": sha256_canonical(
                {
                    "strategy_id": strategy_id,
                    "lookback": PRIMARY_LOOKBACK,
                    "live_eligible": True,
                    "traded_this_run": True,
                }
            ),
            "plan": plan,
            "order_results": results,
        },
        "caveats": caveats + [tca_note],
        "equity_curve": curve,
        "evalstats": stats,
        "tca": {"summary": summary, "note": tca_note},
        "sizer": {
            "rebalances": simulated["rebalances"],
            "binding_counts": {key: int(value) for key, value in simulated["bindings"].items()},
            "mean_gross_exposure": format(simulated["mean_gross"], "f"),
        },
        "calibration": None,
        "llm_audit": None,
        "trial_ledger": {
            "path": _display_path(ledger_path),
            "distinct_configs": n_trials,
            "entries": len(ledger.entries()),
            "head_hash": ledger.head_hash(),
            "order_log": _display_path(order_log_path),
            "order_log_entries": len(order_log.entries()),
        },
    }
    if not execute:
        report["orders_submitted"] = 0
        report["broker_writes"] = False
    validate(report, SCHEMA)
    write_json(out, report)
    if owns_client and client is not None:
        client.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sidecar experimental paper runner. Dry-run unless --execute is set."
    )
    parser.add_argument("--when", choices=("auto", "open", "close"), default="auto")
    parser.add_argument("--execute", action="store_true", help="Post to the sidecar Alpaca paper account")
    parser.add_argument("--force-window", action="store_true", help="Allow --execute outside the session window")
    parser.add_argument("--strategy", choices=("inverse_vol", "spy_only", "kronos"), default="inverse_vol")
    parser.add_argument("--kronos-report", type=Path, default=DEFAULT_KRONOS_REPORT)
    parser.add_argument("--bars", type=Path, default=DEFAULT_BARS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--order-log", type=Path, default=DEFAULT_ORDER_LOG)
    parser.add_argument("--kill-file", type=Path, default=None)
    parser.add_argument("--now", default=None, help="ISO timestamp with offset, for tests")
    parser.add_argument("--print-schedule", action="store_true")
    args = parser.parse_args(argv)
    if args.print_schedule:
        print("Dry-run near the open:  python -m sidecar.harness.runner --when open")
        print("Dry-run near the close: python -m sidecar.harness.runner --when close")
        print("Execute near the close: python -m sidecar.harness.runner --when close --execute")
        print("Cron, weekdays, America/New_York:")
        print("  35 9 * * 1-5  cd sidecar && python -m sidecar.harness.runner --when open")
        print("  45 15 * * 1-5 cd sidecar && python -m sidecar.harness.runner --when close --execute")
        return 0
    try:
        report = run_runner(
            bars_path=args.bars,
            out=args.out,
            ledger_path=args.ledger,
            order_log_path=args.order_log,
            kill_file=args.kill_file,
            strategy=args.strategy,
            kronos_report=args.kronos_report,
            when=args.when,
            execute=args.execute,
            force_window=args.force_window,
            now=_parse_now(args.now),
        )
    except SidecarCredentialsMissing as exc:
        print(str(exc))
        return 2
    except (RunnerError, PaperTradeError) as exc:
        print(str(exc))
        if "outside" in str(exc) or "window" in str(exc):
            return 3
        return 1
    print(f"wrote {args.out}")
    print("mode", report["mode"], "orders_submitted", report["orders_submitted"])
    print("session_phase", report["session_phase"], "kill_switch", report["kill_switch"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
