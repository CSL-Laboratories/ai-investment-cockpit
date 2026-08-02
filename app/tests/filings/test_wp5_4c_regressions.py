"""WP5 heavy-4c regression tests — the two hardening gaps the adversarial pass surfaced.

Each test FAILS on the as-built WP5 cite/ and passes on the hardened version. Both findings
are latent (the deterministic WP4 engine always sets accession_no == raw_ref and mints a
canonical URL), but the citation core is the legal shield — it must CATCH a mis-attributed or
spoofed citation regardless of what feeds it, not assume the engine got it right.

  * FINDING 1 (critical) — resolver.resolve_citation re-read raw_store.resolve(raw_ref) and
    compared the slice to the snippet but NEVER checked raw_ref == accession_no. A citation
    attributed to filing A whose raw_ref pointed at filing B (with a snippet valid for B) would
    resolve OK — a byte-perfect reproduction with a FALSE attribution. Fix: reject
    raw_ref != accession_no up front (reason 'raw-ref-accession-mismatch'), fail-closed.
  * FINDING 2 (high) — digest.edgar_url_matches_accession substring-matched the archive marker
    ANYWHERE in the URL (so a phishing host embedding '/Archives/edgar/data/...' passed) and
    accepted ANY digit CIK. Fix: anchor the canonical https://www.sec.gov/Archives/edgar/data/
    prefix with startswith AND verify the CIK/filename against the source Filing.

Recorded fixtures only — NO live sec.gov, NO LLM.
"""
from __future__ import annotations

import pytest

from tests.filings import _wp5_support as W


# ======================================================================================
# FINDING 1 — raw_ref must name the same filing as accession_no (attribution consistency).
# ======================================================================================
def test_resolver_rejects_raw_ref_pointing_at_a_different_filing(tmp_path):
    """A citation attributed to filing A but whose raw_ref points at filing B — with a snippet
    that genuinely reproduces from B — must NOT resolve. Before the fix it returned ok=True
    (a real snippet, wrong attribution); after, ok=False 'raw-ref-accession-mismatch'."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "attr")
    # deltas span two accessions (A, B). Build the real citations; pick one from each filing.
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    acc_a = deltas[0].accession_no
    cit_b = next(c for c in citations if c.accession_no != acc_a)  # valid for filing B

    # Sanity: cit_b resolves cleanly as itself (raw_ref == its own accession B).
    assert C.resolver.resolve_citation(cit_b, store).ok is True

    # Forge the attribution: claim it is from A while keeping B's raw_ref/snippet/offsets.
    # The bytes STILL reproduce from B, so only an accession/raw_ref binding can catch this.
    cit_b.accession_no = acc_a
    res = C.resolver.resolve_citation(cit_b, store)
    assert res.ok is False, "resolver accepted a citation whose raw_ref names a different filing"
    assert res.reason == "raw-ref-accession-mismatch", (
        f"expected a distinct attribution-mismatch reason, got {res.reason!r}"
    )


def test_claim_gate_blocks_a_misattributed_citation(tmp_path):
    """The all-or-nothing gate must block the whole brief when any citation's raw_ref and
    accession_no name different filings (fail-closed)."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "attr_gate")
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    assert C.claim_gate.evaluate(deltas, citations, store).approvable is True  # clean control

    # Point one citation's raw_ref at the OTHER accession (both blobs are in the store).
    other_acc = next(d.accession_no for d in deltas if d.accession_no != citations[0].accession_no)
    citations[0].raw_ref = other_acc
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is False, "gate approved a citation resolving from the wrong filing"
    assert any(
        f.get("reason") == "unresolvable-citation"
        and f.get("detail") == "raw-ref-accession-mismatch"
        for f in gate.failures
    ), f"attribution mismatch not surfaced as a blocking failure: {gate.failures}"


# ======================================================================================
# FINDING 2 — the digest URL guard must be scheme/host-anchored and CIK/filename-checked.
# ======================================================================================
_PHISHING = (
    "https://sec-gov-archive.phishing.example/redirect?u=/Archives/edgar/data/1/"
    "{acc}/malware.exe"
)


