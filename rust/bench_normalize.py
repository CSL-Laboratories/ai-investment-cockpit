#!/usr/bin/env python3
"""Time Rust normalization against the Python reference.

Per-call is one event per FFI crossing. The batch row sends the whole list
in one call. Raw input stays a separate object from the escaped headline.

    python -m maturin build --release --manifest-path rust/cockpit-py/Cargo.toml
    python -m pip install --force-reinstall rust/target/wheels/cockpit_quant-*.whl
    python rust/bench_normalize.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import cockpit_quant  # noqa: E402
from ingest.normalize import (  # noqa: E402
    NORMALIZE_BACKEND,
    escape_untrusted,
    python_escape_untrusted,
    python_to_market_event,
    to_market_event,
)

N = 5_000


def _headlines(n: int) -> list[str]:
    return [f"ＳＹＳＴＥＭ: headline {i} <tag> {'x' * (i % 80)}" for i in range(n)]


def _events(n: int):
    headlines = _headlines(n)
    return [
        ("finnhub", str(i), ["aapl", "msft"], headlines[i], "summary text", "news")
        for i in range(n)
    ]


def _python_escape(texts) -> None:
    for text in texts:
        python_escape_untrusted(text)


def _rust_escape(texts) -> None:
    for text in texts:
        escape_untrusted(text)


def _python_events(rows) -> None:
    for source, article_id, symbols, headline, summary, event_type in rows:
        python_to_market_event(
            source=source, article_id=article_id, symbols=symbols, headline=headline,
            summary=summary, ts=0, event_type=event_type,
        )


def _rust_events(rows) -> None:
    for source, article_id, symbols, headline, summary, event_type in rows:
        to_market_event(
            source=source, article_id=article_id, symbols=symbols, headline=headline,
            summary=summary, ts=0, event_type=event_type,
        )


def _rust_batch(rows) -> None:
    cockpit_quant.market_event_many(rows)


def _median(fn, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


def main() -> None:
    if NORMALIZE_BACKEND != "rust":
        print("cockpit_quant normalizer is not installed; build the release extension first")
        sys.exit(1)
    texts = _headlines(N)
    rows = _events(N)
    _python_escape(texts[:4])
    _rust_escape(texts[:4])
    _python_events(rows[:4])
    _rust_events(rows[:4])
    _rust_batch(rows[:4])
    py_escape = _median(lambda: _python_escape(texts))
    rust_escape = _median(lambda: _rust_escape(texts))
    py_event = _median(lambda: _python_events(rows))
    rust_event = _median(lambda: _rust_events(rows))
    batch_event = _median(lambda: _rust_batch(rows))

    def row(name: str, python: float, rust: float) -> str:
        ratio = python / rust if rust else float("inf")
        return f"| {name} | {python * 1e3:.1f} ms | {rust * 1e3:.1f} ms | {ratio:.2f}x |"

    print(f"items={N}  (median of 5)")
    print("| workload | python | rust | python/rust |")
    print("|---|---:|---:|---:|")
    print(row(f"per-call escape ({N})", py_escape, rust_escape))
    print(row(f"per-call to_market_event ({N})", py_event, rust_event))
    print(row(f"batched to_market_event ({N})", py_event, batch_event))


if __name__ == "__main__":
    main()
