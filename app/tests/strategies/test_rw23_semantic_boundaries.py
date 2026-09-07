"""BUG-146: markup boundaries cannot end the scope of a source qualification."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from schemas.models.merger_edge import MergerEdgeEvidence
from strategies.merger_semantics import (
    SemanticEvidenceUnavailable,
    _EXPECTED_DATE,
    _derive_eligibility,
    _derive_outcome,
    _require_strategy_eligible,
    _text,
    _unique_match,
)
from tests.strategies import test_merger_semantics as fixtures


CASH = "each share of common stock listed on Nasdaq will receive $120 in cash per share"
AGREEMENT = "The registrant entered into a definitive merger. "
BOUNDARIES = [
    pytest.param("<!-- page marker -->", id="comment"),
    pytest.param("<br>", id="line-break"),
    pytest.param("<span hidden>layout</span>", id="hidden-span"),
    pytest.param("</p><p>", id="paragraph"),
]


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("qualified", [
    "It is not true that {boundary}" + CASH + ".",
    CASH + "{boundary}, and that statement is false.",
    "Unless a future agreement is signed, {boundary}" + CASH + ".",
    CASH + "{boundary}, if a future agreement is signed.",
])
def test_cash_qualifiers_survive_markup_in_the_same_sentence(boundary, qualified):
    raw = (AGREEMENT + qualified.format(boundary=boundary)).encode()
    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(_text(raw), amended=False)


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_outcome_qualification_survives_markup(boundary):
    raw = ("It is not true that " + boundary + "the merger has been completed.").encode()
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(_text(raw))


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_date_qualification_survives_markup(boundary):
    raw = ("It is not true that " + boundary + "expected closing date: May 1, 2026.").encode()
    with pytest.raises(SemanticEvidenceUnavailable):
        _unique_match(_EXPECTED_DATE, _text(raw), field="date")


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_agreement_qualification_survives_markup(boundary):
    raw = ("It is not true that " + boundary + AGREEMENT + CASH + ".").encode()
    with pytest.raises(SemanticEvidenceUnavailable, match="definitive"):
        _derive_eligibility(_text(raw), amended=False)


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_boundaries_cannot_join_incomplete_cash_or_outcome_fragments(boundary):
    # Cash has a wide wildcard between its security identity and payment predicate;
    # replacing the old period with mere whitespace would still join these fragments.
    raw = (AGREEMENT + "Each share of common stock listed on Nasdaq " + boundary
           + "will receive $120 in cash per share.").encode()
    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(_text(raw), amended=False)
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_outcome(_text(("The merger was " + boundary + "completed.").encode()))


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_complete_affirmative_terms_remain_valid_after_real_sentence_ends(boundary):
    raw = ("No payment is asserted in the preceding example. " + boundary
           + AGREEMENT + CASH + "." + boundary
           + "Expected closing date: May 1, 2026.").encode()
    text = _text(raw)
    label = _derive_eligibility(text, amended=False)
    _require_strategy_eligible(label)
    assert label["cash_offer"] == "120"
    assert _unique_match(_EXPECTED_DATE, text, field="date") == "may 1, 2026"
    outcome_raw = (boundary + "The merger was completed." + boundary).encode()
    assert _derive_outcome(_text(outcome_raw)) == "closed"


@pytest.mark.parametrize("marker", ["\ufffc", "&#xfffc;", "&#65532;"])
def test_source_cannot_supply_internal_boundary_markers(marker):
    with pytest.raises(SemanticEvidenceUnavailable, match="markup"):
        _text((AGREEMENT + marker + CASH + ".").encode())


@pytest.mark.parametrize("boundary", BOUNDARIES[:3])
def test_signed_provider_refuses_cash_denied_across_markup(tmp_path, monkeypatch, boundary):
    raw = fixtures._deal_text().replace(
        ("Each" + CASH[len("each"):] + ".").encode(),
        ("It is not true that " + boundary + CASH + ".").encode(),
    )
    monkeypatch.setattr(fixtures, "_deal_text", lambda: raw)
    state, _root, _client, market_data, provider, *_ = fixtures._semantic_system(tmp_path)

    assert provider.estimate(fixtures._proposal(), quote=market_data.quote(fixtures.SYMBOL)) is None
    with state.session_factory() as session:
        assert session.execute(select(MergerEdgeEvidence)).scalars().all() == []
    assert state.audit.verify_chain().ok
