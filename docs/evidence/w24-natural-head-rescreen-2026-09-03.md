---
title: W-24 natural-head GET-only re-screen — 2026-09-03 (Thursday cron / hand-run primary)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-03
related:
  - REGISTER (W-24 / W-23)
  - evidence/w24-natural-head-rescreen-2026-09-02
  - evidence/w24-natural-head-rescreen-2026-09-01
  - evidence/w24-post62-rescreen-2026-09-01
  - wake thu-w-24-natural-head-rescreen
soak_sha: 1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4
parser_sha_pr62: efc626f18382fc049297520fa863f4d57aa03603
---

# W-24 natural-head GET-only re-screen — 2026-09-03

## Claim

| Field | Value |
|---|---|
| Package | `W-24` |
| Claim action | Thursday cron / hand-run primary **natural-head GET-only re-screen** (`thu-w-24-natural-head-rescreen`) |
| Claimant | Cockpit Admin automation (curl SEC intake + offline #62 grammar) |
| Date (PT) | 2026-09-03 ~05:38–05:55 PT |
| Date (UTC) | 2026-09-03 12:38–12:55Z |
| Parser code | `/workspace/ai-investment-cockpit` with PR **#62** `merger_semantics` (`efc626f`) |
| Soak SHA (unchanged) | `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4` |
| Soak 8787 | Up (GET `/status` → `401` without key; process not restarted) |
| Soak tree dirty? | **No** — evidence only under `/workspace/w24-evidence/` |
| Selection commitment | **Not attempted** |
| Discord selection message | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` | **remains empty** (no AAPL/MSFT/NVDA seed) |

## Scope / constraints honored

- GET-only against live SEC (EDGAR FTS + company_tickers + submissions + primary docs via `curl`; offline semantics on main clone).
- Absolute head required in `{8-K, 10-Q, 10-K}` for “supported” count.
- Selection-ready = semantic eligibility OK on combined source-set **and** `current_matches_set_end`.
- Date-ready = expected resolution date **and** outside date both parse.
- No forge edge, no soak HEAD rewrite, no read_only restart, no Discord, no watchlist seed, no OrderGateway bypass, no Luna/Composer swarm, no form-set PR.
- Auto-review blocked direct venv+urllib script execution; intake used `curl`, semantics ran local-only offline.

## Scan method

Same path as 2026-09-01 / 2026-09-02 natural-head / post-#62 rescreens:

1. **Discovery (not authority):** StockAnalysis frozen list ∪ prior frozen names ∪ EDGAR FTS  
   `"Agreement and Plan of Merger" AND "in cash"`, forms=`8-K`, `2026-06-01`..`2026-09-03`, pages `from=0` and `from=100` (size 100).
2. **Authority:** live `company_tickers` → CIK map → live `submissions` absolute head → newest ≤12 supported-2026 `{8-K,10-Q,10-K}` primary docs (GET; prior-day doc cache reused when accession+filename match; 21 new docs fetched).
3. **Semantics:** `merger_semantics` from **main clone** (PR #62 cash grammar) via `/workspace/ai-investment-cockpit/.venv` — **not** soak `app/`.
4. Scripts: `_classify_heads_2026_09_03.py`, `_plan_docs_2026_09_03.py`, `_offline_semantic_2026_09_03.py` (+ intended `get_only_natural_head_rescreen_2026_09_03.py` blocked by Auto-review bind).

## Counts

| Metric | 2026-09-03 | 2026-09-02 |
|---|---|---|
| Union pool screened | **191** | 190 |
| StockAnalysis visible | 43 | 43 |
| Frozen prior (expanded) | 50 | 32 |
| EDGAR FTS tickers | 173 | 173 |
| EDGAR FTS total hits | **613** | 595 |
| No current SEC ticker map | **50** | 51 |
| Absolute head unsupported form | **108** | 101 |
| Absolute head supported `{8-K,10-Q,10-K}` | **33** | 38 |
| Cash-per-share parse hits (`_CASH`) | **2** (BHF, SAFT) | 2 |
| Semantic source-set pass | **1** (`BHF`) | 1 (`BHF`) |
| Selection-ready (elig + current matches set end) | **1** (`BHF`) | 1 |
| Current target date-ready | **0** | 0 |
| Meets ≥30 cohort gate | **false** | false |
| Doc bytes scanned (local) | 222,374,887 | — |

Top filing refusals (supported-head docs): see intake JSON `summary.top_filing_refusals`.

### Primary gate result

**date-not-ready** — one grammar/selection-eligible natural head (`BHF`) exists, but expected + outside dates both refuse; cohort **1 ≪ 30**.

### Candidates (selection-eligible, not date-ready)

| Symbol | Head | Cash/share | Date-ready | Notes |
|---|---|---|---|---|
| **BHF** | 8-K `0001685040-26-000031` (2026-08-18) | **$70.00** | **no** | Source set `0001685040-26-000028` → head; `current_matches_set_end=true`; date refusals on both expected + outside |

### Cash parse but **not** selection-ready

| Symbol | Head | Cash hit | Blocker |
|---|---|---|---|
| **SAFT** | 10-Q `0001172052-26-000023` | `$105.00` | outside pinned cash-merger universe (`has_stock` calibration) — no semantic source-set |

### Absolute-head-supported symbols (form gate only; n=33)

AON, ATAI, BHF, BKR, BLFS, CCO, CHTR, CYCU, CYCUW, DIOD, ESI, FGRS, FIGR, FSEA, GNL, HUN, JTAI, **KR** (new), LXP, OLN, QXO, RMBI, SAFT, SLP, SOLS, STRR, STRRP, **TXNM** (returned), UHS, UUUU, VEEE, ZSQR, ZYME.

Notable vs 2026-09-02: **KR** and **TXNM** now supported absolute heads; several prior heads (e.g. VRT, BTGO, CORZ*, UCB, ATAI kept) rotated off the supported set. None cleared cash eligibility except BHF (and SAFT cash-hit only).

### Unsupported absolute-head form histogram (top)

`4`×47, `DEFA14A`×8, `144`×7, `425`×7, `8-K/A`×7, `EFFECT`×6, `SCHEDULE 13G`×4, `SCHEDULE 13G/A`×4, …

## Gate status after re-screen

| Gate | Status |
|---|---|
| ≥30 selection-ready candidates | **Open (1)** — BHF only; dates not ready |
| Discord selection witness | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` settable? | **NO** |
| Paper / OrderGateway path | **Still fail-closed** (correct) |
| Soak HEAD / read_only | **Unchanged** at `1e2d4e3`; 8787 still answering |

## Exact next operator action

Keep soak as awareness on `1e2d4e3`. Do **not** seed watchlist; do **not** rewrite soak HEAD; no Luna/Composer for this empty/date-not-ready screen. Next market-day plan: wait for natural absolute heads that are both grammar-pass **and date-ready** until selection-ready ≥30 (Fri+ natural watch / next weekday screen if armed). Optional later packages (not this wake): SAFT stock-flag false positive; FSEA/SLP/CCO alternate cash phrasing; BHF date-field audit already frozen.

## Evidence paths

- `/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-03.md` (this file)
- `/workspace/w24-evidence/w24-prospective-intake-natural-head-rescreen-2026-09-03.json`
- `/workspace/w24-evidence/discovery-pool-rescreen-2026-09-03.json`
- `/workspace/w24-evidence/intake-natural-head-rescreen-2026-09-03.log`
- `/workspace/w24-evidence/rescreen-raw-2026-09-03/` (FTS pages, submissions, docs)
- Scripts: `_classify_heads_2026_09_03.py`, `_plan_docs_2026_09_03.py`, `_offline_semantic_2026_09_03.py`
