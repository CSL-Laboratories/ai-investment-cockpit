"""QT-01 — normalized market/execution event schemas. The engine's only vocabulary.

THREE TIMESTAMPS, NOT ONE — THE DECISION THAT IS EXPENSIVE TO REVERSE
---------------------------------------------------------------------
Every event here carries ``exchange_ts``, ``recv_ts`` AND ``ingest_ts``. Not a "timestamp".

  ``exchange_ts``  the venue's own stamp — when the event happened at the matching engine.
  ``recv_ts``      when the bytes reached US — the wire arrival stamp from the capture.
  ``ingest_ts``    when OUR engine processed it — after decode, normalization, dispatch.

Collapsing these into one field is the single most common way a microstructure backtest
lies, and it lies in two directions at once:

  * Using ``exchange_ts`` to decide what a strategy could SEE is look-ahead. The strategy
    reacts to a book state that had not yet reached it. Every latency-sensitive edge is
    manufactured this way, and the result looks like alpha rather than like a bug.
  * Using ``recv_ts`` to measure the MARKET is wrong in the other direction: sequencing two
    venues, or the same venue across a feed hiccup, by arrival order rewrites causality.

You need both readings simultaneously, so you must store both. ``ingest_ts`` is the third
because it is the only way to tell a slow FEED from a slow DECODER — the distinction the
whole ITCH exercise exists to measure. ``feed_latency_ns`` and ``decode_latency_ns`` below
are the two subtractions, named, so nobody re-derives them inconsistently.

Retrofitting this is miserable: once events exist with one timestamp, every call site has
guessed which one it meant, and the guesses are unrecoverable. Hence day one.

WHY INT NANOSECONDS AND NOT ``datetime``
-----------------------------------------
This repo has a hard, hard-won wall-clock contract (``ingest/clock_contract.py``): a clock
seam is a zero-arg callable returning a TIMEZONE-AWARE ``datetime``, and returning a bare
number raises. That contract is correct and it is NOT what event time is.

Event time is a monotonically-ordered integer read off a capture file, compared millions of
times per replay, and required to be exact. ``datetime`` gives microsecond resolution (ITCH
is nanosecond), allocates, and compares slowly. So:

    WALL CLOCK  -> ``core.clock.Clock``, tz-aware ``datetime``. Scheduling, audit, "today".
    EVENT TIME  -> ``int`` nanoseconds since the Unix epoch, UTC. Ordering, latency, replay.

These two never mix, and ``to_ns`` / ``from_ns`` are the ONLY crossing points. ``to_ns``
refuses a naive datetime for exactly the reason ``resolve_wall_clock`` does: a naive reading
is a reading of an unknown zone, and silently treating it as UTC mis-stamps by the local
offset — which, on an event stream sorted by that stamp, reorders history.

WHY INTEGER PRICE TICKS
------------------------
``PriceScale`` converts ``Decimal`` <-> ``int`` ticks at the boundary and the book uses ints
throughout. Price levels are DICT KEYS and sort keys; a float key makes ``100.10`` and
``100.09999999999999`` two different levels, which silently splits a queue in half and
destroys exactly the queue-position accounting this engine exists to model. Integers make
the bug unrepresentable. ``Decimal`` is the cockpit's money type (``risk.engine``), so it is
what we convert to and from — never float, in either direction.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

NS_PER_SECOND = 1_000_000_000

# The epoch every ``exchange_ts``/``recv_ts``/``ingest_ts`` in this package is measured from.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class EventTimeError(ValueError):
    """An event-time value violated the integer-nanosecond contract.

    Separate from ``ClockContractError`` on purpose, and the distinction is the point: that
    error means "a WALL-CLOCK seam returned the wrong thing", this one means "an EVENT-TIME
    value is malformed". Merging them would blur precisely the boundary this module exists
    to keep sharp — and a reader who sees one error type for two contracts will assume the
    contracts are one, which is the drift ``ingest/clock_contract.py`` documents at length.
    """


def to_ns(when: datetime) -> int:
    """Wall-clock ``datetime`` -> event-time integer nanoseconds. One of two crossing points.

    Refuses a naive datetime. A naive reading names an unknown zone; reading it as UTC
    mis-stamps by the local offset, and since this value is the SORT KEY of an event stream,
    a mis-stamp does not merely shift a number — it reorders history, which no downstream
    check can detect or undo.
    """
    if not isinstance(when, datetime):
        raise EventTimeError(
            f"to_ns expects a datetime, got {type(when).__name__} ({when!r}). Event time is "
            "int nanoseconds; wall clock is a tz-aware datetime. See quant/events.py."
        )
    if when.tzinfo is None or when.utcoffset() is None:
        raise EventTimeError(
            f"to_ns refuses a NAIVE datetime ({when!r}): it is a reading of an unknown zone. "
            "Stamping an event stream with it reorders the stream by the local offset. Pass "
            "datetime.now(timezone.utc) or attach real tzinfo — same rule as "
            "ingest/clock_contract.resolve_wall_clock."
        )
    delta = when - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * NS_PER_SECOND + delta.microseconds * 1_000


def from_ns(ns: int) -> datetime:
    """Event-time integer nanoseconds -> tz-aware UTC ``datetime``. The other crossing point.

    LOSSY BY CONSTRUCTION: ``datetime`` resolves to microseconds, ITCH stamps nanoseconds, so
    this truncates. That is acceptable at the reporting boundary (a tearsheet does not care
    about the sub-microsecond digit) and unacceptable anywhere in the engine — which is why
    the engine never calls it. Round-tripping ``from_ns(to_ns(x))`` is exact; round-tripping
    ``to_ns(from_ns(x))`` is NOT, and the tests pin that asymmetry so it stays known.
    """
    if isinstance(ns, bool) or not isinstance(ns, int):
        raise EventTimeError(
            f"from_ns expects int nanoseconds, got {type(ns).__name__} ({ns!r})."
        )
    return _EPOCH + _timedelta_ns(ns)


def _timedelta_ns(ns: int):
    from datetime import timedelta

    return timedelta(microseconds=ns // 1_000)


class Side(enum.Enum):
    """Order/quote side. ``BUY`` rests on the bid, ``SELL`` on the ask.

    ``sign`` is +1/-1 so inventory and PnL arithmetic never re-derives it with an inline
    conditional — the place signs get flipped by accident.
    """

    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class Liquidity(enum.Enum):
    """Whether a fill ADDED liquidity (rested, earns the maker rebate) or REMOVED it.

    Kept on the fill rather than inferred later because it is not always recoverable after
    the fact, and because it is the sign of the fee — getting it wrong flips a strategy's
    economics rather than merely perturbing them (see ``quant/costs.py``).
    """

    MAKER = "maker"
    TAKER = "taker"


@dataclass(frozen=True, slots=True)
class PriceScale:
    """The ``Decimal`` <-> integer-tick boundary. Constructed once per instrument.

    Every conversion in the system goes through one of these two methods, so the tick size
    is stated in exactly one place per instrument instead of being a magic ``100`` sprinkled
    across the book, the strategies, and the PnL code.
    """

    tick_size: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.tick_size, Decimal):
            raise TypeError(
                f"tick_size must be Decimal, got {type(self.tick_size).__name__}. Float tick "
                "sizes reintroduce exactly the representation error integer ticks remove."
            )
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {self.tick_size}")

    def to_ticks(self, price: Decimal) -> int:
        """``Decimal`` price -> integer ticks. Refuses a price off the tick grid.

        An off-grid price is REJECTED rather than rounded. Rounding here would be a silent
        price change on the order path's cousin: the caller asked to buy at 100.005 on a
        penny grid and would get 100.00 or 100.01 without being told which.
        """
        if not isinstance(price, Decimal):
            raise TypeError(
                f"price must be Decimal, got {type(price).__name__} ({price!r}). Money is "
                "never a float in this repo."
            )
        quotient, remainder = divmod(price, self.tick_size)
        if remainder != 0:
            raise ValueError(
                f"price {price} is not a multiple of tick_size {self.tick_size} "
                f"(remainder {remainder}). Refusing to round: silently moving a price is a "
                "worse failure than refusing an off-grid one."
            )
        return int(quotient)

    def to_decimal(self, ticks: int) -> Decimal:
        """Integer ticks -> ``Decimal`` price. Exact, always."""
        if isinstance(ticks, bool) or not isinstance(ticks, int):
            raise TypeError(f"ticks must be int, got {type(ticks).__name__} ({ticks!r})")
        return Decimal(ticks) * self.tick_size


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Fields every event in the system carries.

    ``kw_only`` throughout this module is deliberate: subclasses add fields, and positional
    dataclass inheritance would make the argument order depend on the class hierarchy — so a
    refactor that inserts a base field silently reassigns every positional call site. With
    keyword-only construction that entire failure mode does not exist, and call sites read as
    documentation. The per-event cost is irrelevant next to being wrong.

    ``seq`` is the venue's sequence number. It is the ONLY gap-detection mechanism: a feed
    that drops a message still delivers well-formed events with plausible timestamps, so
    absence is invisible in the time domain and obvious in the sequence domain.
    """

    symbol: str
    seq: int
    exchange_ts: int
    recv_ts: int
    ingest_ts: int

    def __post_init__(self) -> None:
        for name in ("exchange_ts", "recv_ts", "ingest_ts", "seq"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise EventTimeError(
                    f"{type(self).__name__}.{name} must be int, got "
                    f"{type(value).__name__} ({value!r}). Event time is int nanoseconds and "
                    "seq is an integer counter — a float here is a wall-clock value that "
                    "wandered across the boundary."
                )
            if value < 0:
                raise EventTimeError(f"{type(self).__name__}.{name} must be >= 0, got {value}")

    @property
    def feed_latency_ns(self) -> int:
        """Wire time: venue stamp -> our capture. NEGATIVE MEANS CLOCK SKEW, and is allowed.

        Not clamped and not raised on. A negative value is real information — it says the
        capture host's clock disagrees with the venue's — and clamping it to zero would hide
        the one measurement that reveals the disagreement. Analysis surfaces it; the schema
        does not silently repair it.
        """
        return self.recv_ts - self.exchange_ts

    @property
    def decode_latency_ns(self) -> int:
        """Our own cost: capture -> engine dispatch. This is the number we control.

        Separating it from ``feed_latency_ns`` is the entire reason there are three stamps:
        a single "latency" number cannot distinguish a slow venue from a slow decoder, and
        those two findings imply opposite work.
        """
        return self.ingest_ts - self.recv_ts

    @property
    def total_latency_ns(self) -> int:
        return self.ingest_ts - self.exchange_ts


class BookAction(enum.Enum):
    """The four things that can happen to a resting order on the venue's book.

    Mirrors ITCH's message taxonomy rather than a simplified add/remove pair, because
    ``EXECUTE`` and ``CANCEL`` have OPPOSITE meanings for the orders queued behind them: a
    cancel ahead of you improves your queue position for free, an execute ahead of you
    improves it while also telling you the price is trading. Collapsing them discards the
    signal that makes queue modelling worth doing.
    """

    ADD = "add"
    CANCEL = "cancel"
    EXECUTE = "execute"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True, kw_only=True)
