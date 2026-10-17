"""WP3 — bounded dedup by (source, article id). LRU-evicts past capacity."""
from __future__ import annotations

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
    The map is memory only.
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
    return isinstance(capacity, int) and not isinstance(capacity, bool) and _I64_MIN <= capacity <= _I64_MAX


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


class Dedup:
    def __init__(self, capacity: int = 50_000):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._cap = capacity
        self._core = None
        native = _NATIVE
        if native is not None and _rust_cap(capacity):
            self._core = native.DedupCore(capacity)

    def _release_to_python(self, key) -> None:
        """Move the one LRU onto ``OrderedDict`` for a non-str hashable.

        Rust stores strings. Python equality is what an ``OrderedDict`` uses,
        including a key that compares equal to a ``str`` already in the window.
        ``hash`` raises the same ``TypeError`` a dict lookup would, before the
        core is dropped.
        """
        hash(key)
        seen: OrderedDict[str, None] = OrderedDict()
        for stored in self._core.keys():
            seen[_decode_core_key(stored)] = None
        self._seen = seen
        self._core = None

    def add(self, key: str) -> bool:
        """Return True if newly added (not a duplicate)."""
        if self._core is not None:
            if isinstance(key, str):
                return self._core.add(_core_key(key))
            self._release_to_python(key)
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
        if self._core is not None:
            if isinstance(key, str):
                return self._core.discard(_core_key(key))
            self._release_to_python(key)
        return self._seen.pop(key, _MISSING) is not _MISSING

    def __reduce__(self):
        """Rebuild an independent window for pickle, ``copy.copy``, and ``copy.deepcopy``.

        The extension object is not copied: a shared ``_core`` would let one
        copy's ``add`` change the other's duplicates, and the pyclass itself
        does not pickle or deep-copy. The Python path uses the same rebuild.
        """
        if self._core is not None:
            return (_restore_dedup, (self._cap, tuple(self._core.keys()), True))
        return (_restore_dedup, (self._cap, tuple(self._seen), False))


def _restore_dedup(capacity, keys, from_core: bool) -> Dedup:
    seen = Dedup(capacity)
    if not from_core:
        seen._core = None
        for key in keys:
            seen._seen[key] = None
        return seen
    if seen._core is None:
        for stored in keys:
            seen._seen[_decode_core_key(stored)] = None
        return seen
    for stored in keys:
        seen._core.add(stored)
    return seen


_load_native()
