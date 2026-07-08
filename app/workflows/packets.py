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
        wrapped = (
            f'<untrusted-content source="{ev.source}" article_ref="{ev.raw_ref}" ts="{ts}">\n'
            f"{inner}\n"
            f"</untrusted-content>"
        )
        items.append(PacketItem(event_id=ev.id, raw_ref=ev.raw_ref, symbol=ev.symbol,
                                text=wrapped))
    return Packet(items=tuple(items))
