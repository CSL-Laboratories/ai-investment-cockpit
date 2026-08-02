"""WP2 — the write-once raw filing store (Invariant 5).

Raw SEC filing *bytes* (the actual document as fetched) are stored on disk under
``<root>/<accession>/`` keyed by accession number, read back BYTE-IDENTICAL, and never
placed in any derived table. This is the anchor for mechanical citation verification
(WP5's resolver re-reads these exact bytes at recorded offsets), so byte fidelity is the
invariant.

Three shapes are ported from the cockpit ``parquet_store.py`` (re-implemented, not
copied); everything else about that module is stripped:

  * **Write-once.** A second write to an existing accession key RAISES (``if exists:
    raise``). The accession directory's presence is the key's presence.
  * **Hash-bind-then-verify.** ``write`` hashes the raw bytes (SHA-256), persists them,
    then re-reads and re-hashes to confirm the on-disk bytes match before returning — so a
    truncated/garbled write is caught at ingest, and any post-ingest edit is caught by
    ``verify``/``resolve`` below the parser and citation layer.
  * **Verify re-reads-and-re-hashes.** ``verify``/``resolve`` re-read the persisted bytes
    and compare their hash constant-time against the hash bound at write.

STRIPPED from the cockpit pattern-source (docs/cockpit-port-map.md WP2, highest-fidelity
copy risk #1): pyarrow/parquet entirely, ``date/symbol`` keying, and the logical-record
round-trip. The brew stores RAW BYTES on a plain filesystem keyed by ACCESSION — not a
columnar record round-trip. NO derived table stores a raw document body.
"""
from __future__ import annotations

import hashlib
import hmac
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

# On-disk layout inside each accession directory.
_BLOB_NAME = "raw.bin"     # the raw filing bytes, byte-identical
_HASH_NAME = "sha256"      # the SHA-256 hex digest bound at write time
DEFAULT_MIN_FREE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class RawRef:
    """A resolvable pointer to a stored raw filing.

    ``ref`` is the string a ``Filing.raw_ref`` column holds (the accession number — the
    store's key). It is a POINTER, never the raw bytes. ``resolve``/``read`` turn it back
    into the byte-identical document.
    """

    accession: str
    sha256: str
    path: str

    @property
    def ref(self) -> str:
        """The value stored in ``Filing.raw_ref`` (the accession key)."""
        return self.accession


class RawStoreError(RuntimeError):
    """Raised when a stored raw filing fails its integrity check."""


class RawStoreCapacityError(RawStoreError):
    """The configured free-disk reserve would be crossed by a raw write."""


