"""IEX quote and trade socket only. This module cannot place an order."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sidecar.intraday_mm.tape import Event

IEX_URL = "wss://stream.data.alpaca.markets/v2/iex"
ALLOWLIST = ("SPY", "QQQ", "IWM")


class FeedError(RuntimeError):
    """The market-data socket was refused."""


def assert_data_url(url: str) -> None:
    if url != IEX_URL:
        raise FeedError("only the free IEX quote and trade socket is allowed")
    if "sip" in url or "api.alpaca.markets" in url or "paper-api.alpaca.markets" in url:
        raise FeedError("refusing a non-IEX socket")


def subscribe_message(symbols: list[str]) -> dict:
    if not symbols or any(symbol not in ALLOWLIST for symbol in symbols):
        raise FeedError("symbol")
    return {"action": "subscribe", "quotes": list(symbols), "trades": list(symbols)}


def _epoch_ms(text: str) -> int:
    raw = text.replace("Z", "+00:00")
    if "." in raw:
        head, tail = raw.split(".", 1)
        offset = ""
        frac = tail
        for index, char in enumerate(tail):
            if char in "+-":
                frac = tail[:index]
                offset = tail[index:]
                break
        raw = head + "." + frac[:6] + (offset or "+00:00")
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def parse_stream_message(raw) -> list[Event]:
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise FeedError("payload")
    events = []
    for item in data:
        if not isinstance(item, dict):
            raise FeedError("payload")
        action = item.get("action")
        if action in {"order", "replace", "cancel"} or item.get("T") == "order":
            raise FeedError("refusing an order message on the market-data socket")
        kind = item.get("T")
        if kind == "q":
            events.append(
                Event(
                    ts_ms=_epoch_ms(str(item["t"])),
                    kind="quote",
                    symbol=str(item["S"]),
                    bid=float(item["bp"]),
                    ask=float(item["ap"]),
                    bid_size=float(item.get("bs") or 0),
                    ask_size=float(item.get("as") or 0),
                )
            )
        elif kind == "t":
            events.append(
                Event(
                    ts_ms=_epoch_ms(str(item["t"])),
                    kind="trade",
                    symbol=str(item["S"]),
                    price=float(item["p"]),
                    size=float(item["s"]),
                )
            )
    return events


def collect_iex(key_id: str, secret: str, symbols: list[str], minutes: float) -> list[Event]:
    """Block for ``minutes`` on the IEX data socket. No order message is sent."""
    assert_data_url(IEX_URL)
    try:
        import asyncio
        import websockets
    except ImportError as exc:
        raise FeedError("the websockets package is required for a live IEX session") from exc

    async def _run() -> list[Event]:
        events: list[Event] = []
        deadline = asyncio.get_event_loop().time() + minutes * 60.0
        async with websockets.connect(IEX_URL, open_timeout=10) as socket:
            await socket.send(json.dumps({"action": "auth", "key": key_id, "secret": secret}))
            await socket.send(json.dumps(subscribe_message(symbols)))
            while asyncio.get_event_loop().time() < deadline:
                timeout = max(0.1, deadline - asyncio.get_event_loop().time())
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
                except TimeoutError:
                    break
                events.extend(parse_stream_message(raw))
        return events

    return asyncio.run(_run())
