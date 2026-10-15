"""CSL-42 — Rust admission controls must match Python.

Skipped when the extension is not installed, which is the hosted Python gate.
``Dedup`` stays on OrderedDict, which won the one-key call the pollers make.
``DedupCore`` is compared against that class. The bucket's ``allow`` and
``allow_many`` are compared with ``python_allow``.
"""
from __future__ import annotations

import random

import pytest

from ingest.dedup import DEDUP_BACKEND, Dedup
from ingest.ratelimit import RATELIMIT_BACKEND, TokenBucket, python_allow

pytestmark = pytest.mark.skipif(
    DEDUP_BACKEND != "rust" or RATELIMIT_BACKEND != "rust",
    reason="cockpit_quant admission extension is not installed",
)


def _cores(capacity):
    import cockpit_quant

    shipped = Dedup(capacity)
    assert shipped._core is None
    return cockpit_quant.DedupCore(capacity), shipped


def _drive_bucket(bucket, times, *, python: bool):
    allowed = []
    for now in times:
        if python:
            allowed.append(python_allow(bucket, now))
        else:
            allowed.append(bucket.allow(now))
    return allowed


def test_dedup_matches_lru_eviction_and_discard():
    rust, python = _cores(2)
    script = ["a", "b", "a", "c", "a", "b"]
    for key in script:
        assert rust.add(key) is python.add(key)
    assert rust.discard("c") is python.discard("c")
    assert rust.discard("a") is python.discard("a")
    assert rust.add("c") is python.add("c")

    for cap in (0, -1, -50):
        rust, python = _cores(cap)
        assert rust.add("k") is python.add("k") is True
        assert rust.add("k") is python.add("k") is True
        assert rust.discard("k") is python.discard("k") is False

    rust, python = _cores(3)
    for key in ("a", "b", "c", "d"):
        assert rust.add(key) is python.add(key)
    assert rust.discard("a") is python.discard("a") is False
    assert rust.discard("c") is python.discard("c") is True
    assert rust.add("c") is python.add("c") is True


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


def test_the_shipped_dedup_keeps_ordered_dict_keys():
    """Pollers pass one key at a time. That call stays on OrderedDict.

    The extension still matches it, and refuses a non-str. The class does not,
    because an OrderedDict key was never limited to ``str``.
    """
    import cockpit_quant

    seen = Dedup(4)
    assert seen._core is None
    assert seen.add(1) is True
    with pytest.raises(TypeError):
        cockpit_quant.DedupCore(4).add(1)
    odd = Dedup(capacity=True)
    assert odd._core is None
    assert odd.add("a") is True


def test_one_allow_matches_python_for_a_float_clock():
    bucket = TokenBucket(rate_per_sec=2.0, capacity=1.0, now=0.0)
    twin = TokenBucket(rate_per_sec=2.0, capacity=1.0, now=0.0)
    assert bucket.allow(0.0) is python_allow(twin, 0.0) is False
    assert bucket.allow(0.5) is python_allow(twin, 0.5)
    assert bucket.tokens == twin.tokens
    assert bucket._last == twin._last


def test_allow_many_matches_including_a_backwards_clock():
    times = [0.0, 0.0, 0.4, 0.4, 1.0, 0.2, 3.0, 3.0, 3.2]
    rust = TokenBucket(rate_per_sec=2.0, capacity=1.5, now=0.0)
    python = TokenBucket(rate_per_sec=2.0, capacity=1.5, now=0.0)
    assert rust.allow_many(times) == _drive_bucket(python, times, python=True)
    assert rust.tokens == python.tokens
    assert rust._last == python._last

    rust.tokens = 0.25
    python.tokens = 0.25
    assert rust.allow_many([4.0]) == [python_allow(python, 4.0)]
    assert rust.tokens == python.tokens
    assert rust._last == python._last


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
