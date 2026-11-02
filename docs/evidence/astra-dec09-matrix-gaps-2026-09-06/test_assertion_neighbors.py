"""Supplemental negative-language probes; outside the proposed cash test scope.

These synthetic strings do not represent a real filing or independently labelled
holdout. A failed refusal is retained as a failure against unchanged c5ef0ad.
"""
from __future__ import annotations

import pytest

from strategies.merger_semantics import (
    SemanticEvidenceUnavailable,
    _EXPECTED_DATE,
    _derive_outcome,
    _text,
    _unique_match,
)


@pytest.mark.parametrize("apostrophe", ["'", "\u2019"], ids=["ascii", "curly"])
def test_contracted_negation_does_not_assert_a_completed_merger(apostrophe):
    raw = f"It isn{apostrophe}t true that the merger was completed.".encode()
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(_text(raw))


@pytest.mark.parametrize("apostrophe", ["'", "\u2019"], ids=["ascii", "curly"])
def test_contracted_negation_does_not_assert_an_exact_expected_date(apostrophe):
    raw = (
        f"It isn{apostrophe}t true that the merger is expected to close by October 1, 2026."
    ).encode()
    with pytest.raises(SemanticEvidenceUnavailable):
        _unique_match(_EXPECTED_DATE, _text(raw), field="expected resolution date")


def test_uncontracted_negation_already_refuses_outcome():
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(_text(b"It is not true that the merger was completed."))


def test_uncontracted_negation_already_refuses_expected_date():
    with pytest.raises(SemanticEvidenceUnavailable):
        _unique_match(
            _EXPECTED_DATE,
            _text(b"It is not true that the merger is expected to close by October 1, 2026."),
            field="expected resolution date",
        )
