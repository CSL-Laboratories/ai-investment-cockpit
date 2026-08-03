"""WP5 acceptance — ORPHAN SWEEP + citation_id REUSE (scaffold WP5 Accept clauses 3 & 4).

(3) ORPHAN SWEEP: a Citation whose ``delta_id`` is not in the delta set, and a Delta with zero
    Citations, each make ``approvable=False`` — no orphan survives the sweep.
(4) citation_id REUSE: two citations sharing an id => blocked.

All-or-nothing (Inv 1 + 12): a single orphan / uncited delta / reused id blocks the WHOLE brief.

Uses the small two-8-K set (2 deltas, 2 accessions). Recorded fixtures only — NO live sec.gov,
NO LLM.
"""
from __future__ import annotations

from tests.filings import _wp5_support as W


def _valid(tmp_path, name):
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / name)
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    assert C.claim_gate.evaluate(deltas, citations, store).approvable is True
    assert len(deltas) >= 2, "orphan/reuse cases need >=2 deltas"
    return C, deltas, citations, filings, store


def test_citation_referencing_unknown_delta_is_orphan_blocked(tmp_path):
    """A Citation whose delta_id is not in the delta set is a dangling orphan -> blocked."""
    C, deltas, citations, _filings, store = _valid(tmp_path, "orphan_cite")
    citations[0].delta_id = "delta_does_not_exist_in_set"
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is False, "a Citation with no matching Delta must block the brief"
    assert gate.failures, "orphan citation must be reported as a failure"


def test_delta_with_zero_citations_is_blocked(tmp_path):
    """A Delta with zero Citations is an uncited claim -> blocked (no orphan survives)."""
    C, deltas, citations, _filings, store = _valid(tmp_path, "uncited_delta")
    # Drop the citation covering deltas[0]; that delta now has ZERO citations.
    orphan_delta_id = deltas[0].delta_id
    survivors = [c for c in citations if c.delta_id != orphan_delta_id]
    assert len(survivors) < len(citations), "test setup: expected to drop one citation"
    gate = C.claim_gate.evaluate(deltas, survivors, store)
    assert gate.approvable is False, "a Delta with no Citation is an uncited claim -> block"
    assert gate.failures, "uncited delta must be reported as a failure"


def test_no_citations_at_all_is_blocked(tmp_path):
    """Degenerate uncited case: deltas present but ZERO citations supplied -> every delta is
    an uncited claim -> blocked (never vacuously approvable)."""
    C, deltas, _citations, _filings, store = _valid(tmp_path, "no_cites")
    gate = C.claim_gate.evaluate(deltas, [], store)
    assert gate.approvable is False, "deltas with no citations at all must not be approvable"
    assert gate.failures


def test_reused_citation_id_is_blocked(tmp_path):
    """Two citations sharing a citation_id => blocked (ids must be unique, 1:1 with delta)."""
    C, deltas, citations, _filings, store = _valid(tmp_path, "reuse")
    # Force a duplicate id across two DIFFERENT deltas' citations.
    citations[1].citation_id = citations[0].citation_id
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is False, "a reused citation_id must block the whole brief"
    assert gate.failures, "reused citation_id must be reported as a failure"


def test_clean_two_8k_set_is_approvable(tmp_path):
    """Control: the untouched small set is approvable — so the blocks above are caused by the
    injected defect, not by the fixture itself."""
    C, deltas, citations, _filings, store = _valid(tmp_path, "control")
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is True
    assert not gate.failures
    assert gate.item_count == len(deltas)
