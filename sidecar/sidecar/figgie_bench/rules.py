"""Discrete Figgie table.

Public economics follow https://www.figgie.com/how-to-play (retrieved 2026-10-19):

- 4 players, 40 cards, counts 12 / 10 / 10 / 8
- the 12-card suit is the common suit, not the goal
- the goal suit is the other suit of the same color, and it has 8 or 10 cards
- each player starts with $350 and antes $50 into a $200 pot
- each goal card pays $10 from the pot
- the remainder ($100 when the goal has 10 cards, $120 when it has 8) goes to
  the player or players with the most goal cards, split evenly; leftover cents
  go to the lowest seat indexes

The four-minute pit is a fixed number of turns so a run is deterministic.
After a trade the book is cleared. That matching convention is the one used
here; an open write-up of the same game describes the same clear
(https://scottfwlr.github.io/figgie/dist/index.html). Players may finish with
negative chips, as the public FAQ allows.

This module does not place broker orders. The RL package is a market-making
simulator and does not contain this card engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SUITS = ("spades", "clubs", "hearts", "diamonds")
PARTNER = {"spades": "clubs", "clubs": "spades", "hearts": "diamonds", "diamonds": "hearts"}
N_PLAYERS = 4
CARDS_EACH = 10
STARTING_CENTS = 350_00
ANTE_CENTS = 50_00
POT_CENTS = ANTE_CENTS * N_PLAYERS
GOAL_CARD_CENTS = 10_00
PRICES = (4, 6, 8, 10, 12)
PRICE_CENTS = tuple(price * 100 for price in PRICES)
N_ACTIONS = 1 + len(SUITS) * 2 * len(PRICES)
ILLEGAL = -1.0e9


def layouts() -> tuple[dict[str, int], ...]:
    """The 12 public decks. Goal is the partner of the 12-card suit."""
    found = []
    for twelve in SUITS:
        goal = PARTNER[twelve]
        others = [suit for suit in SUITS if suit not in (twelve, goal)]
        found.append({twelve: 12, goal: 8, others[0]: 10, others[1]: 10})
        for short in others:
            counts = {twelve: 12, goal: 10}
            for suit in others:
                counts[suit] = 8 if suit == short else 10
            found.append(counts)
    if len(found) != 12:
        raise RuntimeError("Figgie has 12 decks")
    return tuple(found)


LAYOUTS = layouts()


def goal_of(counts: dict[str, int]) -> str:
    twelve = next(suit for suit, count in counts.items() if count == 12)
    return PARTNER[twelve]


def twelve_of(counts: dict[str, int]) -> str:
    return next(suit for suit, count in counts.items() if count == 12)


def action_index(suit_index: int, side_index: int, price_index: int) -> int:
    return 1 + suit_index * (2 * len(PRICES)) + side_index * len(PRICES) + price_index


def decode_action(action: int) -> tuple[str, str, int] | None:
    if action == 0:
        return None
    if action < 0 or action >= N_ACTIONS:
        raise ValueError("action out of range")
    cursor = action - 1
    suit_index, cursor = divmod(cursor, 2 * len(PRICES))
    side_index, price_index = divmod(cursor, len(PRICES))
    side = "buy" if side_index == 0 else "sell"
    return SUITS[suit_index], side, PRICE_CENTS[price_index]


@dataclass
class Quote:
    seat: int
    suit: str
    side: str
    price_cents: int


@dataclass
class Fill:
    turn: int
    suit: str
    price_cents: int
    buyer: int
    seller: int
    aggressor: int


@dataclass(frozen=True)
class SeatView:
    """What one seat may use. The goal suit, the 12-card suit, and other hands are absent."""

    seat: int
    hand: dict[str, int]
    chips: int
    book: dict[str, dict[str, Quote | None]]
    last_trade: dict[str, int | None]
    public_flow: dict[str, int]
    turn: int
    n_turns: int


def seat_view(state: GameState, seat: int, *, turn: int, n_turns: int) -> SeatView:
    return SeatView(
        seat=seat,
        hand=dict(state.hands[seat]),
        chips=state.chips[seat],
        book={suit: dict(sides) for suit, sides in state.book.items()},
        last_trade=dict(state.last_trade),
        public_flow=dict(state.public_flow),
        turn=turn,
        n_turns=n_turns,
    )


@dataclass
class GameState:
    counts: dict[str, int]
    goal: str
    twelve: str
    hands: list[dict[str, int]]
    chips: list[int]
    book: dict[str, dict[str, Quote | None]] = field(default_factory=dict)
    last_trade: dict[str, int | None] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    public_flow: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.book:
            self.book = {suit: {"buy": None, "sell": None} for suit in SUITS}
        if not self.last_trade:
            self.last_trade = {suit: None for suit in SUITS}
        if not self.public_flow:
            self.public_flow = {suit: 0 for suit in SUITS}

    @classmethod
    def deal(cls, rng: np.random.Generator) -> GameState:
        counts = dict(LAYOUTS[int(rng.integers(0, len(LAYOUTS)))])
        cards: list[str] = []
        for suit, count in counts.items():
            cards.extend([suit] * count)
        deck = np.array(cards)
        rng.shuffle(deck)
        hands = [{suit: 0 for suit in SUITS} for _ in range(N_PLAYERS)]
        for seat in range(N_PLAYERS):
            for card in deck[seat * CARDS_EACH : (seat + 1) * CARDS_EACH]:
                hands[seat][str(card)] += 1
        return cls(
            counts=counts,
            goal=goal_of(counts),
            twelve=twelve_of(counts),
            hands=hands,
            chips=[STARTING_CENTS - ANTE_CENTS for _ in range(N_PLAYERS)],
        )


def legal_mask_view(view: SeatView) -> np.ndarray:
    mask = np.full(N_ACTIONS, ILLEGAL, dtype=np.float64)
    mask[0] = 0.0
    for suit_index, suit in enumerate(SUITS):
        for price_index in range(len(PRICES)):
            mask[action_index(suit_index, 0, price_index)] = 0.0
            if view.hand[suit] > 0:
                mask[action_index(suit_index, 1, price_index)] = 0.0
    return mask


def legal_mask(state: GameState, seat: int) -> np.ndarray:
    return legal_mask_view(seat_view(state, seat, turn=0, n_turns=1))


def _clear(state: GameState) -> None:
    state.book = {suit: {"buy": None, "sell": None} for suit in SUITS}


def _trade(
    state: GameState,
    *,
    buyer: int,
    seller: int,
    suit: str,
    price_cents: int,
    aggressor: int,
    turn: int,
) -> Fill:
    if state.hands[seller][suit] <= 0:
        raise RuntimeError("seller has no card")
    state.hands[seller][suit] -= 1
    state.hands[buyer][suit] += 1
    state.chips[buyer] -= price_cents
    state.chips[seller] += price_cents
    state.last_trade[suit] = price_cents
    sign = 1 if aggressor == buyer else -1
    state.public_flow[suit] += sign
    fill = Fill(
        turn=turn,
        suit=suit,
        price_cents=price_cents,
        buyer=buyer,
        seller=seller,
        aggressor=aggressor,
    )
    state.fills.append(fill)
    _clear(state)
    return fill


def apply_action(state: GameState, seat: int, action: int, *, turn: int) -> Fill | None:
    """Post or take one card. Illegal sells and out-of-range actions become a pass."""
    if action < 0 or action >= N_ACTIONS:
        return None
    decoded = decode_action(action)
    if decoded is None:
        return None
    suit, side, price_cents = decoded
    if side == "sell" and state.hands[seat][suit] <= 0:
        return None
    opposite = "sell" if side == "buy" else "buy"
    resting = state.book[suit][opposite]
    if resting is not None and resting.seat != seat:
        crosses = price_cents >= resting.price_cents if side == "buy" else price_cents <= resting.price_cents
        seller = resting.seat if resting.side == "sell" else seat
        if crosses and state.hands[seller][suit] > 0:
            if side == "buy":
                return _trade(
                    state,
                    buyer=seat,
                    seller=resting.seat,
                    suit=suit,
                    price_cents=resting.price_cents,
                    aggressor=seat,
                    turn=turn,
                )
            return _trade(
                state,
                buyer=resting.seat,
                seller=seat,
                suit=suit,
                price_cents=resting.price_cents,
                aggressor=seat,
                turn=turn,
            )
        if state.hands[seller][suit] <= 0:
            state.book[suit][opposite] = None
    current = state.book[suit][side]
    improves = current is None or current.seat == seat
    if current is not None and current.seat != seat:
        improves = price_cents > current.price_cents if side == "buy" else price_cents < current.price_cents
    if improves:
        state.book[suit][side] = Quote(seat=seat, suit=suit, side=side, price_cents=price_cents)
    return None


def settle(state: GameState) -> None:
    """Pay the pot. Ante plus this payout is zero-sum. Trades are already zero-sum."""
    counts = [state.hands[seat][state.goal] for seat in range(N_PLAYERS)]
    if sum(counts) != state.counts[state.goal]:
        raise RuntimeError("goal cards are not conserved")
    remainder = POT_CENTS - GOAL_CARD_CENTS * sum(counts)
    if remainder not in (100_00, 120_00):
        raise RuntimeError("remainder is not the published bonus")
    for seat, count in enumerate(counts):
        state.chips[seat] += GOAL_CARD_CENTS * count
    best = max(counts)
    winners = [seat for seat, count in enumerate(counts) if count == best]
    base, extra = divmod(remainder, len(winners))
    for index, seat in enumerate(winners):
        state.chips[seat] += base + (1 if index < extra else 0)


def pnl_cents(state: GameState) -> list[int]:
    return [chips - STARTING_CENTS for chips in state.chips]
