---
title: W-24 natural-head GET-only re-screen — 2026-09-04 (Friday cron / hand-run primary)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-04
related:
  - REGISTER (W-24 / W-23)
  - evidence/w24-natural-head-rescreen-2026-09-03
  - evidence/w24-natural-head-rescreen-2026-09-02
  - evidence/w24-natural-head-rescreen-2026-09-01
  - wake fri-w-24-natural-head-rescreen
soak_sha: 61ca0af27dc903ff97237b3cc2c8e83c735ebb09
parser_sha_pr62: efc626f18382fc049297520fa863f4d57aa03603
---

# W-24 natural-head GET-only re-screen — 2026-09-04

## Claim

| Field | Value |
|---|---|
| Package | `W-24` |
| Claim action | Friday cron / hand-run primary **natural-head GET-only re-screen** (`fri-w-24-natural-head-rescreen`) |
| Claimant | Cockpit Admin automation (curl SEC intake + offline #62 grammar) |
| Date (PT) | 2026-09-04 ~05:31–05:36 PT |
| Date (UTC) | 2026-09-04 12:31–12:36Z |
| Parser code | `/workspace/ai-investment-cockpit` with PR **#62** `merger_semantics` (`efc626f`) |
| Main HEAD | `61ca0af27dc903ff97237b3cc2c8e83c735ebb09` (post-#68; same pin as soak) |
| Soak SHA (unchanged) | `61ca0af27dc903ff97237b3cc2c8e83c735ebb09` |
| Soak pin note | Already pinned post-#68 on 2026-09-03 — **unchanged this run** |
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
- Auto-review-safe path: intake used `curl`; semantics ran local-only offline via `/workspace/ai-investment-cockpit/.venv`.

## Scan method

Same path as 2026-09-01 / 2026-09-02 / 2026-09-03 natural-head / post-#62 rescreens:

1. **Discovery (not authority):** StockAnalysis frozen list ∪ prior frozen names (expanded with Sep 3 supported: **KR, TXNM, BHF, SAFT, CYCUW, STRRP**, …) ∪ EDGAR FTS  
   `"Agreement and Plan of Merger" AND "in cash"`, forms=`8-K`, `2026-06-01`..`2026-09-04`, pages `from=0` and `from=100` (size 100).
2. **Authority:** live `company_tickers` → CIK map → live `submissions` absolute head → newest ≤12 supported-2026 `{8-K,10-Q,10-K}` primary docs (GET; prior-day doc cache reused when accession+filename match; **12** new docs fetched — all **USAR**).
3. **Semantics:** `merger_semantics` from **main clone** (PR #62 cash grammar) via `/workspace/ai-investment-cockpit/.venv` — **not** soak `app/`.
4. Scripts: `_classify_heads_2026_09_04.py`, `_plan_docs_2026_09_04.py`, `_offline_semantic_2026_09_04.py`, `_build_discovery_2026_09_04.py` (+ `get_only_natural_head_rescreen_2026_09_04.py` kept as full-pipeline reference; curl path used).

## Counts

| Metric | 2026-09-04 | 2026-09-03 |
|---|---|---|
| Union pool screened | **192** | 191 |
| StockAnalysis visible | 43 | 43 |
| Frozen prior (expanded) | 53 | 50 |
| EDGAR FTS tickers | 174 | 173 |
| EDGAR FTS total hits | **617** | 613 |
| No current SEC ticker map | **50** | 50 |
| Absolute head unsupported form | **112** | 108 |
| Absolute head supported `{8-K,10-Q,10-K}` | **30** | 33 |
| Cash-per-share parse hits (`_CASH`) | **0** | 2 (BHF, SAFT) |
| Semantic source-set pass | **0** | 1 (`BHF`) |
| Selection-ready (elig + current matches set end) | **0** | 1 |
| Current target date-ready | **0** | 0 |
| Meets ≥30 cohort gate | **false** | false |
| Doc bytes scanned (local) | 183,560,177 | 222,374,887 |

Top filing refusals (supported-head docs): `SEC source does not state one cash-per-share value` ×347.

### Primary gate result

**empty / date-not-ready** — **zero** grammar/selection-eligible natural heads; prior `BHF` lost supported absolute head (now Form `3`); cohort **0 ≪ 30**.

### Candidates (selection-eligible, not date-ready)

_None._

### Cash parse but **not** selection-ready

_None today._ (Sep 3: SAFT `$105` cash-hit only — now absolute head `PREM14A`, outside supported set.)

### BHF / SAFT status (vs Sep 3)

| Symbol | Sep 3 | Sep 4 absolute head | Gate impact |
|---|---|---|---|
| **BHF** | selection-ready 8-K `$70`; dates refused | Form **`3`** `0001628280-26-060470` (2026-09-03) | **Lost** supported head → not selection-ready |
| **SAFT** | cash-hit `$105` on 10-Q; not selection-ready | Form **`PREM14A`** `0001104659-26-105083` (2026-09-03) | **Lost** supported head → no cash screen on head set |

### Absolute-head-supported symbols (form gate only; n=30)

ATAI, BKR, BLFS, CCO, CYCU, CYCUW, DIOD, ESI, FGRS, FIGR, FSEA, GNL, HUN, JTAI, KR, LXP, OLN, QXO, RMBI, SLP, SOLS, STRR, STRRP, TXNM, UHS, **USAR** (new), UUUU, VEEE, ZSQR, ZYME.

Notable vs 2026-09-03: **USAR** newly supported (fresh 8-K 2026-09-04); **AON, BHF, CHTR, SAFT** rotated off supported absolute heads. No symbol cleared cash eligibility.

### Unsupported absolute-head form histogram (top)

`4`×49, `144`×10, `425`×7, `DEFA14A`×6, `SCHEDULE 13D/A`×5, `EFFECT`×5, `SCHEDULE 13G`×4, `PREM14A`×4, …

## Gate status after re-screen

| Gate | Status |
|---|---|
| ≥30 selection-ready candidates | **Open (0)** — empty; dates N/A |
| Discord selection witness | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` settable? | **NO** |
| Paper / OrderGateway path | **Still fail-closed** (correct) |
| Soak HEAD / read_only | **Unchanged** at `61ca0af` (post-#68 pin); 8787 still answering |

## Exact next operator action

Keep soak as awareness on `61ca0af` (already pinned post-#68). Do **not** seed watchlist; do **not** rewrite soak HEAD; no Luna/Composer for this empty/date-not-ready screen. Next market-day plan: **Monday 2026-09-07** natural-head GET-only re-screen (weekend — no Sat/Sun selection claim). Wait for natural absolute heads that are both grammar-pass **and date-ready** until selection-ready ≥30. Optional later packages (not this wake): BHF Form-3 head rotation / date-field audit already frozen; SAFT PREM14A vs 10-Q cash extract; USAR cash-phrasing probe if FTS/cash grammar warrants.

## Evidence paths

- `/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-04.md` (this file)
- `/workspace/w24-evidence/w24-prospective-intake-natural-head-rescreen-2026-09-04.json`
- `/workspace/w24-evidence/discovery-pool-rescreen-2026-09-04.json`
- `/workspace/w24-evidence/intake-natural-head-rescreen-2026-09-04.log`
- `/workspace/w24-evidence/rescreen-raw-2026-09-04/` (FTS pages, submissions, docs, head-classify.json, doc-fetch-plan.json)
- Scripts: `_classify_heads_2026_09_04.py`, `_plan_docs_2026_09_04.py`, `_offline_semantic_2026_09_04.py`, `_build_discovery_2026_09_04.py`, `get_only_natural_head_rescreen_2026_09_04.py`
