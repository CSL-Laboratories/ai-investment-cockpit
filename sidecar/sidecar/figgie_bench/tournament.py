"""Play Figgie, score each seat with the showdown 2x2, and write a report.

No broker order is submitted. The default language seat is the stub. A real
client runs only when ``enable_llm`` is set and the cost cap is positive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from sidecar.figgie_bench.agents import GRUAgent, RandomAgent, RuleAgent, fresh_policy
from sidecar.figgie_bench.infer import Belief, plurality
from sidecar.figgie_bench.learn import train_smoke
from sidecar.figgie_bench.llm_agent import LLMAgent
from sidecar.figgie_bench.metrics import bootstrap_mean_ci, markouts, mean_or_none, normal_ci
from sidecar.figgie_bench.rules import (
    N_PLAYERS,
    PARTNER,
    STARTING_CENTS,
    SUITS,
    GameState,
    apply_action,
    pnl_cents,
    seat_view,
    settle,
)
from sidecar.jsonutil import write_json
from sidecar.llm_audit.showdown import ShowdownLog, score_showdown


def process_supported(driver: str, belief: str, hand: dict[str, int], flow: dict[str, int]) -> bool:
    """Decision-time evidence. The true goal is not an input."""
    if belief not in SUITS:
        return False
    if driver == "plurality_is_twelve":
        picked = plurality(hand)
        return picked is not None and PARTNER[picked[0]] == belief
    if driver == "others_net_bought":
        return flow.get(belief, 0) > 0
    return False


def play_game(
    agents: list,
    rng: np.random.Generator,
    *,
    n_turns: int,
    game_index: int,
    started_at: datetime,
    log: ShowdownLog | None,
    seed: int,
) -> dict:
    state = GameState.deal(rng)
    for agent in agents:
        agent.begin_game()
    beliefs = [Belief(dict(state.hands[seat])) for seat in range(N_PLAYERS)]
    logloss: list[list[float]] = [[] for _ in range(N_PLAYERS)]
    for turn in range(n_turns):
        for seat in range(N_PLAYERS):
            view = seat_view(state, seat, turn=turn, n_turns=n_turns)
            if hasattr(view, "goal"):
                raise RuntimeError("seat view exposes the goal")
            action = agents[seat].act(view)
            fill = apply_action(state, seat, action, turn=turn)
            if fill is None:
                continue
            aggressive_side = "buy" if fill.aggressor == fill.buyer else "sell"
            for other in range(N_PLAYERS):
                if other == fill.aggressor:
                    continue
                beliefs[other].update_trade(suit=fill.suit, aggressive_side=aggressive_side)
                logloss[other].append(beliefs[other].log_loss(state.goal))
    base = started_at + timedelta(seconds=game_index * 20)
    horizon_end = base + timedelta(seconds=max(n_turns, 1))
    reviews = []
    for seat in range(N_PLAYERS):
        view = seat_view(state, seat, turn=n_turns, n_turns=n_turns)
        mode, mode_prob = beliefs[seat].mode()
        stated = agents[seat].thesis(view, mode, mode_prob)
        belief = str(stated["belief"])
        if belief not in SUITS:
            belief = mode
        driver = str(stated["driver"])
        if agents[seat].name == "llm":
            confidence = float(stated["confidence"])
        else:
            confidence = float(beliefs[seat].goal_probabilities()[belief])
        confidence = min(1.0, max(0.0, confidence))
        supported = process_supported(driver, belief, state.hands[seat], state.public_flow)
        reviews.append(
            {
                "seat": seat,
                "role": agents[seat].name,
                "belief": belief,
                "driver": driver,
                "confidence": confidence,
                "process_supported": supported,
                "model_id": str(stated["model_id"]),
                "goal": state.goal,
            }
        )
    settle(state)
    if sum(state.chips) != STARTING_CENTS * N_PLAYERS:
        raise RuntimeError("chips are not conserved")
    pnls = pnl_cents(state)
    if log is not None:
        for review in reviews:
            seat = int(review["seat"])
            judgment_id = f"figgie-{seed}-{game_index}-{review['role']}-{seat}"
            decision_at = base + timedelta(milliseconds=seat)
            outcome_at = horizon_end + timedelta(seconds=1)
            realized = 1.0 if review["belief"] == review["goal"] else -1.0
            log.append_decision(
                {
                    "judgment_id": judgment_id,
                    "route": review["role"],
                    "as_of": base.isoformat(),
                    "horizon_end": horizon_end.isoformat(),
                    "symbol": review["belief"],
                    "horizon": "game",
                    "thesis": f"goal_suit={review['belief']}",
                    "driver": review["driver"],
                    "driver_class": "process",
                    "expected_direction": "up",
                    "confidence": review["confidence"],
                    "process_supported": review["process_supported"],
                    "model_id": review["model_id"],
                },
                recorded_at=decision_at,
            )
            log.append_outcome(
                {
                    "judgment_id": judgment_id,
                    "outcome_at": outcome_at.isoformat(),
                    "realized_return": realized,
                    "market_return": 0.0,
                    "beta": 0.0,
                    "catalyst_occurred": False,
                },
                recorded_at=outcome_at,
            )
    role_markout = [markouts(state.fills, seat, state.goal) for seat in range(N_PLAYERS)]
    return {
        "pnl_cents": pnls,
        "roles": [agent.name for agent in agents],
        "goal": state.goal,
        "twelve": state.twelve,
        "logloss": logloss,
        "markouts": role_markout,
        "n_fills": len(state.fills),
        "reviews": reviews,
    }


def _rotate(agents: list, game_index: int) -> list:
    return [agents[(seat - game_index) % len(agents)] for seat in range(len(agents))]


def _trade_curve(series: list[list[float]]) -> list[float | None]:
    if not series:
        return []
    longest = max(len(row) for row in series)
    curve = []
    for index in range(longest):
        sample = [row[index] for row in series if index < len(row)]
        curve.append(None if not sample else sum(sample) / len(sample))
    return curve


def run_tournament(
    *,
    seed: int = 0,
    n_games: int = 8,
    n_turns: int = 8,
    bc_games: int = 1,
    ppo_games: int = 1,
    train: bool = True,
    enable_llm: bool = False,
    client: object | None = None,
    max_cost_usd: float | None = None,
    price_per_call: float = 0.01,
    log_path: Path | None = None,
    started_at: datetime | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    if train:
        policy = train_smoke(rng, bc_games=bc_games, ppo_games=ppo_games, n_turns=min(n_turns, 6))
    else:
        policy = fresh_policy(rng)
    gru = GRUAgent(policy, rng, greedy=True)
    llm = LLMAgent(
        enable_llm=enable_llm,
        client=client,
        max_cost_usd=max_cost_usd,
        price_per_call=price_per_call,
    )
    order = [RandomAgent(rng), RuleAgent(), gru, llm]
    moment = started_at or datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")
    log = ShowdownLog(log_path) if log_path is not None else None
    games = [
        play_game(
            _rotate(order, index),
            rng,
            n_turns=n_turns,
            game_index=index,
            started_at=moment,
            log=log,
            seed=seed,
        )
        for index in range(n_games)
    ]
    by_role: dict[str, dict] = {}
    for role in {agent.name for agent in order}:
        pnls: list[float] = []
        losses: list[float] = []
        marks: list[float] = []
        curve: list[list[float]] = []
        for game in games:
            for seat, name in enumerate(game["roles"]):
                if name != role:
                    continue
                pnls.append(game["pnl_cents"][seat] / 100.0)
                losses.extend(game["logloss"][seat])
                curve.append(game["logloss"][seat])
                marks.extend(row["markout"] for row in game["markouts"][seat])
        # Dollar P&L is not a simple return. The equity bootstrap compounds its input,
        # so the series is P&L divided by the $350 start and the interval is scaled back.
        scaled = [value / (STARTING_CENTS / 100.0) for value in pnls]
        boot = bootstrap_mean_ci(scaled, seed=seed)
        if boot and boot.get("status") == "ok":
            boot = dict(boot)
            boot["low"] = boot["low"] * (STARTING_CENTS / 100.0)
            boot["high"] = boot["high"] * (STARTING_CENTS / 100.0)
            boot["unit"] = "dollars"
        by_role[role] = {
            "n": len(pnls),
            "mean_pnl": None if not pnls else sum(pnls) / len(pnls),
            "normal_ci95": normal_ci(pnls),
            "bootstrap_mean_ci95": boot,
            "goal_log_loss": mean_or_none(losses),
            "log_loss_by_trade": _trade_curve(curve),
            "mean_markout": mean_or_none(marks),
            "n_markout": len(marks),
        }
    showdown = score_showdown(log.entries()) if log is not None else None
    return {
        "schema_version": "sidecar.figgie_bench.v1",
        "threshold_status": "not_frozen",
        "yield_claim": False,
        "orders_submitted": 0,
        "n_games": n_games,
        "n_turns": n_turns,
        "seed": seed,
        "enable_llm": enable_llm,
        "llm_calls": llm.calls,
        "llm_rejected": llm.rejected,
        "llm_stopped_for_cap": llm.stopped,
        "cost_usd": llm.spent,
        "completion_invented": False,
        "roles": by_role,
        "showdown_cells": None if showdown is None else showdown["cells"],
        "showdown_n_scored": None if showdown is None else showdown["n_scored"],
        "showdown_brier": None if showdown is None else showdown["brier"],
        "notes": [
            "Rules follow figgie.com/how-to-play: the goal suit is the other suit of the same color as the 12-card suit.",
            "The RL package is a market-making simulator and has no card engine. This bench reuses its masked GRU, clipped PPO, and the Avellaneda-Stoikov half-spread with a nonzero inventory target.",
            "CS224R Figgie win rates are not used. Fresh smoke weights are not the committed market-making checkpoint.",
            "Goal-suit log loss uses a declared 12-deck posterior updated from other players' aggressive trades.",
            "Markout is sign(side) * (reference - fill price). Negative is adverse. The reference is the next trade in that suit within 3 trades, else $10 for a goal card and $0 otherwise.",
            "The stationary bootstrap is applied to P&L divided by the $350 start, then scaled back to dollars, because that helper compounds its input as a simple return.",
            "Post-game review uses the showdown 2x2. The stated goal suit is an up call. The reason bit is decision-time evidence, not the reveal.",
            "No model completion is invented here. The stub plays the rule bot at cost $0 unless enable_llm is set with a client and a cap.",
            "Win rates on this bench are not an edge and are not a qualify decision. threshold_status is not_frozen.",
        ],
    }


def render_tournament(report: dict) -> str:
    lines = [
        "# Figgie bench",
        "",
        "Four seats play the public Figgie payout. The goal suit is the other suit of the same color as the 12-card suit. It is not the 12-card suit.",
        "",
        "The RL package does not contain a card engine. The GRU, the mask, behavioral cloning, and clipped PPO are reused from that market-making simulator. The rule bot uses the Avellaneda-Stoikov half-spread with inventory measured against a target of 4 goal cards. CS224R Figgie win rates are not used.",
        "",
        f"`threshold_status` is `{report['threshold_status']}`. `yield_claim` is false. Orders submitted: {report['orders_submitted']}.",
        f"Language calls: {report['llm_calls']}. Cost: ${report['cost_usd']:.2f}. Completions invented by this module: {report['completion_invented']}.",
        "",
        f"Games: {report['n_games']}. Turns per game: {report['n_turns']}. Seed: {report['seed']}.",
        "",
        "## Mean P&L per game ($)",
        "",
        "| role | n | mean | normal 95% CI | goal log loss | mean markout |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for role, row in sorted(report["roles"].items()):
        ci = row["normal_ci95"]
        if ci["low"] is None:
            interval = "n/a"
        else:
            interval = f"{ci['low']:.2f} to {ci['high']:.2f}"
        lines.append(
            f"| {role} | {row['n']} | {_num(row['mean_pnl'])} | {interval} | {_num(row['goal_log_loss'])} | {_num(row['mean_markout'])} |"
        )
    lines.extend(["", "## Goal-suit log loss by trade index", ""])
    for role, row in sorted(report["roles"].items()):
        curve = ", ".join("n/a" if item is None else f"{item:.3f}" for item in row["log_loss_by_trade"][:12])
        lines.append(f"- `{role}`: {curve or 'no other-player trades'}")
    lines.extend(
        [
            "",
            "## Showdown",
            "",
            "Each seat's stated goal suit is scored with the same 2x2 as the judgment showdown. Direction is whether that suit was the goal. The reason bit is the evidence stored before the reveal: a unique plurality whose partner is the stated suit, or a positive public net buy of that suit.",
            "",
        ]
    )
    if report["showdown_cells"] is None:
        lines.append("No showdown log was attached.")
    else:
        lines.append(f"Scored {report['showdown_n_scored']} seat-games. Brier {_num(report['showdown_brier'])}.")
        for cell, item in report["showdown_cells"].items():
            lines.append(f"- {cell}: {item['n']} ({item['share']:.4f})")
    lines.extend(["", "These games test the bench. They do not estimate a trading edge.", ""])
    return "\n".join(lines)


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def write_example(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "figgie_tournament.jsonl"
    if log_path.exists():
        log_path.unlink()
    report = run_tournament(
        seed=7,
        n_games=8,
        n_turns=8,
        bc_games=1,
        ppo_games=1,
        train=True,
        log_path=directory / "figgie_tournament.jsonl",
    )
    (directory / "figgie_tournament.md").write_text(render_tournament(report), encoding="utf-8")
    write_json(directory / "figgie_tournament.json", report)
    return report
