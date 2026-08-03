"""WP5 acceptance — DIGEST (scaffold WP5 Accept clause 5; playbook G5).

For an APPROVABLE set, ``render_digest`` returns ``.approvable=True`` and ``.text`` contains,
per citation, a WORKING EDGAR URL that ends in the REAL primary-doc filename and is
format-checked against the accession (accession-nodash present in the path). For an
UNPUBLISHABLE set, ``.approvable=False`` and the failing item is NOT presented as a clean cited
fact (fail-closed).

The approvable check runs over the NVDA 10-Q pair, whose deltas cite TWO different filings
(current + prior) with TWO different real filenames — so it proves the digest renders the real
working URL for each cited accession, not just one. The unpublishable check corrupts ONE delta's
offset so its internally-built citation cannot resolve; all-or-nothing then blocks the brief.

Recorded fixtures only — NO live sec.gov, NO LLM.
"""
from __future__ import annotations

import pytest

from tests.filings import _wp4_support as S
from tests.filings import _wp5_support as W


@pytest.fixture(scope="module")
def approvable_digest(tmp_path_factory):
    C = W.cite_surface()
    root = tmp_path_factory.mktemp("wp5_digest_ok")
    deltas, filings, store = W.pair_set("NVDA10Q", root)
    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    return result, deltas, filings, store


def test_approvable_digest_is_approvable(approvable_digest):
    result, _deltas, _filings, _store = approvable_digest
    assert result.approvable is True, "a fully-cited resolver-clean pair must render approvable"
    assert result.gate.approvable is True, ".gate must agree with .approvable"
    assert isinstance(result.text, str) and result.text.strip(), "digest text must be non-empty"


def test_approvable_digest_has_working_real_url_per_cited_accession(approvable_digest):
    """Every accession the deltas cite appears in the text as its working EDGAR archive URL —
    accession-nodash in the path AND ending in the REAL primary-document filename."""
    result, deltas, _filings, _store = approvable_digest
    text = result.text
    cited_accessions = {d.accession_no for d in deltas}
    assert len(cited_accessions) >= 2, "NVDA10Q should cite both the current and prior filing"
    for acc in sorted(cited_accessions):
        url = W.expected_url(acc)
        meta = W.META_BY_ACCESSION[acc]
        assert url in text, (
            f"digest missing the working EDGAR URL for {acc}:\n  expected {url}"
        )
        # Format-checked against the accession: accession-nodash present in the path.
        assert acc.replace("-", "") in url
        # Ends in the REAL primary-doc filename (not a synthesized <accession>.htm).
        assert url.endswith(meta.filename)
        assert url.endswith(".htm") and meta.filename in url


def test_approvable_digest_url_matches_archive_helper(approvable_digest):
    """The rendered URL is exactly what ``archive_document_url`` builds for the accession —
    the canonical /Archives/edgar/data/<cik-int>/<accession-nodash>/<file> shape."""
    from filings.edgar.fetch import archive_document_url

    result, deltas, _filings, _store = approvable_digest
    for acc in {d.accession_no for d in deltas}:
        meta = W.META_BY_ACCESSION[acc]
        assert archive_document_url(meta.cik, acc, meta.filename) in result.text


def test_approvable_digest_renders_the_changed_content(approvable_digest):
    """A readable morning digest states WHAT changed: a distinctive byte-verified change from
    the NVDA 10-Q appears in the rendered text.

    Delta snippets are raw filing bytes (they may carry inline-XBRL tags / HTML entities), and
    the digest may present them verbatim OR tag-stripped for readability — so this normalizes
    the digest text the SAME way the WP4 ground truth is matched (tag-strip + entity-decode +
    whitespace-collapse) before asserting the quote is present. That is robust to either
    rendering while still proving the actual change reached the page (not just a bare URL)."""
    result, _deltas, _filings, _store = approvable_digest
    normalized = S.normalize(result.text)
    quote = (
        "We repurchased 108 million shares of our common stock for $20.2 billion during the "
        "first quarter of fiscal year 2027."
    )
    assert quote in normalized, (
        "the digest does not present a known byte-verified NVDA 10-Q change as cited content"
    )


def test_digest_identifies_each_source_registrant_by_cik(approvable_digest):
    result, _deltas, filings, _store = approvable_digest
    for filing in filings.values():
        assert f"CIK {filing.cik}" in result.text


def test_readable_excerpt_strips_terminal_control_characters():
    C = W.cite_surface()
    excerpt = C.digest._readable("alpha\x1b[31mRED\u2028NEXT")
    assert "\x1b" not in excerpt
    assert "\u2028" not in excerpt
    assert excerpt == "alpha [31mRED NEXT"


def test_unpublishable_digest_is_blocked_and_hides_the_failing_item(tmp_path):
    """Corrupt ONE delta's offset span so its internally-built citation cannot resolve. The
    digest must be UNPUBLISHABLE and must NOT present the failing item as a clean cited fact
    (its working URL rendered next to a snippet as if verified)."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "digest_bad")

    # Confirm the untouched set WOULD be approvable (so the block is caused by our corruption).
    ok = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert ok.approvable is True

    # Corrupt deltas[0]: replace its snippet with a fabricated string that is NOT the bytes at
    # its offsets. The digest builds citations internally from these deltas, so the citation for
    # deltas[0] will fail to resolve.
    sentinel = "__WP5_FABRICATED_UNCITED_CLAIM__ not present in the filing bytes"
    bad_delta = deltas[0]
    bad_delta.snippet = sentinel
    bad_acc = bad_delta.accession_no
    bad_url = W.expected_url(bad_acc)

    result = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert result.approvable is False, "an unresolvable internal citation must block the digest"
    assert result.gate.approvable is False
    assert "UNPUBLISHABLE" in result.text.upper(), (
        "an unpublishable digest must be clearly marked (fail-closed report, not a cited brief)"
    )
    # Fail-closed: the fabricated snippet must NOT be presented as a clean cited fact — i.e.
    # never rendered together with its (would-be) working EDGAR URL as if verified.
    assert not (sentinel in result.text and bad_url in result.text), (
        "the digest presented a gate-FAILING item as a clean cited fact (snippet + working URL)"
    )


def test_unpublishable_differs_from_approvable_render(tmp_path):
    """Sanity: the gate actually changes the output — the unpublishable render is not the same
    text as the approvable one (the gate result is not ignored by the renderer)."""
    C = W.cite_surface()
    deltas, filings, store = W.two_8k_set(tmp_path / "digest_diff")
    good = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    deltas[0].snippet = "__WP5_FABRICATED__ not in the filing"
    bad = C.digest.render_digest(deltas, filings, store, clock=W.FROZEN)
    assert good.approvable is True and bad.approvable is False
    assert good.text != bad.text, "renderer ignored the claim-gate result"
