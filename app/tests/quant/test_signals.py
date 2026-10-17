"""QT-02 — online estimator gates.

The properties pinned, all variants of one idea: AN ESTIMATOR THAT DOES NOT YET KNOW MUST
SAY SO, rather than returning a number that reads as confidence.

  * Cold estimators return None, never 0.0. A market maker DIVIDES by volatility; a zero
    estimate produces an infinitely confident quote at exactly the moment the model knows
    the least.
  * A degenerate input (zero variance) returns None, not a huge z-score. A constant spread
    is no signal, not an infinitely strong one.
  * Order-flow imbalance is signed by the AGGRESSOR, and the sign is asserted in both
    directions — a sign error here produces a signal that is exactly wrong.
  * Every estimator is ONLINE, so look-ahead is structurally impossible: a test feeds a
    prefix and confirms the answer does not depend on data supplied later.
"""
from __future__ import annotations

import logging
import math

import pytest

from quant.signals import EwmaVolatility, OrderFlowImbalance, RollingBeta, RollingZScore


# ---- volatility ------------------------------------------------------------------------


def test_lambda_outside_the_open_unit_interval_is_refused():
    for lam in (0.0, 1.0, 1.5, -0.5, float("nan"), float("inf"), 1):
        with pytest.raises(ValueError, match=r"lam must be in \(0, 1\)"):
            EwmaVolatility(lam=lam)


def test_signals_backend_is_logged(caplog):
    from quant import signals

    with caplog.at_level(logging.INFO, logger="quant.signals"):
        signals._load_native()
    assert f"SIGNALS_BACKEND={signals.SIGNALS_BACKEND}" in caplog.text


def test_volatility_is_none_until_warm_rather_than_zero():
    vol = EwmaVolatility()
    assert vol.value is None
    vol.update(100.0)
    assert vol.value is None          # one price is not a return
    vol.update(101.0)
    assert vol.value is None          # one return is not a variance
    vol.update(100.5)
    assert vol.value is not None and vol.value > 0
    assert vol.is_warm


def test_volatility_rises_with_larger_moves():
    calm, wild = EwmaVolatility(), EwmaVolatility()
    for i in range(60):
        calm.update(100.0 + (i % 2) * 0.01)
        wild.update(100.0 + (i % 2) * 5.0)
    assert wild.value > calm.value


def test_volatility_is_scale_free_because_it_is_built_on_log_returns():
    """A 1% move must produce the same estimate at $10 and at $1000. This is the property
    that makes the sigma-to-ticks conversion in the market maker a single multiplication —
    if it failed, the model would be silently rescaled by the price level."""
    cheap, dear = EwmaVolatility(), EwmaVolatility()
    for i in range(60):
        multiplier = 1.0 + (0.01 if i % 2 else 0.0)
        cheap.update(10.0 * multiplier)
        dear.update(1000.0 * multiplier)
    assert cheap.value == pytest.approx(dear.value, rel=1e-12)


def test_volatility_ignores_a_non_positive_price_instead_of_raising_on_log_of_zero():
    vol = EwmaVolatility()
    for price in (100.0, 101.0, 100.5):
        vol.update(price)
    before = vol.value
    vol.update(0.0)
    assert vol.value == before


def test_volatility_is_online_so_later_data_cannot_change_an_earlier_reading():
    """The look-ahead gate. An estimator that could be influenced by the future is the
    single easiest way to manufacture alpha in a backtest."""
    prefix = [100.0, 101.0, 100.5, 102.0]
    partial = EwmaVolatility()
    for p in prefix:
        partial.update(p)
    reading = partial.value

    full = EwmaVolatility()
    for p in prefix + [500.0, 0.1, 300.0]:
        full.update(p)
        if p == prefix[-1]:
            assert full.value == reading


# ---- z-score ----------------------------------------------------------------------------


def test_zscore_is_none_until_the_window_is_full():
    z = RollingZScore(window=10)
    for i in range(9):
        z.update(float(i))
        assert z.value is None
    z.update(9.0)
    assert z.value is not None


def test_zscore_of_a_constant_series_is_none_not_infinite():
    """A constant spread is NO signal, not an infinitely strong one, and a strategy must be
    able to tell the difference."""
    z = RollingZScore(window=5)
    for _ in range(5):
        z.update(3.0)
    assert z.stdev == 0.0
    assert z.value is None


