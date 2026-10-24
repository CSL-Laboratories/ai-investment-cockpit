"""Goal-suit posterior from a player's own hand and other players' trades.

The prior is uniform on the 12 public decks. A deck that cannot contain the
player's hand is dropped. Another player's aggressive buy of a suit multiplies
decks whose goal is that suit by 1.5. An aggressive sell multiplies them by
0.75. These lifts are declared here and are not fit to a result.

Log loss is ``-log p(true goal)`` after each trade the player did not initiate.
"""

from __future__ import annotations

import math

from sidecar.figgie_bench.rules import LAYOUTS, SUITS, goal_of

BUY_LIFT = 1.5
SELL_LIFT = 0.75
FLOOR = 1.0e-12


class Belief:
    def __init__(self, hand: dict[str, int]) -> None:
        self.weights: list[list] = []
        for counts in LAYOUTS:
            if all(hand[suit] <= counts[suit] for suit in SUITS):
                self.weights.append([dict(counts), 1.0])
        if not self.weights:
            raise RuntimeError("hand matches no Figgie deck")
        self._normalize()

    def _normalize(self) -> None:
        total = sum(weight for _counts, weight in self.weights)
        if total <= 0.0 or not math.isfinite(total):
            share = 1.0 / len(self.weights)
            for row in self.weights:
                row[1] = share
            return
        for row in self.weights:
            row[1] = row[1] / total

    def update_trade(self, *, suit: str, aggressive_side: str) -> None:
        lift = BUY_LIFT if aggressive_side == "buy" else SELL_LIFT
        for row in self.weights:
            if goal_of(row[0]) == suit:
                row[1] *= lift
        self._normalize()

    def goal_probabilities(self) -> dict[str, float]:
        totals = {suit: 0.0 for suit in SUITS}
        for counts, weight in self.weights:
            totals[goal_of(counts)] += weight
        return totals

    def mode(self) -> tuple[str, float]:
        probs = self.goal_probabilities()
        suit = max(SUITS, key=lambda name: (probs[name], -SUITS.index(name)))
        return suit, float(probs[suit])

    def log_loss(self, true_goal: str) -> float:
        probability = max(FLOOR, self.goal_probabilities()[true_goal])
        return -math.log(probability)


def plurality(hand: dict[str, int]) -> tuple[str, int] | None:
    """Unique most-held suit and its margin, or None when the top two tie."""
    ranked = sorted(SUITS, key=lambda suit: (hand[suit], -SUITS.index(suit)), reverse=True)
    if hand[ranked[0]] == 0 or hand[ranked[0]] == hand[ranked[1]]:
        return None
    return ranked[0], hand[ranked[0]] - hand[ranked[1]]
