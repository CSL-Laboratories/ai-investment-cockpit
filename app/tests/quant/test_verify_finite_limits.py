"""NF/Q25: a malformed age limit, clock or bound never makes a verdict actionable.

Red on main 7fb17b6: the age limits are compared raw (NaN reads every age as fresh, +inf
removes the bound), and two Decimal bounds are outside ``_validate_params``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace
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
AGE_LIMIT_REASON = "execution-verification age limits must be whole ints"


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


# ---- answer to the first reads: a refusal never echoes a malformed book time ----

class _Int(int):
    """An int subclass: ``isinstance(x, int)`` reads it as an int, ``is_int_value`` does not."""


@pytest.mark.parametrize("now", [None, NAN, INF, True, float(NOW), "1"])
@pytest.mark.parametrize("ts", [NAN, INF, float(NOW), True, "1"])
def test_a_refusal_serializes_with_a_malformed_book_time(now, ts):
    verdict = _verify(SELL, VerifyParams(PENNY), now_ns=now, book=replace(_book(), ts=ts))
    assert verdict.verdict.value == "insufficient_data" and not verdict.is_actionable
    assert verdict.computed_at_ns == 0
    json.dumps(asdict(verdict), allow_nan=False, default=str)


@pytest.mark.parametrize("now", [None, NAN, True])
def test_a_refusal_keeps_a_whole_int_book_time(now):
    verdict = _verify(SELL, VerifyParams(PENNY), now_ns=now)
    assert verdict.verdict.value == "insufficient_data"
    assert verdict.computed_at_ns == NOW - 10_000_000_000


@pytest.mark.parametrize("age", [True, False, _Int(1)])
def test_a_bool_or_int_subclass_metrics_age_is_refused(age):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH),
                      metrics=replace(METRICS, age_days=age))
    assert verdict.verdict.value == "insufficient_data" and not verdict.is_actionable
    assert verdict.reasons == ("market metrics date is future or invalid",)


@pytest.mark.parametrize("count", [True, _Int(20)])
def test_a_bool_or_int_subclass_observation_count_is_refused(count):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH),
                      metrics=replace(METRICS, observations=count))
    assert verdict.verdict.value == "insufficient_data" and not verdict.is_actionable


def test_an_int_subclass_borrow_time_is_refused():
    quote = replace(_borrow(0), computed_at_ns=_Int(NOW))
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH),
                      position_effect=PositionEffect.OPEN_SHORT, expected_edge=_edge(0),
                      borrow_quote=quote)
    assert verdict.borrow_state != "verified" and not verdict.is_actionable


def test_an_int_subclass_edge_time_is_refused():
    edge = replace(_edge(0), computed_at_ns=_Int(NOW))
    verdict = _verify(BUY, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH), expected_edge=edge)
    assert verdict.net_edge_state != "sufficient" and not verdict.is_actionable


# ---- controls ----

def test_a_fresh_book_with_default_limits_is_still_priced_ok():
    verdict = _verify(SELL, VerifyParams(PENNY), book=_book(age_ns=1_000_000_000))
    assert verdict.verdict.value == "ok" and verdict.is_actionable


def test_a_stale_book_with_default_limits_is_still_refused_as_stale():
    verdict = _verify(SELL, VerifyParams(PENNY))
    assert verdict.reasons == ("book snapshot is 10.0s stale",)


# ---- a finite age limit that is not a whole int (float, Decimal, bool) is refused ----
# On a FRESH fixture (inside the limit's value) only the whole-int rule refuses it.

def _say(verdict):
    return (verdict.verdict.value, verdict.is_actionable, verdict.net_edge_state,
            verdict.borrow_state, verdict.reasons)


# a 1 s old book is inside 5 s; True reads as 1 ns, so its book is 0 s old
@pytest.mark.parametrize("limit, age", [(5e9, 10**9), (Decimal("5000000000"), 10**9), (True, 0)])
def test_finite_non_int_staleness_limit_on_a_fresh_book_is_refused(limit, age):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=limit), book=_book(age))
    assert verdict.reasons == (AGE_LIMIT_REASON,) and not verdict.is_actionable, _say(verdict)


# 1 day old metrics are inside 7.5 days, inside True (== 1) and inside Decimal 7
@pytest.mark.parametrize("limit", [7.5, True, Decimal("7")])
def test_finite_non_int_metrics_age_limit_on_fresh_metrics_is_refused(limit):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH,
                                         max_metrics_age_days=limit))
    assert verdict.reasons == (AGE_LIMIT_REASON,) and not verdict.is_actionable, _say(verdict)


@pytest.mark.parametrize("limit", [float(DAY), Decimal(DAY)])
def test_finite_non_int_edge_age_limit_on_a_fresh_edge_is_refused(limit):
    verdict = _verify(BUY, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH, max_edge_age_ns=limit),
                      expected_edge=_edge(0))
    assert verdict.reasons == (AGE_LIMIT_REASON,) and not verdict.is_actionable, _say(verdict)


@pytest.mark.parametrize("limit", [float(DAY), Decimal(DAY)])
def test_finite_non_int_borrow_age_limit_on_a_fresh_borrow_is_refused(limit):
    verdict = _verify(SELL, VerifyParams(PENNY, max_staleness_ns=FRESH_ENOUGH, max_borrow_age_ns=limit),
                      position_effect=PositionEffect.OPEN_SHORT, expected_edge=_edge(0),
                      borrow_quote=_borrow(0))
    assert verdict.reasons == (AGE_LIMIT_REASON,) and not verdict.is_actionable, _say(verdict)
