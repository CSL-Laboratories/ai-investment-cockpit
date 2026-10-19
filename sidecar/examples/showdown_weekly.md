# Showdown weekly report

Each row was logged as a decision before its outcome. The thesis hash is the decision-time text. A later row cannot replace it.

Direction hits only when the realized simple return is strictly positive for an up call or strictly negative for a down call. A flat return is a miss.

The stated driver happened when a market call has `beta * market_return` on the called side, when a catalyst call matches a news or sentiment event that was not already priced, or when a process call's decision-time evidence bit is true. The unlucky cell is that bit plus a missed direction. The first cell also requires the attribution test: the market component dominates for a market call, and the idiosyncratic residual dominates and has the called sign for a catalyst. Process calls have no beta test.

`threshold_status` is `not_frozen`. `yield_claim` is false. Orders submitted: 0.

Scored 11 of 11 decisions. Excluded: {}.

## Cells

| Cell | n | share |
| --- | --- | --- |
| right_for_right_reasons | 7 | 0.6364 |
| right_for_wrong_reasons | 0 | 0.0000 |
| wrong_for_right_reasons | 1 | 0.0909 |
| wrong_for_wrong_reasons | 3 | 0.2727 |

Direction hit rate: 0.636364.
Driver-happened rate: 0.727273.
Brier of confidence against the direction hit: 0.315758.
Constant-0.5 null Brier: 0.250000.
Mean absolute magnitude error (2 rows): 0.030098.

## Reliability by confidence

| bin | n | mean confidence | direction hit rate |
| --- | --- | --- | --- |
| 0.6-0.7 | 2 | 0.6274 | 0.5000 |
| 0.9-1.0 | 9 | 0.9900 | 0.6667 |

## By driver class

- `catalyst` n=8, direction hit rate 0.6250, happened rate 0.6250. right_for_right_reasons=5, right_for_wrong_reasons=0, wrong_for_right_reasons=0, wrong_for_wrong_reasons=3.
- `market` n=1, direction hit rate 1.0000, happened rate 1.0000. right_for_right_reasons=1, right_for_wrong_reasons=0, wrong_for_right_reasons=0, wrong_for_wrong_reasons=0.
- `process` n=2, direction hit rate 0.5000, happened rate 1.0000. right_for_right_reasons=1, right_for_wrong_reasons=0, wrong_for_right_reasons=1, wrong_for_wrong_reasons=0.

## Weeks

- `2024-W09` n=9, Brier 0.326767. right_for_right_reasons=6, right_for_wrong_reasons=0, wrong_for_right_reasons=0, wrong_for_wrong_reasons=3.
- `2026-W31` n=1, Brier 0.138859. right_for_right_reasons=1, right_for_wrong_reasons=0, wrong_for_right_reasons=0, wrong_for_wrong_reasons=0.
- `2026-W32` n=1, Brier 0.393583. right_for_right_reasons=0, right_for_wrong_reasons=0, wrong_for_right_reasons=1, wrong_for_wrong_reasons=0.

Synthetic tapes and card games test whether the score can separate a lucky direction from the stated reason. They do not show an edge.
