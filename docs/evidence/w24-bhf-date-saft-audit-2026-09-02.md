---
title: W-24 BHF date-refuse + SAFT/alternate-cash audit (burn window)
type: evidence
status: date-absent-saft-stock-fp-alts-refuse-correct-under-pin
last-verified: 2026-09-02
related:
  - w24-bhf-date-audit-2026-09-01.md
  - w24-natural-head-rescreen-2026-09-02.md
  - w24-post62-rescreen-2026-09-01.md
  - w24-parser-miss-audit-2026-09-01.md
  - PR #62 / efc626f (cash heads only)
soak_sha: 1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4
parser_sha_pr62: efc626f18382fc049297520fa863f4d57aa03603
burn_window: Grok-Bot until ~1:17pm PT 2026-09-02
---

# W-24 BHF date + SAFT / alt-cash audit — 2026-09-02 (~11:17–11:25 PT)

Local investigation only (no Cursor agent CLI, no Codex Luna, no CloudAgent, no gh PR opens). Soak not restarted/rewritten.

## RESULT — Soak health

| Check | Value |
|---|---|
| `/workspace/cockpit-soak` HEAD | `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4` (abbrev `1e2d4e3c`) |
| Tip message | `Merge pull request #59 from NotSkittlz/work/bug113-durable-watch-only-generation` |
| `127.0.0.1:8787` | **LISTEN** (`python` pid present); GET `/status` → `401` without key |
| Action this burn | **None** — confirm only; no restart, no soak rewrite |

## RESULT — BHF date refuse (elig + current_matches OK)

### Selection posture (from Sep-2 natural-head rescreen + live re-derive)

