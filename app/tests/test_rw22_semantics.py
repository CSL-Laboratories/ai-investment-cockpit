"""RW-22 / BUG-140: source qualifications cannot become verified cash terms."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from schemas.models.merger_edge import MergerEdgeEvidence, MergerSelectionCommitment
from strategies.merger_semantics import (
    SemanticEvidenceUnavailable,
    _derive_eligibility,
    _require_strategy_eligible,
    _text,
)
from tests.strategies import test_merger_semantics as fixtures


CASH = "each share of common stock listed on Nasdaq will receive $120 in cash per share"
QUALIFIED = [
    pytest.param(
        "The statement that " + CASH + ", as repeated in the preliminary transaction "
        "summary distributed to the holders of the company common stock, is false.",
        id="long-trailing-denial",
    ),
    pytest.param(
        "It is not true that, as described in the preliminary transaction summary "
        "distributed to the holders of the company common stock and discussed in the "
        "accompanying supplemental proxy disclosure materials, " + CASH + ".",
        id="long-leading-denial",
    ),
    pytest.param(
        "If a supplemental consideration agreement is entered into following completion "
        "of the ongoing negotiations between the parties and the subsequent review of "
        "the proposed economic terms by their respective boards, " + CASH + ".",
        id="long-leading-condition",
    ),
]
AFFIRMATIVE = [
    pytest.param(CASH + ".", id="plain-cash-control"),
    pytest.param(CASH + ", with no fractional shares issued.", id="fractional-issued"),
    pytest.param(
        CASH + ", and no fractional shares will be issued.", id="fractional-will-be-issued",
    ),
]


def _raw_deal(cash_sentence):
    ordinary = fixtures._deal_text()
    original = ("Each" + CASH[len("each"):] + ".").encode()
    assert original in ordinary
    return ordinary.replace(original, cash_sentence.encode())


@pytest.mark.parametrize("cash_sentence", QUALIFIED)
def test_distant_qualifier_refuses_cash_eligibility(cash_sentence):
    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(_text(_raw_deal(cash_sentence)), amended=False)


@pytest.mark.parametrize("cash_sentence", AFFIRMATIVE)
def test_exact_settlement_boilerplate_preserves_cash_eligibility(cash_sentence):
    label = _derive_eligibility(_text(_raw_deal(cash_sentence)), amended=False)
    _require_strategy_eligible(label)
    assert label["cash_offer"] == "120"


@pytest.mark.parametrize("suffix", [
    ", and no fractional shares may be issued.",
    ", and no fractional shares will not be issued.",
    ", with no fractional shares entitled to cash.",
    ", and no fractional shares issued under the agreement are entitled to payment.",
])
def test_settlement_exception_does_not_strip_other_qualifications(suffix):
    with pytest.raises(SemanticEvidenceUnavailable, match="cash-per-share"):
        _derive_eligibility(_text(_raw_deal(CASH + suffix)), amended=False)


@pytest.mark.parametrize("cash_sentence", QUALIFIED)
def test_signed_qualified_deal_cannot_mint_verified_edge(tmp_path, monkeypatch, cash_sentence):
    raw = _raw_deal(cash_sentence)
    monkeypatch.setattr(fixtures, "_deal_text", lambda: raw)
    state, _root, _client, market_data, provider, *_ = fixtures._semantic_system(tmp_path)

    # The same real provider/raw-store/audit composition used by the accepted control
    # must refuse the denied/conditional source even with a complete witnessed cohort.
    assert provider.estimate(
        fixtures._proposal(), quote=market_data.quote(fixtures.SYMBOL),
    ) is None
    with state.session_factory() as session:
        assert session.execute(select(MergerEdgeEvidence)).scalars().all() == []
        commitment = session.execute(select(MergerSelectionCommitment)).scalar_one()
        assert len(commitment.document["deals"]) == 30
    assert state.audit.verify_chain().ok


@pytest.mark.parametrize("cash_sentence", AFFIRMATIVE)
def test_signed_affirmative_deal_still_produces_verified_edge(
    tmp_path, monkeypatch, cash_sentence,
):
    raw = _raw_deal(cash_sentence)
    monkeypatch.setattr(fixtures, "_deal_text", lambda: raw)
    state, _root, _client, market_data, provider, *_ = fixtures._semantic_system(tmp_path)

    estimate = provider.estimate(
        fixtures._proposal(), quote=market_data.quote(fixtures.SYMBOL),
    )
    assert estimate is not None and estimate.verified
    assert estimate.expected_gross_return_bps > 0
    with state.session_factory() as session:
        edge = session.execute(select(MergerEdgeEvidence)).scalar_one()
        assert edge.document["semantic_authority_hash"]
    assert state.audit.verify_chain().ok
