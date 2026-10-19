# figgie bench

A four-seat Figgie table used to score a decision layer. Nothing here submits an order.

The `rl` package is a market-making simulator. It does not contain a card engine. This bench implements the public rules and reuses that package's masked GRU, behavioral cloning, clipped PPO, and the Avellaneda-Stoikov half-spread. The half-spread is pointed at a target of 4 cards in the suit the rule treats as the goal, not at zero inventory. CS224R Figgie win rates are not results of this module, and the committed market-making checkpoint is not loaded.

## Rules pinned from figgie.com/how-to-play

Retrieved 2026-10-19.

- 4 players and 40 cards. One suit has 12, one has 8, two have 10.
- The 12-card suit is the common suit. The goal suit is the other suit of the same color (spades/clubs, hearts/diamonds). The goal has 8 or 10 cards.
- Each player starts with $350 and antes $50. The pot is $200.
- Each goal card pays $10. The remainder ($100 or $120) goes to the seats that hold the most goal cards, split evenly. Leftover cents go to the lowest seat indexes.
- Players may finish with negative chips.

The public four-minute pit is a fixed number of turns. A trade clears the book. Those two choices keep a run deterministic.

## Seats

- `random` samples a legal action.
- `rule` treats its unique plurality suit as the 12 and trades the partner as the goal.
- `gru` is a fresh masked GRU, cloned from the rule for one or two games and then updated with clipped PPO for one or two games.
- `llm_stub` plays the rule and does not call a model. Cost is $0. `enable_llm=True` requires a caller-supplied `client.complete` and a positive `max_cost_usd`. This package does not open a socket. A call that would exceed the cap is not sent.

Agents see their own hand, the book, and the public aggressive flow. They do not see the goal suit, the 12-card suit, or another seat's hand.

## Metrics

Mean P&L per game, in dollars versus the $350 start, with a normal 95% interval. Eight or more games also request the stationary bootstrap in `evalstats`, on P&L divided by the $350 start, with the interval scaled back to dollars. That bootstrap compounds its input, so a series that drives the equity curve through zero is reported as unavailable. Goal-suit log loss is the declared 12-deck posterior after each trade the seat did not initiate. Markout is `sign(side) * (reference - fill price)`. Negative is adverse. The reference is the next trade in that suit within three trades, otherwise $10 for a goal card and $0 for any other suit.

After the hand, each stated goal suit is scored with `sidecar.llm_audit.showdown`. Direction is whether that suit was the goal. The reason bit is evidence from before the reveal.

## Command

```bash
python -m sidecar.figgie_bench
```

`threshold_status` stays `not_frozen`. A win rate here is not an edge and not a qualify decision.
