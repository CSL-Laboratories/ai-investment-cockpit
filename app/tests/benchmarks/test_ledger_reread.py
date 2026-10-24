"""Helpers and a tiny seeded run for the manual ledger re-read benchmark."""
from __future__ import annotations

import pytest

from benchmarks import ledger_reread as lr


def test_nearest_rank_index_by_sample_count():
    assert lr.nearest_rank([5.0], lr.P50) == 5.0
    assert lr.nearest_rank([5.0], lr.P95) == 5.0
    assert lr.nearest_rank([2.0, 1.0], lr.P50) == 1.0  # round(0.5) == 0 (banker's rounding)
    assert lr.nearest_rank([2.0, 1.0], lr.P95) == 2.0
    samples = [float(i) for i in range(1, 22)]
    assert lr.nearest_rank(samples, lr.P50) == 11.0
    assert lr.nearest_rank(samples, lr.P95) == 20.0
    assert lr.nearest_rank(list(reversed(samples)), lr.P95) == 20.0


def test_nearest_rank_rejects_bad_input():
    with pytest.raises(ValueError):
        lr.nearest_rank([], lr.P50)
    with pytest.raises(ValueError):
        lr.nearest_rank([1.0], 1.5)


def test_check_percentiles_passes():
    lr._check_percentiles()


def test_parse_args_validation():
    with pytest.raises(SystemExit):
        lr._parse_args(["--rows", "0"])
    with pytest.raises(SystemExit):
        lr._parse_args(["--samples", "0"])
    with pytest.raises(SystemExit):
        lr._parse_args(["--rows", "10", "10"])
    with pytest.raises(SystemExit):
        lr._parse_args(["--app-dir", "."])
    assert lr._parse_args(["--rows", "5"]).rows == [5]


def test_row_spec_cycles_event_types():
    kinds = [lr._row_spec(i, "deep", "note", "research")[0] for i in range(6)]
    assert kinds == ["deep", "note", "research"] * 2


def test_minimal_seeded_run_reports_all_operations():
    measurements = lr._measure_size(rows=9, samples=2, warmups=0)
    assert [m["operation"] for m in measurements] == [
        "DeepBudget.try_consume",
        "compute_gate",
        "GET /status",
    ]
    for m in measurements:
        assert m["rows"] == 9
        assert m["samples"] == 2
        assert m["deep_escalation_rows"] == 3
        assert m["paper_notification_rows"] == 3
        assert m["research_update_rows"] == 3
    assert "| 9 | GET /status |" in lr._markdown_table(measurements)
