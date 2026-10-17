"""QT-03 — synthetic market data. Develop the whole engine with no capture file.

WHAT IT IS AND WHAT IT IS EXPLICITLY NOT
------------------------------------------
A generative model of order flow: a latent fair value doing a random walk, limit orders
arriving around it with intensity decaying in distance from the touch, cancellations of
resting orders, and aggressive orders that cross. It produces a book that behaves plausibly
— a spread that widens under volatility, depth that rebuilds after a sweep, queues that turn
over — which is enough to exercise every code path in the engine.

It is NOT a market. It has no adverse selection you did not put there, no informed traders,
no news, and no correlation between order flow and subsequent price beyond what the
generator was told to create. A strategy that is profitable here has demonstrated that it
RUNS, not that it works. That distinction is the reason this docstring is emphatic: the
main hazard of a good synthetic feed is that its output looks exactly like a real backtest.

The corpus is already blunt about this at the portfolio level ("the reliable ROI is time,
awareness and discipline — not alpha"; backtest-derived return claims are called out
specifically). The same scepticism applies here with more force, because a generator that
you wrote cannot surprise you.

DETERMINISTIC FROM A SEED
--------------------------
Everything derives from ``seed``. Two runs produce byte-identical event streams, so a
strategy change is measurable as a diff rather than as a difference of two noisy averages.
Uses ``random.Random`` explicitly rather than the module-level RNG so a caller elsewhere in
the process cannot perturb a run by drawing from the global generator.

THE THREE TIMESTAMPS ARE MODELLED, NOT FAKED EQUAL
----------------------------------------------------
``exchange_ts``, ``recv_ts`` and ``ingest_ts`` are generated with real, distinct, jittered
gaps. Setting them equal would be easier and would make every latency test in the suite
vacuous — the feed would silently guarantee the property the engine is supposed to measure.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterator

from quant.book.lob import LimitOrderBook
from quant.book.matching import AggressiveOrder, TimeInForce, match
from quant.events import BookAction, BookUpdate, Event, Side, Trade


@dataclass(slots=True)
class SyntheticParams:
    """The generator's knobs. Defaults produce a liquid, penny-spread, mid-priced name."""

    initial_ticks: int = 10_000
    tick_volatility: float = 0.35
    """Standard deviation of the latent fair value's step, in ticks per event."""

    depth_levels: int = 5
    arrival_decay: float = 0.55
    """Geometric decay of order arrival intensity per tick away from the touch. This is the
    empirical shape the market maker's ``kappa`` is meant to capture, so a sweep over kappa
    against this feed is at least self-consistent — which is a weaker claim than being right."""

    min_size: int = 100
    max_size: int = 1_000
    cancel_probability: float = 0.35
    target_resting_orders: int = 40
    """Depth is MEAN-REVERTING, not free-running. The cancel intensity is scaled by
    ``resting / target``, so the book stabilises around this many orders.

    Without it the generator adds faster than it cancels and depth grows without bound. A
    book with hundreds of stale resting orders has a touch that never moves: the realized
    mid volatility collapses toward zero even though the latent fair value is walking
    normally, and every volatility-dependent strategy quietly stops responding. Real books
    are roughly stationary in depth, and a generator that is not produces a market where
    nothing the engine measures behaves like the thing it is modelling.
    """

    trade_probability: float = 0.08
    """Aggressive crossing orders per event. Low, because real books are overwhelmingly
    adds and cancels — well over 90% of ITCH messages on a liquid name never trade. A feed
    with a realistic add/cancel ratio is what makes queue position matter at all."""

    exchange_gap_ns: int = 250_000
    feed_latency_ns: int = 180_000
    feed_jitter_ns: int = 40_000
    decode_latency_ns: int = 12_000
    decode_jitter_ns: int = 4_000


