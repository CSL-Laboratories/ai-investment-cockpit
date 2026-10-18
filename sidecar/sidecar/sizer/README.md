# sizer

One Decimal function:

```python
from decimal import Decimal
from sidecar.sizer import suggest_notional

result = suggest_notional(edge, vol, target_vol, kelly_frac, caps, equity)
```

Floats are rejected. `vol`, `target_vol`, and `equity` must be positive. `kelly_frac` and `max_weight` lie in `(0, 1]`. `max_weight` defaults to 1, so the suggestion cannot lever the account.

Volatility weight is `min(max_weight, target_vol / vol)` (Moreira–Muir scaling, capped). Kelly weight is `min(max_weight, kelly_frac * edge / vol**2)` for a positive edge, and 0 otherwise. The suggestion is the minimum of those notionals and `min(caps, equity * max_weight)`, quantized down to the cent. It is always `<= caps`.

`edge` and `vol` must be the same horizon. The function does not annualize and does not know about other positions. A book of several names has to scale the sum down after the per-name calls; the harness does that, and the scale only shrinks.

`ewma_volatility` is the RiskMetrics recursion, λ default 0.94, seeded with the first squared return so later returns do not set the initial variance.

## Retrofit

In `app/risk/engine.py`, fold the suggestion in as `min(max_allowed_notional, suggested)`. Do not add a second order path. The function is pure enough to live beside `risk/` later: Decimal, stdlib, no I/O, no model.
