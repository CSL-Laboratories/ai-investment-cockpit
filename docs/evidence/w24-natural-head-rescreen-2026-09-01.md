---
title: W-24 natural-head GET-only re-screen — 2026-09-01 afternoon PT
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-01
related:
  - REGISTER (W-24 / W-23)
  - evidence/w24-prospective-cohort-intake-2026-09-01
  - runbooks/w24-merger-semantic-verifier
---

# W-24 natural-head GET-only re-screen — 2026-09-01

## Claim

| Field | Value |
|---|---|
| Package | `W-24` |
| Claim action | Afternoon **natural-head GET-only re-screen** (no selection) |
| Claimant | Grok Bot executor (on behalf of Lucas Guerra soak ops) |
| Date (PT) | 2026-09-01 ~09:22–09:25 PT |
| Date (UTC) | 2026-09-01 16:22–16:25Z |
| Soak SHA (unchanged) | `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4` |
| Soak tree dirty? | **No** — evidence only under `/workspace/w24-evidence/` |
| Selection commitment | **Not attempted** |
| Discord selection message | **Not posted** (selection-ready count = 0 ≪ 30) |
| `COCKPIT_MERGER_WATCHLIST` | **remains empty** |

## Scope / constraints honored

- GET-only against live SEC (company_tickers + submissions + primary docs).
- Absolute head required in `{8-K, 10-Q, 10-K}` for “supported” count.
- No forge, no soak DB/raw writes, no merger watchlist seed, no Discord witness.
- Discovery lists (StockAnalysis ∪ frozen prior ∪ EDGAR FTS) are **not** authority.

## Prior stall

Prior attempt left an empty `status-raw-rescreen-2026-09-01.json.tmp` and a hung `status_probe.py` (CryptFile / SecretService hang without `run_with_cryptfile`). Hung probe was killed. Midday authenticated `/status` re-probe via CryptFile was Auto-review blocked in this executor; live API still answers `401` without key on `127.0.0.1:8787` (process left running from morning). Morning `status-raw.json` remains the last authenticated snapshot (`read_only`, merger watchlist empty).

## What was run

| Step | Result |
|---|---|
| Soak SHA check | `1e2d4e3` — unchanged, not rewritten |
| `get_only_intake_screen_rescreen.py` | Completed → JSON below |
| `provision_merger_edge.py selection` | **Not run** |
| Discord selection post | **Not run** |
| Merger watchlist change | **Not run** |

## Counts

| Metric | Value |
|---|---|
| Discovery union pool | **122** |
| StockAnalysis visible | 43 |
| Frozen prior | 19 |
| EDGAR FTS tickers (extracted) | 96 |
| EDGAR FTS total hits (8-K cash-merger query) | 590 |
| No current SEC ticker map | **46** |
| Absolute head unsupported form | **53** |
| Absolute head supported `{8-K,10-Q,10-K}` | **15** |
| Semantic source-set pass | **0** |
| Current-target date pass (expected + outside) | **0** |
| Selection-ready (≥30 gate) | **empty — 0** |
| `meets_min_30_gate` | false |
| Bytes received (SEC) | 134,340,417 |
| Top filing refusal | “SEC source does not state one cash-per-share value” × 165 |

### Selection-ready list

**empty**

### Absolute-head-supported symbols (not selection-ready)

These cleared the natural-head form gate only; none passed eligibility / date parsers under current_accession source-set rules.

| Symbol | Head form | Filing date | Accession | 2026 supported filings | Docs fetched (≤12) |
|---|---|---|---|---|---|
| AIRE | 8-K | 2026-08-25 | `0001213900-26-093535` | 16 | 12 |
| AON | 8-K | 2026-08-31 | `0001193125-26-375328` | 11 | 11 |
| ATAI | 8-K | 2026-08-31 | `0001140361-26-035091` | 14 | 12 |
| BHF | 8-K | 2026-08-18 | `0001685040-26-000031` | 9 | 9 |
| BTGO | 8-K | 2026-08-28 | `0001740604-26-000056` | 13 | 12 |
| CCO | 10-Q | 2026-08-05 | `0001334978-26-000025` | 12 | 12 |
| DIOD | 8-K | 2026-08-27 | `0001193125-26-371294` | 18 | 12 |
| FGRS | 8-K | 2026-09-01 | `0001493152-26-040931` | 17 | 12 |
| FIGR | 8-K | 2026-09-01 | `0001493152-26-040931` | 17 | 12 |
| FSEA | 8-K | 2026-08-27 | `0000943374-26-000392` | 5 | 5 |
| GNL | 8-K | 2026-08-13 | `0001104659-26-095323` | 28 | 12 |
| JTAI | 10-Q | 2026-08-14 | `0001493152-26-038333` | 21 | 12 |
| LXP | 8-K | 2026-07-29 | `0000910108-26-000047` | 10 | 10 |
| RMBI | 8-K | 2026-08-20 | `0001628280-26-058282` | 13 | 12 |
| SAFT | 10-Q | 2026-08-07 | `0001172052-26-000023` | 10 | 10 |

### Unsupported absolute-head form histogram (top)

`4`×24, `SC 13E3`×3, `DEFA14A`×3, `EFFECT`×3, `425`×3, `SCHEDULE 13G/A`×3, `144`×2, `SCHEDULE 13D/A`×2, plus singles (`25-NSE`, `S-8 POS`, `D`, `POS AM`, `SC TO-T/A`, `PREM14A`, `S-4`, …).

## Gate status after re-screen

| Gate | Status |
|---|---|
| ≥30 selection-ready candidates | **Open** (0) |
| Discord selection witness | **Not posted** |
| `COCKPIT_MERGER_WATCHLIST` settable? | **NO** |
| Paper / OrderGateway path | **Still fail-closed** (correct) |

Same conclusion as morning intake: natural supported heads exist, but none clear cash-per-share eligibility + exact dates with `current_accession` ending the source set. Fail-closed.

## Evidence paths

- `/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-01.md` (this file)
- `/workspace/w24-evidence/w24-prospective-intake-rescreen-2026-09-01.json`
- `/workspace/w24-evidence/discovery-pool-rescreen-2026-09-01.json`
- `/workspace/w24-evidence/edgar-fts-page0-rescreen.json` (written by script)
- `/workspace/w24-evidence/intake-rescreen-run.log`
- Script: `get_only_intake_screen_rescreen.py`