@dataclass(slots=True)
class SyntheticFeed:
    """Deterministic generative order flow for one or more symbols.

    Iterating yields events in ``recv_ts`` order, satisfying the ``Feed`` protocol. The
    generator tracks its own shadow of resting order ids so its cancels refer to orders it
    actually added — a feed that cancels ids it never created would exercise the book's
    error path rather than its normal one, and would make every replay look broken.
    """

    symbols: tuple[str, ...] = ("SYNTH",)
    n_events: int = 5_000
    seed: int = 7
    params: SyntheticParams = field(default_factory=SyntheticParams)

    def __iter__(self) -> Iterator[Event]:
        cursor = _rust_cursor(self)
        if cursor is not None:
            return _events_from_cursor(cursor)
        return self._python_events()

    def _python_events(self) -> Iterator[Event]:
        rng = random.Random(self.seed)
        p = self.params
        state = {
            symbol: {
                "fair": float(p.initial_ticks),
                # A REAL book, not a shadow list. An approximate shadow drifts out of step
                # with what its own events imply, and then emits cancels for orders it has
                # already traded away — which reaches the consumer as a stream of phantom
                # desyncs indistinguishable from genuine dropped messages. The generator has
                # no reason to be approximate about its own state, so it is not.
                "book": LimitOrderBook(symbol),
                "next_ref": 1,
                "seq": 0,
            }
            for symbol in self.symbols
        }
        exchange_ts = 1_700_000_000 * 1_000_000_000
        last_recv = 0
        last_ingest = 0

        for _ in range(self.n_events):
            symbol = rng.choice(self.symbols)
            s = state[symbol]
            exchange_ts += max(1, int(rng.expovariate(1.0 / p.exchange_gap_ns)))

            # Jitter varies the LATENCY, it does not reorder the stream. A capture is a
            # single socket read in order, so arrival stamps are monotonic by construction;
            # letting jitter invert two closely-spaced events would model packet reordering
            # that a single-stream capture cannot exhibit, and would make the feed violate
            # the point-in-time ordering rule it exists to demonstrate.
            recv_ts = max(
                last_recv + 1,
                exchange_ts + p.feed_latency_ns + rng.randint(-p.feed_jitter_ns, p.feed_jitter_ns),
            )
            ingest_ts = max(
                last_ingest + 1,
                recv_ts + p.decode_latency_ns + rng.randint(-p.decode_jitter_ns, p.decode_jitter_ns),
            )
            last_recv, last_ingest = recv_ts, ingest_ts
            s["seq"] += 1
            stamps = {
                "symbol": symbol, "seq": s["seq"], "exchange_ts": exchange_ts,
                "recv_ts": recv_ts, "ingest_ts": ingest_ts,
            }

            s["fair"] += rng.gauss(0.0, p.tick_volatility)

            resting = len(s["book"])
            cancel_intensity = min(
                0.90, p.cancel_probability * resting / max(1, p.target_resting_orders)
            )
            roll = rng.random()
            if roll < p.trade_probability and resting:
                # BUG-076: a zero-fill aggressor is a non-event for the tape. Emit book
                # flow under the same stamps instead, so the per-symbol seq stays
                # contiguous (validate_ordering rejects gaps — BUG-077).
                trade = self._trade(rng, s, stamps)
                yield trade if trade is not None else self._add(rng, s, stamps)
            elif roll < p.trade_probability + cancel_intensity and resting:
                yield self._cancel(rng, s, stamps)
            else:
                yield self._add(rng, s, stamps)

    # ---- event constructors ---------------------------------------------------------

    def _add(self, rng: random.Random, s: dict, stamps: dict) -> BookUpdate:
        p = self.params
        book: LimitOrderBook = s["book"]
        side = Side.BUY if rng.random() < 0.5 else Side.SELL

        # Distance from the touch drawn from a geometric-ish decay: most arrivals join the
        # front levels, a tail rests deep. A uniform distribution over levels would give a
        # flat book, in which queue position barely varies and the whole model is untested.
        distance = 0
        while distance < p.depth_levels - 1 and rng.random() > p.arrival_decay:
            distance += 1

        base = int(round(s["fair"]))
        price = base - 1 - distance if side is Side.BUY else base + 1 + distance

        # Keep the generated book UNCROSSED. Aggression is expressed as a trade event, not as
        # a resting order that happens to overlap: an ADD that crosses would either have to
        # trade (making this function emit executions too) or would leave the book crossed
        # permanently, and a permanently crossed book makes every mid, spread and reservation
        # price downstream meaningless.
        best_ask, best_bid = book.best_ask(), book.best_bid()
        if side is Side.BUY and best_ask is not None:
            price = min(price, best_ask - 1)
        elif side is Side.SELL and best_bid is not None:
            price = max(price, best_bid + 1)

        qty = rng.randrange(p.min_size, p.max_size + 1, 100)
        ref = s["next_ref"]
        s["next_ref"] += 1
        book.add(order_id=str(ref), side=side, price_ticks=price, qty=qty,
                 seq=stamps["seq"], ts=stamps["exchange_ts"])
        return BookUpdate(action=BookAction.ADD, side=side, price_ticks=price,
                          qty=qty, order_ref=ref, **stamps)

    def _cancel(self, rng: random.Random, s: dict, stamps: dict) -> BookUpdate:
        book: LimitOrderBook = s["book"]
        # Insertion order, one order. Copying ``_index`` wrapped every resting
        # id just to discard all but one, which was the generator's hot path.
        order = book.resting_at(rng.randrange(len(book)))
        book.cancel(order.order_id)
        return BookUpdate(action=BookAction.CANCEL, side=order.side,
                          price_ticks=order.price_ticks, qty=order.qty,
                          order_ref=int(order.order_id), **stamps)

    def _trade(self, rng: random.Random, s: dict, stamps: dict) -> Trade | None:
        """An aggressive order crossing. The consumer turns this into a real match.

        Deliberately emitted as a ``Trade`` rather than as a pre-computed set of executions:
        ``runner.apply_market_event`` replays it as an aggressive order through the actual
        matcher, which is what allows OUR resting orders to be filled by it according to real
        queue priority. Pre-computing the executions here would decide our fills inside the
        feed — precisely the fill-probability shortcut this engine exists to remove.
        """
        book: LimitOrderBook = s["book"]
        aggressor = Side.BUY if rng.random() < 0.5 else Side.SELL
        qty = rng.randrange(self.params.min_size, self.params.max_size + 1, 100)
        touch = book.best_ask() if aggressor is Side.BUY else book.best_bid()
        if touch is None:
            base = int(round(s["fair"]))
            touch = base + 1 if aggressor is Side.BUY else base - 1
        result = match(
            book,
            AggressiveOrder(order_id=f"agg{stamps['seq']}", side=aggressor, qty=qty,
                            price_ticks=touch, tif=TimeInForce.IOC),
            seq=stamps["seq"], exchange_ts=stamps["exchange_ts"],
        )
        # Report what actually traded, not what was requested. A trade event claiming more
        # size than the book held would inject phantom aggression on the consumer's side.
        # BUG-076: ``filled_qty or qty`` did the exact opposite of this comment — a zero
        # fill (all resting liquidity on the aggressor's own side, touch INVENTED at
        # fair±1) substituted the full requested size and printed it at a price that was
        # never on the book. No fill -> no print.
        if result.filled_qty <= 0:
            return None
        return Trade(price_ticks=touch, qty=result.filled_qty, aggressor=aggressor, **stamps)

    @staticmethod
    def _any_resting(s: dict) -> bool:
        return len(s["book"]) > 0


