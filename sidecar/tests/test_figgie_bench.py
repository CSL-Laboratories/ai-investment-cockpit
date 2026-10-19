from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from sidecar.figgie_bench.agents import OBS_SIZE, observe
from sidecar.figgie_bench.llm_agent import LLMAgent, prompt_for
from sidecar.figgie_bench.rules import (
    CARDS_EACH,
    LAYOUTS,
    N_PLAYERS,
    PARTNER,
    POT_CENTS,
    PRICE_CENTS,
    STARTING_CENTS,
    SUITS,
    GameState,
    apply_action,
    goal_of,
    legal_mask,
    seat_view,
    settle,
)
from sidecar.figgie_bench.tournament import process_supported, run_tournament
from sidecar.llm_audit.showdown import classify


def _hands(goal_counts: list[int], goal: str = "clubs") -> list[dict[str, int]]:
    hands = []
    for count in goal_counts:
        hand = {suit: 0 for suit in SUITS}
        hand[goal] = count
        hands.append(hand)
    return hands


def test_layouts_match_the_public_goal_rule():
    assert len(LAYOUTS) == 12
    for counts in LAYOUTS:
        assert sum(counts.values()) == 40
        assert sorted(counts.values()) == [8, 10, 10, 12]
        twelve = next(suit for suit, count in counts.items() if count == 12)
        goal = goal_of(counts)
        assert goal == PARTNER[twelve]
        assert counts[goal] in (8, 10)
        assert goal != twelve


def test_deal_conserves_cards_and_hides_the_goal_from_the_seat():
    state = GameState.deal(np.random.default_rng(4))
    assert all(sum(hand.values()) == CARDS_EACH for hand in state.hands)
    totals = {suit: sum(hand[suit] for hand in state.hands) for suit in SUITS}
    assert totals == state.counts
    view = seat_view(state, 0, turn=0, n_turns=4)
    assert not hasattr(view, "goal")
    assert not hasattr(view, "twelve")
    assert observe(view).shape == (OBS_SIZE,)
    mask = legal_mask(state, 0)
    for suit in SUITS:
        if state.hands[0][suit] == 0:
            sell = [index for index in range(len(mask)) if index and decode_sell(index, suit)]
            assert sell
            assert all(mask[index] < -1.0e8 for index in sell)


def decode_sell(action: int, suit: str) -> bool:
    from sidecar.figgie_bench.rules import decode_action

    decoded = decode_action(action)
    return decoded is not None and decoded[0] == suit and decoded[1] == "sell"


def test_published_four_player_payout_and_a_trade():
    counts = {"spades": 12, "clubs": 10, "hearts": 10, "diamonds": 8}
    state = GameState(
        counts=counts,
        goal="clubs",
        twelve="spades",
        hands=_hands([1, 3, 5, 1]),
        chips=[STARTING_CENTS - 50_00] * N_PLAYERS,
    )
    settle(state)
    assert state.chips == [300_00 + 10_00, 300_00 + 30_00, 300_00 + 150_00, 300_00 + 10_00]
    assert sum(state.chips) == STARTING_CENTS * N_PLAYERS

    seller = {suit: 0 for suit in SUITS}
    seller["hearts"] = 1
    buyer = {suit: 0 for suit in SUITS}
    table = GameState(
        counts=counts,
        goal="clubs",
        twelve="spades",
        hands=[seller, buyer, {suit: 0 for suit in SUITS}, {suit: 0 for suit in SUITS}],
        chips=[1_000, 1_000, 1_000, 1_000],
    )
    offer = apply_action(table, 0, _sell("hearts", 8), turn=0)
    assert offer is None
    fill = apply_action(table, 1, _buy("hearts", 8), turn=1)
    assert fill is not None
    assert table.hands[1]["hearts"] == 1
    assert table.hands[0]["hearts"] == 0
    assert table.chips[0] == 1_000 + 800
    assert table.chips[1] == 1_000 - 800
    assert all(quote is None for sides in table.book.values() for quote in sides.values())
    assert sum(table.chips) == 4_000


def _sell(suit: str, dollars: int) -> int:
    from sidecar.figgie_bench.rules import PRICE_CENTS as cents
    from sidecar.figgie_bench.rules import SUITS as suits
    from sidecar.figgie_bench.rules import action_index

    return action_index(suits.index(suit), 1, cents.index(dollars * 100))


