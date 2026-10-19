# Unusual options flow

Read-only scan of Alpaca option chains. It GETs snapshots, trades, contract open interest, and the underlying IEX price. It has no write method. `orders_submitted` on every document is 0.

## Feeds

`indicative` is the free default. `opra` is used only when `--feed opra` or `SIDECAR_OPTIONS_FEED=opra` is set, and only on the snapshot request. The historical trades endpoint rejects a `feed` parameter, so that call does not send one. Trades are requested in batches of 100 symbols with an explicit start and end. Start is the session open. End is that session's close when the close is already at least 16 minutes ago. Otherwise end is 16 minutes before the scan clock, and never later than the close. A scan in the first 16 minutes after the close therefore omits the tail of the session.

Alpaca answers a trades request that touches the latest 15 minutes with HTTP 403 and the text "OPRA agreement is not signed". That text means the request asked for real-time OPRA without Algo Trader Plus. There is no agreement to sign. The 16-minute end keeps the free historical feed on `tape=trades`. If that request still returns 403, the scan does not fail: `tape` is `snapshot_only`, the markdown says so, and sweeps are not detected because only each contract's latest snapshot trade is available. That one print is low-confidence. It does not set direction and it does not award the aggressive flag. Direction stays `unknown` unless at least two prints sit on the same side of the quote. A rejected snapshot feed is still an error and is not retried on the other feed.

Open interest comes from the paper host's contract reference (`GET /v2/options/contracts`). Alpaca's default expiration filter stops at the next weekend, so the scan sends the expiration bounds of the snapshot chain and follows every page token (`next_page_token` or `page_token`). Open interest is often a string in that payload; it is parsed. Interest with a missing date, or a date more than 5 calendar days before the session, is ignored.

Each underlying's snapshot pages, contract pages, and each 100-symbol trade batch stop after `SIDECAR_FLOW_MAX_PAGES` pages (default 400, also `--max-pages`). The default was raised from 40 after SPY's 2026-10-19 full-session tape passed 40 pages; 400 finished that tape. The cap is per request, not a budget shared by the watchlist. If a next-page token is still present at the cap, the scan logs a warning, keeps the rows already received, and continues with the other names. The scan JSON, the markdown, and `top_alerts_<session>.json` list those names in `tape_truncated` (trade prints) or `chain_truncated` (snapshots or open interest). A truncated tape is not treated as a finished tape. HTTP 429 still waits on `Retry-After`, or on an exponential backoff capped at 30 seconds, and retries up to three more times. The trade request still ends at least 16 minutes before the scan clock.

The session is the last **completed** Alpaca session. A session counts only after its close. Before the open, and during the day before the close, the previous session is used. Sunday 2026-10-18 21:50 Pacific is Monday 00:50 Eastern, which is before Monday's open, so the scan session is Friday 2026-10-16 rather than Monday 2026-10-19. A Saturday, Sunday, or holiday is the same rule. The document says when the clock and the session date differ.

The scan universe is `FLOW_UNIVERSE` in `config.py`: SPY, QQQ, IWM, and 30 single names (AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, AVGO, NFLX, JPM, BAC, XOM, UNH, LLY, COIN, PLTR, MSTR, SMCI, BA, DIS, INTC, MU, UBER, CRM, ORCL, QCOM, HD, COST, WMT). That 33-name list is the default for `python -m sidecar.flow scan` when `--symbols` is omitted and `SIDECAR_FLOW_WATCHLIST` is unset. The environment variable may name a comma-separated subset. It cannot add a symbol. Sentiment still uses the original twelve-name `WATCHLIST`. Neither list changes what the options runner is allowed to send. The published list stays the top 15 alerts, at most 3 per name.

Live GETs wait `SIDECAR_FLOW_MIN_INTERVAL` seconds (default 0.3, about 200 requests per minute) and retry HTTP 429 up to three more times. Stock snapshots are batched 50 symbols at a time. Option trades stay in batches of 100. Underlying chains are still requested one name at a time because that is how the snapshots route is shaped. Names are not fetched concurrently: extra in-flight calls would cross the same rate limit.

A full after-close scan of the 33 names is paced by that interval. One calendar call and one stock-snapshot batch cover the list. SPY's 2026-10-19 chain was 12,730 contracts, about 13 snapshot pages and 13 contract pages at 1,000 per page, and its trade tape needed a page cap above 40. The other names are smaller. A planning figure is a few hundred to a bit over a thousand GETs, about 5–15 minutes when responses are a few hundred milliseconds. One trade batch that actually uses the 400-page cap adds about two minutes of pacing before response time. This is an estimate from those page sizes and the 0.3 second interval, not a timed live run.

