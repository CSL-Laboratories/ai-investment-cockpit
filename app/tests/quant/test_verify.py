"""Shared verifier integrity tests after W-21 retires displayed-depth economics."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import PriceScale
from quant.verify import MarketImpactInputs, VerifyParams, Verdict, proposal_quantity, verify_proposal


PENNY = PriceScale(tick_size=Decimal("0.01"))
PARAMS = VerifyParams(scale=PENNY)
METRICS = MarketImpactInputs(
    symbol="S", as_of=date(2026, 7, 7),
    median_daily_dollar_volume=Decimal("100000000"),
    annualized_realized_volatility=Decimal("0.20"), observations=60, age_days=1,
)


def book(*, bids=((10_000, 800),), asks=((10_002, 800),), seq=10, ts=0):
    return BookSnapshot(
        symbol="S", seq=seq, ts=ts,
        bids=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=1) for p, q in bids),
        asks=tuple(LevelSnapshot(price_ticks=p, qty=q, order_count=1) for p, q in asks),
    )


def proposal(**kwargs):
    out = {
        "symbol": "S", "side": "buy", "quantity": 1,
        "order_type": "limit", "time_horizon": "short",
    }
    out.update(kwargs)
    return out


def test_a_fresh_two_sided_l1_snapshot_is_priceable():
    verdict = verify_proposal(
        proposal(), book(), PARAMS, metrics=METRICS, now_ns=1_000_000,
    )
    assert verdict.verdict is Verdict.OK
    assert verdict.expected_cost_bps > 0


def test_one_sided_crossed_stale_and_future_books_fail_closed():
    cases = [
        verify_proposal(proposal(), book(asks=()), PARAMS, metrics=METRICS, now_ns=1),
        verify_proposal(
            proposal(), book(bids=((10_002, 1),), asks=((10_001, 1),)),
            PARAMS, metrics=METRICS, now_ns=1,
        ),
        verify_proposal(
            proposal(), book(ts=0), PARAMS, metrics=METRICS,
            now_ns=60_000_000_000,
        ),
        verify_proposal(
            proposal(), book(ts=2), PARAMS, metrics=METRICS, now_ns=1,
        ),
    ]
    assert all(result.verdict is Verdict.INSUFFICIENT_DATA for result in cases)


def test_unknown_side_effect_and_symbol_mismatch_are_refused():
    bad_side = verify_proposal(
        proposal(side="hodl"), book(), PARAMS, metrics=METRICS, now_ns=1,
    )
    bad_symbol = verify_proposal(
        proposal(symbol="OTHER"), book(), PARAMS, metrics=METRICS, now_ns=1,
    )
    assert bad_side.verdict is Verdict.INSUFFICIENT_DATA
    assert bad_symbol.verdict is Verdict.INSUFFICIENT_DATA


def test_quantity_is_derived_from_notional_and_fractional_explicit_size_is_refused():
    assert proposal_quantity({"notional": 10_000}, Decimal("100.00")) == 100
    assert proposal_quantity({"quantity": 250}, Decimal("100.00")) == 250
    assert proposal_quantity({"quantity": 5.7}, Decimal("100.00")) is None
    assert proposal_quantity({"quantity": 0}, Decimal("100.00")) is None
    assert proposal_quantity({"quantity": "garbage"}, Decimal("100.00")) is None


def test_verdict_is_deterministic_and_carries_l1_provenance():
    first = verify_proposal(
        proposal(), book(seq=99), PARAMS, metrics=METRICS, now_ns=5,
    )
    second = verify_proposal(
        proposal(), book(seq=99), PARAMS, metrics=METRICS, now_ns=5,
    )
    assert first == second
    assert first.book_seq == 99
    assert first.metrics_as_of == "2026-07-07"


def test_verify_imports_nothing_that_would_violate_invariant_5():
    import pathlib
    import quant.verify as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    for banned in (
        "import numpy", "import pandas", "import requests", "import httpx",
        "import subprocess", "open(", "urllib",
    ):
        assert banned not in source, f"quant/verify.py must not contain {banned!r}"
