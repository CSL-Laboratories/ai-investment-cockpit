from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

from sidecar.llm_audit import JudgmentLog, anonymize_text, score_judgments
from sidecar.tca import fit_impact, implementation_shortfall


def test_buy_shortfall_splits_into_delay_and_execution():
    row = implementation_shortfall(
        {
            "symbol": "SPY",
            "side": "buy",
            "qty": "100",
            "decision_price": "100",
            "arrival_mid": "100.5",
            "fill_price": "101",
            "fee": "0",
        }
    )
    assert Decimal(row["delay_cost"]) == Decimal("50")
    assert Decimal(row["execution_cost"]) == Decimal("50")
    assert Decimal(row["implementation_shortfall"]) == Decimal("100")
    assert Decimal(row["implementation_shortfall_bps"]) == Decimal("100")


def test_sell_below_the_decision_price_is_a_cost():
    row = implementation_shortfall(
        {
            "side": "sell",
            "qty": "10",
            "decision_price": "100",
            "arrival_mid": "99.5",
            "fill_price": "99",
            "fee": "0",
        }
    )
    assert Decimal(row["implementation_shortfall"]) == Decimal("10")


def test_impact_fit_recovers_a_known_linear_specification():
    sigma = 0.02
    epsilon, eta, gamma = 0.15, 0.40, 0.25
    fills = []
    for index, part in enumerate((0.01, 0.02, 0.04, 0.08, 0.12, 0.16, 0.2, 0.25, 0.3, 0.4)):
        fraction = sigma * (epsilon + eta * math.sqrt(part) + gamma * part)
        arrival = Decimal("100")
        fill = arrival * (Decimal(1) + Decimal(str(fraction)))
        fills.append(
            {
                "side": "buy",
                "qty": str(part * 1_000_000),
                "decision_price": "100",
                "arrival_mid": "100",
                "fill_price": format(fill, "f"),
                "fee": "0",
                "sigma": str(sigma),
                "adv": "1000000",
            }
        )
    fit = fit_impact(fills)
    assert fit["status"] == "fit"
    assert float(fit["epsilon"]) == pytest_approx(epsilon)
    assert float(fit["temporary_eta"]) == pytest_approx(eta)
    assert float(fit["permanent_gamma"]) == pytest_approx(gamma)
    assert fit["recommended"] is not None
    assert fit["does_not_replace_fee_schedule"] is True


def test_impact_refuses_a_short_sample():
    fit = fit_impact(
        [
            {
                "side": "buy",
                "qty": "10",
                "decision_price": "10",
                "arrival_mid": "10",
                "fill_price": "10.1",
                "sigma": "0.01",
                "adv": "1000",
            }
        ]
    )
    assert fit["status"] == "insufficient_sample"
    assert fit["recommended"] is None


def pytest_approx(value: float):
    import pytest

    return pytest.approx(value, rel=1e-6, abs=1e-6)


def test_anonymize_is_stable_and_hides_the_ticker():
    masked = anonymize_text("Buy AAPL not APPL", ["AAPL"])
    assert "AAPL" not in masked
    assert masked == anonymize_text("Buy AAPL not APPL", ["AAPL"])
    assert "APPL" in masked


def test_scorecard_excludes_lookahead_and_beats_a_null_only_descriptively(tmp_path):
    log = JudgmentLog(tmp_path / "judgments.jsonl")
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    good = {
        "judgment_id": "g",
        "route": "claude",
        "as_of": "2026-01-02T00:00:00+00:00",
        "symbol": "SPY",
        "horizon": "5d",
        "probability": 0.9,
        "model_id": "claude-test",
        "model_training_cutoff": "2024-01-01",
        "outcome_up": 1,
        "outcome_return": 0.02,
        "outcome_at": "2026-01-10T00:00:00+00:00",
        "entity_anonymized": True,
    }
    leaked = dict(good, judgment_id="leaked", outcome_at="2025-12-01T00:00:00+00:00")
    pretrained = dict(
        good,
        judgment_id="pretrained",
        as_of="2023-01-01T00:00:00+00:00",
        outcome_at="2023-02-01T00:00:00+00:00",
    )
    bad = dict(good, judgment_id="bad", probability=0.1, outcome_up=0, outcome_return=-0.03)
    unresolved = dict(good, judgment_id="open")
    unresolved.pop("outcome_up")
    unresolved.pop("outcome_at")
    for row in (good, leaked, pretrained, bad, unresolved):
        log.append(row, recorded_at=now)
    card = score_judgments(log.entries())
    assert card["n_scored"] == 2
    assert card["excluded"]["outcome_not_after_as_of"] == 1
    assert card["excluded"]["as_of_not_after_training_cutoff"] == 1
    assert card["excluded"]["unresolved"] == 1
    route = card["routes"][0]
    assert float(route["brier"]) == pytest_approx(0.01)
    assert route["beats_null"] is True
    assert card["yield_claim"] is False
    assert card["threshold_status"] == "not_frozen"
    from sidecar.llm_audit.score import spearman

    assert spearman([1, 2, 3], [3, 2, 1]) == -1
