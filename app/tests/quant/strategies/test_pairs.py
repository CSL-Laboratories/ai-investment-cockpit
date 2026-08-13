"""QT-02 — pairs / stat-arb gates.

The properties pinned:

  * THE HEDGE RATIO IS ROLLING. A beta fitted over all history is fitted mostly to a regime
    that has ended, and the spread inherits a spurious trend that reads as a confident signal.
  * BOTH LEGS OR NEITHER. A single-leg entry is naked directional risk wearing a pairs label,
    and against a real book the illiquid leg is exactly the one that fails to fill.
  * THE STOP EXISTS BECAUSE THE PREMISE CAN BE FALSE. Beyond ``stop_z`` the position is
    flattened rather than added to. Mean-reversion strategies do not die of many small
    losses; they die once, and without a stop the backtest average is meaningless.
  * TWO SYMBOLS, EXACTLY. Refused rather than defaulted — a hedge ratio is meaningless with
    any other number.
"""
from __future__ import annotations

import pytest

from quant.events import Side
from quant.strategies.pairs import Pairs, PairsParams

from .conftest import context, snapshot


def _pairs(**kwargs) -> Pairs:
    params = PairsParams(beta_window=kwargs.pop("beta_window", 30),
                         z_window=kwargs.pop("z_window", 30), **kwargs)
    return Pairs(name="pairs", symbols=("AAA", "BBB"), params=params)


def _both(x_ticks: int, y_ticks: int, ts: int = 0, **kw):
    """A context holding both legs at the given fair values (equal sizes -> mid == micro)."""
    return context(
        snapshot("AAA", bid=x_ticks - 1, ask=x_ticks + 1),
        snapshot("BBB", bid=y_ticks - 1, ask=y_ticks + 1),
        ts=ts, **kw,
    )


def _warm(strategy: Pairs, *, beta: float = 2.0, n: int = 60) -> None:
    """Drive a linear relationship WITH residual noise, so the estimators are warm and the
    z-score is defined.

    The noise is not decoration. A perfectly clean relationship produces residuals that are
    identically zero, and ``RollingZScore`` correctly reports None for a zero-variance
    window — a constant spread is no signal, not an infinitely strong one. A warm-up without
    noise therefore leaves the strategy permanently mute, which is the right behaviour and
    the wrong fixture.

    Deterministic (no RNG) so a failure is reproducible, and the x range is wide enough that
    the rolling beta is well-conditioned — two closely-related names naturally give x little
    variance, which is where an OLS hedge ratio is least stable.
    """
    for i in range(n):
        x = 10_000 + (i % 23) * 50
        noise = ((i * 37) % 11) - 5
        strategy.on_book_update(_both(x, int(beta * x) + noise, ts=i))


# ---- construction --------------------------------------------------------------------------


def test_a_pair_needs_exactly_two_symbols():
    with pytest.raises(ValueError, match="exactly two symbols"):
        Pairs(name="p", symbols=("AAA",))
    with pytest.raises(ValueError, match="exactly two symbols"):
        Pairs(name="p", symbols=("AAA", "BBB", "CCC"))


def test_a_cold_pair_does_not_trade():
    p = _pairs()
    assert p.on_book_update(_both(10_000, 20_000)) == []


def test_a_missing_leg_stops_the_strategy_rather_than_trading_the_one_it_can_see():
    p = _pairs()
    _warm(p)
    only_x = context(snapshot("AAA", bid=9_999, ask=10_001))
    assert p.on_book_update(only_x) == []


# ---- the signal -------------------------------------------------------------------------------


def test_the_hedge_ratio_recovers_the_true_relationship():
    p = _pairs()
    _warm(p, beta=2.0)
    # Tolerance, not exactness: the warm-up carries residual noise on purpose (see _warm),
    # so the recovered slope is an estimate. Demanding an exact 2.0 would only be passable
    # with a degenerate fixture that leaves the z-score undefined.
    assert p.describe()["state"]["beta"] == pytest.approx(2.0, rel=0.01)


def test_a_spread_within_the_entry_band_does_not_trade():
    p = _pairs(entry_z=2.0)
    _warm(p)
    assert p.on_book_update(_both(10_000, 20_000, ts=100)) == []


def test_a_rich_y_leg_shorts_y_and_buys_x():
    """z > 0 means y is expensive relative to x. Short the rich leg, long the cheap one."""
    p = _pairs(entry_z=1.5, trade_size=100)
    _warm(p)
    intents = p.on_book_update(_both(10_000, 20_400, ts=100))
    by_symbol = {i.symbol: i for i in intents}
    assert set(by_symbol) == {"AAA", "BBB"}
    assert by_symbol["BBB"].side is Side.SELL
    assert by_symbol["AAA"].side is Side.BUY


