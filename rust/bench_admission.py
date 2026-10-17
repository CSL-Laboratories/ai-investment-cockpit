#!/usr/bin/env python3
"""Time admission against the Python reference.

Per-call rows are what a poller runs: ``Dedup.add`` and ``TokenBucket.allow``,
both on the Python implementation. Batch rows hand the script to the Rust
extension in one call.

    python -m maturin build --release --manifest-path rust/cockpit-py/Cargo.toml
    python -m pip install --force-reinstall rust/target/wheels/cockpit_quant-*.whl
    python rust/bench_admission.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from ingest.dedup import DEDUP_BACKEND, Dedup, dedup_replay  # noqa: E402
from ingest.ratelimit import RATELIMIT_BACKEND, TokenBucket, token_bucket_replay  # noqa: E402

N = 20_000
_CAP = 8_192


def _keys(n: int) -> list[str]:
    return [f"src:{i % (n // 2)}" for i in range(n)]


def _hot_dedup(keys) -> None:
    """The poller path. ``_core`` stays unset, so this is the OrderedDict."""
    seen = Dedup(capacity=_CAP)
    for key in keys:
        seen.add(key)


def _hot_bucket(times) -> None:
    """The poller path. ``allow`` does not enter the extension."""
    bucket = TokenBucket(rate_per_sec=1.0, capacity=5.0, now=0.0)
    for now in times:
        bucket.allow(now)


def _median(fn, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


def main() -> None:
    if DEDUP_BACKEND != "rust" or RATELIMIT_BACKEND != "rust":
        print("cockpit_quant admission is not installed; build the release extension first")
        sys.exit(1)
    keys = _keys(N)
    times = [i * 0.01 for i in range(N)]
    ops = [("a", key) for key in keys]
    probe = Dedup(capacity=_CAP)
    if probe._core is not None:
        print("per-call Dedup attached a Rust core; the hot path would pay FFI")
        sys.exit(1)
    _hot_dedup(keys[:8])
    dedup_replay(_CAP, ops[:8])
    _hot_bucket(times[:8])
    token_bucket_replay(1.0, 5.0, 0.0, 0.0, times[:8])

    # Per-call is one Python implementation, so both columns are that timing.
    # Batch columns compare it with one Rust call.
    ref_dedup = hot_dedup = _median(lambda: _hot_dedup(keys))
    batch_dedup = _median(lambda: dedup_replay(_CAP, ops))
    ref_bucket = hot_bucket = _median(lambda: _hot_bucket(times))
    batch_bucket = _median(lambda: token_bucket_replay(1.0, 5.0, 0.0, 0.0, times))

    def row(name: str, reference: float, measured: float) -> str:
        ratio = reference / measured if measured else float("inf")
        return f"| {name} | {reference * 1e3:.1f} ms | {measured * 1e3:.1f} ms | {ratio:.2f}x |"

    print(f"n={N}  (median of 5, release)")
    print("| workload | python | this head | python/head |")
    print("|---|---:|---:|---:|")
    print(row("per-call dedup", ref_dedup, hot_dedup))
    print(row("batched dedup", ref_dedup, batch_dedup))
    print(row("per-call token bucket", ref_bucket, hot_bucket))
    print(row("batched token bucket", ref_bucket, batch_bucket))


if __name__ == "__main__":
    main()
