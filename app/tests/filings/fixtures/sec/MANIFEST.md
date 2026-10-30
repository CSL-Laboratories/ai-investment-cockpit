---
title: SEC fixture manifest — Week-1 capture
type: reference
status: captured
last-verified: 2026-07-08
related: []
---

# SEC fixture manifest

Captured 2026-07-08 by Claude (curl, declared User-Agent with a personal contact
— redacted 2026-10-29, CSL-579 — per SEC fair-access policy, sequential requests ≥1s
apart). The fixtures were not re-captured. Replay tests now declare the CSL contact
alias `contact@csl-laboratories.com` via `SEC_TEST_USER_AGENT` in
`app/tests/filings/_sec_replay.py` (override: `COCKPIT_TEST_SEC_USER_AGENT`). Raw bytes, unmodified — the WP4 offset
round-trip acceptance test depends on these files never being re-saved or reformatted.
`sha256` column is the first 16 hex chars of the file's SHA-256, pinning the bytes.

Satisfies the scaffold's Week-1 task: **≥3 real filing pairs spanning ≥2 registrants'
formats** (NVDA self-filed 10-Qs/8-Ks + AAPL 10-Qs) plus recorded
`submissions`/`companyfacts` API responses.

## Pair 1 — NVDA consecutive 10-Qs

| file | form | accession | period | filed | sha256 |
|---|---|---|---|---|---|
| `NVDA/0001045810-26-000052/nvda-20260426.htm` | 10-Q | 0001045810-26-000052 | 2026-04-26 | 2026-05-20 | `1b5de37b973da4a3` |
| `NVDA/0001045810-25-000230/nvda-20251026.htm` | 10-Q | 0001045810-25-000230 | 2025-10-26 | 2025-11-19 | `8a40e602a6f5d617` |

Consecutive *same-form* filings (a 10-K sits between them chronologically —
that is the delta engine's real-world "prior same-form filing for this CIK" case).

## Pair 2 — AAPL consecutive 10-Qs (second registrant format)

| file | form | accession | period | filed | sha256 |
|---|---|---|---|---|---|
| `AAPL/0000320193-26-000013/aapl-20260328.htm` | 10-Q | 0000320193-26-000013 | 2026-03-28 | 2026-05-01 | `bb90060d61a273e5` |
| `AAPL/0000320193-26-000006/aapl-20251227.htm` | 10-Q | 0000320193-26-000006 | 2025-12-27 | 2026-01-30 | `3a3d9336a9244884` |

## Pair 3 — NVDA consecutive 8-Ks

| file | form | accession | event date | filed | sha256 |
|---|---|---|---|---|---|
| `NVDA/0001045810-26-000060/nvda-20260628.htm` | 8-K (Item 5.02) | 0001045810-26-000060 | 2026-06-28 | 2026-07-02 | `986414551c74fb51` |
| `NVDA/0001045810-26-000056/nvda-20260624.htm` | 8-K (Item 5.07) | 0001045810-26-000056 | 2026-06-24 | 2026-06-30 | `7db2e4095ae87975` |

## Recorded API responses

| file | endpoint | entity | sha256 |
|---|---|---|---|
| `NVDA/submissions.json` | `data.sec.gov/submissions/CIK0001045810.json` | NVIDIA CORP | `d5b3b259147adc1b` |
| `NVDA/companyfacts.json` | `data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json` | NVIDIA CORP | `93edcffe24f55d08` |
| `AAPL/submissions.json` | `data.sec.gov/submissions/CIK0000320193.json` | Apple Inc. | `ea2aa552e984a29e` |
| `AAPL/companyfacts.json` | `data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` | Apple Inc. | `31f9ab4398402faa` |

Document source URLs follow the pattern
`https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/<filename>`
(raw Archives paths — never the `/ix?doc=` viewer).

## Rules for consumers

- **Never modify, re-save, or reformat these files** — offsets are cited against
  these exact bytes. Adversarial/injection variants (WP6) are made from *copies*.
- Error fixtures (403/429/timeout/malformed) are synthesized by build agents, not
  captured here.
- Hand-verified expected delta sets for the WP4 golden tests live alongside the
  tests, not in this directory.
