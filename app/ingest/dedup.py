"""WP3 — bounded dedup by (source, article id). LRU-evicts past capacity."""
from __future__ import annotations

import copy
import importlib.util
import logging
from collections import OrderedDict

log = logging.getLogger(__name__)

_MISSING = object()      # distinct from None, which is the stored VALUE of every seen key
_I64_MIN = -2**63
_I64_MAX = 2**63 - 1
DEDUP_BACKEND = "python"
_NATIVE = None


def _load_native() -> None:
    """Use the Rust dedup when the extension is installed.

    A missing module keeps the pure-Python path. A present extension that
    fails to import is broken and the error propagates. A present module
    missing ``DedupCore`` is a partial wheel, not a silent Python fallback.
    The map is memory only. Per-call ``Dedup.add`` stays on the OrderedDict;
    ``dedup_replay`` is the batch path that uses the extension.
    """
    global _NATIVE, DEDUP_BACKEND
    if importlib.util.find_spec("cockpit_quant") is None:
        log.info("DEDUP_BACKEND=%s", DEDUP_BACKEND)
        return
    import cockpit_quant as native

    if not hasattr(native, "DedupCore"):
        raise ImportError("cockpit_quant is installed but does not provide DedupCore")
    _NATIVE = native
    DEDUP_BACKEND = "rust"
    log.info("DEDUP_BACKEND=%s", DEDUP_BACKEND)


def _rust_cap(capacity: int) -> bool:
    return (
        isinstance(capacity, int)
        and not isinstance(capacity, bool)
        and _I64_MIN <= capacity <= _I64_MAX
    )


def _core_key(key: str) -> str:
    """One UTF-8 identity for the Rust LRU.

    A lone surrogate is a ``str`` PyO3 cannot take as ``&str``. Escaping only
    those keys, and keys that already start with the escape mark, keeps one
    map: a later UTF-8 ``add`` still evicts the surrogate when it is oldest.
    Ascii ids that do not start with NUL are passed through.
    """
    if not key.startswith("\x00"):
        if key.isascii():
            return key
        try:
            key.encode("utf-8")
        except UnicodeEncodeError:
            pass
        else:
            return key
    # UTF-32 keeps a non-BMP character distinct from the surrogate pair that
    # UTF-16 would fold into the same bytes.
    return "\x00" + key.encode("utf-32", "surrogatepass").hex()


def _decode_core_key(stored: str) -> str:
    """Inverse of ``_core_key``. A pass-through key is stored unchanged."""
    if not stored.startswith("\x00"):
        return stored
    return bytes.fromhex(stored[1:]).decode("utf-32", "surrogatepass")


def _rebuild_core(capacity, keys):
    """A fresh extension map for ``keys``, or None when it cannot hold them.

    Non-str keys are legal on the OrderedDict and cannot be stored in the
    extension, so a mixed map has no core. The rebuilt core is a new object.
    """
    native = _NATIVE
    if native is None or not _rust_cap(capacity):
        return None
    if any(not isinstance(key, str) for key in keys):
        return None
    core = native.DedupCore(capacity)
    for key in keys:
        core.add(_core_key(key))
    return core


class Dedup:
    def __init__(self, capacity: int = 50_000):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._cap = capacity
        # Pollers call add/discard once per item. A per-call hop into
        # DedupCore is slower than this map, so the hot path does not attach
        # one. dedup_replay is the batch path. A core set by a caller is
        # rebuilt on copy so it is not shared.
        self._core = None

    def add(self, key: str) -> bool:
        """Return True if newly added (not a duplicate).

        Any hashable is accepted, with or without the extension installed.
        An unhashable key raises ``TypeError``, as an OrderedDict does.
        """
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        if len(self._seen) > self._cap:
            self._seen.popitem(last=False)
        return True

    def discard(self, key: str) -> bool:
        """ROLL BACK an ``add``. Returns whether the key was present.

        THE KEY MEANS "THIS WAS EMITTED", NOT "WE TRIED TO EMIT THIS". A poller must therefore
        be able to un-say it when the emit FAILS: ``add`` is a check-and-set (there is no
        race-free way to peek first), so the only way to keep the key's meaning honest under a
        TRANSIENT downstream failure is to take it back. Without this, a momentary
        outage (persist_events down, disk full, a DB lock) permanently burns the key and the
        item can NEVER be retried — a silent, unrecoverable evidence loss that the caller's
        counters record as a "drop" while the evidence itself is simply gone.

        BOUND (honest, small): if this key's ``add`` evicted the LRU tail, ``discard`` cannot
        resurrect the evicted key. The worst case is a re-emit of an item older than the whole
        capacity window — a bounded duplicate, never a loss. Losing evidence is the failure we
        refuse; a stale duplicate is not.
        """
        return self._seen.pop(key, _MISSING) is not _MISSING

    def __copy__(self) -> Dedup:
        """Independent map. A Rust core is rebuilt rather than shared."""
        return _restore_dedup(self._cap, list(self._seen), self._core is not None)

    def __deepcopy__(self, memo: dict) -> Dedup:
        cloned = Dedup.__new__(Dedup)
        memo[id(self)] = cloned
        cloned._cap = copy.deepcopy(self._cap, memo)
        keys = copy.deepcopy(list(self._seen), memo)
        cloned._seen = OrderedDict.fromkeys(keys)
        cloned._core = _rebuild_core(cloned._cap, keys) if self._core is not None else None
        return cloned

    def __reduce__(self):
        return (_restore_dedup, (self._cap, list(self._seen), self._core is not None))


def _restore_dedup(capacity, keys, rust_core: bool) -> Dedup:
    """Rebuild a dedup from its ordered keys. Used by copy and pickle."""
    seen = Dedup.__new__(Dedup)
    seen._cap = capacity
    seen._seen = OrderedDict.fromkeys(keys)
    seen._core = _rebuild_core(capacity, keys) if rust_core else None
    return seen


def _str_script(ops) -> bool:
    for op, key in ops:
        if op not in ("a", "d") or not isinstance(key, str):
            return False
    return True


def dedup_replay(capacity: int, ops: list[tuple[str, object]]) -> list[bool]:
    """Run an add/discard script.

    A script of ``str`` keys uses the extension in one call. A lone surrogate
    cannot cross as UTF-8, so that script is escaped and retried once. Any
    other hashable stays on the OrderedDict and matches ``Dedup.add`` instead
    of raising only on the extension. ``op`` is ``"a"`` or ``"d"``.
    """
    native = _NATIVE
    if native is not None and _rust_cap(capacity) and _str_script(ops):
        try:
            return list(native.dedup_replay(capacity, ops))
        except UnicodeEncodeError:
            encoded = [(op, _core_key(key)) for op, key in ops]
            return list(native.dedup_replay(capacity, encoded))
    seen = Dedup(capacity)
    out = []
    for op, key in ops:
        if op == "a":
            out.append(seen.add(key))
        elif op == "d":
            out.append(seen.discard(key))
        else:
            raise ValueError(f"unknown dedup op {op}")
    return out


_load_native()
