"""RW-19 read-only market-data evidence capture.

Usage from the repository root:
    .venv/Scripts/python tools/rw19_capture.py alpaca-bars \
        --date 2026-08-11 --symbols AAPL MSFT

Credentials are read from the OS keyring and are never printed or written. The command only
uses historical market-data GET endpoints; it cannot place or modify an order.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.secrets import require_secret  # noqa: E402
from ingest.characterization import (  # noqa: E402
    DEFAULT_THRESHOLDS_BPS,
    utc_day_bounds,
    verify_evidence_manifest,
    write_bar_evidence,
    write_capability_evidence,
    write_market_stream_evidence,
    write_news_evidence,
)

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
NEWS_REST_URL = "https://data.alpaca.markets/v1beta1/news"
NEWS_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"


def _fetch_bars(client: httpx.Client, *, symbols: tuple[str, ...], day: date, feed: str) -> dict:
    start, end = utc_day_bounds(day)
    combined = {symbol: [] for symbol in symbols}
    page_token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "start": start,
            "end": end,
            "feed": feed,
            "limit": 10_000,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        response = client.get(DATA_URL, params=params)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Alpaca {feed} history failed: HTTP {response.status_code}; "
                f"request_id={response.headers.get('X-Request-ID')!r}"
            )
        payload = response.json()
        bars = payload.get("bars") or {}
        for symbol in symbols:
            rows = bars.get(symbol) or []
            if not isinstance(rows, list):
                raise RuntimeError(f"Alpaca {feed} history returned non-list bars for {symbol}")
            combined[symbol].extend(rows)
        page_token = payload.get("next_page_token")
        if not page_token:
            return combined


def _symbols(values: list[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
    if not normalized:
        raise ValueError("--symbols requires at least one explicit symbol")
    if any(not symbol.replace(".", "").replace("-", "").isalnum() for symbol in normalized):
        raise ValueError("symbols must be alphanumeric with optional dot or hyphen")
    return normalized


def capture_alpaca_bars(args) -> Path:
    observation_date = date.fromisoformat(args.date)
    captured_at = datetime.now(UTC)
    if observation_date >= captured_at.date():
        raise ValueError("capture a completed historical UTC day, never today or the future")
    symbols = _symbols(args.symbols)
    headers = {
        "APCA-API-KEY-ID": require_secret("alpaca_key").reveal(),
        "APCA-API-SECRET-KEY": require_secret("alpaca_secret").reveal(),
    }
    with httpx.Client(headers=headers, timeout=args.timeout) as client:
        iex = _fetch_bars(client, symbols=symbols, day=observation_date, feed="iex")
        sip = _fetch_bars(client, symbols=symbols, day=observation_date, feed="sip")
    return write_bar_evidence(
        Path(args.output_root),
        observation_date=observation_date,
        captured_at=captured_at,
        symbols=symbols,
        iex=iex,
        sip=sip,
        environment={
            "capture_host": "local",
            "paper_only": True,
        },
        thresholds_bps=tuple(args.thresholds_bps),
    )


def _capture_news_ws(
    *, key: str, secret: str, symbols: tuple[str, ...], duration_seconds: float,
    connector=None,
) -> tuple[datetime, datetime, list[dict]]:
    if connector is None:
        from websockets.sync.client import connect

        connector = connect

    started = datetime.now(UTC)
    deadline = time.monotonic() + duration_seconds
    rows = []
    with connector(NEWS_WS_URL, open_timeout=15.0, close_timeout=5.0) as socket:
        socket.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
        socket.send(json.dumps({"action": "subscribe", "news": list(symbols)}))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = socket.recv(timeout=remaining)
            except TimeoutError:
                break
            received_at = datetime.now(UTC).isoformat()
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                continue
            messages = decoded if isinstance(decoded, list) else [decoded]
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if message.get("T") == "error":
                    raise RuntimeError(
                        f"Alpaca news stream error code={message.get('code')!r} "
                        f"message={message.get('msg')!r}"
                    )
                if message.get("T") == "n":
                    rows.append({"received_at": received_at, "message": message})
    return started, datetime.now(UTC), rows


def _capture_market_ws(
    *, key: str, secret: str, feed: str, symbols: tuple[str, ...], duration_seconds: float,
    connector=None,
) -> tuple[datetime, datetime, list[dict]]:
    if connector is None:
        from websockets.sync.client import connect

        connector = connect

    url = f"wss://stream.data.alpaca.markets/v2/{feed}"
    started = datetime.now(UTC)
    deadline = time.monotonic() + duration_seconds
    rows = []
    with connector(url, open_timeout=15.0, close_timeout=5.0) as socket:
        socket.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
        socket.send(json.dumps({
            "action": "subscribe", "quotes": list(symbols), "trades": list(symbols),
            "bars": list(symbols),
        }))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = socket.recv(timeout=remaining)
            except TimeoutError:
                break
            received_at = datetime.now(UTC).isoformat()
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                continue
            messages = decoded if isinstance(decoded, list) else [decoded]
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if message.get("T") == "error":
                    raise RuntimeError(
                        f"Alpaca {feed} stream error code={message.get('code')!r} "
                        f"message={message.get('msg')!r}"
                    )
                if message.get("T") in {"q", "t", "b", "c", "x", "s"}:
                    rows.append({"received_at": received_at, "message": message})
    return started, datetime.now(UTC), rows


def _fetch_news_rest(
    client: httpx.Client,
    *,
    symbols: tuple[str, ...],
    start: datetime,
    end: datetime,
) -> list[dict]:
    rows = []
    page_token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "sort": "asc",
            "limit": 50,
            "include_content": "true",
        }
        if page_token:
            params["page_token"] = page_token
        response = client.get(NEWS_REST_URL, params=params)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Alpaca news history failed: HTTP {response.status_code}; "
                f"request_id={response.headers.get('X-Request-ID')!r}"
            )
        payload = response.json()
        page_rows = payload.get("news") or []
        if not isinstance(page_rows, list):
            raise RuntimeError("Alpaca news history returned a non-list payload")
        rows.extend(page_rows)
        page_token = payload.get("next_page_token")
        if not page_token:
            return rows


def capture_alpaca_news(args) -> Path:
    if not 5 <= args.duration_seconds <= 3_600:
        raise ValueError("--duration-seconds must be between 5 and 3600")
    symbols = _symbols(args.symbols)
    key = require_secret("alpaca_key").reveal()
    secret = require_secret("alpaca_secret").reveal()
    start, end, stream_rows = _capture_news_ws(
        key=key,
        secret=secret,
        symbols=symbols,
        duration_seconds=args.duration_seconds,
    )
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    with httpx.Client(headers=headers, timeout=args.timeout) as client:
        rest_rows = _fetch_news_rest(
            client,
            symbols=symbols,
            start=start,
            end=end,
        )
    return write_news_evidence(
        Path(args.output_root),
        window_start=start,
        window_end=end,
        symbols=symbols,
        stream_rows=stream_rows,
        rest_rows=rest_rows,
        environment={"capture_host": "local", "paper_only": True},
    )


def capture_alpaca_market(args) -> Path:
    if not 5 <= args.duration_seconds <= 3_600:
        raise ValueError("--duration-seconds must be between 5 and 3600")
    symbols = _symbols(args.symbols)
    if args.feed == "test" and symbols != ("FAKEPACA",):
        raise ValueError("the always-available Alpaca test feed requires exactly FAKEPACA")
    key = require_secret("alpaca_key").reveal()
    secret = require_secret("alpaca_secret").reveal()
    start, end, rows = _capture_market_ws(
        key=key, secret=secret, feed=args.feed, symbols=symbols,
        duration_seconds=args.duration_seconds,
    )
    return write_market_stream_evidence(
        Path(args.output_root),
        window_start=start,
        window_end=end,
        feed=args.feed,
        symbols=symbols,
        rows=rows,
        environment={"capture_host": "local", "paper_only": True},
    )


def capture_alpaca_capabilities(args) -> Path:
    symbol = _symbols([args.symbol])[0]
    key = require_secret("alpaca_key").reveal()
    secret = require_secret("alpaca_secret").reveal()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    probes = {}
    with httpx.Client(headers=headers, timeout=args.timeout) as client:
        for feed in ("iex", "sip"):
            response = client.get(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest",
                params={"feed": feed},
            )
            try:
                body = response.json()
            except ValueError:
                body = {"invalid_json": True}
            encoded = json.dumps(body, sort_keys=True)
            if key in encoded or secret in encoded:
                raise RuntimeError("Alpaca response unexpectedly reflected a credential")
            probes[f"latest_quote_{feed}"] = {
                "method": "GET",
                "feed": feed,
                "status_code": response.status_code,
                "request_id": response.headers.get("X-Request-ID"),
                "body": body,
            }
    return write_capability_evidence(
        Path(args.output_root),
        captured_at=datetime.now(UTC),
        symbol=symbol,
        probes=probes,
        environment={"capture_host": "local", "paper_only": True},
    )


def verify_manifest(args) -> Path:
    result = verify_evidence_manifest(Path(args.manifest))
    if not result["ok"]:
        raise RuntimeError(f"evidence verification failed: {result['failures']!r}")
    print(json.dumps(result, sort_keys=True))
    return Path(args.manifest)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    bars = commands.add_parser("alpaca-bars", help="capture completed IEX/SIP minute bars")
    bars.add_argument("--date", required=True, help="completed UTC date (YYYY-MM-DD)")
    bars.add_argument("--symbols", nargs="+", required=True, help="explicit measurement universe")
    bars.add_argument("--thresholds-bps", nargs="+", type=int, default=DEFAULT_THRESHOLDS_BPS)
    bars.add_argument("--output-root", default=str(REPO_ROOT / "data" / "evidence" / "rw19"))
    bars.add_argument("--timeout", type=float, default=30.0)
    bars.set_defaults(handler=capture_alpaca_bars)
    news = commands.add_parser("alpaca-news", help="capture bounded news WS and REST identity")
    news.add_argument("--symbols", nargs="+", required=True, help="explicit measurement universe")
    news.add_argument("--duration-seconds", type=float, default=60.0)
    news.add_argument("--output-root", default=str(REPO_ROOT / "data" / "evidence" / "rw19"))
    news.add_argument("--timeout", type=float, default=30.0)
    news.set_defaults(handler=capture_alpaca_news)
    market = commands.add_parser("alpaca-market", help="capture bounded market WebSocket data")
    market.add_argument("--feed", choices=("test", "iex", "sip"), required=True)
    market.add_argument("--symbols", nargs="+", required=True, help="explicit measurement universe")
    market.add_argument("--duration-seconds", type=float, default=60.0)
    market.add_argument("--output-root", default=str(REPO_ROOT / "data" / "evidence" / "rw19"))
    market.set_defaults(handler=capture_alpaca_market)
    capabilities = commands.add_parser(
        "alpaca-capabilities", help="record live IEX/SIP latest-quote entitlement"
    )
    capabilities.add_argument("--symbol", required=True, help="explicit non-sensitive symbol")
    capabilities.add_argument("--timeout", type=float, default=30.0)
    capabilities.add_argument(
        "--output-root", default=str(REPO_ROOT / "data" / "evidence" / "rw19")
    )
    capabilities.set_defaults(handler=capture_alpaca_capabilities)
    verify = commands.add_parser("verify", help="verify raw and summary hashes in one manifest")
    verify.add_argument("manifest")
    verify.set_defaults(handler=verify_manifest)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    manifest = args.handler(args)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
