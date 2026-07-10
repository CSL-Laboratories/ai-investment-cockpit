"""WP8 — untrusted-content packet builder (contract Owner A 'packets.py').

Implements M2 of docs/prompt-injection-defense.md §4: every external item enters a prompt
wrapped in ``<untrusted-content ...>`` delimiters under the standing DATA-never-INSTRUCTIONS
instruction (carried by the prompt templates). The inner text is re-passed through
``ingest.normalize.escape_untrusted`` — idempotent by design (M3/SEC-22 defense in depth) —
so a delimiter smuggled into a headline cannot close the wrapper. ``dispatched_refs``
records the EXACT set of raw_refs sent to the model — the deterministic corroboration
anchor for SEC-14 (a proposal may only cite members of the dispatched packet).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ingest.normalize import escape_untrusted
from schemas.models.market_event import MarketEvent


def _attr_escape(value) -> str:
    """Escape ONE attribute VALUE for a double-quoted wrapper attribute. An attacker-controlled
    MCP ``source``/field value must not be able to break out of the quoted attribute, so & < >
    AND the double-quote are neutralized (& first -> idempotent). This is the attribute-context
    twin of ``escape_untrusted`` (which guards element text)."""
    s = value if isinstance(value, str) else str(value)
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def wrap_untrusted(inner: str, *, source: str, **attrs) -> str:
    """Assemble ONE ``<untrusted-content ...>...</untrusted-content>`` block. ``inner`` is ALREADY
    escaped+capped by the caller (``escape_untrusted``) — this function never re-escapes element
    text. ``source`` and every attr VALUE are attribute-escaped so an attacker-controlled MCP
    source/field value cannot break out of the double-quoted attribute. Attribute order: ``source``
    first, then ``attrs`` in insertion order (e.g. ``article_ref``, ``ts``)."""
    parts = [f'source="{_attr_escape(source)}"']
    parts.extend(f'{k}="{_attr_escape(v)}"' for k, v in attrs.items())
    return f'<untrusted-content {" ".join(parts)}>\n{inner}\n</untrusted-content>'


@dataclass(frozen=True)
class PacketItem:
    event_id: str
    raw_ref: str
    symbol: str
    text: str  # the full wrapped <untrusted-content ...>...</untrusted-content> block


@dataclass(frozen=True)
class Packet:
    items: tuple[PacketItem, ...]

    @property
    def dispatched_refs(self) -> frozenset[str]:
        """The exact raw_ref set dispatched to the model (SEC-14 corroboration anchor)."""
        return frozenset(i.raw_ref for i in self.items)

    def prompt_block(self) -> str:
        """Concatenated wrapped blocks, ready to substitute into a prompt template."""
        return "\n\n".join(i.text for i in self.items)


def build_packet(events: Sequence[MarketEvent], *, max_item_chars: int = 1200) -> Packet:
    items = []
    for ev in events:
        headline = ev.headline or ""
        body = f"{headline} — {ev.summary}" if ev.summary else headline
        inner = escape_untrusted(body)[:max_item_chars]
        ts = ev.ts.isoformat() if ev.ts is not None else ""
        # SEAM 1: identical bytes to the previous inline f-string for news — ALLOWED_SOURCES
        # values (and raw_ref/ts) contain no < > & " so _attr_escape is a no-op here.
        wrapped = wrap_untrusted(inner, source=ev.source, article_ref=ev.raw_ref, ts=ts)
        items.append(PacketItem(event_id=ev.id, raw_ref=ev.raw_ref, symbol=ev.symbol,
                                text=wrapped))
    return Packet(items=tuple(items))
