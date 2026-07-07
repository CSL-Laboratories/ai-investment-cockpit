"""WP6 — the SEC-11 external witness (AnchorStore implementations).

The audit chain's in-DB watermark (``audit_meta``) cannot, on its own, stop a *replayable
rollback*: a keyless writer who captured a past ``(seq, head_seq_sig)`` pair can ``DELETE`` the
tail and replay that captured state; ``verify_chain()`` cannot tell a rollback-to-a-genuine-past
from the real past (see ``storage/audit.py`` SEC-11 CAVEAT). Only an OUT-OF-DB witness closes
it: an append-only record of ``(seq, head_hash)`` the local process cannot silently rewrite.
The performance gate and the mode oracle then require the verified in-DB head to be ``>=`` the
last external anchor and fail closed otherwise.

This module provides the concrete ``AnchorStore`` implementations (satisfying
``orchestrator.state.AnchorStore``) and ``post_head_anchor`` to publish the current verified
head. The INTEGRATOR wires the chosen store into ``compute_gate`` + ``ModeLadder.current()``;
this module does NOT edit those files.

HONESTY ABOUT THE GUARANTEE (do not overclaim):
  * ``InMemoryAnchorStore`` is a test/process-local stand-in — it dies with the process and a
    local writer with heap access can rewind it. It proves the monotonic contract, nothing more.
  * ``FileAnchorStore`` (append-only JSONL) is only a MIRROR. A keyless writer who can delete
    the DB tail can usually also truncate a local file — same trust domain. It is useful as a
    fast local cross-check and for staging, NOT as the real witness.
  * The REAL guarantee needs a witness in a DIFFERENT trust domain that the local writer cannot
    forge: a git commit Lucas reviews (history he would notice being rewritten) AND/OR a
    ``#audit-log`` Discord post (a server-side timestamp the local writer cannot backdate).
    ``GitAuditLogAnchorStore`` is the adapter SKETCH those plug into.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from orchestrator.state import EVENT_ANCHOR


class AnchorError(Exception):
    """A head could not be anchored (chain unverified, or the store refused a rewind)."""


class AnchorRewind(AnchorError):
    """An ``append`` tried to move the external witness BACKWARD (lower seq) or fork it (same
    seq, different hash) — refused. The witness is monotonic; a rewind is exactly the rollback
    it exists to catch."""


# ---- in-memory (process-local stand-in) -----------------------------------------------
class InMemoryAnchorStore:
    """Monotonic in-memory anchor. Satisfies ``orchestrator.state.AnchorStore``. Test/dev only
    — provides the append/latest contract, NOT durability or a real external trust boundary."""

    def __init__(self) -> None:
        self._seq: int | None = None
        self._hash: str | None = None
        self._lock = threading.Lock()

    def latest(self) -> "tuple[int, str] | None":
        with self._lock:
            if self._seq is None or self._hash is None:
                return None
            return (self._seq, self._hash)

    def append(self, seq: int, head_hash: str) -> None:
        with self._lock:
            _guard_monotonic(self._seq, self._hash, seq, head_hash)
            self._seq, self._hash = int(seq), head_hash


# ---- append-only local file (a MIRROR, not the real witness) --------------------------
class FileAnchorStore:
    """Append-only JSONL anchor mirror. Each line is ``{"seq", "head_hash"}``. ``latest()``
    returns the HIGHEST anchored seq. ``append`` refuses a rewind. See the module docstring:
    a local file shares the local writer's trust domain, so this is a fast cross-check/mirror,
    NOT the guarantee. Use ``GitAuditLogAnchorStore`` for the real out-of-domain witness."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> list[tuple[int, str]]:
        if not self._path.exists():
            return []
        out: list[tuple[int, str]] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                out.append((int(rec["seq"]), str(rec["head_hash"])))
        return out

    def latest(self) -> "tuple[int, str] | None":
        with self._lock:
            rows = self._read_all()
            if not rows:
                return None
            return max(rows, key=lambda r: r[0])

    def append(self, seq: int, head_hash: str) -> None:
        with self._lock:
            cur = None
            rows = self._read_all()
            if rows:
                cur = max(rows, key=lambda r: r[0])
            cur_seq = cur[0] if cur else None
            cur_hash = cur[1] if cur else None
            _guard_monotonic(cur_seq, cur_hash, seq, head_hash)
            if cur_seq == int(seq):
                return                                   # idempotent re-anchor of the same head
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"seq": int(seq), "head_hash": head_hash},
                                    sort_keys=True, separators=(",", ":")) + "\n")


