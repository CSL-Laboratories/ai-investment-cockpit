"""Time one round-set of hot-path cases and print JSON on stdout.

The comparer runs this file as a script with ``PYTHONPATH`` pointed at the
tree under test. This file's directory is the pull-request copy of the harness,
so both trees are measured by the same loops.
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
import traceback

from cases import CASES


def time_cases(rounds: int) -> dict[str, list[float]]:
    if rounds < 1:
        raise ValueError("rounds must be >= 1")
    for name, fn in CASES:
        reset = getattr(fn, "reset", None)
        if reset is not None:
            reset()
        try:
            fn()
        except Exception:
            print(f"benchmark case {name} failed during warmup", file=sys.stderr)
            traceback.print_exc()
            raise
    samples: dict[str, list[float]] = {name: [] for name, _fn in CASES}
    for _ in range(rounds):
        for name, fn in CASES:
            reset = getattr(fn, "reset", None)
            if reset is not None:
                reset()
            gc.collect()
            started = time.perf_counter()
            try:
                fn()
            except Exception:
                print(f"benchmark case {name} failed", file=sys.stderr)
                traceback.print_exc()
                raise
            samples[name].append(time.perf_counter() - started)
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time cockpit hot-path cases.")
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        samples = time_cases(args.rounds)
    except Exception:
        return 1
    json.dump(samples, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
