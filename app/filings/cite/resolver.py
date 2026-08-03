"""WP5 — mechanical citation resolver (Invariant 1, fail-closed).

The resolver is the byte-for-byte re-verification step of the deterministic citation
chain (claim -> Delta -> Citation -> raw filing bytes at accession+offset). It re-reads
the write-once raw store at a ``Citation``'s recorded offsets and confirms the stored
snippet is reproduced *exactly* — otherwise the citation is not trustworthy and MUST NOT
back a published claim.

THE ANCHOR CONTRACT (WP4, load-bearing) a citation must satisfy to resolve:

    raw_store.resolve(raw_ref)[byte_start:byte_end].decode("utf-8") == snippet
    byte_end - byte_start == len(snippet.encode("utf-8"))

Fail-closed discipline (Invariant 12): ANY problem — a missing raw blob, a raw-store
integrity failure (``RawStoreError``: the stored bytes were edited after ingest), an
out-of-range or inverted offset, a byte-length mismatch, an undecodable slice, or a text
mismatch — yields ``ok=False`` with a DISTINCT reason. No exception is ever allowed to
escape as a pass, and an unresolvable citation is never treated as ``ok``. Determinism
(no LLM, no subprocess, no network) mirrors the WP4 core.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from filings.raw_store import RawStoreError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

    from schemas.models.citation import Citation


@dataclass(frozen=True)
class ResolveResult:
    """Outcome of mechanically re-verifying one ``Citation`` against the raw store.

    :ivar ok: ``True`` IFF the snippet was reproduced byte-for-byte at the recorded offsets.
    :ivar citation_id: the citation this result is about.
    :ivar reason: a distinct, stable failure code; ``None`` IFF ``ok`` (reason is None iff ok).
    """

    ok: bool
    citation_id: str
    reason: str | None = None


def _raw_ref_key(ref: object) -> str:
    """Normalize the key the write-once store uses without constraining custom test stores."""
    return str(getattr(ref, "ref", ref))


class _OneRefCache:
    """A minimal store facade that resolves one raw key at most once.

    ``resolve_all`` creates one facade per raw-ref group, so the full raw blob can be released
    before moving to the next filing.  Delegating every other key directly preserves the public
    ``RawStore`` seam and the resolver's existing failure taxonomy.
    """

    def __init__(self, store: Any, key: str):
        self._store = store
        self._key = key
        self._loaded = False
        self._raw: Any = None
        self._error: Exception | None = None

    def resolve(self, ref: object) -> Any:
        if _raw_ref_key(ref) != self._key:
            return self._store.resolve(ref)
        if not self._loaded:
            self._loaded = True
            try:
                self._raw = self._store.resolve(ref)
            except Exception as exc:  # cached so every citation sees the same store failure
                self._error = exc
        if self._error is not None:
            raise self._error
        return self._raw


def resolve_citation(citation: Citation, raw_store: Any) -> ResolveResult:
    """Mechanically re-verify one citation against the raw store (fail-closed).

    Returns ``ok=True`` only when, re-reading the raw store fresh, every clause of the
    anchor contract holds. Every distinct failure mode maps to a distinct ``reason``; no
    exception escapes (a resolver that raised would be a silent pass elsewhere).
    """
    citation_id = getattr(citation, "citation_id", None) or "<unknown>"
    try:
        raw_ref = citation.raw_ref
        snippet = citation.snippet
        byte_start = citation.byte_start
        byte_end = citation.byte_end
        accession_no = getattr(citation, "accession_no", None)

        # 0) Attribution consistency (Inv 1): the raw bytes MUST come from the same filing the
        #    citation is attributed to. RawStore keys by accession, so raw_ref == accession_no
        #    by the WP2/WP4 contract; a citation whose raw_ref names a DIFFERENT filing than its
        #    accession_no would reproduce a REAL snippet from the WRONG document — a byte-perfect
        #    resolve with a false attribution. Reject it before reading anything (fail-closed).
        if accession_no is not None and str(raw_ref) != str(accession_no):
            return ResolveResult(False, citation_id, "raw-ref-accession-mismatch")

        # 1) Fetch the byte-identical raw blob WITH its integrity check. resolve() re-hashes
        #    the stored bytes and raises RawStoreError if they were modified after ingest.
        try:
            blob = raw_store.resolve(raw_ref)
        except FileNotFoundError:
            return ResolveResult(False, citation_id, "raw-store-missing")
        except RawStoreError:
            return ResolveResult(False, citation_id, "raw-store-integrity-failed")
        except Exception:  # noqa: BLE001 - any store failure is fail-closed, never a pass
            return ResolveResult(False, citation_id, "raw-store-error")

        if not isinstance(blob, (bytes, bytearray)):
            return ResolveResult(False, citation_id, "raw-store-not-bytes")

        # 2) Offsets must be a valid half-open [start, end) span inside the blob. Python
        #    slicing silently clamps out-of-range indices, so validate BEFORE slicing.
        if not isinstance(byte_start, int) or not isinstance(byte_end, int):
            return ResolveResult(False, citation_id, "offset-not-int")
        if byte_start < 0 or byte_end < 0:
            return ResolveResult(False, citation_id, "offset-negative")
        if byte_start > byte_end:
            return ResolveResult(False, citation_id, "offset-inverted")
        # A citation must quote at least one byte. A zero-length span (byte_start == byte_end,
        # snippet == "") otherwise passes vacuously — the byte-length clause holds (0 == 0) and
        # the empty slice decodes to "" — so a citation that cites NOTHING would verify (WP5 4c
        # round-3). Reject it fail-closed.
        if byte_start == byte_end:
            return ResolveResult(False, citation_id, "empty-span")
        if byte_end > len(blob):
            return ResolveResult(False, citation_id, "offset-out-of-range")

        # 3) Byte length of the recorded span must equal the snippet's utf-8 byte length.
        if not isinstance(snippet, str):
            return ResolveResult(False, citation_id, "snippet-not-str")
        expected_len = len(snippet.encode("utf-8"))
        if (byte_end - byte_start) != expected_len:
            return ResolveResult(False, citation_id, "byte-length-mismatch")

        # 4) The slice must decode as utf-8 ...
        segment = bytes(blob[byte_start:byte_end])
        try:
            decoded = segment.decode("utf-8")
        except UnicodeDecodeError:
            return ResolveResult(False, citation_id, "snippet-decode-error")

        # 5) ... and reproduce the stored snippet byte-for-byte.
        if decoded != snippet:
            return ResolveResult(False, citation_id, "snippet-mismatch")

        return ResolveResult(True, citation_id, None)
    except Exception:  # noqa: BLE001 - catch-all: an unresolvable citation is NEVER a pass
        return ResolveResult(False, citation_id, "unexpected-error")


def resolve_all(citations: Iterable[Citation], raw_store: Any) -> list[ResolveResult]:
    """Resolve every citation in input order, reading each raw filing at most once.

    The claim gate commonly checks hundreds of citations from the same one or two filings.  A
    group-local cache preserves the single-citation resolver's validation and failure reasons,
    while avoiding repeated full-file hash checks and retaining only one raw filing in memory at
    a time.
    """
    rows = list(citations)
    groups: dict[str, list[tuple[int, Citation]]] = defaultdict(list)
    results: dict[int, ResolveResult] = {}
    for index, citation in enumerate(rows):
        try:
            groups[_raw_ref_key(getattr(citation, "raw_ref", None))].append((index, citation))
        except Exception:  # preserve resolve_citation's fail-closed behavior for malformed input
            results[index] = resolve_citation(citation, raw_store)

    for key, group in groups.items():
        cached_store = _OneRefCache(raw_store, key)
        for index, citation in group:
            results[index] = resolve_citation(citation, cached_store)
    return [results[index] for index in range(len(rows))]