Credentials are the sidecar paper pair only (`SIDECAR_PAPER_ALPACA_KEY_ID` / `SIDECAR_PAPER_ALPACA_SECRET`, or the sidecar keyring service). A missing pair stops the scan. The offline event study does not need them.

## Score

The first live SPY scan (session 2026-10-16, indicative, tape of trades) flagged 892 of 12,630 contracts at a score of 3 or more, mostly 0–3 DTE where volume over open interest is ordinary. The gates below were set after that scan. No return in this tree has been computed from them.

| Flag | Rule | Points |
|---|---|---|
| Volume / open interest | DTE greater than 2, open interest at least 100, volume at least 100, and the ratio's percentile inside that underlying and DTE bucket is at least 0.95. The bucket needs 20 eligible contracts. | 1 at the 95th, 2 at the 97th, 3 at the 99th |
| Volume z-score | volume at least 100 and z at least 2 against the previous 20 sessions of that contract | `min(3, z)` |
| Premium | the largest print's percentile inside that underlying and DTE bucket is at least 0.95, among at least 20 contracts that printed | 1 |
| Aggressive buy | a print at or above the ask with price × size × 100 at least $50,000. On a snapshot-only tape this needs two prints on that side of the quote; one latest trade does not count | 1 |
| Sweep | at least two of those prints inside 1 second, total premium at least $50,000 | 1 |
| Short-dated OTM | 3–14 days, not 0–2, strike more than 2% through spot (delta band only if spot is missing) | 1 |
| Skew shift | that same contract, and put IV minus call IV rose by at least 0.02 versus the previous stored session | 1 |

DTE buckets are 0–2, 3–14, 15–45, 46–180, and 181+. A percentile is the share of the bucket strictly below the contract, so a tie does not put every copy at the top. A bucket smaller than 20 awards no vol/OI and no premium point.

Volume z stays empty until that contract has 20 prior sessions, or when those sessions have zero variance. The field is `volume_z_status: unavailable`. The alert bar is then 4 instead of 3. A vol/OI percentile of 3, or a large aggressive sweep, is not an alert by itself until the baseline exists or another point is present. Once z is a number, the bar returns to 3 even if z itself is small.

An alert is a score at or above that bar. The published list is the top 15 alerts, at most 3 per underlying. Contracts with absolute delta above 0.85 rank after every other alert. Those are often stock-replacement or financing trades, not directional bets, and they are left out of the cluster totals. A direction of `unknown` is left out of those totals too. Everyone else is ranked by score, then premium, then contract. Clusters group the same underlying, expiration, and direction and sum print premium. On a trade tape, direction is bullish for a call bought or a put sold, and bearish for a put bought or a call sold. If the largest print is not at the ask or the bid, the direction follows the call or the put and `aggressor` is `unknown`. On a snapshot-only tape, one print sets `aggressor` to `low_confidence` and `direction` to `unknown`. Two prints on the same side of the quote can still set direction and the aggressive flag. The ask on a print is the scan's quote unless the trade payload has its own ask. That is a same-scan approximation, not a reconstructed NBBO.

The scan writes `artifacts/flow/top_alerts_<session>.json` (`sidecar.flow.top_alerts.v1`) with ticker, contract, direction, aggressor, aggressor confidence, deep ITM, premium, flags, and score. `orders_submitted` is 0. That file is the input for an external X or news check. It is not an order.

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
python -m sidecar.flow scan --out artifacts/flow_scan.json --markdown artifacts/flow_scan.md --history artifacts/flow_history.jsonl --alerts-dir artifacts/flow
python -m sidecar.flow study --out examples/flow_event_study.json --ledger examples/flow_event_trials.jsonl
```

That scan command is the full 33-name watchlist. The box wrapper `sidecar_run.sh flow` should run it with no `--symbols` and with `SIDECAR_FLOW_WATCHLIST` unset. `--symbols SPY` limits one run; it is not the scheduled default. `--max-pages` and `SIDECAR_FLOW_MAX_PAGES` override the page cap of 400. The scan also writes `artifacts/flow/top_alerts_<session>.json`. `--alerts-dir` changes the directory.
