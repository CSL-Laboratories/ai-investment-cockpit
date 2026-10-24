"""Defined-risk paper runner. Dry-run unless --execute is set.

Posts, when requested, only to the sidecar Alpaca paper account. Regular-session
hours are 09:30–16:00 America/New_York. This is not the cockpit order path.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sidecar.chainlog import ChainLog
from sidecar.harness.paper_credentials import SidecarCredentialsMissing, load_sidecar_paper_credentials
from sidecar.jsonutil import json_ready, write_json
from sidecar.options.client import OptionsClientError, OptionsPaperClient, options_level_approved
from sidecar.options.config import ALLOWLIST, MIN_IV_HISTORY, PRIMARY, RANK_WINDOW, RV_WINDOW
from sidecar.options.plan import (
    ContractQuote,
    plan_actions,
    quote_from_parts,
    signal_ok,
)
from sidecar.options.pricing import realized_vol
from sidecar.options.structures import parse_occ

ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
DEFAULT_KILL = ROOT / "artifacts" / "KILL"
DEFAULT_STATE = ROOT / "artifacts" / "options_state.json"
DEFAULT_IV = ROOT / "artifacts" / "iv_history.jsonl"
DEFAULT_OUT = ROOT / "artifacts" / "options_report.json"
DEFAULT_LOG = ROOT / "artifacts" / "options_orders.jsonl"


class OptionsRunnerError(RuntimeError):
    """The options runner refused to continue."""


def in_regular_session(moment: datetime) -> bool:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise OptionsRunnerError("timestamp must be timezone-aware")
    local = moment.astimezone(ET)
    if local.weekday() >= 5:
        return False
    clock = local.time()
    return time(9, 30) <= clock < time(16, 0)


def _parse_now(text: str | None) -> datetime:
    if text is None:
        return datetime.now(timezone.utc)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise OptionsRunnerError("now must include a timezone offset")
    return moment


def _parse_broker_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, tail = text.split(".", 1)
        offset = ""
        frac = tail
        for index, char in enumerate(tail):
            if char in "+-":
                frac = tail[:index]
                offset = tail[index:]
                break
        text = head + "." + frac[:6] + (offset or "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def load_state(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise OptionsRunnerError("options state was not a list")
    return payload


def save_state(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(rows), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_iv_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise OptionsRunnerError(f"blank iv history line {line_no}")
        row = json.loads(line)
        if not isinstance(row, dict):
            raise OptionsRunnerError(f"iv history line {line_no} is not an object")
        rows.append(row)
    return rows


def append_iv(path: Path, *, day: str, symbol: str, iv: Decimal, existing: list[dict]) -> None:
    if any(row.get("date") == day and row.get("symbol") == symbol for row in existing):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"date": day, "symbol": symbol, "iv": format(iv, "f"), "source": "chain"}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    existing.append(row)


def iv_rank_from_history(
    rows: list[dict],
    *,
    symbol: str,
    today: str,
    current: Decimal,
    minimum: int = MIN_IV_HISTORY,
) -> dict:
    """Percentile of `current` against prior sessions.

    The canonical window is 252. With fewer sessions, down to `minimum`, the
    percentile uses every prior session and `gap` says so. Proxy rows inside
    the window are counted so the report can name them.
    """
    if minimum < 2 or minimum > RANK_WINDOW:
        raise OptionsRunnerError("iv min history must be from 2 through 252")
    prior: list[tuple[str, Decimal, bool]] = []
    for row in rows:
        if row.get("symbol") != symbol or str(row.get("date")) >= today:
            continue
        try:
            value = Decimal(str(row["iv"]))
        except Exception as exc:
            raise OptionsRunnerError("iv history has a non-decimal iv") from exc
        prior.append((str(row["date"]), value, row.get("source") == "proxy"))
    prior.sort(key=lambda item: item[0])
    empty = {
        "rank": None,
        "n_prior": len(prior),
        "window_used": 0,
        "canonical_window": RANK_WINDOW,
        "minimum": minimum,
        "proxy_rows_in_window": 0,
        "gap": f"IV rank needs at least {minimum} prior sessions. This symbol has {len(prior)}.",
    }
    if len(prior) < minimum:
        return empty
    window = prior[-RANK_WINDOW:]
    rank = Decimal(sum(1 for _day, value, _proxy in window if value <= current)) / Decimal(len(window))
    proxy_rows = sum(1 for _day, _value, proxy in window if proxy)
    gaps = []
    if len(window) < RANK_WINDOW:
        gaps.append(
            f"IV rank uses {len(window)} prior sessions. The canonical window is {RANK_WINDOW}."
        )
    if proxy_rows:
        gaps.append(
            "Prior IV includes CBOE index closes divided by 100, tagged source=proxy "
            "(VIX for SPY, VXN for QQQ, RVX for IWM). Those rows are not this chain's ATM implied vol."
        )
    return {
        "rank": rank,
        "n_prior": len(prior),
        "window_used": len(window),
        "canonical_window": RANK_WINDOW,
        "minimum": minimum,
        "proxy_rows_in_window": proxy_rows,
        "gap": " ".join(gaps) if gaps else None,
    }


def _realized_from_bars(bars: dict, symbol: str) -> Decimal | None:
    rows = ((bars.get("bars") or {}).get(symbol)) or []
    closes = []
    for row in rows:
        try:
            closes.append(float(row["c"]))
        except (KeyError, TypeError, ValueError):
            return None
    vol = realized_vol(closes, len(closes) - 1, RV_WINDOW) if closes else None
    if vol is None:
        return None
    return Decimal(str(vol))


def _views_from_client(client: OptionsPaperClient, *, today: date, now: datetime) -> dict[str, list[ContractQuote]]:
    from datetime import timedelta

    expiry_gte = (today + timedelta(days=7)).isoformat()
    expiry_lte = (today + timedelta(days=45)).isoformat()
    views: dict[str, list[ContractQuote]] = {}
    for underlying in ALLOWLIST:
        snaps = client.snapshots(underlying)
        quotes: list[ContractQuote] = []
        for right in ("put", "call"):
            for contract in client.contracts(
                underlying, expiry_gte=expiry_gte, expiry_lte=expiry_lte, right=right
            ):
                symbol = str(contract.get("symbol", ""))
                parsed = parse_occ(symbol)
                snap = snaps.get(symbol)
                if parsed is None or not isinstance(snap, dict):
                    continue
                _root, expiry, occ_right, strike = parsed
                greeks = snap.get("greeks") if isinstance(snap.get("greeks"), dict) else {}
                quote = snap.get("latestQuote") if isinstance(snap.get("latestQuote"), dict) else {}
                delta = quote_from_parts(
                    symbol=symbol,
                    underlying=underlying,
                    expiry=expiry,
                    right=occ_right,
                    strike=strike,
                    delta=_as_decimal(greeks.get("delta")),
                    iv=_as_decimal(snap.get("impliedVolatility")),
                    bid=_as_decimal(quote.get("bp")),
                    ask=_as_decimal(quote.get("ap")),
                    quote_time=_parse_broker_time(quote.get("t")) or datetime.min.replace(tzinfo=timezone.utc),
                    now=now,
                )
                if delta is None:
                    # Missing greeks, a stale quote, or a crossed market. That contract is unusable.
                    if greeks.get("delta") is None or snap.get("impliedVolatility") is None:
                        continue
                    continue
                if occ_right != right:
                    continue
                quotes.append(delta)
        views[underlying] = quotes
    return views


def _as_decimal(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception:
        return Decimal("NaN")
    return number


def _atm_iv(quotes: list[ContractQuote]) -> Decimal | None:
    puts = [row for row in quotes if row.right == "put"]
    if not puts:
        return None
    puts.sort(key=lambda row: abs(abs(row.delta) - Decimal("0.50")))
    nearest = puts[0]
    if abs(abs(nearest.delta) - Decimal("0.50")) > Decimal("0.20"):
        return None
    return nearest.iv


def run_options(
    *,
    execute: bool,
    now: datetime | None = None,
    force_window: bool = False,
    client: OptionsPaperClient | None = None,
    kill_file: Path | None = None,
    state_path: Path | None = None,
    iv_path: Path | None = None,
    out: Path | None = None,
    order_log_path: Path | None = None,
    kind: str = PRIMARY.kind,
    bars: dict | None = None,
    views: dict[str, list[ContractQuote]] | None = None,
    positions: list[dict] | None = None,
    open_orders: list[dict] | None = None,
    options_approved: bool | None = None,
    iv_min_history: int = MIN_IV_HISTORY,
    backfill_proxy: bool = False,
) -> dict:
    moment = now or datetime.now(timezone.utc)
    if not force_window and not in_regular_session(moment):
        raise OptionsRunnerError("outside the regular session window")
    kill = kill_file if kill_file is not None else DEFAULT_KILL
    state_file = state_path or DEFAULT_STATE
    iv_file = iv_path or DEFAULT_IV
    destination = out or DEFAULT_OUT
    log_path = order_log_path or DEFAULT_LOG
    owns_client = False
    if client is None and views is None:
        key_id, secret = load_sidecar_paper_credentials()
        client = OptionsPaperClient(key_id, secret)
        owns_client = True
    local_day = moment.astimezone(ET).date()
    session_day = local_day.isoformat()
    account = None
    approved = options_approved
    broker_positions = positions
    broker_orders = open_orders
    if client is not None and views is None:
        account = client.account()
        if approved is None:
            approved = options_level_approved(account)
        broker_positions = client.positions() if broker_positions is None else broker_positions
        broker_orders = client.open_orders() if broker_orders is None else broker_orders
        if bars is None:
            bars = client.daily_bars(list(ALLOWLIST), start="2016-01-01")
        views = _views_from_client(client, today=local_day, now=moment)
    if views is None:
        raise OptionsRunnerError("no option chain was provided")
    if approved is None:
        approved = False
    if broker_positions is None:
        broker_positions = []
    if broker_orders is None:
        broker_orders = []
    if backfill_proxy:
        from sidecar.options.iv_proxy import backfill_proxy_history

        backfill_proxy_history(iv_file)
    history = load_iv_history(iv_file)
    signals = {}
    iv_rank = {}
    for underlying in ALLOWLIST:
        current = _atm_iv(views.get(underlying) or [])
        if current is not None:
            append_iv(iv_file, day=session_day, symbol=underlying, iv=current, existing=history)
        realized = _realized_from_bars(bars, underlying) if isinstance(bars, dict) else None
        detail = (
            None
            if current is None
            else iv_rank_from_history(
                history,
                symbol=underlying,
                today=session_day,
                current=current,
                minimum=iv_min_history,
            )
        )
        rank = None if detail is None else detail["rank"]
        if detail is None:
            iv_rank[underlying] = {
                "rank": None,
                "n_prior": 0,
                "window_used": 0,
                "canonical_window": RANK_WINDOW,
                "minimum": iv_min_history,
                "proxy_rows_in_window": 0,
                "gap": "No ATM implied vol on the chain, so IV rank was not computed.",
            }
        else:
            iv_rank[underlying] = {
                "rank": None if detail["rank"] is None else format(detail["rank"], "f"),
                "n_prior": detail["n_prior"],
                "window_used": detail["window_used"],
                "canonical_window": detail["canonical_window"],
                "minimum": detail["minimum"],
                "proxy_rows_in_window": detail["proxy_rows_in_window"],
                "gap": detail["gap"],
            }
        signals[underlying] = signal_ok(iv=current, iv_rank=rank, realized=realized)
    state = load_state(state_file)
    working_ids = {str(order.get("client_order_id") or "") for order in broker_orders}
    # Drop state rows that have neither a position nor a working order.
    live_symbols = set()
    for row in broker_positions:
        symbol = str(row.get("symbol", ""))
        if parse_occ(symbol) is None:
            continue
        try:
            qty = Decimal(str(row.get("qty", "0")))
        except Exception as exc:
            raise OptionsRunnerError("position qty is not a decimal") from exc
        if qty != 0:
            live_symbols.add(symbol)
    kept_state = []
    for row in state:
        symbols = {str(leg.get("symbol")) for leg in row.get("legs", [])}
        open_id = str(row.get("open_client_order_id") or "")
        close_id = str(row.get("close_client_order_id") or "")
        if symbols & live_symbols or open_id in working_ids or close_id in working_ids:
            kept_state.append(row)
    state = kept_state
    plan = plan_actions(
        today=local_day,
        session_day=session_day,
        views=views,
        signals=signals,
        state=state,
        positions=broker_positions,
        kill_file=kill,
        kind=kind,
        options_approved=bool(approved),
    )
    order_log = ChainLog(log_path)
    order_log.append(
        {
            "kind": "session",
            "execute": execute,
            "session_day": session_day,
            "kill_switch": kill.exists(),
            "options_approved": bool(approved),
        }
    )
    submitted = 0
    results = []
    if kill.exists() and execute and client is not None:
        cancelled = client.cancel_sidecar_orders()
        order_log.append({"kind": "kill_switch", "cancelled_client_order_ids": cancelled, "execute": True})
    if not execute:
        for body in plan.closes + plan.opens:
            results.append({"result": "dry_run", "client_order_id": body["client_order_id"]})
            order_log.append(
                {
                    "kind": "order",
                    "result": "dry_run",
                    "client_order_id": body["client_order_id"],
                    "execute": False,
                }
            )
    else:
        if client is None:
            raise OptionsRunnerError("execute was set without a paper client")
        save_state(state_file, state)
        for body in plan.closes:
            outcome = client.place_mleg(body, execute=True, opening=False)
            if outcome["result"] == "submitted":
                submitted += 1
            for row in state:
                symbols = [leg["symbol"] for leg in body["legs"]]
                state_symbols = [str(leg.get("symbol")) for leg in row.get("legs", [])]
                if symbols == state_symbols:
                    row["close_client_order_id"] = body["client_order_id"]
            results.append({"result": outcome["result"], "client_order_id": body["client_order_id"]})
            order_log.append(
                {
                    "kind": "order",
                    "result": outcome["result"],
                    "client_order_id": body["client_order_id"],
                    "execute": True,
                    "opening": False,
                }
            )
            save_state(state_file, state)
        for body, state_row in zip(plan.opens, plan.state_upserts, strict=True):
            outcome = client.place_mleg(body, execute=True, opening=True)
            if outcome["result"] == "submitted":
                submitted += 1
                state.append(state_row)
            results.append({"result": outcome["result"], "client_order_id": body["client_order_id"]})
            order_log.append(
                {
                    "kind": "order",
                    "result": outcome["result"],
                    "client_order_id": body["client_order_id"],
                    "execute": True,
                    "opening": True,
                }
            )
            save_state(state_file, state)
    if not execute:
        # Dry-run must not create a position the next session would treat as open.
        pass
    report = {
        "schema_version": "sidecar.options_runner.v1",
        "mode": "sidecar_options_execute" if execute else "sidecar_options_dry_run",
        "orders_submitted": 0 if not execute else submitted,
        "broker_writes": bool(execute and submitted),
        "experimental_order_path": True,
        "paper_base_url": "https://paper-api.alpaca.markets",
        "live_trading": False,
        "session_day": session_day,
        "in_regular_session": in_regular_session(moment),
        "kill_switch": kill.exists(),
        "options_approved": bool(approved),
        "structure": kind,
        "signals": signals,
        "iv_rank": iv_rank,
        "iv_min_history": iv_min_history,
        "n_planned_opens": len(plan.opens),
        "n_planned_closes": len(plan.closes),
        "planned_limit_prices": [body["limit_price"] for body in plan.opens + plan.closes],
        "refusals": plan.refusals,
        "unknown_short": plan.unknown_short,
        "results": results,
        "threshold_status": "not_frozen",
        "caveats": [
            "This runner is an experimental paper path. It is not the cockpit order path.",
            "Opening orders are limit credits (negative limit price) and only defined-risk spreads.",
            (
                "IV rank uses the last 252 prior sessions when that many exist. "
                f"The minimum is {iv_min_history}. A shorter window is named in iv_rank.gap. "
                "Rows tagged source=proxy are CBOE VIX, VXN, or RVX closes divided by 100, "
                "not this chain's ATM implied vol."
            ),
            "Indicative greeks are not the OPRA feed. A missing greek, a stale quote, or an unconfirmed options level refuses the open.",
        ],
    }
    if account is not None:
        report["account_status"] = account.get("status")
    write_json(destination, report)
    if owns_client and client is not None:
        client.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Defined-risk options paper runner. Dry-run unless --execute.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force-window", action="store_true")
    parser.add_argument("--structure", choices=("put_credit", "iron_condor"), default="put_credit")
    parser.add_argument("--now", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--kill-file", type=Path, default=None)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--iv-history", type=Path, default=DEFAULT_IV)
    parser.add_argument("--iv-min-history", type=int, default=MIN_IV_HISTORY)
    parser.add_argument("--backfill-proxy", action="store_true")
    parser.add_argument("--order-log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args(argv)
    try:
        report = run_options(
            execute=args.execute,
            now=_parse_now(args.now),
            force_window=args.force_window,
            kill_file=args.kill_file,
            state_path=args.state,
            iv_path=args.iv_history,
            iv_min_history=args.iv_min_history,
            backfill_proxy=args.backfill_proxy,
            out=args.out,
            order_log_path=args.order_log,
            kind=args.structure,
        )
    except SidecarCredentialsMissing as exc:
        print(str(exc))
        return 2
    except (OptionsRunnerError, OptionsClientError) as exc:
        print(str(exc))
        if "window" in str(exc):
            return 3
        return 1
    print(f"wrote {args.out}")
    print("mode", report["mode"], "orders_submitted", report["orders_submitted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
