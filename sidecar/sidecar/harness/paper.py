"""Read Alpaca paper state and write the same daily report shape.

This module never calls a trading write. If credentials are absent it exits 2 and prints
the provisioning command. It does not invent an equity curve.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from sidecar.harness.alpaca import DATA_BASE_URL, PAPER_BASE_URL, PaperReadClient
from sidecar.harness.backtest import ROOT
from sidecar.harness.secrets import CredentialsMissing, load_alpaca_credentials
from sidecar.evalstats.report import evaluate
from sidecar.jsonutil import validate, write_json
from sidecar.tca.shortfall import summarize_fills

SCHEMA = Path(__file__).resolve().parent / "daily_report.schema.json"
EASTERN = ZoneInfo("America/New_York")


def _dec(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def _history_returns(history: dict) -> tuple[list[dict], list[float], str | None]:
    stamps = history.get("timestamp") or []
    equity = history.get("equity") or []
    if len(stamps) != len(equity) or len(equity) < 2:
        return [], [], "portfolio history is missing or shorter than two points"
    points = []
    for stamp, level in zip(stamps, equity, strict=True):
        if level is None:
            return [], [], "portfolio history contains a null equity point"
        number = _dec(level)
        if number is None or number <= 0:
            return [], [], "portfolio history contains a non-positive equity point"
        day = datetime.fromtimestamp(int(stamp), tz=timezone.utc).astimezone(EASTERN).date()
        points.append({"date": day.isoformat(), "equity": format(number, "f")})
    returns = []
    for prev, nxt in zip(points, points[1:], strict=False):
        before = Decimal(prev["equity"])
        after = Decimal(nxt["equity"])
        returns.append(float((after - before) / before))
    return points, returns, None


def _fills_from_orders(orders: list, bars: dict) -> list[dict]:
    """Join closed orders to daily bars when both prices exist.

    Decision price is the previous session close when a bar is available. Arrival is that
    session's open. Both are proxies and are labeled. Orders without a fill price are skipped.
    """
    fills = []
    bar_map = bars.get("bars") or {}
    for order in orders:
        price = _dec(order.get("filled_avg_price"))
        qty = _dec(order.get("filled_qty"))
        side = str(order.get("side", "")).lower()
        symbol = str(order.get("symbol", "")).upper()
        filled_at = order.get("filled_at")
        if price is None or qty is None or price <= 0 or qty <= 0 or side not in {"buy", "sell"}:
            continue
        if not filled_at:
            continue
        try:
            filled_day = datetime.fromisoformat(str(filled_at).replace("Z", "+00:00")).astimezone(
                EASTERN
            ).date()
        except ValueError:
            continue
        symbol_bars = bar_map.get(symbol) or []
        dated = []
        for bar in symbol_bars:
            try:
                bar_day = datetime.fromisoformat(str(bar.get("t")).replace("Z", "+00:00")).astimezone(
                    EASTERN
                ).date()
            except (TypeError, ValueError):
                continue
            dated.append((bar_day, bar))
        dated.sort()
        today = next((bar for day, bar in dated if day == filled_day), None)
        previous = [bar for day, bar in dated if day < filled_day]
        if today is None or not previous:
            continue
        decision = _dec(previous[-1].get("c"))
        arrival = _dec(today.get("o"))
        if decision is None or arrival is None or decision <= 0 or arrival <= 0:
            continue
        fills.append(
            {
                "symbol": symbol,
                "side": side,
                "qty": format(qty, "f"),
                "decision_price": format(decision, "f"),
                "arrival_mid": format(arrival, "f"),
                "fill_price": format(price, "f"),
                "fee": "0",
                "venue_label": "alpaca_paper",
                "arrival_proxy": "daily_open",
            }
        )
    return fills


def build_paper_report(client: PaperReadClient, *, period: str = "1A") -> dict:
    account = client.account()
    history = client.portfolio_history(period=period)
    orders = client.closed_orders()
    curve, returns, problem = _history_returns(history)
    symbols = sorted({str(order.get("symbol", "")).upper() for order in orders if order.get("symbol")})
    bars: dict = {"bars": {}}
    if symbols and curve:
        bars = client.daily_bars(symbols, start=curve[0]["date"])
    fills = _fills_from_orders(orders, bars if isinstance(bars, dict) else {"bars": {}})
    if problem:
        stats = {
            "schema_version": "sidecar.eval_report.v1",
            "status": "insufficient_sample",
            "reason": problem,
            "n_observations": 0,
            "n_trials": 1,
            "periods_per_year": 252,
            "threshold_status": "not_frozen",
            "notes": ["Paper history could not be scored."],
        }
    else:
        stats = evaluate(returns, 1, bootstrap_reps=200, seed=0)
    summary = summarize_fills(fills)
    equity = _dec(account.get("equity")) if isinstance(account, dict) else None
    cash = _dec(account.get("cash")) if isinstance(account, dict) else None
    report = {
        "schema_version": "sidecar.daily_report.v1",
        "mode": "alpaca_paper_readonly",
        "strategy_id": "sidecar.paper_observer.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_base_url": PAPER_BASE_URL,
        "data_base_url": DATA_BASE_URL,
        "orders_submitted": 0,
        "broker_writes": False,
        "account": {
            "equity": None if equity is None else format(equity, "f"),
            "cash": None if cash is None else format(cash, "f"),
            "status": None if not isinstance(account, dict) else account.get("status"),
        },
        "primary_config": {"observer": "portfolio_history", "period": period, "feed": "iex"},
        "caveats": [
            "Read-only against https://paper-api.alpaca.markets. No order was submitted.",
            "Alpaca paper fills are simulated by Alpaca. TCA on them is a paper-venue calibration.",
            "Decision price is the previous daily close and arrival is the daily open. That is a proxy, not the cockpit decision timestamp.",
            "Market data uses the IEX feed.",
            "No numeric threshold here is a frozen qualify gate.",
            "Account identifiers are omitted from this report.",
        ],
        "equity_curve": curve,
        "evalstats": stats,
        "tca": {"summary": summary, "impact": None},
        "sizer": None,
        "calibration": None,
        "llm_audit": None,
        "trial_ledger": None,
    }
    return validate(report, SCHEMA)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read Alpaca paper and write a daily report")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "paper_report.json")
    parser.add_argument("--period", default="1A")
    args = parser.parse_args(argv)
    try:
        key_id, secret = load_alpaca_credentials()
    except CredentialsMissing as exc:
        print(str(exc))
        print("Command: python -m sidecar.harness.paper --out sidecar/artifacts/paper_report.json")
        print("Env: SIDECAR_ALPACA_KEY_ID and SIDECAR_ALPACA_SECRET")
        print("Or keyring service ai-investment-cockpit names alpaca_key and alpaca_secret")
        return 2
    client = PaperReadClient(key_id, secret)
    try:
        report = build_paper_report(client, period=args.period)
    finally:
        client.close()
    write_json(args.out, report)
    stats = report["evalstats"]
    print(f"wrote {args.out}")
    print(f"status {stats['status']} observations {stats['n_observations']}")
    print("orders_submitted", report["orders_submitted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
