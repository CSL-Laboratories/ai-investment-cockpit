"""WP7 — the Robinhood Agentic (MCP) broker stub (Inv 1; SEC-03).

The Robinhood Agentic endpoint is an MCP surface: the live platform would let an agent call
its order tool DIRECTLY, WITHOUT any local review step. Invariant 1 forbids that — an order
must reach a broker ONLY through risk-pass AND mode-check AND approval/veto. So this client
NEVER exposes a direct submit: it routes every order through ``OrderGateway.place_equity_order``,
which PROVABLY runs ``review_equity_order`` (the full SEC-01/04/07/08 re-check) before the
submit. The platform not forcing review is exactly why we force it here.

SEC-03 hardening:
  * ``RobinhoodMCPBroker`` (the broker seam the gateway holds) is a PRIVATE instance attribute
    of the gateway — never a module global, never dispatched by string name;
  * there is NO live socket / MCP transport here — ``submit_order`` is an in-process stub;
  * ``agentic_place`` takes the ``OrderGateway`` explicitly and calls the gated path — a
    caller cannot reach ``submit_order`` on the broker without a live capability token minted
    by the approval/veto path.
"""
from __future__ import annotations

from decimal import Decimal

from core.ids import new_id


class RobinhoodMCPBroker:
    """In-process stub of the Robinhood Agentic MCP order tool. Held PRIVATELY by the
    ``OrderGateway`` as its ``_broker``. Acknowledges orders (no fill) — no network, no socket,
    no string-name dispatch."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit_order(self, canonical_tuple: dict) -> dict:
        oid = new_id("rh")
        self.submitted.append({"broker_order_id": oid, **canonical_tuple})
        return {
            "broker_order_id": oid,
            "status": "accepted",
            "filled_qty": Decimal("0"),
            "avg_fill_price": None,
        }


def agentic_place(gateway, order, *, approval, risk_decision, ladder, breaker_engine,
                  risk_recheck, positions, token_nonce, producing_runtime, clock=None):
    """Route a Robinhood-Agentic order through the OrderGateway's gated path. This is the ONLY
    sanctioned entry for the MCP surface: it delegates to ``place_equity_order`` (review THEN
    submit), so the local review always precedes the submit even though the live platform
    would not force it (Inv 1)."""
    return gateway.place_equity_order(
        order, approval=approval, risk_decision=risk_decision, ladder=ladder,
        breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
        token_nonce=token_nonce, producing_runtime=producing_runtime, clock=clock,
    )
