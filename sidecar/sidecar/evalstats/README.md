# evalstats

PSR, deflated Sharpe, minimum track-record length, CSCV/PBO, stationary-bootstrap intervals, drawdown, and an append-only trial ledger.

```python
from sidecar.evalstats import TrialLedger, evaluate

report = evaluate(returns, n_trials, benchmark_returns)
```

`returns` are per-period simple returns. PSR and DSR use the per-period Sharpe (sample standard deviation, divide by n − 1). Skewness and Pearson kurtosis use biased moments (divide by n). Annualized Sharpe is `per_period * sqrt(periods_per_year)` and is not fed back into PSR. With one trial, DSR equals PSR against `sr_benchmark` (default 0). With more trials, the hurdle is that benchmark plus the Bailey / López de Prado expected-maximum haircut.

`evaluate` returns `sidecar.eval_report.v1`. `threshold_status` is `not_frozen`. There is no qualify or reject field. Series shorter than 8 observations, or a non-positive PSR denominator, come back as `insufficient_sample` or `moment_bound_violated` instead of a made-up Sharpe.

PBO needs a matrix of at least two aligned trial series. It is the fraction of combinatorially symmetric splits where the in-sample winner's out-of-sample Sharpe is at or below the median. Complementary splits are counted once. The split is not purged or embargoed, so pass returns whose labels do not overlap. `n_slices` must be even.

`TrialLedger` hashes each config and chains the file. `distinct_config_count()` is the trial count to pass into `evaluate`. Kinds are `strategy_config`, `parameter_set`, and `prompt_version`.

Drawdown is the peak-to-trough decline of a path that starts at 1. Duration is the longest run of marks spent under the running peak.

Bootstrap intervals are Politis–Romano stationary bootstrap percentile intervals. The default block length is `round(n ** (1/3))`.

## Retrofit

See the top-level `sidecar/README.md`. The cockpit's feasibility classifier stays discrete until a human freezes numeric thresholds in a new protocol file, before any measurement run. `thresholds.proposed.json` is not that file.
