"""Queue-aware fills with a retail latency. No broker connection.

`legacy` is the original touch-size queue. `rigtorp` is Erik Rigtorp's
market-by-level estimate (https://rigtorp.se/2013/06/08/estimating-order-queue-position.html).
The tape does not contain this simulator's orders. Nothing here submits one.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field

from sidecar.intraday_mm.stats import RollingMoments

MAX_INVENTORY = 10
MARKOUT_HORIZONS_MS = (1000, 5000, 30000)
ADVERSE_HORIZON_MS = 1000
INITIAL_ESTIMATES = ("send", "ack", "average")
QUEUE_FUNCTIONS = ("identity", "log1p")
FILL_MODELS = ("legacy", "rigtorp")


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


def _shape(kind: str, value: float) -> float:
    if value <= 0:
        return 0.0
    if kind == "identity":
        return value
    if kind == "log1p":
        return math.log1p(value)
    raise ValueError("queue function")


def visible_size(
    side: str,
    price: float,
    bid: float,
    ask: float,
    bid_size: float,
    ask_size: float,
) -> float | None:
    """Aggregate size at `price` on a top-of-book feed. None when the level is hidden."""
    if side == "buy":
        if price > bid + 1e-9:
            return 0.0
        if abs(price - bid) <= 1e-9:
            return max(float(bid_size), 0.0)
        return None
    if side != "sell":
        raise ValueError("side")
    if price < ask - 1e-9:
        return 0.0
    if abs(price - ask) <= 1e-9:
        return max(float(ask_size), 0.0)
    return None


def queue_decrement_probability(ahead: float, quantity: float, level: float, function: str) -> float:
    """Probability a cancel, not a trade, removes size in front of us.

    p = f(V) / (f(V) + f(max(Q - S - V, 0))). `level` is the tape size at our
    price. The tape does not include our order, so Q = level + S and the size
    behind us is max(level - V, 0).
    """
    if not math.isfinite(ahead) or ahead <= 0:
        return 0.0
    behind = max(level - ahead, 0.0)
    numer = _shape(function, ahead)
    denom = numer + _shape(function, behind)
    if denom <= 0:
        return 1.0
    return numer / denom


def fill_probability(ahead: float, quantity: float, function: str) -> float:
    """Chance the next lots that reach this level get to our order."""
    if not math.isfinite(ahead):
        return 0.0
    if ahead <= 1e-12:
        return 1.0
    numer = _shape(function, quantity)
    denom = numer + _shape(function, ahead)
    if denom <= 0:
        return 0.0
    return numer / denom


def quote_edge(side: str, price: float, mid: float) -> float:
    if side == "buy":
        return mid - price
    if side == "sell":
        return price - mid
    raise ValueError("side")


def intrinsic_value(
    side: str,
    price: float,
    mid: float,
    ahead: float,
    quantity: float,
    function: str,
    adverse_cost: float,
) -> float:
    """Fill-probability times the quote edge, minus adverse-selection cost.

    Adverse cost is max(0, -mean 1s markout). It is zero until a markout exists.
    A negative value is the cancel signal. It is not a fitted forecast.
    """
    expected = fill_probability(ahead, quantity, function) * quote_edge(side, price, mid)
    return expected - max(0.0, adverse_cost)


def _hits_level(side: str, order_price: float, trade_price: float) -> bool:
    if side == "buy":
        return trade_price <= order_price + 1e-9
    return trade_price >= order_price - 1e-9


@dataclass
class Resting:
    side: str
    price: float
    qty: float
    live_ms: int
    queue_ahead: float
    filled: float = 0.0
    last_level: float | None = None
    trade_applied: float = 0.0
    intrinsic: float | None = None


@dataclass
class _Pending:
    side: str
    price: float
    live_ms: int
    q_send: float | None = None


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
    model: str = "legacy"
    initial: str = "send"
    queue_fn: str = "log1p"
    n_posts: int = 0
    n_cancels: int = 0
    n_requotes: int = 0
    n_iv_rejects: int = 0
    adverse: RollingMoments = field(default_factory=lambda: RollingMoments(20, min_count=1))
    _adverse_cursor: int = 0
    _cancelled_bid: bool = False
    _cancelled_ask: bool = False

    def __post_init__(self) -> None:
        if self.model not in FILL_MODELS:
            raise ValueError("fill model")
        if self.initial not in INITIAL_ESTIMATES:
            raise ValueError("initial queue estimate")
        if self.queue_fn not in QUEUE_FUNCTIONS:
            raise ValueError("queue function")

    def on_quote(self, ts_ms: int, bid: float, ask: float, bid_size: float, ask_size: float) -> None:
        self.last_bid = bid
        self.last_ask = ask
        self.last_bid_size = bid_size
        self.last_ask_size = ask_size
        if ask > bid:
            self.last_mid = (bid + ask) / 2.0
            self.mids.append((ts_ms, self.last_mid))
        self._activate(ts_ms)
        if self.model == "rigtorp":
            self._rigtorp_quote(ts_ms)
            self._settle_adverse(ts_ms)
            self._cancel_if_worthless()

    def on_trade(self, ts_ms: int, price: float, size: float) -> None:
        self._activate(ts_ms)
        if self.model == "rigtorp":
            self._rigtorp_trade(ts_ms, price, size)
            self._settle_adverse(ts_ms)
            self._cancel_if_worthless()
            return
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
            self._propose("buy", bid, live)
        if post_ask and ask is not None and self.inventory > -MAX_INVENTORY:
            self._propose("sell", ask, live)

    def _propose(self, side: str, price: float, live_ms: int) -> None:
        q_send = None
        if self.last_bid is not None and self.last_ask is not None:
            q_send = visible_size(side, price, self.last_bid, self.last_ask, self.last_bid_size, self.last_ask_size)
        if self.model == "rigtorp" and self.last_mid is not None:
            ahead = math.inf if q_send is None else q_send
            value = intrinsic_value(side, price, self.last_mid, ahead, 1.0, self.queue_fn, self._adverse_cost())
            if value < 0:
                self.n_iv_rejects += 1
                return
        if side == "buy" and self._cancelled_bid:
            self.n_requotes += 1
            self._cancelled_bid = False
        if side == "sell" and self._cancelled_ask:
            self.n_requotes += 1
            self._cancelled_ask = False
        self.pending.append(_Pending(side, price, live_ms, q_send))
        self.n_posts += 1

    def _adverse_cost(self) -> float:
        mean = self.adverse.mean()
        if mean is None:
            return 0.0
        return max(0.0, -mean)

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
            level = visible_size(
                pending.side,
                pending.price,
                self.last_bid,
                self.last_ask,
                self.last_bid_size,
                self.last_ask_size,
            )
            if self.model == "rigtorp":
                ahead = _initial_queue(self.initial, pending.q_send, level)
            resting = Resting(pending.side, pending.price, 1.0, pending.live_ms, ahead, last_level=level)
            if pending.side == "buy":
                self.bid = resting
            else:
                self.ask = resting
        self.pending = still

    def _rigtorp_quote(self, ts_ms: int) -> None:
        if self.last_bid is None or self.last_ask is None:
            return
        for order in (self.bid, self.ask):
            if order is None or ts_ms < order.live_ms:
                continue
            level = visible_size(
                order.side,
                order.price,
                self.last_bid,
                self.last_ask,
                self.last_bid_size,
                self.last_ask_size,
            )
            previous = order.last_level
            order.last_level = level
            if previous is None or level is None or not math.isfinite(order.queue_ahead):
                if level is not None and not math.isfinite(order.queue_ahead):
                    order.queue_ahead = level
                continue
            if ts_ms == order.live_ms and previous == level:
                continue
            delta = level - previous
            if delta >= 0:
                order.trade_applied = 0.0
                continue
            explained = min(-delta, order.trade_applied)
            order.trade_applied -= explained
            cancelled = -delta - explained
            if cancelled <= 0:
                continue
            probability = queue_decrement_probability(order.queue_ahead, order.qty, previous, self.queue_fn)
            order.queue_ahead = max(order.queue_ahead + probability * (-cancelled), 0.0)

    def _rigtorp_trade(self, ts_ms: int, price: float, size: float) -> None:
        if size <= 0:
            return
        for order in (self.bid, self.ask):
            if order is None or ts_ms < order.live_ms:
                continue
            if order.side == "buy" and self.inventory >= MAX_INVENTORY:
                continue
            if order.side == "sell" and self.inventory <= -MAX_INVENTORY:
                continue
            if not _hits_level(order.side, order.price, price):
                continue
            if not math.isfinite(order.queue_ahead):
                continue
            remaining = order.qty - order.filled
            if remaining <= 1e-12:
                continue
            order.trade_applied += size
            ahead = order.queue_ahead
            consumed = min(ahead, size)
            order.queue_ahead = max(ahead - consumed, 0.0)
            remainder = size - consumed
            fill = min(remaining, max(remainder, 0.0))
            if order.queue_ahead <= 1e-12 and fill > 1e-12:
                order.filled += fill
                self._apply(order, fill, ts_ms)

    def _cancel_if_worthless(self) -> None:
        if self.last_mid is None:
            return
        cost = self._adverse_cost()
        for side in ("buy", "sell"):
            order = self.bid if side == "buy" else self.ask
            if order is None or order.filled >= order.qty - 1e-12:
                continue
            value = intrinsic_value(
                order.side,
                order.price,
                self.last_mid,
                order.queue_ahead,
                order.qty,
                self.queue_fn,
                cost,
            )
            order.intrinsic = value
            if value < 0:
                self.n_cancels += 1
                if side == "buy":
                    self.bid = None
                    self._cancelled_bid = True
                else:
                    self.ask = None
                    self._cancelled_ask = True

    def _settle_adverse(self, ts_ms: int) -> None:
        while self._adverse_cursor < len(self.fills):
            fill = self.fills[self._adverse_cursor]
            if fill.ts_ms + ADVERSE_HORIZON_MS > ts_ms:
                return
            value = markout(fill, self.mids, ADVERSE_HORIZON_MS)
            if value is None:
                return
            self._adverse_cursor += 1
            self.adverse.push(value)

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


def _initial_queue(mode: str, q_send: float | None, q_ack: float | None) -> float:
    """V(t0). A hidden level stays unknown. Improving a price starts at zero."""
    if mode == "send":
        chosen = q_send
    elif mode == "ack":
        chosen = q_ack
    elif mode == "average":
        if q_send is None or q_ack is None:
            chosen = None
        else:
            chosen = (q_send + q_ack) / 2.0
    else:
        raise ValueError("initial queue estimate")
    if chosen is None:
        return math.inf
    return max(chosen, 0.0)


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
