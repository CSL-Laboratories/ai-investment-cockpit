"""CSL-42 — Rust dedup and token bucket must match the Python admission controls.

Parity cases are skipped when the extension is not installed, which is the
hosted Python gate. ``COCKPIT_REQUIRE_RUST=1`` fails the file instead of
skipping, so a wheel that installs but does not activate admission fails CI.
Dedup compares a Rust-backed instance with a twin whose ``_core`` is cleared.
The bucket compares ``TokenBucket.allow`` with ``python_allow`` on a twin that
starts from the same numbers.
"""
from __future__ import annotations

import logging
import math
import os
import random
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ingest.dedup import DEDUP_BACKEND, Dedup, _core_key
from ingest.ratelimit import RATELIMIT_BACKEND, TokenBucket, python_allow

# The rust.yml job sets this so a wheel that installs but does not activate
# fails the job. Hosted Python gates leave it unset and skip the parity cases.
_REQUIRE_RUST = os.environ.get("COCKPIT_REQUIRE_RUST") == "1"
if _REQUIRE_RUST and (DEDUP_BACKEND != "rust" or RATELIMIT_BACKEND != "rust"):
    raise RuntimeError("COCKPIT_REQUIRE_RUST=1 but the admission backend is python")

_needs_rust = pytest.mark.skipif(
    not _REQUIRE_RUST and (DEDUP_BACKEND != "rust" or RATELIMIT_BACKEND != "rust"),
    reason="cockpit_quant admission extension is not installed",
)


def _dedup(capacity):
    accelerated = Dedup(capacity)
    reference = Dedup(capacity)
    reference._core = None
    return accelerated, reference


def _drive_bucket(bucket, times, *, python: bool):
    allowed = []
    for now in times:
        if python:
            allowed.append(python_allow(bucket, now))
        else:
            allowed.append(bucket.allow(now))
    return allowed


@_needs_rust
def test_dedup_matches_lru_eviction_and_discard():
    rust, python = _dedup(2)
    script = ["a", "b", "a", "c", "a", "b"]
    for key in script:
        assert rust.add(key) is python.add(key)
    assert rust.discard("c") is python.discard("c")
    assert rust.discard("a") is python.discard("a")
    assert rust.add("c") is python.add("c")

    for cap in (0, -1, -50):
        rust, python = _dedup(cap)
        assert rust.add("k") is python.add("k") is True
        assert rust.add("k") is python.add("k") is True
        assert rust.discard("k") is python.discard("k") is False

    rust, python = _dedup(3)
    for key in ("a", "b", "c", "d"):
        assert rust.add(key) is python.add(key)
    assert rust.discard("a") is python.discard("a") is False
    assert rust.discard("c") is python.discard("c") is True
    assert rust.add("c") is python.add("c") is True


@_needs_rust
def test_dedup_replay_matches_a_random_script():
    import cockpit_quant

    rng = random.Random(7)
    keys = [f"k{i}" for i in range(30)]
    ops = []
    for _ in range(400):
        key = rng.choice(keys)
        ops.append(("d" if rng.random() < 0.25 else "a", key))
    replay = cockpit_quant.dedup_replay(11, ops)
    python = Dedup(11)
    python._core = None
    for (op, key), replayed in zip(ops, replay):
        if op == "a":
            assert replayed is python.add(key)
        else:
            assert replayed is python.discard(key)


@_needs_rust
def test_a_non_str_key_is_refused_on_the_rust_path():
    rust, python = _dedup(4)
    with pytest.raises(TypeError, match="dedup key must be a str"):
        rust.add(1)
    assert python.add(1) is True
    odd = Dedup(capacity=True)
    assert odd._core is None
    assert odd.add("a") is True


