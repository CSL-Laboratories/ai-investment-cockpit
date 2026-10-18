# Defined-risk options

Paper-only credit spreads on the sidecar Alpaca account. The offline file does not use a broker.

```bash
python -m sidecar.options.evaluate --out examples/options_eval.json --ledger examples/options_eval.ledger.jsonl
python -m sidecar.options.runner
python -m sidecar.options.runner --execute
```

Dry-run is the default. `--execute` posts a multi-leg limit to `https://paper-api.alpaca.markets` and nowhere else. Credentials are `SIDECAR_PAPER_ALPACA_KEY_ID` and `SIDECAR_PAPER_ALPACA_SECRET`, or keyring service `ai-investment-cockpit-sidecar` (`paper_key`, `paper_secret`). The cockpit pair is not read.

Limits: defined-risk only, $1,000 max loss per spread, $5,000 total, 5 open spreads, DTE 7–45, allowlist SPY/QQQ/IWM, kill file `artifacts/KILL`. Opening limits are negative credits. Missing greeks, a stale quote, or an options level below 3 refuses the open. IV rank needs 252 prior local sessions before a new spread is allowed. The session window is 09:30–16:00 America/New_York. Outside it the process exits 3 unless `--force-window` is set. Missing credentials exit 2.

The committed book ended at 99039.00 from 100000. `beats_cash` is false. PSR 0.168970384225. DSR 0.0221962979604. PBO 0.584770784771. Premiums are Black-Scholes on adjusted daily closes, with trailing realized vol standing in for implied vol. That is not an OPRA backtest. `orders_submitted` is 0. See `sidecar/README.md` for the full table. This runner is not the cockpit order path.
