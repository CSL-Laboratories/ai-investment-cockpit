"""One-asset market-making simulator.

Training matches the learner against another maker. The tighter quote wins the
taker, and a tie goes to the opponent, so the learner has to quote strictly
inside. Evaluation runs each policy alone against the same taker draws.

Takers are noise, informed flow that sees the upcoming shock, and an
order-flow-imbalance momentum taker. Nothing in this module talks to a broker.
"""

from __future__ import annotations

import math

import numpy as np

from sidecar.rl.as_expert import avellaneda_stoikov_quotes

HALF_SPREADS = (0.02, 0.05, 0.12)
SKEWS = (-0.08, 0.0, 0.08)
N_ACTIONS = 9
MAX_INVENTORY = 10
BETA_SHAPE = 0.5
DISCOUNT = 0.99


def action_mask(inventory: int, max_inventory: int = MAX_INVENTORY) -> np.ndarray:
    """Invalid actions get a large negative logit. At least one action stays legal."""
    mask = np.zeros(N_ACTIONS, dtype=np.float64)
    for index in range(N_ACTIONS):
        skew = SKEWS[index % 3]
        if inventory >= max_inventory and skew >= 0:
            mask[index] = -1.0e9
        elif inventory <= -max_inventory and skew <= 0:
            mask[index] = -1.0e9
    if np.all(mask < -1.0e8):
        mask[:] = -1.0e9
        mask[0 if inventory > 0 else 2] = 0.0
    return mask