@_needs_rust
def test_token_bucket_matches_including_a_backwards_clock():
    times = [0.0, 0.0, 0.4, 0.4, 1.0, 0.2, 3.0, 3.0, 3.2]
    rust = TokenBucket(rate_per_sec=2.0, capacity=1.5, now=0.0)
    python = TokenBucket(rate_per_sec=2.0, capacity=1.5, now=0.0)
    assert _drive_bucket(rust, times, python=False) == _drive_bucket(python, times, python=True)
    assert rust.tokens == python.tokens
    assert rust._last == python._last

    rust.tokens = 0.25
    python.tokens = 0.25
    assert rust.allow(4.0) is python_allow(python, 4.0)
    assert rust.tokens == pytest.approx(python.tokens, rel=0, abs=0)
    assert rust._last == python._last


@_needs_rust
def test_token_bucket_replay_matches_and_starts_empty():
    import cockpit_quant

    birth = TokenBucket(rate_per_sec=1.0, capacity=1.0, now=0.0)
    assert birth.allow(0.0) is False
    rng = random.Random(9)
    times = [0.0]
    for _ in range(200):
        times.append(times[-1] + rng.choice((-0.1, 0.0, 0.05, 0.5, 2.0)))
    allowed, tokens, last = cockpit_quant.token_bucket_replay(1.5, 3.0, 0.0, 0.0, times)
    python = TokenBucket(rate_per_sec=1.5, capacity=3.0, now=0.0)
    for now, bit in zip(times, allowed):
        assert bit is python_allow(python, now)
    assert tokens == python.tokens
    assert last == python._last


def _same_number(left, right) -> None:
    if (
        isinstance(left, float)
        and isinstance(right, float)
        and math.isnan(left)
        and math.isnan(right)
    ):
        return
    assert left == right


@_needs_rust
def test_token_bucket_nan_matches_python_max_and_min():
    """A NaN on the right of ``max`` / ``min`` does not replace the left, as in Python."""
    cases = [
        (1.0, 2.0, 0.0, 0.0, float("nan")),
        (float("nan"), 2.0, 0.0, 0.0, 1.0),
        (1.0, float("nan"), 0.0, 0.0, 1.0),
        (1.0, 2.0, float("nan"), 0.0, 1.0),
        (float("inf"), 2.0, 0.0, 0.0, 1.0),
        (1.0, 2.0, 0.0, 0.0, float("inf")),
        (0.0, 2.0, 0.0, 0.0, float("inf")),
    ]
    for rate, capacity, tokens, last, now in cases:
        rust = TokenBucket(rate_per_sec=rate, capacity=capacity, now=last)
        rust.tokens = tokens
        python = TokenBucket(rate_per_sec=rate, capacity=capacity, now=last)
        python.tokens = tokens
        assert rust.allow(now) is python_allow(python, now)
        _same_number(rust.tokens, python.tokens)
        _same_number(rust._last, python._last)


@_needs_rust
def test_a_lone_surrogate_shares_one_lru_with_utf8_keys():
    key = "\ud800"
    rust, python = _dedup(2)
    assert rust.add("a") is python.add("a") is True
    assert rust.add(key) is python.add(key) is True
    assert rust.add("b") is python.add("b") is True
    # "a" was the oldest. One window of 2 has already dropped it.
    assert rust.add("a") is python.add("a") is True
    assert rust.discard(key) is python.discard(key) is False

    rust, python = _dedup(2)
    assert rust.add("a") is python.add("a") is True
    assert rust.add("b") is python.add("b") is True
    assert rust.add("a") is python.add("a") is False
    assert rust.add(key) is python.add(key) is True
    assert rust.add("a") is python.add("a") is False
    assert rust.discard(key) is python.discard(key) is True
    assert rust.add("b") is python.add("b") is True

    folded = "alpaca:benzinga:\ud800\U0001f600"
    pair = "alpaca:benzinga:\ud800\ud83d\ude00"
    assert folded != pair
    assert _core_key(folded) != _core_key(pair)
    rust, python = _dedup(4)
    assert rust.add(folded) is python.add(folded) is True
    assert rust.add(pair) is python.add(pair) is True
    assert rust.add("\x00" + folded) is python.add("\x00" + folded) is True
    assert rust.add("\x00" + pair) is python.add("\x00" + pair) is True

    marked = "\x00a"
    rust, python = _dedup(2)
    assert rust.add(marked) is python.add(marked) is True
    assert rust.add("b") is python.add("b") is True
    assert rust.add("c") is python.add("c") is True
    assert rust.add(marked) is python.add(marked) is True


