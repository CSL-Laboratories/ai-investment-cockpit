"""QT-02 — Avellaneda-Stoikov market maker gates.

The properties pinned, in order of how badly getting them wrong hurts:

  * A COLD MODEL DOES NOT QUOTE. The half-spread divides by volatility; substituting zero
    produces an infinitely confident quote at exactly the moment the model knows least.
  * INVENTORY SKEWS THE RESERVATION PRICE DOWNWARD WHEN LONG. Both quotes move down, making
    the ask more attractive — the model sheds risk by repricing, not by crossing. A maker
    without this accumulates a position that dwarfs its spread earnings.
  * THE REQUOTE THRESHOLD HOLDS A SLIGHTLY STALE QUOTE. Against a real FIFO book a replace
    goes to the back of the queue, so requoting on every tick destroys the queue priority
    that is the passive strategy's actual asset. This is the behaviour an exp(-k*delta) fill
    model cannot express, and the reason the whole LOB exists.
  * THE INVENTORY BOUND IS HARD, NOT A SKEW. A skew alone is soft and a trending market
    walks straight through it.
  * QUOTES ROUND OUTWARD, never inside the model's own spread.
"""
from __future__ import annotations

import math

import pytest

from quant.events import Side
from quant.oms import CancelOrder, NewOrder
from quant.strategies.market_maker import MarketMaker, MarketMakerParams

from .conftest import context, snapshot


def _warm(mm: MarketMaker, *, ticks=(10_000, 10_004, 10_001, 10_003, 10_002)) -> None:
    """Feed enough mid movement for the EWMA to produce a usable volatility."""
    for i, ask in enumerate(ticks):
        mm.on_book_update(context(snapshot(bid=ask - 2, ask=ask), ts=i * 1_000_000))


def _maker(**kwargs) -> MarketMaker:
    return MarketMaker(name="mm", symbols=("TEST",), params=MarketMakerParams(**kwargs))


# ---- refusing to speak ------------------------------------------------------------------


def test_a_cold_model_produces_no_quotes():
    mm = _maker()
    assert mm.quotes(context(snapshot())) is None
    assert mm.on_book_update(context(snapshot())) == []


def test_a_warm_model_does_quote():
    """CONTROL for the cold test — otherwise a maker that never quotes would pass it."""
    mm = _maker()
    _warm(mm)
    assert mm.quotes(context(snapshot())) is not None


def test_a_non_finite_volatility_refuses_to_quote():
    """A NaN sigma fails every comparison, so the skew clamp would pin at +max."""
    mm = _maker()
    _warm(mm)
    ctx = context(snapshot(), positions={"TEST": 0})
    assert mm.quotes(ctx) is not None
    mm._vol.update(float("nan"))
    assert mm._vol.value is not None and math.isnan(mm._vol.value)
    assert mm.quotes(ctx) is None


def test_a_one_sided_book_produces_no_quotes():
    """No ask means no mid, and a reservation price derived from half a book is a guess
    dressed as a model."""
    mm = _maker()
    _warm(mm)
    assert mm.quotes(context(snapshot(ask=None))) is None


# ---- the model ----------------------------------------------------------------------------


def test_long_inventory_pushes_both_quotes_down():
    """The inventory skew, stated directly. Long means the maker wants to sell, so it lowers
    the ask to attract a buyer AND lowers the bid to discourage buying more."""
    mm = _maker(gamma=0.5)
    _warm(mm)
    flat = mm.quotes(context(snapshot(), positions={"TEST": 0}))
    long = mm.quotes(context(snapshot(), positions={"TEST": 5_000}))
    assert long[0] < flat[0]
    assert long[1] < flat[1]


def test_short_inventory_pushes_both_quotes_up():
    """The mirror. Asserted separately because a sign error would pass the long-only test
    if the skew were keyed on absolute inventory."""
    mm = _maker(gamma=0.5)
    _warm(mm)
    flat = mm.quotes(context(snapshot(), positions={"TEST": 0}))
    short = mm.quotes(context(snapshot(), positions={"TEST": -5_000}))
    assert short[0] > flat[0]
    assert short[1] > flat[1]


def test_higher_risk_aversion_widens_the_spread():
    timid = _maker(gamma=2.0)
    bold = _maker(gamma=0.05)
    _warm(timid)
    _warm(bold)
    timid_bid, timid_ask = timid.quotes(context(snapshot()))
    bold_bid, bold_ask = bold.quotes(context(snapshot()))
    assert (timid_ask - timid_bid) >= (bold_ask - bold_bid)


def test_the_inventory_term_decays_as_the_horizon_closes():
    """(T - t) multiplies the inventory skew, so an identical position skews less near the
    end of the horizon. Without this the maker never becomes willing to unwind."""
    mm = _maker(gamma=0.5, horizon_ns=1_000_000_000)
    _warm(mm)
    early = mm.quotes(context(snapshot(), ts=0, positions={"TEST": 5_000}))
    late = mm.quotes(context(snapshot(), ts=990_000_000, positions={"TEST": 5_000}))
    assert abs(late[0] - 10_001) < abs(early[0] - 10_001)


