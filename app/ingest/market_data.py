"""RW-14 — one cached Alpaca L1/daily-bars source for every market-data consumer.

The service owns I/O and mutable cache state. ``risk/`` and ``quant/`` remain pure: callers
receive immutable quote dictionaries, :class:`BookSnapshot` values, and deterministic daily
metrics. Quote risk, execution verification, stale-data sampling, and ``/ws/quant`` all read
the same atomic slot so independently timed quote paths cannot disagree.

Remote timestamps are authority-bearing input. A missing, naive, malformed, or future stamp
never becomes a fresh observation. A fetch fault also remains visible until a later successful
poll; the last good price is retained for operator inspection but cannot authorize an order.
"""
from __future__ import annotations

import math
import statistics
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from orchestrator.state import EVENT_MARKET_DATA_RAW
from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import PriceScale, to_ns
from quant.verify import VerifyParams
from storage.parquet_store import (
    content_hash,
    read_snapshot,
    snapshot_path,
    write_snapshot,
)

ANNUAL_TRADING_DAYS = 252
DEFAULT_BAR_LIMIT = 60
DEFAULT_TICK_SIZE = Decimal("0.01")


class MarketDataError(RuntimeError):
    """A sanitized market-data contract failure."""


@dataclass(frozen=True, slots=True)
class MarketMetrics:
    symbol: str
    as_of: date
    median_daily_dollar_volume: Decimal
    realized_volatility: float
    observations: int


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """One atomically published quote/book/raw-evidence tuple."""

    symbol: str
    quote: dict
    book: BookSnapshot
    raw_ref: str
    content_hash: str

    @property
    def identity(self) -> tuple[str, int, int, str, str]:
        return (self.symbol, self.book.seq, self.book.ts, self.raw_ref, self.content_hash)


@dataclass(frozen=True, slots=True)
class _QuoteSlot:
    quote: dict
    exchange_time: datetime
    book: BookSnapshot
    raw_ref: str
    content_hash: str


