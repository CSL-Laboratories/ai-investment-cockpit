"""QT-02 — the strategy contract: market state in, intents out.

THE SHAPE, AND WHY IT IS THIS SHAPE
------------------------------------
A strategy is a set of callbacks that RETURN INTENTS. It does not place orders, does not
touch the book, does not read a clock, and does not know whether it is running against a
replay, a simulated venue, or a post-hoc analysis pass.

    def on_book_update(self, ctx: StrategyContext) -> list[Intent]

That signature is the whole design. Because the return value is data rather than an effect:

  * a strategy unit test is ``assert strategy.on_book_update(ctx) == [expected_intent]``,
    with no venue, no matcher and no fixture beyond a snapshot;
  * the same strategy object runs identically under replay and under live paper — the
    runner changes, the strategy does not;
  * ``guards.py`` can inspect and veto intents BEFORE anything acts on them, which is what
    makes a pre-trade limit a limit rather than a suggestion.

The alternative — handing strategies a venue handle and letting them act — makes every one
of those impossible and puts the venue reference inside user-written code, which is exactly
where the containment argument in ``quant/__init__.py`` must not have a hole.

FILLS COME FROM THE MATCHER. THERE IS NO FILL PROBABILITY HERE.
----------------------------------------------------------------
``on_fill`` is called with a real ``Fill`` produced by ``book/matching.py`` walking a real
queue. No strategy in this package draws a random number to decide whether it traded. That
is the single substantive difference between this engine and the usual toy simulator, and
it is enforced by there being no such facility to reach for.

STRATEGIES ARE STATEFUL, AND THAT IS FINE
-------------------------------------------
Estimators warm up, inventory accumulates, quotes are remembered. What a strategy must NOT
do is mutate anything it receives: ``StrategyContext`` and every snapshot inside it are
frozen, so an attempt to "just tweak" the book state fails loudly instead of corrupting the
one object every other strategy in the run is also reading.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from quant.book.lob import BookSnapshot
from quant.events import Fill, PriceScale, Trade
from quant.oms import Intent, ManagedOrder, OrderIdSequence


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """An immutable read of the world at one instant, handed to every callback.

    Multi-symbol from the start (``books`` is a mapping, not a single book) because the pairs
    strategy needs two and retrofitting a second symbol into a single-book context means
    changing every signature. Single-symbol strategies use the ``book`` convenience property
    and never notice.
    """

    ts: int
    books: Mapping[str, BookSnapshot]
    positions: Mapping[str, int]
    live_orders: Sequence[ManagedOrder]
    scale: PriceScale
    primary_symbol: str | None = None

    @property
    def book(self) -> BookSnapshot | None:
        """The single book, for single-symbol strategies.

        Falls back to the only book present when no primary is named, so a one-symbol run
        does not have to configure something it does not have. Returns None rather than
        guessing when several are present and none was designated — a strategy silently
        trading whichever symbol happened to sort first is worse than one that fails.
        """
        if self.primary_symbol is not None:
            return self.books.get(self.primary_symbol)
        if len(self.books) == 1:
            return next(iter(self.books.values()))
        return None

    def position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def orders_for(self, symbol: str) -> list[ManagedOrder]:
        return [o for o in self.live_orders if o.symbol == symbol]


@dataclass(slots=True)
class Strategy(ABC):
    """Base class. Override the callbacks you care about; the rest return no intents.

    Every callback DEFAULTS TO DOING NOTHING rather than being abstract. A momentum strategy
    has no opinion on timers and a market maker may have no opinion on the tape; forcing
    empty overrides adds noise and, worse, invites a reflexive ``pass`` that silently
    swallows an event the author did mean to handle.

    ``name`` doubles as the OMS ``owner`` and as the book's order ``owner``, which is what
    makes self-trade prevention per-strategy and PnL attributable. It must be unique per
    running instance.
    """

    name: str
    symbols: tuple[str, ...] = ()
    _ids: OrderIdSequence = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a strategy needs a name: it is the OMS owner and the PnL key")
        self._ids = OrderIdSequence(prefix=self.name)

    def next_client_id(self) -> str:
        """Deterministic ``<name>-<n>``. Two runs of one replay produce identical ids, which
        is what makes two backtests diffable — the main way a change's effect is measured."""
        return self._ids.next()

    # ---- callbacks -----------------------------------------------------------------------

    def on_book_update(self, ctx: StrategyContext) -> list[Intent]:
        """The book changed. The main callback for anything quoting."""
        return []

    def on_trade(self, trade: Trade, ctx: StrategyContext) -> list[Intent]:
        """A print crossed the tape. Note this is MARKET flow, not necessarily ours."""
        return []

    def on_fill(self, fill: Fill, ctx: StrategyContext) -> list[Intent]:
        """WE traded. Produced by the matcher walking a real queue, never by a random draw."""
        return []

    def on_timer(self, ts: int, ctx: StrategyContext) -> list[Intent]:
        """A wall of event time passed with or without activity.

        Separate from ``on_book_update`` because a market maker must be able to act when
        NOTHING is happening — stale quotes in a quiet book are how inventory accumulates
        against you. A strategy driven only by book updates cannot react to the absence of
        book updates, which is itself a market state.
        """
        return []

    @abstractmethod
    def describe(self) -> dict:
        """Parameters and current internal state, for the tearsheet and the WS broadcast.

        Abstract because a strategy whose parameters are not inspectable cannot be swept,
        compared or debugged, and every strategy eventually needs to be all three.
        """
