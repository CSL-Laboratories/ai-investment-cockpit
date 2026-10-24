"""Small hand-written finance word list.

This is not Loughran-McDonald, VADER, or a trained model. A word is counted once
per post. "calls" is treated as bullish and "puts" as bearish, which is wrong for
"sold calls" and for sarcasm. The list is fixed; it is not fit to returns.

Positive: bullish, buy, buying, call, calls, moon, squeeze, breakout, undervalued,
upgrade, beat, beats, surge, soar.
Negative: bearish, sell, selling, put, puts, crash, plunge, fraud, bankruptcy,
downgrade, miss, misses, bagholder, dilution, tank.
"""

from __future__ import annotations

import re

POSITIVE = frozenset(
    {
        "bullish",
        "buy",
        "buying",
        "call",
        "calls",
        "moon",
        "squeeze",
        "breakout",
        "undervalued",
        "upgrade",
        "beat",
        "beats",
        "surge",
        "soar",
    }
)
NEGATIVE = frozenset(
    {
        "bearish",
        "sell",
        "selling",
        "put",
        "puts",
        "crash",
        "plunge",
        "fraud",
        "bankruptcy",
        "downgrade",
        "miss",
        "misses",
        "bagholder",
        "dilution",
        "tank",
    }
)

_WORD = re.compile(r"[a-z]+")


def lexicon_score(text: str) -> float:
    counts = {token for token in _WORD.findall(text.lower())}
    positive = len(counts & POSITIVE)
    negative = len(counts & NEGATIVE)
    total = positive + negative
    if total == 0:
        return 0.0
    return (positive - negative) / total
