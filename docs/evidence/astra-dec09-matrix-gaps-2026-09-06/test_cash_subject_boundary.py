"""Research refusal assertions for c5ef0ad; failures are retained as failures.

Synthetic raw text only. No signed selection, witness, credentials, database,
broker, or production configuration. This is a proposed test scope, not a repair.
Run from an isolated app source with the companion diagnostic launcher.
"""
from __future__ import annotations

import pytest

from strategies.merger_semantics import SemanticEvidenceUnavailable
from tests.strategies.test_rw21_real_filing_grammar import _synthetic_current_terms


def _document_with_cash_clause(tmp_path, clause):
    verifier, store, document = _synthetic_current_terms(tmp_path)
    # The expected document comes from the unchanged affirmative control, not
    # from deriving a label from the adversarial text under test.
    raw = (
        "Nasdaq. The company entered into a definitive merger. "
        f"{clause} "
        "Expected closing date: 2026-10-01. Outside date: 2026-11-01. "
        "The closing remains subject to regulatory approval."
    ).encode()
    store.write("synthetic-subject-boundary", raw)
    document = {
        **document,
        "source_accessions": ["synthetic-subject-boundary"],
        "announcement_accession": "synthetic-subject-boundary",
        "current_accession": "synthetic-subject-boundary",
    }
    return verifier, document


@pytest.mark.parametrize(
    "clause",
    [
        "Each share of target common stock will receive a distribution; "
        "each share of preferred stock will receive $105 in cash per share.",
        "Each share of target common stock will receive a distribution; "
        "acquirer common shares receive $105 in cash per share.",
    ],
    ids=["preferred-share-payout", "acquirer-share-payout"],
)
def test_current_deal_refuses_another_subjects_cash_after_target_verb(tmp_path, clause):
    verifier, document = _document_with_cash_clause(tmp_path, clause)
    with pytest.raises(SemanticEvidenceUnavailable):
        verifier._verify_deal(document)


def test_affirmative_target_cash_control_is_still_verifiable(tmp_path):
    verifier, document = _document_with_cash_clause(
        tmp_path,
        "Each share of target common stock will receive $105 in cash per share.",
    )
    verifier._verify_deal(document)


def test_sentence_boundary_does_not_supply_another_subjects_cash(tmp_path):
    verifier, document = _document_with_cash_clause(
        tmp_path,
        "Each share of target common stock will receive a distribution. "
        "Acquirer common shares receive $105 in cash per share.",
    )
    with pytest.raises(SemanticEvidenceUnavailable):
        verifier._verify_deal(document)
