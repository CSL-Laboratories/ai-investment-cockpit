# Quant sidecars

Standalone research modules for the AI Investment Cockpit. Nothing in this tree is imported by `app/`. Integrating a module later is a separate, reviewed change.

The offline packages and the read-only paper observer do not place orders. `sidecar.harness.runner` is a separate experimental path: it can post to its own Alpaca **paper** account, and only that account. `sidecar.options.runner` is a second experimental paper path, for defined-risk option spreads only, on that same sidecar paper account. Neither is the cockpit's single order path. An order inside the cockpit still has to pass risk, mode, and approval before the gateway. `app/risk/` stays Decimal and stdlib-only. `app/quant/` still must not import `app/brokers/`. This tree does not import either. The experimental paths have to be reconciled at integration time; they do not replace the cockpit path and they do not weaken it.

`sidecar.rl` and `sidecar.intraday_mm` are simulation only. They never place an order. `sidecar.flow` and `sidecar.sentiment` only read market data and local text. They write alerts and snapshots. They do not place orders.

## What is here

| Package | Role |
|---|---|
| `sidecar.evalstats` | PSR, deflated Sharpe, CSCV/PBO, minimum track-record length, stationary-bootstrap intervals, drawdown, append-only trial ledger |
| `sidecar.merger` | Point-in-time EDGAR filing join and an auditable logistic completion model |
| `sidecar.tca` | Implementation shortfall and an Almgren-style impact fit |
| `sidecar.llm_audit` | Judgment log, Brier / reliability / Spearman IC, look-ahead and training-cutoff filters |
| `sidecar.sizer` | Volatility target and fractional Kelly. Decimal only. Can only shrink a cap |
| `sidecar.harness` | Offline replay, a read-only Alpaca paper observer, and a separate experimental paper runner |
| `sidecar.rl` | Simulated GRU market maker. Behavioral cloning from a vendored Avellaneda-Stoikov expert, prioritized fictitious self-play, potential-based shaping. Never live |
| `sidecar.kronos_signal` | Walk-forward Kronos OHLCV forecasts after the June 2024 pretraining cutoff. Optional paper-runner strategy. Weights load only in the offline eval |
| `sidecar.options` | Defined-risk put credit spreads and iron condors. Synthetic Black-Scholes book, plus a paper runner that can post a multi-leg limit only to the sidecar paper account |
| `sidecar.intraday_mm` | Avellaneda-Stoikov and the GRU clone on a quote/trade tape. Queue and latency fills, markouts, parquet replay. Never places an order |
| `sidecar.flow` | Unusual options activity from Alpaca indicative or OPRA data. Scored alerts, a feature series, and a 1-session / 5-session event study. Read-only |
| `sidecar.sentiment` | r/wallstreetbets and a local X inbox. Mention velocity, a fixed lexicon, unusual-buzz snapshots, a join to flow alerts, and the same event study. Read-only |

P6–P8 from the research note are not separate packages. Down-market beta is reported by `evalstats` when a benchmark series is passed in. A data-quality gate would have to call the orchestrator breakers, and a latency model would have to edit `app/quant/`. Neither belongs in this tree.

## Run the checks

From this directory, with Python 3.11 or 3.12:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
```

`pytest` and `ruff` here do not replace the cockpit's DEC-09 matrix. They are the local check for this tree only. Hosted workflows are intentionally untouched.

## Offline replay

```bash
python -m sidecar.harness.backtest --out examples/offline_report.json
```

The checked-in `examples/offline_report.json` is the output of that command on `fixtures/etf_daily.csv` (Yahoo Finance daily bars for SPY, QQQ, and IWM, downloaded 2026-10-18; see `fixtures/SOURCE.json`). The primary rule was fixed before the run: 20-day trailing mean as a naive edge, 25% Kelly, 1% daily vol target, rebalance every 5 sessions, fill the next adjusted open, 1 bp one-way cost. Lookbacks 10, 40, and 60 are trials in the ledger. They are not used to pick the reported path.

That run is a simulation. It is not a broker fill, not a survivorship-free stock universe, and not a qualify decision. Read the `caveats` array in the report before quoting a number from it. `orders_submitted` is 0.

## Paper observer

The read-only client is hard-pinned to `https://paper-api.alpaca.markets` for account reads and `https://data.alpaca.markets` for IEX bars. `GET` only. `https://api.alpaca.markets` is rejected.

Credentials for the observer, first complete pair wins:

