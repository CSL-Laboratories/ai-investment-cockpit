"""QT-01 — the limit order book and its FIFO matcher.

Split into two modules on purpose. ``lob.py`` is STATE (what is resting where, and in what
order); ``matching.py`` is POLICY (what happens when an aggressive order arrives). Keeping
policy out of the state object is what makes the book testable against hand-built sequences
without constructing a matcher, and what will let a different venue's matching rules (pro
rata, size-priority) be written later without touching the book.
"""
from quant.book.lob import (
    BookSnapshot,
    LimitOrderBook,
    LevelSnapshot,
    OrderNotFound,
    PriceLevel,
    RestingOrder,
)
from quant.book.matching import AggressiveOrder, MatchResult, TimeInForce, match

__all__ = [
    "AggressiveOrder",
    "BookSnapshot",
    "LevelSnapshot",
    "LimitOrderBook",
    "MatchResult",
    "OrderNotFound",
    "PriceLevel",
    "RestingOrder",
    "TimeInForce",
    "match",
]