def test_time_remaining_is_clamped_so_it_never_goes_negative():
    """Past the horizon a negative (T - t) would FLIP the inventory term and make the maker
    skew INTO its position — the opposite of unwinding, and genuinely dangerous."""
    mm = _maker(gamma=0.5, horizon_ns=1_000)
    _warm(mm)
    late = mm.quotes(context(snapshot(), ts=10_000_000, positions={"TEST": 5_000}))
    flat = mm.quotes(context(snapshot(), ts=10_000_000, positions={"TEST": 0}))
    assert late == flat        # the inventory term has fully decayed, not reversed


def test_quotes_never_land_inside_the_models_own_spread():
    """Rounding to nearest would sometimes place a quote inside the computed spread, giving
    away exactly the edge the spread was computed to capture."""
    mm = _maker()
    _warm(mm)
    bid, ask = mm.quotes(context(snapshot()))
    assert bid <= mm._last_reservation - mm._last_half_spread
    assert ask >= mm._last_reservation + mm._last_half_spread
    assert ask > bid


def test_the_spread_is_clamped_so_a_degenerate_estimate_cannot_produce_an_absurd_quote():
    mm = _maker(max_spread_ticks=4, gamma=50.0, kappa=0.001)
    _warm(mm, ticks=(10_000, 12_000, 8_000, 13_000, 7_000))
    bid, ask = mm.quotes(context(snapshot()))
    assert ask - bid <= 4 + 2       # clamp plus at most one tick of outward rounding per side


# ---- requoting and queue priority -------------------------------------------------------------


def test_a_small_target_move_does_NOT_requote():
    """THE queue-priority test. Under an exp(-k*delta) fill model holding a stale quote is
    pure loss; against a real FIFO book it preserves priority that a replace would forfeit."""
    mm = _maker(requote_threshold_ticks=5)
    _warm(mm)
    first = mm.on_book_update(context(snapshot()))
    live = _as_live(first)
    assert first, "the maker should have opened quotes"

    again = mm.on_book_update(context(snapshot(bid=10_001, ask=10_003), live_orders=live))
    assert again == []


def test_a_large_target_move_DOES_requote_by_cancelling_and_replacing():
    """CONTROL for the hysteresis test: a maker that never requotes would pass that one."""
    mm = _maker(requote_threshold_ticks=1)
    _warm(mm)
    live = _as_live(mm.on_book_update(context(snapshot())))
    intents = mm.on_book_update(context(snapshot(bid=10_050, ask=10_052), live_orders=live))
    assert any(isinstance(i, CancelOrder) for i in intents)
    assert any(isinstance(i, NewOrder) for i in intents)


def test_a_zero_threshold_requotes_on_every_move_which_is_the_priority_destroying_setting():
    """Pinned as a behaviour, not endorsed. Setting the threshold to 0 is the fastest way to
    make this strategy lose money against a real queue, and the engine can now show that."""
    mm = _maker(requote_threshold_ticks=0)
    _warm(mm)
    live = _as_live(mm.on_book_update(context(snapshot())))
    intents = mm.on_book_update(context(snapshot(bid=10_001, ask=10_003), live_orders=live))
    assert any(isinstance(i, CancelOrder) for i in intents)


# ---- the hard inventory bound ------------------------------------------------------------------


def test_at_the_long_limit_the_maker_stops_quoting_the_bid_entirely():
    """A skew alone is a soft control and a trending market walks straight through it."""
    mm = _maker(max_inventory=500, quote_size=100)
    _warm(mm)
    intents = mm.on_book_update(context(snapshot(), positions={"TEST": 500}))
    new_orders = [i for i in intents if isinstance(i, NewOrder)]
    assert [o.side for o in new_orders] == [Side.SELL]


def test_at_the_short_limit_the_maker_stops_quoting_the_ask_entirely():
    mm = _maker(max_inventory=500, quote_size=100)
    _warm(mm)
    intents = mm.on_book_update(context(snapshot(), positions={"TEST": -500}))
    new_orders = [i for i in intents if isinstance(i, NewOrder)]
    assert [o.side for o in new_orders] == [Side.BUY]


def test_within_the_limit_the_maker_quotes_both_sides():
    """CONTROL for the two bound tests."""
    mm = _maker(max_inventory=500, quote_size=100)
    _warm(mm)
    intents = mm.on_book_update(context(snapshot(), positions={"TEST": 0}))
    sides = {o.side for o in intents if isinstance(o, NewOrder)}
    assert sides == {Side.BUY, Side.SELL}


def test_crossing_the_limit_cancels_the_live_quote_on_the_offending_side():
    mm = _maker(max_inventory=500, quote_size=100)
    _warm(mm)
    live = _as_live(mm.on_book_update(context(snapshot(), positions={"TEST": 0})))
    intents = mm.on_book_update(context(snapshot(), positions={"TEST": 500}, live_orders=live))
    cancelled = {i.client_id for i in intents if isinstance(i, CancelOrder)}
    bid_ids = {o.client_id for o in live if o.side is Side.BUY}
    assert cancelled & bid_ids


# ---- housekeeping -----------------------------------------------------------------------------


