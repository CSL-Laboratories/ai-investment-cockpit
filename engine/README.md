# engine/

This is the new Cockpit engine tree. X1 names it `CX-NEW`: recreated code goes in
`engine/**` and its tests in `engine/tests/**`. It is empty on purpose. No engine code
lands here until the rules below can be checked.

The rules come from CP0-v2 in csl-research,
`contracts/ai-investment-cockpit/amendments/SUCCESSOR-PRODUCT-2026-10-26.md`, as amended
by `contracts/ai-investment-cockpit/amendments/X1-PARITY-CUTOVER-2026-11-02.md` (Lucas's
receipt on CSL-633, comment `7ece7d3b`). If this file and those differ, those win.

## What lives where

| Tree | Status | What to do with it |
| -- | -- | -- |
| `engine/**`, `engine/tests/**` | `CX-NEW` (X1, CP0-v2 §3) | New engine code and its tests. |
| `app/**`, `rust/**` | `CX-LEGACY` (X1) | Frozen whole at Cockpit main `11a122e2`. Still the paper runtime and the reference `engine/` is built against. Edited only to remove personal data or a secret, or for an escalate-list safety fix, each on its own issue with founder review. Deleted, with the X1 guard, by one cutover PR that opens only when the amendment's parity gate holds. |
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

## What engine/ has to reach before app/ goes

Every `keep` and `port` oracle class in the Cockpit X1 inventory
(`docs/evidence/x1-cockpit-legacy-test-inventory-2026-10-26.md`) needs successors here,
listed in a parity table with the failing and passing run ids for each row. Floors are
counted at `11a122e2`. `engine/` also needs order authority (CP0-v2 §4.4) and an edge
source of its own (CSL-637). The amendment's section 3 is the full gate.

## When code may land

Rule CA (CP0-v2 §6.1) counts a test only when it is a named file under
`engine/tests/**` or `tools/test_*.py` and an explicit step in `ci.yml` job `linux`
runs it. For `engine/tests/` that step is `engine tests`. See
[`tests/README.md`](tests/README.md).

The product contract is csl-research `contracts/PRODUCT.md`, pinned in
[`contract/PRODUCT.pin.json`](../contract/PRODUCT.pin.json) (Lucas, Nov 12, 2026).
