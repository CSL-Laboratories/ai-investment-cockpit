---
title: W-24 natural-head GET-only re-screen — 2026-09-02 (missed Wednesday wake)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-02
related:
  - REGISTER (W-24 / W-23)
  - evidence/w24-natural-head-rescreen-2026-09-01
  - evidence/w24-post62-rescreen-2026-09-01
  - wake wed-w-24-natural-head-rescreen
soak_sha: 1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4
parser_sha_pr62: efc626f18382fc049297520fa863f4d57aa03603
---

# W-24 natural-head GET-only re-screen — 2026-09-02

## Claim

| Field | Value |
|---|---|
| Package | `W-24` |
| Claim action | Hand-run missed Wednesday **natural-head GET-only re-screen** (wake `wed-w-24-natural-head-rescreen` never fired; lastRunAt null) |
| Claimant | Grok Bot executor (Cockpit Admin / soak ops) |
| Date (PT) | 2026-09-02 ~05:57–05:59 PT |
| Date (UTC) | 2026-09-02 12:57–12:59Z |
| Parser code | `/workspace/ai-investment-cockpit` with PR **#62** `merger_semantics` (`efc626f`) |
| Soak SHA (unchanged) | `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4` |
| Soak 8787 | Up (GET `/status` → `401` without key; process not restarted) |
| Soak tree dirty? | **No** — evidence only under `/workspace/w24-evidence/` |
| Selection commitment | **Not attempted** |
| Discord selection message | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` | **remains empty** (no AAPL/MSFT/NVDA seed) |

## Scope / constraints honored

- GET-only against live SEC (EDGAR FTS + company_tickers + submissions + primary docs).
- Absolute head required in `{8-K, 10-Q, 10-K}` for “supported” count.
- Selection-ready = semantic eligibility OK on combined source-set **and** `current_matches_set_end`.
- Date-ready = expected resolution date **and** outside date both parse.
- No forge edge, no soak HEAD rewrite, no read_only restart, no Discord, no watchlist seed, no OrderGateway bypass, no Luna/Composer swarm, no form-set PR.

## Scan method

Same path as 2026-09-01 natural-head / post-#62 rescreens:

1. **Discovery (not authority):** StockAnalysis frozen list ∪ prior frozen names ∪ EDGAR FTS  
   `"Agreement and Plan of Merger" AND "in cash"`, forms=`8-K`, `2026-06-01`..`2026-09-02`, pages `from=0` and `from=100` (size 100).
2. **Authority:** live `company_tickers` → CIK map → live `submissions` absolute head → newest ≤12 supported-2026 `{8-K,10-Q,10-K}` primary docs (GET; local prior-day doc cache reused only when accession+filename match).
3. **Semantics:** `merger_semantics` from **main clone** (PR #62 cash grammar) via `/workspace/ai-investment-cockpit/.venv` — **not** soak `app/`.
4. Script: `/workspace/w24-evidence/get_only_natural_head_rescreen_2026_09_02.py`

## Counts

| Metric | 2026-09-02 | Post-#62 (2026-09-01) |
|---|---|---|
| Union pool screened | **190** | 189 |
| StockAnalysis visible | 43 | 43 |
| Frozen prior (expanded) | 32 | 19 |
| EDGAR FTS tickers | 173 | — |
| EDGAR FTS total hits | **595** | 590 |
| No current SEC ticker map | **51** | 51 |
| Absolute head unsupported form | **101** | 100 |
| Absolute head supported `{8-K,10-Q,10-K}` | **38** | 38 |
| Cash-per-share parse hits (`_CASH`) | **2** (BHF, SAFT) | 2 |
| Semantic source-set pass | **1** (`BHF`) | 1 (`BHF`) |
| Selection-ready (elig + current matches set end) | **1** (`BHF`) | 1 |
| Current target date-ready | **0** | 0 |
| Meets ≥30 cohort gate | **false** | false |
| Bytes received (new SEC GETs this run) | 20,773,869 | (offline reuse) |

Top filing refusals (supported-head docs):

| Refusal | Count |
|---|---|
| `SEC source does not state one cash-per-share value` | 436 |
| `ok` (eligibility pass on individual filing) | 2 |
| `calibration row is outside the pinned cash-merger strategy universe` | 2 |

### Primary gate result

**date-not-ready** — one grammar/selection-eligible natural head (`BHF`) exists, but expected + outside dates both refuse; cohort **1 ≪ 30**.

### Candidates (selection-eligible, not date-ready)

| Symbol | Head | Cash/share | Date-ready | Notes |
|---|---|---|---|---|
| **BHF** | 8-K `0001685040-26-000031` (2026-08-18) | **$70.00** | **no** | Source set `0001685040-26-000028` → head; `current_matches_set_end=true`; date refusals on both expected + outside |

### Cash parse but **not** selection-ready

| Symbol | Head | Cash hit | Blocker |
|---|---|---|---|
| **SAFT** | 10-Q `0001172052-26-000023` | `$105.00` | `has_stock=True` → outside pinned cash-merger universe (no semantic source-set) |

### Absolute-head-supported symbols (form gate only; n=38)

AON, ATAI, BHF, BKR, BLFS, BTGO, CCO, CHTR, CORZ, CORZR, CORZW, CORZZ, CYCU, CYCUW, DIOD, ESI, FGRS, FIGR, FSEA, GNL, HUN, JTAI, LXP, OLN, QXO, RMBI, SAFT, SLP, SOLS, STRR, STRRP, UCB, UHS, UUUU, VEEE, **VRT** (new head 8-K 2026-09-02), ZSQR, ZYME.

Notable vs prior day: **VRT** appeared as supported absolute head on 2026-09-02; AIRE no longer in supported set this run. None of the new/remaining heads cleared cash eligibility except BHF (and SAFT cash-hit only).

### Unsupported absolute-head form histogram (top)

`4`×43, `425`×8, `DEFA14A`×7, `144`×7, `EFFECT`×6, `SCHEDULE 13G/A`×5, `SC 13E3`×3, `SCHEDULE 13D/A`×3, `SCHEDULE 13G`×3, …

## Gate status after re-screen

| Gate | Status |
|---|---|
| ≥30 selection-ready candidates | **Open (1)** — BHF only; dates not ready |
| Discord selection witness | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` settable? | **NO** |
| Paper / OrderGateway path | **Still fail-closed** (correct) |
| Soak HEAD / read_only | **Unchanged** at `1e2d4e3`; 8787 still answering |

## Exact next operator action

Keep waiting for natural absolute heads / additional grammar-pass + **date-ready** names until selection-ready ≥30. Do **not** seed watchlist; do **not** rewrite soak HEAD. Optional later packages (not this wake): SAFT stock-flag false positive; FSEA/SLP/CCO alternate cash phrasing.

## Evidence paths

- `/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-02.md` (this file)
- `/workspace/w24-evidence/w24-prospective-intake-natural-head-rescreen-2026-09-02.json`
- `/workspace/w24-evidence/discovery-pool-rescreen-2026-09-02.json`
- `/workspace/w24-evidence/intake-natural-head-rescreen-2026-09-02.log`
- `/workspace/w24-evidence/rescreen-raw-2026-09-02/` (FTS pages, submissions, docs)
- Script: `get_only_natural_head_rescreen_2026_09_02.py`
