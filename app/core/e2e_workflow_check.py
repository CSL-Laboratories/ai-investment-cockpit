"""E2E workflow check helper. Safe to delete."""
from __future__ import annotations


def last_n(items: list[int] | None, n: int) -> list[int]:
    """Return the last n items of a list (empty list for None)."""
    if not items or n <= 0:
        return []
    return items[-n:]


def average(values: list[float]) -> float:
    """Arithmetic mean of values (0.0 for an empty list)."""
    if not values:
        return 0.0
    return sum(values) / len(values)
