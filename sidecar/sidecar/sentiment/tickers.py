"""Ticker extraction.

A cashtag or a bare uppercase token counts only when it is on the allowlist and
not on the common-word blocklist. The blocklist wins, including when the word is
also a listed company. Bare lowercase words are ignored so "call" is not CALL.
"""

from __future__ import annotations

import re

from sidecar.flow.config import WATCHLIST

ALLOWED = frozenset(WATCHLIST)
BLOCKED = frozenset(
    {
        "A",
        "I",
        "AM",
        "AN",
        "ALL",
        "ARE",
        "ATH",
        "BE",
        "BIG",
        "CAN",
        "CEO",
        "CPI",
        "DD",
        "DO",
        "EDIT",
        "EOD",
        "ETF",
        "EV",
        "FED",
        "FOMO",
        "FOR",
        "GDP",
        "GO",
        "IMO",
        "IPO",
        "IT",
        "LOL",
        "LOW",
        "NOW",
        "ON",
        "OP",
        "OR",
        "OTC",
        "OUT",
        "PM",
        "RIP",
        "RN",
        "RUN",
        "SEC",
        "SEE",
        "SO",
        "TLDR",
        "UP",
        "USA",
        "USD",
        "WSB",
        "YOLO",
        "AI",
        "AH",
    }
)

_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
_BARE = re.compile(r"\b([A-Z]{1,5})\b")


def extract_tickers(text: str, *, allowed: frozenset[str] = ALLOWED) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(symbol: str) -> None:
        if symbol in seen or symbol in BLOCKED or symbol not in allowed:
            return
        seen.add(symbol)
        found.append(symbol)

    for match in _CASHTAG.finditer(text):
        add(match.group(1).upper())
    for match in _BARE.finditer(text):
        add(match.group(1))
    return found
