# engine/

This is the new Cockpit engine tree. X1 names it `CX-NEW`: recreated code goes in
`engine/**` and its tests in `engine/tests/**`. It is empty on purpose. No engine code
lands here until the rules below can be checked.

The rules come from CP0-v2 in csl-research,
`contracts/ai-investment-cockpit/amendments/SUCCESSOR-PRODUCT-2026-10-26.md`. If this
file and that one differ, that one wins.

## What lives where

| Tree | Status | What to do with it |
| -- | -- | -- |
| `engine/**`, `engine/tests/**` | `CX-NEW` (X1, CP0-v2 §3) | New engine code and its tests. |
| `app/**`, `rust/**` | `CX-LEGACY` (X1) | Read-only. Frozen at tag `pre-x1-cockpit-20261026`. Deleted by the cutover PR after the Nov 10 rehearsal. Never edited. |
| `cockpit_spine/**`, `successor/**` | Reference | Kept as reference until the cutover. Useful parts move to csl-research before they are deleted. Engine PRs do not edit them. |
| `tools/**` | CI and test tooling (Rule CA) | Checkers and test tooling only. |

## Rules for code in this tree

1. **No language is chosen here.** X1 does not decide the implementation language. F-3
   style spikes do, and the result is recorded in csl-research before the first code PR.
2. **No session, queue, lease or route-selection code.** Cockpit is Recursion's first
   customer and consumes the Recursion route contract through a consumer adapter
   (CP0-v2 §4 items 1 and 3, WP12).
3. **Order authority does not change.** Paper-first. One risk-pass, mode, approval and
   `OrderGateway` path. Risk logic stays deterministic, with no model, I/O or subprocess
   calls. A provider response, typed decision or confidence value is an input, never
   permission. The operator MCP inspects and stops only; a tool named like
   `place_order` fails the candidate (CP0-v2 §4 item 4, X2).
4. **Order-path files stay on the escalate list** in whichever tree they live: risk,
   brokers, audit, PnL, halt, kill switch, gate, mode and the loopback routes
   (CP0-v2 §4 item 6, `AGENTS.md` "Proceed or escalate").
5. **Keys live in the OS keyring.** CSL adds no pay-per-token keys (CP0-v2 §4 item 5).
6. **Research does not live here.** Specs, decisions, evidence and runbooks go to
   csl-research (X4). Only files that code or agents read offline stay in this repo.

## When code may land

Rule CA (CP0-v2 §6.1) counts a test only when it is a named file under
`engine/tests/**` or `tools/test_*.py` and an explicit step in `ci.yml` job `linux`
runs it. No such step exists for `engine/tests/` yet. Until one merges, work admitted
for this tree is limited to research and contract drafts. See
[`tests/README.md`](tests/README.md).

The product contract lives in csl-research. `docs/replacement/SUCCESSOR-PRODUCT.md`
is the local copy until it becomes a pointer with a pinned digest.
