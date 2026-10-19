# Intraday market maker

Simulation on a quote and trade tape. This package does not place an order.

```bash
python -m sidecar.intraday_mm.session --synthetic --minutes 30 --out examples/intraday_mm_eval.json --ledger examples/intraday_mm_eval.ledger.jsonl
python -m sidecar.intraday_mm.session --replay path/to/session.parquet
python -m sidecar.intraday_mm.session --minutes 30
```

The live command reads `wss://stream.data.alpaca.markets/v2/iex` only, during 09:30–16:00 America/New_York, and writes parquet for replay. It uses the sidecar paper credentials for the data socket. It does not subscribe to orders. SIP and both trading hosts are refused. Exit 3 outside the window unless `--force-window` is set. Exit 2 if the live command has no credentials. `--synthetic` and `--replay` are offline.

`examples/intraday_mm_eval.json` is a 30-minute synthetic tape, not a live IEX session. It uses the original fill rule. On the held-out 12 minutes the Avellaneda-Stoikov book ended at -12.5618445107 and the GRU clone at -14.5778638398. `beats_as` is false. Markouts at 1s, 5s, and 30s are negative. The 50/75/100 millisecond trials filled identically on this tape. `orders_submitted` is 0. See `sidecar/README.md`.

## Queue position

Alpaca IEX quotes are top of book: price and aggregate size, not each order. The fill model `rigtorp` follows Erik Rigtorp's estimate for that kind of feed. The recorded tape does not contain this simulator's orders.

The initial queue ahead, `V`, is the displayed size at our price. The comparison uses the size at send. `ack` uses the size when the order is live, after the latency. `average` uses the mean of those two. A price that improves the touch starts at 0. A price behind the touch is not on the top-of-book feed, so `V` stays unknown and that order does not fill.

After that, a size decrease at our price is split. The part that matches a trade at our price reduces `V` one for one: `V = max(V + dQ, 0)`. The rest is treated as a cancel:

`p = f(V) / (f(V) + f(max(Q - S - V, 0)))`

`Q` is the tape size plus our size, because the tape does not include us. `f` is `log1p` in the comparison. `identity` is available. Neither function was fit to our fills.

A fill happens only for the size that is left after `V` reaches 0 on a trade at our price or through it. A cancel can drive `V` to 0 and still not fill. A one-lot print through a thick level does not fill the whole order. The original rule does, and that rule is still `legacy`.

Intrinsic value is the fill probability implied by `V`, times the distance from our price to the mid, minus `max(0, -mean 1s markout)`. The markout mean is a 20-observation rolling window and is 0 until a markout exists. A negative value cancels the order. The next decision can post again. That cancel rule is not a forecast that was fit to anything.

The same rolling window, updated in constant time, is the mid-change volatility the quotes use. `rolling_std_strided` is the batch form of that calculation.

```bash
python -m sidecar.intraday_mm.session --queue-compare --minutes 30 --out examples/intraday_mm_queue_compare.json
```

`examples/intraday_mm_queue_compare.json` is that run. There is no recorded IEX parquet in this tree. Both sessions are synthetic, 30 minutes, seed 0, held-out last 40 percent. `orders_submitted` is 0.

The constant-size session is the committed tape. Touch size stays 40, so the cancel branch does not run. The legacy Avellaneda-Stoikov book is the same 791 fills and -12.5618445107 as `intraday_mm_eval.json`.

| Constant size | Legacy AS | Rigtorp AS | Legacy RL | Rigtorp RL |
|---|---:|---:|---:|---:|
| Fills | 791 | 36 | 802 | 4 |
| Fill rate | 0.553921568627 | 0.09375 | 0.569602272727 | 0.102564102564 |
| Ending mark-to-market | -12.5618445107 | -1.58894599388 | -14.5778638398 | 2.44649651285 |
| Markout 1s | -0.00924256756822 (789) | 0.0264907938993 (35) | -0.0104860109837 (800) | -0.0165780083727 (4) |
| Markout 5s | -0.00932436115628 (784) | 0.0367863935288 (34) | -0.00994173072756 (795) | -0.0785104486324 (4) |
| Markout 30s | -0.0151083535503 (751) | -0.00170601796129 (25) | -0.021656802306 (762) | -0.222146106492 (4) |

The smaller mark-to-market loss comes from 36 and 4 fills against 791 and 802. The Rigtorp RL book posted 39 times. Its 30-second markout on those 4 fills is largely negative. The positive 1-second Avellaneda-Stoikov markout is 35 fills, and the 30-second markout on those fills is still negative.

The variable-size session adds joins, cancels, and trades that reduce the displayed size. It is not a recording.

| Variable size | Legacy AS | Rigtorp AS | Legacy RL | Rigtorp RL |
|---|---:|---:|---:|---:|
| Fills | 770 | 1 | 768 | 195 |
| Fill rate | 0.538461538462 | 0.25 | 0.551724137931 | 0.138691322902 |
| Ending mark-to-market | -16.6444365295 | -2.05221826477 | -27.235437288 | 0.119105210234 |
| Markout 1s | -0.0139528450518 (769) | -0.0662419469349 (1) | -0.0170174513361 (767) | 0.0272641072383 (195) |
| Markout 5s | -0.0188784343923 (765) | 0.0352943553505 (1) | -0.02061032307 (763) | 0.0214596962006 (195) |
| Markout 30s | -0.0234363935633 (735) | 0.594457965142 (1) | -0.0254523362082 (736) | -0.00113080548767 (185) |

The Rigtorp Avellaneda-Stoikov book posted 4 times and filled once. The single 30-second markout is one print. The Rigtorp RL book ended at about +0.12 with inventory -9, while its mean per-minute return stayed negative (`-2.15838982539e-05`) and its 30-second markout stayed slightly negative. The 1-second and 5-second markouts on that book are positive. That is one synthetic path. It is not evidence of edge, and `log1p` was not estimated from fills.

Do not connect this package to the cockpit order path.
