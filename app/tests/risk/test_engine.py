"""WP4 G4 — risk engine: clean approve, every hard check blocks, max-sizing -> human,
and the property test (no input yields an approved decision with a failed hard check)."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from risk.engine import RiskContext, evaluate

D = Decimal


def clean_ctx(**over) -> RiskContext:
    base = dict(
        max_notional=D("10000"), max_positions=10, concentration_pct=D("0.5"),
        daily_loss_limit=D("1000"), quote_staleness_s=30.0, max_spread_pct=D("0.02"),
        pending_exposure_cap=D("50000"), cash=D("50000"), buying_power=D("50000"),
        holdings={"AAPL": {"market_value": D("5000"), "qty": D("25")}}, open_positions=1,
        realized_loss_today=D("0"), pending_notional=D("0"), watchlist={"MSFT"},
        quote_price=D("190"), quote_age_s=2.0, quote_spread_pct=D("0.001"), mode="paper",
    )
    base.update(over)
    return RiskContext(**base)


def clean_proposal(**over) -> dict:
    p = dict(proposal_id="p1", symbol="AAPL", side="buy", asset_type="equity",
             order_type="limit", time_horizon="short", thesis="t", invalidation="i",
             sizing_basis="thesis", source_refs=[], notional="1000")
    p.update(over)
    return p


def test_clean_proposal_approves():
    d = evaluate(clean_proposal(), clean_ctx())
    assert d["decision"] == "approved" and d["failed_checks"] == []


@pytest.mark.parametrize("check,prop,ctx", [
    ("symbol_allowlist", {"symbol": "NVDA"}, {}),
    ("quote_freshness", {}, {"quote_age_s": 60.0}),
    ("spread", {}, {"quote_spread_pct": D("0.10")}),
    ("max_notional", {"notional": "20000"}, {}),
    ("buying_power", {"notional": "1000"}, {"buying_power": D("100"), "max_notional": D("100000")}),
    ("daily_loss_limit", {}, {"realized_loss_today": D("2000")}),
    ("pending_exposure", {}, {"pending_notional": D("49500")}),
    ("mode_allows_order", {}, {"mode": "read_only"}),
    ("max_positions", {"symbol": "MSFT"}, {"open_positions": 10}),
    ("reg_t_minimum", {"notional": "10"},
     {"cash": D("100"), "holdings": {}, "buying_power": D("100000"), "concentration_pct": D("1")}),
    ("concentration", {"notional": "1000"},
     {"concentration_pct": D("0.01"), "max_notional": D("100000"), "buying_power": D("100000")}),
])
def test_hard_check_blocks(check, prop, ctx):
    d = evaluate(clean_proposal(**prop), clean_ctx(**ctx))
    assert d["decision"] == "rejected"
    assert check in d["failed_checks"]


def test_max_sizing_forces_human_review():
    d = evaluate(clean_proposal(sizing_basis="max"), clean_ctx())
    assert d["decision"] == "needs_human" and d["human_required"] is True
    assert "sizing_not_max" in d["warnings"]


def _rand_ctx(rng: random.Random) -> RiskContext:
    return RiskContext(
        max_notional=D(rng.randrange(0, 20000)), max_positions=rng.randrange(0, 12),
        concentration_pct=D(str(round(rng.random(), 2))), daily_loss_limit=D(rng.randrange(0, 2000)),
        quote_staleness_s=float(rng.randrange(0, 60)), max_spread_pct=D(str(round(rng.random(), 3))),
        pending_exposure_cap=D(rng.randrange(0, 60000)), cash=D(rng.randrange(0, 60000)),
        buying_power=D(rng.randrange(0, 60000)),
        holdings={"AAPL": {"market_value": D(rng.randrange(0, 10000))}} if rng.random() < 0.7 else {},
        open_positions=rng.randrange(0, 12), realized_loss_today=D(rng.randrange(0, 3000)),
        pending_notional=D(rng.randrange(0, 60000)),
        watchlist={"MSFT", "AAPL"} if rng.random() < 0.5 else set(),
        quote_price=D(rng.randrange(0, 500)), quote_age_s=float(rng.randrange(0, 120)),
        quote_spread_pct=D(str(round(rng.random(), 3))),
        mode=rng.choice(["read_only", "paper", "review_live", "auto_veto", "auto_full"]),
    )


def test_negative_notional_rejected():
    d = evaluate(clean_proposal(notional="-1000000"), clean_ctx())
    assert d["decision"] == "rejected" and "notional_positive" in d["failed_checks"]


def test_zero_or_missing_size_rejected():
    p = clean_proposal()
    del p["notional"]                                   # no notional, no quantity -> size 0
    d = evaluate(p, clean_ctx())
    assert d["decision"] == "rejected" and "notional_positive" in d["failed_checks"]


def test_zero_quote_price_rejected():
    p = clean_proposal(quantity=100)
    del p["notional"]                                   # quantity-driven, price 0 -> notional 0
    d = evaluate(p, clean_ctx(quote_price=D("0")))
    assert d["decision"] == "rejected"
    assert "quote_sane" in d["failed_checks"] or "notional_positive" in d["failed_checks"]


def test_naked_short_rejected():
    # MSFT is only on the watchlist, never held -> a sell is a naked short
    d = evaluate(clean_proposal(symbol="MSFT", side="sell", notional="1000"), clean_ctx())
    assert d["decision"] == "rejected" and "sell_backed" in d["failed_checks"]


def test_oversized_sell_rejected():
    d = evaluate(clean_proposal(symbol="AAPL", side="sell", notional="9000"), clean_ctx())  # held mv 5000
    assert d["decision"] == "rejected" and "sell_backed" in d["failed_checks"]


def test_backed_sell_approves():
    d = evaluate(clean_proposal(symbol="AAPL", side="sell", quantity=15, notional="3000"), clean_ctx())
    assert d["decision"] == "approved"


def test_divergent_size_gates_on_larger():
    # notional says $1 but quantity*price says ~$19M -> gate must size on the larger
    d = evaluate(clean_proposal(notional="1", quantity=100000), clean_ctx(quote_price=D("190")))
    assert d["decision"] == "rejected" and "max_notional" in d["failed_checks"]


def test_option_asset_rejected():
    d = evaluate(clean_proposal(asset_type="option"), clean_ctx())
    assert d["decision"] == "rejected" and "asset_type_supported" in d["failed_checks"]


def test_market_order_needs_human():
    d = evaluate(clean_proposal(order_type="market"), clean_ctx())
    assert d["decision"] == "needs_human" and "priced_not_market" in d["warnings"]


def test_matched_rule_only_on_approval():
    rule = {"id": "R1", "predicate": {"side": "buy"}}
    d = evaluate(clean_proposal(), clean_ctx(quote_age_s=999.0), rules=[rule])   # stale -> rejected
    assert d["decision"] == "rejected" and d["matched_rule"] is None


def test_rule_empty_scope_matches_nothing():
    rule = {"id": "R2", "symbols_scope": [], "predicate": {"side": "buy"}}
    d = evaluate(clean_proposal(), clean_ctx(), rules=[rule])
    assert d["decision"] == "approved" and d["matched_rule"] is None


def _rand_proposal(rng: random.Random) -> dict:
    p = dict(proposal_id="p", symbol=rng.choice(["AAPL", "MSFT", "NVDA"]),
             side=rng.choice(["buy", "sell"]),
             asset_type=rng.choice(["equity", "option", "crypto"]),
             order_type=rng.choice(["limit", "market", "stop", "bogus"]),
             time_horizon="short", thesis="t", invalidation="i",
             sizing_basis=rng.choice(["thesis", "max", "half"]), source_refs=[])
    kind = rng.choice(["notional", "quantity", "both", "neither", "negative"])
    if kind in ("notional", "both"):
        p["notional"] = str(rng.randrange(0, 30000))
    if kind in ("quantity", "both"):
        p["quantity"] = rng.randrange(0, 2000)
    if kind == "negative":
        p["notional"] = str(-rng.randrange(1, 100000))
    return p


def test_property_no_approval_with_failed_hard():
    rng = random.Random(20260706)
    rules = [{"id": "R", "predicate": {"side": "buy"}, "worst_case_notional": "5000"}]
    for _ in range(5000):
        d = evaluate(_rand_proposal(rng), _rand_ctx(rng), rules=rng.choice([None, rules]))
        if d["decision"] == "approved":
            assert d["failed_checks"] == []
            assert all(c["passed"] for c in d["checks"] if c["hard"])
        # SEC-19: a rule is only ever advertised on an approved decision
        assert d["matched_rule"] is None or d["decision"] == "approved"


# ---- BUG-066: a poisoned holding must fail closed, never crash the gate ----------------
def test_null_market_value_in_one_holding_fails_closed_instead_of_crashing():
    """BUG-066: a broker snapshot row like {"symbol": "TSLA", "market_value": null} made
    the portfolio sum NaN, and the first NaN comparison raised decimal.InvalidOperation out
    of evaluate() — an unhandled crash on the intraday loop instead of the documented
    'risk fails conservatively' posture."""
    ctx = clean_ctx(holdings={"AAPL": {"market_value": D("5000")},
                              "TSLA": {"market_value": Decimal("NaN")}})
    d = evaluate(clean_proposal(), ctx)                 # must not raise
    assert d["decision"] in ("rejected", "needs_human")
    assert "reg_t_minimum" in d["failed_checks"], (
        "unverifiable equity must read as NO equity (fail closed), not as a crash")


def test_nan_held_value_cannot_back_a_sell():
    ctx = clean_ctx(holdings={"AAPL": {"market_value": Decimal("NaN")},
                              "MSFT": {"market_value": D("100000")}})
    d = evaluate(clean_proposal(side="sell", notional="1000"), ctx)   # must not raise
    assert "sell_backed" in d["failed_checks"], (
        "a holding whose value cannot be verified must not back a sell")


def test_garbage_realized_loss_fails_the_daily_loss_check():
    ctx = clean_ctx(realized_loss_today=Decimal("NaN"))
    d = evaluate(clean_proposal(), ctx)                 # must not raise
    assert "daily_loss_limit" in d["failed_checks"]
