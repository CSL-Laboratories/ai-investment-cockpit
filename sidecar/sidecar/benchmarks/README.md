# Benchmark strategies

Three QuantJourney examples, ported as research books. They are selectable by
name (`w18`, `o17`, `w25`) and they do not submit orders. The live paper close
stays on `inverse_vol` (or `spy_only` / `kronos` when that existing flag is
passed). `--execute` with a benchmark name is refused.

Source repository: [QuantJourneyOrg/quantjourney-bt](https://github.com/QuantJourneyOrg/quantjourney-bt), Apache-2.0, commit `4f403d853833e1daa6de01c5b6c6cb3983dc0044`.

| Name | Source file | Rule kept |
|---|---|---|
| `w18` | `strategies/example_weights_18_vol_target_momentum.py` | 126-day momentum, top 3 names with a positive return, equal weight clipped at 0.40, then `VolTargetModel(0.15, lookback=42, max_leverage=2)` refreshed on the first sample day of the month. Rebalance on business month-end. |
| `o17` | `strategies/example_orders_17_monthly_rotation_orders.py` | First bar of a month, rank by 126-day return (negative returns can still be chosen), sell names that left the top 3, buy a flat name `int(nav / n / price)` shares. A name that stays in the set is not resized. |
| `w25` | `strategies/example_weights_25_continuous_futures_trend.py` | SMA(63) versus SMA(252), sign, unit-gross inverse-volatility weights, business month-end rebalance. |

The published universes are not the cached SPY/QQQ/IWM file. W18 and O17 were published on SPY, EFA, EEM, TLT, IEF, GLD, DBC and VNQ from 2007-01-03 to 2026-01-01 (CAGR about 10.96% and 8.62%). W25 was published on MES=F, MNQ=F, ZN=F, CL=F, GC=F and ZC=F from 2019-05-06 to 2026-01-01 (CAGR about 5.88%). This tree has no futures tape. The proxies are SPY, QQQ, IEF, USO, GLD and DBA. DBA is a broad agriculture basket, not corn. A name missing from the bar file is listed on the report and is not filled in.

Negative W25 weights are a research short. The shadow book sets them to cash. It does not buy an inverse ETF. The comparison prints both the long/short research row and that cash variant.

Fills in the comparison are the sidecar convention: signal at the adjusted close, trade at the next adjusted open, 1 bp. The source weight engine marks the new weights at that close, and O17's published cost is 3 bp plus $0.005 per share with a $1 minimum. Those are not this cost model. `max_position_size=0.40` on the W18 and O17 constructors is report metadata in the source once a custom risk model or order mode is set. W18 still clips the pre-overlay equal weight at 0.40. The vol overlay can then scale that weight, up to 2x. This port does the same.

Ties in a momentum rank break on the symbol, ascending. `Series.nlargest` does not document that tie-break.

```bash
cd sidecar
python -m sidecar.benchmarks
python -m sidecar.harness.runner --when close --shadow --no-refresh
```

`--shadow` appends hypothetical marks to `artifacts/benchmark_shadow.jsonl` and still plans only the selected live strategy. The comparison writes `artifacts/benchmark_comparison.md`. That directory is gitignored. `orders_submitted` is 0. `threshold_status` is `not_frozen`.
