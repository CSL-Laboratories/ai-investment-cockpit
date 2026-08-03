"""WP5 acceptance — DETERMINISM (scaffold WP5 Accept clause 6; playbook G5).

Two ``render_digest`` runs over IDENTICAL inputs are byte-identical (the digest is a plain,
deterministic, LLM-free artifact — no wall-clock leakage, stable ordering with a fixed
tiebreak). Verified both for an approvable brief (over the 600+-delta NVDA 10-Q pair, so the
highest-materiality-first ordering + tiebreak are exercised) and for an unpublishable one.

A ``FrozenClock`` is injected so any citation ``created_ts`` / rendered timestamp is itself
stable — non-determinism can then only come from the renderer's own logic, which is what this
asserts.

Recorded fixtures only — NO live sec.gov, NO LLM.
"""
from __future__ import annotations

from tests.filings import _wp5_support as W


def test_approvable_digest_is_byte_identical_across_runs(tmp_path):
    C = W.cite_surface()
    deltas, filings, store = W.pair_set("NVDA10Q", tmp_path / "det_ok")
    a = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    b = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert a.approvable is True and b.approvable is True
    assert a.text == b.text, "two renders over identical inputs must be byte-identical"


def test_citation_build_is_byte_identical_across_runs(tmp_path):
    """The digest's determinism rests on the builder being deterministic: identical inputs ->
    identical citation_ids/anchors in identical order."""
    C = W.cite_surface()
    deltas, filings, _store = W.pair_set("AAPL10Q", tmp_path / "det_cite")
    a = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    b = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    assert [ (c.citation_id, c.delta_id, c.byte_start, c.byte_end, c.snippet, c.edgar_url)
             for c in a ] == \
           [ (c.citation_id, c.delta_id, c.byte_start, c.byte_end, c.snippet, c.edgar_url)
             for c in b ]


def test_unpublishable_digest_is_byte_identical_across_runs(tmp_path):
    """Determinism holds for the fail-closed report too — the UNPUBLISHABLE text is stable."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "det_bad")
    deltas[0].snippet = "__WP5_FABRICATED__ not in the filing bytes"
    a = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    b = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert a.approvable is False and b.approvable is False
    assert a.text == b.text, "the unpublishable report must be deterministic too"