| Field | Value |
|---|---|
| Symbol | **BHF** |
| Absolute head | 8-K `0001685040-26-000031` (filingDate **2026-08-18**, `bhf-20260818.htm`) |
| Source accession (first elig) | 10-Q `0001685040-26-000028` (filingDate **2026-08-06**, `bhf-20260630.htm`) |
| Source set | `0001685040-26-000028` → `0001685040-26-000031` |
| `current_matches_set_end` | **true** |
| Cash | **$70.00** (post-#62 `_CASH`) |
| Eligibility | **ok** (`has_stock=false`, definitive/active, US nasdaq common) |
| Date-ready | **no** |

### Pinned date gates (`app/strategies/merger_semantics.py` @ `efc626f` / main with #62)

Used by `_verify_deal` after eligibility:

- `_EXPECTED_DATE`: `expected (resolution\|closing) date (:\|is) <DATE>` **or** `expected to (close\|be completed) (on\|by) <DATE>`
- `_OUTSIDE_DATE`: `outside date (:\|is\|of) <DATE>`
- `_unique_match`: exactly **one** distinct date value per field, else refuse  
  → `"SEC source does not state one unambiguous expected resolution date"` / `"… outside date"`

There is **no** separate “today vs date” selection gate that fails BHF after parse: dates never parse, so `expected_resolution_date` / `outside_date` stay **null**. `merger_edge` later requires those document fields as real dates for calibrated deals; nulls never become ready by wall-clock alone.

### Live regex on combined source text (028 + 031), today **2026-09-02**

| Probe | Result |
|---|---|
| `_EXPECTED_DATE.findall(combined)` | **[]** |
| `_OUTSIDE_DATE.findall(combined)` | **[]** |
| Literal `outside date` | **0** |
| Literal `expected closing/resolution date` | **0** |
| Literal `expected to close` | **1** (year-only) |

**Concrete near-miss calendar language in 10-Q 028 (not pinned):**

> … if the Merger has not closed by **September 6, 2026**, because the remaining insurance regulatory approvals have not yet been obtained, the Merger Agreement will be automatically extended to **December 6, 2026**. The Merger is expected to close in **2026**.

| Near-miss phrase | Parsed date | Days from 2026-09-02 | Why refuse under pin |
|---|---|---|---|
| conditional “not closed by September 6, 2026” | 2026-09-06 | **+4** | Not labeled `outside date :\|is\|of`; also paired with Dec 6 → would fail `_unique_match` even if stretched |
| conditional “automatically extended to December 6, 2026” | 2026-12-06 | **+95** | Same — extension trigger, not `_OUTSIDE_DATE` |
| “expected to close in 2026” | year-only | n/a | Missing required `(on\|by) <calendar DATE>` for `_EXPECTED_DATE` |

Absolute-head 8-K **031** is officer-appointment (Item 5.02); no merger consideration / outside / expected-close calendar. Employment dates around Sep 2–3, 2026 are unrelated.

### Will BHF naturally become date-ready without forging?

**No — not by calendar passage of Sep 6 or Dec 6.** Waiting until those dates does not insert the pinned labels into source text. Natural unlock requires a **future SEC primary** in the supported form set that states, uniquely:

1. one `_EXPECTED_DATE` calendar date, **and**
2. one `_OUTSIDE_DATE` calendar date,

on a combined source set that still keeps elig + `current_matches_set_end`.

**ETA:** unknown / not knowable from current filings. Conditional outside-ish language already exists but is intentionally fail-closed. Widening date grammar to swallow “not closed by / auto-extended to / expected to close in YYYY” would be a **new semantics package**, not a BUG-109-class extractable miss.

```
CLAIMED_DATE_PACKAGE: no
NATURAL_DATE_READY_BY_WALLCLOCK: no
ETA: unknown (needs new labeled filing language)
```

## RESULT — SAFT (not selection-ready)

| Field | Value |
|---|---|
| Absolute head | 10-Q `0001172052-26-000023` (2026-08-07) |
| `_CASH` after #62 | **hits `$105.00`** on absolute head |
| Blocker | `has_stock=True` → `_require_strategy_eligible` → **`calibration row is outside the pinned cash-merger strategy universe`** |
| Semantic source-set | **none** (not selection-ready) |

**Stock flag mechanism (live):** `_STOCK_CONSIDERATION` matches repurchase boilerplate, not merger stock consideration:

> … as of june 30, 2026 and december 31, 2025, the company had purchased **3,478,060 shares of common stock** …

Match path: `(?:receive|plus|and)[^.]{0,100}<number>\s+shares?\s+of\s+…stock` — false positive on “and … purchased 3,478,060 shares of common stock”.

Announce 8-Ks still miss pinned `_CASH` (e.g. `0001104659-26-086488`: “right to receive **$105.00 in cash**” without HEAD/TAIL `each/every share of … common stock` … `per share … in cash` collocation).

**Classification:** selection refuse is **correct under current pin** (universe gate). Underlying cash extract works; the stock FP is a **bounded secondary defect** (documented since parser-miss audit), not a reason to seed watchlist.

## RESULT — FSEA / SLP / CCO alternate cash phrasing

All present in Sep-2 supported absolute-head set; **0** pinned `_CASH` hits on inspected primaries (`ANY_PINNED_CASH=False`).

| Symbol | Real cash language in window | Why pinned `_CASH` refuses | Miss vs refuse-correct |
|---|---|---|---|
| **FSEA** | Announce 8-K `0001193125-26-206551` (+ 10-Qs): “each share of **company** common stock … right to receive **$17.25 in cash**” | Needs `(the\|our)? (company\|target)?'s?` + verb path + **`per share … in cash` / `in cash per share`** tail; here cash is `$17.25 in cash` without collocated `per share` tail after HEAD | **Real-world cash deal; refuse-correct under pin** (alternate phrasing / TAIL gap — same family as pre-#62 secondary misses, not a silent extractable HEAD already accepted) |
| **SLP** | Announce 8-K `0001023459-26-000024`: “right to receive **$18.50 in cash**” | Same missing collocated `per share` TAIL / HEAD binding | Same: **refuse-correct under pin** |
| **CCO** | 8-K/10-Qs: “converted into the right to receive **cash in an amount equal to $2.43**”; also narrative “stockholders receiving **$2.43 per share in cash**” without `(each\|every) share of … common stock` + `(will\|shall) …` chain | Farther structure than FSEA/SLP; subject-switch / HEAD requirements block | Same: **refuse-correct under pin** (would need broader alternate-phrasing package) |

Fail-closed: **do not** forge edges, **do not** seed `COCKPIT_MERGER_WATCHLIST`, **do not** treat these as selection-ready.

## RESULT — Safe next code package worth claiming later?

| Candidate | Claim now? | Notes |
|---|---|---|
| BHF date grammar widen | **No** | Phrases absent; stretching conditional/year-only is new semantics, not extractable miss |
| SAFT `_STOCK_CONSIDERATION` repurchase FP | **Maybe later (bounded)** | Cash already extracts; FP is reproducible on absolute head; needs careful fail-closed tests so true stock deals still refuse |
| FSEA/SLP `$X in cash` without `per share` collocation | **Maybe later (larger, #62-class)** | Real cash; explicit alternate TAIL — only if product wants that universe expansion |
| CCO `cash in an amount equal to $X` / narrative stockholders receiving | **Maybe later (larger still)** | Farther from pin; higher over-accept risk |
| Watchlist seed / forge / soak rewrite | **Never** | Fail-closed |

**Burn-window recommendation:** **none claimed.** Keep waiting for natural absolute heads that are both grammar-eligible and **date-labeled** until selection-ready ≥30. Cohort remains **1 ≪ 30** (BHF only, dates null).

## Evidence paths

- This note: `/workspace/w24-evidence/w24-bhf-date-saft-audit-2026-09-02.md`
- Prior: `w24-bhf-date-audit-2026-09-01.md`, `w24-natural-head-rescreen-2026-09-02.md`, `w24-post62-rescreen-2026-09-01.md`, `w24-parser-miss-audit-2026-09-01.md`
- JSON: `w24-prospective-intake-natural-head-rescreen-2026-09-02.json`
- Filings: `rescreen-raw/docs/BHF__*028*`, `BHF__*031*`, `SAFT__*023*`, FSEA/SLP/CCO primaries under same tree
- Grammar: `/workspace/ai-investment-cockpit/app/strategies/merger_semantics.py` (`_EXPECTED_DATE`, `_OUTSIDE_DATE`, `_CASH`, `_STOCK_CONSIDERATION`, `_verify_deal`)
