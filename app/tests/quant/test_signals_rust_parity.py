"""CSL-42 — Rust online estimators must match ``quant.signals``.

Skipped when the extension is not installed, which is the hosted Python gate.
Each case builds one Rust-backed instance and a twin whose ``_core`` is cleared,
then feeds both the same stream. Integer order-flow ratios that land on
-1, 0, or 1 compare with ``==``. Log-return and rolling-sum paths compare with
a tight tolerance because ``math.log`` and ``f64::ln`` are not a bit-identical
contract.
"""
from __future__ import annotations

import math
import random

import pytest

from quant.signals import (
    SIGNALS_BACKEND,
    EwmaVolatility,
    OrderFlowImbalance,
    RollingBeta,
    RollingZScore,
)

pytestmark = pytest.mark.skipif(
    SIGNALS_BACKEND != "rust",
    reason="cockpit_quant signals extension is not installed",
)


def _same(rust_value, python_value) -> None:
    if rust_value is None or python_value is None:
        assert rust_value is python_value
        return
    if rust_value == python_value:
        return
    assert rust_value == pytest.approx(python_value, rel=1e-12, abs=1e-12)


def _pair(cls, *args, **kwargs):
    accelerated = cls(*args, **kwargs)
    reference = cls(*args, **kwargs)
    assert accelerated._core is not None
    reference._core = None
    return accelerated, reference


def test_window_messages_stay_on_the_python_path():
    with pytest.raises(ValueError, match=r"window must be >= 2, got 1"):
        RollingZScore(window=1)
    with pytest.raises(ValueError, match=r"window must be >= 1, got 0"):
        OrderFlowImbalance(window=0)
    with pytest.raises(ValueError, match=r"window must be >= 3, got 2"):
        RollingBeta(window=2)
    with pytest.raises(TypeError):
        RollingZScore(window=2.5)


def test_ewma_matches_including_non_positive_prices():
    rust, python = _pair(EwmaVolatility, lam=0.94)
    prices = [100.0, 101.0, 100.5, 0.0, -3.0, 102.0, 99.0, 100.0, 1e-6, 1e6]
    for price in prices:
        _same(rust.update(price), python.update(price))
        _same(rust.value, python.value)
        assert rust.is_warm is python.is_warm
    with pytest.raises(TypeError):
        rust.update(True)


def test_ewma_replay_matches_the_python_loop():
    import cockpit_quant

    rng = random.Random(42)
    prices = [100.0]
    for _ in range(400):
        prices.append(prices[-1] * math.exp(rng.gauss(0.0, 0.01)))
    prices[50] = 0.0
    replay = cockpit_quant.ewma_replay(0.94, prices)
    python = EwmaVolatility(lam=0.94)
    python._core = None
    for price, replayed in zip(prices, replay):
        _same(replayed, python.update(price))


def test_zscore_matches_through_rollover_and_a_constant_window():
    rust, python = _pair(RollingZScore, window=8)
    series = [float(i % 5) for i in range(20)] + [3.0] * 8 + [50.0, -50.0]
    for value in series:
        _same(rust.update(value), python.update(value))
        _same(rust.mean, python.mean)
        _same(rust.stdev, python.stdev)
        _same(rust.value, python.value)
        assert rust.is_warm is python.is_warm
    # The constant stretch fills the window, so the public value is None.
    constant = RollingZScore(window=5)
    for _ in range(5):
        constant.update(3.0)
    assert constant.stdev == 0.0
    assert constant.value is None


def test_ofi_sign_balance_and_the_signed_64_bit_domain():
    rust, python = _pair(OrderFlowImbalance, window=6)
    stream = [100, -100, 100, -100, 3, -1, 0, 2**62, -(2**62), 7, -7]
    for qty in stream:
        assert rust.update(signed_qty=qty) == python.update(signed_qty=qty)
        assert rust.value == python.value
        assert rust.is_warm is python.is_warm
    assert rust.value == python.value

    balanced_r, balanced_p = _pair(OrderFlowImbalance, window=4)
    for qty in (100, -100, 100, -100):
        assert balanced_r.update(signed_qty=qty) == balanced_p.update(signed_qty=qty)
    assert balanced_r.value == 0.0

    extreme, twin = _pair(OrderFlowImbalance, window=2)
    assert extreme.update(signed_qty=-(2**63)) == twin.update(signed_qty=-(2**63))
    assert extreme.update(signed_qty=2**63 - 1) == twin.update(signed_qty=2**63 - 1)

    with pytest.raises(ValueError, match="signed 64-bit"):
        rust.update(signed_qty=2**63)
    with pytest.raises(TypeError):
        rust.update(signed_qty=True)
    # The reference path still accepts a Python int outside the signed-64 domain.
    twin.update(signed_qty=2**63)
    assert twin.value == 1.0


def test_beta_matches_a_line_a_flat_x_and_a_regime_change():
    rust, python = _pair(RollingBeta, window=12)
    for i in range(12):
        x = float(i)
        _same(rust.update(x, 3.0 + 2.5 * x), python.update(x, 3.0 + 2.5 * x))
        _same(rust.beta, python.beta)
        _same(rust.alpha, python.alpha)
        _same(rust.residual(10.0, 28.0), python.residual(10.0, 28.0))
        assert rust.is_warm is python.is_warm
    for i in range(12):
        _same(rust.update(float(i), 2.0 * float(i)), python.update(float(i), 2.0 * float(i)))
        _same(rust.beta, python.beta)
        _same(rust.alpha, python.alpha)

    flat_r, flat_p = _pair(RollingBeta, window=6)
    for i in range(6):
        _same(flat_r.update(5.0, float(i)), flat_p.update(5.0, float(i)))
    assert flat_r.beta is None
    assert flat_r.residual(5.0, 1.0) is None
    with pytest.raises(TypeError):
        rust.residual("x", 1.0)
