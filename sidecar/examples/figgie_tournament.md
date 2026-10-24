# Figgie bench

Four seats play the public Figgie payout. The goal suit is the other suit of the same color as the 12-card suit. It is not the 12-card suit.

The RL package does not contain a card engine. The GRU, the mask, behavioral cloning, and clipped PPO are reused from that market-making simulator. The rule bot uses the Avellaneda-Stoikov half-spread with inventory measured against a target of 4 goal cards. CS224R Figgie win rates are not used.

`threshold_status` is `not_frozen`. `yield_claim` is false. Orders submitted: 0.
Language calls: 0. Cost: $0.00. Completions invented by this module: False.

Games: 8. Turns per game: 8. Seed: 7.

## Mean P&L per game ($)

| role | n | mean | normal 95% CI | goal log loss | mean markout |
| --- | --- | --- | --- | --- | --- |
| gru | 8 | -4.2500 | -36.42 to 27.92 | 1.4947 | 8.2500 |
| llm_stub | 8 | -2.5000 | -33.15 to 28.15 | 1.4572 | -5.2500 |
| random | 8 | 33.0000 | -9.62 to 75.62 | 1.5195 | -2.6000 |
| rule | 8 | -26.2500 | -45.83 to -6.67 | 1.4527 | 1.0000 |

## Goal-suit log loss by trade index

- `gru`: 1.480, 1.527, 1.485
- `llm_stub`: 1.441, 1.474, 1.485
- `random`: 1.485, 1.658
- `rule`: 1.393, 1.564, 1.485

## Showdown

Each seat's stated goal suit is scored with the same 2x2 as the judgment showdown. Direction is whether that suit was the goal. The reason bit is the evidence stored before the reveal: a unique plurality whose partner is the stated suit, or a positive public net buy of that suit.

Scored 32 seat-games. Brier 0.1992.
- right_for_right_reasons: 5 (0.1562)
- right_for_wrong_reasons: 3 (0.0938)
- wrong_for_right_reasons: 18 (0.5625)
- wrong_for_wrong_reasons: 6 (0.1875)

These games test the bench. They do not estimate a trading edge.
