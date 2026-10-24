"""Replay a quote/trade tape through the two makers. No orders are sent."""

from __future__ import annotations

import numpy as np

from sidecar.intraday_mm.fills import MARKOUT_HORIZONS_MS, Book, markout, minute_returns
from sidecar.intraday_mm.policies import as_quotes, expert_action, features, greedy_quotes, train_clone
from sidecar.intraday_mm.stats import RollingMoments
from sidecar.intraday_mm.tape import Event
from sidecar.rl.sim import MAX_INVENTORY, action_mask


def run_book(
    events: list[Event],
    *,
    symbol: str,
    latency_ms: int,
    quote_fn,
    start_ms: int | None = None,
    end_ms: int | None = None,
    model: str = "legacy",
    initial: str = "send",
    queue_fn: str = "log1p",
) -> dict:
    book = Book(latency_ms=latency_ms, model=model, initial=initial, queue_fn=queue_fn)
    snapshots: list[tuple[int, float]] = []
    last_decision_mid = None
    moments = RollingMoments(20, min_count=5)
    mid0 = None
    decisions = [
        event.ts_ms
        for event in events
        if event.kind == "quote"
        and event.symbol == symbol
        and (start_ms is None or event.ts_ms >= start_ms)
        and (end_ms is None or event.ts_ms < end_ms)
    ]
    # One decision per second.
    decision_times = []
    last_second = None
    for ts in decisions:
        second = ts // 1000
        if second != last_second:
            decision_times.append(ts)
            last_second = second
    decision_set = set(decision_times)
    n_steps = max(len(decision_times), 1)
    step = 0
    for event in events:
        if event.symbol != symbol:
            continue
        if start_ms is not None and event.ts_ms < start_ms:
            continue
        if end_ms is not None and event.ts_ms >= end_ms:
            continue
        if event.kind == "quote" and event.bid is not None and event.ask is not None:
            book.on_quote(event.ts_ms, event.bid, event.ask, event.bid_size or 0.0, event.ask_size or 0.0)
            snapshots.append((event.ts_ms, book.mtm()))
            if mid0 is None and book.last_mid is not None:
                mid0 = book.last_mid
            if event.ts_ms in decision_set and book.last_mid is not None:
                if last_decision_mid is not None:
                    moments.push(book.last_mid - last_decision_mid)
                sigma = moments.std()
                last_ret = 0.0 if last_decision_mid is None else book.last_mid - last_decision_mid
                if sigma is not None and mid0 is not None:
                    tau = max(0.0, 1.0 - step / n_steps)
                    quoted = quote_fn(
                        mid=book.last_mid,
                        inventory=book.inventory,
                        sigma=sigma,
                        tau=tau,
                        step=step,
                        n_steps=n_steps,
                        mtm=book.mtm(),
                        mid0=mid0,
                        last_ret=last_ret,
                    )
                    if quoted is not None:
                        bid, ask, post_bid, post_ask = quoted
                        book.decide(event.ts_ms, bid, ask, post_bid, post_ask)
                last_decision_mid = book.last_mid
                step += 1
        elif event.kind == "trade" and event.price is not None and event.size is not None:
            book.on_trade(event.ts_ms, event.price, event.size)
            if book.last_mid is not None:
                snapshots.append((event.ts_ms, book.mtm()))
    capital = MAX_INVENTORY * (book.mids[0][1] if book.mids else 1.0)
    returns = minute_returns(snapshots, capital)
    markouts = {str(horizon): [] for horizon in MARKOUT_HORIZONS_MS}
    for fill in book.fills:
        for horizon in MARKOUT_HORIZONS_MS:
            value = markout(fill, book.mids, horizon)
            if value is not None:
                markouts[str(horizon)].append(value)
    posts = book.n_posts
    return {
        "n_fills": len(book.fills),
        "n_posts": posts,
        "n_cancels": book.n_cancels,
        "n_requotes": book.n_requotes,
        "n_iv_rejects": book.n_iv_rejects,
        "fill_rate": None if posts <= 0 else len(book.fills) / posts,
        "ending_mtm": book.mtm(),
        "ending_inventory": book.inventory,
        "minute_returns": returns,
        "markouts": markouts,
        "capital": capital,
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _as_fn(**state):
    quoted = as_quotes(state["mid"], state["inventory"], state["sigma"], state["tau"])
    return quoted


def collect_training(
    events: list[Event],
    *,
    symbol: str,
    latency_ms: int,
    start_ms: int,
    end_ms: int,
    model: str = "legacy",
    initial: str = "send",
    queue_fn: str = "log1p",
):
    """Walk the train slice with the Avellaneda-Stoikov inventory path and record labels."""
    book = Book(latency_ms=latency_ms, model=model, initial=initial, queue_fn=queue_fn)
    observations = []
    actions = []
    masks = []
    inventories = []
    moments = RollingMoments(20, min_count=5)
    last_mid = None
    decision_times = []
    last_second = None
    for event in events:
        if event.symbol != symbol or event.kind != "quote":
            continue
        if event.ts_ms < start_ms or event.ts_ms >= end_ms:
            continue
        second = event.ts_ms // 1000
        if second != last_second:
            decision_times.append(event.ts_ms)
            last_second = second
    decision_set = set(decision_times)
    n_steps = max(len(decision_times), 1)
    step = 0
    mid0 = None
    for event in events:
        if event.symbol != symbol:
            continue
        if event.ts_ms < start_ms or event.ts_ms >= end_ms:
            continue
        if event.kind == "quote" and event.bid is not None and event.ask is not None:
            book.on_quote(event.ts_ms, event.bid, event.ask, event.bid_size or 0.0, event.ask_size or 0.0)
            if mid0 is None and book.last_mid is not None:
                mid0 = book.last_mid
            if event.ts_ms in decision_set and book.last_mid is not None and mid0 is not None:
                if last_mid is not None:
                    moments.push(book.last_mid - last_mid)
                sigma = moments.std()
                if sigma is not None:
                    last_ret = 0.0 if last_mid is None else book.last_mid - last_mid
                    action = expert_action(book.last_mid, book.inventory, sigma, max(0.0, 1.0 - step / n_steps))
                    if action is not None:
                        observations.append(
                            features(
                                inventory=book.inventory,
                                last_ret=last_ret,
                                sigma=sigma,
                                step=step,
                                n_steps=n_steps,
                                mtm=book.mtm(),
                                mid0=mid0,
                            )
                        )
                        actions.append(action)
                        masks.append(action_mask(book.inventory))
                        inventories.append(book.inventory)
                        quoted = as_quotes(
                            book.last_mid, book.inventory, sigma, max(0.0, 1.0 - step / n_steps)
                        )
                        if quoted is not None:
                            book.decide(event.ts_ms, quoted[0], quoted[1], quoted[2], quoted[3])
                last_mid = book.last_mid
                step += 1
        elif event.kind == "trade" and event.price is not None and event.size is not None:
            book.on_trade(event.ts_ms, event.price, event.size)
    if not observations:
        return None
    return (
        np.stack(observations),
        np.asarray(actions, dtype=np.int64),
        np.stack(masks),
        np.asarray(inventories, dtype=np.int64),
    )


def evaluate_tape(
    events: list[Event],
    *,
    symbol: str = "SPY",
    latency_ms: int = 75,
    train_fraction: float = 0.6,
    seed: int = 0,
    model: str = "legacy",
    initial: str = "send",
    queue_fn: str = "log1p",
) -> dict:
    if not events:
        raise ValueError("tape is empty")
    start = events[0].ts_ms
    end = events[-1].ts_ms
    split = start + int((end - start) * train_fraction)
    trained = collect_training(
        events,
        symbol=symbol,
        latency_ms=latency_ms,
        start_ms=start,
        end_ms=split,
        model=model,
        initial=initial,
        queue_fn=queue_fn,
    )
    policy = None
    bc_start = None
    bc_end = None
    if trained is not None:
        policy, bc_start, bc_end = train_clone(*trained, seed=seed)
    hidden = np.zeros(policy.hidden) if policy is not None else None

    def rl_fn(**state):
        nonlocal hidden
        if policy is None or hidden is None:
            return None
        observation = features(
            inventory=state["inventory"],
            last_ret=state["last_ret"],
            sigma=state["sigma"],
            step=state["step"],
            n_steps=state["n_steps"],
            mtm=state["mtm"],
            mid0=state["mid0"],
        )
        hidden, bid, ask, post_bid, post_ask = greedy_quotes(
            policy, hidden, observation, state["inventory"], state["mid"]
        )
        return bid, ask, post_bid, post_ask

    common = {"model": model, "initial": initial, "queue_fn": queue_fn}
    as_result = run_book(
        events,
        symbol=symbol,
        latency_ms=latency_ms,
        quote_fn=_as_fn,
        start_ms=split,
        end_ms=end + 1,
        **common,
    )
    rl_result = run_book(
        events,
        symbol=symbol,
        latency_ms=latency_ms,
        quote_fn=rl_fn,
        start_ms=split,
        end_ms=end + 1,
        **common,
    )
    return {
        "split_ms": split,
        "bc_loss_start": bc_start,
        "bc_loss_end": bc_end,
        "as": as_result,
        "rl": rl_result,
    }


def synthetic_tape(minutes: int, *, seed: int = 0, symbol: str = "SPY") -> list[Event]:
    """Deterministic quote/trade tape. The touch is six cents wide so a two-cent quote can improve it."""
    rng = np.random.default_rng(seed)
    mid = 500.0
    start = 1_700_000_000_000
    events: list[Event] = []
    steps = int(minutes * 60 * 10)
    for index in range(steps):
        ts = start + index * 100
        mid = max(50.0, mid + float(rng.normal(0.0, 0.02)))
        events.append(Event(ts, "quote", symbol, mid - 0.03, mid + 0.03, 40.0, 40.0))
        if index % 5 == 4:
            draw = float(rng.random())
            if draw < 0.45:
                price = mid - 0.03
            elif draw < 0.90:
                price = mid + 0.03
            elif draw < 0.95:
                price = mid - 0.08
            else:
                price = mid + 0.08
            events.append(Event(ts, "trade", symbol, price=price, size=15.0))
    return events


def sized_synthetic_tape(minutes: int, *, seed: int = 0, symbol: str = "SPY") -> list[Event]:
    """Same mid path shape as `synthetic_tape`, with size changes, trades, and cancels.

    The constant-size tape never decreases the touch, so it cannot show the
    cancel branch of the queue estimate. This one can. It is still synthetic.
    """
    rng = np.random.default_rng(seed + 17)
    mid = 500.0
    bid_size = 80.0
    ask_size = 80.0
    start = 1_700_000_000_000
    events: list[Event] = []
    steps = int(minutes * 60 * 10)
    for index in range(steps):
        ts = start + index * 100
        mid = max(50.0, mid + float(rng.normal(0.0, 0.02)))
        if float(rng.random()) < 0.25:
            bid_size = max(5.0, bid_size - float(rng.integers(1, 12)))
        if float(rng.random()) < 0.25:
            ask_size = max(5.0, ask_size - float(rng.integers(1, 12)))
        if float(rng.random()) < 0.20:
            bid_size += float(rng.integers(1, 8))
        if float(rng.random()) < 0.20:
            ask_size += float(rng.integers(1, 8))
        events.append(Event(ts, "quote", symbol, mid - 0.03, mid + 0.03, bid_size, ask_size))
        if index % 5 == 4:
            draw = float(rng.random())
            trade_size = 15.0
            if draw < 0.45:
                price = mid - 0.03
                bid_size = max(1.0, bid_size - trade_size)
            elif draw < 0.90:
                price = mid + 0.03
                ask_size = max(1.0, ask_size - trade_size)
            elif draw < 0.95:
                price = mid - 0.08
                bid_size = max(1.0, bid_size - trade_size)
            else:
                price = mid + 0.08
                ask_size = max(1.0, ask_size - trade_size)
            events.append(Event(ts, "trade", symbol, price=price, size=trade_size))
    return events


def _fmt(value: float | None) -> str | None:
    if value is None:
        return None
    return format(float(value), ".12g")


def _summarize(result: dict) -> dict:
    markouts = {}
    for horizon, values in result["markouts"].items():
        markouts[horizon] = {
            "n": len(values),
            "mean": None if _mean(values) is None else format(_mean(values), ".12g"),
        }
    return {
        "n_fills": result["n_fills"],
        "n_posts": result.get("n_posts"),
        "fill_rate": _fmt(result.get("fill_rate")),
        "n_cancels": result.get("n_cancels"),
        "n_requotes": result.get("n_requotes"),
        "n_iv_rejects": result.get("n_iv_rejects"),
        "ending_mtm": format(float(result["ending_mtm"]), ".12g"),
        "ending_inventory": result["ending_inventory"],
        "n_minute_returns": len(result["minute_returns"]),
        "mean_minute_return": None
        if _mean(result["minute_returns"]) is None
        else format(_mean(result["minute_returns"]), ".12g"),
        "markouts": markouts,
    }


def _side_row(result: dict) -> dict:
    summary = _summarize(result)
    return {
        "n_fills": summary["n_fills"],
        "n_posts": summary["n_posts"],
        "fill_rate": summary["fill_rate"],
        "ending_mtm": summary["ending_mtm"],
        "ending_inventory": summary["ending_inventory"],
        "mean_minute_return": summary["mean_minute_return"],
        "n_cancels": summary["n_cancels"],
        "n_requotes": summary["n_requotes"],
        "n_iv_rejects": summary["n_iv_rejects"],
        "markouts": summary["markouts"],
    }


def build_queue_comparison(events: list[Event], *, tape: str, symbol: str = "SPY", note: str) -> dict:
    """Run the legacy fill rule and the Rigtorp estimate on one tape. No orders."""
    legacy = evaluate_tape(events, symbol=symbol, model="legacy")
    rigtorp = evaluate_tape(events, symbol=symbol, model="rigtorp", initial="send", queue_fn="log1p")
    return {
        "tape": tape,
        "note": note,
        "legacy": {"as": _side_row(legacy["as"]), "rl": _side_row(legacy["rl"])},
        "rigtorp": {"as": _side_row(rigtorp["as"]), "rl": _side_row(rigtorp["rl"])},
    }


def _comparison_sentence(row: dict) -> str:
    parts = []
    for name in ("as", "rl"):
        old = row["legacy"][name]
        new = row["rigtorp"][name]
        parts.append(
            f"{name} fills {old['n_fills']} -> {new['n_fills']}, "
            f"fill rate {old['fill_rate']} -> {new['fill_rate']}, "
            f"ending mtm {old['ending_mtm']} -> {new['ending_mtm']}, "
            f"1s markout {old['markouts']['1000']['mean']} (n={old['markouts']['1000']['n']}) -> "
            f"{new['markouts']['1000']['mean']} (n={new['markouts']['1000']['n']})"
        )
    return f"{row['tape']}: " + "; ".join(parts) + "."


def build_queue_report(*, minutes: int = 30, symbol: str = "SPY") -> dict:
    """Compare both fill models. There is no recorded IEX tape in this tree."""
    constant = synthetic_tape(minutes, seed=0, symbol=symbol)
    variable = sized_synthetic_tape(minutes, seed=0, symbol=symbol)
    rows = [
        build_queue_comparison(
            constant,
            tape="synthetic_constant_size",
            symbol=symbol,
            note=(
                "The committed 30-minute tape. Touch size stays 40, so a size decrease from a cancel does not occur. "
                "Trades are the only queue updates."
            ),
        ),
        build_queue_comparison(
            variable,
            tape="synthetic_variable_size",
            symbol=symbol,
            note=(
                "Deterministic tape with joins, cancels, and trades that reduce the displayed size. "
                "It is not a recorded IEX session."
            ),
        ),
    ]
    caveats = [
        "orders_submitted is 0. This comparison does not place an order.",
        "No recorded Alpaca IEX parquet is in this tree. Both sessions are synthetic.",
        "legacy joins the displayed touch size and fills the whole order on a trade strictly through the price.",
        "rigtorp uses V at send time. A trade at or through our price reduces V by the print size. "
        "A cancel reduces V by p*dQ, with p = log1p(V) / (log1p(V) + log1p(size behind)). "
        "log1p was not fit to our fills. The order fills only for the size that remains after V reaches 0.",
        "Intrinsic value is fill probability times the distance to the mid, minus max(0, -mean 1s markout). "
        "A negative value cancels the order. The next decision may post again.",
        "The constant-size session cannot show the cancel branch. Read the variable-size session for that.",
        "Markouts missing at the end of the tape are omitted, not set to zero. Neither session is evidence of edge.",
    ]
    return {
        "schema_version": "sidecar.intraday_mm_queue_compare.v1",
        "orders_submitted": 0,
        "live_trading": False,
        "recorded_iex_session": False,
        "initial_estimate": "send",
        "queue_function": "log1p",
        "minutes": minutes,
        "symbol": symbol,
        "sessions": rows,
        "reading": [_comparison_sentence(row) for row in rows],
        "caveats": caveats,
    }


def build_report(events: list[Event], *, tape: str, symbol: str = "SPY", ledger_path=None) -> dict:
    from sidecar.evalstats.core import EvalStatsError
    from sidecar.evalstats.ledger import TrialLedger
    from sidecar.evalstats.report import evaluate
    from sidecar.jsonutil import sha256_canonical

    primary_latency = 75
    compared = evaluate_tape(events, symbol=symbol, latency_ms=primary_latency, seed=0)
    start = events[0].ts_ms
    end = events[-1].ts_ms
    split = start + int((end - start) * 0.6)
    series = []
    latency_rows = []
    for latency in (50, 75, 100):
        if latency == primary_latency:
            result = compared["as"]
        else:
            result = run_book(
                events,
                symbol=symbol,
                latency_ms=latency,
                quote_fn=_as_fn,
                start_ms=split,
                end_ms=end + 1,
            )
        series.append(result["minute_returns"])
        latency_rows.append({"latency_ms": latency, **_summarize(result), "primary": latency == primary_latency})
    width = min((len(row) for row in series), default=0)
    matrix = [row[-width:] for row in series] if width >= 4 else None
    n_slices = 4 if width >= 4 else None
    ledger = None
    configs = []
    for latency in (50, 75, 100):
        configs.append({"name": f"as_latency_{latency}", "latency_ms": latency, "policy": "avellaneda_stoikov"})
    configs.append(
        {
            "name": "gru_bc_walkforward",
            "latency_ms": primary_latency,
            "policy": "gru_bc",
            "hidden": 8,
            "seed": 0,
            "train_fraction": 0.6,
        }
    )
    n_trials = 1
    head = None
    if ledger_path is not None:
        ledger = TrialLedger(ledger_path)
        for config in configs:
            ledger.append(
                kind="strategy_config",
                name=config["name"],
                config={key: value for key, value in config.items() if key != "name"},
            )
        n_trials = max(ledger.distinct_config_count(), 1)
        head = ledger.head_hash()
    else:
        n_trials = len({sha256_canonical({key: value for key, value in config.items() if key != "name"}) for config in configs})

    def _stats(returns):
        kwargs = {"n_trials": n_trials, "periods_per_year": 252 * 390}
        if matrix is not None and n_slices is not None and returns is series[1]:
            kwargs["trial_matrix"] = matrix
            kwargs["n_slices"] = n_slices
        try:
            return evaluate(returns, **kwargs)
        except EvalStatsError:
            return evaluate(returns, n_trials=n_trials, periods_per_year=252 * 390)

    as_stats = _stats(compared["as"]["minute_returns"])
    rl_stats = evaluate(compared["rl"]["minute_returns"], n_trials=n_trials, periods_per_year=252 * 390)
    as_mean = _mean(compared["as"]["minute_returns"])
    rl_mean = _mean(compared["rl"]["minute_returns"])
    beats_as = False
    difference = None
    if as_mean is not None and rl_mean is not None:
        difference = rl_mean - as_mean
        beats_as = difference > 0
    caveats = [
        "This module never places an order. orders_submitted is 0.",
        "The primary latency is 75 milliseconds, inside a 50–100 millisecond retail assumption. 50 and 100 are trials for PBO on the Avellaneda-Stoikov fill path only.",
        "Queue ahead is the displayed size when the order joins the touch at arrival, and zero when it improves. A price behind the touch fills only on a trade strictly through it.",
        "Trades before the order's live time do not fill. Markouts at 1s, 5s, and 30s are omitted when the tape ends first; they are not filled in as zero.",
        "The GRU is behaviorally cloned, on the earlier 60 percent of the tape, toward the nearest discrete Avellaneda-Stoikov action. The later 40 percent is the comparison. Both books start flat at the split.",
        "Gamma and kappa are the sidecar simulator units. On a one-cent IEX market those quotes can rest behind the touch. This synthetic tape uses a six-cent touch so the quote can improve it. That is not a live IEX result.",
        "beats_as compares mean per-minute returns on one tape. It is not a qualify decision. threshold_status is not_frozen.",
        "Annualized Sharpe scales per-minute returns by sqrt(252 * 390). Read the per-minute mean before the annualized figure.",
    ]
    if len({row["ending_mtm"] for row in latency_rows}) == 1:
        caveats.append(
            "On this tape the 50, 75, and 100 millisecond runs produced the same fills and the same minute returns. "
            "Trades are several hundred milliseconds apart, so every latency in that band is live before the next trade. "
            "PBO is then comparing identical series."
        )
    return {
        "schema_version": "sidecar.intraday_mm_eval.v1",
        "orders_submitted": 0,
        "live_trading": False,
        "tape": tape,
        "symbol": symbol,
        "primary_latency_ms": primary_latency,
        "latency_ms_assumption": "75ms inside the stated 50-100ms retail range",
        "train_fraction": 0.6,
        "bc_loss_start": None if compared["bc_loss_start"] is None else format(compared["bc_loss_start"], ".12g"),
        "bc_loss_end": None if compared["bc_loss_end"] is None else format(compared["bc_loss_end"], ".12g"),
        "as": {**_summarize(compared["as"]), "evalstats": as_stats},
        "rl": {**_summarize(compared["rl"]), "evalstats": rl_stats},
        "latency_trials": latency_rows,
        "difference_mean_minute_return": None if difference is None else format(difference, ".12g"),
        "beats_as": beats_as,
        "ledger_distinct_configs": None if ledger is None else ledger.distinct_config_count(),
        "ledger_head_hash": head,
        "threshold_status": "not_frozen",
        "caveats": caveats,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import datetime, time, timezone
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from sidecar.harness.paper_credentials import SidecarCredentialsMissing, load_sidecar_paper_credentials
    from sidecar.intraday_mm.feed import FeedError, collect_iex
    from sidecar.intraday_mm.tape import read_tape, write_tape
    from sidecar.jsonutil import write_json

    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Simulate two market makers on a tape. Never places an order.")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--synthetic", action="store_true", help="Use the deterministic synthetic tape. No credentials.")
    parser.add_argument(
        "--queue-compare",
        action="store_true",
        help="Write the legacy versus Rigtorp comparison. Simulation only.",
    )
    parser.add_argument("--replay", type=Path, default=None, help="Replay a parquet tape. No credentials.")
    parser.add_argument("--force-window", action="store_true")
    parser.add_argument("--now", default=None)
    parser.add_argument("--out", type=Path, default=root / "examples" / "intraday_mm_eval.json")
    parser.add_argument("--ledger", type=Path, default=root / "examples" / "intraday_mm_eval.ledger.jsonl")
    parser.add_argument("--tape-out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.symbol not in {"SPY", "QQQ", "IWM"}:
        print("symbol must be SPY, QQQ, or IWM")
        return 1

    def _open(moment: datetime) -> bool:
        local = moment.astimezone(ZoneInfo("America/New_York"))
        if local.weekday() >= 5:
            return False
        clock = local.time()
        return time(9, 30) <= clock < time(16, 0)

    try:
        if args.replay is not None:
            events = read_tape(args.replay)
            tape = "replay"
        elif args.synthetic or args.queue_compare:
            events = synthetic_tape(max(1, int(args.minutes)), symbol=args.symbol)
            tape = "synthetic"
        else:
            if args.now:
                moment = datetime.fromisoformat(args.now)
                if moment.tzinfo is None:
                    raise FeedError("now must include a timezone offset")
            else:
                moment = datetime.now(timezone.utc)
            if not args.force_window and not _open(moment):
                raise FeedError("outside the regular session window")
            key_id, secret = load_sidecar_paper_credentials()
            events = collect_iex(key_id, secret, [args.symbol], args.minutes)
            tape = "iex"
            destination = args.tape_out or (root / "artifacts" / "intraday_session.parquet")
            write_tape(destination, events)
    except SidecarCredentialsMissing as exc:
        print(str(exc))
        return 2
    except FeedError as exc:
        print(str(exc))
        if "window" in str(exc):
            return 3
        return 1
    if not events:
        print("tape was empty; refusing to invent a result")
        return 1
    if args.queue_compare:
        report = build_queue_report(minutes=max(1, int(args.minutes)), symbol=args.symbol)
        write_json(args.out, report)
        print(f"wrote {args.out}")
        print("orders_submitted", report["orders_submitted"])
        for line in report["reading"]:
            print(line)
        return 0
    report = build_report(events, tape=tape, symbol=args.symbol, ledger_path=args.ledger)
    write_json(args.out, report)
    print(f"wrote {args.out}")
    print(
        "tape",
        tape,
        "as_fills",
        report["as"]["n_fills"],
        "rl_fills",
        report["rl"]["n_fills"],
        "beats_as",
        report["beats_as"],
        "orders_submitted",
        report["orders_submitted"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