class BookUpdate(Event):
    """A single change to the venue's book — the normalized ITCH message.

    ``order_ref`` is the venue's order id where the feed provides one (ITCH does). With it,
    a replay reconstructs true per-order queue positions; without it, only aggregate level
    sizes, and queue position degrades to an estimate. ``feed/replay.py`` records which
    regime a dataset is in so a tearsheet cannot quietly claim the stronger one.
    """

    action: BookAction
    side: Side
    price_ticks: int
    qty: int
    order_ref: int | None = None

    def __post_init__(self) -> None:
        # Explicit base call, NOT zero-arg ``super()``: ``@dataclass(slots=True)`` builds a
        # replacement class object, so the ``__class__`` cell these methods close over still
        # points at the pre-decoration class and zero-arg ``super()`` raises TypeError at
        # runtime. Naming the base is the standard workaround and the only one that survives
        # someone adding a third subclass.
        Event.__post_init__(self)
        if isinstance(self.qty, bool) or not isinstance(self.qty, int):
            raise TypeError(f"qty must be int, got {type(self.qty).__name__} ({self.qty!r})")
        if self.qty < 0:
            raise ValueError(f"qty must be >= 0, got {self.qty}")
        if isinstance(self.price_ticks, bool) or not isinstance(self.price_ticks, int):
            raise TypeError(
                f"price_ticks must be int, got {type(self.price_ticks).__name__} "
                f"({self.price_ticks!r}) — prices are integer ticks inside the engine."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class Trade(Event):
    """A print: someone crossed the spread. ``aggressor`` is the side that REMOVED liquidity.

    Naming it ``aggressor`` rather than ``side`` is not pedantry — "the side of a trade" is
    ambiguous (every trade has both) and the ambiguity is where sign errors in order-flow
    imbalance come from. ``quant/strategies/momentum.py`` depends on this being unambiguous.
    """

    price_ticks: int
    qty: int
    aggressor: Side


@dataclass(frozen=True, slots=True, kw_only=True)
class Quote(Event):
    """Top of book. A derived convenience — the book is the source of truth, not this.

    Exists so strategies that only need BBO do not walk levels, and so a feed that provides
    only L1 has a home. Anything reasoning about QUEUE must use the book: a quote cannot
    express "how much is ahead of me at this price", which is the whole question.
    """

    bid_ticks: int | None
    bid_qty: int
    ask_ticks: int | None
    ask_qty: int

    @property
    def spread_ticks(self) -> int | None:
        if self.bid_ticks is None or self.ask_ticks is None:
            return None
        return self.ask_ticks - self.bid_ticks

    @property
    def mid_ticks(self) -> float | None:
        """Arithmetic mid. Float ON PURPOSE — a half-tick mid is not a tradeable price.

        Returning float here is a deliberate type-level signal that this is a STATISTIC, not
        a price you can send. Anything routed to the book takes ``int`` ticks and will reject
        this, which is the intended friction.
        """
        if self.bid_ticks is None or self.ask_ticks is None:
            return None
        return (self.bid_ticks + self.ask_ticks) / 2.0

    @property
    def microprice(self) -> float | None:
        """Size-weighted mid: the imbalance-adjusted fair value.

        Weights each side by the OPPOSITE side's size — heavy bid, thin ask means the next
        print is likelier at the ask, so fair value sits nearer the ask. Getting the weights
        backwards produces a signal that is exactly wrong rather than merely noisy, which is
        why it is written once here instead of in each strategy.
        """
        if self.bid_ticks is None or self.ask_ticks is None:
            return None
        total = self.bid_qty + self.ask_qty
        if total <= 0:
            return self.mid_ticks
        return (self.bid_ticks * self.ask_qty + self.ask_ticks * self.bid_qty) / total


@dataclass(frozen=True, slots=True, kw_only=True)
class Fill(Event):
    """OUR execution. Emitted by the matcher, consumed by OMS, PnL and strategies.

    ``queue_position_at_entry`` is carried on the fill because it is the diagnostic that
    tells you WHY the strategy performed as it did — the difference between "we were at the
    front and got picked off" and "we were 4,000 shares back and only filled on the sweep"
    is invisible in the PnL number and decisive for the parameters. It is recorded at entry,
    not at fill, since by fill time it is zero by definition.
    """

    order_id: str
    side: Side
    price_ticks: int
    qty: int
    liquidity: Liquidity
    queue_position_at_entry: int | None = None

    def __post_init__(self) -> None:
        Event.__post_init__(self)  # see BookUpdate.__post_init__ for why not super()
        if self.qty <= 0:
            raise ValueError(f"a Fill must have positive qty, got {self.qty}")

    @property
    def signed_qty(self) -> int:
        """Position delta. +qty on a buy, -qty on a sell."""
        return self.side.sign * self.qty


class AckStatus(enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    REPLACED = "replaced"


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderAck(Event):
    """The venue's response to one of our order actions.

    A separate event from ``Fill`` because acceptance and execution are separate instants
    with separate latencies, and a strategy that treats "accepted" as "resting at the front"
    has assumed away the queue.
    """

    order_id: str
    status: AckStatus
    reason: str | None = None
