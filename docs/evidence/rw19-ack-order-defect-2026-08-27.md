---
title: RW-19 subscription-ack order defect and NVIDIA-window aborted attempts
type: evidence
status: live
last-verified: 2026-08-27
---

# RW-19 subscription-ack order defect — 2026-08-27

## Scope and safety baseline

Independently re-verified 2026-08-27 by a four-agent audit; artifact hashes below were
recomputed from disk. This records two aborted plan-bound news-reconnect attempts and the
collector defects they exposed. It grants no strategy, mode, paper-order, live-order, or
autonomous authority.

Explicit posture for every statement in this document:

- `authority: none`
- `rw19_gate: OPEN_NOT_CLOSED`
- `dec07_status: open`
- no retry occurred after either abort
- both external evidence directories are write-once; their artifact hashes recomputed on
  2026-08-27 match the published manifests

`RW-19` and `DEC-07` remain open. Zero news articles were collected. No reconnect identity,
correction, late-delivery, or REST-backfill observation is available from these attempts.

## Attempt 1 — 2026-08-26 NVIDIA-results window (pre-start refusal)

Sealed window start: `2026-08-26T20:50:00Z` (60-second tolerance). Headless resume arrived
approximately 20 minutes late. Plan/timing validation runs before collector staging, so the
collector refused before staging, credentials, or network (exit 2, zero Alpaca requests).

The published record is an operator-authored pre-start refusal, explicitly not a collector
tombstone. A first-class pre-start refusal command/schema is a possible separate future package
and is not claimed here.

External directory (write-once, unmodified):

`C:\Users\Lucas\Documents\ai-investment-cockpit-evidence\rw19-nvda-fy27q2-20260826T205000Z-aborted`

Refusal record SHA-256:

`88d7a9c0cf092d776630602eca3302644d9fbd61515f72d69936fc1c38b7a965`

## Attempt 2 — 2026-08-27 earnings-aftermath window (`FAILED_CAPTURE`)

Collector started at `2026-08-27T13:00:45.929Z` inside tolerance. Fresh preflight was green:
`read_only`, audit chain valid at sequence 10,263, HALT absent, zero order/approval rows, and
clean `main == origin/main`
`9c6d21ea6db9de0a0bf862523c2730d681f2400c`.

WebSocket connect and auth succeeded. Alpaca acknowledged all 11 requested symbols, reordered:

- requested canonical: `AAPL`, `AVGO`, `INTC`, `META`, `MRVL`, `MSFT`, `MU`, `NVDA`, `SNDK`,
  `SPCX`, `TSLA`
- vendor returned: `SPCX`, `INTC`, `MSFT`, `NVDA`, `MU`, `SNDK`, `TSLA`, `META`, `AAPL`,
  `AVGO`, `MRVL`

The collector required order-identical acknowledgement, classified `socket_subscribe`, failed
closed, and published a verified tombstone. Zero news frames, zero REST, no retry. Raw journal
`raw/control-before.jsonl` retains the vendor order.

External directory (write-once, unmodified):

`C:\Users\Lucas\Documents\ai-investment-cockpit-evidence\rw19-nvda-fy27q2-aftermath-20260827T130000Z-aborted`

Artifact SHA-256 values recomputed from disk:

| Artifact | SHA-256 |
|---|---|
| plan | `d9706c9efeadeb5156e5c97d7e364d93027cd9deab7a38776f54d0df0a781715` |
| preflight | `95cd4a073845e70b3de840082b846ea8b02e8d5b1485eeb208ac3cbd6bfe8b00` |
| aborted manifest | `85968ad88e1e35a7cbd350b930f9f20da5f6af823a9742bba23a4b829becce1c` |
| collection hash (field inside the manifest, not a file hash) | `128f645baf73c980e1f079e9e9dfa0d1de8e2e70aec6cc9e6439545156651c08` |

## Defect — `BUG-111`

File: `tools/rw19_news_reconnect_plan_bound.py`.

Handshake subscription-acknowledgement validation used order-identical list equality
(`message.get("news") != list(symbols)`). A correct vendor ack that returned the exact symbol
set in a different order therefore failed closed as `socket_subscribe` and caused Attempt 2's
`FAILED_CAPTURE`.

## Defect — `BUG-112`

Same file. Mid-stream subscription control frames after the handshake were journaled but never
re-validated, so a mid-capture universe change would be silently accepted. Found during the
2026-08-27 audit of `BUG-111`.

## Repair contract

Branch: `work/rw19-ack-set-validation`. PR pending. Local focused gate green. `DEC-09` four-cell
matrix green 2026-08-27: Windows 3.11/3.12 each 2,427 passed / 5 skipped, WSL2 Ubuntu 24.04
3.11/3.12 each 2,421 passed / 6 skipped, Ruff clean in all four cells. Every skip is
environmental (Windows symlink privilege, sibling `edgar-morning-brew` checkout absent,
discord/claude/codex CLI availability probes). The WSL2 cells ran with a sanitized POSIX `PATH`
so Windows-interop CLI shims cannot leak into the Linux cells; one pre-fix WSL run surfaced
exactly that artifact (the Windows `codex` npm shim on the interop `PATH` failed to spawn under
Linux), which is a harness condition, not a repository defect.

An adversarial review round (attack → fix → regression) ran before the matrix: it found no
bypass of the ack validator itself and two defense-in-depth gaps, both fixed with regressions —
`verify()` now re-derives the ack invariant from the journaled control rows of complete
packages (a resealed wrong-universe journal is rejected; `verify_aborted` remains exempt
because a tombstone legitimately preserves the offending ack), and the collector containment
gate now checks transitive closure membership with a pinned two-entry allowlist.

Both defects share `_subscription_ack_error` with ordered guards before any set construction:

1. acknowledgement `news` must be a list;
2. every entry must be a string;
3. duplicates are rejected;
4. exact set equality against the planned symbol universe (order-independent).

Raw journaled control records continue to retain vendor order. Mid-stream subscription frames
are journal-first, then re-validated; failure class remains `socket_subscribe` and fail-closed.

## Claims and remaining gates

- Useful negative progress only: apparatus reached authenticated subscribe on Attempt 2 and
  correctly refused a late Attempt 1 before network.
- Zero news articles; no reconnect identity, correction, late-delivery, or REST-backfill
  evidence from either attempt.
- `RW-19` empirical gate remains `OPEN_NOT_CLOSED`.
- `DEC-07` remains open.
- Authority remains none. No paper opening Order, live authority, autonomous authority, or
  `RW-13` work is authorized by this evidence.
