"""QT-01 — FIFO price-time matching. The policy half of the book.

WHAT "FIFO" ACTUALLY COMMITS US TO
-----------------------------------
An aggressive order walks the opposite side from the touch outward, and AT EACH PRICE LEVEL
consumes resting orders strictly in arrival order. Two consequences do all the work:

  * A partial fill leaves the resting order AT THE FRONT of its level with reduced size —
    it does not go to the back. ``LimitOrderBook.reduce`` is the operation, and the reason
    it exists separately from cancel/re-add is that getting this wrong makes every passive
    strategy look worse than it is, in a way that no aggregate statistic reveals.
  * Price improvement is not automatic. A buy at 101 hitting an ask of 100 trades AT 100 —
    the RESTING order's price, always. The passive side sets the price; that is what being
    passive buys you, and it is where the maker's edge comes from.

Most US equity venues are price-time FIFO, so this is the right default. Pro-rata and
size-priority venues exist; they belong as alternative functions beside this one rather than
as flags inside it, because their queue economics are genuinely different and a strategy
tuned for one is not merely suboptimal on the other.

WHY MATCHING IS NOT A METHOD ON THE BOOK
-----------------------------------------
The book is state; this is policy. Keeping them apart means the hand-built book tests assert
on queue positions with no matcher present, and the matcher tests drive a book built by
hand. When they are fused, every book test transitively depends on matching rules and a
matching bug reads as a book bug.

SELF-TRADE PREVENTION IS ON BY DEFAULT
---------------------------------------
A market maker quoting both sides WILL eventually cross itself, and a simulator that happily
matches your bid against your own ask manufactures round-trip volume at zero cost — free
"spread capture" against yourself, which shows up as clean profit and is entirely fictional.
Default is ``CANCEL_RESTING`` (the incoming order proceeds, the resting one is pulled), which
is the common venue default. It is a default rather than mandatory because reproducing a
specific venue's STP behaviour is sometimes the point.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from quant.book.lob import LimitOrderBook, RestingOrder
from quant.events import Fill, Liquidity, Side


class TimeInForce(enum.Enum):
    """How long an unfilled remainder survives.

    ``DAY`` and ``GTC`` behave identically HERE — both rest — because the difference is
    session expiry, which is a scheduling concern the runner owns, not a matching one. They
    are kept as distinct values so an order carries its real intent through to the OMS
    instead of being flattened at the matcher and reconstructed later.
    """

    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"

    @property
    def rests(self) -> bool:
        return self in (TimeInForce.DAY, TimeInForce.GTC)


class SelfTradePrevention(enum.Enum):
    CANCEL_RESTING = "cancel_resting"
    CANCEL_AGGRESSING = "cancel_aggressing"
    ALLOW = "allow"


class MatchStatus(enum.Enum):
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    RESTING = "resting"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True, kw_only=True)
class AggressiveOrder:
    """An order arriving at the venue. May cross, may rest, may do both.

    ``price_ticks=None`` means MARKET. Market orders never rest: an unfilled remainder is
    cancelled regardless of TIF, because "rest at no price" is meaningless. That is enforced
    in ``match`` rather than validated here, since it is a matching rule and not a property
    of the order.

    ``owner=None`` marks market flow — an order belonging to the rest of the world, replayed
    from a feed. Only orders with an owner generate ``Fill`` events, because a ``Fill`` in
    this system means "OUR execution" and emitting them for the whole market would flood PnL
    with other people's trades.
    """

    order_id: str
    side: Side
    qty: int
    price_ticks: int | None = None
    tif: TimeInForce = TimeInForce.DAY
    owner: str | None = None
    stp: SelfTradePrevention = SelfTradePrevention.CANCEL_RESTING

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"aggressive order {self.order_id!r} must have positive qty, got {self.qty}")
        if self.price_ticks is None and self.tif is TimeInForce.FOK:
            # A market FOK is representable but its semantics differ per venue (all-or-none
            # at any price, vs at the touch). Refusing is better than picking one silently.
            raise ValueError("market FOK is ambiguous; specify a limit price")

    @property
    def is_market(self) -> bool:
        return self.price_ticks is None


@dataclass(slots=True)
class MatchResult:
    """Everything that happened as a result of one aggressive order.

    ``fills`` holds OUR fills only (maker side, taker side, or both). ``trades`` holds every
    execution including anonymous market-vs-market ones, because the tape is what a momentum
    strategy consumes and what volume statistics are built from — discarding trades we were
    not party to would leave the simulated tape sparser than the real one and quietly break
    any participation-rate calculation.
    """

    status: MatchStatus
    filled_qty: int = 0
    remaining_qty: int = 0
    fills: list[Fill] = field(default_factory=list)
    trades: list[tuple[int, int]] = field(default_factory=list)
    resting_order_id: str | None = None
    stp_cancelled: list[str] = field(default_factory=list)

    @property
    def notional_ticks(self) -> int:
        return sum(price * qty for price, qty in self.trades)

    @property
    def avg_price_ticks(self) -> float | None:
        if not self.filled_qty:
            return None
        return self.notional_ticks / self.filled_qty


def _crosses(aggressive_side: Side, limit_ticks: int | None, level_ticks: int) -> bool:
    """Would an order at ``limit_ticks`` trade against a resting level at ``level_ticks``?

    A market order (``limit_ticks is None``) crosses everything. This is the only place the
    inequality direction is written down; every walk uses it so the buy and sell cases cannot
    drift apart.
    """
    if limit_ticks is None:
        return True
    if aggressive_side is Side.BUY:
        return level_ticks <= limit_ticks
    return level_ticks >= limit_ticks


def _available_qty(book: LimitOrderBook, order: AggressiveOrder) -> int:
    """How much of ``order`` could trade right now. Used only for the FOK all-or-none test.

    Counts self-trade-prevented orders as UNAVAILABLE when STP would pull them, so a FOK that
    would only "fill" by trading against itself is correctly rejected rather than reported as
    a fill.
    """
    opposite = order.side.opposite
    total = 0
    for price in book.prices(opposite):
        if not _crosses(order.side, order.price_ticks, price):
            break
        level = book.level(opposite, price)
        if level is None:
            continue
        for resting in level:
            if _is_self_trade(order, resting) and order.stp is not SelfTradePrevention.ALLOW:
                continue
            total += resting.qty
            if total >= order.qty:
                return total
    return total


def _is_self_trade(order: AggressiveOrder, resting: RestingOrder) -> bool:
    return order.owner is not None and resting.owner == order.owner


def match(
    book: LimitOrderBook,
    order: AggressiveOrder,
    *,
    seq: int = 0,
    exchange_ts: int = 0,
    recv_ts: int | None = None,
    ingest_ts: int | None = None,
) -> MatchResult:
    """Apply ``order`` to ``book``: cross what crosses, rest what remains, emit our fills.

    MUTATES ``book``. That is the point — this is the venue's matching engine, and a
    non-mutating version would have to return a whole new book per message, which at ITCH
    message rates is not a tradeoff worth discussing.

    The three timestamps are threaded through to every ``Fill`` rather than collapsed,
    because a sim venue with a latency model stamps them differently (see
    ``execution/sim_venue.py``) and a fill that cannot say when it was known is useless for
    the latency analysis this engine exists to support. They default to ``exchange_ts`` so
    a zero-latency test does not have to say so three times.
    """
    recv_ts = exchange_ts if recv_ts is None else recv_ts
    ingest_ts = recv_ts if ingest_ts is None else ingest_ts

    # Rust books keep the walk on the native side. A Python book (no extension,
    # or the explicit reference class) falls through to the implementation below.
    if getattr(book, "_core", None) is not None:
        from quant.book._rust_backend import match_core

        return match_core(
            book, order, seq=seq, exchange_ts=exchange_ts, recv_ts=recv_ts, ingest_ts=ingest_ts,
        )

    result = MatchResult(status=MatchStatus.CANCELLED, remaining_qty=order.qty)

    # FOK is all-or-none and must be decided BEFORE any state changes: a partially executed
    # fill-or-kill is a contradiction, and rolling one back after the fact means unwinding
    # queue positions that other orders have already moved through.
    if order.tif is TimeInForce.FOK and _available_qty(book, order) < order.qty:
        result.status = MatchStatus.REJECTED
        return result

    remaining = order.qty
    opposite = order.side.opposite

    # Snapshot the price ladder before walking. The walk deletes levels as it empties them,
    # and mutating the structure being iterated is the classic way a matcher skips a level.
    for price in book.prices(opposite):
        if remaining <= 0 or not _crosses(order.side, order.price_ticks, price):
            break
        level = book.level(opposite, price)
        if level is None:
            continue

        # Same reasoning one level down: consuming an order removes it from the dict.
        for resting_id in list(level.orders):
            if remaining <= 0:
                break
            resting = level.orders.get(resting_id)
            if resting is None:
                continue

            if _is_self_trade(order, resting) and order.stp is not SelfTradePrevention.ALLOW:
                if order.stp is SelfTradePrevention.CANCEL_RESTING:
                    book.cancel(resting_id)
                    result.stp_cancelled.append(resting_id)
                    continue
                # CANCEL_AGGRESSING: the incoming order dies here, keeping what it already
                # traded. Returning early rather than breaking twice keeps the two STP
                # policies from sharing a fallthrough nobody can read.
                result.remaining_qty = remaining
                result.status = (
                    MatchStatus.PARTIALLY_FILLED if result.filled_qty else MatchStatus.CANCELLED
                )
                return result

            traded = min(remaining, resting.qty)
            # THE PRICE IS THE RESTING ORDER'S. The aggressor's limit only decided *whether*
            # to trade, never at what price.
            trade_price = resting.price_ticks
            queue_at_entry = resting.qty_ahead_at_entry

            book.reduce(resting_id, traded)
            remaining -= traded
            result.filled_qty += traded
            result.trades.append((trade_price, traded))

            if resting.owner is not None:
                result.fills.append(
                    Fill(
                        symbol=book.symbol, seq=seq, exchange_ts=exchange_ts,
                        recv_ts=recv_ts, ingest_ts=ingest_ts,
                        order_id=resting_id, side=resting.side, price_ticks=trade_price,
                        qty=traded, liquidity=Liquidity.MAKER,
                        queue_position_at_entry=queue_at_entry,
                    )
                )
            if order.owner is not None:
                result.fills.append(
                    Fill(
                        symbol=book.symbol, seq=seq, exchange_ts=exchange_ts,
                        recv_ts=recv_ts, ingest_ts=ingest_ts,
                        order_id=order.order_id, side=order.side, price_ticks=trade_price,
                        qty=traded, liquidity=Liquidity.TAKER,
                        queue_position_at_entry=0,
                    )
                )

    result.remaining_qty = remaining

    if remaining <= 0:
        result.status = MatchStatus.FILLED
        return result

    # A market order never rests: "rest at no price" has no meaning, so the remainder is
    # cancelled whatever the TIF said.
    if order.tif.rests and not order.is_market:
        book.add(
            order_id=order.order_id, side=order.side, price_ticks=order.price_ticks,
            qty=remaining, seq=seq, ts=exchange_ts, owner=order.owner,
        )
        result.resting_order_id = order.order_id
        result.status = MatchStatus.RESTING
        return result

    result.status = MatchStatus.PARTIALLY_FILLED if result.filled_qty else MatchStatus.CANCELLED
    return result