def test_a_capacity_past_the_float_range_stays_on_python():
    huge = 10**400
    rust = TokenBucket(rate_per_sec=1, capacity=huge, now=0)
    python = TokenBucket(rate_per_sec=1, capacity=huge, now=0)
    assert rust.allow(1) is python_allow(python, 1) is True
    assert rust.tokens == python.tokens
    assert rust._last == python._last


def test_an_int_rust_cannot_hold_stays_off_the_extension():
    from ingest.ratelimit import _exact_f64

    assert _exact_f64(2**53 + 1) is None
    assert _exact_f64(10**400) is None
    assert _exact_f64(2) == 2.0
    assert _exact_f64(1.5) == 1.5
    assert _exact_f64(True) is None


@_needs_rust
def test_an_exact_integer_capacity_matches_python():
    rust = TokenBucket(rate_per_sec=1, capacity=2, now=0)
    python = TokenBucket(rate_per_sec=1, capacity=2, now=0)
    assert rust.allow(0) is python_allow(python, 0) is False
    assert rust.allow(1) is python_allow(python, 1) is True
    assert rust.tokens == python.tokens
    assert rust._last == python._last


def test_admission_backend_is_logged(caplog):
    import ingest.dedup as dedup
    import ingest.ratelimit as ratelimit

    with caplog.at_level(logging.INFO, logger="ingest.dedup"):
        dedup._load_native()
    with caplog.at_level(logging.INFO, logger="ingest.ratelimit"):
        ratelimit._load_native()
    assert f"DEDUP_BACKEND={dedup.DEDUP_BACKEND}" in caplog.text
    assert f"RATELIMIT_BACKEND={ratelimit.RATELIMIT_BACKEND}" in caplog.text


def _python(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    app = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = app + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=app,
        env=env,
        check=False,
    )


def _load_probe(path: str) -> str:
    """Load one admission module by path so package init cannot hide the other."""
    return textwrap.dedent(
        f"""
        spec = importlib.util.spec_from_file_location("admission_probe", "{path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        """
    )


@pytest.mark.parametrize("path", ["ingest/dedup.py", "ingest/ratelimit.py"])
def test_broken_extension_import_is_not_swallowed(path):
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                raise ImportError("broken extension")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        """
    ) + _load_probe(path)
    ran = _python(script)
    assert ran.returncode != 0, ran.stderr
    assert "broken extension" in ran.stderr


@pytest.mark.parametrize(
    ("path", "symbol"),
    [("ingest/dedup.py", "DedupCore"), ("ingest/ratelimit.py", "token_bucket_allow")],
)
def test_partial_extension_is_not_a_silent_python_fallback(path, symbol):
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util
        import types

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                return types.ModuleType("cockpit_quant")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        """
    ) + _load_probe(path)
    ran = _python(script)
    assert ran.returncode != 0, ran.stderr
    assert symbol in ran.stderr


def test_missing_extension_stays_on_python():
    script = textwrap.dedent(
        """
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return None
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        import ingest.dedup as dedup
        import ingest.ratelimit as ratelimit
        assert dedup.DEDUP_BACKEND == "python", dedup.DEDUP_BACKEND
        assert ratelimit.RATELIMIT_BACKEND == "python", ratelimit.RATELIMIT_BACKEND
        """
    )
    ran = _python(script)
    assert ran.returncode == 0, ran.stderr
