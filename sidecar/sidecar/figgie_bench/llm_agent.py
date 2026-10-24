"""LLM seat. Tests use a stub. A real call needs an explicit client and a cost cap.

This module does not open a socket and does not invent a completion. With
``enable_llm`` false, the seat plays the rule bot and the report says the cost
is $0. With ``enable_llm`` true, the caller supplies ``client.complete`` and a
positive ``max_cost_usd``. The next call is refused when it would exceed the cap.
"""

from __future__ import annotations

import json

from sidecar.figgie_bench.agents import RuleAgent, _index_for
from sidecar.figgie_bench.rules import PRICES, SUITS, SeatView, legal_mask_view

ACTION_SCHEMA = {
    "action": ["pass", "buy", "sell"],
    "suit": list(SUITS),
    "price": list(PRICES),
    "belief": list(SUITS),
    "driver": ["plurality_is_twelve", "others_net_bought"],
    "confidence": [0, 1],
}


def prompt_for(view: SeatView) -> str:
    payload = {
        "seat": view.seat,
        "own_counts": {suit: view.hand[suit] for suit in SUITS},
        "own_chips_cents": view.chips,
        "public_flow": {suit: view.public_flow[suit] for suit in SUITS},
        "turn": view.turn,
        "n_turns": view.n_turns,
        "prices": list(PRICES),
    }
    return json.dumps(payload, sort_keys=True)


def _parse(raw: object, view: SeatView) -> tuple[int, dict] | None:
    if not isinstance(raw, dict):
        return None
    belief = raw.get("belief")
    driver = raw.get("driver")
    if belief not in SUITS or driver not in ("plurality_is_twelve", "others_net_bought"):
        return None
    try:
        confidence = float(raw["confidence"])
    except (KeyError, TypeError, ValueError):
        return None
    if confidence != confidence or not 0.0 <= confidence <= 1.0:
        return None
    name = raw.get("action")
    if name == "pass":
        action = 0
    elif name in ("buy", "sell") and raw.get("suit") in SUITS and raw.get("price") in PRICES:
        action = _index_for(str(raw["suit"]), str(name), int(raw["price"]) * 100)
        if float(legal_mask_view(view)[action]) < -1.0e8:
            return None
    else:
        return None
    return action, {
        "driver": str(driver),
        "belief": str(belief),
        "confidence": confidence,
        "model_id": "caller_llm",
    }


class LLMAgent:
    def __init__(
        self,
        *,
        enable_llm: bool = False,
        client: object | None = None,
        max_cost_usd: float | None = None,
        price_per_call: float = 0.01,
    ) -> None:
        if price_per_call <= 0:
            raise ValueError("price_per_call must be positive")
        if enable_llm:
            if client is None or not hasattr(client, "complete"):
                raise ValueError("enable_llm requires a client with complete()")
            if max_cost_usd is None or max_cost_usd <= 0:
                raise ValueError("enable_llm requires a positive max_cost_usd")
        self.enable_llm = enable_llm
        self.client = client
        self.max_cost_usd = 0.0 if max_cost_usd is None else float(max_cost_usd)
        self.price_per_call = float(price_per_call)
        self.spent = 0.0
        self.calls = 0
        self.rejected = 0
        self.stopped = False
        self.last_thesis: dict | None = None
        self.name = "llm" if enable_llm else "llm_stub"
        self._rule = RuleAgent()

    def begin_game(self) -> None:
        self.last_thesis = None
        self._rule.begin_game()

    def act(self, view: SeatView) -> int:
        if not self.enable_llm:
            self.last_thesis = None
            return self._rule.act(view)
        if self.spent + self.price_per_call > self.max_cost_usd:
            self.stopped = True
            return 0
        raw = self.client.complete(prompt_for(view), schema=ACTION_SCHEMA)  # type: ignore[union-attr]
        self.calls += 1
        self.spent += self.price_per_call
        parsed = _parse(raw, view)
        if parsed is None:
            self.rejected += 1
            self.last_thesis = None
            return 0
        action, thesis = parsed
        self.last_thesis = thesis
        return action

    def thesis(self, view: SeatView, belief_suit: str, belief_prob: float) -> dict:
        if self.last_thesis is not None:
            return dict(self.last_thesis)
        stated = self._rule.thesis(view, belief_suit, belief_prob)
        stated["model_id"] = "figgie.llm_stub.v1" if not self.enable_llm else "caller_llm"
        stated["confidence"] = 0.0
        return stated