def test_url_matcher_rejects_a_phishing_host_that_embeds_the_archive_path(tmp_path):
    C = W.cite_surface()
    match = C.digest.edgar_url_matches_accession
    acc = "0001045810-26-000060"
    good = W.expected_url(acc)
    assert match(good, acc) is True, "the real archive URL must still pass"
    # Substring-of-the-marker attack: the marker appears, but not as the anchored prefix.
    assert match(_PHISHING.format(acc=acc.replace("-", "")), acc) is False
    # Wrong-CIK canonical URL for the right accession, when the source CIK is known.
    wrong_cik = f"https://www.sec.gov/Archives/edgar/data/999999999/{acc.replace('-', '')}/x.htm"
    assert match(wrong_cik, acc, cik="0001045810") is False
    assert match(good, acc, cik="0001045810", filename="nvda-20260628.htm") is True


def test_digest_blocks_a_spoofed_source_url(tmp_path):
    """A delta carrying a phishing edgar_url must make the digest UNPUBLISHABLE and must never
    render that URL beside a snippet as a clean cited fact."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "url_spoof")
    assert C.digest.render_digest(deltas, filings, store, clock=W.FROZEN).approvable is True

    bad_delta = deltas[0]
    poisoned = _PHISHING.format(acc=bad_delta.accession_no.replace("-", ""))
    bad_delta.edgar_url = poisoned

    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert result.approvable is False, "digest rendered a spoofed-source-URL item as approvable"
    assert result.gate.approvable is False
    assert "UNPUBLISHABLE" in result.text.upper()
    assert any(f.get("reason") == "malformed-edgar-url" for f in result.gate.failures)
    # Fail-closed: the failing item's snippet is not presented as a verified change ...
    assert "what changed:" not in result.text
    # ... and no clean 'source:' citation line renders the poisoned URL.
    assert f"source: {poisoned}" not in result.text


def test_digest_blocks_a_wrong_cik_url(tmp_path):
    """A canonical-looking URL with the WRONG CIK for the accession is rejected against the
    source filing's real CIK."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "url_cik")
    bad_delta = deltas[0]
    acc_nodash = bad_delta.accession_no.replace("-", "")
    bad_delta.edgar_url = f"https://www.sec.gov/Archives/edgar/data/999999999/{acc_nodash}/x.htm"
    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert result.approvable is False, "digest accepted a wrong-CIK archive URL"
    assert any(f.get("reason") == "malformed-edgar-url" for f in result.gate.failures)


# ======================================================================================
# ROUND-7 â€” source_ref is provenance, not decoration.  A byte-perfect citation with a
# forged section/fact label is still a false attribution and must fail the all-or-nothing
# claim gate.  The same rule must hold when a Delta needs the builder's deterministic
# fallback source ref; falsey Citation refs can never compare equal and pass vacuously.
# ======================================================================================
_MISSING_SOURCE_REFS = (
    pytest.param(None, id="none"),
    pytest.param("", id="empty"),
    pytest.param(" \t ", id="whitespace"),
)


