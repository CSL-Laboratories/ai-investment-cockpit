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
import os
import random
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

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
    if isinstance(rust_value, float) and isinstance(python_value, float):
        if math.isnan(rust_value) and math.isnan(python_value):
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
    prices = [100.0, 101.0, 100.5, 0.0, -3.0, 102.0, 99.0, 100.0, 1e-6, 1e6, -0.0]
    for price in prices:
        _same(rust.update(price), python.update(price))
        _same(rust.value, python.value)
        assert rust.is_warm is python.is_warm
    # A non-float keeps the Python result. Bool is 1, and the extension does not
    # raise where the reference accepts the value.
    _same(rust.update(True), python.update(True))
    _same(rust.value, python.value)
    assert rust._core is None
    with pytest.raises(TypeError):
        rust.update("nope")
    with pytest.raises(TypeError):
        python.update("nope")


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


def test_ewma_non_float_inputs_match_python():
    integer = EwmaVolatility(lam=1)
    assert integer._core is None
    twin = EwmaVolatility(lam=1)
    twin._core = None
    for price in (100, 110, 90, True, Decimal("100")):
        assert integer.update(price) == twin.update(price)
        assert integer.value == twin.value

    rust, python = _pair(EwmaVolatility, lam=0.94)
    for price in (100.0, 1e308):
        _same(rust.update(price), python.update(price))
    # The next tiny price underflows to a zero ratio. Both paths raise, and
    # the estimate already published stays put.
    before_r, before_p = rust.value, python.value
    with pytest.raises(ValueError, match="math domain error"):
        rust.update(1e-308)
    with pytest.raises(ValueError, match="math domain error"):
        python.update(1e-308)
    _same(rust.value, before_r)
    _same(python.value, before_p)

    # +inf followed by a finite price is a zero ratio. Both paths raise.
    followed = EwmaVolatility(lam=0.94)
    plain = EwmaVolatility(lam=0.94)
    plain._core = None
    assert followed.update(float("inf")) is None
    assert plain.update(float("inf")) is None
    with pytest.raises(ValueError, match="math domain error"):
        followed.update(100.0)
    with pytest.raises(ValueError, match="math domain error"):
        plain.update(100.0)

    # A finite price then +inf is a non-finite ratio. ``math.log`` would return
    # inf; the extension raises so an infinite volatility cannot be quoted.
    finite = EwmaVolatility(lam=0.94)
    finite.update(100.0)
    with pytest.raises(ValueError, match="math domain error"):
        finite.update(float("inf"))
    assert finite.value is None


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

    # Integer observations use an exact sum. Converting each one to f64 first
    # would drop the +1.
    wide_r, wide_p = _pair(RollingZScore, window=3)
    for value in (2**53, 1, -(2**53), 4, True):
        assert wide_r.update(value) == wide_p.update(value)
        assert wide_r.mean == wide_p.mean
        assert wide_r.stdev == wide_p.stdev
        assert wide_r.value == wide_p.value
    assert wide_r._core is None


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

    # Outside signed 64-bit, and any non-int, the extension steps aside.
    assert rust.update(signed_qty=2**63) == python.update(signed_qty=2**63)
    assert rust.update(signed_qty=True) == python.update(signed_qty=True)
    assert rust.update(signed_qty=1.5) == python.update(signed_qty=1.5)
    assert rust._core is None

    ratio_r, ratio_p = _pair(OrderFlowImbalance, window=3)
    # float(2**53 - 1) / float(2**53 + 1) rounds to 1. Python's true division does not.
    assert ratio_r.update(signed_qty=2**53) == ratio_p.update(signed_qty=2**53)
    got = ratio_r.update(signed_qty=-1)
    assert got == ratio_p.update(signed_qty=-1)
    assert got == ((2**53) - 1) / ((2**53) + 1)
    assert ratio_r.value == ratio_p.value

    import cockpit_quant

    replay = cockpit_quant.ofi_replay(3, [2**53, -1])
    assert replay[-1] == ((2**53) - 1) / ((2**53) + 1)


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

    ints_r, ints_p = _pair(RollingBeta, window=4)
    for i in range(6):
        assert ints_r.update(i, 3 + 2 * i) == ints_p.update(i, 3 + 2 * i)
        assert ints_r.beta == ints_p.beta
        assert ints_r.alpha == ints_p.alpha
        assert ints_r.residual(1, 0) == ints_p.residual(1, 0)
    assert ints_r._core is None


