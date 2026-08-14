"""QT-06 — Tier B: the execution verdict as a real pre-trade check inside risk/.

Tier A (``quant/verify.py``) measures what a trade will cost. This is where that measurement
acquires authority: ``execution_cost_sane`` is HARD and blocks, ``execution_quality`` is SOFT
and forces human review.

The properties pinned:

  * AN UNECONOMIC TRADE IS REJECTED, not merely flagged. A trade that cannot pay for itself
    is exactly what a hard check is for.
  * A MARGINAL TRADE GOES TO A HUMAN rather than through or away.
  * THE ENGINE STILL WORKS WITH NO BOOK. A missing verdict does not block, because a system
    with no price feed must not have every order rejected — that would be a hard failure
    dressed as a control.
  * BUT WITH ``require_quant_verdict`` IT FAILS CLOSED: missing or stale degrades to
    needs_human, never to approved (Invariant 7).
  * INVARIANT 1 IS UNTOUCHED: no input yields ``approved`` with a failed hard check. The
    existing 5,000-iteration property test still governs; this file adds the verdict-shaped
    inputs it does not generate.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from risk.engine import RiskContext, evaluate


@dataclass(frozen=True)
class FakeVerdict:
    """Duck-typed stand-in. The engine reads the SHAPE of a verdict, never its class — which
    is what keeps ``risk/`` to a single small surface against the quant package."""

    verdict: str
    expected_cost_bps: Decimal = Decimal("2")
    book_seq: int = 100
    net_edge_state: str = "sufficient"


def _ctx(**kwargs) -> RiskContext:
    base = dict(
        max_notional=Decimal("10000"), max_positions=10, concentration_pct=Decimal("0.25"),
        daily_loss_limit=Decimal("500"), quote_staleness_s=60, max_spread_pct=Decimal("0.05"),
        pending_exposure_cap=Decimal("10000"), cash=Decimal("50000"),
        buying_power=Decimal("50000"), holdings={}, open_positions=0,
        realized_loss_today=Decimal("0"), pending_notional=Decimal("0"),
        watchlist={"AAPL"}, quote_price=Decimal("100"), quote_age_s=1.0,
        quote_spread_pct=Decimal("0.001"), mode="paper",
    )
    base.update(kwargs)
    return RiskContext(**base)


def _proposal(**kwargs) -> dict:
    base = {"symbol": "AAPL", "side": "buy", "asset_type": "equity",
            "order_type": "limit", "quantity": 10, "sizing_basis": "fixed-fraction"}
    base.update(kwargs)
    return base


def _check(result: dict, name: str) -> dict:
    return next(c for c in result["checks"] if c["name"] == name)


# ---- the hard gate ------------------------------------------------------------------------


def test_a_clean_verdict_approves():
    """Baseline. Everything below is a departure from this, so it must genuinely approve."""
    result = evaluate(_proposal(), _ctx(quant_verdict=FakeVerdict("ok")))
    assert result["decision"] == "approved"
    assert _check(result, "execution_cost_sane")["passed"]
    assert _check(result, "execution_quality")["passed"]


def test_an_uneconomic_verdict_REJECTS_the_proposal():
    result = evaluate(_proposal(), _ctx(quant_verdict=FakeVerdict("uneconomic")))
    assert result["decision"] == "rejected"
    assert "execution_cost_sane" in result["failed_checks"]


def test_a_cost_above_the_hard_ceiling_rejects_even_when_the_verdict_reads_ok():
    """Two independent grounds. The verdict's own thresholds are the quant engine's opinion;
    the ceiling is the risk engine's, and the risk engine does not delegate its limits."""
    result = evaluate(
        _proposal(),
        _ctx(quant_verdict=FakeVerdict("ok", expected_cost_bps=Decimal("250")),
             max_execution_cost_bps=Decimal("100")),
    )
    assert result["decision"] == "rejected"
    assert "execution_cost_sane" in result["failed_checks"]


def test_a_cost_at_the_ceiling_is_allowed_through():
    """CONTROL for the ceiling: the comparison is `>`, not `>=`, and an off-by-one here
    would reject every trade priced exactly at the configured limit."""
    result = evaluate(
        _proposal(),
        _ctx(quant_verdict=FakeVerdict("ok", expected_cost_bps=Decimal("100")),
             max_execution_cost_bps=Decimal("100")),
    )
    assert _check(result, "execution_cost_sane")["passed"]


# ---- the soft gate ---------------------------------------------------------------------------


def test_a_marginal_verdict_forces_human_review_rather_than_blocking_or_passing():
    result = evaluate(_proposal(), _ctx(quant_verdict=FakeVerdict("marginal")))
    assert result["decision"] == "needs_human"
    assert result["human_required"] is True
    assert "execution_quality" in result["warnings"]


def test_insufficient_data_forces_human_review():
    """The book could not answer. That is not permission to proceed unexamined."""
    result = evaluate(_proposal(), _ctx(quant_verdict=FakeVerdict("insufficient_data")))
    assert result["decision"] == "needs_human"


def test_an_unrecognised_verdict_value_degrades_to_needs_human():
    """A verdict the engine cannot interpret is treated as no answer, not as approval."""
    result = evaluate(_proposal(), _ctx(quant_verdict=FakeVerdict("looks_great_ship_it")))
    assert result["decision"] == "needs_human"


# ---- working without a book, and failing closed when required -----------------------------------


