"""CSL-42 — Rust dedup and token bucket must match the Python admission controls.

Per-call ``Dedup.add`` / ``TokenBucket.allow`` stay on the OrderedDict and the
Python step: a poller calls them once per item, and one FFI hop is slower than
that work. Batch scripts use ``dedup_replay`` and ``token_bucket_replay``, which
call the extension when the values are ones it can store.

Parity cases that need the extension are skipped when it is not installed,
which is the hosted Python gate. ``COCKPIT_REQUIRE_RUST=1`` fails the file
instead of skipping, so a wheel that installs but does not activate admission
fails CI.
"""
from __future__ import annotations

import copy
import logging
import math
import os
import pickle
import random
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ingest.dedup import DEDUP_BACKEND, Dedup, _core_key, dedup_replay
from ingest.ratelimit import RATELIMIT_BACKEND, TokenBucket, python_allow, token_bucket_replay

# The rust.yml job sets this so a wheel that installs but does not activate
# fails the job. Hosted Python gates leave it unset and skip the parity cases.
_REQUIRE_RUST = os.environ.get("COCKPIT_REQUIRE_RUST") == "1"
if _REQUIRE_RUST and (DEDUP_BACKEND != "rust" or RATELIMIT_BACKEND != "rust"):
    raise RuntimeError("COCKPIT_REQUIRE_RUST=1 but the admission backend is python")

_needs_rust = pytest.mark.skipif(
    not _REQUIRE_RUST and (DEDUP_BACKEND != "rust" or RATELIMIT_BACKEND != "rust"),
    reason="cockpit_quant admission extension is not installed",
)


def _script(capacity, ops) -> list[bool]:
    seen = Dedup(capacity)
    seen._core = None
    out = []
    for op, key in ops:
        if op == "a":
            out.append(seen.add(key))
        else:
            out.append(seen.discard(key))
    return out


def _drive_bucket(bucket, times, *, python: bool):
    allowed = []
    for now in times:
        if python:
            allowed.append(python_allow(bucket, now))
        else:
            allowed.append(bucket.allow(now))
    return allowed


@_needs_rust
def test_dedup_replay_matches_lru_eviction_and_discard():
    ops = [("a", key) for key in ("a", "b", "a", "c", "a", "b")]
    ops += [("d", "c"), ("d", "a"), ("a", "c")]
    assert dedup_replay(2, ops) == _script(2, ops)

    for cap in (0, -1, -50):
        cap_ops = [("a", "k"), ("a", "k"), ("d", "k")]
        assert dedup_replay(cap, cap_ops) == _script(cap, cap_ops) == [True, True, False]

    ops = [("a", key) for key in ("a", "b", "c", "d")]
    ops += [("d", "a"), ("d", "c"), ("a", "c")]
    assert dedup_replay(3, ops) == _script(3, ops)


@_needs_rust
def test_dedup_replay_matches_a_random_script():
    import cockpit_quant

    rng = random.Random(7)
    keys = [f"k{i}" for i in range(30)]
    ops = []
    for _ in range(400):
        key = rng.choice(keys)
        ops.append(("d" if rng.random() < 0.25 else "a", key))
    replay = dedup_replay(11, ops)
    direct = list(cockpit_quant.dedup_replay(11, ops))
    assert replay == direct == _script(11, ops)


def test_a_non_str_key_matches_on_both_backends():
    """A hashable that is not a str is admitted the same way with or without a core.

    The extension's ``DedupCore.add`` only accepts ``str``. Per-call admission
    does not use it, so an int or a tuple is not a TypeError on one backend.
    """
    plain = Dedup(4)
    assert plain._core is None
    assert plain.add(1) is True
    assert plain.add(1) is False
    assert plain.add(("src", "id")) is True
    assert plain.discard(("src", "id")) is True
    assert plain.discard(("src", "id")) is False
    with pytest.raises(TypeError):
        plain.add([])

    ops = [("a", 1), ("a", 1), ("d", 1), ("a", ("k", 2)), ("d", ("k", 2))]
    assert dedup_replay(4, ops) == _script(4, ops) == [True, False, True, True, True]
    with pytest.raises(ValueError, match="unknown dedup op"):
        dedup_replay(2, [("z", "a")])


@_needs_rust
def test_a_non_str_key_agrees_when_a_rust_core_is_attached(monkeypatch):
    import cockpit_quant

    def boom(*_args, **_kwargs):
        raise AssertionError("non-str replay entered the extension")

    monkeypatch.setattr(cockpit_quant, "dedup_replay", boom)
    rust = Dedup(4)
    rust._core = cockpit_quant.DedupCore(4)
    python = Dedup(4)
    for key in (1, ("src", "id"), frozenset({"a"})):
        assert rust.add(key) is python.add(key) is True
        assert rust.add(key) is python.add(key) is False
        assert rust.discard(key) is python.discard(key) is True
    with pytest.raises(TypeError):
        rust.add([])
    with pytest.raises(TypeError):
        python.add([])
    assert dedup_replay(4, [("a", 1), ("d", 1)]) == [True, True]
    odd = Dedup(capacity=True)
    assert odd._core is None
    assert odd.add("a") is True


