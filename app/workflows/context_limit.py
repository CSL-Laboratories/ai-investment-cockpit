"""Hard context cap for schema-validated workflow prompts (CSL-42 / CSL-98).

Claude Haiku 5.5 bills the whole request at five times the short-context rate
once the prompt exceeds 100k input tokens. ``estimate_tokens`` uses UTF-8
bytes / 3, which sits just above the ~30% denser Claude 4.7+ tokenizer on
English, so a prompt this module accepts stays under that boundary.

``fit_packet`` drops a packet's tail until the wrapped items plus the
template overhead fit. ``call_agent`` refuses any rendered prompt that is
still over the cap and does not spawn a model.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.models import DEFAULT_CONTEXT_TOKEN_CAP, HAIKU_55_LONG_CONTEXT_TOKENS
from workflows.packets import Packet, PacketItem


def estimate_tokens(text: str) -> int:
    """Conservative token count. Empty text is 0."""
    if not text:
        return 0
    return (len(text.encode("utf-8")) + 2) // 3


def context_token_cap(settings) -> int:
    """The configured cap, or the default when the value is missing or illegal.

    A cap at or above the Haiku 5.5 surcharge boundary is never honored.
    """
    raw = getattr(settings, "context_token_cap", DEFAULT_CONTEXT_TOKEN_CAP)
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_TOKEN_CAP
    if 1 <= cap < HAIKU_55_LONG_CONTEXT_TOKENS:
        return cap
    return DEFAULT_CONTEXT_TOKEN_CAP


@dataclass(frozen=True)
class PacketFit:
    packet: Packet
    dropped: tuple[PacketItem, ...]
    estimated_tokens: int
    cap: int

    @property
    def fits(self) -> bool:
        return self.estimated_tokens <= self.cap


def fit_packet(packet: Packet, *, reserved_tokens: int, cap: int) -> PacketFit:
    """Keep a prefix of ``packet`` whose wrapped text fits under ``cap``.

    ``reserved_tokens`` is the template with an empty packet block. The tail
    is what gets dropped, so callers that need a subject event kept should
    place that item first. The sum of the two estimates is at least the
    estimate of the concatenated prompt, so a fit packet stays under the cap
    when it is rendered.
    """
    if reserved_tokens < 0:
        raise ValueError("reserved_tokens must be >= 0")
    if cap < 1:
        raise ValueError("context cap must be positive")
    items = list(packet.items)
    while items:
        block = "\n\n".join(item.text for item in items)
        estimated = estimate_tokens(block) + reserved_tokens
        if estimated <= cap:
            kept = Packet(items=tuple(items))
            return PacketFit(
                packet=kept,
                dropped=tuple(packet.items[len(items):]),
                estimated_tokens=estimated,
                cap=cap,
            )
        items.pop()
    return PacketFit(
        packet=Packet(items=()),
        dropped=tuple(packet.items),
        estimated_tokens=reserved_tokens,
        cap=cap,
    )
