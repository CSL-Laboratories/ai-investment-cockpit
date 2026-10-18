"""Train and evaluate the simulated market maker.

``--profile smoke`` is the CPU check. ``--profile report`` is the longer run whose
numbers can be quoted. Neither profile places an order or reads a broker credential.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.report import evaluate
from sidecar.harness.backtest import DEFAULT_BARS, load_bars
from sidecar.jsonutil import sha256_canonical, write_json
from sidecar.rl.as_expert import avellaneda_stoikov_quotes
from sidecar.rl.policy import GRUPolicy, OpponentPool, bc_loss_and_grad, ppo_grads
from sidecar.rl.sim import (
    BETA_SHAPE,
    DISCOUNT,
    MAX_INVENTORY,
    Session,
    action_mask,
    decode_action,
    nearest_action,
)

ROOT = Path(__file__).resolve().parents[2]
INPUT_SIZE = 5
PROFILES = {
    "smoke": {
        "hidden": 8,
        "episode_len": 12,
        "bc_steps": 8,
        "updates": 1,
        "episodes_per_update": 2,
        "ppo_epochs": 1,
        "eval_sessions": 8,
        "seeds": (0, 1),
        "learning_rates": (1.0e-2,),
        "historical_sessions": 4,
        "historical_len": 8,
        "bootstrap_reps": 60,
        "pool_limit": 2,
    },
    "report": {
        "hidden": 16,
        "episode_len": 32,
        "bc_steps": 40,
        "updates": 6,
        "episodes_per_update": 4,
        "ppo_epochs": 2,
        "eval_sessions": 12,
        "seeds": (0, 1),
        "learning_rates": (1.0e-2, 3.0e-3),
        "historical_sessions": 8,
        "historical_len": 12,
        "bootstrap_reps": 80,
        "pool_limit": 4,
    },
}


def _clip(grad: np.ndarray, limit: float = 5.0) -> np.ndarray:
    norm = float(np.linalg.norm(grad))
    if norm > limit:
        return grad * (limit / norm)
    return grad


def _as_agent_quotes(session: Session):
    tau = max(0.0, 1.0 - session.t / session.episode_len)
    quotes = avellaneda_stoikov_quotes(
        mid=session.mid,
        inventory=session.agent.inventory,
        sigma=session.sigma,
        tau=tau,
    )
    if quotes is None:
        return session.mid - 0.12, session.mid + 0.12, True, True
    return quotes


def _play_solo(policy: GRUPolicy | None, theta: np.ndarray | None, seed: int, episode_len: int, shocks):
    session = Session(
        np.random.default_rng(seed),
        episode_len=episode_len,
        shocks=None if shocks is None else np.asarray(shocks, dtype=np.float64),
        match=False,
    )
    hidden = np.zeros(policy.hidden) if policy is not None else None
    while session.t < episode_len:
        if policy is None:
            bid, ask, post_bid, post_ask = _as_agent_quotes(session)
        else:
            hidden, _cache = policy._step(hidden, session.features(), theta)
            logits = policy.layout.view(theta, "W_o") @ hidden + policy.layout.view(theta, "b_o")
            mask = action_mask(session.agent.inventory)
            action = policy.greedy(logits, mask)
            bid, ask, post_bid, post_ask, _half, _skew = decode_action(
                action, session.mid, session.agent.inventory
            )
        session.step_quotes(
            bid if post_bid else session.mid,
            ask if post_ask else session.mid,
            post_bid,
            post_ask,
        )
    capital = MAX_INVENTORY * session.mid0
    acceptance = (session.filled / session.posted) if session.posted else 0.0
    adverse = float(np.mean(session.adverse)) if session.adverse else None
    return {
        "return": session.agent_pnl / capital,
        "pnl": session.agent_pnl,
        "acceptance_rate": acceptance,
        "adverse_selection": adverse,
        "n_adverse": len(session.adverse),
        "posted": session.posted,
        "filled": session.filled,
    }


def _bc_episode(policy: GRUPolicy, episode_len: int, rng: np.random.Generator):
    session = Session(rng, episode_len=episode_len, match=False)
    observations = []
    actions = []
    masks = []
    inventories = []
    while session.t < episode_len:
        observations.append(session.features())
        inventories.append(session.agent.inventory)
        masks.append(action_mask(session.agent.inventory))
        bid, ask, post_bid, post_ask = _as_agent_quotes(session)
        actions.append(nearest_action(session.mid, bid, ask, session.agent.inventory))
        session.step_quotes(
            bid if post_bid else session.mid,
            ask if post_ask else session.mid,
            post_bid,
            post_ask,
        )
    return (
        np.asarray(observations, dtype=np.float64),
        np.asarray(actions, dtype=np.int64),
        np.asarray(masks, dtype=np.float64),
        np.asarray(inventories, dtype=np.int64),
    )


def _warm_start(policy: GRUPolicy, steps: int, episode_len: int, rng: np.random.Generator) -> list[float]:
    losses = []
    for _ in range(steps):
        observations, actions, masks, inventories = _bc_episode(policy, episode_len, rng)
        loss, grad = bc_loss_and_grad(policy, observations, actions, masks, inventories)
        policy.adam.step(policy.theta, _clip(grad))
        losses.append(loss)
    return losses


def _match_episode(policy: GRUPolicy, opponent_theta: np.ndarray | None, episode_len: int, rng: np.random.Generator):
    session = Session(np.random.default_rng(int(rng.integers(0, 2**31 - 1))), episode_len=episode_len, match=True)
    hidden = np.zeros(policy.hidden)
    opponent_hidden = np.zeros(policy.hidden)
    observations = []
    actions = []
    old_logp = []
    rewards = []
    masks = []
    while session.t < episode_len:
        observation = session.features()
        mask = action_mask(session.agent.inventory)
        hidden, _cache = policy._step(hidden, observation, policy.theta)
        logits = policy.layout.view(policy.theta, "W_o") @ hidden + policy.layout.view(policy.theta, "b_o")
        action, logp = policy.sample(logits, mask, rng)
        bid, ask, post_bid, post_ask, _half, _skew = decode_action(action, session.mid, session.agent.inventory)
        if opponent_theta is None:
            tau = max(0.0, 1.0 - session.t / session.episode_len)
            quotes = avellaneda_stoikov_quotes(
                mid=session.mid,
                inventory=session.opponent.inventory,
                sigma=session.sigma,
                tau=tau,
            )
            if quotes is None:
                opp_bid, opp_ask, opp_bid_on, opp_ask_on = session.mid - 0.12, session.mid + 0.12, True, True
            else:
                opp_bid, opp_ask, opp_bid_on, opp_ask_on = quotes
        else:
            opponent_hidden, _cache = policy._step(
                opponent_hidden, session.features(session.opponent), opponent_theta
            )
            opp_logits = (
                policy.layout.view(opponent_theta, "W_o") @ opponent_hidden
                + policy.layout.view(opponent_theta, "b_o")
            )
            opp_action = policy.greedy(opp_logits, action_mask(session.opponent.inventory))
            opp_bid, opp_ask, opp_bid_on, opp_ask_on, _half, _skew = decode_action(
                opp_action, session.mid, session.opponent.inventory
            )
        reward, _done, _info = session.step_quotes(
            bid if post_bid else session.mid,
            ask if post_ask else session.mid,
            post_bid,
            post_ask,
            opp_bid if opp_bid_on else None,
            opp_ask if opp_ask_on else None,
            opp_bid_on,
            opp_ask_on,
        )
        observations.append(observation)
        actions.append(action)
        old_logp.append(logp)
        rewards.append(reward)
        masks.append(mask)
    win = session.agent_pnl > session.opponent_pnl
    return {
        "observations": np.asarray(observations, dtype=np.float64),
        "actions": np.asarray(actions, dtype=np.int64),
        "old_logp": np.asarray(old_logp, dtype=np.float64),
        "rewards": np.asarray(rewards, dtype=np.float64),
        "masks": np.asarray(masks, dtype=np.float64),
        "win": win,
    }


def _train_one(profile: dict, seed: int, learning_rate: float) -> tuple[GRUPolicy, list[float]]:
    rng = np.random.default_rng(seed)
    policy = GRUPolicy(INPUT_SIZE, profile["hidden"], 9, rng)
    policy.adam.lr = learning_rate
    losses = _warm_start(policy, profile["bc_steps"], profile["episode_len"], rng)
    pool = OpponentPool(limit=profile["pool_limit"])
    pool.add(policy.copy_theta())
    for _update in range(profile["updates"]):
        episodes = []
        chosen = []
        for _episode in range(profile["episodes_per_update"]):
            index = pool.sample(rng, psp=0.3)
            theta = None if index is None else pool.snapshots[index][0]
            episode = _match_episode(policy, theta, profile["episode_len"], rng)
            episodes.append(episode)
            chosen.append(index)
        for _epoch in range(profile["ppo_epochs"]):
            grad = np.zeros_like(policy.theta)
            for episode in episodes:
                step_grad, _loss = ppo_grads(
                    policy,
                    episode["observations"],
                    episode["actions"],
                    episode["old_logp"],
                    episode["rewards"],
                    episode["masks"],
                )
                grad += step_grad
            policy.adam.step(policy.theta, _clip(grad / max(len(episodes), 1)))
        for index, episode in zip(chosen, episodes, strict=True):
            pool.update(index, episode["win"])
        pool.add(policy.copy_theta())
    return policy, losses


def _historical_shock(session_index: int, episode_len: int) -> np.ndarray:
    rows = load_bars(DEFAULT_BARS)["SPY"]
    closes = [float(bar.close_adj) for bar in rows]
    returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]
    start = (session_index * episode_len) % max(1, len(returns) - episode_len)
    window = returns[start : start + episode_len]
    if len(window) < episode_len:
        raise RuntimeError("fixture does not have enough SPY sessions for the replay")
    return np.asarray([100.0 * value for value in window], dtype=np.float64)


def _summarize(rows: list[dict]) -> dict:
    acceptance = [row["acceptance_rate"] for row in rows]
    adverse = [row["adverse_selection"] for row in rows if row["adverse_selection"] is not None]
    return {
        "mean_session_return": float(np.mean([row["return"] for row in rows])),
        "mean_pnl": float(np.mean([row["pnl"] for row in rows])),
        "acceptance_rate": float(np.mean(acceptance)),
        "adverse_selection_mean": None if not adverse else float(np.mean(adverse)),
        "n_sessions": len(rows),
    }


def run_profile(name: str, *, out: Path, ledger_path: Path) -> dict:
    if name not in PROFILES:
        raise SystemExit(f"unknown profile {name}")
    profile = PROFILES[name]
    ledger = TrialLedger(ledger_path)
    trained = []
    for seed in profile["seeds"]:
        for learning_rate in profile["learning_rates"]:
            config = {
                "strategy_id": "sidecar.rl_market_maker.sim_only.v1",
                "profile": name,
                "seed": seed,
                "learning_rate": learning_rate,
                "hidden": profile["hidden"],
                "episode_len": profile["episode_len"],
                "bc_steps": profile["bc_steps"],
                "updates": profile["updates"],
                "beta_shape": BETA_SHAPE,
                "discount": DISCOUNT,
                "live_eligible": False,
            }
            ledger.append(
                kind="parameter_set",
                name=f"rl-mm:seed={seed}:lr={learning_rate}",
                config=config,
            )
            policy, bc_losses = _train_one(profile, seed, learning_rate)
            session_rows = [
                _play_solo(policy, policy.theta, 10_000 + index, profile["episode_len"], None)
                for index in range(profile["eval_sessions"])
            ]
            trained.append(
                {
                    "config": config,
                    "config_hash": sha256_canonical(config),
                    "bc_loss_first": bc_losses[0],
                    "bc_loss_last": bc_losses[-1],
                    "sessions": session_rows,
                    "policy": policy,
                }
            )
    ledger.append(
        kind="strategy_config",
        name="sidecar.avellaneda_stoikov.sim_only.v1",
        config={
            "strategy_id": "sidecar.avellaneda_stoikov.sim_only.v1",
            "role": "baseline",
            "live_eligible": False,
            "profile": name,
        },
    )
    as_rows = [
        _play_solo(None, None, 10_000 + index, profile["episode_len"], None)
        for index in range(profile["eval_sessions"])
    ]
    primary = trained[0]
    learner_returns = [row["return"] for row in primary["sessions"]]
    as_returns = [row["return"] for row in as_rows]
    matrix = [[row["return"] for row in item["sessions"]] for item in trained]
    n_trials = ledger.distinct_config_count()
    stats = evaluate(
        learner_returns,
        n_trials,
        as_returns,
        periods_per_year=252,
        trial_matrix=matrix if len(matrix) >= 2 else None,
        n_slices=4,
        bootstrap_reps=profile["bootstrap_reps"],
        seed=0,
    )
    as_stats = evaluate(
        as_returns,
        n_trials,
        periods_per_year=252,
        bootstrap_reps=profile["bootstrap_reps"],
        seed=1,
    )
    policy = primary["policy"]
    historical_learner = []
    historical_as = []
    for index in range(profile["historical_sessions"]):
        shocks = _historical_shock(index, profile["historical_len"])
        historical_learner.append(
            _play_solo(policy, policy.theta, 20_000 + index, profile["historical_len"], shocks)
        )
        historical_as.append(_play_solo(None, None, 20_000 + index, profile["historical_len"], shocks))
    learner_summary = _summarize(primary["sessions"])
    as_summary = _summarize(as_rows)
    report = {
        "schema_version": "sidecar.rl_mm_eval.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": name,
        "live_trading": False,
        "orders_submitted": 0,
        "broker_writes": False,
        "beta_shape": BETA_SHAPE,
        "discount": DISCOUNT,
        "learner": {
            **learner_summary,
            "config_hash": primary["config_hash"],
            "bc_loss_first": primary["bc_loss_first"],
            "bc_loss_last": primary["bc_loss_last"],
            "evalstats": stats,
        },
        "as_baseline": {**as_summary, "evalstats": as_stats},
        "comparison": {
            "learner_minus_as_mean_session_return": learner_summary["mean_session_return"]
            - as_summary["mean_session_return"],
            "learner_minus_as_acceptance_rate": learner_summary["acceptance_rate"]
            - as_summary["acceptance_rate"],
        },
        "hyperparameters": [
            {
                "config_hash": item["config_hash"],
                "seed": item["config"]["seed"],
                "learning_rate": item["config"]["learning_rate"],
                "mean_session_return": float(np.mean([row["return"] for row in item["sessions"]])),
            }
            for item in trained
        ],
        "historical_replay": {
            "label": "historical_daily_replay_not_l1",
            "bars": "fixtures/etf_daily.csv",
            "learner": _summarize(historical_learner),
            "as_baseline": _summarize(historical_as),
        },
        "trial_ledger": {
            "path": str(ledger_path),
            "distinct_configs": n_trials,
            "entries": len(ledger.entries()),
            "head_hash": ledger.head_hash(),
        },
        "caveats": [
            "Simulation only. This module never submits an order and has no broker credentials.",
            "The expert is the Avellaneda-Stoikov formula reimplemented in sidecar.rl.as_expert, not an import from the cockpit tree.",
            "Gamma and kappa are simulator units on a $100 mid. They are the same expression as the cockpit quoter with different numeric clamps.",
            "Held-out sessions use seeds 10000 and up. Training seeds are the hyperparameter seeds only.",
            "Each observation in evalstats is one simulated session, not one market day. Annualized Sharpe is the sqrt(252) display scaling of that per-session Sharpe.",
            "PBO, when present, ranks hyperparameter seeds and learning rates on those sessions. It is not a purged CPCV and it is not a qualify gate.",
            "historical_daily_replay_not_l1 replays SPY daily simple returns as the mid path. The repository has no L1 tape. A daily move is the wrong horizon for a market-making quote.",
            "Quote acceptance is filled sides over posted sides. Adverse selection is the mean of sign(side) * (next_mid - fill_price); negative means the mid moved against the fill.",
            "Figgie win rates from the CS224R report are not reused here.",
            "A short CPU run can leave two hyperparameter rows with the same held-out session returns. The table is what was measured.",
            "threshold_status stays not_frozen.",
        ],
    }
    if report["live_trading"] is not False or report["orders_submitted"] != 0 or report["broker_writes"] is not False:
        raise RuntimeError("refusing to write a market-making report that is not simulation-only")
    write_json(out, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate a GRU market maker. Never places an order.")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="smoke")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--ledger", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out
    if out is None:
        out = (
            ROOT / "examples" / "rl_mm_eval.json"
            if args.profile == "report"
            else ROOT / "artifacts" / "rl_mm_smoke.json"
        )
    ledger = args.ledger or out.with_suffix(".ledger.jsonl")
    report = run_profile(args.profile, out=out, ledger_path=ledger)
    learner = report["learner"]["mean_session_return"]
    baseline = report["as_baseline"]["mean_session_return"]
    print(f"wrote {out}")
    print("learner_mean_session_return", learner)
    print("as_mean_session_return", baseline)
    print("difference", report["comparison"]["learner_minus_as_mean_session_return"])
    print("orders_submitted", report["orders_submitted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
