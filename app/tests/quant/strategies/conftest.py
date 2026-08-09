"""Shared builders for strategy tests.

Strategies are pure functions of (market state -> intents), so their tests need a market
state and nothing else — no venue, no matcher, no clock. These helpers build that state
directly, which is the whole payoff of the intent-returning contract in ``strategies/base``.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import PriceScale
from quant.strategies.base import StrategyContext

PENNY = PriceScale(tick_size=Decimal("0.01"))


def snapshot(
    symbol: str = "TEST",
    *,
    bid: int | None = 10_000,
    ask: int | None = 10_002,
    bid_qty: int = 500,
    ask_qty: int = 500,
    seq: int = 1,
    ts: int = 0,
) -> BookSnapshot:
    """A one-level-per-side book. Sizes default equal so the microprice equals the mid,
    keeping inventory-skew tests free of an imbalance term they are not testing."""
    return BookSnapshot(
        symbol=symbol, seq=seq, ts=ts,
        bids=() if bid is None else (LevelSnapshot(price_ticks=bid, qty=bid_qty, order_count=1),),
        asks=() if ask is None else (LevelSnapshot(price_ticks=ask, qty=ask_qty, order_count=1),),
    )


def context(
    *books: BookSnapshot,
    ts: int = 0,
    positions: dict | None = None,
    live_orders: tuple = (),
    primary: str | None = None,
) -> StrategyContext:
    mapping = {b.symbol: b for b in books}
    return StrategyContext(
        ts=ts, books=mapping, positions=positions or {}, live_orders=live_orders,
        scale=PENNY, primary_symbol=primary,
    )


@pytest.fixture
def ctx() -> StrategyContext:
    return context(snapshot())
