#!/usr/bin/env python3
"""E2 reconstruct-never-copy filter for sandbox-runtime wrap lines (CSL-450 D10).

Input is a root-only fd-2 capture. Public output is a token subset reconstructed
from constants, never a copy of input bytes. Extra St() templates (C1–C7) are
an allowlist miss and are dropped. A lone forged full-match still reports
``observed`` (U2 / GAPS U-10); authenticity is E1's fd/deny/sudo story.

This module is the reviewed grammar. D10's production relay is a statically
linked binary with the same grammar compiled in; do not treat a CPython
invocation as that binary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple

# D10 §2.3 step 1 / N10. Capture-time cap; the filter then reads exactly this
# many stored bytes (NUL-padded if short) and always scans the whole buffer.
CAP_BYTES = 1 * 1024 * 1024
# D10 §2.3 step 2 / feas-capture.
MAX_LINE = 512

# Binary wrap logger: tokens in this order, each at most once.
TOKEN_ORDER: Tuple[str, ...] = (
    "network",
    "filesystem",
    "env",
    "seccomp(unix-block)",
)
TOKEN_SET = frozenset(TOKEN_ORDER)

# Matching line as emitted by St() under SRT_DEBUG (D10 §0.1, §2.3 step 3).
MATCH_PREFIX = "[SandboxDebug] [Sandbox Linux] Wrapped command with bwrap ("
MATCH_SUFFIX = " restrictions)"

# Reconstructed public line: constants only, no [SandboxDebug] prefix (D10 §2.3 step 5).
RECONSTRUCT_PREFIX = "[Sandbox Linux] Wrapped command with bwrap ("
RECONSTRUCT_SUFFIX = " restrictions)"

STATUS_OBSERVED = "observed"
STATUS_NOT_OBSERVED = "not_observed"
STATUS_CONFLICT = "conflict"


@dataclass(frozen=True)
class FilterResult:
    """Public status plus internal counts that must not be logged (D10 §2.3 step 6)."""

    status: str
    tokens: FrozenSet[str] = field(default_factory=frozenset)
    reconstructed: Optional[str] = None
    # Empty agreed set is a match but not positive §1.7 evidence (D10 §2.3 step 3).
    positive_evidence: bool = False
    matches: int = 0
    bad_utf8: int = 0
    cr: int = 0
    overlong: int = 0
    dropped: int = 0


def apply_cap(data: bytes, cap: int = CAP_BYTES) -> Tuple[bytes, int]:
    """Keep the first ``cap`` bytes; count any remainder as truncated (N10).

    The capture process keeps reading and discarding after the cap so a full
    FIFO cannot block the CLI. The filter itself never sees the discarded tail.
    """
    if cap < 0:
        raise ValueError("cap")
    if len(data) <= cap:
        return bytes(data), 0
    return bytes(data[:cap]), len(data) - cap


def capture_buffer(data: bytes, cap: int = CAP_BYTES) -> bytes:
    """Exactly ``cap`` bytes: stored prefix, NUL-padded if short (D10 §2.3 step 7)."""
    stored, _truncated = apply_cap(data, cap)
    if len(stored) < cap:
        return stored + b"\0" * (cap - len(stored))
    return stored


def split_lines(buf: bytes) -> List[bytes]:
    """Split on ``\\n`` only. A trailing fragment without newline is still a line."""
    if not buf:
        return []
    parts = buf.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
    return parts


def decode_line(raw: bytes) -> Tuple[Optional[str], str]:
    """Return ``(text, skip_reason)``. skip_reason is empty on a usable line.

    Reasons: ``overlong``, ``cr``, ``bad_utf8``. A whole-input reject on invalid
    UTF-8 would be a model veto of an authentic literal; D10 does not do that.
    """
    if len(raw) > MAX_LINE:
        return None, "overlong"
    if b"\r" in raw:
        return None, "cr"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "bad_utf8"
    return text, ""


def parse_wrap_tokens(line: str) -> Optional[FrozenSet[str]]:
    """Anchored full match. Tokens at most once, canonical order. Else None."""
    if not line.startswith(MATCH_PREFIX) or not line.endswith(MATCH_SUFFIX):
        return None
    inner = line[len(MATCH_PREFIX) : len(line) - len(MATCH_SUFFIX)]
    if inner == "":
        return frozenset()
    parts = inner.split(", ")
    if any(part == "" for part in parts):
        return None
    seen: List[str] = []
    last_index = -1
    for part in parts:
        if part not in TOKEN_SET or part in seen:
            return None
        idx = TOKEN_ORDER.index(part)
        if idx <= last_index:
            return None
        last_index = idx
        seen.append(part)
    return frozenset(seen)


def reconstruct_line(tokens: Iterable[str]) -> str:
    """Build the public wrap line from constants. No input byte is copied."""
    agreed = frozenset(tokens)
    if not agreed.issubset(TOKEN_SET):
        raise ValueError("tokens")
    ordered = [name for name in TOKEN_ORDER if name in agreed]
    inner = ", ".join(ordered)
    return RECONSTRUCT_PREFIX + inner + RECONSTRUCT_SUFFIX


def all_subsets() -> Tuple[FrozenSet[str], ...]:
    """The finite public alphabet: 16 subsets of the four tokens, including empty."""
    out: List[FrozenSet[str]] = []
    n = len(TOKEN_ORDER)
    for mask in range(1 << n):
        out.append(frozenset(TOKEN_ORDER[i] for i in range(n) if mask & (1 << i)))
    return tuple(out)


def filter_capture(data: bytes, cap: int = CAP_BYTES) -> FilterResult:
    """D10 §2.3 steps 2–5, 8. Always scans the whole ``cap``-byte buffer."""
    buf = capture_buffer(data, cap)
    sets: List[FrozenSet[str]] = []
    bad_utf8 = cr = overlong = dropped = 0
    for raw in split_lines(buf):
        text, reason = decode_line(raw)
        if reason == "overlong":
            overlong += 1
            continue
        if reason == "cr":
            cr += 1
            continue
        if reason == "bad_utf8":
            bad_utf8 += 1
            continue
        assert text is not None
        parsed = parse_wrap_tokens(text)
        if parsed is None:
            dropped += 1
            continue
        sets.append(parsed)
    if not sets:
        return FilterResult(
            status=STATUS_NOT_OBSERVED,
            bad_utf8=bad_utf8,
            cr=cr,
            overlong=overlong,
            dropped=dropped,
        )
    unique = {s for s in sets}
    if len(unique) > 1:
        return FilterResult(
            status=STATUS_CONFLICT,
            matches=len(sets),
            bad_utf8=bad_utf8,
            cr=cr,
            overlong=overlong,
            dropped=dropped,
        )
    agreed = next(iter(unique))
    return FilterResult(
        status=STATUS_OBSERVED,
        tokens=agreed,
        reconstructed=reconstruct_line(agreed),
        positive_evidence=bool(agreed),
        matches=len(sets),
        bad_utf8=bad_utf8,
        cr=cr,
        overlong=overlong,
        dropped=dropped,
    )


def wrap_line_for_tokens(tokens: Sequence[str], *, debug_prefix: bool = True) -> str:
    """Test helper: a matching capture line for ``tokens`` (already canonical)."""
    inner = ", ".join(tokens)
    body = MATCH_PREFIX + inner + MATCH_SUFFIX if debug_prefix else reconstruct_line(tokens)
    return body