def test_zscore_sign_follows_the_latest_observation():
    high, low = RollingZScore(window=20), RollingZScore(window=20)
    for i in range(19):
        high.update(float(i % 3))
        low.update(float(i % 3))
    high.update(50.0)
    low.update(-50.0)
    assert high.value > 0 and low.value < 0


def test_zscore_window_is_bounded_so_old_regimes_are_forgotten():
    z = RollingZScore(window=10)
    for _ in range(10):
        z.update(0.0)
    for _ in range(10):
        z.update(5.0)
    assert z.mean == 5.0        # the zeros have fully aged out


def test_a_degenerate_window_size_is_refused():
    with pytest.raises(ValueError):
        RollingZScore(window=1)


# ---- order flow imbalance -----------------------------------------------------------------


def test_ofi_is_positive_for_buy_aggression_and_negative_for_sell():
    """Both directions asserted. A sign error here yields a signal that is exactly wrong
    rather than merely weak, which is why ``Trade`` names the field ``aggressor``."""
    buys = OrderFlowImbalance(window=10)
    sells = OrderFlowImbalance(window=10)
    for _ in range(10):
        buys.update(signed_qty=100)
        sells.update(signed_qty=-100)
    assert buys.value == 1.0
    assert sells.value == -1.0


def test_ofi_is_zero_when_flow_is_balanced():
    ofi = OrderFlowImbalance(window=10)
    for i in range(10):
        ofi.update(signed_qty=100 if i % 2 else -100)
    assert ofi.value == pytest.approx(0.0)


def test_ofi_of_an_empty_window_is_zero_which_is_genuinely_no_imbalance():
    assert OrderFlowImbalance(window=10).value == 0.0


def test_ofi_is_normalised_by_gross_volume_so_it_stays_bounded():
    ofi = OrderFlowImbalance(window=100)
    for i in range(100):
        ofi.update(signed_qty=(1 if i % 3 else -2) * (i + 1) * 10)
    assert -1.0 <= ofi.value <= 1.0


# ---- rolling beta --------------------------------------------------------------------------


def test_beta_recovers_a_known_linear_relationship():
    beta = RollingBeta(window=50)
    for i in range(50):
        x = float(i)
        beta.update(x, 3.0 + 2.5 * x)
    assert beta.beta == pytest.approx(2.5)
    assert beta.alpha == pytest.approx(3.0)
    assert beta.residual(10.0, 3.0 + 2.5 * 10.0) == pytest.approx(0.0, abs=1e-9)


def test_beta_is_none_when_x_has_no_variance():
    """A vertical scatter has no slope. Returning a number here would hand the pairs
    strategy a hedge ratio derived from nothing."""
    beta = RollingBeta(window=10)
    for i in range(10):
        beta.update(5.0, float(i))
    assert beta.beta is None
    assert beta.residual(5.0, 1.0) is None


def test_beta_window_is_bounded_so_an_ended_regime_stops_dominating():
    """A beta fitted over all history is fitted mostly to a regime that has ended, and the
    resulting spread inherits a spurious trend that reads as a confident signal."""
    beta = RollingBeta(window=20)
    for i in range(20):
        beta.update(float(i), 10.0 * float(i))
    assert beta.beta == pytest.approx(10.0)
    for i in range(20):
        beta.update(float(i), 2.0 * float(i))
    assert beta.beta == pytest.approx(2.0)


def test_residual_is_the_spread_the_pairs_strategy_trades():
    beta = RollingBeta(window=30)
    for i in range(30):
        beta.update(float(i), 2.0 * float(i))
    rich = beta.residual(10.0, 25.0)      # y above where beta says it should be
    cheap = beta.residual(10.0, 15.0)
    assert rich > 0 and cheap < 0
    assert rich == pytest.approx(5.0)


def test_estimators_do_not_produce_nan_under_extreme_but_legal_input():
    """NaN propagates silently through every comparison as False, so a strategy fed NaN
    simply stops trading and reports nothing wrong."""
    vol = EwmaVolatility()
    for price in (1e-6, 1e6, 1e-6, 1e6):
        vol.update(price)
    assert vol.value is not None and not math.isnan(vol.value)