class RawStore:
    """Write-once, hash-bound, byte-identical raw filing store keyed by accession.

    :param root: the raw-store root directory (production: ``settings.raw_dir`` ==
        ``<data_dir>/raw``). Per-accession subdirectories are created under it.
    """

    def __init__(self, root, *, min_free_bytes: int = DEFAULT_MIN_FREE_BYTES):
        if min_free_bytes < 0:
            raise ValueError("min_free_bytes must be non-negative")
        self._root = Path(root)
        self._min_free_bytes = min_free_bytes

    # ---- paths -------------------------------------------------------------
    @staticmethod
    def _key(accession: str) -> str:
        """Accept an opaque accession key, never a filesystem path.

        Real SEC accession numbers are simple strings.  The store deliberately does not impose
        their exact regular expression here so test doubles and future data sources remain
        possible, but untrusted citation metadata must never escape the raw-store root through
        path traversal or drive-qualified keys.
        """
        if not isinstance(accession, str):
            raise TypeError(f"raw-store key must be str, got {type(accession).__name__}")
        if (
            not accession
            or accession != accession.strip()
            or accession in {".", ".."}
            or any(character in accession for character in ("/", "\\", ":", "\x00"))
        ):
            raise ValueError(f"invalid raw-store key {accession!r}")
        return accession

    def _dir(self, accession: str) -> Path:
        return self._root / self._key(accession)

    def _blob_path(self, accession: str) -> Path:
        return self._dir(accession) / _BLOB_NAME

    def _hash_path(self, accession: str) -> Path:
        return self._dir(accession) / _HASH_NAME

    # ---- queries -----------------------------------------------------------
    def exists(self, accession: str) -> bool:
        """True once a raw filing has been written for ``accession`` (the write-once key)."""
        return self._dir(accession).exists()

    def stored_hash(self, accession: str) -> str:
        """The SHA-256 hex digest bound to the raw bytes at write time."""
        return self._hash_path(accession).read_text(encoding="utf-8").strip()

    def _ensure_capacity(self, incoming_bytes: int) -> None:
        free = shutil.disk_usage(self._root).free
        required = self._min_free_bytes + incoming_bytes
        if free < required:
            raise RawStoreCapacityError(
                f"raw store disk reserve would be crossed: {free} bytes free, "
                f"{incoming_bytes} incoming, {self._min_free_bytes} reserve required"
            )

    @staticmethod
    def _write_staged(staging: Path, raw: bytes, digest: str, accession: str) -> None:
        blob = staging / _BLOB_NAME
        blob.write_bytes(raw)
        # Hash-bind-then-verify before the directory becomes visible under the accession.
        if hashlib.sha256(blob.read_bytes()).hexdigest() != digest:
            raise RawStoreError(
                f"raw store write-back mismatch for accession {accession!r} "
                "(on-disk bytes do not match the hash of the input)"
            )
        (staging / _HASH_NAME).write_text(digest, encoding="utf-8")

    # ---- write-once --------------------------------------------------------
    def write(self, accession: str, data: bytes) -> RawRef:
        """Store the raw filing ``data`` under ``accession`` exactly once.

        Raises ``FileExistsError`` on a second write to an existing accession key
        (write-once), ``TypeError`` if ``data`` is not bytes, and ``RawStoreError`` if the
        on-disk bytes fail the immediate hash-bind-then-verify read-back.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(
                f"raw filing body must be bytes, got {type(data).__name__} "
                "(the raw store holds raw document BYTES, not decoded text)"
            )
        d = self._dir(accession)
        if d.exists():  # write-once: the accession key already has a body
            raise FileExistsError(
                f"raw filing already stored (write-once): accession {accession!r}"
            )
        raw = bytes(data)
        digest = hashlib.sha256(raw).hexdigest()
        self._root.mkdir(parents=True, exist_ok=True)
        self._ensure_capacity(len(raw))

        # Publish a complete directory with one same-filesystem rename. A crash before the
        # rename can leave only a hidden staging directory, never a partial accession key
        # that wedges all future retries.
        staging = Path(tempfile.mkdtemp(prefix=f".{self._key(accession)}.", dir=self._root))
        try:
            self._write_staged(staging, raw, digest, accession)
            try:
                staging.rename(d)
            except OSError as exc:
                if d.exists():
                    raise FileExistsError(
                        f"raw filing already stored (write-once): accession {accession!r}"
                    ) from exc
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return RawRef(
            accession=accession,
            sha256=digest,
            path=str(self._blob_path(accession)),
        )

    # ---- read / verify -----------------------------------------------------
    def read(self, accession: str) -> bytes:
        """Return the stored raw bytes for ``accession`` (no integrity check).

        Raises ``FileNotFoundError`` if nothing is stored under the key.
        """
        blob = self._blob_path(accession)
        if not blob.exists():
            raise FileNotFoundError(f"no raw filing stored for accession {accession!r}")
        return blob.read_bytes()

    def verify(self, accession: str) -> bool:
        """Re-read the persisted bytes and confirm they still hash to the digest bound at
        write time. Detects any post-ingest edit below the parser/citation layer."""
        recomputed = hashlib.sha256(self.read(accession)).hexdigest()
        return hmac.compare_digest(recomputed, self.stored_hash(accession))

    def resolve(self, ref) -> bytes:
        """Resolve a ``Filing.raw_ref`` (accession string, or a ``RawRef``) to its
        byte-identical raw document, verifying integrity first.

        Raises ``RawStoreError`` if the stored bytes fail their hash check.
        """
        accession = ref.ref if isinstance(ref, RawRef) else str(ref)
        # Verify and return the same disk read.  ``verify()->read()`` doubled full-file I/O and
        # left a small TOCTOU window between the verified bytes and the returned bytes.
        raw = self.read(accession)
        recomputed = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(recomputed, self.stored_hash(accession)):
            raise RawStoreError(
                f"raw store integrity check FAILED for accession {accession!r} "
                "(stored bytes were modified after ingest)"
            )
        return raw
