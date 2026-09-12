---
title: W-24 natural-head GET-only screen — 2026-09-12 (operator board summary)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-12
related:
  - REGISTER (W-24 / W-23 / RW-31)
  - evidence/w24-natural-head-rescreen-2026-09-09
claimant: Grok Bot (owner-operated soak / operator box)
operator_main_head_reported: 885d33a
operator_main_head: 885d33a76b3cb9ab7a507e56f4a1d3f993e59b83
raw_packet_status: cited_summary_only
---

# W-24 natural-head GET-only screen — 2026-09-12

This is an in-repo **board summary** of the Grok Bot GET-only natural-head
screen posted 2026-09-12. It is **not** a replica of the 2026-09-09
evidence note. Full markdown/JSON/raw packets live on the soak host
(`/workspace/w24-evidence/…`) and are **not** in this clone. Do not
invent soak SHA, PT timestamps, FTS hit counts, JSON hashes, or a
supported-head symbol list for this date.

## Claim

| Field | Value |
|---|---|
| Package | `W-24` (counts) / `RW-31` (in-repo ledger) |
| Claim action | Fresh GET-only natural-head screen, same method as 2026-09-09 |
| Claimant | Grok Bot / operator board |
| Operator `main` tip | `885d33a` (resolved in this clone to `885d33a76b3cb9ab7a507e56f4a1d3f993e59b83`) |
| Parser / method | Same GET-only SEC path as [2026-09-09](w24-natural-head-rescreen-2026-09-09.md); parser from **main clone**, not soak `app/` |
| Selection commitment | **Not attempted** (board) |
| Discord selection message | **Not posted** (board) |
| `COCKPIT_MERGER_WATCHLIST` | **Not seeded** (board) |
| Soak HEAD rewrite | **Not rewritten** (board) |
| Raw packet status | `cited_summary_only` |

## Counts (board)

| Metric | 2026-09-12 (board) | 2026-09-09 (checked-in note) |
|---|---|---|
| Union pool screened | **197** | 195 |
| Absolute head supported `{8-K,10-Q,10-K}` | **32** | 32 |
| Cash-per-share parse hits | **0** | 0 |
| Selection-ready | **0** | 0 |
| Date-ready | **0** | 0 |
| Meets ≥30 cohort gate | **false** | false |

Supported-head identities for 2026-09-12 were **not** on the board.
Do not copy the 2026-09-09 32-name list forward as if it were this
screen's set.

### Primary gate result

**NO PASS / empty / date-not-ready** — cash 0, selection-ready 0,
date-ready 0, cohort **0 ≪ 30**. Empty is honest. Do **not** lower the
30-identity gate.

## What this note does not contain

- Soak SHA (not on the board)
- PT / UTC run window (not on the board)
- StockAnalysis / frozen-prior / EDGAR FTS breakdowns
- Absolute-head form histogram
- Supported-head ticker list
- Hashes of live JSON or raw docs

If Grok Bot later commits the full JSON/raw packet hashes, a follow-up
claim may pin them. This note must not pretend they are in git now.

## Exact next operator action

Keep GET-only natural-head screens. Do **not** seed
`COCKPIT_MERGER_WATCHLIST`, post Discord selection, rewrite soak HEAD, or
`/paper approve`. Wait until selection-ready ≥30 **and** date-ready.
The 32 supported heads with cash 0 are a **candidate extraction-refusal
set**, not a cash-merger strategy cohort. Labelling is **not**
authorized by RW-31.

## Evidence paths

- This file (board summary only)
- Checked-in prior full note: [w24-natural-head-rescreen-2026-09-09.md](w24-natural-head-rescreen-2026-09-09.md)
- Live (not in this clone): `/workspace/w24-evidence/` on the soak host