def _one_clean_source_ref_chain(tmp_path, name):
    """Return one real, byte-resolving Delta/Citation chain from recorded fixtures."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / name)
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    delta = deltas[0]
    citation = next(c for c in citations if c.delta_id == delta.delta_id)

    assert isinstance(delta.source_ref, str) and delta.source_ref.strip()
    assert isinstance(citation.source_ref, str) and citation.source_ref.strip()
    assert W.raw_slice(store, citation.raw_ref, citation.byte_start, citation.byte_end) == (
        citation.snippet
    )
    assert C.claim_gate.evaluate([delta], [citation], store).approvable is True
    return C, delta, citation, filings, store


@pytest.mark.parametrize("mutated", ("citation", "delta"))
def test_claim_gate_blocks_source_ref_mismatch_despite_verbatim_byte_anchor(tmp_path, mutated):
    """Changing only a source label must not let a real snippet wear a false attribution."""
    C, delta, citation, _filings, store = _one_clean_source_ref_chain(
        tmp_path, f"source_ref_{mutated}"
    )
    forged = f"__WP5_FORGED_SOURCE_REF__{delta.delta_id}"

    if mutated == "citation":
        citation.source_ref = forged
    else:
        delta.source_ref = forged

    # The raw anchor is still valid: source_ref binding is the only injected defect.
    assert W.raw_slice(store, citation.raw_ref, citation.byte_start, citation.byte_end) == (
        citation.snippet
    )
    gate = C.claim_gate.evaluate([delta], [citation], store)

    assert gate.approvable is False, "gate accepted a citation with a forged source reference"
    assert gate.failures


@pytest.mark.parametrize("missing", _MISSING_SOURCE_REFS)
def test_source_ref_fallback_is_deterministic_and_cannot_be_forged(tmp_path, missing):
    """A missing Delta label gets one canonical fallback, not a Citation-controlled label."""
    C, delta, _citation, filings, store = _one_clean_source_ref_chain(tmp_path, "ref_fallback")
    delta.source_ref = missing

    first = C.builder.build_citation(delta, filings[delta.accession_no], clock=W.FROZEN)
    second = C.builder.build_citation(delta, filings[delta.accession_no], clock=W.FROZEN)
    fallback = first.source_ref

    assert isinstance(fallback, str) and fallback.strip()
    assert fallback == second.source_ref, "fallback source ref must be deterministic"
    assert W.raw_slice(store, first.raw_ref, first.byte_start, first.byte_end) == first.snippet
    assert C.claim_gate.evaluate([delta], [first], store).approvable is True

    first.source_ref = f"{fallback} / forged"
    gate = C.claim_gate.evaluate([delta], [first], store)
    assert gate.approvable is False, "citation supplied a forged source-ref fallback"
    assert gate.failures


@pytest.mark.parametrize("missing", _MISSING_SOURCE_REFS)
def test_claim_gate_rejects_missing_citation_source_ref(tmp_path, missing):
    """An absent Citation ref is malformed, never a fallback that silently passes."""
    C, delta, citation, _filings, store = _one_clean_source_ref_chain(
        tmp_path, "missing_citation_ref"
    )
    citation.source_ref = missing

    assert W.raw_slice(store, citation.raw_ref, citation.byte_start, citation.byte_end) == (
        citation.snippet
    )
    gate = C.claim_gate.evaluate([delta], [citation], store)
    assert gate.approvable is False
    assert gate.failures


@pytest.mark.parametrize(
    ("delta_ref", "citation_ref"),
    [
        pytest.param(None, None, id="none-none"),
        pytest.param(None, "", id="none-empty"),
        pytest.param("", None, id="empty-none"),
        pytest.param(" \t ", " ", id="whitespace-whitespace"),
    ],
)
def test_claim_gate_never_treats_two_missing_source_refs_as_equal(
    tmp_path, delta_ref, citation_ref
):
    """``None == None`` is not a citation-chain provenance match."""
    C, delta, citation, _filings, store = _one_clean_source_ref_chain(
        tmp_path, "both_missing_refs"
    )
    delta.source_ref = delta_ref
    citation.source_ref = citation_ref

    gate = C.claim_gate.evaluate([delta], [citation], store)
    assert gate.approvable is False
    assert gate.failures


# ======================================================================================
# ROUND-2 FINDING (critical) — the gate must verify a citation actually cites ITS delta.
# Binding by delta_id alone let a byte-perfect citation for a DIFFERENT filing, repointed via
# delta_id, be marked approvable (a wrong-cited claim). Fix = per-delta anchor cross-binding.
# ======================================================================================
def test_gate_blocks_a_citation_repointed_to_a_foreign_delta(tmp_path):
    """Take a valid citation for delta B, repoint its delta_id to delta A, and gate [A]+[that
    citation]. Its bytes resolve (from B), it is not an orphan (delta_id is A, in the set), not a
    duplicate — but it does NOT cite A. Before the fix: approvable=True. After: blocked."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "anchor")
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    # delta_a and a citation genuinely for the OTHER filing.
    delta_a = deltas[0]
    cit_foreign = next(c for c in citations if c.accession_no != delta_a.accession_no)
    assert C.resolver.resolve_citation(cit_foreign, store).ok is True  # resolves its own (B) bytes

    # Repoint it to claim it cites delta_a — the exact skeptic-4 repro.
    cit_foreign.delta_id = delta_a.delta_id
    gate = C.claim_gate.evaluate([delta_a], [cit_foreign], store)
    assert gate.approvable is False, (
        "gate approved a byte-perfect citation for a DIFFERENT filing repointed to delta A"
    )
    assert any(
        f.get("reason") == "citation-delta-anchor-mismatch"
        and f.get("delta_id") == delta_a.delta_id
        for f in gate.failures
    ), f"anchor mismatch not surfaced: {gate.failures}"


