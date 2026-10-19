from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sidecar.llm_audit.showdown import ShowdownLog, classify, render_weekly, score_showdown


def _when(hour: int = 15) -> datetime:
    return datetime(2026, 6, 1, hour, tzinfo=timezone.utc)


def _decision(**overrides) -> dict:
    row = {
        "judgment_id": "j1",
        "route": "test",
        "as_of": "2026-06-01T15:00:00+00:00",
        "horizon_end": "2026-06-08T15:00:00+00:00",
        "symbol": "NEXA",
        "horizon": "5d",
        "thesis": "earnings_beat",
        "driver": "earnings_beat",
        "driver_class": "catalyst",
        "expected_direction": "up",
        "expected_magnitude": 0.04,
        "confidence": 0.7,
        "model_id": "test",
    }
    row.update(overrides)
    return row


def _outcome(**overrides) -> dict:
    row = {
        "judgment_id": "j1",
        "outcome_at": "2026-06-08T15:00:01+00:00",
        "realized_return": 0.04,
        "market_return": 0.0,
        "beta": 1.3,
        "catalyst_occurred": True,
        "catalyst_already_priced": False,
    }
    row.update(overrides)
    return row


def test_four_cells_and_the_attribution_split():
    right = classify(_decision(), _outcome())
    assert right["cell"] == "right_for_right_reasons"
    assert right["was_why"] is True

    lucky = classify(_decision(), _outcome(catalyst_occurred=False, realized_return=0.02))
    assert lucky["cell"] == "right_for_wrong_reasons"

    carried = classify(
        _decision(),
        _outcome(realized_return=0.05, market_return=0.04, beta=1.3, catalyst_occurred=True),
    )
    assert carried["happened"] is True
    assert carried["was_why"] is False
    assert carried["cell"] == "right_for_wrong_reasons"

    unlucky = classify(_decision(), _outcome(realized_return=-0.03, catalyst_occurred=True))
    assert unlucky["cell"] == "wrong_for_right_reasons"

    wrong = classify(_decision(), _outcome(realized_return=-0.03, catalyst_occurred=False))
    assert wrong["cell"] == "wrong_for_wrong_reasons"

    priced = classify(_decision(), _outcome(catalyst_already_priced=True, realized_return=0.0))
    assert priced["happened"] is False
    assert priced["cell"] == "wrong_for_wrong_reasons"


def test_market_can_be_unlucky_when_the_residual_flips_the_total():
    decision = _decision(driver="macro_print", driver_class="market", expected_direction="up", thesis="macro_print")
    judged = classify(decision, _outcome(realized_return=-0.01, market_return=0.02, beta=1.0))
    assert judged["happened"] is True
    assert judged["was_why"] is False
    assert judged["direction_right"] is False
    assert judged["cell"] == "wrong_for_right_reasons"


def test_process_bit_is_locked_on_the_decision():
    decision = _decision(
        driver="trailing_60d_trend",
        driver_class="process",
        thesis="combined.deterministic_stand_in.v1",
        process_supported=True,
        expected_direction="up",
    )
    judged = classify(decision, _outcome(realized_return=-0.02, process_supported=False))
    assert judged["cell"] == "wrong_for_right_reasons"
    missed = classify(
        _decision(driver_class="process", driver="trailing_60d_trend", process_supported=False),
        _outcome(realized_return=0.02),
    )
    assert missed["cell"] == "right_for_wrong_reasons"


def test_log_refuses_lookahead_and_a_rewritten_thesis(tmp_path):
    log = ShowdownLog(tmp_path / "showdown.jsonl")
    decision_at = _when()
    logged = log.append_decision(_decision(), recorded_at=decision_at)
    assert "realized_return" not in logged
    assert logged["thesis_hash"]
    with pytest.raises(ValueError, match="outcome"):
        log.append_decision(_decision(judgment_id="leak", realized_return=0.1), recorded_at=decision_at)
    with pytest.raises(ValueError, match="already"):
        log.append_decision(_decision(), recorded_at=decision_at + timedelta(hours=1))
    with pytest.raises(ValueError, match="before the horizon"):
        log.append_outcome(_outcome(outcome_at="2026-06-02T15:00:00+00:00"), recorded_at=decision_at + timedelta(days=2))
    with pytest.raises(ValueError, match="strictly after the decision"):
        log.append_outcome(_outcome(), recorded_at=decision_at)
    with pytest.raises(ValueError, match="thesis"):
        log.append_outcome(_outcome(thesis="rewritten"), recorded_at=decision_at + timedelta(days=8))
    log.append_outcome(_outcome(), recorded_at=decision_at + timedelta(days=8))
    with pytest.raises(ValueError, match="already"):
        log.append_outcome(_outcome(), recorded_at=decision_at + timedelta(days=9))
    scored = score_showdown(log.entries())
    assert scored["n_scored"] == 1
    assert scored["rows"][0]["cell"] == "right_for_right_reasons"
    assert scored["orders_submitted"] == 0
    assert scored["yield_claim"] is False
    assert scored["brier"] == pytest.approx(0.09)
    assert scored["reliability"]
    report = render_weekly(scored)
    assert "right_for_right_reasons" in report
    assert scored["weeks"][0]["week"] == "2026-W23"


def test_flat_return_is_not_a_hit():
    judged = classify(_decision(), _outcome(realized_return=0.0))
    assert judged["direction_right"] is False


def test_standin_adapter_writes_the_rule_id_before_the_return(tmp_path):
    from sidecar.llm_audit.adapters import log_standin_closes

    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] * 1.01)
    for _ in range(6):
        closes.append(closes[-1] * 0.98)
    market = [100.0] * len(closes)
    log = ShowdownLog(tmp_path / "standin.jsonl")
    written = log_standin_closes(
        log,
        symbol="SPY",
        closes=closes,
        market_closes=market,
        beta=1.0,
        as_of_start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        lookback=60,
        horizon_sessions=5,
        stride=10,
    )
    assert written == 1
    decision = next(row for row in log.entries() if row["kind"] == "decision")
    assert decision["thesis"] == "combined.deterministic_stand_in.v1"
    assert decision["process_supported"] is True
    assert "realized_return" not in decision
    scored = score_showdown(log.entries())
    assert scored["rows"][0]["cell"] == "wrong_for_right_reasons"
    assert scored["rows"][0]["driver_class"] == "process"


def test_news_catalog_joins_truth_without_copying_the_headline(tmp_path):
    from combined.synth.news import CATALOG
    from sidecar.llm_audit.adapters import log_news_catalog

    wanted = tuple(spec for spec in CATALOG if spec["kind"] in {"earnings_beat", "hoax", "priced_in_beat", "ma_rumor_false"})
    log = ShowdownLog(tmp_path / "news.jsonl")
    summary = log_news_catalog(log, seed=3, noise_sigma=0.0, specs=wanted)
    assert summary["orders_submitted"] == 0
    assert summary["llm_calls"] == 0
    assert summary["cost_usd"] == 0
    assert summary["completion_invented"] is False
    text = (tmp_path / "news.jsonl").read_text(encoding="utf-8")
    assert "system:" not in text
    assert "Nexa Biolabs" not in text
    scored = score_showdown(log.entries())
    by_driver = {row["driver"]: row for row in scored["rows"]}
    assert by_driver["earnings_beat"]["cell"] == "right_for_right_reasons"
    assert by_driver["earnings_beat"]["happened"] is True
    assert by_driver["hoax"]["happened"] is False
    assert by_driver["priced_in_beat"]["happened"] is False
    assert by_driver["ma_rumor_false"]["happened"] is False
    assert scored["n_scored"] == 4