1. Keyring service `ai-investment-cockpit`, names `alpaca_key` and `alpaca_secret` (the same names as the cockpit secret helper).
2. Otherwise both environment variables `SIDECAR_ALPACA_KEY_ID` and `SIDECAR_ALPACA_SECRET`.
3. Otherwise the sidecar paper pair: keyring service `ai-investment-cockpit-sidecar`, names `paper_key` and `paper_secret`, or both `SIDECAR_PAPER_ALPACA_KEY_ID` and `SIDECAR_PAPER_ALPACA_SECRET`.

A partial pair is ignored. Values are not written into the report or the error text.

```bash
python -m sidecar.harness.paper --out artifacts/paper_report.json
```

Exit code 2 means the credentials are not available. Alpaca paper fills are simulated by Alpaca. A TCA block in that report is a paper-venue calibration, not a live impact estimate. Decision price and arrival are daily proxies (previous close, session open), labeled as such.

## Experimental paper runner

This is not the cockpit order path. It posts, when `--execute` is set, only to a **different** Alpaca paper account. It never reads `alpaca_key`, `alpaca_secret`, `SIDECAR_ALPACA_KEY_ID`, or `SIDECAR_ALPACA_SECRET`.

Dry-run does not construct a trade client. It refreshes adjusted allowlist bars when the sidecar paper credentials are present, and otherwise plans from the local file:

```bash
python -m sidecar.harness.runner --when open
python -m sidecar.harness.runner --when close
python -m sidecar.harness.runner --when close --no-refresh
```

Execute, after provisioning the sidecar pair:

```bash
keyring set ai-investment-cockpit-sidecar paper_key
keyring set ai-investment-cockpit-sidecar paper_secret
# or export both SIDECAR_PAPER_ALPACA_KEY_ID and SIDECAR_PAPER_ALPACA_SECRET
python -m sidecar.harness.runner --when close --execute --out artifacts/paper_execute_report.json
```

The trading URL is fixed at `https://paper-api.alpaca.markets`. Any other trading base URL is rejected. Before an order, `GET /v2/account` on that host must return an ACTIVE account; an explicit non-paper mode field or a blocked account is refused. Limits (max $2,000 notional, $20,000 gross, 3 positions, $500 daily loss, allowlist SPY/QQQ/IWM) can only shrink or refuse the sizer. Client order ids are stable per session and intent. `artifacts/KILL` (or `SIDECAR_KILL_FILE`) blocks new orders and, on execute, cancels open sidecar ids. The default strategy is the predeclared inverse-vol rule. `spy_only` is the other live choice. The Avellaneda-Stoikov config is logged on the trial ledger and is not traded. See `sidecar/harness/README.md` for the cron lines and the account-attestation caveat.

## Simulated market maker

```bash
python -m sidecar.rl.train --profile smoke
python -m sidecar.rl.train --profile report --out examples/rl_mm_eval.json --ledger examples/rl_mm_eval.ledger.jsonl
```

`examples/rl_mm_eval.json` is the report-profile run committed with this tree. It is a short CPU simulation, not a live book and not a claim that the learner beat the Avellaneda-Stoikov baseline. Read that file's `caveats` before quoting it. The comparison block is the measured difference in mean session return.

## Kronos forecasts

```bash
python -m pip install -e ".[kronos]"
python -m sidecar.kronos_signal.evaluate --profile smoke
python -m sidecar.kronos_signal.evaluate --profile report --out examples/kronos_eval.json --ledger examples/kronos_eval.ledger.jsonl
python -m sidecar.harness.runner --strategy kronos --when close
```

The default runner strategy is still `inverse_vol`. `--strategy kronos` reads `examples/kronos_eval.json` and does not load the model. It refuses to trade when that report is missing. `examples/kronos_eval.json` is the report-profile run committed with this tree. Read its `caveats` before quoting a number. `live_eligible` means the July 2024 protocol finished. It does not mean the forecast beat zero-return, trailing mean, or buy-and-hold.

On that committed run the primary config (mini, lookback 64, temperature 1.0, 36 post-cutoff decisions) ended at 91945.01467356542211066211403 from 100000. PSR 0.270841980117, DSR 0.0192597888218, max drawdown 0.121297714523. Pooled IC 0.16113576, directional hit rate 0.52777778, Brier 0.27854938 against a constant-0.5 Brier of 0.25. Trailing mean ended at 103025.6929640805959608712531 and buy-and-hold at 101418.4563917799362885658572. The flat book stayed at 100000. `beats_baselines` is false. PBO across the eight configs is 0.333333333333 on the forecast-weighted series, not on the equity curve. See `sidecar/kronos_signal/README.md`.