def test_gate_accepts_the_honest_one_to_one_citations(tmp_path):
    """Control: the builder's real 1:1 citations pass the new anchor cross-binding cleanly."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "anchor_ok")
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is True, f"honest 1:1 citations were blocked: {gate.failures}"


# ======================================================================================
# ROUND-2 SECONDARY — the digest presents WORKING links only: a source filing without a real
# primary_doc (only a synthesized <accession>.htm URL is possible) is not a clean cited fact.
# ======================================================================================
def test_digest_blocks_a_citation_without_a_known_primary_doc(tmp_path):
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "no_primary_doc")
    assert C.digest.render_digest(deltas, filings, store, clock=W.FROZEN).approvable is True
    # Drop the real filename from one cited filing -> its URL can only be synthesized.
    filings[deltas[0].accession_no].primary_doc = None
    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert result.approvable is False, "digest rendered a citation with no known working link"
    assert any(f.get("reason") == "unverifiable-edgar-url" for f in result.gate.failures)


# ======================================================================================
# ROUND-3 sub-findings.
# ======================================================================================
def test_resolver_rejects_a_zero_length_span(tmp_path):
    """A citation quoting ZERO bytes (byte_start == byte_end, snippet == '') must NOT resolve —
    it cites nothing. Before the fix it passed vacuously (0 == 0, '' == '')."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "empty_span")
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    c = citations[0]
    c.byte_start = c.byte_end = 100  # a valid in-range offset, zero-length span
    c.snippet = ""
    res = C.resolver.resolve_citation(c, store)
    assert res.ok is False, "resolver accepted a zero-byte citation (cites nothing)"
    assert res.reason == "empty-span", f"expected 'empty-span', got {res.reason!r}"
    # And the gate blocks the whole brief on it.
    assert C.claim_gate.evaluate([deltas[0]], [c], store).approvable is False