def _buy(suit: str, dollars: int) -> int:
    from sidecar.figgie_bench.rules import PRICE_CENTS as cents
    from sidecar.figgie_bench.rules import SUITS as suits
    from sidecar.figgie_bench.rules import action_index

    return action_index(suits.index(suit), 0, cents.index(dollars * 100))


def test_showdown_reason_does_not_use_the_reveal():
    hand = {"spades": 4, "clubs": 2, "hearts": 2, "diamonds": 2}
    assert process_supported("plurality_is_twelve", "clubs", hand, {"clubs": 0}) is True
    assert process_supported("plurality_is_twelve", "spades", hand, {"spades": 3}) is False
    assert process_supported("others_net_bought", "hearts", hand, {"hearts": 1}) is True
    decision = {
        "expected_direction": "up",
        "driver_class": "process",
        "process_supported": True,
    }
    hit = classify(decision, {"realized_return": 1.0, "market_return": 0.0, "beta": 0.0, "catalyst_occurred": False})
    miss = classify(decision, {"realized_return": -1.0, "market_return": 0.0, "beta": 0.0, "catalyst_occurred": False})
    assert hit["cell"] == "right_for_right_reasons"
    assert miss["cell"] == "wrong_for_right_reasons"


def test_llm_stub_is_free_and_the_cap_stops_the_client():
    view = seat_view(GameState.deal(np.random.default_rng(1)), 0, turn=0, n_turns=4)
    stub = LLMAgent(enable_llm=False)
    prompt = prompt_for(view)
    assert "goal" not in prompt
    assert stub.act(view) >= 0
    assert stub.calls == 0
    assert stub.spent == 0.0
    class _NoCall:
        def complete(self, prompt_text: str, *, schema: dict) -> dict:
            del prompt_text, schema
            return {}

    with pytest.raises(ValueError, match="max_cost_usd"):
        LLMAgent(enable_llm=True, client=_NoCall(), max_cost_usd=None)

    class Client:
        def __init__(self) -> None:
            self.n = 0

        def complete(self, prompt_text: str, *, schema: dict) -> dict:
            assert "goal" not in prompt_text
            assert "belief" in schema
            self.n += 1
            return {"action": "pass", "belief": "hearts", "confidence": 0.4, "driver": "others_net_bought"}

    client = Client()
    seat = LLMAgent(enable_llm=True, client=client, max_cost_usd=0.02, price_per_call=0.01)
    assert seat.act(view) == 0
    assert seat.act(view) == 0
    assert seat.act(view) == 0
    assert client.n == 2
    assert seat.stopped is True
    assert seat.spent == pytest.approx(0.02)
    stated = seat.thesis(view, "spades", 0.2)
    assert stated["belief"] == "hearts"
    assert stated["model_id"] == "caller_llm"


def test_tournament_reports_intervals_markouts_and_showdown(tmp_path):
    report = run_tournament(
        seed=11,
        n_games=8,
        n_turns=6,
        bc_games=1,
        ppo_games=1,
        train=True,
        log_path=tmp_path / "figgie.jsonl",
        started_at=datetime(2026, 10, 19, 15, tzinfo=timezone.utc),
    )
    assert report["orders_submitted"] == 0
    assert report["yield_claim"] is False
    assert report["llm_calls"] == 0
    assert report["cost_usd"] == 0
    assert report["completion_invented"] is False
    assert report["showdown_n_scored"] == 32
    for role, row in report["roles"].items():
        assert row["n"] == 8
        assert row["normal_ci95"]["low"] <= row["mean_pnl"] <= row["normal_ci95"]["high"]
        assert row["bootstrap_mean_ci95"]["status"] == "ok"
        assert "goal_log_loss" in row
        assert "mean_markout" in row
        del role
    assert sum(report["showdown_cells"][cell]["n"] for cell in report["showdown_cells"]) == 32
    text = (tmp_path / "figgie.jsonl").read_text(encoding="utf-8")
    assert "realized_return" in text
    decision_lines = [line for line in text.splitlines() if '"kind":"decision"' in line or '"kind": "decision"' in line]
    assert decision_lines
    assert all("realized_return" not in line for line in decision_lines)


def test_price_grid_is_the_one_the_trade_uses():
    assert 800 in PRICE_CENTS
    assert POT_CENTS == 200_00
