# harness

Three entry points. The offline backtest and the paper observer do not submit orders. The daily runner is a separate experimental paper path. It is not the cockpit's single order path (risk, then mode, then approval, then the gateway). Reconcile the two at integration time. Do not point the runner at the cockpit account.

## Offline

```bash
python -m sidecar.harness.backtest --bars fixtures/etf_daily.csv --out examples/offline_report.json
```

`sidecar.inverse_vol_fractional_kelly.v1` sizes each name with `suggest_notional`, using the trailing mean return as a naive edge. The primary lookback is 20. Lookbacks 10, 40, and 60 are appended to the trial ledger and used only for PBO and the deflated-Sharpe trial count. The signal uses adjusted closes through day t. The fill is the next session's adjusted open. Adjusted open is `open * adj_close / close` for that session, so the bar's own adjustment factor scales open and close together.

Costs are a flat 1 bp of traded notional. Targets are scaled so cash after costs is non-negative. If that cannot be done, the run stops.

The report's benchmark block, when SPY is in the file, is the same-day SPY move. It is a realized beta, not an input to the signal.

## Read-only paper observer

```bash
python -m sidecar.harness.paper --out artifacts/paper_report.json
```

Reads `GET /v2/account`, `GET /v2/account/portfolio/history`, `GET /v2/orders?status=closed`, and IEX daily bars. The trading host constant is `https://paper-api.alpaca.markets`. Any other trading host, including `https://api.alpaca.markets`, raises. The method must be `GET`.

Credentials, first complete pair wins:

1. Keyring service `ai-investment-cockpit` / `alpaca_key` + `alpaca_secret`, or both `SIDECAR_ALPACA_KEY_ID` and `SIDECAR_ALPACA_SECRET`.
2. Otherwise the sidecar paper pair: keyring service `ai-investment-cockpit-sidecar` / `paper_key` + `paper_secret`, or both `SIDECAR_PAPER_ALPACA_KEY_ID` and `SIDECAR_PAPER_ALPACA_SECRET`.

Exit code 2 if neither pair is present. One sidecar paper pair is enough for this observer and for the runner below. The runner still does not accept the cockpit names.

Account ids are not copied into the report. Decision and arrival prices on paper fills are the previous daily close and that session's open, and the venue label is `alpaca_paper`.

## Experimental paper runner

Dry-run is the default. It sizes the book with the same sizer and writes the plan and an append-only order log. It does not construct a trade client. By default it first refreshes adjusted IEX daily bars for SPY, QQQ, and IWM from `https://data.alpaca.markets` (`adjustment=all`) into `artifacts/daily_bars.csv`, using the sidecar paper credentials. That read does not place an order and does not modify `fixtures/etf_daily.csv`. If the refresh fails, a dry-run keeps the local `--bars` file and says so. `--execute` refuses to submit orders when the refresh fails, including when the latest bar is more than five days behind the session date. `--no-refresh` skips the fetch and uses `--bars` (the committed fixture, unless you pass another file).

```bash
python -m sidecar.harness.runner --when open
python -m sidecar.harness.runner --when close
python -m sidecar.harness.runner --when auto
```

`--when auto` selects the open window (09:30–10:05 America/New_York) or the close window (15:40–16:05). Outside those windows a dry-run still writes a report. `--execute` outside the selected window exits 3 unless `--force-window` is set.

Execute posts only to `https://paper-api.alpaca.markets`, and only after `GET /v2/account` on that host returns an ACTIVE account. Alpaca's payload has no reliable paper boolean. A 200 from this pinned host is the attestation that the key is a paper key. If `account_type`, `trading_mode`, or `environment` is present it must say `paper` or `paper_trading`. A blocked account is refused. Any other trading base URL is refused before the request.

Credentials, and nothing else:

1. Keyring service `ai-investment-cockpit-sidecar`, names `paper_key` and `paper_secret`.
2. Otherwise both `SIDECAR_PAPER_ALPACA_KEY_ID` and `SIDECAR_PAPER_ALPACA_SECRET`.

There is no fallback to `ai-investment-cockpit` / `alpaca_key` / `alpaca_secret` or to `SIDECAR_ALPACA_KEY_ID` / `SIDECAR_ALPACA_SECRET`. Exit code 2 if the sidecar pair is missing.

```bash
keyring set ai-investment-cockpit-sidecar paper_key
keyring set ai-investment-cockpit-sidecar paper_secret
python -m sidecar.harness.runner --when close --execute --out artifacts/paper_execute_report.json
```

Pre-trade limits, applied after the sizer and only able to shrink or refuse: max notional per order $2,000, max gross exposure $20,000, max 3 positions, daily loss stop $500 from the previous portfolio-history equity, allowlist SPY, QQQ, IWM, minimum notional $1. Client order ids are alphanumeric, at most 48 characters, and stable for the same New York session, symbol, side, and cent-rounded notional. A repeat run finds the id and does not post a second order.

Kill switch: create `artifacts/KILL`, or set `SIDECAR_KILL_FILE`. New orders are refused. In execute mode, open orders whose client id starts with `sc` are cancelled.

```bash
touch artifacts/KILL
```

Strategies for a live run: `inverse_vol` (the predeclared offline rule, default) or `spy_only`. `kronos` is optional and is refused unless `examples/kronos_eval.json` (or `--kronos-report`) exists, records evaluation start `2024-07-01` and pretraining through `2024-06-30`, and is marked `live_eligible`. The runner does not load Kronos weights. One book per process. Every run appends the inverse-vol lookbacks, the SPY-only config, the Kronos config, and the simulation-only Avellaneda-Stoikov config to the trial ledger. The market maker is not sent to the broker. The Kronos ledger row stays `live_eligible: false`; the report file is the gate.

The daily report is `sidecar.daily_report.v1`: equity curve, PSR/DSR, drawdown, TCA, and a SPY benchmark when the curve is the local simulation. Execute mode uses broker portfolio history and aligned paper fills when those samples are long enough. Otherwise the curve and TCA stay on the local simulation and the report says so. `orders_submitted` counts new posts. A dry-run is 0.

Weekday cron, America/New_York:

```cron
35 9 * * 1-5  cd sidecar && python -m sidecar.harness.runner --when open
45 15 * * 1-5 cd sidecar && python -m sidecar.harness.runner --when close --execute
```

## Report

Offline and the read-only observer emit `orders_submitted: 0` and `broker_writes: false`. The runner may emit a positive `orders_submitted` only in `sidecar_paper_execute` mode. `experimental_order_path` is true on runner reports.
