"""WP7 shared seam — the single-use submit *capability* token (SEC-03).

Invariant 1 must guard a *capability*, not a function name. A submit reaches the broker only
by presenting a ``SubmitToken`` that:

  * is minted ONLY by the approval/veto path (``bridge/approval.py`` / ``bridge/veto.py``) —
    a CI grep asserts no other caller mints; the gateway never mints its own token;
  * is bound to the exact ``(proposal_id, risk_decision_id, order_tuple_hash)`` the human (or
    the elapsed veto window) authorized — a Modify / re-price changes the tuple hash, so the
    old token's binding no longer matches and a NEW approval must mint a NEW token;
  * is single-use — consumed *inside* the locked submit transaction, so a replayed submit
    finds no live token.

A compromised caller that holds the ``OrderGateway`` object still cannot submit: it has no
live, matching token, and forging one requires the mint path, which requires a valid
``ApprovalRecord``. The registry is the gateway's private state (never a module global).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


class CapabilityError(Exception):
    """A submit was attempted without a live, matching single-use capability token."""


@dataclass(frozen=True)
class SubmitToken:
    nonce: str
    proposal_id: str
    risk_decision_id: str
    order_tuple_hash: str

    def binding(self) -> tuple[str, str, str]:
        return (self.proposal_id, self.risk_decision_id, self.order_tuple_hash)


class TokenRegistry:
    """Live (unconsumed) submit tokens. The ``OrderGateway`` holds exactly ONE privately; the
    approval/veto path mints into it; the locked submit transaction consumes from it. Consume
    is single-use and fail-closed: ANY consume attempt burns the nonce (a wrong-binding replay
    voids the token, forcing re-approval rather than risking a mismatched submit)."""

    def __init__(self) -> None:
        self._live: dict[str, SubmitToken] = {}
        self._lock = threading.Lock()

    def mint(self, *, nonce: str, proposal_id: str, risk_decision_id: str,
             order_tuple_hash: str) -> SubmitToken:
        if not nonce:
            raise CapabilityError("token nonce must be non-empty")
        token = SubmitToken(nonce, proposal_id, risk_decision_id, order_tuple_hash)
        with self._lock:
            if nonce in self._live:
                raise CapabilityError(f"duplicate token nonce {nonce!r}")
            self._live[nonce] = token
        return token

    def consume(self, *, nonce: str, proposal_id: str, risk_decision_id: str,
                order_tuple_hash: str) -> SubmitToken:
        """Pop the token (single-use: burns the nonce regardless of outcome) and validate its
        binding. Raises ``CapabilityError`` on a missing/replayed nonce or a binding mismatch
        (Modify/re-price) — the caller must treat the submit as refused."""
        with self._lock:
            token = self._live.pop(nonce, None)
        if token is None:
            raise CapabilityError(f"no live token for nonce {nonce!r} (replay / never minted)")
        if token.binding() != (proposal_id, risk_decision_id, order_tuple_hash):
            raise CapabilityError("token binding mismatch (modify/re-price needs new approval)")
        return token

    def is_live(self, nonce: str) -> bool:
        with self._lock:
            return nonce in self._live

    def live_count(self) -> int:
        with self._lock:
            return len(self._live)
