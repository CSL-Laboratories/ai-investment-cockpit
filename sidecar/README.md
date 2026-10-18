# Quant sidecars

Standalone research modules for the AI Investment Cockpit. They read exported or public data, write schema-checked JSON, and do not place orders. Nothing in this tree is imported by `app/`. Integrating a module later is a separate, reviewed change.

The nine cockpit invariants still apply to that later change. In particular, an order still has to pass risk, mode, and approval before `OrderGateway`. `app/risk/` stays Decimal and stdlib-only. `app/quant/` still must not import `app/brokers/`. These packages do not weaken any of that.

## What is here

| Package | Role |
|---|---|
| `sidecar.evalstats` | PSR, deflated Sharpe, CSCV/PBO, minimum track-record length, stationary-bootstrap intervals, drawdown, append-only trial ledger |
| `sidecar.merger` | Point-in-time EDGAR filing join and an auditable logistic completion model |
| `sidecar.tca` | Implementation shortfall and an Almgren-style impact fit |
| `sidecar.llm_audit` | Judgment log, Brier / reliability / Spearman IC, look-ahead and training-cutoff filters |
| `sidecar.sizer` | Volatility target and fractional Kelly. Decimal only. Can only shrink a cap |
| `sidecar.harness` | Offline replay on committed daily bars, and a read-only Alpaca **paper** observer |

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

The client is hard-pinned to `https://paper-api.alpaca.markets` for account reads and `https://data.alpaca.markets` for IEX bars. `GET` only. `https://api.alpaca.markets` is rejected. No order route exists in this tree.

Credentials, first complete pair wins:

1. Keyring service `ai-investment-cockpit`, names `alpaca_key` and `alpaca_secret` (the same names as `app/core/secrets.py`).
2. Otherwise both environment variables `SIDECAR_ALPACA_KEY_ID` and `SIDECAR_ALPACA_SECRET`.

A partial pair is ignored. Values are not written into the report or the error text.

```bash
python -m sidecar.harness.paper --out artifacts/paper_report.json
```

Exit code 2 means the credentials are not available. The command above is the one to run once they are. This environment had neither keyring nor the env pair, so the live paper report was not produced here.

Alpaca paper fills are simulated by Alpaca. A TCA block in that report is a paper-venue calibration, not a live impact estimate. Decision price and arrival are daily proxies (previous close, session open), labeled as such.

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
- **Plug-in:** in `app/risk/engine.py`, the allowed notional becomes `min(existing max_allowed_notional, suggestion)`. The function cannot raise a cap. It does not belong on a path that calls a broker by itself.

## Not a performance claim

`examples/offline_report.json` is one simulated path of one predeclared rule. Quote it only with the report's caveats. Do not treat PSR, DSR, or PBO from that file as a frozen gate, and do not describe the path as evidence the cockpit's merger strategy has edge. The merger model was not fit on that run: there is no real point-in-time deal cohort in the fixture, and synthetic deals are refused unless the caller sets `allow_synthetic=True`.
