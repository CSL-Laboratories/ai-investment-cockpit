"""Independent RW-21 refuse regressions and pinned-source replays.

Adversarial strings below are synthetic boundary probes, not filings, cohort labels,
or evidence of held-out precision. Real raw fixture tests additionally verify the
capture manifest before using exact SEC bytes.
"""
from __future__ import annotations

import pytest

from filings.raw_store import RawStore
from strategies.merger_semantics import (
    CashMergerSemanticVerifier,
    SemanticEvidenceUnavailable,
    _EXPECTED_DATE,
    _OUTSIDE_DATE,
    _derive_eligibility,
    _derive_outcome,
    _text,
    _unique_match,
)
from tests.strategies.test_merger_semantics import (
    _SAFT_10Q_AGREEMENT_HEAD,
    _SAFT_10Q_BOILERPLATE,
    _SAFT_10Q_CASH_HEAD,
)


# A deliberately synthetic authority-looking clause used only to attack extraction.
_BAIT = (
    "The company entered into a definitive merger. "
    "Each share of common stock listed on Nasdaq will receive $105 in cash per share. "
    "The merger was completed."
)


@pytest.mark.parametrize(
    "hidden",
    [
        '<script type="application/json">{ "example": "' + _BAIT + '" }</script>',
        '<style type="text/css">/* ' + _BAIT + ' */</style>',
        '<ix:hidden><ix:nonNumeric>' + _BAIT + '</ix:nonNumeric></ix:hidden>',
        '<!-- > ' + _BAIT + ' -->',
        '<span title="> ' + _BAIT + '">ordinary filing text</span>',
    ],
    ids=["script", "style", "ix-hidden", "comment-angle", "quoted-attribute-angle"],
)
def test_non_displayed_markup_cannot_supply_cash_or_outcome_authority(hidden):
    raw = ('<html><body>No merger terms are asserted here.' + hidden + '</body></html>').encode()
    text = _text(raw)

    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_eligibility(text, amended=False)
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(text)


@pytest.mark.parametrize(
    "hidden",
    [
        '<div hidden>' + _BAIT + '</div>',
        '<div style="display: none">' + _BAIT + '</div>',
        '<div style="visibility: hidden">' + _BAIT + '</div>',
        '<template>' + _BAIT + '</template>',
        '<script>' + _BAIT,
        '<ix:hidden>' + _BAIT,
        '<div style="display:none" style="display:block">' + _BAIT + '</div>',
        '<div hidden/>' + _BAIT,
        '<script/>' + _BAIT,
        '<div style="display:/**/none">' + _BAIT + '</div>',
    ],
    ids=["html-hidden", "display-none", "visibility-hidden", "template",
         "unclosed-script", "unclosed-ix-hidden", "duplicate-style-attributes",
         "self-closing-hidden-nonvoid", "self-closing-script", "css-comment-hidden"],
)
def test_hidden_or_unclosed_markup_cannot_supply_authority(hidden):
    raw = ('<html><body>Ordinary filing text. ' + hidden).encode()
    # Refusing the entire malformed/unrecognized document is also fail-closed.
    try:
        text = _text(raw)
    except SemanticEvidenceUnavailable:
        return
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_eligibility(text, amended=False)
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(text)


def test_distinct_paragraph_fragments_cannot_synthesize_an_outcome():
    raw = b"<p>The merger was</p><p>completed transaction examples:</p>"
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(_text(raw))


@pytest.mark.parametrize(
    "sentence",
    [
        "It is not true that the merger was completed.",
        "There is no evidence that the merger has been completed.",
        "The issuer never announced completion of the merger.",
        "It is not true that the merger agreement was terminated.",
        "There is no evidence that the parties have terminated the merger agreement.",
        "It is not true that the transaction remains pending.",
        "It is denied that the merger was completed.",
        "The merger was completed? No.",
        "The merger was completed; that statement is false.",
    ],
)
def test_negated_outcome_assertions_are_unavailable(sentence):
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(_text(sentence.encode()))


@pytest.mark.parametrize(
    ("sentence", "outcome"),
    [
        ("The merger was completed.", "closed"),
        ("The merger agreement was terminated.", "failed"),
        ("The transaction remains pending.", "open"),
        ("The transaction will not proceed.", "failed"),
    ],
)
def test_affirmative_outcome_controls_remain_source_derived(sentence, outcome):
    assert _derive_outcome(_text(sentence.encode())) == outcome


@pytest.mark.parametrize(
    "sentence",
    [
        "The merger is expected to close in 2026.",
        "The merger is expected to close by the end of the third quarter of 2026.",
        "If the merger has not closed by September 6, 2026, the agreement will be "
        "automatically extended to December 6, 2026.",
    ],
    ids=["year-only", "quarter-only", "not-closed-by-auto-extension"],
)
def test_cash_repairs_do_not_turn_conditional_or_imprecise_dates_into_authority(sentence):
    text = _text(sentence.encode())
    for pattern, field in (
        (_EXPECTED_DATE, "expected resolution date"),
        (_OUTSIDE_DATE, "outside date"),
    ):
        with pytest.raises(SemanticEvidenceUnavailable, match="unambiguous"):
            _unique_match(pattern, text, field=field)