def parse_exchange_time(value) -> datetime:
    """Parse an RFC3339-like vendor stamp as aware UTC; never invent a timezone."""
    if not isinstance(value, str) or not value.strip():
        raise MarketDataError("quote timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("quote timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("quote timestamp is timezone-naive")
    return parsed.astimezone(timezone.utc)


def _positive_decimal(value, *, field: str) -> Decimal:
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataError(f"{field} is not numeric") from exc
    if not out.is_finite() or out <= 0:
        raise MarketDataError(f"{field} must be positive")
    return out


def _positive_size(value, *, field: str) -> int:
    number = _positive_decimal(value, field=field)
    integral = number.to_integral_value()
    if number != integral:
        raise MarketDataError(f"{field} must be a whole-share size")
    return int(integral)


def calculate_daily_metrics(symbol: str, bars: Iterable[dict]) -> MarketMetrics:
    """Return median daily dollar volume and annualized close-to-close log volatility."""
    clean = str(symbol).strip().upper()
    by_stamp: dict[datetime, tuple[Decimal, Decimal]] = {}
    for row in bars:
        if not isinstance(row, dict):
            continue
        try:
            stamp = parse_exchange_time(row.get("t"))
            close = _positive_decimal(row.get("c"), field="bar close")
            volume = _positive_decimal(row.get("v"), field="bar volume")
        except MarketDataError:
            continue
        by_stamp[stamp] = (close, volume)
    ordered = sorted(by_stamp.items())
    if len(ordered) < 3:
        raise MarketDataError("at least three valid completed daily bars are required")
    closes = [float(values[0]) for _, values in ordered]
    dollar_volumes = [values[0] * values[1] for _, values in ordered]
    returns = [math.log(cur / prev) for prev, cur in zip(closes, closes[1:])]
    realized = statistics.stdev(returns) * math.sqrt(ANNUAL_TRADING_DAYS)
    return MarketMetrics(
        symbol=clean,
        as_of=ordered[-1][0].date(),
        median_daily_dollar_volume=statistics.median(dollar_volumes),
        realized_volatility=realized,
        observations=len(ordered),
    )


class MarketDataService:
    """Poll, validate, cache, persist, and publish one operator-approved symbol universe."""

    def __init__(self, client, *, symbols, clock, raw_root, snapshot_source,
                 additional_symbols_fn=None,
                 audit_log=None,
                 max_age_s: float = 120.0,
                 tick_size: Decimal = DEFAULT_TICK_SIZE) -> None:
        normalized = tuple(sorted({
            str(s).strip().upper() for s in symbols if str(s).strip()
        }))
        if not normalized and additional_symbols_fn is None:
            raise MarketDataError("market data requires an explicit or discoverable symbol universe")
        self._client = client
        self._base_symbols = normalized
        self._symbols = normalized
        self._additional_symbols_fn = additional_symbols_fn
        self._universe_fault: str | None = None
        self._clock = clock
        self._raw_root = Path(raw_root)
        self._snapshot_source = snapshot_source
        self._audit = audit_log
        if not math.isfinite(float(max_age_s)) or float(max_age_s) <= 0:
            raise MarketDataError("market-data max age must be a positive finite number")
        self._max_age_s = float(max_age_s)
        self._scale = PriceScale(tick_size=tick_size)
        self._slots: dict[str, _QuoteSlot] = {}
        self._faults: dict[str, str] = {symbol: "not_polled" for symbol in normalized}
        self._metrics: dict[str, MarketMetrics] = {}
        self._seq: dict[str, int] = {symbol: 0 for symbol in normalized}
        self._bars_loaded_for: date | None = None
        self._audited_raw: set[tuple[str, str]] = set()
        self._lock = threading.RLock()

    @property
    def symbols(self) -> tuple[str, ...]:
        with self._lock:
            return self._symbols

    @property
    def snapshot_source(self):
        return self._snapshot_source

    @property
    def max_age_s(self) -> float:
        return self._max_age_s

    @property
    def verify_params(self) -> VerifyParams:
        """Execution-verdict freshness aligned with the authoritative quote-age limit."""
        return VerifyParams(
            scale=self._scale,
            max_staleness_ns=int(self._max_age_s * 1_000_000_000),
        )

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise MarketDataError("market-data clock returned a timezone-naive datetime")
        return now.astimezone(timezone.utc)

    def _refresh_symbols(self) -> tuple[str, ...]:
        """Merge holdings into the configured watchlist without hiding discovery faults.

        A position may legitimately outlive a watchlist edit. Polling only the watchlist would
        then make the risk engine allow a sell that the freshness guard can never observe.
        Discovery is refreshed by the same scheduled I/O owner; if it fails, the previous
        universe remains available for awareness but the global freshness signal fails closed.
        """
        if self._additional_symbols_fn is None:
            return self.symbols
        try:
            extra = tuple(sorted({
                str(symbol).strip().upper()
                for symbol in self._additional_symbols_fn()
                if str(symbol).strip()
            }))
        except Exception:  # noqa: BLE001 - unknown holdings cannot grant order authority
            with self._lock:
                self._universe_fault = "additional_symbol_discovery_unavailable"
                return self._symbols

        combined = tuple(dict.fromkeys((*self._base_symbols, *extra)))
        with self._lock:
            removed = set(self._symbols) - set(combined)
            for symbol in removed:
                self._slots.pop(symbol, None)
                self._faults.pop(symbol, None)
                self._metrics.pop(symbol, None)
                self._seq.pop(symbol, None)
            for symbol in combined:
                if symbol not in self._seq:
                    self._seq[symbol] = 0
                    self._faults[symbol] = "not_polled"
            self._symbols = combined
            self._universe_fault = None
            return self._symbols

    def _book(self, symbol: str, quote: dict, stamp: datetime, seq: int) -> BookSnapshot:
        bid = _positive_decimal(quote.get("bid"), field="bid")
        ask = _positive_decimal(quote.get("ask"), field="ask")
        if ask < bid:
            raise MarketDataError("quote is crossed")
        bid_size = _positive_size(quote.get("bid_size"), field="bid size")
        ask_size = _positive_size(quote.get("ask_size"), field="ask size")
        try:
            bid_ticks = self._scale.to_ticks(bid)
            ask_ticks = self._scale.to_ticks(ask)
        except (TypeError, ValueError) as exc:
            raise MarketDataError("quote price is off the configured tick grid") from exc
        return BookSnapshot(
            symbol=symbol,
            seq=seq,
            ts=to_ns(stamp),
            bids=(LevelSnapshot(bid_ticks, bid_size, 1),),
            asks=(LevelSnapshot(ask_ticks, ask_size, 1),),
        )

    @staticmethod
    def _raw_quote(quote: dict, book: BookSnapshot) -> dict:
        return {
            "symbol": book.symbol,
            "exchange_ts": str(quote["ts"]),
            "exchange_ts_ns": book.ts,
            "seq": book.seq,
            "price": str(quote["price"]),
            "spread_pct": str(quote["spread_pct"]),
            "bid": str(quote["bid"]),
            "ask": str(quote["ask"]),
            "bid_size": int(quote["bid_size"]),
            "ask_size": int(quote["ask_size"]),
            "feed": str(quote.get("feed") or "iex"),
        }

    def _bind_raw(self, *, kind: str, day: str, key: str, digest: str, count: int) -> None:
        identity = (day, key)
        with self._lock:
            if identity in self._audited_raw:
                return
        if self._audit is not None:
            self._audit.append(
                EVENT_MARKET_DATA_RAW,
                actor="market_data",
                refs={"raw_ref": f"market-data://{day}/{key}"},
                payload={
                    "kind": kind,
                    "date": day,
                    "raw_key": key,
                    "content_hash": digest,
                    "count": count,
                },
            )
        with self._lock:
            self._audited_raw.add(identity)

    def _require_raw_binding(
        self, *, kind: str, day: str, key: str, digest: str, count: int
    ) -> None:
        """Accept a pre-existing cache only when it was signed before this read."""
        if self._audit is None:
            return
        verified_events = getattr(self._audit, "verified_events", None)
        if verified_events is None:
            raise MarketDataError("audit log cannot verify pre-existing raw snapshots")
        raw_ref = f"market-data://{day}/{key}"
        matches = [
            event for event in verified_events(EVENT_MARKET_DATA_RAW)
            if (event.refs or {}).get("raw_ref") == raw_ref
        ]
        expected = (kind, day, key, digest, count)
        bindings = {
            (
                (event.payload or {}).get("kind"),
                (event.payload or {}).get("date"),
                (event.payload or {}).get("raw_key"),
                (event.payload or {}).get("content_hash"),
                (event.payload or {}).get("count"),
            )
            for event in matches
        }
        if bindings != {expected}:
            raise MarketDataError("pre-existing raw snapshot lacks one unambiguous signed binding")
        with self._lock:
            self._audited_raw.add((day, key))

    def _persist_quote(self, quote: dict, book: BookSnapshot) -> tuple[str, str]:
        root = self._raw_root / "quotes"
        stamp = datetime.fromtimestamp(book.ts / 1_000_000_000, tz=timezone.utc)
        day = stamp.date().isoformat()
        key = f"{book.symbol}-quote-{book.ts}-{book.seq}"
        raw = self._raw_quote(quote, book)
        created = False
        try:
            _, digest = write_snapshot(root, day, key, [raw])
            created = True
        except FileExistsError:
            existing = read_snapshot(root, day, key)
            if existing != [raw]:
                raise MarketDataError("pre-existing quote snapshot conflicts with the observation")
            digest = content_hash(existing)
            self._require_raw_binding(
                kind="quote", day=day, key=key, digest=digest, count=1
            )
        if created:
            self._bind_raw(kind="quote", day=day, key=key, digest=digest, count=1)
        return f"market-data://{day}/{key}", digest

    def poll_quotes(self) -> dict:
        """Poll every symbol once. Faults are retained as fail-closed freshness state."""
        poll_started_at = self._now()
        symbols = self._refresh_symbols()
        updated: list[str] = []
        failed: list[str] = []
        for symbol in symbols:
            try:
                quote = dict(self._client.latest_quote(symbol))
                observed_at = self._now()
                stamp = parse_exchange_time(quote.get("ts"))
                if stamp > observed_at:
                    raise MarketDataError("quote timestamp is in the future")
                with self._lock:
                    seq = self._seq[symbol] + 1
                book = self._book(symbol, quote, stamp, seq)
                bid = _positive_decimal(quote.get("bid"), field="bid")
                ask = _positive_decimal(quote.get("ask"), field="ask")
                price = (bid + ask) / Decimal("2")
                spread_pct = Decimal("100") * (ask - bid) / price
                normalized = dict(quote)
                normalized.update({
                    "symbol": symbol,
                    "price": price,
                    "spread_pct": spread_pct,
                    "ts_ns": book.ts,
                    "age_s": (observed_at - stamp).total_seconds(),
                })
                raw_ref, digest = self._persist_quote(normalized, book)
                normalized.update({"raw_ref": raw_ref, "content_hash": digest})
                slot = _QuoteSlot(normalized, stamp, book, raw_ref, digest)
                with self._lock:
                    self._slots[symbol] = slot
                    self._seq[symbol] = seq
                    self._faults.pop(symbol, None)
                from orchestrator.quant_ws import engine_snapshot

                self._snapshot_source.publish(
                    engine_snapshot(symbol=symbol, book=book, ts=book.ts, levels=1)
                )
                updated.append(symbol)
            except Exception:  # noqa: BLE001 - remote/parse/storage faults are per-symbol state
                with self._lock:
                    self._faults[symbol] = "quote_unavailable"
                failed.append(symbol)
        return {"updated": updated, "failed": failed, "at": poll_started_at.isoformat()}

    def observation(self, symbol: str) -> MarketObservation:
        clean = str(symbol).strip().upper()
        now = self._now()
        with self._lock:
            slot = self._slots.get(clean)
            fault = self._faults.get(clean)
            if slot is None or fault is not None:
                raise MarketDataError(f"no current valid quote for {clean}")
            out = dict(slot.quote)
            book = slot.book
            raw_ref = slot.raw_ref
            digest = slot.content_hash
            exchange_time = slot.exchange_time
        out["age_s"] = (now - exchange_time).total_seconds()
        if out["age_s"] < 0:
            raise MarketDataError(f"quote timestamp is in the future for {clean}")
        return MarketObservation(clean, out, book, raw_ref, digest)

    def quote(self, symbol: str) -> dict:
        return self.observation(symbol).quote

    def book(self, symbol: str) -> BookSnapshot | None:
        clean = str(symbol).strip().upper()
        with self._lock:
            slot = self._slots.get(clean)
            fault = self._faults.get(clean)
            return None if slot is None or fault is not None else slot.book

    def stale_reason(self, symbol: str, *, now: datetime, limit_s: float) -> str | None:
        clean = str(symbol).strip().upper()
        if now.tzinfo is None or now.utcoffset() is None:
            return "market-data clock is timezone-naive"
        with self._lock:
            universe_fault = self._universe_fault
            slot = self._slots.get(clean)
            faulted = tuple(sorted(self._faults))
        if universe_fault is not None:
            return "market-data symbol universe is unavailable"
        if faulted:
            return "market quote unavailable for bounded universe: " + ", ".join(faulted)
        if slot is None:
            return f"no market quote observed for {clean}"
        age = (now.astimezone(timezone.utc) - slot.exchange_time).total_seconds()
        if age < 0:
            return f"market quote timestamp is in the future for {clean}"
        if age > limit_s:
            return f"market quote for {clean} is stale ({age:.3f}s > {limit_s:.3f}s)"
        return None

    def max_quote_age_s(self, *, now: datetime) -> float:
        if now.tzinfo is None or now.utcoffset() is None:
            return float("inf")
        current = now.astimezone(timezone.utc)
        ages: list[float] = []
        with self._lock:
            if self._universe_fault is not None:
                return float("inf")
            for symbol in self._symbols:
                if symbol in self._faults or symbol not in self._slots:
                    return float("inf")
                ages.append((current - self._slots[symbol].exchange_time).total_seconds())
        if not ages or any(age < 0 for age in ages):
            return float("inf")
        return max(ages)

    @staticmethod
    def _completed_bars(rows, today: date) -> list[dict]:
        completed = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                if parse_exchange_time(row.get("t")).date() < today:
                    completed.append(row)
            except MarketDataError:
                continue
        return completed

    def refresh_daily_bars(self) -> dict:
        """Refresh once per UTC day, reusing the write-once raw cache across restarts."""
        today = self._now().date()
        symbols = self._refresh_symbols()
        with self._lock:
            if self._bars_loaded_for == today:
                return {"updated": [], "cached": list(self._metrics), "at": today.isoformat()}
        updated: list[str] = []
        cached: list[str] = []
        failed: list[str] = []
        root = self._raw_root / "daily-bars"
        for symbol in symbols:
            path = snapshot_path(root, today.isoformat(), symbol)
            try:
                if path.exists():
                    rows = read_snapshot(root, today.isoformat(), symbol)
                    completed = self._completed_bars(rows, today)
                    if not isinstance(rows, list) or len(completed) != len(rows):
                        raise MarketDataError("cached daily bars contain invalid or incomplete rows")
                    digest = content_hash(rows)
                    self._require_raw_binding(
                        kind="daily_bars",
                        day=today.isoformat(),
                        key=symbol,
                        digest=digest,
                        count=len(rows),
                    )
                    disposition = cached
                else:
                    rows = self._completed_bars(
                        self._client.daily_bars(symbol, limit=DEFAULT_BAR_LIMIT), today
                    )
                    calculate_daily_metrics(symbol, rows)
                    _, digest = write_snapshot(root, today.isoformat(), symbol, rows)
                    rows = read_snapshot(root, today.isoformat(), symbol)
                    disposition = updated
                    self._bind_raw(
                        kind="daily_bars",
                        day=today.isoformat(),
                        key=symbol,
                        digest=digest,
                        count=len(rows),
                    )
                metric = calculate_daily_metrics(symbol, rows)
                with self._lock:
                    self._metrics[symbol] = metric
                disposition.append(symbol)
            except Exception:  # noqa: BLE001 - one symbol cannot hide the others' metrics
                failed.append(symbol)
        if not failed:
            with self._lock:
                self._bars_loaded_for = today
        return {"updated": updated, "cached": cached, "failed": failed,
                "at": today.isoformat()}

    def metrics(self, symbol: str) -> MarketMetrics | None:
        with self._lock:
            return self._metrics.get(str(symbol).strip().upper())

    def status(self) -> dict:
        with self._lock:
            return {
                "symbols": list(self._symbols),
                "universe_fault": self._universe_fault,
                "quoted": sorted(self._slots),
                "faulted": sorted(self._faults),
                "metrics": sorted(self._metrics),
                "max_age_s": self._max_age_s,
                "book_seq": {symbol: self._seq[symbol] for symbol in self._symbols},
            }