def test_digest_url_sweep_fails_closed_on_a_missing_source_cik(tmp_path):
    """If the source filing has no CIK, the URL's CIK segment cannot be verified — fail CLOSED,
    do not skip the check (defense-in-depth; unreachable in the pipeline where cik is non-null)."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "no_cik")
    filings[deltas[0].accession_no].cik = None
    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert result.approvable is False, "digest rendered a URL it could not verify (no source CIK)"
    assert any(f.get("reason") == "unverifiable-edgar-url" for f in result.gate.failures)


# ======================================================================================
# ROUND-3 CRITICAL — the digest must NOT render a numeric delta's uncited prior_value as a
# sourced fact (Option A, Lucas 2026-07-09). Only the current filing's figure is byte-cited.
# ======================================================================================
def test_digest_never_renders_an_uncited_prior_value(tmp_path):
    from decimal import Decimal

    C = W.cite_surface()
    deltas, filings, store = W.pair_set("NVDA10Q", tmp_path / "optA")  # has numeric deltas
    numeric = next(d for d in deltas if d.kind == "numeric")
    concept = numeric.source_ref.split(":", 1)[1].split("/", 1)[0]  # e.g. "NetIncomeLoss"
    numeric.prior_value = Decimal("424242424242")  # a value present in NO filing

    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)  # limit=None -> all
    assert result.approvable is True, (
        "fabricating an UNCITED prior_value must not break resolution (the citation anchors the "
        "current filing's snippet, which is unchanged)"
    )
    assert "424242424242" not in result.text, (
        "digest rendered an uncited (fabricated) prior_value as a sourced fact (Inv 1 breach)"
    )
    # The current cited figure IS still surfaced (we didn't just drop numeric deltas).
    assert concept in result.text, "digest dropped the numeric delta's cited current figure"


# ======================================================================================
# ROUND-4 CRITICAL — a numeric delta's current_value is published as [cited] but is NOT an
# anchor field (not re-read by the resolver). The gate must re-derive it from the cited
# <ix:nonFraction> snippet and fail closed on divergence (Inv 1 + 12, all-or-nothing).
# ======================================================================================
def test_gate_grounds_numeric_current_value_in_the_cited_snippet(tmp_path):
    from decimal import Decimal

    C = W.cite_surface()
    deltas, filings, store = W.pair_set("NVDA10Q", tmp_path / "grounding")
    numeric = next(d for d in deltas if d.kind == "numeric")
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)

    # Control: honest current_value == the value encoded by the cited snippet -> approvable.
    assert C.claim_gate.evaluate(deltas, citations, store).approvable is True

    # Tamper ONLY current_value (leave every anchor field, incl. the snippet, intact) so the
    # citation still resolves byte-for-byte — the exact skeptic repro.
    numeric.current_value = Decimal("999000000000")
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is False, (
        "gate approved a numeric figure that its cited snippet does not byte-encode"
    )
    assert any(
        f.get("reason") == "numeric-figure-ungrounded" and f.get("delta_id") == numeric.delta_id
        for f in gate.failures
    ), f"ungrounded numeric figure not surfaced: {gate.failures[:5]}"


def test_digest_blocks_a_tampered_numeric_figure(tmp_path):
    """End-to-end: a fabricated current_value makes the whole digest UNPUBLISHABLE and the false
    figure is never rendered as a clean cited fact."""
    from decimal import Decimal

    C = W.cite_surface()
    deltas, filings, store = W.pair_set("NVDA10Q", tmp_path / "grounding_digest")
    numeric = next(d for d in deltas if d.kind == "numeric")
    numeric.current_value = Decimal("999000000000")
    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert result.approvable is False
    assert "UNPUBLISHABLE" in result.text.upper()
    assert "$999B" not in result.text, "digest rendered a fabricated figure as [cited]"


def test_numeric_value_from_snippet_matches_every_fixture_delta(tmp_path):
    """The grounding parser must reproduce current_value for EVERY real numeric delta (a parser
    that rejected honest deltas would fail-close the whole corpus)."""
    from decimal import Decimal

    C = W.cite_surface()
    parse = C.claim_gate.numeric_value_from_snippet
    checked = 0
    for pair in ("NVDA10Q", "AAPL10Q"):
        deltas, _f, _s = W.pair_set(pair, tmp_path / f"parse_{pair}")
        for d in deltas:
            if d.kind != "numeric":
                continue
            checked += 1
            assert parse(d.snippet) == Decimal(d.current_value), (
                f"{d.source_ref}: snippet value {parse(d.snippet)} != current_value "
                f"{d.current_value}"
            )
    assert checked >= 100, f"expected the full numeric corpus, only checked {checked}"


# ======================================================================================
# ROUND-5 — numeric denomination must follow the XBRL unit (honest-pipeline breach): a share
# count or a rate must NEVER render as dollars; and the concept label must be gate-grounded.
# ======================================================================================
def test_numeric_figures_are_denominated_by_unit(tmp_path):
    from filings.cite import digest as D

    W.cite_surface()  # contract-drift guard (raises loudly if the cite/ surface is missing)
    deltas, _f, _s = W.pair_set("NVDA10Q", tmp_path / "denom")
    seen = {"USD": 0, "shares": 0, "pure": 0, "USD/shares": 0}
    for d in deltas:
        if d.kind != "numeric":
            continue
        # unit = everything after the FIRST slash (may be "USD/shares"); concept precedes it.
        unit = d.source_ref.split("/", 1)[1] if "/" in d.source_ref else ""
        fig = D._denominate(d.current_value, D._unit_ref(d.snippet))
        if unit == "USD":
            assert fig.startswith("$"), f"USD figure not shown as dollars: {fig!r}"
            seen["USD"] += 1
        elif unit == "shares":
            assert "$" not in fig and fig.endswith("shares"), f"share count shown as {fig!r}"
            seen["shares"] += 1
        elif unit == "pure":
            assert "$" not in fig, f"dimensionless rate shown as dollars: {fig!r}"
            seen["pure"] += 1
        elif unit == "USD/shares":
            assert fig.startswith("$") and "per share" in fig, f"per-share shown as {fig!r}"
            seen["USD/shares"] += 1
    assert seen["shares"] > 0 and seen["pure"] > 0, f"corpus lacked shares/pure coverage: {seen}"


def test_gate_grounds_the_numeric_concept_in_the_cited_snippet(tmp_path):
    C = W.cite_surface()
    deltas, filings, store = W.pair_set("NVDA10Q", tmp_path / "concept")
    numeric = next(d for d in deltas if d.kind == "numeric")
    citations = C.builder.build_citations(deltas, filings, clock=W.FROZEN)
    assert C.claim_gate.evaluate(deltas, citations, store).approvable is True  # control

    # Relabel the figure to a DIFFERENT concept (snippet/offsets/value untouched) — the correct
    # number under the WRONG line item. The gate must catch it via the snippet's name= attr.
    numeric.source_ref = "us-gaap:Revenues/USD"
    gate = C.claim_gate.evaluate(deltas, citations, store)
    assert gate.approvable is False, "gate approved a figure relabelled to the wrong concept"
    assert any(
        f.get("reason") == "numeric-concept-ungrounded" and f.get("delta_id") == numeric.delta_id
        for f in gate.failures
    ), f"concept mismatch not surfaced: {gate.failures[:5]}"


# ======================================================================================
# ROUND-6 hardening (defense-in-depth) — the exported gate must fail closed when two distinct
# deltas share one delta_id, so an uncited delta cannot ride another's citation. Unreachable in
# the pipeline (delta_id is a content-hash primary key) but the public evaluate() must be honest.
# ======================================================================================
def test_gate_fails_closed_on_a_duplicate_delta_id(tmp_path):
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "dupdelta")
    texts = [d for d in deltas if d.kind == "text-section"]
    assert len(texts) >= 2, "fixture set-up: need two distinct text deltas"
    legit, false = texts[0], texts[1]

    # Control: two DISTINCT deltas, each with its own citation, are approvable.
    cits = C.builder.build_citations([false, legit], filings, clock=W.FROZEN)
    assert C.claim_gate.evaluate([false, legit], cits, store).approvable is True

    # Make `false` share legit's delta_id (legit last, so the anchor check validates legit and
    # passes) and try to ride legit's single citation — an uncited claim with no citation of its
    # own content.
    cit_legit = C.builder.build_citation(legit, filings[legit.accession_no], clock=W.FROZEN)
    false.delta_id = legit.delta_id
    gate = C.claim_gate.evaluate([false, legit], [cit_legit], store)
    assert gate.approvable is False, "gate approved an uncited delta riding a shared delta_id"
    assert any(f.get("reason") == "duplicate-delta-id" for f in gate.failures), (
        f"duplicate delta_id not surfaced: {gate.failures[:5]}"
    )
