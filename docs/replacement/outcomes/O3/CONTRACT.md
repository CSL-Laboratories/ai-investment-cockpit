# O3 candidate: instrument, portfolio, collateral, resolution and accounting semantics

**Issue:** [CSL-215](https://linear.app/csl-laboratories/issue/CSL-215)
**Parent:** [CSL-212](https://linear.app/csl-laboratories/issue/CSL-212)
**Discretion:** `human_gated`
**Claimed maturity:** `researched`

Not claimed: `human_decided`, `contract_frozen`, `prototyped`, `implemented`,
`provider_free_verified`, `demo_or_paper_qualified`, `shadow_qualified`,
`limited_live_qualified`, `release_qualified`.

This file is a candidate for human review. It does not freeze financial truth,
select a broker, migrate a live ledger, or change runtime authority. The nine
invariants and the four accepted CSL-212 documents stay as they are. Freezing
this candidate needs a human-authored, digest-bound receipt on CSL-215.

The hash pin on [fixtures/manifest.json](fixtures/manifest.json) stops the
fixture bytes from drifting. That pin is not `contract_frozen`.

## What this candidate covers

Stocks and event contracts stay distinct instruments, with distinct order
lifecycles and distinct books. The candidate defines:

- instrument and venue identity;
- equity order states and event-contract order states;
- event-contract market states, including resolution and void;
- fill handling for partial, duplicate, and late fills;
- a double-entry journal for cash, collateral reservations, fees, realized
  P&L, and strategy attribution;
- equity corporate actions;
- reconciliation, restart, and append-only correction.

It supplies those fragments for later outcomes to consume only after a human
receipt. It does not implement them in `app/risk/`, `app/brokers/`, or
`app/quant/`.

## Research basis

Parallel search `search_17de480276f5deb848309a5562f1b43e` is the embedded
result set recorded as run on Dan's box on 2026-10-17. This session did not
start another Parallel search. The five pages below were read on 2026-10-17.
Local inventory was read on branch base `ebbaa34489f171e079834d02098135711e1d4755`.
The replacement registry's inspected commit remains
`73ab78d9d69494c757cec875855121b1d29320a4`. This candidate does not edit that
annotation.

### Venue and accounting sources

- [Kalshi market settlement](https://docs.kalshi.com/getting_started/market_settlement).
  A yes outcome pays yes holders $1 per contract. A no outcome pays no holders
  $1 per contract. Only net positions are settled. Settlement fees are
  described as zero for a simple yes/no determination and as possible for
  sub-cent scalar settlement. The payout (`CollateralAmountChange`) is rounded
  to whole cents, and `CollateralAmountChange + MiscFeeAmt` equals the
  pre-rounding settlement value.
- [Kalshi FIX market settlement](http://docs.kalshi.com/fix/market-settlement).
  The settlement report is `35=UMS`. `MarketResult` is `yes`, `no`, or
  `scalar`. `SettlementPrice` (tag 730) is the pre-fee YES price. The payout
  balance change is net of the settlement fee. `MiscFeeAmt` is already
  deducted from that payout. Adding the fee back reconstructs the pre-fee
  amount. Deducting it again is a double count. Paginated reports use a new
  `MarketSettlementReportID` per page; the market ticker is the join key.
  The scalar example on that page (100.01 YES at 30.60 cents, gross
  `$30.60306`, rounding fee `$0.00006`, net payout `$30.6030`) is a source
  illustration. This candidate does not adopt that rounding procedure as a
  product formula.
- [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf),
  last updated and effective July 7, 2026 on the fetched copy. The general
  taker fee is described as `round up(M × 0.07 × C × P × (1−P))` and the
  maker fee as `round up(M × 0.0175 × C × P × (1−P))`. Fees apply when a
  trade executes. Canceling a resting order has no fee under that schedule.
  The same copy says there is no settlement fee. That sentence and the
  scalar rounding fee in the settlement docs are both recorded. This
  candidate does not decide which text controls a future schedule. Fixtures
  carry explicit fee amounts.
- [Kalshi rulebook, CFTC filing](https://www.cftc.gov/filings/orgrules/rules07012525155.pdf).
  Positions are fully cash collateralized. An order that lacks collateral is
  not accepted. A cancel of a resting order withdraws the unexecuted
  remainder; a partial fill stands. Trade cancellation authority is limited
  (Rule 5.11). A trade the clearing house rejects is void ab initio
  (Rule 6.1(d)). Binary settlement pays the settlement value (the filing's
  example is $1.00) to the in-the-money side (Rule 6.3(a)). Scalar settlement
  pays a proportion (Rule 6.3(b)). If the expiration value cannot be placed
  against the payout criterion, the filing describes venue discretion,
  including last trade and an outcome-review committee (Rule 6.3(c), Rule 7.1).
  This candidate does not treat that discretion as an automatic mark.
- [Cross-venue prediction-market accounting](https://camusocpa.com/cross-venue-prediction-market-accounting/)
  (page last updated August 29, 2026). Venue profit-and-loss figures are not
  added together to produce books. Cash reconciliation and position
  reconciliation are separate. A Kalshi position stays identifiable as a
  Kalshi position. Polymarket.com and Polymarket US are different source
  environments. Fees, rebates, and incentives stay separate from contract
  premium. Corrections stay visible. Platform profit and loss is not federal
  taxable income. Section 1092 is a question about offsetting positions, not
  a result that follows from two venues being economically related. This
  page is a practitioner discussion. It is not a venue rule and it is not a
  tax opinion adopted here.

### Local inventory

The current tree has stock-shaped accounting and no accepted event-contract
ledger:

- `app/quant/oms.py` tracks simulated orders (`pending_new`, `working`,
  `partially_filled`, `filled`, `cancelled`, `rejected`). Position changes
  from fills, not from intents. It does not share code with the broker
  state machine.
- `app/brokers/order_state_machine.py` is the equity order path that can
  reach a broker: `proposed`, `approved`, `submitted`, `partially_filled`,
  with terminals `filled`, `cancelled`, `expired`, `rejected`, and
  `canary_breach`.
- `app/quant/pnl.py` decomposes one strategy on one symbol into spread
  capture, inventory drift, and fees. It has no collateral account and no
  resolution.
- `app/brokers/paper_reconciliation.py` is read-only. Missing or conflicting
  broker evidence leaves the paper order open.
- `docs/product-tax-contract.md` is the historical September tax contract.
  The accepted scope audit says that contract is not automatically inherited.
  Nothing in this candidate applies it.

## Identity

Two instrument families. A record cannot be both.

| Family | Unit | Book | Collateral |
| --- | --- | --- | --- |
| `equity_share` | share | `equity` | none in this candidate |
| `event_contract` | contract | `event` | fully cash collateralized |

An event contract also carries `contract_style` (`binary` or `scalar`) and
`outcome_side` (`yes` or `no`). Yes and no are different instrument records
on the same venue market. They are not a signed quantity on one row.

`venue_id` is part of identity. The labels in the fixtures
(`venue-equity-candidate`, `venue-event-candidate`) are placeholders. They
are not a broker selection. Kalshi demo first, and Polymarket US second only
if stable, remain inherited owner direction from CSL-212. This candidate does
not open either account and does not model a Polymarket token lifecycle.

Money is an integer minor count plus a book scale. The equity book uses
scale 2 (cents). The event book uses scale 6 so a documented sub-cent fee
such as `$0.00006` can be stored without a float. Journal lines in one book
share that scale. Floats are invalid.

Books do not post to each other. A consolidated report may sum two books
only after each book has reconciled on its own. It must not net a Kalshi
contract against another venue's token, and it must not treat platform
profit and loss as the book.

## Order lifecycles

The machines are separate even where a word is spelled the same.
`partially_filled` on an equity order is not an event-contract state.

### Equity order

Aligned with the current broker vocabulary, as a description of that path,
not as a code change:

`proposed` → `approved` → `submitted` → `partially_filled` → `filled`

Also from the non-terminal states, where the current machine already allows
them: `cancelled`, `expired`, `rejected`, and `canary_breach`.
Terminals: `filled`, `cancelled`, `expired`, `rejected`, `canary_breach`.

### Event-contract order

`pending_collateral_check` → `resting` → `partially_filled` → `filled`

Other terminals: `cancelled` (unexecuted remainder only),
`rejected_insufficient_collateral`, `expired_unfilled`, `trade_cancelled`,
`void_ab_initio`.

A resting event order that is not matched does not pay a trading fee in the
fixtures. The fee schedule above says a cancel, by itself, is not a fill and
is not a fee. This candidate does not turn that sentence into a permanent
fee rule.

### Event-contract market

`listed` → `open` → `closed_pending_determination` → `determined` → `settled`

Side paths: `voided`, and `corrected` after a later append-only correction.
`determined` carries a result of `yes`, `no`, or `scalar` plus retained
settlement evidence. `voided` is not a yes or no result.

This candidate's examples are long buys. Short stock, margin, and options
stay outside it, matching the issue's non-goals.

## Journal

Each book is a double-entry journal. Every entry balances in minor units:
sum of debits equals sum of credits. Entries are append-only. A correction
posts a reversing entry and a new entry. It does not overwrite the original.

Accounts:

| Account | Equity book | Event book | Normal increase |
| --- | --- | --- | --- |
| `cash_available` | yes | yes | debit |
| `collateral_reserved` | no | yes | debit |
| `fees_expense` | yes | yes | debit |
| `position_cost` | yes | no | debit |
| `realized_pnl` | yes | yes | credit |

On the event book, `cash_available` and `collateral_reserved` are partitions
of cash held at that venue. Reserving collateral moves cash between those
accounts. It does not also debit `position_cost` and credit cash. That
second posting would count the same premium twice. `position_cost` exists
only on the equity book, where this candidate has no collateral partition.

A buy of a binary yes contract at a premium reserves that premium as
collateral. Settlement of an in-the-money binary, in the fixture, debits
`cash_available` for the payout, credits `collateral_reserved` for the
reserved premium, and credits `realized_pnl` for the difference. The
contracts come off the position. That shape follows the public description
of paying the in-the-money side and removing the contracts. It is a
candidate posting, not a live cash movement.

Trading fees and settlement fees are separate lines. When settlement
evidence says the fee is already deducted from the payout, the cash debit
is the net payout and the fee is not debited again. The pre-fee amount is
`payout + fee` only in that case, which is the identity on the FIX
settlement page. Fixtures that use a zero settlement fee record that zero
explicitly.

Attribution is a rollup of `realized_pnl` and `fees_expense` by
`strategy_id` over the journal entries applied after the checkpoint. It is
not a second set of accounts. If the rollup disagrees with the reported
attribution, the fixture fails `attribution_mismatch`. Strategy labels in
the fixtures are placeholders.

Posting references tie each entry to its cause. An entry with reason
`fill` carries the `fill_id` it posts, and the premium and fee on those
entries must equal that fill's `qty * price_minor` and `fee_minor`
per fill, not just in aggregate. `reserve_collateral` entries carry a
`fill_id` when they post an executed fill, or an `order_id` when they
post the reservation of a resting order; a `trade_fee` is execution
cost and always carries a `fill_id`. One entry carries at most one
causal reference — an entry naming both `fill_id` and `order_id` is
ambiguous and fails. An `order_id` reservation must post before the
order's first fill: it is valid while the order is still resting, and a
reservation sequenced after a fill is rejected. An order-linked
reservation must equal the total posted by all of that order's fills —
the sum of each fill's `qty * price_minor` plus its `fee_minor` — not
more and not less; a reserve that exceeds or falls short of that total
fails `balance_mismatch`. When an order reserved collateral up front,
its later fills do not re-post premium — the reservation already holds
it — so those fills are exempt from the per-fill premium check while
their fees still reconcile per fill.
Every line on a referenced entry must post the referenced fill's or
order's own `instrument_id`, never a different declared instrument. A
posting with a missing, unknown, checkpointed, conflicting, or
cross-instrument reference fails the fixture.

## Fills

Position quantity changes from accepted fills, corporate actions that change
quantity, settlement, void, or an explicit correction. An order intent does
not change quantity.

- **Partial.** Accepted fill quantity cannot exceed order quantity. Reported
  quantity that includes the unfilled remainder fails
  `partial_fill_divergence`.
- **Duplicate.** A `fill_id` applies once. A second copy fails
  `duplicate_fill_divergence` and does not increase quantity again.
- **Late.** A fill sequenced after a cancel-like terminal
  (`cancelled`, `expired`, `rejected`, `canary_breach`,
  `rejected_insufficient_collateral`, `expired_unfilled`,
  `trade_cancelled`, `void_ab_initio`) fails `late_fill_divergence`
  unless a correction record admits that fill id. The original entry stays
  in the journal either way.

## Corporate actions

Equity only. A cash dividend debits `cash_available` and credits
`realized_pnl` and does not change share quantity. The candidate vocabulary
also names `split`, `stock_dividend`, `symbol_change`, and `merger_cash`.
The validator fail-closes those kinds (`corporate_action_unsupported`) until
a human receipt admits fixtures for them. An event-contract book rejects
any corporate action (`family_crossed`).

## Resolution, void, and correction

Settlement evidence is a retained record: source id, market result, long
quantity, short quantity, payout, settlement fee, whether that fee is
already in the payout, and the pre-rounding amount. The long quantity has
to match the pre-settlement position in that instrument. Reported quantity
and collateral are then zero. A settled market without that evidence, or
with a payout that does not match the journal, fails
`settlement_unreconciled`. The collateral the settlement releases is
per instrument: the credit on `collateral_reserved` must equal the
reservation that instrument held before settlement, so an open
reservation on a different instrument is not a reason to under- or
over-release this one.

A voided market reconciles only when all of the following hold: a retained
`market_void` record exists, reported quantity is zero, collateral is zero,
and the journal has no settlement payout. Quantity, reserved collateral,
and open reservations are checked for that instrument only; an unrelated
instrument's open reservation does not block the void. Anything else fails
`void_unreconciled`. Returning the reserved premium to available cash is a
`void_unwind` entry, not a winning settlement. A clearing reject that the
rulebook calls void ab initio is this kind of unwind, not a determined
result.

Undeterminable expiration, outcome-review discretion, and scalar rounding
remain source facts. They are not automatic prices in this candidate.

## Reconciliation and restart

Cash and positions reconcile separately. Either mismatch fails the fixture.
A checkpoint stores the sequence, the applied idempotency keys, the ids
of the entries it already applied (`applied_entry_ids`), cash,
collateral, positions, and open reservations. The checkpoint's own open
reservations must sum to its `collateral_reserved`; a checkpoint that
disagrees with itself fails `collateral_double_count` before replay
starts. Checkpoint instruments must also be declared. A correction may
reverse a checkpointed entry: `reverses_entry_id` matches either a
replayed journal entry or an id in the checkpoint's applied history,
and either match admits the fill it names. Replay applies only entries
with a higher sequence and a new idempotency key. Reapplying a key, or an
entry at or below the checkpoint sequence, is a restart replay. On the
event book that failure is `collateral_double_count`. On the equity book it
is `restart_replay`. The replay is not applied a second time.

Open reservations on the event book must sum to `collateral_reserved`. A
reservation list that has been doubled to match a doubled collateral
balance can still fail because the idempotency key was already in the
checkpoint.

## Falsifiers and fixtures

| Fixture | Role | What it checks |
| --- | --- | --- |
| `equity_partial_then_dividend` | positive | Two partial fills that complete, then a cash dividend. Position is 100 shares, not 200. |
| `event_yes_settlement` | positive | Premium reserved once, trade fee once, yes settlement, position flat, collateral zero. |
| `event_restart_idempotent` | positive | Checkpoint already contains the reservation. Replay posts only the settlement. |
| `event_void_reconciled` | positive | Void evidence retained, premium returned, no settlement payout. |
| `neg_double_counted_collateral_after_restart` | negative | The reservation key is in the checkpoint and in the replay. Collateral is doubled. |
| `neg_voided_market_unreconciled` | negative | Market is voided, position and collateral remain, payout is posted, void evidence is missing. |
| `neg_partial_fill_divergence` | negative | 40 of 100 shares filled; reported position is 100. |
| `neg_duplicate_fill_divergence` | negative | The same fill id is applied twice; reported position is 80. |
| `neg_late_fill_divergence` | negative | A fill is sequenced after cancel; reported position includes it. |
| `event_resting_order_reservation` | positive | A filled position plus an order's `order_id`-linked reservation posted while it was resting; the order then partially fills without re-posting premium. |
| `neg_offsetting_fill_prices` | negative | Two fills whose prices disagree with their postings but net out in aggregate. Per-fill linkage fails it. |

The retained oracle is [oracle/designed-failures.json](oracle/designed-failures.json).
Its hash is pinned in the manifest. The negative controls are required to
fail, and to fail with those codes.

## Exact candidate command

```bash
python docs/replacement/outcomes/O3/validate_candidate.py
```

The command reads the manifest, checks fixture and oracle hashes, validates
each fixture with the local JSON Schemas, and runs the accounting checks.
Schemas are Draft 2020-12 documents applied with the library's bundled
metaschema. The command does not fetch a schema URL, open a socket, or read
a credential. Exit zero means the positives were consistent and the
negatives failed as designed. That exit is not an independent verifier
rerun, not completion, and not a promotion above `researched`. The registry
row for O3 stays `pending_outcome_artifact`.

## Alternatives recorded, not selected

These are options for the human review. This candidate uses the first row
of each pair as its proposal and does not close the others.

| Topic | Proposal in this candidate | Recorded alternative |
| --- | --- | --- |
| Instrument model | Separate equity and event families and books | One instrument type with optional collateral fields |
| Event cash | Collateral is a partition of venue cash | Collateral is a second debit on top of premium |
| Fees | Explicit amounts on each fixture | Encode the July 7, 2026 formula as the product rule |
| Scalar settlement | Source illustration only | Implement the published rounding example as the rule |
| Corporate actions other than cash dividends | Named, fail closed | Admit split, stock dividend, symbol change, and merger fixtures now |
| Runtime home | No runtime change in this PR | Replace or wrap `app/quant` and `app/brokers` |

## Smallest next experiments

After a human receipt, and not before:

1. Replay the four positive fixtures through a throwaway in-memory poster
   and confirm the same balances. Still no broker and no network.
2. Add one admitted fixture for an undeterminable expiration that stays
   `closed_pending_determination` with no payout.
3. Ask the owner whether the first net-yield metric includes taxes before
   any tax rule is written.

## Explicit non-goals

No tax conclusion. No broker selection. No margin or options scope. No live
ledger migration. No credentials. No capital movement. No change to order
authority. No Polymarket lifecycle. No claim that the current OMS, P&L, or
paper reconciliation implements this candidate.

## Unresolved decisions (owner: Lucas)

None of these are decided in this candidate.

- **Spend.** No paid provider, data purchase, or research quota is
  authorized. Any spend needs its own receipt.
- **Credentials.** No venue, broker, or model credential is used or
  requested. A Kalshi demo key, if one is ever wanted, is a separate
  decision.
- **Tax and legal.** No conclusion on federal character of event contracts,
  Section 1092 straddles, wash sales, California treatment, account type
  (taxable, IRA, or another), or whether taxes sit inside the first
  net-yield metric. No finding on account eligibility, Kalshi membership,
  Polymarket US stability, or prediction-market terms of use. CSL-92,
  CSL-97, and CSL-100 stay related evidence, not admitted scope.
- **Architecture.** Whether a successor ledger replaces, wraps, or sits
  beside `app/quant` and `app/brokers` is open. Language, storage, and
  topology stay with O8. This PR does not choose them.
- **Release.** Nothing here is release scope.
- **Live.** No live trading, live ledger, broker cutover, or capital
  movement.
- **Freezing.** Promoting this candidate to `human_decided` or
  `contract_frozen` needs a human-authored digest-bound receipt on
  CSL-215. Labels, CI, and this document are not that receipt.