def test_no_verdict_does_not_block_by_default():
    """A system with no price feed must not have every order rejected — that is a hard
    failure dressed as a control. The cockpit has no book wired today."""
    result = evaluate(_proposal(), _ctx())
    assert result["decision"] == "approved"
    assert _check(result, "execution_cost_sane")["passed"]


def test_requiring_a_verdict_makes_a_missing_one_fail_closed():
    result = evaluate(_proposal(), _ctx(require_quant_verdict=True))
    assert result["decision"] == "needs_human"
    assert "execution_quality" in result["warnings"]


def test_a_stale_verdict_fails_closed_when_verdicts_are_required():
    """The verdict describes a book that no longer exists. Approving on it would be
    approving on a measurement of something else."""
    result = evaluate(
        _proposal(),
        _ctx(quant_verdict=FakeVerdict("ok", book_seq=100), quant_book_seq=140,
             require_quant_verdict=True),
    )
    assert result["decision"] == "needs_human"
    assert "book seq" in _check(result, "execution_quality")["detail"]


def test_a_current_verdict_is_not_treated_as_stale():
    """CONTROL for staleness: same-sequence must pass, or the gate rejects everything."""
    result = evaluate(
        _proposal(),
        _ctx(quant_verdict=FakeVerdict("ok", book_seq=140), quant_book_seq=140,
             require_quant_verdict=True),
    )
    assert result["decision"] == "approved"


# ---- W-21 relative edge is a separate authority ------------------------------------------


def test_unavailable_required_edge_fails_closed_to_no_approval():
    result = evaluate(
        _proposal(),
        _ctx(
            quant_verdict=FakeVerdict("ok", net_edge_state="unavailable"),
            require_quant_verdict=True,
            require_net_edge=True,
        ),
    )
    assert result["decision"] == "needs_human"
    assert "net_edge_available" in result["warnings"]
    assert _check(result, "net_edge_sane")["passed"]


def test_present_edge_below_relative_threshold_is_a_distinct_hard_failure():
    result = evaluate(
        _proposal(),
        _ctx(
            quant_verdict=FakeVerdict("ok", net_edge_state="insufficient"),
            require_quant_verdict=True,
            require_net_edge=True,
        ),
    )
    assert result["decision"] == "rejected"
    assert "net_edge_sane" in result["failed_checks"]
    assert "execution_cost_sane" not in result["failed_checks"]


def test_closing_trade_marks_relative_edge_not_applicable_without_weakening_other_checks():
    result = evaluate(
        _proposal(side="sell", quantity=10),
        _ctx(
            holdings={"AAPL": {"market_value": Decimal("5000")}},
            quant_verdict=FakeVerdict("ok", net_edge_state="not_applicable"),
            require_quant_verdict=True,
            require_net_edge=True,
        ),
    )
    assert result["decision"] == "approved"
    assert _check(result, "net_edge_available")["passed"]


def test_stale_insufficient_edge_cannot_use_stale_data_as_a_hard_fact_or_approve():
    result = evaluate(
        _proposal(),
        _ctx(
            quant_verdict=FakeVerdict("ok", book_seq=1, net_edge_state="insufficient"),
            quant_book_seq=2,
            require_quant_verdict=True,
            require_net_edge=True,
        ),
    )
    assert result["decision"] == "needs_human"
    assert _check(result, "net_edge_sane")["passed"]
    assert "net_edge_available" in result["warnings"]


def test_a_stale_UNECONOMIC_verdict_still_does_not_approve():
    """Staleness must not launder a bad verdict into an approval. The soft path catches it
    even though the hard check stops applying once the verdict is known to be out of date."""
    result = evaluate(
        _proposal(),
        _ctx(quant_verdict=FakeVerdict("uneconomic"), quant_book_seq=999,
             require_quant_verdict=True),
    )
    assert result["decision"] != "approved"


# ---- invariant 1 is untouched -------------------------------------------------------------------


def test_a_hard_failure_elsewhere_still_rejects_regardless_of_a_glowing_verdict():
    """The execution verdict is one check among many and has no power to override another.
    A hijacked or buggy quant layer must not be able to approve an unbacked short."""
    result = evaluate(
        _proposal(side="sell", quantity=100),
        _ctx(quant_verdict=FakeVerdict("ok", expected_cost_bps=Decimal("0"))),
    )
    assert result["decision"] == "rejected"
    assert "sell_backed" in result["failed_checks"]


@pytest.mark.parametrize("state", ["ok", "marginal", "uneconomic", "insufficient_data", "bogus"])
def test_no_verdict_state_can_produce_approved_with_a_failed_hard_check(state):
    """The Invariant-1 property, restricted to the inputs this feature added. The existing
    5,000-iteration sweep in test_engine.py does not generate verdict-shaped contexts."""
    result = evaluate(
        _proposal(symbol="NOTONLIST"),
        _ctx(quant_verdict=FakeVerdict(state), require_quant_verdict=True),
    )
    assert result["failed_checks"]
    assert result["decision"] == "rejected"


def test_matched_rule_is_never_advertised_on_a_verdict_rejection():
    """SEC-19: a rule match is advisory only on an APPROVED decision. A new hard check must
    not open a path where a rejected proposal still carries an auto-approval hint."""
    result = evaluate(
        _proposal(),
        _ctx(quant_verdict=FakeVerdict("uneconomic")),
        rules=[{"id": "r1", "symbols": ["AAPL"], "sides": ["buy"], "max_notional": "100000"}],
    )
    assert result["decision"] == "rejected"
    assert result["matched_rule"] is None
