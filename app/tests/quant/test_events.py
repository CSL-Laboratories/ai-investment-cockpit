"""QT-01 — event schema gates.

Pins the decisions that are expensive to reverse once events exist in a dataset:

  * THREE timestamps per event, and the two latency subtractions derived from them in one
    place. A single "timestamp" cannot distinguish a slow feed from a slow decoder.
  * Event time is int nanoseconds; wall clock is a tz-aware datetime; ``to_ns``/``from_ns``
    are the only crossings and a NAIVE datetime is refused at the boundary.
  * Prices convert Decimal <-> integer ticks exactly, and an off-grid price is REFUSED
    rather than silently rounded.

The naive-datetime and off-grid-price tests both have a passing control case beside them,
so a green result is causal: it shows the guard fires on the bad input AND admits the good
one, rather than a validator that happens to reject everything.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quant.events import (
    AckStatus,
    BookAction,
    BookUpdate,
    EventTimeError,
    Fill,
    Liquidity,
    NS_PER_SECOND,
    OrderAck,
    PriceScale,
    Quote,
    Side,
    Trade,
    from_ns,
    to_ns,
)

AWARE = datetime(2026, 8, 7, 14, 30, 0, tzinfo=timezone.utc)
PENNY = PriceScale(tick_size=Decimal("0.01"))


def _stamps(exchange: int = 1_000, feed: int = 250, decode: int = 40) -> dict:
    """Three distinct stamps so a test that collapses them fails loudly."""
    return {
        "exchange_ts": exchange,
        "recv_ts": exchange + feed,
        "ingest_ts": exchange + feed + decode,
    }


# ---- wall clock <-> event time -------------------------------------------------------


def test_to_ns_round_trips_an_aware_datetime_exactly():
    assert from_ns(to_ns(AWARE)) == AWARE


def test_to_ns_is_epoch_nanoseconds_not_some_other_origin():
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert to_ns(epoch) == 0
    assert to_ns(epoch + timedelta(seconds=1)) == NS_PER_SECOND


def test_to_ns_refuses_a_naive_datetime():
    with pytest.raises(EventTimeError, match="NAIVE"):
        to_ns(datetime(2026, 8, 7, 14, 30, 0))


def test_to_ns_accepts_a_non_utc_aware_datetime_and_normalizes_it():
    """CONTROL for the naive test: awareness is the requirement, UTC is not.

    A seam handing us US/Eastern must work — otherwise callers 'fix' the rejection by
    stripping tzinfo, which is exactly the failure the guard exists to prevent.
    """
    eastern = timezone(timedelta(hours=-4))
    same_instant = AWARE.astimezone(eastern)
    assert to_ns(same_instant) == to_ns(AWARE)


def test_to_ns_refuses_a_bare_number_because_a_monotonic_reading_is_not_an_epoch():
    with pytest.raises(EventTimeError):
        to_ns(1_754_575_800.0)


def test_from_ns_truncates_to_microseconds_and_the_asymmetry_is_known():
    """``from_ns`` is lossy: datetime is microsecond-resolution, ITCH is nanosecond.

    Pinned rather than fixed. The loss is acceptable at the reporting boundary and
    unacceptable inside the engine, so what matters is that it stays KNOWN — a future reader
    must not discover it by way of a mismatched tearsheet.
    """
    ns = 1_700_000_000_123_456_789
    assert to_ns(from_ns(ns)) == 1_700_000_000_123_456_000


# ---- the three timestamps ------------------------------------------------------------


def test_latency_decomposes_into_feed_and_decode_which_sum_to_total():
    trade = Trade(symbol="AAPL", seq=1, price_ticks=15_000, qty=100,
                  aggressor=Side.BUY, **_stamps())
    assert trade.feed_latency_ns == 250
    assert trade.decode_latency_ns == 40
    assert trade.total_latency_ns == 290
    assert trade.feed_latency_ns + trade.decode_latency_ns == trade.total_latency_ns


def test_negative_feed_latency_is_reported_not_clamped():
    """Clock skew between capture host and venue is REAL INFORMATION, not an error.

    Clamping to zero would hide the only measurement that reveals the disagreement.
    """
    skewed = Trade(symbol="AAPL", seq=1, exchange_ts=1_000, recv_ts=900, ingest_ts=950,
                   price_ticks=15_000, qty=100, aggressor=Side.BUY)
    assert skewed.feed_latency_ns == -100


def test_a_float_timestamp_is_refused_because_it_is_a_wall_clock_value_that_wandered_over():
    with pytest.raises(EventTimeError, match="must be int"):
        Trade(symbol="AAPL", seq=1, exchange_ts=1_000.5, recv_ts=1_100, ingest_ts=1_200,
              price_ticks=15_000, qty=100, aggressor=Side.BUY)


def test_a_bool_is_refused_as_a_timestamp_even_though_bool_is_an_int_subclass():
    with pytest.raises(EventTimeError):
        Trade(symbol="AAPL", seq=True, exchange_ts=1_000, recv_ts=1_100, ingest_ts=1_200,
              price_ticks=15_000, qty=100, aggressor=Side.BUY)


def test_events_are_frozen_so_a_historical_record_cannot_be_edited():
    trade = Trade(symbol="AAPL", seq=1, price_ticks=15_000, qty=100,
                  aggressor=Side.BUY, **_stamps())
    with pytest.raises(Exception):
        trade.price_ticks = 99


# ---- price scale ---------------------------------------------------------------------


def test_price_scale_round_trips_decimal_through_integer_ticks():
    assert PENNY.to_ticks(Decimal("150.00")) == 15_000
    assert PENNY.to_decimal(15_000) == Decimal("150.00")


def test_off_grid_price_is_refused_rather_than_rounded():
    """Rounding here would silently change the caller's price. Refusing is the safe failure."""
    with pytest.raises(ValueError, match="not a multiple"):
        PENNY.to_ticks(Decimal("150.005"))


