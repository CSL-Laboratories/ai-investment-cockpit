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
    by the approval/veto path;
  * W11 (SEC-03 (d)/(a)): the LIVE capability token is minted via
    ``OrderGateway.mint_live_submit_token`` — bound to the COVERING signed ``EVENT_ADJUDICATION``
    at mint time — and ``agentic_place`` REFUSES unless the always-on runtime order tracer
    (``brokers/order_tracer.py``) guards the broker. The live order authority never runs untraced,
    and its capability never exists for an unapproved order.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable

from brokers.mcp_untrusted import HardenedMCP, harden_mcp_result, minimize_to_agentic
from core.ids import new_id


class RobinhoodMCPBroker:
    """In-process stub of the Robinhood Agentic MCP order tool. Held PRIVATELY by the
    ``OrderGateway`` as its ``_broker``. Acknowledges orders (no fill) — no network, no socket,
    no string-name dispatch."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def read_agentic_snapshot(self, fetch: Callable[[], dict], *, agentic_account_id: str,
                              subject_symbols: frozenset[str] = frozenset()) -> HardenedMCP:
        """W12 SEC-02 read seam: the MCP RESPONSE is a first-class prompt-injection input, so its
        free text is neutralized before it can reach any prompt.

        ``fetch`` is INJECTED — a mock in tests; W17 wires the live MCP read (no live socket is
        opened here). The raw payload is minimized to the Agentic account ONLY (every other
        account's holdings are dropped and FAIL CLOSED if the Agentic account can't be positively
        identified), then every free-text field is escaped, SEC-09-scanned, and wrapped as
        ``mcp:robinhood`` untrusted-content. Only the returned :class:`HardenedMCP.prompt_block`
        may enter a model context."""
        minimized = minimize_to_agentic(fetch(), agentic_account_id=agentic_account_id)
        return harden_mcp_result(minimized, source="robinhood", subject_symbols=subject_symbols)

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
    would not force it (Inv 1).

    W11 (SEC-03): FAIL CLOSED unless the always-on runtime order tracer guards the broker — the
    live order authority must never run untraced (a bypass of the gateway would otherwise reach
    the live socket unobserved). The forwarded ``token_nonce`` is a live capability minted via
    ``gateway.mint_live_submit_token`` (bound to the covering signed adjudication)."""
    from brokers.order_tracer import OrderAuthorityViolation, is_installed

    if not is_installed(gateway._broker):
        raise OrderAuthorityViolation(
            "SEC-03: the live Robinhood-Agentic path requires the runtime order tracer installed "
            "on the broker; refusing to place an order untraced."
        )
    return gateway.place_equity_order(
        order, approval=approval, risk_decision=risk_decision, ladder=ladder,
        breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
        token_nonce=token_nonce, producing_runtime=producing_runtime, clock=clock,
    )
