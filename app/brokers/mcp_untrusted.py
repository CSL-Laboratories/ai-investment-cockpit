"""W12 — untrusted MCP tool-result content (security-hardening-backlog SEC-02).

The Robinhood Agentic MCP read tools return read-**all**-accounts payloads whose
free-text fields (position memos, instrument names, the ``review_equity_order`` message)
carry injected instructions once they re-enter a prompt. This module is the ONE seam any
future MCP read result must pass through before a prompt sees it:

  1. **minimize** to the Agentic account BEFORE any prompt — a non-Agentic account's
     holdings/notes must never enter a prompt at all (the live platform can read all
     accounts; this app must not act on that);
  2. treat every remaining free-text field as untrusted exactly like a news item —
     ``scrub_text`` (SEC-09) -> ``escape_untrusted`` (prompt-injection-defense M3) ->
     ``wrap_untrusted`` (M2), reusing the SAME primitives ``workflows/packets.py`` uses for
     news. No parallel escaper.

No live MCP transport here (SEC-03: ``RobinhoodMCPBroker.submit_order`` stays the only
broker seam) — this module only shapes a MOCKED tool-result payload the way the real read
path will need to once Phase 3 wires it.
"""
from __future__ import annotations

from typing import Sequence

from core.ids import new_id
from ingest.normalize import escape_untrusted
from workflows.packets import Packet, PacketItem, wrap_untrusted
from workflows.scrubber import scrub_text

# Free-text fields that may carry injected content once they re-enter a prompt. Checked on
# both the account object and each of its positions (a position-level memo is exactly as
# untrusted as an account-level note).
_FREE_TEXT_FIELDS = ("notes", "memo", "description", "review_message")


def minimize_to_agentic_account(accounts: Sequence[dict], *, agentic_account_id: str) -> list[dict]:
    """Drop every account except the Agentic one BEFORE any of its fields reach a prompt."""
    return [a for a in accounts if a.get("account_id") == agentic_account_id]


def _iter_free_text(account: dict):
    """Yield ``(field_ref, raw_text)`` for every free-text field on the account and each of
    its positions."""
    account_id = account.get("account_id", "unknown")
    for field in _FREE_TEXT_FIELDS:
        value = account.get(field)
        if value:
            yield f"{account_id}:{field}", str(value)
    for i, pos in enumerate(account.get("positions") or []):
        for field in _FREE_TEXT_FIELDS:
            value = pos.get(field)
            if value:
                yield f"{account_id}:positions[{i}].{field}", str(value)


def wrap_mcp_field(raw_text: str, *, ref: str, source: str = "mcp:robinhood") -> str:
    """SEC-09 scrub, THEN prompt-injection escape, THEN wrap — same order as the news path,
    so an MCP field gets no less scrutiny than a news headline."""
    scrubbed = scrub_text(raw_text).clean
    inner = escape_untrusted(scrubbed)
    return wrap_untrusted(inner, source=source, ref=ref)


def prepare_mcp_result(accounts: Sequence[dict], *, agentic_account_id: str,
                       source: str = "mcp:robinhood") -> Packet:
    """The ONE entry point for an MCP tool-result payload: minimize to the Agentic account,
    then wrap every free-text field as untrusted content. Returns a ``Packet`` (the same
    shape ``workflows/packets.py`` produces for news) ready to fold into a prompt block."""
    minimized = minimize_to_agentic_account(accounts, agentic_account_id=agentic_account_id)
    items = []
    for account in minimized:
        for ref, raw_text in _iter_free_text(account):
            wrapped = wrap_mcp_field(raw_text, ref=ref, source=source)
            items.append(PacketItem(event_id=new_id("mcpitem"), raw_ref=ref,
                                    symbol="", text=wrapped))
    return Packet(items=tuple(items))
