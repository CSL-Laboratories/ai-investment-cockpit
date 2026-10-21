"""Noise-tolerant speed gate shared by the Rust benchmarks (CSL-404).

The guarantee is split by path (Samir's decision on CSL-439 / #241):

* Batched paths keep the strict guarantee: the shipped Rust path must not be
  slower than the Python reference (1.00x floor).
* Per-call paths get a 0.95x floor instead. It replaces "never slower than
  Python" for those paths only: a per-call path may measure up to 5% slower
  and still pass, and a consistent slowdown beyond that fails.

What also changed is how a runner's noise is kept out of the answer.

The old gates timed five Python runs, then five Rust runs, and compared the two
medians against a hard 1.00x floor. A noisy-neighbour burst that lands on one
block moves only that side, and on a 2-vCPU runner that turned a per-call EWMA
measured at 1.38x on the rerun into 0.97x on the failing run (cockpit #223, job
118942409418).

Here each pair times the reference and the candidate back to back, alternating
which goes first, and the gate reads the median of the per-pair ratios. A burst
now hits both sides of a pair or a minority of pairs. Paths that run at about
parity with Python by design (per-call, which crosses the interpreter once per
observation) get a 0.95x floor as a noise margin. Batched paths, which run
several times faster, keep the strict 1.00x floor.
"""
from __future__ import annotations

import statistics
import time
from collections.abc import Callable

PAIRS = 16
PER_CALL_FLOOR = 0.95
BATCH_FLOOR = 1.00


def _time(fn: Callable[[], object], clock: Callable[[], float]) -> float:
    start = clock()
    fn()
    return clock() - start


def paired_ratio(reference: Callable[[], object], candidate: Callable[[], object],
                 pairs: int = PAIRS, clock: Callable[[], float] = time.perf_counter) -> float:
    """Median of ``reference / candidate`` over ``pairs`` interleaved timings.

    Above 1.0 means the candidate is faster. Even pairs time the reference first
    and odd pairs time the candidate first, so warm-up or drift inside a pair
    does not favour one side. ``pairs`` must be even so both orders contribute the
    same number of ratios; with an odd count the larger orientation would decide
    the median whenever the order itself moves the ratio (Codex on #241).
    """
    if pairs < 2 or pairs % 2:
        raise ValueError("pairs must be a positive even number")
    ratios = []
    for i in range(pairs):
        if i % 2:
            cand = _time(candidate, clock)
            ref = _time(reference, clock)
        else:
            ref = _time(reference, clock)
            cand = _time(candidate, clock)
        ratios.append(ref / cand if cand > 0 else float("inf"))
    return statistics.median(ratios)


def gate_failure(name: str, ratio: float, floor: float) -> str | None:
    """The exit message when ``ratio`` is under ``floor``, else ``None``."""
    if ratio < floor:
        return (f"{name} regressed: python/rust {ratio:.2f}x is under the "
                f"{floor:.2f}x floor (median of {PAIRS} interleaved pairs)")
    return None


def gate_line(name: str, ratio: float, floor: float) -> str:
    verdict = "ok" if ratio >= floor else "FAIL"
    return f"gate {name}: {ratio:.2f}x (floor {floor:.2f}x, {PAIRS} pairs) {verdict}"
