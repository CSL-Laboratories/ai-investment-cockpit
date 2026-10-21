"""CSL-404: the Rust benchmark gate keeps "not slower than Python" without flaking on noise.

``rust/bench_gate.py`` is plain Python, so these run without the extension. A
scripted clock stands in for the runner: each ``fn()`` call advances it by the
next scripted duration.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[3] / "rust" / "bench_gate.py"
_spec = importlib.util.spec_from_file_location("bench_gate", _GATE)
bench_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench_gate)


class ScriptedRunner:
    """Times come from ``durations[side]``; ``clock`` only moves when a side runs."""

    def __init__(self, reference: list[float], candidate: list[float]):
        self.now = 0.0
        self.queues = {"ref": list(reference), "cand": list(candidate)}
        self.order: list[str] = []

    def clock(self) -> float:
        return self.now

    def side(self, name: str):
        def run():
            self.order.append(name)
            self.now += self.queues[name].pop(0)
        return run

    def ratio(self, pairs: int = bench_gate.PAIRS) -> float:
        return bench_gate.paired_ratio(self.side("ref"), self.side("cand"),
                                       pairs=pairs, clock=self.clock)


def _old_block_median(reference: list[float], candidate: list[float]) -> float:
    """What main did: five reference runs, then five candidate runs, ratio of medians."""
    ref = sorted(reference[:5])[2]
    cand = sorted(candidate[:5])[2]
    return ref / cand


def test_floors_are_the_decided_guarantee():
    assert bench_gate.PER_CALL_FLOOR == 0.95
    assert bench_gate.BATCH_FLOOR == 1.00
    assert bench_gate.PAIRS >= 11


def test_pairs_alternate_which_side_runs_first():
    runner = ScriptedRunner([1.0] * 4, [1.0] * 4)
    runner.ratio(pairs=4)
    assert runner.order == ["ref", "cand", "cand", "ref", "ref", "cand", "cand", "ref"]


def test_a_noise_burst_on_one_block_no_longer_fails_the_gate():
    """The #223 shape: Rust is ~1.4x faster, but a burst slows a run of Rust timings.

    In block order the burst covers three of the five Rust samples and the old
    gate reads ~0.97x. Interleaved, the same burst covers a minority of pairs.
    """
    ref = [3.4] * 15
    cand = [2.4] * 15
    for i in (1, 2, 3):          # three consecutive slow candidate samples
        cand[i] = 3.5
    assert _old_block_median(ref, cand) < 1.0, "precondition: old gate fails on this noise"
    ratio = ScriptedRunner(ref, cand).ratio()
    assert ratio >= bench_gate.PER_CALL_FLOOR
    assert bench_gate.gate_failure("per-call EWMA", ratio, bench_gate.PER_CALL_FLOOR) is None


def test_a_real_per_call_regression_still_fails():
    """NEGATIVE CONTROL: Rust consistently 10% slower is a regression, margin or not."""
    ratio = ScriptedRunner([3.4] * 15, [3.4 / 0.90] * 15).ratio()
    assert ratio == pytest.approx(0.90)
    msg = bench_gate.gate_failure("per-call EWMA", ratio, bench_gate.PER_CALL_FLOOR)
    assert msg is not None and "per-call EWMA" in msg and "0.95x" in msg


def test_near_parity_passes_per_call_but_fails_batched():
    """0.97x is inside the per-call noise margin; a batched path has no such excuse."""
    ratio = ScriptedRunner([3.4] * 15, [3.4 / 0.97] * 15).ratio()
    assert bench_gate.gate_failure("per-call", ratio, bench_gate.PER_CALL_FLOOR) is None
    assert bench_gate.gate_failure("batched", ratio, bench_gate.BATCH_FLOOR) is not None


def test_majority_of_slow_pairs_fails():
    """NEGATIVE CONTROL: noise that hits most pairs is not filtered away."""
    cand = [2.4] * 7 + [4.0] * 8
    ratio = ScriptedRunner([3.4] * 15, cand).ratio()
    assert ratio < bench_gate.PER_CALL_FLOOR


def test_gate_line_reports_verdict():
    assert bench_gate.gate_line("x", 1.2, 1.0).endswith("ok")
    assert bench_gate.gate_line("x", 0.9, 0.95).endswith("FAIL")


def test_pairs_must_be_positive():
    with pytest.raises(ValueError):
        bench_gate.paired_ratio(lambda: None, lambda: None, pairs=0)