## Defined-risk options

```bash
python -m sidecar.options.evaluate --out examples/options_eval.json --ledger examples/options_eval.ledger.jsonl
python -m sidecar.options.runner
python -m sidecar.options.runner --execute
```

`examples/options_eval.json` is the synthetic book on `fixtures/etf_daily.csv`. There is no historical OPRA chain in this tree. Premiums are Black-Scholes with zero rates, and the "implied vol" is the trailing 60-session realized vol of the same underlying. The signal is that proxy's rank versus the prior 252 sessions, plus a 60-day minus 10-day realized spread above 0.02. The primary was fixed first: put credit spread, absolute delta 0.20, width $5, about 30 calendar days, close at 7 DTE, take profit at half the entry mid, stop at twice the entry mid. Delta 0.15, IV-rank floor 0.40, and the iron condor are trials. They did not replace the primary.

The full spread on each leg is max($0.05, 8% of the model mid). Sells hit the bid and buys hit the ask. The signal uses one close and the fill is the next session's close.

That run ended at **99039.00** from 100000. `beats_cash` is false. 82 opens, all closed (69 take-profit, 12 stop, 1 at the DTE limit). 943 daily returns. Mean return -1.01773291136e-05. Per-period Sharpe -0.0287261485653. Annualized Sharpe -0.456013471371. PSR 0.168970384225. DSR 0.0221962979604. Max drawdown 0.0140357527633. PBO across the four configs 0.584770784771. Median implementation shortfall versus the model mid 402.0100502512562814070351759 bps (mean 502.0144135083238239900633058). That shortfall is the 8% spread, not an OPRA print. The other rows also finished below 100000 (99483.00, 98934.00, and 99235.00). `orders_submitted` is 0. This is not evidence of a volatility-risk premium.

The paper runner uses the same sidecar credentials as the equity runner (`SIDECAR_PAPER_ALPACA_KEY_ID` and `SIDECAR_PAPER_ALPACA_SECRET`, or keyring service `ai-investment-cockpit-sidecar`). It does not read the cockpit pair. Dry-run is the default and does not post. `--execute` posts a multi-leg **limit** to `https://paper-api.alpaca.markets` only, after `GET /v2/account` on that host. A credit open uses a negative limit price. A close uses a positive debit. Market orders are refused. The live host `https://api.alpaca.markets` is refused.

Hard limits, checked before any post: defined-risk only (every short is covered by a further-OTM long, same expiry and right), max loss $1,000 per spread, $5,000 across open spreads, at most 5 open spreads, entry DTE 7–45, allowlist SPY/QQQ/IWM. `artifacts/KILL` blocks new orders and, on execute, cancels resting sidecar ids. It does not flatten positions. A missing greek, a quote older than 15 minutes, a crossed or very wide market, or an options approval level that is missing or below 3 refuses the open. IV rank needs 252 prior rows in `artifacts/iv_history.jsonl`. Until that file has them, the runner opens nothing. An option position the state file does not explain also blocks new risk.

Regular session is 09:30–16:00 America/New_York, weekdays. Outside that window the command exits 3 unless `--force-window` is set. Missing sidecar credentials exit 2. Snapshots use `feed=indicative`, not OPRA. This environment had no sidecar paper keys, so the live chain was not called. Execute was tested with a mock transport only.

## Intraday market maker

```bash
python -m sidecar.intraday_mm.session --synthetic --minutes 30 --out examples/intraday_mm_eval.json --ledger examples/intraday_mm_eval.ledger.jsonl
python -m sidecar.intraday_mm.session --replay path/to/session.parquet
python -m sidecar.intraday_mm.session --minutes 30
```

The last command is the only one that opens a socket, and only `wss://stream.data.alpaca.markets/v2/iex`. SIP, the live trading host, and the paper trading host are refused. It subscribes to quotes and trades. It does not send an order. It records parquet under `artifacts/` and then runs the same simulator. It requires the sidecar paper credentials for the data socket, and it runs only in the regular session unless `--force-window` is set (exit 3 outside the window, exit 2 if the credentials are missing). `--synthetic` and `--replay` do not need credentials and are not windowed.

