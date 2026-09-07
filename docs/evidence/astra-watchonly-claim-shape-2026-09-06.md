# watch_only durability D1–D4 — claim shape (queued after BUG-125)

**Status:** mid-flight evidence land before Codex hard stop (2026-09-06 ~19:45 PT).  
**Not a production PR.** Queued behind merged BUG-125 / #74. Soak stays `c5ef0ad` on 8787.

## Prior diagnosis
See `docs/evidence/astra-watchonly-durability-2026-09-06.md` — FAIL D1–D4 with fixtures; residual after BUG-113/#59.

## Claim shape (fail-closed only)
- **In scope:** refuse / fail-closed durability for watch_only generation across restart + concurrent write paths covered by D1–D4.
- **Out of scope:** selection loosen, date loosen, watchlist seed, soak rewrite, DEC-09 M1–M3 cash-subject (evidence-only elsewhere).
- **Base:** main post-#74 (`ce62cf2` / `17b73a0` lineage). Do not reopen F1/F2 or HWM signing.
- **Monitor PASS criteria (when claimed):** fixtures prove refuse on D1–D4 miss modes; no selection PASS; soak pin unchanged.

## Mid-flight fixtures (this land)
Under `docs/evidence/astra-watchonly-claim-shape-2026-09-06/`:
- `a57e8cf-baseline.json` / `.log` — baseline pytest slice at a57e8cf
- `a57e8cf-durability-replay.json` / `.log` — durability replay probe
- `prompt.md` — Codex prompt for this package

Full refuse-contract write-up may still arrive from the residual Astra lane before 8:15 PT; amend this PR if it lands, else treat this as the freeze.

## Prompt
See `astra-watchonly-claim-shape-2026-09-06/prompt.md`.
