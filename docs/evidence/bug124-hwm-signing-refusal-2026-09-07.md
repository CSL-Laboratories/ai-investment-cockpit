---
title: BUG-124 — refuse changed audit high-water before signing
type: evidence
status: claimed-repair
last-verified: 2026-09-07
---

# BUG-124 — refuse changed audit high-water before signing

RESULT: bounded fail-closed repair on the SEC-11 audit-spine signing boundary.

This is **not** a reopen of `BUG-114` / PR #68 (allocation HWM-before-head) and **not**
`BUG-118` / PR #70 (`result()` snapshot pin). Those remain fixed. No W-24 selection
bar is loosened.

Authoritative claim shape: [[astra-hwm-resign-claim-shape-2026-09-06]] (docs PR #72
freeze; Astra miss 13 in [[astra-drain-2026-09-06/misses-beyond-68]]).

## Contract

An append that observed an established signed high-water must reject a missing or
invalid reread at the final signing boundary, preserve the damaged `audit_meta`
bytes, and let the exception-propagating transaction roll back the attempted
audit/state writes. Missing authority may initialize only a genuine first
watermark. Invalid authority always refuses. Legitimate signed advancement stays
monotonic.

## Repair

`AuditLog._insert` now passes whether preflight observed established authority into
`_bump_hwm`. After the entry flush and before any metadata DELETE/INSERT/signing,
the reread is classified:

- signature invalid (`-1`) → `AuditIntegrityError`, metadata left in place
- missing after established authority → `AuditIntegrityError`, metadata left in place
- missing on genuine first initialization → create and sign `seq`
- valid → `max(current, seq)` and re-sign

`append_in` still does not acquire its own commit. SQLite busy/collision retry and
the #68/#70 protections are unchanged.

## Out of scope (not claimed)

Erasure-floor poison (miss 2/8), Order FSM F1/F2, watch_only D1–D4, schema
migration, signer/key lifecycle, transaction-manager rewrite, floor repair,
breaker recovery, gateway, selection loosen, soak rewrite.