`examples/intraday_mm_eval.json` is the synthetic tape, seed 0, 30 minutes, symbol SPY. It is not a live IEX session. The touch is six cents wide so the simulator-unit Avellaneda-Stoikov quote (about two cents) can improve it. On a one-cent IEX book those same parameters can rest behind the touch. Primary latency is 75 milliseconds. The GRU is cloned on the first 60 percent of the tape toward the nearest discrete AS action, then both books are scored on the last 40 percent, starting flat. That held-out slice is 12 per-minute returns.

| Item | Avellaneda-Stoikov | GRU clone |
|---|---|---|
| Fills | 791 | 802 |
| Ending mark-to-market | -12.5618445107 | -14.5778638398 |
| Ending inventory (cap 10) | 7 | 8 |
| Mean per-minute return | -0.000250506879461 | -0.000290762666382 |
| PSR | 1.28933853821e-06 | 0.0464729051239 |
| DSR | 4.35958796841e-09 | 0.00314581911532 |
| Markout 1s (n) | -0.00924256756822 (789) | -0.0104860109837 (800) |
| Markout 5s (n) | -0.00932436115628 (784) | -0.00994173072756 (795) |
| Markout 30s (n) | -0.0151083535503 (751) | -0.021656802306 (762) |

Learner minus AS mean per-minute return: **-4.02557869207e-05**. `beats_as` is false. Behavioral-cloning loss moved from 2.16699257669 to 0.0986595777378. Markouts are negative at every horizon, which is adverse selection. Fills in the last 30 seconds have no 30-second markout; those values are omitted, not set to zero. The 50 millisecond and 100 millisecond AS trials produced the same fills as 75 milliseconds, because trades on this tape are several hundred milliseconds apart. PBO on those three identical series is 1, on 3 splits of 12 returns. That is not a separate latency result. Annualized Sharpe on 12 one-minute returns is only a display scaling. `orders_submitted` is 0. `threshold_status` is `not_frozen`.

`examples/intraday_mm_queue_compare.json` reruns that tape with Rigtorp's queue estimate, and a second synthetic tape whose displayed size changes. The legacy column on the constant-size tape matches the table above. The new rule fills much less often. It does not establish an edge. The module README has both tables. There is no recorded IEX session in this tree.

## Flow and sentiment event studies

`examples/flow_event_study.json` and `examples/sentiment_event_study.json` use the local ETF bars. Neither file is a historical options tape or a social-media archive, so neither answers whether those signals predict returns. The flow events are every 21st SPY session starting at index 252. The sentiment events are a canned post rule on that same calendar (eight bullish `$SPY` posts every 30th session). Both studies score the adjusted close 1 and 5 sessions later and count those horizons as two trials. Bonferroni's bar is `1 - PSR < 0.025`. Five-session returns overlap.

| Study | Horizon | Events | Mean return | Hit rate | PSR | DSR | Clears Bonferroni |
|---|---|---:|---:|---:|---:|---:|---|
| Flow calendar | 1 (primary) | 48 | -0.00063130791115 | 0.479166666667 | 0.34130343899 | 0.176531685547 | no |
| Flow calendar | 5 | 48 | 0.00447979580265 | 0.604166666667 | 0.953539256152 | 0.877062872537 | no |
| Sentiment fixture | 1 (primary) | 33 | 0.000704710566369 | 0.484848484848 | 0.728508831929 | 0.535281809613 | no |
| Sentiment fixture | 5 | 33 | 0.00580647129446 | 0.666666666667 | 0.950534709933 | 0.870826150548 | no |

The 5-session PSR on the flow calendar is what a date rule can print. It is not evidence of unusual options activity. `threshold_status` is `not_frozen`. `orders_submitted` is 0. `examples/flow_scan_sample.md` is a hand-built chain for the alert layout.

## Retrofit

Do this only after the cockpit's own fixes, as a reviewed change. These packages stay the computation. The cockpit change is the thin adapter.

### evalstats

- **Reads:** a daily equity or return series. `app/brokers/paper_performance.py` currently keeps only the current session (`equity_drawdown_today`, consecutive losses, realized P&L). A later export of signed daily equity snapshots, one row per New York session, is the input. Shape: `[{"date": "YYYY-MM-DD", "equity": "decimal-string"}]`.
- **Emits:** `sidecar.eval_report.v1`. `threshold_status` is always `not_frozen`.
- **Plug-in:** `app/feasibility/evaluate.py` may *attach* the report as evidence. It must not replace `classify()`, and it must not grow a numeric pass/fail until a new freeze file is written **before** the measurement population exists. `thresholds.proposed.json` is an illustration, not that freeze. `STATISTICAL_NUMERIC_THRESHOLDS` in the current evaluator stays as it is.
- **Trial count:** `TrialLedger.distinct_config_count()` is the `n_trials` argument. Deleting the ledger to shrink that count is a protocol violation the code cannot see. DSR uses distinct config hashes, so re-running the same config does not inflate N.