def _assert_copies_are_independent(seen: Dedup, *, core: bool) -> None:
    seen.add("a")
    seen.add("b")
    seen.add("a")
    if core:
        for key in seen._seen:
            assert seen._core.add(_core_key(key)) is True
    clones = [
        copy.copy(seen),
        copy.deepcopy(seen),
        pickle.loads(pickle.dumps(seen)),
        copy.deepcopy({"seen": seen})["seen"],
    ]
    for clone in clones:
        assert clone is not seen
        assert clone._seen is not seen._seen
        assert list(clone._seen) == ["b", "a"]
        if core:
            assert clone._core is not None
            assert clone._core is not seen._core
            assert clone._core.add(_core_key("b")) is False
            assert seen._core.add(_core_key("b")) is False
            assert clone._core.discard(_core_key("b")) is True
        else:
            assert clone._core is None
        assert clone.add("b") is False
        assert clone.add("c") is True
        assert clone.discard("a") is True
    assert seen.add("b") is False
    assert seen.add("a") is False
    assert seen.add("c") is True
    if core:
        assert seen._core.discard(_core_key("b")) is True


def test_a_python_dedup_copy_is_independent_and_picklable():
    _assert_copies_are_independent(Dedup(3), core=False)


@_needs_rust
def test_a_rust_core_copy_is_independent_and_picklable():
    import cockpit_quant

    seen = Dedup(3)
    seen._core = cockpit_quant.DedupCore(3)
    _assert_copies_are_independent(seen, core=True)

    mixed = Dedup(4)
    mixed._core = cockpit_quant.DedupCore(4)
    mixed._core.add("z")
    mixed.add(1)
    mixed.add("a")
    cloned = pickle.loads(pickle.dumps(mixed))
    assert list(cloned._seen) == [1, "a"]
    assert cloned._core is None
    assert cloned._seen is not mixed._seen
    assert cloned.add(1) is False
    assert mixed.add(1) is False
    assert mixed._core.add("z") is False


@_needs_rust
def test_per_call_dedup_does_not_attach_a_rust_core():
    seen = Dedup(50_000)
    assert seen._core is None
    assert seen.add("alpaca:1") is True
    assert seen.add("alpaca:1") is False
    assert seen.discard("alpaca:1") is True
    assert seen._core is None


@_needs_rust
def test_token_bucket_matches_including_a_backwards_clock():
    times = [0.0, 0.0, 0.4, 0.4, 1.0, 0.2, 3.0, 3.0, 3.2]
    rust = TokenBucket(rate_per_sec=2.0, capacity=1.5, now=0.0)
    python = TokenBucket(rate_per_sec=2.0, capacity=1.5, now=0.0)
    assert _drive_bucket(rust, times, python=False) == _drive_bucket(python, times, python=True)
    assert rust.tokens == python.tokens
    assert rust._last == python._last
    allowed, tokens, last = token_bucket_replay(2.0, 1.5, 0.0, 0.0, times)
    fresh = TokenBucket(rate_per_sec=2.0, capacity=1.5, now=0.0)
    assert allowed == _drive_bucket(fresh, times, python=True)
    assert tokens == fresh.tokens
    assert last == fresh._last

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
    allowed, tokens, last = token_bucket_replay(1.5, 3.0, 0.0, 0.0, times)
    direct, direct_tokens, direct_last = cockpit_quant.token_bucket_replay(1.5, 3.0, 0.0, 0.0, times)
    python = TokenBucket(rate_per_sec=1.5, capacity=3.0, now=0.0)
    for now, bit, direct_bit in zip(times, allowed, direct):
        assert bit is direct_bit is python_allow(python, now)
    assert tokens == direct_tokens == python.tokens
    assert last == direct_last == python._last


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
    import cockpit_quant

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
        allowed, new_tokens, new_last = cockpit_quant.token_bucket_allow(
            rate, capacity, tokens, last, now
        )
        replayed, replay_tokens, replay_last = token_bucket_replay(
            rate, capacity, tokens, last, [now]
        )
        python = TokenBucket(rate_per_sec=rate, capacity=capacity, now=last)
        python.tokens = tokens
        assert python_allow(python, now) is allowed is replayed[0]
        _same_number(new_tokens, python.tokens)
        _same_number(replay_tokens, python.tokens)
        _same_number(new_last, python._last)
        _same_number(replay_last, python._last)
        hot = TokenBucket(rate_per_sec=rate, capacity=capacity, now=last)
        hot.tokens = tokens
        assert hot.allow(now) is allowed
        _same_number(hot.tokens, python.tokens)
        _same_number(hot._last, python._last)


