"""Decision rules for the non-blocking performance comparison."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.cases import CASE_NAMES
from benchmarks.compare import (
    COMMENT_MARKER,
    child_env,
    compare_samples,
    main,
    rel_iqr,
    render_comment,
    write_outputs,
)

REPO = Path(__file__).resolve().parents[3]


def _report(base: dict, cand: dict, **over):
    kwargs = dict(threshold=0.10, rounds=5, orderings=2, elapsed_s=1.25)
    kwargs.update(over)
    return compare_samples(base, cand, **kwargs)


def test_rel_iqr_nearest_rank():
    samples = [0.8, 0.8, 0.8, 1.0, 1.0, 1.0, 1.0, 1.2, 1.2, 1.2]
    assert rel_iqr(samples) == pytest.approx(0.4)


def test_clear_regression_when_spread_is_tight():
    report = _report({"risk_evaluate": [1.0] * 10}, {"risk_evaluate": [1.12] * 10})
    case = report.cases[0]
    assert case.status == "regression"
    assert report.regressions == ("risk_evaluate",)
    assert "does not fail" in render_comment(report)


def test_ten_percent_is_not_over_the_threshold():
    report = _report({"risk_evaluate": [1.0] * 10}, {"risk_evaluate": [1.10] * 10})
    assert report.cases[0].status == "ok"
    assert report.regressions == ()


def test_borderline_gap_with_spread_is_inconclusive():
    # Median +12%, relative IQR about 3.6%, so slowdown - spread stays under 10%.
    cand = [1.10, 1.10, 1.10, 1.12, 1.12, 1.12, 1.12, 1.14, 1.14, 1.14]
    report = _report({"risk_evaluate": [1.0] * 10}, {"risk_evaluate": cand})
    assert report.cases[0].status == "inconclusive"
    assert report.regressions == ()


def test_large_gap_still_flags_when_spread_is_moderate():
    cand = [1.25, 1.25, 1.25, 1.30, 1.30, 1.30, 1.30, 1.35, 1.35, 1.35]
    report = _report({"order_tuple_hash": [1.0] * 10}, {"order_tuple_hash": cand})
    assert report.cases[0].status == "regression"


def test_speedup_is_not_a_regression():
    report = _report({"outbound_scrub": [1.0] * 8}, {"outbound_scrub": [0.8] * 8})
    assert report.cases[0].status == "ok"
    assert report.cases[0].slowdown < 0


def test_short_samples_are_not_flagged():
    report = _report({"schema_validate_proposal": [0.001] * 8}, {"schema_validate_proposal": [0.002] * 8})
    assert report.cases[0].status == "inconclusive"


def test_missing_candidate_case_is_not_a_median_regression():
    report = _report({"audit_append_verify": [0.05] * 6}, {})
    assert report.cases[0].status == "missing"
    assert report.regressions == ()


def test_comment_file_only_when_a_regression_is_flagged(tmp_path):
    comment = tmp_path / "comment.md"
    clear = tmp_path / "clear.md"
    report = _report({"risk_evaluate": [1.0] * 8}, {"risk_evaluate": [1.2] * 8})
    write_outputs(report, comment=comment, clear_comment=clear, summary=None, report_path=None)
    text = comment.read_text(encoding="utf-8")
    assert text.startswith(COMMENT_MARKER)
    assert "risk_evaluate" in text
    assert COMMENT_MARKER in clear.read_text(encoding="utf-8")

    comment.unlink()
    ok = _report({"risk_evaluate": [1.0] * 8}, {"risk_evaluate": [1.0] * 8})
    write_outputs(ok, comment=comment, clear_comment=clear, summary=None, report_path=None)
    assert not comment.exists()


def test_child_env_points_at_the_tree_and_drops_scale(monkeypatch, tmp_path):
    monkeypatch.setenv("COCKPIT_BENCH_SCALE", "0.1")
    monkeypatch.setenv("PYTHONPATH", "/tmp/not-the-tree")
    env = child_env(tmp_path, scale=1)
    assert env["PYTHONPATH"] == str((tmp_path / "app").resolve())
    assert "COCKPIT_BENCH_SCALE" not in env
    assert env["PYTHONHASHSEED"] == "0"


def test_scale_is_refused_on_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(SystemExit, match="scale"):
        main([
            "--baseline-root", str(REPO),
            "--candidate-root", str(REPO),
            "--scale", "0.1",
            "--rounds", "1",
            "--orderings", "1",
        ])


def test_same_tree_comparison_exits_clean(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    report_path = tmp_path / "report.json"
    code = main([
        "--baseline-root", str(REPO),
        "--candidate-root", str(REPO),
        "--rounds", "1",
        "--orderings", "1",
        "--scale", "0.05",
        "--report", str(report_path),
        "--summary", str(tmp_path / "summary.md"),
        "--comment", str(tmp_path / "comment.md"),
        "--clear-comment", str(tmp_path / "clear.md"),
    ])
    assert code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert {row["name"] for row in payload["cases"]} == set(CASE_NAMES)
    assert all(row["baseline_median_s"] > 0 and row["candidate_median_s"] > 0 for row in payload["cases"])
    # One sample a side cannot clear the four-sample confirmation bar.
    assert payload["regressions"] == []


def test_benchmarks_job_is_not_part_of_linux_gates():
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gates = text.split("  linux-gates:", 1)[1].split("  windows:", 1)[0]
    assert "benchmarks" not in gates
    assert "needs: [filter, linux]" in text
    bench = text.split("  benchmarks:", 1)[1]
    assert "timeout-minutes: 5" in bench
    assert "--threshold 0.10" in bench
    assert "pull_request" in bench
    assert "issues: write" in bench
    assert "pull-requests: write" in bench
