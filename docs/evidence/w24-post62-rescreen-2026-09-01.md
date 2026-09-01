---
title: W-24 post-PR#62 GET-only re-screen (BUG-109 cash-per-share grammar)
type: evidence
status: selection-blocked-partial-unlock
last-verified: 2026-09-01
related:
  - w24-rescreen-zero-ready-2026-09-01.md
  - w24-parser-miss-audit-2026-09-01.md
  - PR #62 / efc626f
soak_sha: 1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4
parser_sha: efc626f18382fc049297520fa863f4d57aa03603
---

# W-24 post-#62 rescreen — 2026-09-01 (~10:04–10:15 PT)

## Claim

| Field | Value |
|---|---|
| Package | `W-24` |
| Action | GET-only re-screen after PR #62 (BUG-109 cash heads) landed on main |
| Date (PT) | 2026-09-01 ~10:04–10:15 PT |
| Date (UTC) | 2026-09-01 ~17:04–17:15Z |
| Parser code | `/workspace/ai-investment-cockpit` **main** `efc626f` (PR #62) |
| Soak SHA (unchanged) | `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4` — **not rewritten**; awareness soak continues on OLD parser |
| Soak tree dirty? | **No** — evidence only under `/workspace/w24-evidence/` |
| Selection commitment | **Not attempted** |
| Discord witness | **Not posted** |
| Orders | **None** |
| `COCKPIT_MERGER_WATCHLIST` | **remains empty / not seeded** |

## Method

Reuse prior afternoon rescreen discovery + prefetched docs (no new SEC writes to soak):

1. Union pool + head-classify from `/workspace/w24-evidence/discovery-pool-rescreen-v2-2026-09-01.json` and `rescreen-raw/head-classify.json` (189 pool / 38 supported absolute heads).
2. Offline semantic pass over `/workspace/w24-evidence/rescreen-raw/docs/` (440 primary docs) using **UPDATED** `merger_semantics` from cockpit main (`efc626f`), via `/workspace/ai-investment-cockpit/.venv`.
3. Selection-ready = semantic eligibility OK on combined source-set **and** `current_matches_set_end` (absolute head is end of chrono source set).
4. Explicitly **did not** import soak `app/` for this pass (soak stays at OLD grammar for ongoing awareness).

Script: `/workspace/w24-evidence/offline_semantic_rescreen_post62.py`

## Counts

| Metric | Post-#62 | Prior afternoon (old grammar) |
|---|---|---|
| Union pool screened | **189** | 189 |
| No current SEC ticker map | 51 | 51 |
| Absolute head unsupported form | 100 | 100 |
| Absolute head supported | **38** | 38 |
| Semantic source-set pass | **1** (`BHF`) | 0 |
| Selection-ready (elig + current matches set end) | **1** (`BHF`) | 0 |
| Current target date-ready | **0** | 0 |
| Meets ≥30 cohort gate | **false** | false |

Top filing refusals (supported-head docs, 440 filings):

| Refusal | Count |
|---|---|
| `SEC source does not state one cash-per-share value` | 436 |
| `ok` (eligibility pass on individual filing) | 2 |
| `calibration row is outside the pinned cash-merger strategy universe` | 2 |

## Newly selection-ready candidates

### BHF — selection-ready under fixed grammar

| Field | Value |
|---|---|
| Symbol | **BHF** |
| Absolute head | 8-K `0001685040-26-000031` (2026-08-18) |
| Source accession (first elig pass) | 10-Q `0001685040-26-000028` |
| Source set (chrono → current) | `0001685040-26-000028` → `0001685040-26-000031` |
| `current_matches_set_end` | **true** |
| Cash offer | **$70.00** |
| Label | common_equity / US / nasdaq / definitive / active; no CVR/earnout/stock |
| Dates | **not ready** — expected resolution + outside date both unambiguous-refuse |
| Also elig OK | 10-Q `0001685040-26-000019` ($70); 10-K `0001685040-26-000011` matches cash but fails calibration (`has_stock`/universe) |

Grammar unlock matches BUG-109 claim: `each share of our common stock` + `$ 70.00 per share, net in cash` now accepted by `_CASH`.

## Focus symbols that did **not** become selection-ready

| Symbol | Cash grammar after #62 | Why not selection-ready |
|---|---|---|
| **SAFT** | Absolute-head 10-Q `0001172052-26-000023` now hits `_CASH` → `105.00` | Eligibility fails: **`has_stock=True`** → `calibration row is outside the pinned cash-merger strategy universe`. Announce 8-Ks still miss cash grammar (`for $105.00 per share, in cash` / award-cancel phrasing). |
| FSEA | Still 0 `_CASH` hits | Announce phrasing `each share of company common stock … $17.25 in cash` (no `of the/our` + `per share in cash` collocated form) |
| SLP | Still 0 `_CASH` hits | `$18.50 in cash` without matching HEAD/TAIL |
| CCO | Still 0 `_CASH` hits | `converted into the right to receive cash in an amount equal to $2.43` (alternate phrasing) |

## Gate status

| Gate | Status |
|---|---|
| ≥30 selection-ready candidates | **Open (1)** — BHF only |
| Admin filing→selection path | **Do not run** — cohort far below 30 |
| Discord witness | **Not posted** |
| Merger watchlist | **Must stay empty** |
| Orders | **None** |
| Soak process code | Remains OLD parser at `1e2d4e3` |

## Exact next operator action

Keep waiting for natural absolute heads / additional grammar-pass names until selection-ready ≥30, **or** decide a separate package for non-cash-grammar gaps (stock-flag false positives on SAFT; FSEA/SLP/CCO alternate phrasing). Do **not** seed `COCKPIT_MERGER_WATCHLIST`; do **not** rewrite soak HEAD.

## Evidence paths

- `/workspace/w24-evidence/w24-post62-rescreen-2026-09-01.md` (this note)
- `/workspace/w24-evidence/w24-prospective-intake-post62-rescreen-2026-09-01.json`
- `/workspace/w24-evidence/offline_semantic_rescreen_post62.py`
- Reused: `discovery-pool-rescreen-v2-2026-09-01.json`, `rescreen-raw/head-classify.json`, `rescreen-raw/docs/`
