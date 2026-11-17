"""NF/Q25: a malformed age limit, clock or bound never makes a verdict actionable.

Red on main 7fb17b6: the age limits are compared raw (NaN reads every age as fresh, +inf
removes the bound), and two Decimal bounds are outside ``_validate_params``.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from quant.book.lob import LimitOrderBook
from quant.events import PriceScale, Side
from quant.verify import (BorrowQuote, EdgeEstimate, MarketImpactInputs, PositionEffect,
                          VerifyParams, verify_proposal)

NAN, INF = float("nan"), float("inf")
NOW = 1_800_000_000_000_000_000
DAY = 24 * 3600 * 10**9
PENNY = PriceScale(Decimal(".01"))
METRICS = MarketImpactInputs("S", date(2027, 1, 14), Decimal("1000000000"), Decimal(".2"), 20, 1)
SELL = {"symbol": "S", "side": "sell", "quantity": 1, "time_horizon": "intraday"}
BUY = {"symbol": "S", "side": "buy", "quantity": 1, "time_horizon": "intraday"}
FRESH_ENOUGH = 20 * 10**9          # lets a 10s-old book through so the OTHER limit is tested


def _book(age_ns=10_000_000_000, mid=10000):
    book = LimitOrderBook("S")
    book.add(order_id="b", side=Side.BUY, price_ticks=mid - 1, qty=10)
    book.add(order_id="a", side=Side.SELL, price_ticks=mid + 1, qty=10)
    return replace(book.snapshot(), ts=NOW - age_ns)


def _edge(age_ns, gross="400"):
    return EdgeEstimate(expected_gross_return_bps=Decimal(gross), time_horizon="intraday",
                        source_id="src", evidence_ref="ev", computed_at_ns=NOW - age_ns,
                        verified=True)


def _borrow(age_ns, rate="0.01"):
    return BorrowQuote(annual_rate=Decimal(rate), locate_fee=Decimal("0"), source_id="b",
                       computed_at_ns=NOW - age_ns, verified=True)


def _verify(proposal, params, **kw):
    kw.setdefault("metrics", METRICS)
    kw.setdefault("now_ns", NOW)
    return verify_proposal(proposal, kw.pop("book", _book()), params, **kw)


# The sweep's own witness (s6-ck-quant F25), verbatim in substance.
def test_F25_nan_staleness_limit_does_not_make_an_old_book_actionable():
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=float("nan")))
    assert not verdict.is_actionable, (verdict.verdict.value, verdict.expected_cost_bps)


@pytest.mark.parametrize("limit", [NAN, INF, 5e9, "5000000000", Decimal("5000000000")])
def test_malformed_staleness_limit_is_insufficient_data(limit):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=limit))
    assert verdict.verdict.value == "insufficient_data"
    assert not verdict.is_actionable


@pytest.mark.parametrize("limit", [NAN, INF])
def test_malformed_edge_age_limit_cannot_accept_a_month_old_edge(limit):
    verdict = _verify(BUY, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH, max_edge_age_ns=limit),
                      expected_edge=_edge(30 * DAY))
    assert verdict.net_edge_state != "sufficient"
    assert not verdict.is_actionable


@pytest.mark.parametrize("limit", [NAN, INF])
def test_malformed_borrow_age_limit_cannot_accept_month_old_borrow(limit):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH, max_borrow_age_ns=limit),
                      position_effect=PositionEffect.OPEN_SHORT, expected_edge=_edge(0),
                      borrow_quote=_borrow(30 * DAY))
    assert verdict.borrow_state != "verified"
    assert not verdict.is_actionable


@pytest.mark.parametrize("limit", [INF, NAN, 7.5, True])
def test_malformed_metrics_age_limit_cannot_accept_year_old_metrics(limit):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH, max_metrics_age_days=limit),
                      metrics=replace(METRICS, age_days=400))
    assert not verdict.is_actionable


def test_infinite_borrow_rate_bound_does_not_verify_an_out_of_contract_rate():
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH,
                                         max_borrow_rate=Decimal("Infinity")),
                      position_effect=PositionEffect.OPEN_SHORT, expected_edge=_edge(0),
                      borrow_quote=_borrow(0, rate="6"))      # contract default bound is 5.0
    assert verdict.borrow_state != "verified"


@pytest.mark.parametrize("bound", [Decimal("NaN"), NAN])
def test_nan_borrow_rate_bound_is_insufficient_data_not_an_exception(bound):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH, max_borrow_rate=bound),
                      position_effect=PositionEffect.OPEN_SHORT, expected_edge=_edge(0),
                      borrow_quote=_borrow(0))
    assert verdict.verdict.value == "insufficient_data"


def test_infinite_edge_bound_does_not_accept_an_out_of_contract_edge():
    verdict = _verify(BUY, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH,
                                        max_expected_gross_return_bps=Decimal("Infinity")),
                      expected_edge=_edge(0, gross="99999999"))
    assert verdict.net_edge_state != "sufficient"
    assert not verdict.is_actionable


@pytest.mark.parametrize("now", [NAN, float(NOW), True])
def test_malformed_clock_is_insufficient_data(now):
    verdict = _verify(SELL, VerifyParams(PENNY), now_ns=now)
    assert verdict.verdict.value == "insufficient_data"


@pytest.mark.parametrize("ts", [NAN, float(NOW)])
def test_malformed_book_time_is_insufficient_data(ts):
    verdict = _verify(SELL, VerifyParams(PENNY), book=replace(_book(), ts=ts))
    assert verdict.verdict.value == "insufficient_data"


# ---- controls ----

def test_a_fresh_book_with_default_limits_is_still_priced_ok():
    verdict = _verify(SELL, VerifyParams(PENNY), book=_book(age_ns=1_000_000_000))
    assert verdict.verdict.value == "ok" and verdict.is_actionable


def test_a_stale_book_with_default_limits_is_still_refused_as_stale():
    verdict = _verify(SELL, VerifyParams(PENNY))
    assert verdict.reasons == ("book snapshot is 10.0s stale",)
