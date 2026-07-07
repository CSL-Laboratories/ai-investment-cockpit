"""WP7 shared seam — single-use submit capability token (SEC-03)."""
from __future__ import annotations

import pytest

from brokers.capability import CapabilityError, TokenRegistry


def _mint(reg, nonce="n1", **over):
    fields = dict(proposal_id="p1", risk_decision_id="rd1", order_tuple_hash="h1")
    fields.update(over)
    return reg.mint(nonce=nonce, **fields)


def test_mint_then_consume_matching_binding():
    reg = TokenRegistry()
    _mint(reg)
    tok = reg.consume(nonce="n1", proposal_id="p1", risk_decision_id="rd1", order_tuple_hash="h1")
    assert tok.proposal_id == "p1"
    assert reg.live_count() == 0


def test_replay_after_consume_is_refused():
    reg = TokenRegistry()
    _mint(reg)
    reg.consume(nonce="n1", proposal_id="p1", risk_decision_id="rd1", order_tuple_hash="h1")
    with pytest.raises(CapabilityError, match="replay / never minted"):
        reg.consume(nonce="n1", proposal_id="p1", risk_decision_id="rd1", order_tuple_hash="h1")


def test_never_minted_nonce_refused():
    reg = TokenRegistry()
    with pytest.raises(CapabilityError, match="replay / never minted"):
        reg.consume(nonce="ghost", proposal_id="p1", risk_decision_id="rd1", order_tuple_hash="h1")


def test_binding_mismatch_refused_and_burns_token():
    reg = TokenRegistry()
    _mint(reg)  # bound to h1
    with pytest.raises(CapabilityError, match="binding mismatch"):
        reg.consume(nonce="n1", proposal_id="p1", risk_decision_id="rd1", order_tuple_hash="h2")
    # single-use: even the correct binding now fails — the nonce was burned (fail closed)
    assert not reg.is_live("n1")
    with pytest.raises(CapabilityError):
        reg.consume(nonce="n1", proposal_id="p1", risk_decision_id="rd1", order_tuple_hash="h1")


def test_duplicate_nonce_mint_refused():
    reg = TokenRegistry()
    _mint(reg)
    with pytest.raises(CapabilityError, match="duplicate token nonce"):
        _mint(reg)


def test_empty_nonce_refused():
    reg = TokenRegistry()
    with pytest.raises(CapabilityError, match="non-empty"):
        _mint(reg, nonce="")
