"""Independent RW-22 adversarial review of the cash-settlement exception."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from schemas.models.merger_edge import MergerEdgeEvidence
from strategies.merger_semantics import _CASH, _assertion_matches, _text
from tests.strategies import test_merger_semantics as fixtures


CASH = "each share of common stock listed on Nasdaq will receive $120 in cash per share"
PADDING = "the description circulated to transaction participants and their advisers " * 5

QUALIFIED_CASH = [
    "It is false that " + PADDING + CASH + ", with no fractional shares issued.",
    CASH + ", with no fractional shares issued; " + PADDING + "this assertion is denied.",
    "Unless " + PADDING + "a revised agreement is signed, " + CASH
    + ", and no fractional shares will be issued.",
    CASH + ", and no fractional shares will be issued; " + PADDING
    + "this payment may be made under a future agreement.",
]


@pytest.mark.parametrize("text", QUALIFIED_CASH)
def test_settlement_exception_cannot_hide_distant_cash_qualifier(text):
    normalized = _text(text.encode())
    assert _CASH.search(normalized) is not None  # exercise filtering, not a grammar miss
    assert list(_assertion_matches(_CASH, normalized)) == []


@pytest.mark.parametrize("clause", [
    "with no fractional shares not issued",
    "with no fractional shares falsely issued",
    "no fractional shares will never be issued",
    "no fractional shares will not be issued",
    "no fractional shares may be issued",
    "with no fractional shares issuedness",
    "with no fractional shares issued unless approved",
    "no fractional shares will be issued if approved",
])
def test_unrecognized_or_qualified_issuance_clause_remains_visible(clause):
    normalized = _text((CASH + ", " + clause + ".").encode())
    assert _CASH.search(normalized) is not None
    assert list(_assertion_matches(_CASH, normalized)) == []


@pytest.mark.parametrize("text", [
    "With no fractional shares issued, " + CASH + ".",
    "No fractional shares will be issued; " + CASH + ".",
    CASH + ", with no fractional shares issued; no fractional shares will be issued.",
])
def test_exact_unqualified_settlement_clauses_work_at_sentence_boundaries(text):
    matches = list(_assertion_matches(_CASH, _text(text.encode())))
    assert len(matches) == 1 and matches[0].group(1) == "120"


@pytest.mark.parametrize("text", QUALIFIED_CASH[1::2])
def test_signed_provider_refuses_qualification_after_exempt_settlement(
    tmp_path, monkeypatch, text,
):
    original = fixtures._deal_text()
    cash_sentence = ("Each" + CASH[len("each"):] + ".").encode()
    assert cash_sentence in original
    raw = original.replace(cash_sentence, text.encode())
    monkeypatch.setattr(fixtures, "_deal_text", lambda: raw)
    state, _root, _client, market_data, provider, *_ = fixtures._semantic_system(tmp_path)

    assert provider.estimate(fixtures._proposal(), quote=market_data.quote(fixtures.SYMBOL)) is None
    with state.session_factory() as session:
        assert session.execute(select(MergerEdgeEvidence)).scalars().all() == []
    assert state.audit.verify_chain().ok
