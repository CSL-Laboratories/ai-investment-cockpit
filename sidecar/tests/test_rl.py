from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from sidecar.rl.as_expert import avellaneda_stoikov_quotes
from sidecar.rl.policy import GRUPolicy, OpponentPool, bc_loss_and_grad
from sidecar.rl.sim import action_mask, shape_terms, unwind_weight
from sidecar.rl.train import run_profile


def test_as_formula_matches_a_hand_calculation_and_refuses_a_bad_sigma():
    gamma = 4.0
    kappa = 80.0
    sigma = 0.1
    variance = gamma * sigma * sigma
    half = 0.5 * (variance + (2.0 / gamma) * math.log1p(gamma / kappa))
    bid, ask, post_bid, post_ask = avellaneda_stoikov_quotes(
        mid=100.0, inventory=0, sigma=sigma, tau=1.0
    )
    assert post_bid and post_ask
    assert bid == math.floor((100.0 - half) * 100.0) / 100.0
    assert ask == math.ceil((100.0 + half) * 100.0) / 100.0
    assert ask > bid
    _bid, _ask, may_bid, may_ask = avellaneda_stoikov_quotes(
        mid=100.0, inventory=10, sigma=sigma, tau=1.0, max_inventory=10
    )
    assert may_bid is False and may_ask is True
    assert avellaneda_stoikov_quotes(mid=100.0, inventory=0, sigma=float("nan"), tau=1.0) is None


def test_mask_and_unwind_weight():
    mask = action_mask(10)
    assert mask[0] == 0.0
    assert all(mask[index] < -1.0e8 for index in range(9) if index % 3 != 0)
    assert unwind_weight(5, 0) == 15.0
    assert unwind_weight(0, 0) == 1.0


def test_potential_shaping_uses_a_zero_terminal_potential():
    terms = shape_terms([2.0, 5.0, 7.0], beta=1.0, discount=1.0)
    assert terms[-1] == -5.0
    assert abs(sum(terms) - (-2.0)) < 1.0e-9


def test_pfsp_samples_the_weaker_snapshot_more_often():
    pool = OpponentPool()
    pool.snapshots = [[np.zeros(1), 0.95], [np.zeros(1), 0.05]]
    rng = np.random.default_rng(0)
    counts = [0, 0]
    for _ in range(500):
        counts[pool.sample(rng, psp=0.0)] += 1
    assert counts[1] > counts[0] * 3


def test_gru_gradient_matches_a_finite_difference():
    rng = np.random.default_rng(1)
    policy = GRUPolicy(5, 3, 4, rng)
    observations = rng.normal(size=(4, 5))

    def loss(theta: np.ndarray) -> float:
        saved = policy.theta
        policy.theta = theta
        logits, values, _caches = policy.forward_sequence(observations)
        policy.theta = saved
        return float(sum(item.sum() for item in logits) + values.sum())

    base = policy.theta.copy()
    logits, values, caches = policy.forward_sequence(observations)
    analytic = policy.backward_sequence(caches, [np.ones_like(item) for item in logits], np.ones_like(values))
    index = 17
    step = 1.0e-5
    plus = base.copy()
    minus = base.copy()
    plus[index] += step
    minus[index] -= step
    numerical = (loss(plus) - loss(minus)) / (2.0 * step)
    assert abs(numerical - analytic[index]) < 1.0e-4


def test_behavioral_cloning_loss_falls_on_a_fixed_batch():
    rng = np.random.default_rng(2)
    policy = GRUPolicy(5, 8, 9, rng)
    observations = rng.normal(size=(12, 5))
    actions = np.array([index % 9 for index in range(12)])
    masks = np.zeros((12, 9))
    inventories = np.zeros(12, dtype=np.int64)
    inventories[-1] = 5
    actions[-1] = 0
    before, _grad = bc_loss_and_grad(policy, observations, actions, masks, inventories)
    for _ in range(25):
        _loss, grad = bc_loss_and_grad(policy, observations, actions, masks, inventories)
        norm = float(np.linalg.norm(grad))
        if norm > 5.0:
            grad = grad * (5.0 / norm)
        policy.adam.step(policy.theta, grad)
    after, _grad = bc_loss_and_grad(policy, observations, actions, masks, inventories)
    assert after < before


def test_smoke_profile_is_simulation_only(tmp_path: Path):
    report = run_profile(
        "smoke",
        out=tmp_path / "rl.json",
        ledger_path=tmp_path / "rl.ledger.jsonl",
    )
    assert report["live_trading"] is False
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["historical_replay"]["label"] == "historical_daily_replay_not_l1"
    assert report["learner"]["evalstats"]["threshold_status"] == "not_frozen"
    assert report["learner"]["evalstats"]["n_observations"] >= 8
    assert "mean_session_return" in report["learner"]
    assert "mean_session_return" in report["as_baseline"]
    assert 0.0 <= report["learner"]["acceptance_rate"] <= 1.0
    assert report["learner"]["evalstats"]["pbo"]["status"] == "ok"
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in (Path(__file__).resolve().parents[1] / "sidecar" / "rl").glob("*.py")
    )
    assert "paper-api.alpaca.markets" not in text
    assert "place_order" not in text
    assert "OrderGateway" not in text
