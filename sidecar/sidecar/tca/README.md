# tca

Implementation shortfall for one fill, and a small Almgren-style regression.

For a buy, cost is positive when the print is above the reference. Delay is arrival minus decision. Execution is fill minus arrival. The fee is added on both sides. Basis points use decision notional for the total and arrival notional for the arrival piece.

`summarize_fills` reports the median, mean, and 95th percentile absolute shortfall, plus realized minus `predicted_cost_bps` when that field is present.

`fit_impact` uses rows that carry positive `sigma` and `adv`:

```text
arrival_shortfall_fraction / sigma = epsilon + eta * sqrt(q / ADV) + gamma * (q / ADV)
```

Fewer than eight usable rows, fewer than three distinct participation rates, a singular design, or a non-positive `epsilon` or `eta` (or a negative `gamma`) leaves `recommended` null. The fee schedule in `app/quant/costs.py` is not replaced. Fills whose fill price equals the arrival mid have no execution dispersion; the harness does not pretend that overnight gap is impact.

## Retrofit

Keep this package outside `app/quant/` so quant still does not import brokers. A later optional loader in `quant/costs.py` may read `sidecar.cost_params.v1` as an impact range on top of the existing fee defaults. Paper-venue and simulated fits stay labeled.
