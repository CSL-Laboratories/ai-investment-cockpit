"""Baselines: random, an inventory-target rule, and a masked GRU.

The rule bot reuses ``avellaneda_stoikov_quotes`` with inventory measured
against a target, not against zero. The half-spread expression is the equity
expert's. The parameters are not a fit to Figgie.

The GRU is ``sidecar.rl.policy.GRUPolicy``. Weights start from a fresh draw.
The committed market-making checkpoint is not loaded, and CS224R Figgie win
rates are not used.
"""

from __future__ import annotations

import numpy as np

from sidecar.figgie_bench.infer import plurality
from sidecar.figgie_bench.rules import (
    N_ACTIONS,
    PARTNER,
    PRICE_CENTS,
    PRICES,
    SUITS,
    SeatView,
    action_index,
    legal_mask_view,
)
from sidecar.rl.as_expert import avellaneda_stoikov_quotes
from sidecar.rl.policy import GRUPolicy

OBS_SIZE = 22
HIDDEN = 8
GOAL_TARGET = 4


def observe(view: SeatView) -> np.ndarray:
    hand = view.hand
    values = [hand[suit] / 10.0 for suit in SUITS]
    values.append(view.chips / 35000.0)
    for side in ("buy", "sell"):
        for suit in SUITS:
            quote = view.book[suit][side]
            values.append(0.0 if quote is None else quote.price_cents / 1200.0)
    for suit in SUITS:
        last = view.last_trade[suit]
        values.append(0.0 if last is None else last / 1200.0)
    for suit in SUITS:
        values.append(view.public_flow[suit] / 5.0)
    values.append((view.n_turns - view.turn) / max(view.n_turns, 1))
    observation = np.asarray(values, dtype=np.float64)
    if observation.shape != (OBS_SIZE,):
        raise RuntimeError("observation size drifted")
    return observation


def _snap(price_cents: int, *, bid: bool) -> int:
    if bid:
        below = [cents for cents in PRICE_CENTS if cents <= price_cents]
        return below[-1] if below else PRICE_CENTS[0]
    above = [cents for cents in PRICE_CENTS if cents >= price_cents]
    return above[0] if above else PRICE_CENTS[-1]


def _index_for(suit: str, side: str, price_cents: int) -> int:
    return action_index(SUITS.index(suit), 0 if side == "buy" else 1, PRICE_CENTS.index(price_cents))


class RandomAgent:
    name = "random"

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng

    def begin_game(self) -> None:
        return None

    def act(self, view: SeatView) -> int:
        mask = legal_mask_view(view)
        legal = np.flatnonzero(mask > -1.0e8)
        return int(self.rng.choice(legal))

    def thesis(self, view: SeatView, belief_suit: str, belief_prob: float) -> dict:
        del view, belief_prob
        return {
            "driver": "others_net_bought",
            "belief": belief_suit,
            "confidence": 0.0,
            "model_id": "figgie.random.v1",
        }


class RuleAgent:
    """Bid the partner of the unique plurality suit, and sell other suits.

    The plurality suit is treated as the 12-card suit. That is a procedure.
    ``process_supported`` is whether that plurality is unique and the stated
    goal is its partner. The showdown then asks whether that partner was the goal.
    """

    name = "rule"

    def begin_game(self) -> None:
        return None

    def act(self, view: SeatView) -> int:
        hand = view.hand
        picked = plurality(hand)
        if picked is None:
            for suit in SUITS:
                if hand[suit] > 0:
                    return _index_for(suit, "sell", PRICE_CENTS[PRICES.index(8)])
            return 0
        twelve, _margin = picked
        goal = PARTNER[twelve]
        tau = (view.n_turns - view.turn) / max(view.n_turns, 1)
        quotes = avellaneda_stoikov_quotes(
            mid=10.0,
            inventory=hand[goal] - GOAL_TARGET,
            sigma=0.25,
            tau=tau,
            max_inventory=10,
            min_half=0.5,
            max_half=4.0,
            max_skew=6.0,
        )
        if quotes is None:
            return 0
        bid, _ask, post_bid, _post_ask = quotes
        if hand[goal] < GOAL_TARGET and post_bid:
            cents = _snap(int(round(bid * 100)), bid=True)
            return _index_for(goal, "buy", cents)
        for suit in sorted(SUITS, key=lambda name: hand[name], reverse=True):
            if suit != goal and hand[suit] > 0:
                return _index_for(suit, "sell", PRICE_CENTS[PRICES.index(8)])
        return 0

    def thesis(self, view: SeatView, belief_suit: str, belief_prob: float) -> dict:
        del belief_suit, belief_prob
        picked = plurality(view.hand)
        belief = max(SUITS, key=lambda name: (view.hand[name], -SUITS.index(name)))
        if picked is not None:
            belief = PARTNER[picked[0]]
        return {
            "driver": "plurality_is_twelve",
            "belief": belief,
            "confidence": 0.0,
            "model_id": "figgie.rule.v1",
        }


class GRUAgent:
    name = "gru"

    def __init__(self, policy: GRUPolicy, rng: np.random.Generator, *, greedy: bool) -> None:
        self.policy = policy
        self.rng = rng
        self.greedy = greedy
        self.h = np.zeros(policy.hidden)
        self.last_logp = 0.0

    def begin_game(self) -> None:
        self.h = np.zeros(self.policy.hidden)
        self.last_logp = 0.0

    def act(self, view: SeatView) -> int:
        observation = observe(view)
        mask = legal_mask_view(view)
        self.h, _cache = self.policy._step(self.h, observation)
        weights = self.policy.layout.view
        logit = weights(self.policy.theta, "W_o") @ self.h + weights(self.policy.theta, "b_o")
        if self.greedy:
            self.last_logp = 0.0
            return int(self.policy.greedy(logit, mask))
        action, logp = self.policy.sample(logit, mask, self.rng)
        self.last_logp = logp
        return int(action)

    def thesis(self, view: SeatView, belief_suit: str, belief_prob: float) -> dict:
        del view, belief_prob
        return {
            "driver": "others_net_bought",
            "belief": belief_suit,
            "confidence": 0.0,
            "model_id": "figgie.gru_smoke.v1",
        }


def fresh_policy(rng: np.random.Generator) -> GRUPolicy:
    return GRUPolicy(OBS_SIZE, HIDDEN, N_ACTIONS, rng)
