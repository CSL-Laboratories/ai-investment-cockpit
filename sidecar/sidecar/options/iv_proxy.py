"""Proxy IV history from public CBOE index closes.

SPY uses VIX, QQQ uses VXN, and IWM uses RVX. Each close is an index level in
percentage points. Dividing by 100 puts it on the same scale as a decimal
implied vol. The row is tagged source=proxy. It is not the chain's own ATM IV.

CBOE's no-key CSV is the primary file. FRED's close-only series is the fallback.
The stored tail is the most recent 756 sessions, so the pre-2003 VIX methodology
backfill is not what the rank window sees.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import httpx

from sidecar.options.config import ALLOWLIST
from sidecar.options.runner import OptionsRunnerError, load_iv_history

PROXY_TAIL = 756
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
PROXY_SERIES = {
    "SPY": {"symbol": "VIX", "fred": "VIXCLS"},
    "QQQ": {"symbol": "VXN", "fred": "VXNCLS"},
    "IWM": {"symbol": "RVX", "fred": "RVXCLS"},
}
_ALLOWED_HOSTS = frozenset({"cdn.cboe.com", "fred.stlouisfed.org"})


def assert_proxy_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in _ALLOWED_HOSTS:
        raise OptionsRunnerError("refusing a proxy URL outside CBOE and FRED")


def _parse_day(value: str) -> date:
    text = value.strip()
    if "/" in text:
        month, day, year = text.split("/")
        return date(int(year), int(month), int(day))
    return date.fromisoformat(text[:10])


def _level_to_decimal(value: str) -> Decimal | None:
    text = value.strip()
    if text in {"", ".", "NA", "null"}:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number <= 0:
        return None
    return number / Decimal(100)


def _column_map(header: list[str]) -> tuple[int, int] | None:
    names = [cell.strip().upper() for cell in header]
    date_idx = None
    close_idx = None
    for index, name in enumerate(names):
        if name in {"DATE", "OBSERVATION_DATE"}:
            date_idx = index
        elif name in {"CLOSE", "VALUE"} or name.endswith("CLS"):
            close_idx = index
    if date_idx is None or close_idx is None:
        return None
    return date_idx, close_idx


def parse_proxy_csv(text: str, *, underlying: str, proxy_symbol: str, vendor: str) -> list[dict]:
    if underlying not in PROXY_SERIES:
        raise OptionsRunnerError("proxy underlying")
    if vendor not in {"cboe", "fred"}:
        raise OptionsRunnerError("proxy vendor")
    rows: list[dict] = []
    columns: tuple[int, int] | None = None
    seen: set[str] = set()
    for line in text.lstrip("\ufeff").splitlines():
        if not line.strip():
            continue
        cells = [cell.strip() for cell in line.split(",")]
        if columns is None:
            columns = _column_map(cells)
            continue
        if len(cells) <= max(columns):
            continue
        try:
            day = _parse_day(cells[columns[0]])
        except ValueError:
            continue
        level = _level_to_decimal(cells[columns[1]])
        if level is None:
            continue
        key = day.isoformat()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "date": key,
                "symbol": underlying,
                "iv": format(level, "f"),
                "source": "proxy",
                "proxy_symbol": proxy_symbol,
                "proxy_vendor": vendor,
            }
        )
    rows.sort(key=lambda row: row["date"])
    if len(rows) > PROXY_TAIL:
        rows = rows[-PROXY_TAIL:]
    return rows


def _fetch_text(url: str, transport: httpx.BaseTransport | None) -> str:
    assert_proxy_url(url)
    with httpx.Client(transport=transport, timeout=60.0, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "cockpit-sidecar-iv-proxy", "Accept": "text/csv"})
    if response.status_code >= 400:
        raise OptionsRunnerError(f"proxy download failed with status {response.status_code}")
    return response.text


def fetch_proxy_rows(
    underlying: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict]:
    spec = PROXY_SERIES[underlying]
    cboe = CBOE_URL.format(symbol=spec["symbol"])
    try:
        text = _fetch_text(cboe, transport)
        rows = parse_proxy_csv(text, underlying=underlying, proxy_symbol=spec["symbol"], vendor="cboe")
    except (OptionsRunnerError, httpx.HTTPError):
        rows = []
    if rows:
        return rows
    fred = FRED_URL.format(series=spec["fred"])
    text = _fetch_text(fred, transport)
    rows = parse_proxy_csv(text, underlying=underlying, proxy_symbol=spec["fred"], vendor="fred")
    if not rows:
        raise OptionsRunnerError(f"proxy history for {underlying} was empty")
    return rows


def merge_proxy_rows(path: Path, incoming: list[dict]) -> dict:
    """Append proxy rows whose date and symbol are not already in the file.

    A chain row, or an older proxy row, is left untouched.
    """
    existing = load_iv_history(path)
    occupied = {(str(row.get("date")), str(row.get("symbol"))) for row in existing}
    added = []
    for row in sorted(incoming, key=lambda item: (item["date"], item["symbol"])):
        key = (row["date"], row["symbol"])
        if key in occupied:
            continue
        occupied.add(key)
        added.append(row)
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in added:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    return {"appended": len(added), "skipped_existing": len(incoming) - len(added)}


def backfill_proxy_history(
    path: Path,
    *,
    transport: httpx.BaseTransport | None = None,
    symbols: tuple[str, ...] = ALLOWLIST,
) -> dict:
    incoming: list[dict] = []
    vendors: dict[str, str] = {}
    for symbol in symbols:
        if symbol not in PROXY_SERIES:
            raise OptionsRunnerError("proxy underlying")
        rows = fetch_proxy_rows(symbol, transport=transport)
        vendors[symbol] = rows[0]["proxy_vendor"]
        incoming.extend(rows)
    merged = merge_proxy_rows(path, incoming)
    merged["vendors"] = vendors
    merged["symbols"] = list(symbols)
    return merged
