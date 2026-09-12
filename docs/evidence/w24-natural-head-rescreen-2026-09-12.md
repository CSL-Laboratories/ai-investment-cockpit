---
title: W-24 natural-head GET-only screen — 2026-09-12 (sealed operator packet)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-12
related:
  - REGISTER (W-24 / W-23 / RW-31 / RW-32)
  - evidence/w24-natural-head-rescreen-2026-09-09
  - evidence/w24-natural-head-rescreen-2026-09-12.seal.json
claimant: Grok Bot (owner-operated soak / operator box)
operator_main_head: 885d33a76b3cb9ab7a507e56f4a1d3f993e59b83
soak_sha_observed_live: 5d50b25194c14beaac7bb979dcd559cc21a2fb95
raw_packet_status: sealed_summary_hashes
---

# W-24 natural-head GET-only screen — 2026-09-12

RW-31 recorded **board counts only**. RW-32 pins the operator **seal**
Grok Bot posted from the soak box. Empty is still honest. This is **not**
a replica of the soak-host markdown (that file's SHA-256 is in the seal
and is **not** this file). Raw SEC primary docs stay on the soak host.

Checked-in seal: [w24-natural-head-rescreen-2026-09-12.seal.json](w24-natural-head-rescreen-2026-09-12.seal.json)
(SHA-256 `de131f21fa7ed81f0ff258c9f6bc16ab4d35b47eb0ec35c93b133906125821bc`).

## Claim

| Field | Value |
|---|---|
| Package | `W-24` (counts) / `RW-32` (seal pin) |
| Claim action | Pin 2026-09-12 GET-only identities + summary-file hashes |
| Claimant | Grok Bot / operator seal |
| Operator `main` tip at screen | `885d33a76b3cb9ab7a507e56f4a1d3f993e59b83` (pre-#94) |
| Parser path (soak box) | `/workspace/cockpit-main-tip/app/strategies/merger_semantics.py` |
| Parser object SHA as sealed | `fda5017b1c87cd4d6e18e6f297e2ca291371d44d` |
| Soak SHA observed live | `5d50b25194c14beaac7bb979dcd559cc21a2fb95` (**not rewritten**) |
| Selection / Discord / watchlist | **Not attempted / not posted / not seeded** |
| Raw SEC docs in this clone | **No** |

`parser_blob_sha` is the operator-reported 40-hex. In this clone that
object is **commit** `fda5017` (`fix(RW-23): preserve cash-term qualifiers
across markup (BUG-146)`), not the blob of
`885d33a:app/strategies/merger_semantics.py` (`9c5efc6277085ff190784036ca4f0010a831d762`).
RW-32 records the seal as stated; it does not silently retcon the parser
object.

## Counts (unchanged; still empty)

| Metric | 2026-09-12 | 2026-09-09 |
|---|---|---|
| Union pool screened | **197** | 195 |
| Absolute head supported `{8-K,10-Q,10-K}` | **32** | 32 |
| Cash-per-share parse hits | **0** | 0 |
| Selection-ready | **0** | 0 |
| Date-ready | **0** | 0 |
| Meets ≥30 cohort gate | **false** | false |

### Primary gate result

**NO PASS / empty / date-not-ready** — cohort **0 ≪ 30**. Pinning
identities does **not** lower the gate and is **not** labelled truth.

## Supported-head identities (n=32; from seal)

ACA, AIRE, AROW, BKR, CCO, CYCU, CYCUW, DIOD, ESI, FSEA, GBCS, GRML,
GRMLW, HUN, JTAI, OLN, RLYB, RMBI, SLP, SOLS, STRR, STRRP, TARS, TBPH,
TWOD, TXNM, UHS, UUUU, VCTR, VEEE, ZSQR, ZYME.

Vs 2026-09-09: **added** AIRE, AROW, RLYB, TARS, TBPH, TWOD, VCTR;
**dropped** ATAI, BBBY, GNL, KPLT, KR, SEI, THFF. Copying Sep 9's list
would have been a false seal.

## Soak-host summary hashes (raw docs still not in git)

| File | SHA-256 |
|---|---|
| `w24-prospective-intake-natural-head-rescreen-2026-09-12.json` | `24a22922186a0ebe0d5232f53e802fa13c8b3f1435c5fe15594b2a0c653b94ca` |
| `discovery-pool-rescreen-2026-09-12.json` | `ba14388c106409bcd86bce65e1d5324905a6a5757676d284568905d6dd0d83f0` |
| `intake-natural-head-rescreen-2026-09-12.log` | `dbee14c9c15476a943d72a7ed6e6cc5bce5e2634251c17e9a11c2b0408ccca34` |
| `w24-natural-head-rescreen-2026-09-12.md` (soak markdown; after removing self-hash line) | `86d7f2115027c295f2de060113b470a65e4e11b40f510444f7982a8eb34d547a` |

That markdown hash is **not** this in-repo note.

## Exact next operator action

Keep GET-only natural-head screens. Do **not** seed
`COCKPIT_MERGER_WATCHLIST`, post Discord selection, rewrite soak HEAD, or
`/paper approve`. Wait until selection-ready ≥30 **and** date-ready.
These 32 names are a **candidate extraction-refusal set**, not a
cash-merger cohort. Labelling is **not** authorized: this seal hashes
summary JSON/log/md, not hashed raw filings.
