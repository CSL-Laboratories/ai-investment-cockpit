"""QT-03 — event sources. One interface, three regimes: synthetic, replay, live.

THE INTERFACE IS THE WHOLE POINT
---------------------------------
A feed is an iterable of events in ``recv_ts`` order. That is all. Because synthetic and
replay sources satisfy the same protocol, the runner, the strategies, the OMS and the PnL
code are written once and never learn which one they are attached to — and development
proceeds with no market data at all, which is what stops "I'll wire the real feed later"
from becoming "the engine only works on the shape of data I imagined".

ORDERED BY ``recv_ts``, NEVER BY ``exchange_ts``. THIS IS THE POINT-IN-TIME RULE.
----------------------------------------------------------------------------------
An event becomes visible to a strategy when it ARRIVED, not when the venue stamped it. The
distinction is the difference between a backtest and a lie:

  * Ordering by ``exchange_ts`` lets a strategy react to a book state that had not yet
    reached it. That is look-ahead measured in microseconds, and at market-making frequency
    microseconds are the entire edge — every latency-sensitive strategy becomes profitable
    and the result looks like alpha rather than like a bug.
  * The corpus already states this requirement for the fundamental data
    (``docs/capability-expansion-master-plan.md``: without ``available_date`` discipline,
    "X-02 backtests and E-02 replays lie by look-ahead"). This is the same rule at the
    microstructure timescale, where the window is nanoseconds rather than days and the
    temptation to ignore it is correspondingly larger.

``validate_ordering`` enforces it, and the replay feed refuses a file that violates it rather
than silently sorting — a dataset out of arrival order is a capture problem, and quietly
repairing it hides a defect in the thing whose latency you are trying to measure.

WHAT A FEED DOES NOT DO
------------------------
It does not touch a book, own a strategy, or decide what an event MEANS. Applying an event
to a book is ``runner.apply_market_event``, in one place, so replay and synthetic cannot
drift in how they mutate state.
"""
from __future__ import annotations

from typing import Iterable, Iterator, Protocol, runtime_checkable

from quant.events import Event

# The normalized columns a replay dataset must carry. Fixed HERE rather than in the reader,
# so the ITCH decoder writing Parquet and the reader consuming it agree by construction
# instead of by comment.
REPLAY_COLUMNS: tuple[str, ...] = (
    "symbol",
    "seq",
    "exchange_ts",
    "recv_ts",
    "kind",          # 'book' | 'trade'
    "action",        # BookAction value, empty for trades
    "side",          # 'buy' | 'sell' (aggressor for trades)
    "price_ticks",
    "qty",
    "order_ref",     # venue order id; null when the feed is aggregate-only
)


@runtime_checkable
class Feed(Protocol):
    """An ordered source of market events. Iterate it once."""

    symbols: tuple[str, ...]

    def __iter__(self) -> Iterator[Event]: ...


class FeedOrderingError(ValueError):
    """Events arrived out of ``recv_ts`` order, or a sequence number went backwards.

    Raised rather than sorted. Out-of-order arrival in a capture is a defect in the capture,
    and repairing it silently would hide a fault in exactly the pipeline whose latency the
    engine exists to characterise. A backtest run over quietly-reordered data measures
    something that never happened.
    """


def validate_ordering(events: Iterable[Event]) -> Iterator[Event]:
    """Pass events through, raising if arrival order or per-symbol sequence regresses.

    A generator rather than a list check, so an infinite or very large feed is still covered
    and the failure surfaces at the offending event instead of after the whole file is read.

    Sequence numbers are checked PER SYMBOL because they are per-symbol on real venues, and
    a global check would produce false failures on any multi-symbol capture. Sequence is the
    only gap-detection mechanism there is: a feed that drops a message still delivers
    well-formed events with plausible timestamps, so absence is invisible in the time domain
    and obvious in the sequence domain.
    """
    last_recv = -1
    last_seq: dict[str, int] = {}
    for event in events:
        if event.recv_ts < last_recv:
            raise FeedOrderingError(
                f"{type(event).__name__} for {event.symbol} arrived at recv_ts "
                f"{event.recv_ts}, before the previous event's {last_recv}. Events are "
                "ordered by ARRIVAL, not by exchange stamp — see quant/feed/__init__.py."
            )
        previous = last_seq.get(event.symbol)
        if previous is not None and event.seq < previous:
            raise FeedOrderingError(
                f"sequence for {event.symbol} went backwards: {event.seq} after {previous}. "
                "A gap or regression here means dropped messages, which is invisible in the "
                "time domain — every queue position after this point is wrong."
            )
        if previous is not None and event.seq > previous + 1:
            # BUG-077: the module claims sequence is THE gap-detection mechanism, yet only
            # regressions were rejected — a forward gap (a genuinely dropped message)
            # replayed as if the capture were complete, and every queue position and
            # volume statistic after it was wrong by an unknown amount.
            raise FeedOrderingError(
                f"sequence gap for {event.symbol}: {previous} -> {event.seq} skips "
                f"{event.seq - previous - 1} message(s). A dropped message is exactly what "
                "this check exists to refuse — reject the capture rather than replay "
                "corrupted queue positions."
            )
        last_recv = event.recv_ts
        last_seq[event.symbol] = event.seq
        yield event