@_needs_rust
def test_a_lone_surrogate_shares_one_lru_with_utf8_keys(monkeypatch):
    import cockpit_quant

    calls = {"n": 0}
    real = cockpit_quant.dedup_replay

    def wrap(capacity, ops):
        calls["n"] += 1
        return real(capacity, ops)

    monkeypatch.setattr(cockpit_quant, "dedup_replay", wrap)
    key = "\ud800"
    ops = [("a", "a"), ("a", key), ("a", "b"), ("a", "a"), ("d", key)]
    assert dedup_replay(2, ops) == _script(2, ops) == [True, True, True, True, False]

    ops = [
        ("a", "a"),
        ("a", "b"),
        ("a", "a"),
        ("a", key),
        ("a", "a"),
        ("d", key),
        ("a", "b"),
    ]
    assert dedup_replay(2, ops) == _script(2, ops) == [True, True, False, True, False, True, True]

    folded = "alpaca:benzinga:\ud800\U0001f600"
    pair = "alpaca:benzinga:\ud800\ud83d\ude00"
    assert folded != pair
    assert _core_key(folded) != _core_key(pair)
    ops = [("a", folded), ("a", pair), ("a", "\x00" + folded), ("a", "\x00" + pair)]
    assert dedup_replay(4, ops) == _script(4, ops) == [True, True, True, True]

    marked = "\x00a"
    ops = [("a", marked), ("a", "b"), ("a", "c"), ("a", marked)]
    assert dedup_replay(2, ops) == _script(2, ops) == [True, True, True, True]
    # Three scripts contain a surrogate: the extension rejects the first pass
    # and the escaped retry is the one that runs. The NUL id is valid UTF-8.
    assert calls["n"] == 7

    # Per-call stays on the OrderedDict, including a lone surrogate.
    seen = Dedup(2)
    assert seen._core is None
    assert seen.add(key) is True
    assert seen.add(key) is False


def test_a_capacity_past_the_float_range_stays_on_python(monkeypatch):
    if RATELIMIT_BACKEND == "rust":
        import cockpit_quant

        def boom(*_args, **_kwargs):
            raise AssertionError("huge capacity entered the extension")

        monkeypatch.setattr(cockpit_quant, "token_bucket_replay", boom)
        monkeypatch.setattr(cockpit_quant, "token_bucket_allow", boom)
    huge = 10**400
    rust = TokenBucket(rate_per_sec=1, capacity=huge, now=0)
    python = TokenBucket(rate_per_sec=1, capacity=huge, now=0)
    assert rust.allow(1) is python_allow(python, 1) is True
    assert rust.tokens == python.tokens
    assert rust._last == python._last
    allowed, tokens, last = token_bucket_replay(1, huge, 0, 0, [1])
    assert allowed == [True]
    assert tokens == rust.tokens
    assert last == rust._last


def test_an_int_rust_cannot_hold_stays_off_the_extension():
    from ingest.ratelimit import _exact_f64

    assert _exact_f64(2**53 + 1) is None
    assert _exact_f64(10**400) is None
    assert _exact_f64(2) == 2.0
    assert _exact_f64(1.5) == 1.5
    assert _exact_f64(True) is None


@_needs_rust
def test_an_exact_integer_capacity_matches_python(monkeypatch):
    import cockpit_quant

    seen = {}
    real = cockpit_quant.token_bucket_replay

    def wrap(*args):
        seen["args"] = args
        return real(*args)

    monkeypatch.setattr(cockpit_quant, "token_bucket_replay", wrap)
    allowed, tokens, last = token_bucket_replay(1, 2, 0, 0, [0, 1])
    python = TokenBucket(rate_per_sec=1, capacity=2, now=0)
    assert allowed[0] is python_allow(python, 0) is False
    assert allowed[1] is python_allow(python, 1) is True
    assert tokens == python.tokens
    assert last == python._last
    assert seen["args"][0] == 1.0
    assert seen["args"][1] == 2.0

    hot = TokenBucket(rate_per_sec=1, capacity=2, now=0)
    ref = TokenBucket(rate_per_sec=1, capacity=2, now=0)
    assert hot.allow(0) is python_allow(ref, 0) is False
    assert hot.allow(1) is python_allow(ref, 1) is True


@_needs_rust
def test_per_call_allow_does_not_enter_the_extension(monkeypatch):
    import cockpit_quant

    def boom(*_args, **_kwargs):
        raise AssertionError("per-call allow entered the extension")

    monkeypatch.setattr(cockpit_quant, "token_bucket_allow", boom)
    hot = TokenBucket(rate_per_sec=1.0, capacity=2.0, now=0.0)
    ref = TokenBucket(rate_per_sec=1.0, capacity=2.0, now=0.0)
    for now in (0.0, 0.0, 1.0, 0.5):
        assert hot.allow(now) is python_allow(ref, now)
    assert hot.tokens == ref.tokens
    assert hot._last == ref._last


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