### merger

- **Reads:** operator deal cards plus EDGAR submissions JSON (`https://data.sec.gov/submissions/CIK##########.json`). A filing counts only when it was public at `observed_at`. Acceptance time is used when present; a date-only filing counts on the next UTC date. The client does not scrape consideration out of HTML.
- **Emits:** `sidecar.merger_calibration.v1`, method `irls_l2_logistic_v1`. `yield_claim` is false. Expected return is `p * spread - (1 - p) * break_loss_magnitude`, with break loss the training p10 of broken-deal returns.
- **Plug-in:** this file is **not** `merger_calibration.v2`. `app/strategies/merger_edge.py` accepts only `wilson95_heldout_worst_case_prices_v2`, with the cohort, held-out field precision, and selection-manifest contract. `wilson_lower_bound()` reproduces that lower-bound arithmetic so a later cohort export can be compared. Loading this artifact as `MergerCalibrationEvidence` would be a contract break. `app/risk/` should eventually see only a Decimal edge and a Decimal downside, computed outside `risk/`.

### tca

- **Reads:** a fill export, or, in the paper observer, closed orders from the paper API joined to IEX daily bars. Fields: `side`, `qty`, `decision_price`, `arrival_mid`, `fill_price`, optional `fee`, `predicted_cost_bps`, `sigma`, `adv`.
- **Emits:** a shortfall summary and `sidecar.cost_params.v1`. Recommended coefficients are omitted when the sample is short, the design is singular, or an impact coefficient has the wrong sign.
- **Plug-in:** keep the module outside `app/quant/` so the quant package still does not import brokers. `app/quant/costs.py` keeps its fee defaults. A later change may *optionally* load `temporary_eta`, `permanent_gamma`, and `epsilon` as an impact add-on and report a range. The fee schedule is not replaced. Paper and simulated fills must stay labeled; they are not live-venue impact.

### llm audit

- **Reads:** a JSONL tap of model judgments. Each row needs `judgment_id`, `route`, `as_of`, `symbol`, `horizon`, `probability`, `model_id`. Outcomes are filled in later.
- **Emits:** `sidecar.route_scorecard.v1`. A row is scored only when `outcome_at` is strictly after `as_of`. If `model_training_cutoff` is set, `as_of` must be strictly after it. The null is a constant probability of 0.5. `beats_null` is a description, not a promotion.
- **Plug-in:** a passive writer beside `app/workflows/` and `app/agent_runner/` results, into this log, not into the order path. `anonymize_text()` is the entity mask to apply before a historical prompt is shown to a model. It is not a secret: anyone with the ticker list can invert it. Route qualification stays a human decision against a freeze that does not exist yet.

### sizer

- **Reads:** Decimal `edge`, `vol`, `target_vol`, `kelly_frac`, `caps`, `equity`. Floats are rejected. `edge` and `vol` must already share a horizon.
- **Emits:** a cent-quantized notional, rounded down, that is `<= caps`. Non-positive edge returns 0. `ewma_volatility` is the RiskMetrics recursion seeded with the first squared return.
- **Plug-in:** in the cockpit risk engine, the allowed notional becomes `min(existing max_allowed_notional, suggestion)`. The function cannot raise a cap. It does not belong on a path that calls a broker by itself. The experimental runner calls the same function and then applies a second, tighter paper limit. That second limit is not a cockpit risk verdict.

### experimental paper runner

- **Reads:** adjusted IEX daily bars for the allowlist from Alpaca market data (or the local file when `--no-refresh` is set or a dry-run refresh cannot run), and, only in `--execute`, the sidecar Alpaca paper account (equity, positions, portfolio history, closed orders). Credentials are the sidecar keyring service or `SIDECAR_PAPER_*` only. A failed refresh in `--execute` submits no orders.
- **Emits:** `sidecar.daily_report.v1` with `experimental_order_path: true`, plus an append-only order log and trial-ledger rows for every config considered, including the simulation-only market maker.
- **Plug-in:** none until a reviewed integration. This client must not be called from the cockpit gateway, and the cockpit gateway must not grow a fallback to these keys. The two paths stay separate until that review deletes one of them on purpose.

