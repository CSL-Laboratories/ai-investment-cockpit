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
<<<<<<< Updated upstream
    by the approval/veto path.
=======
    by the approval/veto path;
  * W11 (SEC-03 (d)/(a)): the LIVE capability token is minted via
    ``OrderGateway.mint_live_submit_token`` — bound to the COVERING signed ``EVENT_ADJUDICATION``
    at mint time — and ``agentic_place`` REFUSES unless the always-on runtime order tracer
    (``brokers/order_tracer.py``) guards the broker. The live order authority never runs untraced,
    and its capability never exists for an unapproved order.

SEC-02 hardening (W12): the platform's read tools return **read-all-accounts** payloads —
``fetch_agentic_summary`` is the mocked stand-in for that read (no live socket) and ALWAYS
routes its result through ``brokers/mcp_untrusted.py`` before returning it, so a caller only
ever sees the Agentic account's fields, each wrapped as untrusted content. There is no path
that hands a raw MCP payload to a caller.
>>>>>>> Stashed changes
"""
from __future__ import annotations

from decimal import Decimal

from brokers.mcp_untrusted import prepare_mcp_result
from core.ids import new_id
from workflows.packets import Packet


class RobinhoodMCPBroker:
    """In-process stub of the Robinhood Agentic MCP order tool. Held PRIVATELY by the
    ``OrderGateway`` as its ``_broker``. Acknowledges orders (no fill) — no network, no socket,
    no string-name dispatch."""

    def __init__(self, agentic_account_id: str = "agentic") -> None:
        self.submitted: list[dict] = []
        self.agentic_account_id = agentic_account_id

    def fetch_agentic_summary(self, raw_accounts_response: list[dict]) -> Packet:
        """Mocked MCP tool-RESULT read (SEC-02): ``raw_accounts_response`` stands in for what
        the future live read-all-accounts call would return. ALWAYS minimized to
        ``self.agentic_account_id`` and wrapped as untrusted content before it is returned —
        there is no raw-payload escape hatch."""
        return prepare_mcp_result(raw_accounts_response,
                                  agentic_account_id=self.agentic_account_id)

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
