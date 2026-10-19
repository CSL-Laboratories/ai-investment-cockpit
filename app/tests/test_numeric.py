"""CSL-327 — pin the shared fail-closed Decimal parse contract.

``core.numeric.decimal_or_nan`` is now the single helper behind the gate, breaker,
order-transition, paper-performance and proposal metrics that used to carry five
private ``_dec`` copies. The contract: valid numeric input parses exactly;
unparseable input reads as ``Decimal("NaN")`` — never ``Decimal("0")`` — so every
consumer's ``is_finite()`` check fails closed (BUG-129) instead of silently
passing a ceiling.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.numeric import decimal_or_nan


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1.5"), "1.5"),
        (Decimal("0.20"), "0.20"),
        (0, "0"),
        (-7, "-7"),
        (1.5, "1.5"),
        (-0.0, "-0.0"),
        ("1.25", "1.25"),
        ("-3.14", "-3.14"),
        ("1e3", "1E+3"),
        (" 12 ", "12"),      # Decimal() trims surrounding whitespace
        ("1_000", "1000"),   # Decimal() accepts digit-group underscores
        ("١٢٣", "123"),       # Decimal() accepts non-ASCII digits
    ],
)
def test_valid_input_parses(value, expected):
    assert decimal_or_nan(value) == Decimal(expected)


@pytest.mark.parametrize(
    "value",
    ["abc", "", "  ", "1.2.3", "0x10", None, True, False, [], {}, object(), b"4.5"],
)
def test_unparseable_reads_nan_never_zero(value):
    got = decimal_or_nan(value)
    assert got.is_nan()
    assert not got.is_finite()
    assert got != Decimal("0")


@pytest.mark.parametrize(
    ("value", "expect_nan"),
    [
        ("NaN", True),
        ("nan", True),
        (float("nan"), True),
        ("Infinity", False),
        ("-Infinity", False),
        (float("inf"), False),
    ],
)
def test_non_finite_parses_through_not_swallowed(value, expect_nan):
    """Infinity/NaN are VALID Decimal parses — returned non-finite, not re-thrown.
    Consumers distinguish with ``is_finite()``/``is_nan()``; swallowing them to
    zero would silently pass ceilings."""
    got = decimal_or_nan(value)
    assert not got.is_finite()
    assert got.is_nan() is expect_nan


def test_breaker_drawdown_trips_on_unreadable_and_over_limit():
    """Consumer contract: the daily-drawdown breaker trips on unreadable or
    non-finite input and on drawdown over limit; it passes within limit.
    (``_breach_unreadable`` short-circuits malformed input before
    ``decimal_or_nan`` runs — this pins the breaker contract, not the helper's
    NaN path; that is covered by the gate malformed-drawdown pin in
    ``tests/test_bugfix_audit_2026_09_07.py`` and by the notional test below.)"""
    from orchestrator.breakers import breach_daily_equity_drawdown

    assert breach_daily_equity_drawdown("bad", "500") is True
    assert breach_daily_equity_drawdown("NaN", "500") is True
    assert breach_daily_equity_drawdown("Infinity", "500") is True
    assert breach_daily_equity_drawdown("600", "500") is True
    assert breach_daily_equity_drawdown("100", "500") is False


def test_order_notional_falls_through_on_unparseable():
    """Consumer where the helper's NaN decides the outcome: an unparseable
    explicit notional must fall through to qty*limit_price (or "0" for an
    un-priced order), not read as a real notional value."""
    from brokers.order_state_machine import _payload_notional
    from schemas.models.order import Order

    assert _payload_notional(Order(notional="bad", qty="2", limit_price="10")) == "20"
    assert _payload_notional(Order(notional="bad", qty="2", limit_price=None)) == "0"
    assert _payload_notional(Order(notional="7.5", qty="2", limit_price="10")) == "7.5"
    assert _payload_notional(Order(notional=None, qty="2", limit_price="10")) == "20"
