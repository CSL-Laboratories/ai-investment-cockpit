"""A few behavioral-cloning steps, then a few clipped PPO steps.

Trajectories are Figgie games against the rule bot. The market-making
behavioral-cloning loss is not used: its label weight indexes that simulator's
quote grid. The masked log-probability and ``ppo_grads`` are the shared pieces.
"""

from __future__ import annotations

import numpy as np

from sidecar.figgie_bench.agents import GRUAgent, RuleAgent, fresh_policy, observe
from sidecar.figgie_bench.rules import N_PLAYERS, GameState, apply_action, legal_mask_view, seat_view
from sidecar.rl.policy import GRUPolicy, masked_logp, ppo_grads


def _finite_step(policy: GRUPolicy, grad: np.ndarray) -> bool:
    if not np.isfinite(grad).all():
        return False
    before = policy.theta.copy()
    policy.adam.step(policy.theta, grad)
    if not np.isfinite(policy.theta).all():
        policy.theta = before
        return False
    return True


def bc_update(policy: GRUPolicy, observations: np.ndarray, actions: np.ndarray, masks: np.ndarray) -> float:
    logits, _values, caches = policy.forward_sequence(observations)
    dlogits = []
    total = 0.0
    for logit, action, mask in zip(logits, actions, masks, strict=True):
        logp, dlogp = masked_logp(logit, mask, int(action))
        total += -logp
        dlogits.append(-dlogp)
    scale = 1.0 / max(len(actions), 1)
    grad = policy.backward_sequence(caches, [item * scale for item in dlogits], np.zeros(len(actions)))
    _finite_step(policy, grad)
    return total * scale


def _play_collect(policy: GRUPolicy, rng: np.random.Generator, *, n_turns: int, learn: bool) -> list[dict]:
    state = GameState.deal(rng)
    rule = RuleAgent()
    learner = GRUAgent(policy, rng, greedy=False)
    learner.begin_game()
    trajectories = [{"obs": [], "act": [], "mask": [], "logp": [], "reward": []} for _ in range(N_PLAYERS)]
    for turn in range(n_turns):
        for seat in range(N_PLAYERS):
            view = seat_view(state, seat, turn=turn, n_turns=n_turns)
            observation = observe(view)
            mask = legal_mask_view(view)
            if learn and seat == 0:
                action = learner.act(view)
                logp = learner.last_logp
            else:
                action = rule.act(view)
                logp = 0.0
            trajectories[seat]["obs"].append(observation)
            trajectories[seat]["act"].append(action)
            trajectories[seat]["mask"].append(mask)
            trajectories[seat]["logp"].append(logp)
            before = state.chips[seat]
            apply_action(state, seat, action, turn=turn)
            trajectories[seat]["reward"].append(float(state.chips[seat] - before))
    return trajectories


def train_smoke(
    rng: np.random.Generator,
    *,
    bc_games: int = 2,
    ppo_games: int = 2,
    n_turns: int = 6,
) -> GRUPolicy:
    policy = fresh_policy(rng)
    for _ in range(bc_games):
        trajectories = _play_collect(policy, rng, n_turns=n_turns, learn=False)
        for seat in range(N_PLAYERS):
            row = trajectories[seat]
            bc_update(
                policy,
                np.vstack(row["obs"]),
                np.asarray(row["act"], dtype=np.int64),
                np.vstack(row["mask"]),
            )
    for _ in range(ppo_games):
        trajectories = _play_collect(policy, rng, n_turns=n_turns, learn=True)
        row = trajectories[0]
        rewards = np.asarray(row["reward"], dtype=np.float64) / 100.0
        grad, _loss = ppo_grads(
            policy,
            np.vstack(row["obs"]),
            np.asarray(row["act"], dtype=np.int64),
            np.asarray(row["logp"], dtype=np.float64),
            rewards,
            np.vstack(row["mask"]),
        )
        _finite_step(policy, grad)
    return policy
