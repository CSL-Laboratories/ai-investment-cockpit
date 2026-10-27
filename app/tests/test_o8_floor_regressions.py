"""Focused runtime evidence for O8's retained post-PDT risk floor.

These tests exercise current functions; they are not qualification or live gates.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from decimal import Decimal

import pytest

from risk.engine import RiskContext, evaluate
from tests.risk.test_engine import clean_ctx, clean_proposal


@pytest.mark.parametrize("equity,approved", [
    ("1999.99", False), ("2000", True), ("2000.01", True),
])
@pytest.mark.parametrize("held_value", ["0", "500"])
def test_post_pdt_reg_t_exact_equity_boundary(equity, approved, held_value):
    """Cash plus holdings must meet $2,000; buying power cannot replace equity."""
    ctx = clean_ctx(
        cash=Decimal(equity) - Decimal(held_value),
        holdings={"AAPL": {"market_value": Decimal(held_value), "qty": Decimal("1")}},
        buying_power=Decimal("100000"),
        concentration_pct=Decimal("1"),
    )
    result = evaluate(clean_proposal(notional="10"), ctx)
    check = next(item for item in result["checks"] if item["name"] == "reg_t_minimum")
    assert check["hard"] is True
    assert check["passed"] is approved
    assert (result["decision"] == "approved") is approved
    if not approved:
        assert result["failed_checks"] == ["reg_t_minimum"]
        assert Decimal(result["max_allowed_notional"]) == 0


def test_post_pdt_has_no_day_trade_counter_gate():
    """Repeated eligible decisions below $25,000 do not acquire a PDT/count gate."""
    ctx = clean_ctx(
        cash=Decimal("2000"), holdings={}, watchlist={"AAPL"},
        buying_power=Decimal("2000"), concentration_pct=Decimal("1"),
    )
    proposal = clean_proposal(notional="10")
    assert not {"day_trade_count", "daytrade_count", "pattern_day_trader"} & {
        field.name for field in fields(RiskContext)
    }
    for _ in range(10):
        result = evaluate(proposal, ctx)
        assert result["decision"] == "approved"
        assert not any("pdt" in item["name"] or "day_trade" in item["name"]
                       or "daytrade" in item["name"] for item in result["checks"])


def test_risk_evaluation_is_repeatable_without_mutating_inputs():
    """Repeated identical inputs yield identical decisions and retain caller data."""
    proposal = clean_proposal()
    ctx = clean_ctx()
    before = deepcopy((proposal, ctx))
    first = evaluate(proposal, ctx)
    assert first["decision"] == "approved"
    for _ in range(5):
        assert evaluate(proposal, ctx) == first
        assert (proposal, ctx) == before
