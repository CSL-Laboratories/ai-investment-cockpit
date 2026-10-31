"""Benchmark ports of three QuantJourney examples.

The live paper runner does not send these books. See README.md.
"""

from sidecar.benchmarks.book import BENCHMARK_NAMES, simulate_benchmark

__all__ = ["BENCHMARK_NAMES", "simulate_benchmark"]
