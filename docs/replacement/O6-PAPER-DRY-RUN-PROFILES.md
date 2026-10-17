# CSL-218 paper and dry-run authority profiles

**Status:** paper and dry-run implementation fragment. This document does not
enable a live profile, admit a live credential, or amend the nine invariants.
Live enablement stays with Lucas, behind a separate human-authored receipt.
Profile C has no runtime.

The loader admits only `paper` and `dry_run`. Any other environment, including
a live or unknown value, is refused. Unknown document fields are refused.
Provider output, including a confidence value, is not a grant.

## Profiles

| Class | Runtime | What it may reserve |
|---|---|---|
| A | Yes. Qualification reference. | `propose` only. |
| B | Yes, and only for `paper` or `dry_run`. | `propose`, plus `submit_paper` or `submit_dry_run` for that environment. |
| C | No. Future target only. | Nothing. Loading class C fails closed. |

A profile binds the account and venue perimeter, the adapter and environment,
the provider and model version, the allowed actions, the expiry, the cumulative
notional and order-count ceilings, the stop owner, the recovery owner, and
evidence references. The admitted perimeter in this slice is a US individual
personal account, US equity, and either the Alpaca paper adapter or the dry-run
adapter. A second active profile cannot occupy the same account, venue, and
environment. Reinstalling a profile cannot raise a ceiling that was already
consumed.

## Enforcement

- The stop owner is the only actor who may install, revoke, kill, hold, release,
  or reserve. The recovery owner cannot clear a stop and cannot reserve an action.
- Release requires an explicit reconciliation flag. A false flag leaves the stop
  in place.
- Kill and hold are rows in the profile store. A new process that opens the same
  file still refuses the perimeter.
- This fragment does not raise or clear the process-wide halt sentinel. Clearing
  a profile stop must not clear an operator kill. That coupling is an open decision.
- `submit_paper` is handed to the existing `OrderGateway.submit` path after the
  reservation. Risk pass, mode, approval, and the single-use capability still
  apply. A profile reservation that the gateway refuses does not reach the broker.
- A non-paper gateway transport is refused before reservation.
- `propose` and `submit_dry_run` do not call the gateway and do not open a transport.
- Client order ids are idempotent. A replay returns the original reservation and
  does not add consumption or call the gateway again. A different notional or
  action for the same id is refused.
- Consumption is reserved before the gateway call. A refused gateway call does
  not refund the reservation.

## Live-blocker ledger

| Blocker | Why it stays closed |
|---|---|
| Live environment | The loader and handoff refuse every environment other than `paper` and `dry_run`. |
| Live credential path | This fragment has no broker secret, no credential-store read, and no live adapter. |
| Live transport | Profile handoff refuses a gateway whose pinned transport is not paper. |
| Profile C | Documented future target. No schema branch can load it. |
| SEC-11 and the other pre-live gates | Unchanged. This fragment does not satisfy them. |
| Human live receipt | Not issued. Lucas retains live enablement. |
| Event-market venues | Kalshi and Polymarket are not admitted bindings. Demo behavior is not production behavior. |
| Paper or dry-run success | Not live eligibility, not economic qualification, and not a release PASS. |
| Independent verification | Not claimed. Implementer tests and CI do not replace a non-implementer rerun. |

## Unresolved decisions

1. Whether profile documents must be cryptographically signed. This slice records
   the stop-owner actor and fails closed on any other actor. It does not add a
   signature scheme.
2. Whether a profile kill must also raise the process-wide halt sentinel. It does
   not, so a profile release cannot clear an operator kill.
3. Whether a cancel or a gateway refusal should release consumed budget. It does
   not. Over-counting is the closed direction.
4. Whether the seven-day expiry ceiling is the owner's chosen maximum. Longer
   documents are refused.
5. Whether one beneficial owner with several account ids needs a cross-account
   ceiling. This slice ceilings one account, venue, and environment.
6. Whether prediction-market demo adapters can be added without a new human
   receipt. They are not admitted here.
7. Profile C's envelope, monitor, and promotion gates. No runtime is included.

## Research basis

These public sources informed the fail-closed shape. They are not legal advice
and they do not admit a live profile.

- [17 CFR 240.15c3-5](https://www.ecfr.gov/current/title-17/chapter-II/part-240/subpart-A/section-240.15c3-5)
  requires pre-set aggregate credit or capital thresholds and restricts market
  access to pre-approved persons and accounts.
- [FINRA 2026 Market Access Rule](https://www.finra.org/rules-guidance/guidance/reports/2026-finra-annual-regulatory-oversight-report/market-access-rule)
  flags stand-alone controls that miss aggregate exposure across ports and venues.
- [SEC small-entity guide for Rule 15c3-5](https://www.sec.gov/files/rules/final/2010/34-63241-secg.htm)
  states the rule's purpose: risk controls for brokers and dealers with market access.
- [Federal Register proposal, 26 January 2010](https://www.federalregister.gov/documents/2010/01/26/2010-1269/risk-management-controls-for-brokers-or-dealers-with-market-access)
  describes automated pre-trade controls and the prohibition on unfiltered access.
- [Kalshi demo environment](https://docs.kalshi.com/getting_started/demo_env)
  uses mock funds, does not share credentials with production, and does not
  reflect production prices.
- [Kalshi create-order quick start](https://docs.kalshi.com/getting_started/quick_start_create_order)
  uses a client order id so a retry cannot create a second order.
