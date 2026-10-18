# Unusual options flow

Read-only scan of Alpaca option chains. It GETs snapshots, trades, contract open interest, and the underlying IEX price. It has no write method. `orders_submitted` on every document is 0.

## Feeds

`indicative` is the free default. `opra` is used only when `--feed opra` or `SIDECAR_OPTIONS_FEED=opra` is set. A rejected OPRA call is not retried on the other feed. Open interest comes from the paper host's contract reference (`GET /v2/options/contracts`). Interest with a missing date, or a date more than 5 calendar days before the session, is ignored.

The watchlist is fixed in `config.py`: SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, TSLA, GOOGL, AMD, AVGO. It does not change what the options runner is allowed to send.

Credentials are the sidecar paper pair only (`SIDECAR_PAPER_ALPACA_KEY_ID` / `SIDECAR_PAPER_ALPACA_SECRET`, or the sidecar keyring service). A missing pair stops the scan. The offline event study does not need them.

## Score

The weights were set before any return in this tree was computed from them.

| Flag | Rule | Points |
|---|---|---|
| Volume / open interest | volume ≥ 100 and volume / open interest ≥ 2 | `min(3, ratio)` |
| Volume z-score | volume ≥ 100 and z ≥ 2 against the previous 20 sessions of that contract | `min(3, z)` |
| Large premium | one print with price × size × 100 ≥ $50,000 | 1 |
| Aggressive buy | such a print at or above the ask | 1 |
| Sweep | at least two of those prints inside 1 second, total premium ≥ $50,000 | 1 |
| Short-dated OTM | 0–14 days, strike more than 2% through spot (delta band only if spot is missing) | 1 |
| Skew shift | that same contract, and put IV minus call IV rose by at least 0.02 versus the previous stored session | 1 |

An alert is a score of at least 3. Each flag is stored. A flag is an alert on its own only when its points reach 3, which is a volume/open-interest ratio of at least 3 or a volume z-score of at least 3. The print flags are 1 point each, so a large aggressive sweep is an alert because those three points add up. The z-score uses the sample standard deviation of prior sessions only. Fewer than 20 priors, or a zero standard deviation, leaves z empty. The ask stamped on a print is the scan's quote unless the trade payload has its own ask. That is a same-scan approximation, not a reconstructed NBBO.

Skew uses at least two puts and two calls in a 2% to 10% out-of-the-money band. The underlying row also carries put and call volume, the put/call volume ratio, and the short-dated OTM volume share. Those ratios are features. They are not extra alert rules.

`examples/flow_scan_sample.json` and `examples/flow_scan_sample.md` are a hand-built chain, so the document format can be read without credentials.

## Event study

`python -m sidecar.flow study` asks whether alert dates predict the underlying's adjusted close 1 and 5 sessions later. Both horizons are trials (`n_trials = 2`). Bonferroni's bar for one horizon is `1 - PSR < 0.025`. Entry is the alert session's adjusted close. There is no historical options chain in this tree, so the committed run uses a fixed SPY calendar: every 21st session, starting at session index 252. That calendar is not unusual options activity.

`examples/flow_event_study.json` (48 events):

| Horizon | Mean return | Hit rate | PSR | DSR | Clears Bonferroni |
|---|---:|---:|---:|---:|---|
| 1 session (primary) | -0.00063130791115 | 0.479166666667 | 0.34130343899 | 0.176531685547 | no |
| 5 sessions | 0.00447979580265 | 0.604166666667 | 0.953539256152 | 0.877062872537 | no |

The market question is unanswered. The 5-session PSR is the kind of number a calendar can produce, and those returns overlap, so PSR and DSR overstate the evidence. Annualized Sharpe in the nested eval report is a display scaling. `threshold_status` is `not_frozen`.

```bash
python -m sidecar.flow scan --out artifacts/flow_scan.json --markdown artifacts/flow_scan.md --history artifacts/flow_history.jsonl
python -m sidecar.flow study --out examples/flow_event_study.json --ledger examples/flow_event_trials.jsonl
```
