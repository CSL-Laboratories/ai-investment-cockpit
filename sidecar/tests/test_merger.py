from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from sidecar.merger.artifact import expected_return, fit_calibration, score_deal, wilson_lower_bound
from sidecar.merger.dataset import build_observations
from sidecar.merger.edgar import Filing, parse_submissions


def _card(deal_id: str, consideration: str, outcome: str, observed: str, resolved: str) -> dict:
    return {
        "deal_id": deal_id,
        "symbol": "AAA",
        "target_cik": "0000000123",
        "consideration": consideration,
        "hostile": False,
        "regulatory_review": deal_id.endswith("0"),
        "offer_price": "40",
        "market_price": "38",
        "announced_at": observed,
        "observed_at": observed,
        "outside_date": "2024-12-31",
        "outcome": outcome,
        "outcome_at": resolved,
        "outcome_price": "40" if outcome == "completed" else "22",
        "synthetic": True,
    }


def _cards() -> list[dict]:
    rows = []
    for index in range(16):
        rows.append(
            _card(
                f"train-cash-{index}",
                "cash",
                "completed" if index < 14 else "broken",
                "2020-03-01T00:00:00+00:00",
                "2020-06-01T00:00:00+00:00",
            )
        )
    for index in range(12):
        rows.append(
            _card(
                f"train-stock-{index}",
                "stock",
                "broken" if index < 10 else "completed",
                "2020-04-01T00:00:00+00:00",
                "2020-07-01T00:00:00+00:00",
            )
        )
    for index in range(6):
        rows.append(
            _card(
                f"test-cash-{index}",
                "cash",
                "completed",
                "2022-02-01T00:00:00+00:00",
                "2022-05-01T00:00:00+00:00",
            )
        )
    rows.append(
        _card(
            "censored",
            "cash",
            "completed",
            "2020-05-01T00:00:00+00:00",
            "2022-08-01T00:00:00+00:00",
        )
    )
    return rows


def test_point_in_time_filings_ignore_the_future():
    early = Filing(
        "0000000123",
        "S-4",
        "0000000123-20-000001",
        date(2020, 2, 1),
        datetime(2020, 2, 1, 15, tzinfo=timezone.utc),
    )
    late = Filing(
        "0000000123",
        "DEFM14A",
        "0000000123-20-000099",
        date(2020, 5, 1),
        datetime(2020, 5, 1, tzinfo=timezone.utc),
    )
    rows = build_observations(_cards(), [early, late])
    train = next(row for row in rows if row["deal_id"] == "train-cash-0")
    assert train["pit_filing_accessions"] == ["0000000123-20-000001"]
    assert "0000000123-20-000099" not in train["pit_filing_accessions"]


def test_outcome_cannot_precede_observation():
    card = _cards()[0]
    card["outcome_at"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(ValueError):
        build_observations([card], [])


def test_logistic_artifact_is_labeled_and_not_the_cockpit_document():
    rows = build_observations(_cards(), [])
    artifact = fit_calibration(rows, training_cutoff="2021-01-01", allow_synthetic=True)
    assert artifact["frozen"] is True
    assert artifact["synthetic"] is True
    assert artifact["yield_claim"] is False
    assert artifact["retrofit"]["compatible_without_amendment"] is False
    names = artifact["feature_names"]
    cash = float(artifact["coefficients"][names.index("cash")])
    assert cash > 0
    assert artifact["n_censored"] >= 1
    assert artifact["metrics"]["test_brier"] is not None
    scored = score_deal(artifact, rows[0])
    assert Decimal(scored["expected_return"]) == expected_return(
        Decimal(scored["p_complete"]),
        Decimal(rows[0]["spread"]),
        Decimal(artifact["break_loss"]["magnitude"]),
    )
    with pytest.raises(ValueError):
        fit_calibration(rows, training_cutoff="2021-01-01", allow_synthetic=False)


def test_wilson_lower_bound_matches_the_pinned_arithmetic():
    successes = 80
    n = 100
    z = Decimal("1.96")
    p_hat = Decimal(successes) / Decimal(n)
    z2 = z * z
    count = Decimal(n)
    manual = (
        p_hat
        + z2 / (Decimal(2) * count)
        - z * ((p_hat * (1 - p_hat) + z2 / (Decimal(4) * count)) / count).sqrt()
    ) / (1 + z2 / count)
    assert wilson_lower_bound(successes, n) == manual


def test_submissions_parser_and_date_only_visibility():
    payload = {
        "cik": "123",
        "filings": {
            "recent": {
                "form": ["8-K", "S-4"],
                "accessionNumber": ["0000000123-20-000001", "0000000123-20-000002"],
                "filingDate": ["2020-01-02", "2020-01-03"],
                "acceptanceDateTime": ["", "2020-01-03T14:00:00-05:00"],
            }
        },
    }
    filings = parse_submissions(payload)
    assert filings[0].cik == "0000000123"
    as_of = datetime(2020, 1, 2, 23, tzinfo=timezone.utc)
    assert filings[0].visible_at(as_of) is False
    assert filings[0].visible_at(datetime(2020, 1, 3, tzinfo=timezone.utc)) is True
    assert filings[1].visible_at(datetime(2020, 1, 3, 18, tzinfo=timezone.utc)) is False
    assert filings[1].visible_at(datetime(2020, 1, 3, 20, tzinfo=timezone.utc)) is True