def test_conflicting_explicit_dates_still_refuse():
    text = _text(
        b"Expected closing date: 2026-10-01. Expected closing date: 2026-10-02. "
        b"Outside date: 2026-11-01. Outside date: 2026-11-02."
    )
    for pattern in (_EXPECTED_DATE, _OUTSIDE_DATE):
        with pytest.raises(SemanticEvidenceUnavailable, match="unambiguous"):
            _unique_match(pattern, text, field="date")


@pytest.mark.parametrize(
    ("sentence", "pattern"),
    [
        ("The merger is not expected to close by October 1, 2026.", _EXPECTED_DATE),
        ("It is not true that the outside date is November 1, 2026.", _OUTSIDE_DATE),
    ],
    ids=["negated-expected-day", "negated-outside-day"],
)
def test_negated_exact_date_assertion_cannot_supply_date_authority(sentence, pattern):
    with pytest.raises(SemanticEvidenceUnavailable):
        _unique_match(pattern, _text(sentence.encode()), field="date")


def test_negated_agreement_assertion_cannot_supply_definitive_authority():
    raw = (
        "It is not true that the company entered into an agreement and plan of merger. "
        "Each share of common stock listed on Nasdaq will receive $105 in cash per share."
    ).encode()
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_eligibility(_text(raw), amended=False)


def test_negated_cash_clause_cannot_supply_consideration_authority():
    raw = (
        "Nasdaq. The company entered into a definitive merger. "
        "It is not true that each share of common stock will receive $105 in cash per share."
    ).encode()
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_eligibility(_text(raw), amended=False)


def test_original_repository_saft_cash_head_is_preserved_after_assertion_filtering():
    # The existing checked-in excerpt is a control, not a new full-primary capture.
    raw = (
        _SAFT_10Q_AGREEMENT_HEAD + " " + _SAFT_10Q_CASH_HEAD + " " + _SAFT_10Q_BOILERPLATE
    ).encode()
    label = _derive_eligibility(_text(raw), amended=False)
    assert label["cash_offer"] == "105.00"
    assert label["has_stock"] is False


@pytest.mark.parametrize(
    "month",
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"],
)
@pytest.mark.parametrize(
    ("prefix", "pattern"),
    [("Expected closing date:", _EXPECTED_DATE), ("Outside date:", _OUTSIDE_DATE)],
    ids=["expected", "outside"],
)
def test_pinned_calendar_month_is_not_confused_with_modal_language(month, prefix, pattern):
    text = _text(f"{prefix} {month} 1, 2026.".encode())
    assert _unique_match(pattern, text, field="date") == f"{month.lower()} 1, 2026"


def test_modal_statement_of_exact_date_remains_unavailable():
    with pytest.raises(SemanticEvidenceUnavailable):
        _unique_match(
            _EXPECTED_DATE,
            "the merger may be expected to close by may 1, 2026.",
            field="date",
        )


def _synthetic_current_terms(tmp_path):
    """Offline semantic unit fixture; supplies no signed or witnessed authority."""
    raw_root = tmp_path / "semantic"
    store = RawStore(raw_root / "sec", min_free_bytes=0)
    text = (
        "Nasdaq. The company entered into a definitive merger. "
        "Each share of target common stock will receive $105 in cash per share. "
        "Expected closing date: 2026-10-01. Outside date: 2026-11-01. "
        "The closing remains subject to regulatory approval."
    )
    store.write("synthetic-announcement", text.encode())
    document = {
        **_derive_eligibility(_text(text.encode()), amended=False),
        "source_accessions": ["synthetic-announcement"],
        "announcement_accession": "synthetic-announcement",
        "current_accession": "synthetic-announcement",
        "expected_resolution_date": "2026-10-01",
        "outside_date": "2026-11-01",
        "conditions": ["regulatory approval"],
    }
    return CashMergerSemanticVerifier(None, raw_root=raw_root, witness=None), store, document


@pytest.mark.parametrize(
    "quarterly",
    [
        b"The company reports its quarterly operating results.",
        b"The employee compensation plan was amended. Revenue increased.",
    ],
    ids=["ordinary-quarterly", "unrelated-compensation-amendment"],
)
def test_ordinary_later_filing_does_not_invent_merger_amendment(tmp_path, quarterly):
    verifier, store, document = _synthetic_current_terms(tmp_path)
    store.write("synthetic-quarterly", quarterly)
    document = {
        **document,
        "source_accessions": ["synthetic-announcement", "synthetic-quarterly"],
        "current_accession": "synthetic-quarterly",
    }
    verifier._verify_deal(document)


def test_intervening_explicit_merger_amendment_cannot_be_hidden_by_ordinary_head(tmp_path):
    verifier, store, document = _synthetic_current_terms(tmp_path)
    store.write("synthetic-amendment", b"The merger agreement was amended.")
    store.write("synthetic-quarterly", b"The company reports quarterly operating results.")
    document = {
        **document,
        "source_accessions": [
            "synthetic-announcement", "synthetic-amendment", "synthetic-quarterly",
        ],
        "current_accession": "synthetic-quarterly",
    }
    with pytest.raises(SemanticEvidenceUnavailable):
        verifier._verify_deal(document)
    verifier._verify_deal({**document, "amendment_reviewed": True})
