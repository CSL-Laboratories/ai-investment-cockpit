"""WP5 acceptance — CORRUPTION DETECTION (scaffold WP5 Accept clause 2; playbook G5).

Deliberately corrupt a citation and assert BOTH that the resolver detects it (ok=False with a
distinct reason, fail-closed) AND that the claim gate marks the WHOLE brief unpublishable
(all-or-nothing, Inv 1 + 12). Four independent corruptions:

  (a) shift byte_start/byte_end by +/-1        -> offset drift, snippet no longer reproduced
  (b) swap in a DIFFERENT accession's raw_ref   -> cites the wrong filing's bytes
  (c) truncate/extend the snippet               -> byte-length no longer matches the span
  (d) mutate the stored raw bytes (RawStoreError) -> integrity tamper below the citation layer

Each uses a fresh small 2-delta / 2-accession set (the two NVDA 8-Ks) so a corruption is
isolated and the second, valid accession is available for the raw_ref swap.

Recorded fixtures only — NO live sec.gov, NO LLM.
"""
from __future__ import annotations

import pytest

from filings.raw_store import RawStoreError
from tests.filings import _wp5_support as W


def _fresh(tmp_path, name):
    """A fresh (cite surface, deltas, valid citations, filings, store) tuple."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / name)
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    # Baseline sanity: the untouched set resolves and is approvable.
    assert all(r.ok for r in C.resolver.resolve_all(citations, store))
    assert C.claim_gate.evaluate(deltas, citations, store).approvable is True
    return C, deltas, citations, filings, store


def _assert_blocked(C, deltas, citations, store, victim):
    """The victim citation fails the resolver AND the whole brief is unpublishable."""
    r = C.resolver.resolve_citation(victim, store)
    assert r.ok is False, "resolver passed a corrupted citation (must fail closed)"
    assert isinstance(r.reason, str) and r.reason, "a failed resolve must carry a reason"
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is False, (
        "one corrupted citation must block the WHOLE brief (all-or-nothing, Inv 1+12)"
    )
    assert gate.failures, "an unpublishable brief must enumerate its failures"
    return r.reason


def test_offset_shift_is_detected_and_blocks(tmp_path):
    """(a) Offset drift: shift the span by +1 in both directions (EQUAL length, so a
    length-only check would miss it) — the resolver must re-read the bytes and reject."""
    C, deltas, citations, _filings, store = _fresh(tmp_path, "shift")
    # Pick a citation for which a +1 window shift genuinely changes the decoded bytes
    # (guaranteed for real prose; searched to avoid any pathological repeated-byte flake).
    victim = None
    for c in citations:
        blob = store.resolve(c.raw_ref)
        if 0 <= c.byte_start + 1 and c.byte_end + 1 <= len(blob):
            if blob[c.byte_start + 1:c.byte_end + 1].decode("utf-8", "replace") != c.snippet:
                victim = c
                break
    assert victim is not None, "no shiftable citation found (fixture too small?)"
    victim.byte_start += 1
    victim.byte_end += 1
    _assert_blocked(C, deltas, citations, store, victim)


def test_wrong_accession_rawref_is_detected_and_blocks(tmp_path):
    """(b) Point raw_ref at a DIFFERENT (valid, present) accession — the offsets now index
    the wrong filing's bytes, so the snippet cannot be reproduced."""
    C, deltas, citations, _filings, store = _fresh(tmp_path, "wrongacc")
    victim = citations[0]
    other = next(c.raw_ref for c in citations if c.raw_ref != victim.raw_ref)
    assert store.exists(other), "the swapped-in accession must be present in the store"
    victim.raw_ref = other
    _assert_blocked(C, deltas, citations, store, victim)


def test_snippet_length_mismatch_is_detected_and_blocks(tmp_path):
    """(c) Extend the snippet so byte_end-byte_start no longer equals its utf-8 byte length."""
    C, deltas, citations, _filings, store = _fresh(tmp_path, "extend")
    victim = citations[0]
    victim.snippet = victim.snippet + " EXTRA-FABRICATED-TAIL"
    _assert_blocked(C, deltas, citations, store, victim)


def test_truncated_snippet_is_detected_and_blocks(tmp_path):
    """(c') Truncate the snippet — same length-mismatch failure, opposite direction."""
    C, deltas, citations, _filings, store = _fresh(tmp_path, "truncate")
    victim = citations[0]
    assert len(victim.snippet) > 1
    victim.snippet = victim.snippet[:-1]
    _assert_blocked(C, deltas, citations, store, victim)


def test_tampered_raw_bytes_raise_and_block(tmp_path):
    """(d) Mutate the stored raw blob below the integrity layer. ``RawStore.resolve`` must
    raise ``RawStoreError``; the resolver must catch it (fail-closed, never a pass) and the
    gate must block. The OTHER, untampered citation still resolves."""
    C, deltas, citations, _filings, store = _fresh(tmp_path, "tamper")
    victim = citations[0]
    root = tmp_path / "tamper"
    path = W.blob_path(root, victim.raw_ref)
    original = path.read_bytes()
    path.write_bytes(original + b"TAMPER")  # hash no longer matches -> integrity failure

    # The store itself now fails integrity for the tampered accession (precondition).
    with pytest.raises(RawStoreError):
        store.resolve(victim.raw_ref)

    reason = _assert_blocked(C, deltas, citations, store, victim)
    assert reason, "tamper must surface a reason, not an escaped exception"

    # A citation for a DIFFERENT, untouched accession still resolves fine.
    good = next(c for c in citations if c.raw_ref != victim.raw_ref)
    assert C.resolver.resolve_citation(good, store).ok is True


def test_distinct_reasons_across_corruption_kinds(tmp_path):
    """The contract requires a DISTINCT reason per problem type: the integrity-tamper reason
    must differ from a plain snippet/offset mismatch reason (not collapsed to one generic
    'invalid')."""
    C = W.cite_surface()

    # text/offset mismatch reason
    _c1, _d1, cits1, _f1, store1 = _fresh(tmp_path, "r_mismatch")
    v1 = cits1[0]
    v1.snippet = v1.snippet + "X"  # length mismatch (a resolvable-blob failure)
    reason_mismatch = C.resolver.resolve_citation(v1, store1).reason

    # integrity-tamper reason (RawStoreError path)
    _c2, _d2, cits2, _f2, store2 = _fresh(tmp_path, "r_tamper")
    v2 = cits2[0]
    p = W.blob_path(tmp_path / "r_tamper", v2.raw_ref)
    p.write_bytes(p.read_bytes() + b"Z")
    reason_tamper = C.resolver.resolve_citation(v2, store2).reason

    # missing-raw reason (unknown accession, nothing stored)
    v2b = cits2[1]
    v2b.raw_ref = "0000000000-00-000000"
    reason_missing = C.resolver.resolve_citation(v2b, store2).reason

    reasons = [reason_mismatch, reason_tamper, reason_missing]
    assert all(isinstance(x, str) and x for x in reasons), (
        f"every failure must carry a non-empty reason: {reasons}"
    )
    assert reason_tamper != reason_mismatch, (
        "integrity tamper and snippet mismatch must be DISTINCT reasons, not one generic code"
    )
    assert len(set(reasons)) >= 2, f"reasons not distinct across kinds: {reasons}"
