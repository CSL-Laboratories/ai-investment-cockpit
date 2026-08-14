"""W-21 failure proofs for the ADV/volatility transaction-cost model.

These tests deliberately exercise the replacement contract rather than preserving QT-05's
displayed-depth semantics.  The live verifier prices an opening position over its full holding
period; the contained simulator keeps its FIFO/depth model separately.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.costs import ZERO_FEES, holding_seconds
from quant.events import PriceScale
from quant.verify import (
    BorrowQuote,
    EdgeEstimate,
    MarketImpactInputs,
    PositionEffect,
    VerifyParams,
    Verdict,
    verify_proposal,
)


PENNY = PriceScale(tick_size=Decimal("0.01"))
NOW_NS = 1_800_000_000_000_000_000
PARAMS = VerifyParams(scale=PENNY)
FREE = VerifyParams(scale=PENNY, schedule=ZERO_FEES)
METRICS = MarketImpactInputs(
    symbol="S",
    as_of=date(2026, 8, 12),
    median_daily_dollar_volume=Decimal("1000000"),
    annualized_realized_volatility=Decimal("0.31749015732775088"),
    observations=60,
    age_days=1,
)


def book(*, bid=10_000, ask=10_002, bid_size=1, ask_size=1, seq=10,
         ts=NOW_NS) -> BookSnapshot:
    return BookSnapshot(
        symbol="S", seq=seq, ts=ts,
        bids=(LevelSnapshot(price_ticks=bid, qty=bid_size, order_count=1),),
        asks=(LevelSnapshot(price_ticks=ask, qty=ask_size, order_count=1),),
    )


def proposal(**kwargs) -> dict:
    out = {
        "symbol": "S", "side": "buy", "quantity": 100,
        "order_type": "limit", "time_horizon": "short",
    }
    out.update(kwargs)
    return out


def edge(gross_bps: Decimal, *, verified=True, horizon="short") -> EdgeEstimate:
    return EdgeEstimate(
        expected_gross_return_bps=gross_bps,
        time_horizon=horizon,
        source_id="deterministic-test-strategy",
        evidence_ref="strategy://fixture/v1",
        computed_at_ns=NOW_NS - 1_000_000_000,
        verified=verified,
    )


def test_horizon_mapping_is_calendar_time_in_one_home():
    assert holding_seconds("intraday") == 86_400
    assert holding_seconds("short") == 7 * 86_400
    assert holding_seconds("long") == 30 * 86_400
    with pytest.raises(ValueError, match="unknown time_horizon"):
        holding_seconds("someday")


def test_displayed_sizes_no_longer_change_live_cost_or_participation():
    thin = verify_proposal(
        proposal(), book(bid_size=1, ask_size=1), PARAMS,
        metrics=METRICS, now_ns=NOW_NS,
    )
    deep = verify_proposal(
        proposal(), book(bid_size=9_000_000, ask_size=9_000_000), PARAMS,
        metrics=METRICS, now_ns=NOW_NS,
    )

    assert thin.expected_cost_bps == deep.expected_cost_bps
    assert thin.adv_participation_rate == deep.adv_participation_rate
    assert thin.adv_participation_rate == Decimal("0.010001")


def test_opening_cost_is_round_trip_spread_plus_impact_plus_fees():
    verdict = verify_proposal(
        proposal(), book(), PARAMS, metrics=METRICS, now_ns=NOW_NS,
    )

    assert verdict.expected_spread_bps > 0
    assert verdict.expected_impact_bps > 0
    assert verdict.expected_fee_bps > 0
    assert verdict.expected_borrow_bps == 0
    assert verdict.borrow_state == "not_applicable"
    assert verdict.expected_cost_bps == (
        verdict.expected_spread_bps
        + verdict.expected_impact_bps
        + verdict.expected_fee_bps
        + verdict.expected_borrow_bps
    )


def test_impact_increases_with_size_and_falls_with_adv():
    small = verify_proposal(
        proposal(quantity=10), book(), FREE, metrics=METRICS, now_ns=NOW_NS,
    )
    large = verify_proposal(
        proposal(quantity=1_000), book(), FREE, metrics=METRICS, now_ns=NOW_NS,
    )
    liquid = verify_proposal(
        proposal(quantity=1_000), book(), FREE,
        metrics=MarketImpactInputs(
            symbol="S", as_of=METRICS.as_of,
            median_daily_dollar_volume=Decimal("100000000"),
            annualized_realized_volatility=METRICS.annualized_realized_volatility,
            observations=60, age_days=1,
        ),
        now_ns=NOW_NS,
    )

    assert large.expected_impact_bps > small.expected_impact_bps
    assert liquid.expected_impact_bps < large.expected_impact_bps


def test_adv_limit_is_a_hard_model_validity_boundary():
    verdict = verify_proposal(
        proposal(quantity=1_001), book(), PARAMS,
        metrics=MarketImpactInputs(
            symbol="S", as_of=METRICS.as_of,
            median_daily_dollar_volume=Decimal("1000000"),
            annualized_realized_volatility=METRICS.annualized_realized_volatility,
            observations=60, age_days=1,
        ),
        now_ns=NOW_NS,
    )

    assert verdict.adv_participation_rate > Decimal("0.10")
    assert verdict.verdict is Verdict.UNECONOMIC
    assert any("ADV" in reason for reason in verdict.reasons)


def test_missing_or_stale_metrics_fail_closed_without_zero_cost_assumption():
    missing = verify_proposal(proposal(), book(), PARAMS, metrics=None, now_ns=NOW_NS)
    stale = verify_proposal(
        proposal(), book(), PARAMS,
        metrics=MarketImpactInputs(
            symbol="S", as_of=date(2026, 7, 1),
            median_daily_dollar_volume=Decimal("1000000"),
            annualized_realized_volatility=Decimal("0.2"),
            observations=60, age_days=43,
        ),
        now_ns=NOW_NS,
    )

    assert missing.verdict is Verdict.INSUFFICIENT_DATA
    assert stale.verdict is Verdict.INSUFFICIENT_DATA
    assert any("metrics" in reason.lower() for reason in missing.reasons)
    assert any("stale" in reason.lower() for reason in stale.reasons)


@pytest.mark.parametrize(
    "metrics",
    [
        replace(METRICS, symbol="OTHER"),
        replace(METRICS, observations=2),
        replace(METRICS, median_daily_dollar_volume=Decimal("NaN")),
        replace(METRICS, annualized_realized_volatility=Decimal("0")),
        replace(METRICS, age_days=-1),
    ],
)
def test_malformed_rw14_metrics_fail_closed(metrics):
    verdict = verify_proposal(
        proposal(), book(), PARAMS, metrics=metrics, now_ns=NOW_NS,
    )
    assert verdict.verdict is Verdict.INSUFFICIENT_DATA


def test_model_authored_edge_is_ignored_and_trusted_edge_is_explicitly_unavailable():
    planted = proposal(quantity=1, expected_return_bps="999999")
    verdict = verify_proposal(
        planted, book(), PARAMS, metrics=METRICS, now_ns=NOW_NS,
    )

    assert verdict.net_edge_state == "unavailable"
    assert verdict.expected_gross_return_bps is None
    assert verdict.expected_net_return_bps is None
    assert verdict.first_negative_qty is None


def test_relative_rule_uses_net_return_and_passes_exactly_at_two_times_cost():
    baseline = verify_proposal(
        proposal(), book(), PARAMS, metrics=METRICS, now_ns=NOW_NS,
    )
    exact_gross = baseline.expected_cost_bps * Decimal("3")

    exact = verify_proposal(
        proposal(), book(), PARAMS, metrics=METRICS,
        expected_edge=edge(exact_gross), now_ns=NOW_NS,
    )
    below = verify_proposal(
        proposal(), book(), PARAMS, metrics=METRICS,
        expected_edge=edge(exact_gross - Decimal("0.01")), now_ns=NOW_NS,
    )

    assert exact.expected_net_return_bps == exact.expected_cost_bps * Decimal("2")
    assert exact.net_edge_state == "sufficient"
    assert below.net_edge_state == "insufficient"


@pytest.mark.parametrize(
    "estimate",
    [
        replace(edge(Decimal("100")), verified=False),
        replace(edge(Decimal("100")), schema_version="edge_estimate.v2"),
        replace(edge(Decimal("100")), time_horizon="long"),
        replace(edge(Decimal("100")), computed_at_ns=NOW_NS - 86_400_000_000_001),
        replace(edge(Decimal("100")), computed_at_ns=NOW_NS + 1),
        replace(edge(Decimal("100")), expected_gross_return_bps=Decimal("10000.01")),
    ],
)
def test_unverified_stale_or_out_of_contract_edge_is_unavailable(estimate):
    verdict = verify_proposal(
        proposal(), book(), PARAMS, metrics=METRICS,
        expected_edge=estimate, now_ns=NOW_NS,
    )
    assert verdict.net_edge_state == "unavailable"
    assert verdict.expected_gross_return_bps is None


def test_short_borrow_is_required_but_a_long_close_never_borrows():
    missing = verify_proposal(
        proposal(side="sell"), book(), PARAMS, metrics=METRICS,
        position_effect=PositionEffect.OPEN_SHORT,
        expected_edge=edge(Decimal("500")), now_ns=NOW_NS,
    )
    close = verify_proposal(
        proposal(side="sell"), book(), PARAMS, metrics=METRICS,
        position_effect=PositionEffect.CLOSE_LONG, now_ns=NOW_NS,
    )

    assert missing.verdict is Verdict.INSUFFICIENT_DATA
    assert missing.borrow_state == "unavailable"
    assert close.borrow_state == "not_applicable"
    assert close.expected_borrow_bps == 0
    assert close.net_edge_state == "not_applicable"


def test_verified_short_borrow_uses_the_horizon_and_is_in_all_in_cost():
    quote = BorrowQuote(
        annual_rate=Decimal("0.30"), locate_fee=Decimal("2.50"),
        source_id="verified-fixture", computed_at_ns=NOW_NS - 1_000_000_000,
        verified=True,
    )
    verdict = verify_proposal(
        proposal(side="sell", time_horizon="short"), book(), PARAMS,
        metrics=METRICS, position_effect=PositionEffect.OPEN_SHORT,
        expected_edge=edge(Decimal("500")), borrow_quote=quote, now_ns=NOW_NS,
    )

    assert verdict.borrow_state == "verified"
    assert verdict.expected_borrow_bps > Decimal("50")
    assert verdict.expected_cost_bps >= verdict.expected_borrow_bps


@pytest.mark.parametrize(
    "quote",
    [
        BorrowQuote(Decimal("0.30"), Decimal("2.50"), "fixture", NOW_NS, False),
        BorrowQuote(
            Decimal("0.30"), Decimal("2.50"), "fixture", NOW_NS, True,
            schema_version="borrow_quote.v2",
        ),
        BorrowQuote(Decimal("0.30"), Decimal("2.50"), "fixture", NOW_NS + 1, True),
        BorrowQuote(
            Decimal("0.30"), Decimal("2.50"), "fixture",
            NOW_NS - 86_400_000_000_001, True,
        ),
        BorrowQuote(Decimal("5.01"), Decimal("2.50"), "fixture", NOW_NS, True),
        BorrowQuote(Decimal("0.30"), Decimal("-0.01"), "fixture", NOW_NS, True),
    ],
)
def test_unverified_stale_or_out_of_contract_borrow_fails_closed(quote):
    verdict = verify_proposal(
        proposal(side="sell"), book(), PARAMS, metrics=METRICS,
        position_effect=PositionEffect.OPEN_SHORT,
        expected_edge=edge(Decimal("500")), borrow_quote=quote, now_ns=NOW_NS,
    )
    assert verdict.verdict is Verdict.INSUFFICIENT_DATA
    assert verdict.borrow_state == "unavailable"


@pytest.mark.parametrize(
    "params",
    [
        replace(PARAMS, impact_coefficient_y=Decimal("0.9")),
        replace(PARAMS, max_adv_participation=Decimal("0.11")),
        replace(PARAMS, min_net_cost_multiple=Decimal("1.99")),
        replace(PARAMS, sensitivity_low_y=Decimal("0.49")),
    ],
)
def test_gate_parameters_cannot_be_weakened_by_a_caller(params):
    verdict = verify_proposal(
        proposal(), book(), params, metrics=METRICS, now_ns=NOW_NS,
    )
    assert verdict.verdict is Verdict.INSUFFICIENT_DATA


def test_high_absolute_cost_is_uneconomic_even_inside_the_adv_limit():
    volatile = replace(METRICS, annualized_realized_volatility=Decimal("2.0"))
    verdict = verify_proposal(
        proposal(quantity=100), book(), PARAMS, metrics=volatile, now_ns=NOW_NS,
    )
    assert verdict.adv_participation_rate < PARAMS.max_adv_participation
    assert verdict.expected_cost_bps >= PARAMS.uneconomic_cost_bps
    assert verdict.verdict is Verdict.UNECONOMIC


def test_curve_and_first_negative_boundary_are_deterministic_whole_shares():
    baseline = verify_proposal(
        proposal(quantity=1), book(), FREE, metrics=METRICS, now_ns=NOW_NS,
    )
    estimate = edge(baseline.expected_cost_bps + Decimal("20"))
    first = verify_proposal(
        proposal(quantity=1), book(), FREE, metrics=METRICS,
        expected_edge=estimate, now_ns=NOW_NS,
    )
    second = verify_proposal(
        proposal(quantity=1), book(), FREE, metrics=METRICS,
        expected_edge=estimate, now_ns=NOW_NS,
    )

    assert first == second
    assert first.cost_curve
    assert all(point.qty >= 1 for point in first.cost_curve)
    assert first.first_negative_qty is not None
    assert first.first_negative_notional is not None

    at_boundary = verify_proposal(
        proposal(quantity=first.first_negative_qty), book(), FREE, metrics=METRICS,
        expected_edge=estimate, now_ns=NOW_NS,
    )
    assert at_boundary.expected_net_return_bps < 0
    if first.first_negative_qty > 1:
        before = verify_proposal(
            proposal(quantity=first.first_negative_qty - 1), book(), FREE,
            metrics=METRICS, expected_edge=estimate, now_ns=NOW_NS,
        )
        assert before.expected_net_return_bps >= 0


def test_first_negative_boundary_can_be_the_first_whole_share():
    verdict = verify_proposal(
        proposal(quantity=1), book(), FREE, metrics=METRICS,
        expected_edge=edge(Decimal("0")), now_ns=NOW_NS,
    )
    assert verdict.first_negative_qty == 1
    assert verdict.first_negative_notional == Decimal("100.01")


def test_w21_verdict_is_json_native_and_has_no_depth_economics():
    payload = verify_proposal(
        proposal(), book(), PARAMS, metrics=METRICS, now_ns=NOW_NS,
    ).as_dict()

    import json

    assert json.loads(json.dumps(payload)) == payload
    assert "adv_participation_rate" in payload
    assert "expected_impact_bps" in payload
    assert "cost_curve" in payload
    for retired in ("participation_rate", "fillable_now", "queue_ahead_shares", "touch_ticks"):
        assert retired not in payload