### rl

- **Reads:** nothing outside this tree except the committed daily-bar fixture for the labeled non-L1 replay.
- **Emits:** `sidecar.rl_mm_eval.v1` with `live_trading: false` and `orders_submitted: 0`. PSR, DSR, and PBO are attached through `evalstats`. The trial ledger records the hyperparameter rows.
- **Plug-in:** do not connect it to a broker. The vendored Avellaneda-Stoikov function is for the simulator's expert and for comparison. It is not a second quoter on the cockpit order path.

### kronos

- **Reads:** the local daily bar file already used by the harness, and the public Hugging Face checkpoints named in `sidecar/kronos_signal/README.md`. The paper runner reads only the finished JSON report.
- **Emits:** `sidecar.kronos_eval.v1` with `orders_submitted: 0`. Scored decisions start on 2024-07-01. The edge and dispersion go to the existing sizer.
- **Plug-in:** do not load the model on the cockpit order path. The experimental runner may size from the report only after that file exists. That runner is still not the cockpit order path.

### options

- **Reads:** an indicative option chain and greeks from Alpaca market data, adjusted IEX daily bars for a 10-day realized vol, and the local IV-rank history. The offline book reads only `fixtures/etf_daily.csv`.
- **Emits:** `sidecar.options_eval.v1` with `orders_submitted: 0`, and, from the runner, `sidecar.options_runner.v1`. The runner can post a defined-risk multi-leg limit to the sidecar paper account. That post is not the cockpit order path.
- **Plug-in:** do not point this client at the cockpit gateway or the cockpit keys. Do not treat the synthetic Black-Scholes book as an OPRA backtest. The runner's IV rank can use a tagged CBOE proxy backfill (VIX, VXN, RVX) and a minimum shorter than 252. The report names that gap. The proxy is not the chain's ATM implied vol.

### intraday market maker

- **Reads:** a parquet quote/trade tape, or the free IEX websocket. No broker write.
- **Emits:** `sidecar.intraday_mm_eval.v1` with `live_trading: false` and `orders_submitted: 0`.
- **Plug-in:** do not connect it to a broker. The queue and latency model is a research fill, not an exchange simulator certification.

### flow

- **Reads:** Alpaca option snapshots and trades (`indicative` unless `opra` is requested by name), paper-host contract open interest, and the underlying IEX price. The event study reads `fixtures/etf_daily.csv`.
- **Emits:** `sidecar.flow.scan.v1`, `sidecar.flow.top_alerts.v1` (top 15, at most 3 per name), and `sidecar.flow.event_study.v1`, all with `orders_submitted: 0`. The live scan defaults to the 33-name universe. A name whose trade tape hits the page cap is marked truncated and the scan continues. The feature rows are there for a later options reader. This package does not call the options runner. Vol/OI and premium are percentiles inside an underlying and DTE bucket. Contracts with 2 DTE or less do not get those two flags. Until 20 volume sessions exist, z is unavailable and the alert bar is 4.
- **Plug-in:** do not let a flow score submit an order. OPRA history was not downloaded, so the committed event study is a calendar null.

### sentiment

- **Reads:** Reddit's public listing when the three `SIDECAR_REDDIT_*` variables are set, and JSONL files in `artifacts/x_inbox/`. The event study reads the local bar file and the snapshots it just wrote.
- **Emits:** append-only snapshot JSONL and `sidecar.sentiment.event_study.v1` with `orders_submitted: 0`. Posts after 16:00 America/New_York roll to the next calendar date.
- **Plug-in:** do not put the buzz score on an order path. The committed posts are a fixture. Reddit was not called for that file.

## Not a performance claim

`examples/offline_report.json` is one simulated path of one predeclared rule. Quote it only with the report's caveats. Do not treat PSR, DSR, or PBO from that file as a frozen gate, and do not describe the path as evidence the cockpit's merger strategy has edge. The merger model was not fit on that run: there is no real point-in-time deal cohort in the fixture, and synthetic deals are refused unless the caller sets `allow_synthetic=True`.

`examples/options_eval.json` lost money against cash on a synthetic option book. `examples/intraday_mm_eval.json` did not beat Avellaneda-Stoikov, and both books lost on the synthetic tape. Neither file is a live fill. `examples/flow_event_study.json` and `examples/sentiment_event_study.json` are calendar checks. They do not measure unusual options activity or a social archive, and neither primary horizon clears its Bonferroni bar.
