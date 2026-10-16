"""Hard context cap for schema-validated workflow prompts (CSL-42 / CSL-98).

Claude Haiku 5.5 bills the whole request at five times the short-context rate
once the prompt exceeds 100k input tokens. ``estimate_tokens`` counts UTF-8
bytes / 2. Number-heavy text is about 2.5 bytes per token, and bytes / 3
under a 90k cap accepted about 270KB of that text (~108k real tokens), past
the surcharge. Bytes / 2 stays at or above a 2-byte tokenizer: an estimate
under 100_000 is at most 200_000 bytes, which is under 100k tokens at 2
bytes each and under 80k tokens at 2.5 bytes each. The configured cap is
itself kept strictly below 100_000.

``fit_packet`` drops a packet's tail until the wrapped items plus the
template overhead fit. ``call_agent`` refuses any rendered prompt that is
still over the cap and does not spawn a model.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.models import DEFAULT_CONTEXT_TOKEN_CAP, HAIKU_55_LONG_CONTEXT_TOKENS
from workflows.packets import Packet, PacketItem


def estimate_tokens(text: str) -> int:
    """Conservative token count. Empty text is 0.

    ``(utf-8 bytes + 1) // 2`` is the ceiling of bytes / 2. A prompt this
    function scores under 100_000 cannot reach Haiku 5.5's 100k tier at 2
    bytes per token, which is denser than the ~2.5 bytes/token of digits
    and JSON.
    """
    if not text:
        return 0
    return (len(text.encode("utf-8")) + 1) // 2


def repair_reserve_tokens() -> int:
    """Estimated tokens ``call_agent`` holds back for a worst-case repair suffix.

    Callers add this to the template overhead given to ``fit_packet`` so a packet
    sheds tail items instead of being refused by the call gate near the cap.
    """
    from agent_runner.runner import worst_case_repair_overhead

    return estimate_tokens(worst_case_repair_overhead())


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
