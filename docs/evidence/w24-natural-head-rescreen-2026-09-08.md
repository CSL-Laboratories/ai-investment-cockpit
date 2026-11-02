---
title: W-24 natural-head GET-only re-screen — 2026-09-08 (Tuesday cron / hand-run primary)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-08
related:
  - REGISTER (W-24 / W-23)
  - evidence/w24-natural-head-rescreen-2026-09-07
  - evidence/w24-natural-head-rescreen-2026-09-04
  - wake tue-w-24-natural-head-rescreen
soak_sha: c5ef0ad3d9acd3a85aa86228491ea1827bfd529e
parser_sha_pr62: efc626f18382fc049297520fa863f4d57aa03603
---

# W-24 natural-head GET-only re-screen — 2026-09-08

## Claim

| Field | Value |
|---|---|
| Package | `W-24` |
| Claim action | Tuesday natural-head GET-only re-screen (post-Mon empty screen) |
| Claimant | Cockpit Admin automation (urllib SEC intake + offline #62 grammar) |
| Date (PT) | 2026-09-08 ~05:56–05:58 PT |
| Date (UTC) | 2026-09-08 ~12:56–12:58Z |
| Parser code | `/workspace/ai-investment-cockpit` with PR **#62** `merger_semantics` (`efc626f18382fc049297520fa863f4d57aa03603`) |
| Main clone HEAD at run | `5d50b25194c14beaac7bb979dcd559cc21a2fb95` (`main`; grammar still #62) |
| Soak SHA (observed, unchanged) | `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e` |
| Soak pin note | Observed `c5ef0ad` (PR #70 merge) — **not rewritten**. Prior pin `61ca0af` frozen at `/workspace/cockpit-soak-61ca0af-prior`; BUG-114 incident frozen at `/workspace/cockpit-soak-bug114-incident`. |
| Soak 8787 | Up (GET `/status` → `401` without key; process not restarted) |
| Soak tree dirty? | **No** — evidence only under `/workspace/w24-evidence/` |
| Selection commitment | **Not attempted** |
| Discord selection message | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` | **remains empty** (no AAPL/MSFT/NVDA seed) |

## Scope / constraints honored

- GET-only against live SEC (EDGAR FTS + company_tickers + submissions + primary docs via urllib; offline semantics on main clone).
- Absolute head required in `{8-K, 10-Q, 10-K}` for "supported" count.
- Selection-ready = semantic eligibility OK on combined source-set **and** `current_matches_set_end`.
- Date-ready = expected resolution date **and** outside date both parse.
- No forge edge, no soak HEAD rewrite, no read_only restart, no Discord, no watchlist seed, no OrderGateway bypass, no Luna/Composer/Astra/Codex, no form-set PR.
- Packages held (empty / date-not-ready screen).

## Scan method

Same path as 2026-09-01..2026-09-07 natural-head / post-#62 rescreens:

1. **Discovery (not authority):** StockAnalysis frozen list ∪ prior frozen names (expanded with Sep 7 supported incl. **ACA/GRML/KPLT/THFF**) ∪ EDGAR FTS  
   `"Agreement and Plan of Merger" AND "in cash"`, forms=`8-K`, `2026-06-01`..`2026-09-08`, pages `from=0` and `from=100` (size 100).
2. **Authority:** live `company_tickers` → CIK map → live `submissions` absolute head (all mapped CIKs refreshed) → newest ≤12 supported-2026 `{8-K,10-Q,10-K}` primary docs (GET; prior-day doc cache reused when accession+filename match).
3. **Semantics:** `merger_semantics` from **main clone** (PR #62 cash grammar) via local python — **not** soak `app/`.
4. Primary script: `get_only_natural_head_rescreen_2026_09_08.py`. Companion evidence helpers (parity): `_classify_heads_2026_09_08.py`, `_plan_docs_2026_09_08.py` (+ discovery/map/offline dated helpers on disk).

Note: first FTS attempt at ~12:55Z returned HTTP 500 from efts.sec.gov; immediate retry at 12:56Z succeeded (curl probe also 200).

## Counts

| Metric | 2026-09-08 | 2026-09-07 |
|---|---|---|
| Union pool screened | **194** | 193 |
| StockAnalysis visible | 43 | 43 |
| Frozen prior (expanded) | 58 | 54 |
| EDGAR FTS tickers | 176 | 175 |
| EDGAR FTS total hits | **628** | 622 |
| No current SEC ticker map | **51** | 51 |
| Absolute head unsupported form | **113** | 113 |
| Absolute head supported `{8-K,10-Q,10-K}` | **30** | 29 |
| Cash-per-share parse hits (`_CASH`) | **0** | 0 |
| Semantic source-set pass | **0** | 0 |
| Selection-ready (elig + current matches set end) | **0** | 0 |
| Current target date-ready | **0** | 0 |
| Meets ≥30 cohort gate | **false** | false |
| Doc bytes received (runner) | 19,256,853 | 176,448,446 (estimate) |

Top filing refusals (supported-head docs): `SEC source does not state one cash-per-share value` ×349.

### Primary gate result

**NO PASS / empty / date-not-ready** — **zero** grammar/selection-eligible natural heads; **zero** date-ready; cohort **0 ≪ 30**.

### Candidates (selection-eligible, not date-ready)

_None._

### Cash parse but **not** selection-ready

_None today._

### Notable vs Mon 2026-09-07

| Symbol | Sep 7 | Sep 8 absolute head | Gate impact |
|---|---|---|---|
| **BHF** | Form `3` `0001628280-26-060470` (2026-09-03) | Form **`3`** `0001628280-26-060470` (2026-09-03) | Still unsupported head |
| **SAFT** | Form `PREM14A` `0001104659-26-105083` (2026-09-03) | Form **`PREM14A`** `0001104659-26-105083` (2026-09-03) | Still unsupported head |
| **USAR** | Form `S-3` `0001213900-26-097756` (2026-09-04) | Form **`S-3`** `0001213900-26-097756` (2026-09-04) | Still unsupported head |
| **UCB** | not in supported set | Form **`8-K`** `0001104659-26-105666` (2026-09-08) | **Added** supported form gate only; **no cash** |

### Absolute-head-supported symbols (form gate only; n=30)

ACA, ATAI, BKR, CCO, CYCU, CYCUW, DIOD, ESI, FSEA, GNL, GRML, GRMLW, HUN, JTAI, KPLT, KR, OLN, RMBI, SLP, SOLS, STRR, STRRP, THFF, TXNM, UCB, UHS, UUUU, VEEE, ZSQR, ZYME.

Notable vs 2026-09-07: **added** UCB (fresh 8-K head filed 2026-09-08); **dropped** none. No symbol cleared cash eligibility.

### Unsupported absolute-head form histogram (top)

`4`×45, `144`×10, `PREM14A`×6, `EFFECT`×6, `DEFA14A`×5, `SCHEDULE 13D/A`×4, `DEFM14A`×4, `425`×4, `SCHEDULE 13G/A`×4, …

## Gate status after re-screen

| Gate | Status |
|---|---|
| ≥30 selection-ready / date-ready cohort | **Open (0)** — empty; dates N/A |
| Discord selection witness | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` settable? | **NO** |
| Paper / OrderGateway path | **Still fail-closed** (correct) |
| Soak HEAD / read_only | **Unchanged** at observed `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e`; 8787 still answering |

## Exact next operator action

Keep soak as awareness only on observed `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e` (do **not** rewrite; prior `61ca0af` and BUG-114 remain frozen). Do **not** seed watchlist; no Luna/Composer/Astra/Codex for this empty/date-not-ready screen. Next market-day plan: **Wednesday 2026-09-09 05:30 PT** natural-head GET-only re-screen. Wait for natural absolute heads that are both grammar-pass **and date-ready** until selection-ready ≥30.

## Evidence paths

- `/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-08.md` (this file)
- `/workspace/w24-evidence/w24-prospective-intake-natural-head-rescreen-2026-09-08.json`
- `/workspace/w24-evidence/discovery-pool-rescreen-2026-09-08.json`
- `/workspace/w24-evidence/intake-natural-head-rescreen-2026-09-08.log`
- `/workspace/w24-evidence/rescreen-raw-2026-09-08/` (FTS pages, submissions, docs, head-classify.json, doc-fetch-plan.json)
- Scripts: `get_only_natural_head_rescreen_2026_09_08.py`, `_build_discovery_2026_09_08.py`, `_map_pool_2026_09_08.py`, `_classify_heads_2026_09_08.py`, `_plan_docs_2026_09_08.py`, `_offline_semantic_2026_09_08.py`
