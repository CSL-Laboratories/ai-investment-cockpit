"""Queue-aware fills with a retail latency. No broker connection."""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field

MAX_INVENTORY = 10
MARKOUT_HORIZONS_MS = (1000, 5000, 30000)


def queue_ahead(
    side: str,
    price: float,
    bid: float,
    ask: float,
    bid_size: float,
    ask_size: float,
) -> float:
    """Displayed size in front when joining the touch. Zero when improving.

    A price behind the touch returns infinity: only a trade strictly through the
    order can fill it, and the queue is not refreshed until the next decision.
    """
    if side == "buy":
        if price > bid + 1e-9:
            return 0.0
        if abs(price - bid) <= 1e-9:
            return max(float(bid_size), 0.0)
        return math.inf
    if side != "sell":
        raise ValueError("side")
    if price < ask - 1e-9:
        return 0.0
    if abs(price - ask) <= 1e-9:
        return max(float(ask_size), 0.0)
    return math.inf


@dataclass
class Resting:
    side: str
    price: float
    qty: float
    live_ms: int
    queue_ahead: float
    filled: float = 0.0


@dataclass
class _Pending:
    side: str
    price: float
    live_ms: int


@dataclass
class Fill:
    ts_ms: int
    side: str
    price: float
    qty: float
    decision_mid: float


@dataclass
class Book:
    latency_ms: int
    inventory: int = 0
    cash: float = 0.0
    bid: Resting | None = None
    ask: Resting | None = None
    pending: list[_Pending] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    mids: list[tuple[int, float]] = field(default_factory=list)
    last_bid: float | None = None
    last_ask: float | None = None
    last_bid_size: float = 0.0
    last_ask_size: float = 0.0
    last_mid: float | None = None

    def on_quote(self, ts_ms: int, bid: float, ask: float, bid_size: float, ask_size: float) -> None:
        self.last_bid = bid
        self.last_ask = ask
        self.last_bid_size = bid_size
        self.last_ask_size = ask_size
        if ask > bid:
            self.last_mid = (bid + ask) / 2.0
            self.mids.append((ts_ms, self.last_mid))
        self._activate(ts_ms)

    def on_trade(self, ts_ms: int, price: float, size: float) -> None:
        self._activate(ts_ms)
        for order in (self.bid, self.ask):
            if order is None or ts_ms < order.live_ms or size <= 0:
                continue
            if order.side == "buy" and self.inventory >= MAX_INVENTORY:
                continue
            if order.side == "sell" and self.inventory <= -MAX_INVENTORY:
                continue
            filled = _consume(order, price, size)
            if filled > 0:
                self._apply(order, filled, ts_ms)

    def decide(self, ts_ms: int, bid: float | None, ask: float | None, post_bid: bool, post_ask: bool) -> None:
        self.bid = None
        self.ask = None
        self.pending = []
        live = ts_ms + self.latency_ms
        if post_bid and bid is not None and self.inventory < MAX_INVENTORY:
            self.pending.append(_Pending("buy", bid, live))
        if post_ask and ask is not None and self.inventory > -MAX_INVENTORY:
            self.pending.append(_Pending("sell", ask, live))

    def _activate(self, ts_ms: int) -> None:
        if self.last_bid is None or self.last_ask is None:
            return
        still = []
        for pending in self.pending:
            if pending.live_ms > ts_ms:
                still.append(pending)
                continue
            ahead = queue_ahead(
                pending.side,
                pending.price,
                self.last_bid,
                self.last_ask,
                self.last_bid_size,
                self.last_ask_size,
            )
            resting = Resting(pending.side, pending.price, 1.0, pending.live_ms, ahead)
            if pending.side == "buy":
                self.bid = resting
            else:
                self.ask = resting
        self.pending = still

    def _apply(self, order: Resting, qty: float, ts_ms: int) -> None:
        mid = self.last_mid if self.last_mid is not None else order.price
        if order.side == "buy":
            if self.inventory + qty > MAX_INVENTORY:
                return
            self.inventory += int(qty)
            self.cash -= order.price * qty
        else:
            if self.inventory - qty < -MAX_INVENTORY:
                return
            self.inventory -= int(qty)
            self.cash += order.price * qty
        self.fills.append(Fill(ts_ms, order.side, order.price, qty, mid))

    def mtm(self) -> float:
        mid = self.last_mid or 0.0
        return self.cash + self.inventory * mid


def _consume(order: Resting, trade_price: float, trade_size: float) -> float:
    remaining = order.qty - order.filled
    if remaining <= 1e-12:
        return 0.0
    if order.side == "buy":
        if trade_price < order.price - 1e-9:
            order.filled = order.qty
            order.queue_ahead = 0.0
            return remaining
        if trade_price > order.price + 1e-9:
            return 0.0
    else:
        if trade_price > order.price + 1e-9:
            order.filled = order.qty
            order.queue_ahead = 0.0
            return remaining
        if trade_price < order.price - 1e-9:
            return 0.0
    size_left = trade_size
    if math.isfinite(order.queue_ahead) and order.queue_ahead > 0:
        used = min(order.queue_ahead, size_left)
        order.queue_ahead -= used
        size_left -= used
    elif not math.isfinite(order.queue_ahead):
        return 0.0
    fill = min(remaining, max(size_left, 0.0))
    order.filled += fill
    return fill


def mid_at_or_after(mids: list[tuple[int, float]], target_ms: int) -> float | None:
    if not mids:
        return None
    stamps = [row[0] for row in mids]
    index = bisect_left(stamps, target_ms)
    if index >= len(mids):
        return None
    return mids[index][1]


def markout(fill: Fill, mids: list[tuple[int, float]], horizon_ms: int) -> float | None:
    future = mid_at_or_after(mids, fill.ts_ms + horizon_ms)
    if future is None:
        return None
    sign = 1.0 if fill.side == "buy" else -1.0
    return sign * (future - fill.price)


def minute_returns(snapshots: list[tuple[int, float]], capital: float) -> list[float]:
    """Per-minute simple returns of capital + mark-to-market, on a fixed minute grid."""
    if capital <= 0 or len(snapshots) < 2:
        return []
    start = (snapshots[0][0] // 60000) * 60000
    end = (snapshots[-1][0] // 60000) * 60000
    marks = []
    index = 0
    latest = 0.0
    cursor = start
    while cursor <= end:
        while index < len(snapshots) and snapshots[index][0] <= cursor:
            latest = snapshots[index][1]
            index += 1
        marks.append(capital + latest)
        cursor += 60000
    returns = []
    for previous, current in zip(marks, marks[1:], strict=False):
        if previous == 0:
            continue
        returns.append((current - previous) / previous)
    return returns
