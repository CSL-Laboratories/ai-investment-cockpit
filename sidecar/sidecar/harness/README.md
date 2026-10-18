# harness

Two entry points. Neither submits an order.

## Offline

```bash
python -m sidecar.harness.backtest --bars fixtures/etf_daily.csv --out examples/offline_report.json
```

`sidecar.inverse_vol_fractional_kelly.v1` sizes each name with `suggest_notional`, using the trailing mean return as a naive edge. The primary lookback is 20. Lookbacks 10, 40, and 60 are appended to the trial ledger and used only for PBO and the deflated-Sharpe trial count. The signal uses adjusted closes through day t. The fill is the next session's adjusted open. Adjusted open is `open * adj_close / close` for that session, so the bar's own adjustment factor scales open and close together.

Costs are a flat 1 bp of traded notional. Targets are scaled so cash after costs is non-negative. If that cannot be done, the run stops.

The report's benchmark block, when SPY is in the file, is the same-day SPY move. It is a realized beta, not an input to the signal.

## Paper

```bash
python -m sidecar.harness.paper --out artifacts/paper_report.json
```

Reads `GET /v2/account`, `GET /v2/account/portfolio/history`, `GET /v2/orders?status=closed`, and IEX daily bars. The trading host constant is `https://paper-api.alpaca.markets`. Any other trading host, including `https://api.alpaca.markets`, raises. The method must be `GET`.

Credentials: keyring service `ai-investment-cockpit` / `alpaca_key` + `alpaca_secret`, or both `SIDECAR_ALPACA_KEY_ID` and `SIDECAR_ALPACA_SECRET`. Exit code 2 if neither pair is present.

Account ids are not copied into the report. Decision and arrival prices on paper fills are the previous daily close and that session's open, and the venue label is `alpaca_paper`.

## Report

Both modes emit `sidecar.daily_report.v1` with `orders_submitted: 0` and `broker_writes: false`. The schema rejects any other value.
