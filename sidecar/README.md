# Quant sidecars

Standalone research modules for the AI Investment Cockpit. Nothing in this tree is imported by `app/`. Integrating a module later is a separate, reviewed change.

The offline packages and the read-only paper observer do not place orders. `sidecar.harness.runner` is a separate experimental path: it can post to its own Alpaca **paper** account, and only that account. It is not the cockpit's single order path. An order inside the cockpit still has to pass risk, mode, and approval before the gateway. `app/risk/` stays Decimal and stdlib-only. `app/quant/` still must not import `app/brokers/`. This tree does not import either. The experimental path has to be reconciled at integration time; it does not replace the cockpit path and it does not weaken it.

`sidecar.rl` is simulation only. It never places an order.

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

Credentials for the observer only, first complete pair wins:

1. Keyring service `ai-investment-cockpit`, names `alpaca_key` and `alpaca_secret` (the same names as the cockpit secret helper).
2. Otherwise both environment variables `SIDECAR_ALPACA_KEY_ID` and `SIDECAR_ALPACA_SECRET`.

A partial pair is ignored. Values are not written into the report or the error text.

```bash
python -m sidecar.harness.paper --out artifacts/paper_report.json
```

Exit code 2 means the credentials are not available. Alpaca paper fills are simulated by Alpaca. A TCA block in that report is a paper-venue calibration, not a live impact estimate. Decision price and arrival are daily proxies (previous close, session open), labeled as such.

## Experimental paper runner

This is not the cockpit order path. It posts, when `--execute` is set, only to a **different** Alpaca paper account. It never reads `alpaca_key`, `alpaca_secret`, `SIDECAR_ALPACA_KEY_ID`, or `SIDECAR_ALPACA_SECRET`.

Dry-run (no credentials, no broker writes):

```bash
python -m sidecar.harness.runner --when open
python -m sidecar.harness.runner --when close
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

- **Reads:** the local daily bar file, and, only in `--execute`, the sidecar Alpaca paper account (equity, positions, portfolio history, closed orders). Credentials are the sidecar keyring service or `SIDECAR_PAPER_*` only.
- **Emits:** `sidecar.daily_report.v1` with `experimental_order_path: true`, plus an append-only order log and trial-ledger rows for every config considered, including the simulation-only market maker.
- **Plug-in:** none until a reviewed integration. This client must not be called from the cockpit gateway, and the cockpit gateway must not grow a fallback to these keys. The two paths stay separate until that review deletes one of them on purpose.

### rl

- **Reads:** nothing outside this tree except the committed daily-bar fixture for the labeled non-L1 replay.
- **Emits:** `sidecar.rl_mm_eval.v1` with `live_trading: false` and `orders_submitted: 0`. PSR, DSR, and PBO are attached through `evalstats`. The trial ledger records the hyperparameter rows.
- **Plug-in:** do not connect it to a broker. The vendored Avellaneda-Stoikov function is for the simulator's expert and for comparison. It is not a second quoter on the cockpit order path.

## Not a performance claim

`examples/offline_report.json` is one simulated path of one predeclared rule. Quote it only with the report's caveats. Do not treat PSR, DSR, or PBO from that file as a frozen gate, and do not describe the path as evidence the cockpit's merger strategy has edge. The merger model was not fit on that run: there is no real point-in-time deal cohort in the fixture, and synthetic deals are refused unless the caller sets `allow_synthetic=True`.
