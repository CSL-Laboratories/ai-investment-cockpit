# Defined-risk options

Paper-only credit spreads on the sidecar Alpaca account. The offline file does not use a broker.

```bash
python -m sidecar.options.evaluate --out examples/options_eval.json --ledger examples/options_eval.ledger.jsonl
python -m sidecar.options.runner
python -m sidecar.options.runner --execute
```

Dry-run is the default. `--execute` posts a multi-leg limit to `https://paper-api.alpaca.markets` and nowhere else. Credentials are `SIDECAR_PAPER_ALPACA_KEY_ID` and `SIDECAR_PAPER_ALPACA_SECRET`, or keyring service `ai-investment-cockpit-sidecar` (`paper_key`, `paper_secret`). The cockpit pair is not read.

Limits: defined-risk only, $1,000 max loss per spread, $5,000 total, 5 open spreads, DTE 7–45, allowlist SPY/QQQ/IWM, kill file `artifacts/KILL`. Opening limits are negative credits. Missing greeks, a stale quote, or an options level below 3 refuses the open. The session window is 09:30–16:00 America/New_York. Outside it the process exits 3 unless `--force-window` is set. Missing credentials exit 2.

IV rank uses the last 252 prior sessions when they exist. `--iv-min-history` (default 60) is the smallest count that still produces a rank. A shorter window is recorded on `iv_rank.gap` in the runner report. The canonical 252-day percentile is unchanged once that many sessions exist.

Waiting a year for the chain to fill `artifacts/iv_history.jsonl` is not required. The backfill writes a proxy series, tagged `source=proxy`:

| Underlying | Index | Primary file | FRED fallback |
|---|---|---|---|
| SPY | VIX | `https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv` | `VIXCLS` |
| QQQ | VXN | `https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv` | `VXNCLS` |
| IWM | RVX | `https://cdn.cboe.com/api/global/us_indices/daily_prices/RVX_History.csv` | `RVXCLS` |

The index close is in percentage points. The stored IV is that close divided by 100, so 18.22 becomes 0.1822. VIX is 30-day SPX implied vol used as a stand-in for SPY, VXN stands in for QQQ, and RVX stands in for IWM. None of them is the chain's ATM implied vol. The file keeps the latest 756 sessions of each index. A date that already has a row, including a live chain mark (`source=chain`), is not overwritten. CBOE is tried first. A failed CBOE download falls through to FRED's close-only CSV. No API key is used.

```bash
python -m sidecar.options.iv_backfill --out artifacts/iv_history.jsonl
python -m sidecar.options.runner --backfill-proxy --iv-min-history 60
```

The committed book ended at 99039.00 from 100000. `beats_cash` is false. PSR 0.168970384225. DSR 0.0221962979604. PBO 0.584770784771. Premiums are Black-Scholes on adjusted daily closes, with trailing realized vol standing in for implied vol. That is not an OPRA backtest. `orders_submitted` is 0. See `sidecar/README.md` for the full table. This runner is not the cockpit order path.