def decode_action(index: int, mid: float, inventory: int, max_inventory: int = MAX_INVENTORY):
    half = HALF_SPREADS[index // 3]
    skew = SKEWS[index % 3]
    reservation = mid + skew
    bid = reservation - half
    ask = reservation + half
    post_bid = inventory < max_inventory
    post_ask = inventory > -max_inventory
    return bid, ask, post_bid, post_ask, half, skew


def nearest_action(mid: float, bid: float, ask: float, inventory: int) -> int:
    half = max((ask - bid) / 2.0, 1.0e-6)
    skew = ((bid + ask) / 2.0) - mid
    mask = action_mask(inventory)
    best = 0
    best_distance = 1.0e18
    for index in range(N_ACTIONS):
        if mask[index] < -1.0e8:
            continue
        distance = (HALF_SPREADS[index // 3] - half) ** 2 + (SKEWS[index % 3] - skew) ** 2
        if distance < best_distance:
            best = index
            best_distance = distance
    return best


def unwind_weight(inventory: int, action: int) -> float:
    """Upweight the rare inventory-reducing labels, analogous to the Figgie lift weight."""
    skew = SKEWS[action % 3]
    if abs(inventory) >= 3 and inventory * skew < 0:
        return 15.0
    return 1.0


class MakerState:
    def __init__(self) -> None:
        self.inventory = 0
        self.cash = 0.0

    def mtm(self, mid: float) -> float:
        return self.cash + self.inventory * mid


class Session:
    def __init__(
        self,
        rng: np.random.Generator,
        *,
        episode_len: int = 32,
        mid0: float = 100.0,
        sigma: float = 0.08,
        shocks: np.ndarray | None = None,
        match: bool = False,
    ) -> None:
        self.rng = rng
        self.episode_len = episode_len
        self.mid = mid0
        self.mid0 = mid0
        self.sigma = sigma
        self.shocks = shocks
        self.match = match
        self.t = 0
        self.agent = MakerState()
        self.opponent = MakerState()
        self.last_ret = 0.0
        self.posted = 0
        self.filled = 0
        self.adverse: list[float] = []
        self.agent_pnl = 0.0
        self.opponent_pnl = 0.0

    def features(self, maker: MakerState | None = None) -> np.ndarray:
        maker = self.agent if maker is None else maker
        capital = MAX_INVENTORY * self.mid0
        return np.array(
            [
                maker.inventory / MAX_INVENTORY,
                self.last_ret / self.sigma,
                1.0 - self.t / self.episode_len,
                maker.mtm(self.mid) / capital,
                math.tanh(self.last_ret * 10.0),
            ],
            dtype=np.float64,
        )

    def _apply_fill(self, maker: MakerState, side: str, price: float) -> bool:
        if side == "buy":
            if maker.inventory + 1 > MAX_INVENTORY:
                return False
            maker.inventory += 1
            maker.cash -= price
            return True
        if maker.inventory - 1 < -MAX_INVENTORY:
            return False
        maker.inventory -= 1
        maker.cash += price
        return True

    def _take(
        self,
        side: str,
        agent_px: float,
        opp_px: float,
        agent_on: bool,
        opp_on: bool,
        uniform: float,
    ) -> str | None:
        """`side` buy means the maker buys. Returns 'agent' when the learner fills."""
        if self.match:
            if not agent_on and not opp_on:
                return None
            if agent_on and opp_on:
                if side == "buy":
                    choice = "agent" if agent_px > opp_px else "opponent"
                else:
                    choice = "agent" if agent_px < opp_px else "opponent"
            else:
                choice = "agent" if agent_on else "opponent"
            maker = self.agent if choice == "agent" else self.opponent
            price = agent_px if choice == "agent" else opp_px
            if not self._apply_fill(maker, side, price):
                return None
            if choice == "agent":
                self._pending_adverse.append((side, price))
            return choice
        if not agent_on:
            return None
        distance = (self.mid - agent_px) if side == "buy" else (agent_px - self.mid)
        probability = math.exp(-80.0 * max(distance, 0.0) / self.mid)
        if uniform < probability and self._apply_fill(self.agent, side, agent_px):
            self._pending_adverse.append((side, agent_px))
            return "agent"
        return None

    def step_quotes(
        self,
        agent_bid: float,
        agent_ask: float,
        agent_bid_on: bool,
        agent_ask_on: bool,
        opp_bid: float | None = None,
        opp_ask: float | None = None,
        opp_bid_on: bool = False,
        opp_ask_on: bool = False,
    ) -> tuple[float, bool, dict]:
        phi = self.agent.mtm(self.mid)
        if self.shocks is not None:
            shock = float(self.shocks[self.t])
        else:
            shock = float(self.rng.normal(0.0, self.sigma))
        # Consume the taker uniforms in a fixed order so two policies on the same
        # seed and the same shocks see the same arrivals.
        gates = [float(self.rng.random()) for _ in range(7)]
        self._pending_adverse = []
        posted_before = self.posted
        if agent_bid_on:
            self.posted += 1
        if agent_ask_on:
            self.posted += 1
        agent_sides: set[str] = set()

        def consider(side: str, uniform: float) -> None:
            price_bid = agent_bid
            price_ask = agent_ask
            agent_px = price_bid if side == "buy" else price_ask
            opp_px = (opp_bid or 0.0) if side == "buy" else (opp_ask or 0.0)
            agent_on = agent_bid_on if side == "buy" else agent_ask_on
            opp_on = opp_bid_on if side == "buy" else opp_ask_on
            choice = self._take(side, agent_px, opp_px, agent_on, opp_on, uniform)
            if choice == "agent":
                agent_sides.add(side)

        if gates[0] < 0.55:
            consider("buy" if gates[1] < 0.5 else "sell", gates[2])
        if gates[3] < 0.20:
            if shock > 0:
                consider("sell", gates[4])
            elif shock < 0:
                consider("buy", gates[4])
        if gates[5] < 0.25:
            if self.last_ret > 0:
                consider("sell", gates[6])
            elif self.last_ret < 0:
                consider("buy", gates[6])
        self.filled += len(agent_sides)
        previous = self.mid
        self.mid = max(1.0, self.mid + shock)
        self.last_ret = self.mid - previous
        for side, price in self._pending_adverse:
            sign = 1.0 if side == "buy" else -1.0
            self.adverse.append(sign * (self.mid - price))
        self.t += 1
        done = self.t >= self.episode_len
        phi_next = 0.0 if done else self.agent.mtm(self.mid)
        raw = self.agent.mtm(self.mid) if done else 0.0
        shaped = raw + BETA_SHAPE * (DISCOUNT * phi_next - phi)
        if done:
            self.agent_pnl = self.agent.mtm(self.mid)
            self.opponent_pnl = self.opponent.mtm(self.mid)
        info = {
            "posted_step": self.posted - posted_before,
            "pnl": self.agent.mtm(self.mid),
            "opponent_pnl": self.opponent.mtm(self.mid),
        }
        return shaped, done, info


def opponent_as_quotes(session: Session) -> tuple[float, float, bool, bool]:
    tau = max(0.0, 1.0 - session.t / session.episode_len)
    quotes = avellaneda_stoikov_quotes(
        mid=session.mid,
        inventory=session.opponent.inventory,
        sigma=session.sigma,
        tau=tau,
    )
    if quotes is None:
        return session.mid - 0.12, session.mid + 0.12, True, True
    return quotes


def shape_terms(wealth: list[float], *, beta: float = BETA_SHAPE, discount: float = DISCOUNT) -> list[float]:
    """Potential-based terms with phi(terminal) = 0. `wealth` includes the start value."""
    terms = []
    for index in range(len(wealth) - 1):
        phi = wealth[index]
        phi_next = 0.0 if index + 1 == len(wealth) - 1 else wealth[index + 1]
        # The last wealth entry is the terminal state, whose potential is defined as 0
        # even though the mark is wealth[-1]. Pass wealth without substituting; the
        # caller uses this helper for the telescope check.
        if index + 1 == len(wealth) - 1:
            phi_next = 0.0
        terms.append(beta * (discount * phi_next - phi))
    return terms