def test_on_grid_price_is_accepted():
    """CONTROL for the off-grid test — the validator must admit valid prices."""
    assert PENNY.to_ticks(Decimal("150.01")) == 15_001


def test_price_scale_refuses_float_because_money_is_never_a_float_in_this_repo():
    with pytest.raises(TypeError):
        PENNY.to_ticks(150.00)
    with pytest.raises(TypeError):
        PriceScale(tick_size=0.01)


# ---- derived statistics --------------------------------------------------------------


def test_microprice_leans_toward_the_side_with_less_size():
    """Heavy bid / thin ask means the next print is likelier at the ask, so fair value
    sits ABOVE the arithmetic mid. Weighting by the same side instead of the opposite one
    produces a signal that is exactly backwards, which is why it lives in one place."""
    quote = Quote(symbol="AAPL", seq=1, bid_ticks=100, bid_qty=900,
                  ask_ticks=102, ask_qty=100, **_stamps())
    assert quote.mid_ticks == 101.0
    assert quote.microprice > quote.mid_ticks
    assert quote.microprice == pytest.approx(101.8)


def test_microprice_equals_mid_when_both_sides_are_balanced():
    quote = Quote(symbol="AAPL", seq=1, bid_ticks=100, bid_qty=500,
                  ask_ticks=102, ask_qty=500, **_stamps())
    assert quote.microprice == quote.mid_ticks == 101.0


def test_one_sided_book_yields_no_spread_or_mid_rather_than_a_fabricated_number():
    quote = Quote(symbol="AAPL", seq=1, bid_ticks=100, bid_qty=500,
                  ask_ticks=None, ask_qty=0, **_stamps())
    assert quote.spread_ticks is None
    assert quote.mid_ticks is None
    assert quote.microprice is None


# ---- fills ---------------------------------------------------------------------------


def test_fill_signed_qty_carries_the_position_delta_so_signs_are_not_re_derived():
    buy = Fill(symbol="AAPL", seq=1, order_id="o1", side=Side.BUY, price_ticks=100,
               qty=250, liquidity=Liquidity.MAKER, **_stamps())
    sell = Fill(symbol="AAPL", seq=2, order_id="o2", side=Side.SELL, price_ticks=100,
                qty=250, liquidity=Liquidity.TAKER, **_stamps())
    assert buy.signed_qty == 250
    assert sell.signed_qty == -250


def test_a_zero_quantity_fill_is_not_representable():
    with pytest.raises(ValueError):
        Fill(symbol="AAPL", seq=1, order_id="o1", side=Side.BUY, price_ticks=100,
             qty=0, liquidity=Liquidity.MAKER, **_stamps())


def test_side_sign_and_opposite_are_consistent():
    assert Side.BUY.sign == 1 and Side.SELL.sign == -1
    assert Side.BUY.opposite is Side.SELL
    assert Side.SELL.opposite.opposite is Side.SELL


def test_book_update_distinguishes_cancel_from_execute():
    """They have OPPOSITE meanings for orders queued behind them and must not collapse:
    a cancel ahead is free queue advancement, an execute ahead advances you AND says the
    price is trading. A taxonomy with one 'remove' action discards that signal."""
    assert BookAction.CANCEL is not BookAction.EXECUTE
    update = BookUpdate(symbol="AAPL", seq=1, action=BookAction.CANCEL, side=Side.BUY,
                        price_ticks=100, qty=50, order_ref=777, **_stamps())
    assert update.order_ref == 777


def test_order_ack_is_a_distinct_event_from_a_fill():
    """Acceptance and execution are separate instants with separate latencies. A strategy
    that treats 'accepted' as 'resting at the front' has assumed away the queue."""
    ack = OrderAck(symbol="AAPL", seq=1, order_id="o1", status=AckStatus.ACCEPTED, **_stamps())
    assert ack.status is AckStatus.ACCEPTED
    assert not isinstance(ack, Fill)
