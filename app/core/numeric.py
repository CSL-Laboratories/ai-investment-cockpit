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
