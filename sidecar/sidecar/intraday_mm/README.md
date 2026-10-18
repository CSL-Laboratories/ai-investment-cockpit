# Intraday market maker

Simulation on a quote and trade tape. This package does not place an order.

```bash
python -m sidecar.intraday_mm.session --synthetic --minutes 30 --out examples/intraday_mm_eval.json --ledger examples/intraday_mm_eval.ledger.jsonl
python -m sidecar.intraday_mm.session --replay path/to/session.parquet
python -m sidecar.intraday_mm.session --minutes 30
```

The live command reads `wss://stream.data.alpaca.markets/v2/iex` only, during 09:30–16:00 America/New_York, and writes parquet for replay. It uses the sidecar paper credentials for the data socket. It does not subscribe to orders. SIP and both trading hosts are refused. Exit 3 outside the window unless `--force-window` is set. Exit 2 if the live command has no credentials. `--synthetic` and `--replay` are offline.

`examples/intraday_mm_eval.json` is a 30-minute synthetic tape, not a live IEX session. On the held-out 12 minutes the Avellaneda-Stoikov book ended at -12.5618445107 and the GRU clone at -14.5778638398. `beats_as` is false. Markouts at 1s, 5s, and 30s are negative. The 50/75/100 millisecond trials filled identically on this tape. `orders_submitted` is 0. See `sidecar/README.md`.

Do not connect this package to the cockpit order path.
