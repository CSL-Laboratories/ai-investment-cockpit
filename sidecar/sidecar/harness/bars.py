"""Refresh the runner's allowlisted daily bars from Alpaca market data.

Reads ``https://data.alpaca.markets`` only. Adjusted (``adjustment=all``) IEX daily
bars are written to a CSV the harness already knows how to load. The committed
fixture is not overwritten. Credentials are the sidecar paper pair, the same pair
the experimental runner uses. This module does not place orders.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from sidecar.harness.alpaca import PaperEndpointError, PaperReadClient
from sidecar.harness.backtest import ROOT, load_bars
from sidecar.harness.paper_credentials import load_sidecar_paper_credentials
from sidecar.harness.trade import PaperLimits

ET = ZoneInfo("America/New_York")
HISTORY_START = "2016-01-01"
MAX_STALE_DAYS = 5
MIN_BARS = 30
DEFAULT_REFRESH_PATH = ROOT / "artifacts" / "daily_bars.csv"
_CLOSE = time(16, 0)


class BarsRefreshError(RuntimeError):
    """Adjusted daily bars could not be refreshed. Callers must not trade on the fixture."""


def _number(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BarsRefreshError("a bar field was not a number") from exc
    if not number.is_finite() or number <= 0:
        raise BarsRefreshError("a bar field was non-positive")
    return number


def _session_day(stamp: object) -> date:
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BarsRefreshError("a bar timestamp was not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise BarsRefreshError("a bar timestamp had no timezone")
    return parsed.astimezone(ET).date()


def _keep(day: date, *, as_of: date, clock: datetime) -> bool:
    if day > as_of:
        raise BarsRefreshError("Alpaca returned a bar after the session date")
    if day < as_of:
        return True
    return clock.astimezone(ET).time() >= _CLOSE


def bars_from_alpaca(payload: dict, symbols: tuple[str, ...], *, as_of: date, now: datetime) -> dict:
    """Turn a merged Alpaca bars payload into harness ``Bar`` groups.

    ``adjustment=all`` means open, high, low, and close are already adjusted.
    ``adj_close`` is set equal to ``close``, so the harness adjusted-open scale is 1.
    """
    raw = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise BarsRefreshError("Alpaca bars payload was not a map")
    grouped: dict[str, list] = {}
    for symbol in symbols:
        rows = raw.get(symbol) or []
        if not isinstance(rows, list) or not rows:
            raise BarsRefreshError(f"Alpaca returned no daily bars for {symbol}")
        chosen = []
        for row in rows:
            if not isinstance(row, dict):
                raise BarsRefreshError(f"a {symbol} bar was not an object")
            day = _session_day(row.get("t"))
            if not _keep(day, as_of=as_of, clock=now):
                continue
            close = _number(row.get("c"))
            chosen.append(
                {
                    "day": day,
                    "symbol": symbol,
                    "open": _number(row.get("o")),
                    "high": _number(row.get("h")),
                    "low": _number(row.get("l")),
                    "close": close,
                    "adj_close": close,
                    "volume": _number(row.get("v")),
                }
            )
        if len(chosen) < MIN_BARS:
            raise BarsRefreshError(f"{symbol} has fewer than {MIN_BARS} completed daily bars")
        chosen.sort(key=lambda item: item["day"])
        deduped = []
        for item in chosen:
            if deduped and deduped[-1]["day"] == item["day"]:
                deduped[-1] = item
            else:
                deduped.append(item)
        last = deduped[-1]["day"]
        if (as_of - last).days > MAX_STALE_DAYS:
            raise BarsRefreshError(
                f"{symbol} latest bar {last.isoformat()} is more than {MAX_STALE_DAYS} days before {as_of.isoformat()}"
            )
        grouped[symbol] = deduped
    return grouped


def write_bars_csv(grouped: dict, dest: Path) -> Path:
    lines = ["date,symbol,open,high,low,close,adj_close,volume"]
    for symbol in sorted(grouped):
        for bar in grouped[symbol]:
            lines.append(
                ",".join(
                    [
                        bar["day"].isoformat(),
                        symbol,
                        format(bar["open"], "f"),
                        format(bar["high"], "f"),
                        format(bar["low"], "f"),
                        format(bar["close"], "f"),
                        format(bar["adj_close"], "f"),
                        format(bar["volume"], "f"),
                    ]
                )
            )
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        load_bars(temporary)
    except ValueError as exc:
        temporary.unlink(missing_ok=True)
        raise BarsRefreshError(str(exc)) from exc
    temporary.replace(dest)
    return dest


def refresh_allowlist_daily_bars(
    *,
    dest: Path = DEFAULT_REFRESH_PATH,
    as_of: date,
    now: datetime,
    client: PaperReadClient | None = None,
    symbols: tuple[str, ...] | None = None,
) -> Path:
    """Fetch adjusted daily bars for the allowlist and write ``dest``.

    Does not read the cockpit credential pair. A transport or payload failure raises
    ``BarsRefreshError``. The caller decides whether that blocks orders.
    """
    names = tuple(symbols) if symbols is not None else PaperLimits().allowlist
    owns = client is None
    if client is None:
        key, secret = load_sidecar_paper_credentials()
        client = PaperReadClient(key, secret)
    try:
        try:
            payload = client.adjusted_daily_bars(list(names), start=HISTORY_START)
        except PaperEndpointError as exc:
            raise BarsRefreshError(f"Alpaca daily bars request failed: {exc}") from exc
        grouped = bars_from_alpaca(payload, names, as_of=as_of, now=now)
        return write_bars_csv(grouped, dest)
    finally:
        if owns:
            client.close()
