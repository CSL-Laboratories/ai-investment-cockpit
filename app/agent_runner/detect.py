"""WP1b — vendor limit/health detection + CLI feature-detection.

Fail-closed posture (SEC-23): the runner treats a limit signal, a non-zero exit, or an
empty/unparseable response as a usage/health fault — not only a known limit-error string.
"""
from __future__ import annotations

import re

_LIMIT_PATTERNS = [
    re.compile(r"usage limit", re.I),
    re.compile(r"rate limit", re.I),
    re.compile(r"\b429\b"),
    re.compile(r"\b503\b"),
    re.compile(r"\b529\b"),
    re.compile(r"quota", re.I),
    re.compile(r"too many requests", re.I),
    re.compile(r"overloaded", re.I),
    re.compile(r"service unavailable", re.I),
    re.compile(r"resource[_ ]?exhausted", re.I),
    re.compile(r"error_max_turns", re.I),
    re.compile(r"\bcapacity\b", re.I),
    re.compile(r"context length", re.I),
    re.compile(r"too long", re.I),
]


def looks_like_limit(text: str) -> bool:
    t = text or ""
    return any(p.search(t) for p in _LIMIT_PATTERNS)


def detect_flags(help_text: str) -> dict:
    """Feature-detect UNVERIFIED CLI flags (``--json-schema`` / ``--effort``). The runner's
    own jsonschema validation stays authoritative regardless of whether a native flag exists."""
    h = help_text or ""
    return {"json_schema": "--json-schema" in h, "effort": "--effort" in h}