def test_constant_window_and_compensated_sum_match_python():
    z_r, z_p = _pair(RollingZScore, window=200)
    for _ in range(200):
        assert z_r.update(100.37) == z_p.update(100.37)
    assert z_r.mean == z_p.mean
    assert z_r.stdev == z_p.stdev
    # 3.11's left-to-right sum leaves a nonzero stdev, so both sides return the
    # same float. Identity would fail for two equal float objects.
    _same(z_r.value, z_p.value)
    if sys.version_info >= (3, 12):
        assert z_p.mean == 100.37
        assert z_p.stdev == 0.0
        assert z_p.value is None

    beta_r, beta_p = _pair(RollingBeta, window=200)
    for i in range(200):
        assert beta_r.update(100.37, float(i)) == beta_p.update(100.37, float(i))
    _same(beta_r.beta, beta_p.beta)
    if sys.version_info >= (3, 12):
        assert beta_p.beta is None

    partial_r, partial_p = _pair(RollingZScore, window=5)
    for value in (1e16, 1.0, -1e16, 2.0, 3.0):
        assert partial_r.update(value) == partial_p.update(value)
    assert partial_r.mean == partial_p.mean
    if sys.version_info >= (3, 12):
        assert partial_p.mean == 1.2


def test_invalid_lambda_raises_the_python_domain_error():
    rust, python = _pair(EwmaVolatility, lam=1.5)
    rust.update(100.0)
    python.update(100.0)
    rust.update(101.0)
    python.update(101.0)
    with pytest.raises(ValueError, match="math domain error"):
        rust.update(100.0)
    with pytest.raises(ValueError, match="math domain error"):
        python.update(100.0)


def test_mutating_lambda_returns_to_python():
    rust, python = _pair(EwmaVolatility, lam=0.94)
    for price in (100.0, 101.0, 100.5, 102.0):
        _same(rust.update(price), python.update(price))
    rust.lam = 0.0
    python.lam = 0.0
    _same(rust.update(103.0), python.update(103.0))
    assert rust._core is None
    seeded = EwmaVolatility(0.94, _variance=0.25, _last=10.0, _count=4)
    assert seeded._core is None


def test_estimators_compare_by_observation_state():
    left, right = RollingZScore(window=3), RollingZScore(window=3)
    assert left == right
    left.update(1.0)
    assert left != right
    right.update(1.0)
    assert left == right
    assert EwmaVolatility(lam=0.94) == EwmaVolatility(lam=0.94)
    assert OrderFlowImbalance(window=4) == OrderFlowImbalance(window=4)
    assert RollingBeta(window=5) == RollingBeta(window=5)


def test_direct_core_rejects_an_empty_window():
    import cockpit_quant

    with pytest.raises(ValueError, match=r"window must be >= 2"):
        cockpit_quant.ZScoreCore(0, True)
    with pytest.raises(ValueError, match=r"window must be >= 1"):
        cockpit_quant.OfiCore(0)
    with pytest.raises(ValueError, match=r"window must be >= 3"):
        cockpit_quant.BetaCore(0, True)


def _python(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    app = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = app + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=app,
        env=env,
        check=False,
    )


def test_broken_extension_import_is_not_swallowed():
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                raise ImportError("broken extension")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        import quant.signals
        """
    )
    ran = _python(script)
    assert ran.returncode != 0
    assert "broken extension" in ran.stderr


def test_missing_extension_stays_on_python():
    script = textwrap.dedent(
        """
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return None
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        import quant.signals as signals
        assert signals.SIGNALS_BACKEND == "python", signals.SIGNALS_BACKEND
        """
    )
    ran = _python(script)
    assert ran.returncode == 0, ran.stderr
