from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from sidecar.harness.backtest import load_bars
from sidecar.harness.runner import RunnerError, run_runner
from sidecar.harness.trade import PaperTradeClient
from sidecar.kronos_signal.cutoff import EVALUATION_START, PRETRAINING_THROUGH
from sidecar.kronos_signal.gate import KronosGateError, load_kronos_gate
from sidecar.kronos_signal.windows import LeakageError, decision_indexes, make_window


def _bars(start: date, count: int, price: str = "100"):
    rows = []
    day = start
    while len(rows) < count:
        if day.weekday() < 5:
            rows.append(
                SimpleNamespace(
                    day=day,
                    open=Decimal(price),
                    high=Decimal(price),
                    low=Decimal(price),
                    close=Decimal(price),
                    volume=Decimal("1000"),
                )
            )
        day += timedelta(days=1)
    return rows


def test_cost_aware_book_stops_at_the_forecast_horizon():
    from sidecar.harness.backtest import Bar
    from sidecar.kronos_signal.evaluate import _book

    rows = []
    day = date(2024, 7, 1)
    price = Decimal("100")
    while len(rows) < 40:
        if day.weekday() < 5:
            rows.append(
                Bar(
                    day=day,
                    symbol="SPY",
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    adj_close=price,
                    volume=Decimal("1000"),
                )
            )
            price += Decimal("1")
        day += timedelta(days=1)
    indexes = [5, 6, 7, 8, 9, 10, 11, 12]
    edge = {index: {"SPY": (Decimal("0.02"), Decimal("0.02"))} for index in indexes}
    book = _book({"SPY": rows}, ["SPY"], indexes, edge, pred_len=1)
    assert len(book["step_returns"]) == len(indexes)
    assert book["curve"][-1]["date"] == rows[14].day.isoformat()
    assert book["curve"][-1]["date"] != rows[-1].day.isoformat()
    flat = _book(
        {"SPY": rows},
        ["SPY"],
        indexes,
        {index: {"SPY": (Decimal(0), Decimal("0.02"))} for index in indexes},
        pred_len=1,
    )
    assert flat["step_returns"] == [0.0] * len(indexes)
    assert Decimal(flat["ending_equity"]) == Decimal("100000")
    hold = _book({"SPY": rows}, ["SPY"], indexes, {}, pred_len=1, buy_and_hold=True)
    assert len(hold["step_returns"]) == len(indexes)
    assert Decimal(hold["ending_equity"]) > Decimal("100000")


def test_cutoff_matches_the_paper():
    assert PRETRAINING_THROUGH == date(2024, 6, 30)
    assert EVALUATION_START == date(2024, 7, 1)


def test_windows_reject_pretraining_labels_and_future_bars():
    pytest.importorskip("pandas")
    pytest.importorskip("torch")
    from sidecar.kronos_signal.forecast import forecast_returns

    rows = _bars(date(2024, 5, 1), 80)
    too_early = next(index for index, bar in enumerate(rows) if bar.day < EVALUATION_START and index > 20)
    with pytest.raises(LeakageError):
        make_window(rows, too_early, lookback=10, pred_len=1)
    indexes = decision_indexes(rows, pred_len=1, stride=1, max_decisions=5)
    assert indexes
    assert all(rows[index].day >= EVALUATION_START for index in indexes)
    window = make_window(rows, indexes[0], lookback=10, pred_len=1)
    assert window["context"][-1].day == window["decision"]
    assert window["future"][0].day > window["decision"]
    with pytest.raises(ValueError):
        forecast_returns(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model was called")),
            [window["context"]],
            [window["decision"]],
            sample_count=1,
            temperature=1.0,
            top_p=0.9,
            seed=0,
        )


