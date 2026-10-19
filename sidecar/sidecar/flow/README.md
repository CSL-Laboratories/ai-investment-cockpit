# Unusual options flow

Read-only scan of Alpaca option chains. It GETs snapshots, trades, contract open interest, and the underlying IEX price. It has no write method. `orders_submitted` on every document is 0.

## Feeds

`indicative` is the free default. `opra` is used only when `--feed opra` or `SIDECAR_OPTIONS_FEED=opra` is set, and only on the snapshot request. The historical trades endpoint rejects a `feed` parameter, so that call does not send one. Trades are requested in batches of 100 symbols. A 403 on trades (no OPRA agreement) does not fail the scan: `tape` is `snapshot_only`, the markdown says so, and sweeps are not detected because only each contract's latest snapshot trade is available. A rejected snapshot feed is still an error and is not retried on the other feed.

Open interest comes from the paper host's contract reference (`GET /v2/options/contracts`). Alpaca's default expiration filter stops at the next weekend, so the scan sends the expiration bounds of the snapshot chain and follows every page token (`next_page_token` or `page_token`). Open interest is often a string in that payload; it is parsed. Interest with a missing date, or a date more than 5 calendar days before the session, is ignored.

The session is the last Alpaca calendar day on or before the scan date. A Saturday, Sunday, or holiday uses that session for the trade window and the score, and the document says so.

The scan universe is `FLOW_UNIVERSE` in `config.py`: SPY, QQQ, IWM, and 30 single names (AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, AVGO, NFLX, JPM, BAC, XOM, UNH, LLY, COIN, PLTR, MSTR, SMCI, BA, DIS, INTC, MU, UBER, CRM, ORCL, QCOM, HD, COST, WMT). `SIDECAR_FLOW_WATCHLIST` may name a comma-separated subset. It cannot add a symbol. Sentiment still uses the original twelve-name `WATCHLIST`. Neither list changes what the options runner is allowed to send.

Live GETs wait `SIDECAR_FLOW_MIN_INTERVAL` seconds (default 0.3, about 200 requests per minute) and retry HTTP 429 up to three more times. Stock snapshots are batched 50 symbols at a time. Option trades stay in batches of 100. Underlying chains are still requested one name at a time because that is how the snapshots route is shaped.

Credentials are the sidecar paper pair only (`SIDECAR_PAPER_ALPACA_KEY_ID` / `SIDECAR_PAPER_ALPACA_SECRET`, or the sidecar keyring service). A missing pair stops the scan. The offline event study does not need them.

## Score

The first live SPY scan (session 2026-10-16, indicative, tape of trades) flagged 892 of 12,630 contracts at a score of 3 or more, mostly 0–3 DTE where volume over open interest is ordinary. The gates below were set after that scan. No return in this tree has been computed from them.

| Flag | Rule | Points |
|---|---|---|
| Volume / open interest | DTE greater than 2, open interest at least 100, volume at least 100, and the ratio's percentile inside that underlying and DTE bucket is at least 0.95. The bucket needs 20 eligible contracts. | 1 at the 95th, 2 at the 97th, 3 at the 99th |
| Volume z-score | volume at least 100 and z at least 2 against the previous 20 sessions of that contract | `min(3, z)` |
| Premium | the largest print's percentile inside that underlying and DTE bucket is at least 0.95, among at least 20 contracts that printed | 1 |
| Aggressive buy | a print at or above the ask with price × size × 100 at least $50,000 | 1 |
| Sweep | at least two of those prints inside 1 second, total premium at least $50,000 | 1 |
| Short-dated OTM | 3–14 days, not 0–2, strike more than 2% through spot (delta band only if spot is missing) | 1 |
| Skew shift | that same contract, and put IV minus call IV rose by at least 0.02 versus the previous stored session | 1 |

DTE buckets are 0–2, 3–14, 15–45, 46–180, and 181+. A percentile is the share of the bucket strictly below the contract, so a tie does not put every copy at the top. A bucket smaller than 20 awards no vol/OI and no premium point.

Volume z stays empty until that contract has 20 prior sessions, or when those sessions have zero variance. The field is `volume_z_status: unavailable`. The alert bar is then 4 instead of 3. A vol/OI percentile of 3, or a large aggressive sweep, is not an alert by itself until the baseline exists or another point is present. Once z is a number, the bar returns to 3 even if z itself is small.

An alert is a score at or above that bar. The published list is the top 15 alerts, at most 3 per underlying, ranked by score, then premium, then contract. Clusters group the same underlying, expiration, and direction and sum print premium. Direction is bullish for a call bought or a put sold, and bearish for a put bought or a call sold. If the largest print is not at the ask or the bid, the direction follows the call or the put and `aggressor` is `unknown`. The ask on a print is the scan's quote unless the trade payload has its own ask. That is a same-scan approximation, not a reconstructed NBBO.

The scan writes `artifacts/flow/top_alerts_<session>.json` (`sidecar.flow.top_alerts.v1`) with ticker, contract, direction, premium, flags, and score. `orders_submitted` is 0. That file is the input for an external X or news check. It is not an order.

No saved tape of the 2026-10-16 chain is in this tree. A fixture with the same 892 short-dated contracts (DTE 0–2, open interest 150, volume 2000) plus 40 ordinary 10-DTE contracts and one 10-DTE outlier scores 1 raw alert of 933 contracts. All 892 cleared a fixed vol/OI ratio of 3. None of them alert under this rule. The outlier is the only row in the top list. That document is `examples/flow_top_alerts_fixture.json`. `artifacts/` is gitignored, so a live scan writes the same shape there and does not commit it.

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

The scan also writes `artifacts/flow/top_alerts_<session>.json`. `--symbols SPY` limits one run. `--alerts-dir` changes the directory.
