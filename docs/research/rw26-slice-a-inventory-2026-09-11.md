---
title: RW-26 slice A feasibility inventory — swarm run
status: reference
last-verified: 2026-09-11
amends:
  - REGISTER
---

# RW-26 slice A feasibility inventory (2026-09-11)

**Claim:** `RW-26` on `cursor/swarm-slice-a-inventory-df89` (`44554ae`).
**Pipeline:** SWE-2 Medium leaf → SWE-2 first-gate → jsonschema → Muse
Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw26-2026-09-11/`. **No empirical PASS.** No activation.
No order-path edit.

## Intake hashes

| Kind | Path | SHA-256 |
|---|---|---|
| leaf | `filings/leaf-inventory.json` | `f74f7336e4348587257b43de48fa4058c0763d565ebcee48600b56d729894ab2` |
| review | `filings/review-inventory.json` | `e19bcb72b5fccbe3d1130f1c5685e447b4eba1f3116b421a311e02c6ded712cf` |
| pre-Muse manifest | `manifest-pre-muse.json` | `fbed1d32173032404654b9d12d4c2892d8b88cb10f80c8955bb25cc8a42c3f4a` |
| synthesis | `filings/synthesis.json` | `617ed238065df73820f29817731e7d50e89dc003558403f66dd81d92b364a025` |

Review verdict: `pass-with-dissent`. Muse receipt hash matched.
`empirical_pass_claimed` is false on every filing. Working tree stayed
clean.

ATIFs (gitignored): leaf `d980d2592f382d611d6f1efecaffecde418e5a9786acc71dc60c7742a11f7cf5`
(196241 prompt / 2618 completion / 16 steps); review
`284336d9f2ae6327f074c9556ee5d63f01cc0a60ab5a2419ceaa2aad04905ca9`
(138487 prompt / 2463 completion / 15 steps).

## Head steer of Muse nuances

1. **Mega-cap on the proposed shortlist.** Keep it as a *comparison*
   candidate. Section A requires comparing cash-merger plus alternatives;
   DEC-03's W-23 rejection is a comparison input (evidence attainability),
   not a silent drop. It is **not** selected and not activated.
2. **`market_maker.py` as an alternate sixth.** **Drop it** from this freeze.
   The bound is cash-merger plus five. Adding it requires a new claim that
   drops another name. Head does not judge long-only fit here.
3. **DEC-03 verdicts are history.** Agree. They do not complete slice A
   per-candidate qualify/watch/reject. Lucas still has to approve any
   later activation.
4. **PR/CRNX particulars.** Citation-only. REGISTER W-23/W-24 rows
   remain the gated status. This inventory does not re-derive those
   evidence gates.

Head-owned spawn observation (amends the smoke note): under
`accept-edits` without `--sandbox`, `ls`/`grep` via `exec` **did run**.
A later `python3` jsonschema `exec` was rejected. Filing writes succeeded.
Do not take `dangerous`. Jobs that must `exec` still need a separately
reviewed profile; this inventory run used shell `ls`/`grep` as a
measured fact, not as a new allowlist.

## Head-frozen proposed shortlist (not selected)

Cash-merger plus five, all already in DEC-03 or `app/quant/strategies/`:

1. Cash-only small merger / cash-merger (DEC-03 + W-23)
2. Closed-end-fund discount reversion (DEC-03, deferred)
3. Mega-cap technical/news signal (DEC-03, previously rejected for W-23)
4. Stay strategy-agnostic / awareness-only fallback (DEC-03)
5. Momentum (`app/quant/strategies/momentum.py`)
6. Pairs (`app/quant/strategies/pairs.py`)

`market_maker.py` is inventoried as a module and is **not** on this
shortlist. Next slice A package would freeze comparison criteria and
stopping conditions, then run qualify/watch/reject against this list —
still not activation.

## What this does not do

- Does not select launch strategies
- Does not activate W-23, paper, live, or any auto rung
- Does not implement slice C/D authority
- Does not start a soak
- Does not claim the DEC-09 matrix or any evidence gate
