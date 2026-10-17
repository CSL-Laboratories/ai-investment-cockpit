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

- The stop-owner string is a label, not a credential. Install mints a stop
  capability and stores only its hash. Revoke, kill, hold, release, and reserve
  require that capability. Presenting the owner string is refused.
- Release requires an explicit reconciliation flag, and the flag is only an
  assertion of intent. The store derives the real reconcile state: a perimeter
  with a reservation still pending, in progress, or failed without a
  gateway-identity reconcile is refused even when the caller passes the flag.
- Kill and hold are rows in the profile store. A new process that opens the same
  file still refuses the perimeter.
- This fragment does not raise or clear the process-wide halt sentinel. Clearing
  a profile stop must not clear an operator kill. That coupling is an open decision.
- `submit_paper` is handed to a `PaperOrderGateway`. That class is the paper
  transport. A caller-set `execution_scope` on `OrderGateway`, including the
  value `paper`, is refused before reservation. The broker must still be the
  admitted Alpaca paper adapter. Risk pass, mode, approval, and the single-use
  capability still apply. A profile reservation that the gateway refuses does
  not reach the broker.
- A non-paper gateway type is refused before reservation.
- Provider, model version, adapter, venue, and venue family are compared with
  the profile before a reservation. An omitted or different binding is refused.
  For `submit_paper`, `producing_runtime` must be that same provider.
- A revoked or expired profile cannot clear the perimeter kill or hold. Release
  is limited to the current unrevoked, unexpired occupant.
- Claim rechecks revocation and expiry. A missing controls row is a store
  refusal, not an open stop.
- Model output is either absent or a closed evidence object. Unknown fields,
  including a grant-shaped object, are refused. The object is never a grant.
- Handoff state is `pending`, `in_progress`, `failed`, or `completed`. Claim of
  a paper submit writes `in_progress` before `PaperOrderGateway.submit`. A
  returned success writes `completed`. A returned refusal writes `failed` and
  leaves the client id reusable: the same id enters the gateway again, and
  reconciliation of a failed row does not mark it complete.
- Every claim that may enter the gateway holds exclusive, durable ownership
  fenced by `claim_generation`, a monotonically increasing token stored on the
  row. A claim on an owned `in_progress` row is refused and never reaches the
  gateway. Completion and failure write only `WHERE claim_generation` matches
  the ticket, so a stale caller's write is a refusal and never downgrades a
  completed row. A crash between claim and return stays `in_progress` under the
  crashed owner; the only re-ownership path is `reconcile_handoff_to_gateway`,
  which first reads the gateway's durable order identity — `order_ref` = the
  order id, transported as `cockpit-<order.id>` and recorded on the HMAC-signed
  `EVENT_ORDER_TRANSITION` chain. If the chain shows `submitted` the handoff is
  recorded `completed` with no second submit; otherwise the claim is re-owned
  under a new generation when a fresh claim would be admitted, or resolved
  `failed` when it would not, so a stuck claim consumes only its own
  reservation slice and never bricks the perimeter. `completed` is the only
  state that skips submit. The gateway's own client order id remains the
  broker reconciliation.
- `propose` and `submit_dry_run` do not call the gateway and do not open a transport.
- Client order ids are idempotent. A replay returns the original reservation and
  does not add consumption or call the gateway again. A different notional or
  action for the same id is refused. A reservation with no order reference can
  be bound once, before the handoff completes.
- A pre-current store (`user_version` below 3) holding rows in any table —
  profiles, controls, consumption, or reservations — refuses to open with
  `legacy_store`. Those rows predate the install-minted stop capability and
  durable claim ownership, and no unauthenticated capability is minted or
  accepted; the store must be recreated or its profiles re-installed through
  the authenticated install path. An empty pre-current store is upgraded in
  place. A current-version store missing a schema column is corruption, not a
  migration, and refuses to open. This module is new in this change set; no
  deployed store predates these versions.
- Consumption is reserved before the gateway call. A refused gateway call does
  not refund the reservation.

## Live-blocker ledger

| Blocker | Why it stays closed |
|---|---|
| Live environment | The loader and handoff refuse every environment other than `paper` and `dry_run`. |
| Live credential path | This fragment has no broker secret, no credential-store read, and no live adapter. |
| Live transport | Profile handoff admits only `PaperOrderGateway`. A caller-set scope string is not paper authority. |
| Profile C | Documented future target. No schema branch can load it. |
| SEC-11 and the other pre-live gates | Unchanged. This fragment does not satisfy them. |
| Human live receipt | Not issued. Lucas retains live enablement. |
| Event-market venues | Kalshi and Polymarket are not admitted bindings. Demo behavior is not production behavior. |
| Paper or dry-run success | Not live eligibility, not economic qualification, and not a release PASS. |
| Independent verification | Not claimed. Implementer tests and CI do not replace a non-implementer rerun. |
| Stop outliving its profile | Un-bricking a perimeter whose stop outlived its profile: founder decision, owner role: founders (CSL-280). A revoked or expired profile cannot clear the stop and install refuses the blocked perimeter until that decision lands. |

## Unresolved decisions

1. Whether profile documents must be cryptographically signed. Stop and clear
   now require an install-minted capability, not the owner string. This slice
   still does not sign the profile document.
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
8. Whether a stop owner whose profile was revoked or expired may clear the
   perimeter stop, and under what additional conditions. Escalated to the
   founders on Linear CSL-280; the fail-closed status quo stands until then.

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
- [SQLite ALTER TABLE](https://sqlite.org/lang_altertable.html) appends a column.
  A `NOT NULL` column needs a default so existing rows pass the new constraint.
- [Fencing tokens](https://ghassan.de/en/articles/distributed-locks-fencing-tokens)
  reject a stale owner's write in the same atomic update that records the
  effect; `claim_generation` applies that to the reservation row.
- [Fail-closed authorization](https://nalar.dev/fail-closed-when-authorization-cannot-be-evaluated/):
  when authorization or reconciliation cannot be evaluated, the decision is a
  refusal, not an open door.
- [AWS transactional outbox](https://github.com/aws-samples/transactional-outbox-pattern/blob/main/README.md)
  notes that a database update and the later notification can diverge, so the
  consumer must be idempotent.
- [Idempotent outbox](https://github.com/vgrente/idempotent-outbox-pattern/tree/main)
  treats the database row as the consistency anchor: at-least-once delivery plus
  an idempotent write.
- [SEC Trading and Markets FAQ](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0)
  describes Rule 15c3-5 pre-trade credit thresholds. The excerpt is thin and is
  not a live-admission source.
