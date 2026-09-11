---
title: RW-27 slice A feasibility freeze
status: reference
last-verified: 2026-09-11
---

# RW-27 slice A feasibility freeze (2026-09-11)

**Claim:** `RW-27` on `cursor/rw27-feasibility-freeze-df89`.
**Code:** `app/feasibility/` plus `app/schemas/json/FeasibilityFreeze.schema.json`.
**Not:** activation, order-path, soak, empirical PASS, launch selection.

## What this package freezes

- The six-name Head shortlist from RW-26 (cash-merger plus five).
- Checked-in freeze file: `app/feasibility/freeze.v1.json` (not under
  gitignored `data/`).
- Comparison-criteria *names* from `docs/product-delivery.md` §A.
  Numeric thresholds stay `unset`.
- Stopping conditions, including: no `qualify`/`watch`/`reject` until
  `metrics_schema_version` is frozen; cash-merger must be compared and is
  not guaranteed a launch place; `market_maker` is not on this list.
- Resource allowance: `public-tracked`, no new feeds, no broker, no soak.

Every candidate result in the checked-in freeze is `unverified`.

## Fail-closed rules

`feasibility.validate.validate_freeze` refuses:

- a shortlist other than the six frozen ids in order
- `market_maker`
- `empirical_pass_claimed: true`
- unknown fields
- `qualify` / `watch` / `reject` while `metrics_schema_version` is null

## Next package (not this one)

Freeze `metrics_schema_version`, then emit per-candidate
qualify/watch/reject against the same shortlist. Still not activation.
