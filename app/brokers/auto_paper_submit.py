"""RW-17 — the ONE sanctioned entry by which an AUTO-ADJUDICATED order reaches the broker.

Invariant 1's static call-site gate (``tests/test_invariants.py``) requires every
order-placement call site to live under ``brokers/``, so that a reviewer auditing this package
sees the COMPLETE list of ways an order can be placed. ``copytrade/autoexec.py`` adjudicates
without a human, which makes it precisely the caller that must NOT be able to hide: it routes
here, and this module is the thing an auditor greps.

Modelled on ``brokers/robinhood_mcp.agentic_place`` — the same shape, for the same reason (the
MCP surface does not call the gateway directly either), and like it this is a delegation to
``place_equity_order`` (review THEN submit) rather than a second order path. Every gate (a)-(h)
runs exactly as it does for a human-approved order; nothing here relaxes one.

IT ADDS ONE CONTROL OF ITS OWN, and that is why it is not a bare pass-through: it FAILS CLOSED
unless the gateway it was handed is the PAPER-scoped one. The zero-touch lane replaces the
operator's approval tap, and the entire safety argument for doing that is that the capital is
paper. A gateway whose ``execution_scope`` is ``'live'`` would still refuse this order at gate
(b) (``_ORDER_AUTHORITY_BY_SCOPE['live']`` does not contain ``paper``, and the lane only ever
resolves in ``Mode.paper``) — but "it would have been refused later" is not the posture to take
about an auto-adjudicated order in front of a live transport. Refuse it HERE, loudly, at the
seam whose whole job is to be the audited boundary.
"""
from __future__ import annotations

from brokers.capability import CapabilityError

PAPER_SCOPE = "paper"


def auto_paper_place(gateway, order, *, approval, risk_decision, ladder, breaker_engine,
                     risk_recheck, positions, token_nonce, producing_runtime, clock=None):
    """Place an order whose approval was minted by an AUDITED AUTO-ADJUDICATION, not a human.

    ``approval`` must be a persisted ``ApprovalRecord`` covered by an HMAC-signed
    ``EVENT_ADJUDICATION`` binding this exact order — the gateway re-loads and re-verifies both
    itself (gate (e)) and refuses otherwise, so this function cannot confer authority it was not
    given. Raises ``CapabilityError`` if the gateway is not paper-scoped.
    """
    scope = getattr(gateway, "_execution_scope", None)
    if scope != PAPER_SCOPE:
        raise CapabilityError(
            f"RW-17: the zero-touch auto-execution lane may place orders ONLY through a "
            f"paper-scoped OrderGateway; this gateway's execution_scope is {scope!r}. "
            "Auto-adjudication without a human exists because the capital is paper."
        )
    return gateway.place_equity_order(
        order, approval=approval, risk_decision=risk_decision, ladder=ladder,
        breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
        token_nonce=token_nonce, producing_runtime=producing_runtime, clock=clock,
    )
