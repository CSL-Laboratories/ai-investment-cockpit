"""NF: the shared predicates behind the guard, verifier and reconcile boundaries.

``is_int_value``: only a real ``int`` (never ``bool``). ``finite_decimal``: a finite
``Decimal`` or ``None`` (never NaN, +-Infinity or ``bool``).
"""
from __future__ import annotations

import enum
from decimal import Decimal

import pytest

from core.numeric import finite_decimal, is_int_value


@pytest.mark.parametrize("value", [0, 1, -7, 10**30])
def test_int_is_an_int_value(value):
    assert is_int_value(value)


class _Lot(enum.IntEnum):
    ONE = 1


@pytest.mark.parametrize("value", [True, False, 1.0, float("nan"), float("inf"), Decimal("1"),
                                   "1", None, b"1", _Lot.ONE])
def test_everything_else_is_not_an_int_value(value):
    assert not is_int_value(value)


@pytest.mark.parametrize(("value", "expected"), [
    (1, "1"), (-2, "-2"), (0.5, "0.5"), ("1.25", "1.25"), (" 3 ", "3"), (Decimal("7"), "7"),
    ("1e400", "1E+400"),
])
def test_finite_values_parse(value, expected):
    assert finite_decimal(value) == Decimal(expected)


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -float("inf"),
                                   "NaN", "Infinity", "-inf", Decimal("NaN"), Decimal("Infinity"),
                                   "abc", "", None, [], b"1"])
def test_unreadable_or_non_finite_is_none(value):
    assert finite_decimal(value) is None
