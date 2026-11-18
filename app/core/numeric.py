"""Shared fail-closed Decimal parsing for gate, broker and workflow metrics."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def decimal_or_nan(value) -> Decimal:
    """Parse ``value`` as a ``Decimal``; return ``Decimal("NaN")`` when unparseable.

    Returns NaN instead of raising for unparseable input, and never silently reads
    bad input as zero (BUG-129): a malformed metric parsed as ``Decimal("0")``
    could pass a ceiling it should trip, so unparseable input reads as NaN and
    consumers treat non-finite values as fail-closed.
    """
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def is_int_value(value) -> bool:
    """True only for an exact ``int``; ``bool`` (and any int subclass), float, ``Decimal``
    and ``str`` are refused.

    Shares, ticks and nanoseconds are ints in the engine (``quant/events.py`` refuses
    anything else at the event boundary). A NaN or an infinity compares False against
    every bound, and ``True`` is ``1`` to Python, so a limit check that compared such a
    value read as passed. A caller refuses the value instead of comparing it. ``type() is``
    rather than ``isinstance``: the stricter reading, at half the cost on the guard path.
    """
    return type(value) is int


def finite_decimal(value) -> Decimal | None:
    """``value`` as a finite ``Decimal``, else ``None``.

    Unparseable input, NaN, +-Infinity and ``bool`` (``True`` is not a quantity) all
    read as ``None``, so a caller that needs a finite amount fails closed instead of
    comparing a value no bound can hold.
    """
    if isinstance(value, bool):
        return None
    parsed = decimal_or_nan(value)
    return parsed if parsed.is_finite() else None