def _rust_cursor(feed: SyntheticFeed):
    """One native event at a time, or None when the extension is not installed.

    A seed ``random.Random`` accepts and the i64 binding does not — a float, a
    str, or an int outside i64 — stays on the Python generator. Any other
    failure from a present extension is raised: a broken build must not
    silently change the stream. The cursor keeps the book and
    the RNG in Rust, so the first event does not wait on the rest of the tape.
    """
    try:
        import cockpit_quant
    except ImportError:
        return None
    open_cursor = getattr(cockpit_quant, "synthetic_cursor", None)
    if open_cursor is None:
        return None
    # random.Random accepts a float or a str, and an int outside i64.
    # PyO3's i64 binding rejects those. Anything else, including a count or
    # a price that does not fit, is passed through and raised.
    seed = feed.seed
    if type(seed) is not int or seed < -2**63 or seed > 2**63 - 1:
        return None
    params = feed.params
    return open_cursor(
        list(feed.symbols),
        feed.n_events,
        seed,
        params.initial_ticks,
        params.tick_volatility,
        params.depth_levels,
        params.arrival_decay,
        params.min_size,
        params.max_size,
        params.cancel_probability,
        params.target_resting_orders,
        params.trade_probability,
        params.exchange_gap_ns,
        params.feed_latency_ns,
        params.feed_jitter_ns,
        params.decode_latency_ns,
        params.decode_jitter_ns,
    )


def _events_from_cursor(cursor) -> Iterator[Event]:
    while True:
        rows = cursor.next_batch(1024)
        if not rows:
            return
        for action, symbol, seq, exchange_ts, recv_ts, ingest_ts, side, price, qty, order_ref in rows:
            stamps = {
                "symbol": symbol,
                "seq": seq,
                "exchange_ts": exchange_ts,
                "recv_ts": recv_ts,
                "ingest_ts": ingest_ts,
            }
            if action == "trade":
                yield Trade(price_ticks=price, qty=qty, aggressor=Side(side), **stamps)
            else:
                yield BookUpdate(
                    action=BookAction(action), side=Side(side), price_ticks=price,
                    qty=qty, order_ref=order_ref, **stamps,
                )