def _guard_monotonic(cur_seq, cur_hash, seq, head_hash) -> None:
    seq = int(seq)
    if not head_hash:
        raise AnchorError("cannot anchor an empty head hash")
    if cur_seq is None:
        return
    if seq < cur_seq:
        raise AnchorRewind(f"anchor rewind refused: new seq {seq} < anchored {cur_seq}")
    if seq == cur_seq and head_hash != cur_hash:
        raise AnchorRewind(
            f"anchor fork refused: seq {seq} already anchored with a different head hash"
        )


# ---- publish the current verified head ------------------------------------------------
def post_head_anchor(st, *, record: bool = True, actor: str = "anchor") -> tuple[int, str]:
    """Read ``st.audit`` head, VERIFY the chain, and append the verified ``(seq, head_hash)`` to
    ``st.anchor_store``. Fail closed: an unverified chain is NOT anchored (that would sign off on
    a tampered state). When ``record`` is True, also append an ``EVENT_ANCHOR`` audit entry so
    the publish itself is on the tamper-evident chain (this advances the head PAST the anchored
    seq, which is fine — the invariant is head >= anchor). Returns the anchored ``(seq, hash)``.

    Raises ``AnchorError`` if there is no anchor store, the chain does not verify, or there is no
    head yet (nothing to anchor)."""
    if st.anchor_store is None:
        raise AnchorError("no anchor store configured on OrchestratorState")
    v = st.audit.verify_chain()
    if not v.ok:
        raise AnchorError(f"refusing to anchor an unverified chain: {v.error}")
    seq, head_hash = st.audit.head()
    if not head_hash or seq <= 0:
        raise AnchorError("no audit head to anchor yet")
    st.anchor_store.append(seq, head_hash)
    if record:
        st.audit.append(EVENT_ANCHOR, actor=actor, refs={"seq": seq},
                        payload={"seq": seq, "head_hash": head_hash})
    return (seq, head_hash)


# ---- the real out-of-domain witness: SKETCH -------------------------------------------
@runtime_checkable
class ExternalWitnessAdapter(Protocol):
    """The seam a real witness implements: publish a head to a trust domain the LOCAL writer
    cannot forge. Two concrete backends are intended (either or both):
      * a git commit of the head file (Lucas reviews history; a rewrite is visible), and
      * a ``#audit-log`` Discord post (a server-stamped time the local writer cannot backdate).
    """

    def publish(self, seq: int, head_hash: str) -> None:
        """Commit/post the head to the external domain. MUST be append-only and monotonic."""
        ...

    def read_latest(self) -> "tuple[int, str] | None":
        """Read the highest head the external domain has recorded (or None)."""
        ...


class GitAuditLogAnchorStore:
    """SKETCH of the production witness. Wraps one or more ``ExternalWitnessAdapter`` backends
    (git commit + ``#audit-log`` post). It is the ONLY implementation that provides the SEC-11
    guarantee against a KEY-HOLDER rollback, because its state lives outside the local writer's
    trust domain. Wiring the concrete git/Discord adapters is deferred (needs the deploy's git
    remote + bot credentials); the interface is pinned here so ``post_head_anchor`` and the
    gate/mode-oracle integration are written against the final shape today.

    NOTE: ``latest()`` reads back from the external domain (not a local cache) so the gate/mode
    oracle compares the in-DB head against a value the local writer could not have forged."""

    def __init__(self, *adapters: ExternalWitnessAdapter) -> None:
        if not adapters:
            raise AnchorError("GitAuditLogAnchorStore needs at least one external witness adapter")
        self._adapters = list(adapters)

    def latest(self) -> "tuple[int, str] | None":
        best: tuple[int, str] | None = None
        for a in self._adapters:
            got = a.read_latest()
            if got is None:
                continue
            if best is None or got[0] > best[0]:
                best = (int(got[0]), str(got[1]))
        return best

    def append(self, seq: int, head_hash: str) -> None:
        cur = self.latest()
        _guard_monotonic(cur[0] if cur else None, cur[1] if cur else None, seq, head_hash)
        for a in self._adapters:
            a.publish(int(seq), head_hash)
