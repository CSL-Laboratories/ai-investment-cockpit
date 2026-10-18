"""Turn Alpaca snapshot, contract, and trade payloads into contract views.

Missing fields stay missing. They are not filled with zero.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sidecar.flow.config import FlowError
from sidecar.flow.score import ContractView, OptionPrint
from sidecar.flow.symbols import parse_occ


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _parse_day(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    return date.fromisoformat(value[:10])


def _snapshot_map(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise FlowError("snapshot payload was not an object")
    body = payload.get("snapshots", payload)
    if not isinstance(body, dict):
        raise FlowError("snapshot payload has no snapshots object")
    return body


def _trade_map(payload: dict) -> dict[str, list]:
    if not isinstance(payload, dict):
        raise FlowError("trade payload was not an object")
    body = payload.get("trades", payload)
    if not isinstance(body, dict):
        raise FlowError("trade payload has no trades object")
    out: dict[str, list] = {}
    for symbol, rows in body.items():
        if isinstance(rows, list):
            out[str(symbol)] = rows
    return out


def _contract_map(payload: dict) -> dict[str, dict]:
    if not isinstance(payload, dict):
        raise FlowError("contract payload was not an object")
    rows = payload.get("option_contracts", [])
    if not isinstance(rows, list):
        raise FlowError("contract payload has no option_contracts list")
    out: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("symbol"), str):
            out[row["symbol"]] = row
    return out


def assemble_chain(
    *,
    underlying: str,
    session_date: date,
    spot: float | None,
    snapshots: dict,
    contracts: dict,
    trades: dict,
) -> list[ContractView]:
    quote_book = _snapshot_map(snapshots)
    reference = _contract_map(contracts)
    tape = _trade_map(trades)
    views: list[ContractView] = []
    for symbol, snap in quote_book.items():
        if not isinstance(symbol, str) or not isinstance(snap, dict):
            continue
        try:
            root, expiration, right, strike = parse_occ(symbol)
        except FlowError:
            continue
        if root != underlying:
            continue
        daily = snap.get("dailyBar") if isinstance(snap.get("dailyBar"), dict) else {}
        quote = snap.get("latestQuote") if isinstance(snap.get("latestQuote"), dict) else {}
        trade = snap.get("latestTrade") if isinstance(snap.get("latestTrade"), dict) else {}
        greeks = snap.get("greeks") if isinstance(snap.get("greeks"), dict) else {}
        meta = reference.get(symbol, {})
        ask = _optional_float(quote.get("ap"))
        prints: list[OptionPrint] = []
        for item in tape.get(symbol, []):
            if not isinstance(item, dict):
                continue
            price = _optional_float(item.get("p"))
            size = _optional_float(item.get("s"))
            stamp = _parse_time(item.get("t"))
            if price is None or size is None or stamp is None:
                continue
            print_ask = _optional_float(item.get("ap"))
            prints.append(
                OptionPrint(
                    price=price,
                    size=size,
                    timestamp=stamp,
                    ask=ask if print_ask is None else print_ask,
                )
            )
        trade_price = _optional_float(trade.get("p"))
        trade_size = _optional_float(trade.get("s"))
        trade_time = _parse_time(trade.get("t"))
        if trade_price is not None and trade_size is not None and trade_time is not None and not prints:
            prints.append(OptionPrint(price=trade_price, size=trade_size, timestamp=trade_time, ask=ask))
        views.append(
            ContractView(
                underlying=underlying,
                symbol=symbol,
                expiration=expiration,
                strike=strike,
                right=right,
                spot=spot,
                volume=_optional_float(daily.get("v")),
                open_interest=_optional_float(meta.get("open_interest")),
                open_interest_date=_parse_day(meta.get("open_interest_date")),
                bid=_optional_float(quote.get("bp")),
                ask=ask,
                delta=_optional_float(greeks.get("delta")),
                iv=_optional_float(snap.get("impliedVolatility")),
                prints=tuple(prints),
                session_date=session_date,
            )
        )
    return views