def test_missing_or_early_report_does_not_open_a_trade_client(tmp_path: Path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("kronos gate constructed a trade client")

    monkeypatch.setattr(PaperTradeClient, "__init__", explode)
    with pytest.raises(RunnerError, match="offline evaluation"):
        run_runner(
            out=tmp_path / "report.json",
            ledger_path=tmp_path / "ledger.jsonl",
            order_log_path=tmp_path / "orders.jsonl",
            kill_file=tmp_path / "absent",
            strategy="kronos",
            kronos_report=tmp_path / "missing.json",
            when="close",
            execute=True,
            force_window=True,
            bootstrap_reps=60,
            refresh=False,
        )
    early = {
        "schema_version": "sidecar.kronos_eval.v1",
        "live_eligible": True,
        "evaluation_start": "2024-01-01",
        "pretraining_through": "2024-06-30",
        "orders_submitted": 0,
        "tradable_signals": [],
    }
    path = tmp_path / "early.json"
    path.write_text(json.dumps(early), encoding="utf-8")
    with pytest.raises(KronosGateError):
        load_kronos_gate(path)


def test_runner_sizes_kronos_from_the_report_without_loading_weights(tmp_path: Path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("runner loaded Kronos weights")

    monkeypatch.setattr("sidecar.kronos_signal.model_loader.load_predictor", explode)
    bars = load_bars(Path(__file__).resolve().parents[1] / "fixtures" / "etf_daily.csv")
    as_of = bars["SPY"][-1].day.isoformat()
    document = {
        "schema_version": "sidecar.kronos_eval.v1",
        "live_eligible": True,
        "evaluation_start": "2024-07-01",
        "pretraining_through": "2024-06-30",
        "orders_submitted": 0,
        "strategy_returns": [0.01, -0.02, 0.015, -0.005, 0.008, -0.001, 0.012, -0.004],
        "equity_curve": [
            {"date": "2024-07-01", "equity": "100000"},
            {"date": as_of, "equity": "100100"},
        ],
        "tradable_signals": [
            {
                "symbol": symbol,
                "as_of": as_of,
                "edge": "0.02" if symbol == "SPY" else "-0.01",
                "vol": "0.02",
                "p_up": "0.75" if symbol == "SPY" else "0.25",
                "horizon_bars": 1,
                "scored": False,
            }
            for symbol in ("SPY", "QQQ", "IWM")
        ],
    }
    report_path = tmp_path / "kronos_eval.json"
    report_path.write_text(json.dumps(document), encoding="utf-8")
    report = run_runner(
        out=tmp_path / "runner.json",
        ledger_path=tmp_path / "ledger.jsonl",
        order_log_path=tmp_path / "orders.jsonl",
        kill_file=tmp_path / "absent",
        strategy="kronos",
        kronos_report=report_path,
        when="close",
        execute=False,
        bootstrap_reps=60,
        refresh=False,
    )
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["strategy_id"] == "sidecar.kronos_signal.v1"
    assert report["equity_source"] == "kronos_offline_eval"
    assert any(row["result"] == "dry_run" and row["symbol"] == "SPY" for row in report["primary_config"]["order_results"])
    assert all(row["symbol"] != "QQQ" or row["result"] != "dry_run" for row in report["primary_config"]["order_results"])


def test_kronos_tests_collect_without_the_kronos_extra():
    import subprocess
    import sys

    script = """
import sys
class Block:
    def find_spec(self, fullname, path, target=None):
        if fullname == "torch" or fullname == "pandas" or fullname.startswith("torch.") or fullname.startswith("pandas."):
            raise ModuleNotFoundError(fullname)
        return None
sys.meta_path.insert(0, Block())
import tests.test_kronos
print("collected")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "collected" in completed.stdout


def test_smoke_profile_stays_post_cutoff(tmp_path: Path):
    pytest.importorskip("torch")
    from sidecar.kronos_signal.evaluate import run_profile

    report = run_profile(
        "smoke",
        out=tmp_path / "kronos.json",
        ledger_path=tmp_path / "kronos.ledger.jsonl",
    )
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["evaluation_start"] == "2024-07-01"
    assert report["pretraining_through"] == "2024-06-30"
    assert report["live_eligible"] is True
    assert report["evalstats"]["threshold_status"] == "not_frozen"
    assert report["evalstats"]["n_observations"] >= 8
    assert report["signal_quality"]["n"] >= 8
    assert report["evalstats"]["pbo"]["status"] == "ok"
    book_mean = sum(report["strategy_returns"]) / len(report["strategy_returns"])
    assert abs(float(report["evalstats"]["mean_return"]) - book_mean) < 1e-9
    assert report["tradable_signals"]
    assert all(row["as_of"] >= "2024-07-01" for row in report["tradable_signals"])
    assert "information_coefficient" in report["signal_quality"]
    assert "directional_hit_rate" in report["signal_quality"]
    assert "brier_direction" in report["signal_quality"]
