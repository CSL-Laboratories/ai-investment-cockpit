"""Avellaneda-Stoikov and a walk-forward clone of the existing GRU.

The GRU is the sidecar policy, trained by behavioral cloning on the earlier
part of the tape toward the nearest discrete Avellaneda-Stoikov action.
Nothing here sends an order.
"""

from __future__ import annotations

import math

import numpy as np

from sidecar.rl.as_expert import avellaneda_stoikov_quotes
from sidecar.rl.policy import GRUPolicy, bc_loss_and_grad
from sidecar.rl.sim import MAX_INVENTORY, action_mask, decode_action, nearest_action


def features(
    *,
    inventory: int,
    last_ret: float,
    sigma: float,
    step: int,
    n_steps: int,
    mtm: float,
    mid0: float,
) -> np.ndarray:
    capital = MAX_INVENTORY * mid0
    safe_sigma = sigma if sigma > 1e-8 else 1.0
    return np.array(
        [
            inventory / MAX_INVENTORY,
            last_ret / safe_sigma,
            1.0 - (step / n_steps if n_steps else 0.0),
            mtm / capital if capital else 0.0,
            math.tanh(last_ret * 10.0),
        ],
        dtype=np.float64,
    )


def as_quotes(mid: float, inventory: int, sigma: float, tau: float):
    quoted = avellaneda_stoikov_quotes(
        mid=mid, inventory=inventory, sigma=sigma, tau=tau, max_inventory=MAX_INVENTORY
    )
    if quoted is None:
        return None
    bid, ask, post_bid, post_ask = quoted
    return bid, ask, post_bid, post_ask


def train_clone(observations: np.ndarray, actions: np.ndarray, masks: np.ndarray, inventories: np.ndarray, *, seed: int, steps: int = 40):
    rng = np.random.default_rng(seed)
    policy = GRUPolicy(5, 8, 9, rng)
    if len(actions) < 8:
        return policy, None, None
    first = None
    last = None
    window = min(32, len(actions))
    for step in range(steps):
        start = int(rng.integers(0, len(actions) - window + 1))
        stop = start + window
        loss, grad = bc_loss_and_grad(
            policy,
            observations[start:stop],
            actions[start:stop],
            masks[start:stop],
            inventories[start:stop],
        )
        if first is None:
            first = float(loss)
        policy.adam.step(policy.theta, grad)
        last = float(loss)
    return policy, first, last


def greedy_quotes(policy: GRUPolicy, hidden: np.ndarray, observation: np.ndarray, inventory: int, mid: float):
    hidden, _cache = policy._step(hidden, observation)
    logit = policy.layout.view(policy.theta, "W_o") @ hidden + policy.layout.view(policy.theta, "b_o")
    mask = action_mask(inventory)
    action = policy.greedy(logit, mask)
    bid, ask, post_bid, post_ask, _half, _skew = decode_action(action, mid, inventory)
    return hidden, bid, ask, post_bid, post_ask


def expert_action(mid: float, inventory: int, sigma: float, tau: float) -> int | None:
    quoted = as_quotes(mid, inventory, sigma, tau)
    if quoted is None:
        return None
    bid, ask, _post_bid, _post_ask = quoted
    return nearest_action(mid, bid, ask, inventory)
