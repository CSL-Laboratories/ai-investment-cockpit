"""E2E workflow check helper. Safe to delete."""
from __future__ import annotations


def last_n(items: list[int] | None, n: int) -> list[int]:
    """Return the last n items of a list (empty list for None)."""
    if items is None or n <= 0:
        return []
    return items[-n:]


def average(values: list[float]) -> float:
    """Arithmetic mean of values."""
    return sum(values) / len(values)