def test_a_cheap_y_leg_buys_y_and_shorts_x():
    """The mirror, asserted separately so a sign error cannot pass on the rich case alone."""
    p = _pairs(entry_z=1.5, trade_size=100)
    _warm(p)
    intents = p.on_book_update(_both(10_000, 19_600, ts=100))
    by_symbol = {i.symbol: i for i in intents}
    assert by_symbol["BBB"].side is Side.BUY
    assert by_symbol["AAA"].side is Side.SELL


def test_the_x_leg_is_sized_by_the_hedge_ratio_not_matched_share_for_share():
    """Matching share counts on a beta-2 pair leaves half the exposure unhedged."""
    p = _pairs(entry_z=1.5, trade_size=100)
    _warm(p, beta=2.0)
    intents = p.on_book_update(_both(10_000, 20_400, ts=100))
    by_symbol = {i.symbol: i for i in intents}
    assert by_symbol["BBB"].qty == 100
    # ~beta * size, not size. The estimate carries the warm-up's residual noise, so this is
    # a tolerance; what it must NOT be is 100, which is the share-matched mistake.
    assert by_symbol["AAA"].qty == pytest.approx(200, rel=0.05)
    assert by_symbol["AAA"].qty != by_symbol["BBB"].qty


def test_both_legs_are_sent_together_or_not_at_all():
    p = _pairs(entry_z=1.5)
    _warm(p)
    intents = p.on_book_update(_both(10_000, 20_400, ts=100))
    assert len({i.symbol for i in intents}) == 2


# ---- exits ----------------------------------------------------------------------------------------


def test_a_decayed_spread_flattens_the_position():
    p = _pairs(entry_z=2.0, exit_z=0.5)
    _warm(p)
    intents = p.on_book_update(
        _both(10_000, 20_000, ts=100, positions={"AAA": 200, "BBB": -100})
    )
    by_symbol = {i.symbol: i for i in intents}
    assert by_symbol["AAA"].side is Side.SELL and by_symbol["AAA"].qty == 200
    assert by_symbol["BBB"].side is Side.BUY and by_symbol["BBB"].qty == 100


def test_a_half_filled_entry_flattens_the_naked_x_leg_immediately():
    """BUG-042: recovery cannot be conditional on leg_y being the leg that filled."""
    p = _pairs(entry_z=2.0, exit_z=0.5)
    _warm(p)

    intents = p.on_book_update(
        _both(10_000, 20_000, ts=100, positions={"AAA": 200, "BBB": 0})
    )

    assert len(intents) == 1
    assert intents[0].symbol == "AAA"
    assert intents[0].side is Side.SELL
    assert intents[0].qty == 200


def test_a_spread_beyond_the_stop_flattens_rather_than_adding_to_the_position():
    """THE test that distinguishes this from a strategy defined by its one terminal event.
    Every further divergence looks like a better entry, which is exactly why it must not be."""
    p = _pairs(entry_z=2.0, stop_z=3.0, trade_size=100)
    _warm(p)
    intents = p.on_book_update(
        _both(10_000, 30_000, ts=100, positions={"AAA": 200, "BBB": -100})
    )
    by_symbol = {i.symbol: i for i in intents}
    assert by_symbol["BBB"].side is Side.BUY      # buying back the short: flattening
    assert by_symbol["BBB"].qty == 100
    assert by_symbol["AAA"].side is Side.SELL


def test_it_does_not_re_enter_while_legs_are_still_in_flight():
    from quant.oms import OMS

    p = _pairs(entry_z=1.5)
    _warm(p)
    first = p.on_book_update(_both(10_000, 20_400, ts=100))
    oms = OMS()
    for intent in first:
        oms.register(intent, strategy="pairs")
    again = p.on_book_update(
        _both(10_000, 20_400, ts=101, live_orders=tuple(oms.live_orders()))
    )
    assert again == []


def test_it_respects_the_maximum_position():
    # stop_z is raised out of the way on purpose: at this dislocation the stop would fire
    # first and flatten, which is correct behaviour but a different property. Testing the
    # position limit requires reaching the entry branch.
    p = _pairs(entry_z=1.5, stop_z=99.0, trade_size=100, max_position=100)
    _warm(p)
    assert p.on_book_update(
        _both(10_000, 20_400, ts=100, positions={"AAA": 200, "BBB": -100})
    ) == []


def test_describe_reports_beta_zscore_and_warmth():
    p = _pairs()
    assert p.describe()["state"]["warm"] is False
    _warm(p)
    described = p.describe()
    assert described["strategy"] == "pairs"
    assert described["params"]["legs"] == ["AAA", "BBB"]
    assert described["state"]["warm"] is True
    assert described["state"]["zscore"] is not None