def test_a_fill_retires_the_quote_so_the_next_update_reopens_it():
    """Requoting from inside on_fill would use book state that predates the trade that just
    happened — quoting against a book we already know to be stale."""
    from quant.events import Fill, Liquidity

    mm = _maker()
    _warm(mm)
    mm.on_book_update(context(snapshot()))
    bid_id = mm._bid_id
    fill = Fill(symbol="TEST", seq=1, exchange_ts=1, recv_ts=1, ingest_ts=1,
                order_id=bid_id, side=Side.BUY, price_ticks=10_000, qty=100,
                liquidity=Liquidity.MAKER)
    assert mm.on_fill(fill, context(snapshot())) == []
    assert mm._bid_id is None


def test_the_timer_requotes_in_a_book_that_has_stopped_updating():
    """Stale quotes in a quiet market are how inventory accumulates against you, and a
    strategy driven only by book updates cannot react to the absence of book updates."""
    mm = _maker()
    _warm(mm)
    intents = mm.on_timer(9_999_999, context(snapshot()))
    assert any(isinstance(i, NewOrder) for i in intents)


def test_describe_exposes_parameters_and_live_model_state():
    """A strategy whose parameters are not inspectable cannot be swept, compared or
    debugged, and every strategy eventually needs to be all three."""
    mm = _maker()
    _warm(mm)
    mm.on_book_update(context(snapshot()))
    described = mm.describe()
    assert described["strategy"] == "market_maker"
    assert described["params"]["requote_threshold_ticks"] == 1
    assert described["state"]["volatility"] is not None
    assert described["state"]["reservation_ticks"] is not None


def test_a_strategy_without_a_name_is_refused_because_the_name_is_the_pnl_key():
    with pytest.raises(ValueError, match="needs a name"):
        MarketMaker(name="", symbols=("TEST",))


def _as_live(intents):
    """Turn NewOrder intents into the ManagedOrder shape the context reports as live."""
    from quant.oms import OMS

    oms = OMS()
    for intent in intents:
        if isinstance(intent, NewOrder):
            oms.register(intent, strategy="mm")
    return tuple(oms.live_orders())


# ---- the reservation skew is bounded ----------------------------------------------------


def test_the_inventory_skew_cannot_walk_the_quote_off_the_book():
    """SANITY CLAMPS COVER THE SPREAD BUT NOT THE SKEW — and the skew is the quadratic one.

    ``_sigma_ticks`` converts return-vol to price-vol by multiplying by the mid, so
    ``variance_term = gamma * sigma^2 * tau`` grows with mid SQUARED, and the skew
    ``inventory * variance_term`` grows with it. Only ``half_spread`` was clamped
    (``max_spread_ticks``), so with any real inventory the reservation price landed far
    through the market. The quote is then added to the book, which RAISES the mid, and the
    next iteration squares the new mid: a positive feedback loop that reaches inf in ~60
    events and dies in ``math.floor(inf)`` with an uncaught OverflowError.

    ``ask <= bid`` at the end of ``quotes()`` fixes internal ordering only. It says nothing
    about how far from the market the pair sits, which is the thing that runs away.
    """
    mm = _maker(max_inventory=1_000, max_skew_ticks=50)
    _warm(mm)
    ctx = context(snapshot(bid=9_999, ask=10_001), positions={"TEST": 1_000})
    mid = 10_000

    quote = mm.quotes(ctx)
    assert quote is not None
    bid, ask = quote
    # Bounded by the skew clamp plus at most a full max spread of half-width on each side.
    limit = 50 + mm.params.max_spread_ticks
    assert abs(bid - mid) <= limit, f"bid {bid} ran {abs(bid - mid)} ticks from mid {mid}"
    assert abs(ask - mid) <= limit, f"ask {ask} ran {abs(ask - mid)} ticks from mid {mid}"


def test_a_short_position_is_clamped_symmetrically():
    """The runaway is sign-symmetric: short inventory skews the reservation UP by the same
    quadratic term, so a one-sided clamp would just move the crash to the other side."""
    mm = _maker(max_inventory=1_000, max_skew_ticks=50)
    _warm(mm)
    ctx = context(snapshot(bid=9_999, ask=10_001), positions={"TEST": -1_000})
    quote = mm.quotes(ctx)
    assert quote is not None
    bid, ask = quote
    limit = 50 + mm.params.max_spread_ticks
    assert abs(bid - 10_000) <= limit and abs(ask - 10_000) <= limit


def test_the_skew_is_still_present_below_the_clamp():
    """CONTROL. A clamp that always binds would silently delete the inventory skew, which is
    the entire model. At small inventory the skew must still move the quote DOWN when long."""
    mm = _maker(max_inventory=1_000, max_skew_ticks=10_000)
    _warm(mm)
    flat = mm.quotes(context(snapshot(bid=9_999, ask=10_001), positions={}))
    long_ = mm.quotes(context(snapshot(bid=9_999, ask=10_001), positions={"TEST": 5}))
    assert flat is not None and long_ is not None
    assert long_[0] < flat[0] and long_[1] < flat[1], "long inventory must skew both quotes down"
