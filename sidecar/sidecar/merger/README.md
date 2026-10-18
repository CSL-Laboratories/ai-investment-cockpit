# merger

Point-in-time deal rows and a frozen logistic completion model.

Deal *terms* come from operator cards (consideration, hostile, regulatory review, offer, market price, outside date, outcome). EDGAR is used only to prove which merger forms were already public. `fetch_submissions` requires `SIDECAR_SEC_USER_AGENT` to contain a contact email. Tests use a fixture payload and do not call the network.

A filing with an acceptance timestamp is visible at and after that timestamp. A date-only filing is visible starting the next UTC date. Outcome time must be strictly after `observed_at`. Training uses deals whose observation and resolution are both on or before `training_cutoff`. Testing uses deals first observed after the cutoff. Deals that were open at the cutoff are censored, not labeled with their eventual outcome.

The fit is L2-regularized logistic regression by IRLS (`irls_l2_logistic_v1`). Features are standardized on the training rows. The intercept is not penalized. The artifact stores coefficients as round-trip strings and is checked by re-scoring the training rows. Freeze is refused when the training sample, either class, or the broken-deal count is below the minimum, when IRLS does not converge, or when synthetic cards are passed without `allow_synthetic=True`.

Break loss is the nearest-rank 10th percentile of training broken-deal returns, stored as a positive magnitude when that return is negative. Expected return:

```text
p_complete * spread - (1 - p_complete) * break_loss_magnitude
```

`wilson_lower_bound` matches the Wilson arithmetic in `app/strategies/merger_edge.py`. It does not satisfy that module's cohort contract.

## Retrofit

Do not load `sidecar.merger_calibration.v1` as `merger_calibration.v2`. The cockpit method pin remains `wilson95_heldout_worst_case_prices_v2` until a reviewed amendment. `app/risk/` should receive only Decimal edge and downside, produced outside `risk/`.
