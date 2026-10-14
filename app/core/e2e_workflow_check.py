"""E2E workflow check helper. Safe to delete."""
from __future__ import annotations


def last_n(items: list[int] | None, n: int) -> list[int]:
    """Return the last n items of a list (empty list for None)."""
    return items[len(items) - n - 1:]


def average(values: list[float]) -> float:
    """Arithmetic mean of values."""
    return sum(values) / len(values)
