# rl

Simulation-only market making. This package does not read broker credentials and does not place orders. The techniques are transferred from the Stanford CS224R Figgie report (PPO, a GRU policy, masked discrete actions, behavioral cloning, prioritized fictitious self-play, potential-based reward shaping). Figgie win rates from that report are not results of this module.

## What is transferred

- Masked categorical actions. Illegal quote actions get a large negative logit.
- A small GRU policy and value head, trained with a clipped PPO surrogate and GAE. NumPy only.
- Behavioral cloning from an Avellaneda-Stoikov expert before PPO. Inventory-reducing labels are upweighted (weight 15), the same idea as that report's rare-action lift weight.
- Prioritized fictitious self-play: a snapshot is sampled with probability proportional to `(1 - win_rate)^2`, mixed with probability 0.3 against the AS heuristic. The other flow is always present: noise takers, informed takers who see the next shock, and an order-flow momentum taker. In a two-maker step the tighter quote wins, and a tie goes to the opponent.
- Potential-based shaping `beta * (discount * phi(next) - phi)`, `beta = 0.5`, `discount = 0.99`, `phi` = mark-to-market wealth, and `phi(terminal) = 0`. The environment reward is zero until the terminal wealth change.

The expert is `sidecar.rl.as_expert`. It is the same reservation and half-spread expression as the cockpit quoter, with simulator-unit gamma and kappa on a $100 mid, rounded outward to the cent. It is not imported from the cockpit tree. At the inventory limit the maker stops quoting the side that would add to the position.

The learner's action grid is three half-spreads ($0.02, $0.05, $0.12) by three skews (−$0.08, $0, +$0.08).

## Commands

Smoke (what the unit tests call):

```bash
python -m sidecar.rl.train --profile smoke
```

Longer CPU run. The checked-in `examples/rl_mm_eval.json` is one execution of this command. Re-running it replaces that file; quote the committed file, not a fresh draw, when citing the pull request.

```bash
python -m sidecar.rl.train --profile report --out examples/rl_mm_eval.json --ledger examples/rl_mm_eval.ledger.jsonl
```

## Committed report

`examples/rl_mm_eval.json` is one report-profile run: 12 held-out sessions, 4 hyperparameter rows, PBO on 4 slices. The learner's mean session return was **-0.0006479044891501824** against the AS baseline **-0.00028131631563966175** (difference **-0.00036658817351052067**). Learner PSR **0.111498808829**, DSR **0.00795028414945**, PBO **0.666666666667**. AS PSR **0.271611965439**, DSR **0.0358877829619**. Acceptance was 0.4037607609072773 versus 0.40062777351083806. Mean adverse-selection score (negative is adverse) was 0.002677305484321503 versus 0.008319932859576733. On the daily-bar replay the learner mean session return was -0.0031383453369656137 versus AS -0.002400723571648157. Behavioral-cloning loss on the primary seed moved from 2.148449016515442 to 1.256130490059318. Two of the four hyperparameter rows tied. The learner did not beat the AS baseline. None of these figures is a qualify decision.

## Evaluation

Held-out simulated sessions (seeds 10000 and up) run the greedy learner and the AS expert separately against the same taker draws. `evalstats` reports PSR, DSR, drawdown, and a bootstrap interval. PBO is CSCV across the seed and learning-rate rows on that matrix. The trial ledger records each of those configs plus the AS baseline. `n_trials` is the distinct config count.

`historical_daily_replay_not_l1` replays SPY daily simple returns from `fixtures/etf_daily.csv` as the mid path. This repository has no L1 tape. A daily move is the wrong horizon for a quote, and the block is labeled that way.

Quote acceptance is filled sides over posted sides. Adverse selection is the mean of `sign(side) * (next_mid - fill_price)`. Negative means the mid moved against the fill.

`threshold_status` stays `not_frozen`. A negative comparison against AS is a result, not something to tune away and hide.

## Retrofit

Do not connect this module to a broker. A later integration would keep training and the simulator here, and would still have to pass the cockpit's risk, mode, and approval path before any order. Nothing in this package is that path. The AS formula in `as_expert.py` can be compared with the cockpit quoter; it must not be wired around it.
