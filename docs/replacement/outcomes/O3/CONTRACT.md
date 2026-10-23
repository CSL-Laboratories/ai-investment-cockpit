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

`instrument_id` is unique in a fixture. A second instrument row with the same
id fails `position_divergence`. The rows are not collapsed, and neither the
first nor the last is kept when the venue, symbol, or outcome side disagree.

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

When accepted quantity is greater than zero and less than the order
quantity, and the order is not in a cancel, expire, or reject terminal,
its state is `partially_filled`. A `resting` label on that partial fill
fails `partial_fill_divergence`. `partially_filled` with no accepted
quantity, or with the full order quantity, fails the same way.

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
not a second set of accounts. Reported attribution rows are one per
`strategy_id`. A second row for the same strategy fails
`attribution_mismatch`, and the last row does not replace the first. If
the rollup disagrees with the reported attribution, the fixture fails
`attribution_mismatch`. Strategy labels in the fixtures are placeholders.

Posting references tie each entry to its cause. An entry with reason
`fill` carries the `fill_id` it posts, and the premium and fee on those
entries must equal that fill's `qty * price_minor` and `fee_minor`
per fill, not just in aggregate. `reserve_collateral` entries carry a
`fill_id` when they post an executed fill, or an `order_id` when they
post the reservation of a resting order; a `trade_fee` is execution
cost and always carries a `fill_id`. One entry carries at most one
causal reference — an entry naming both `fill_id` and `order_id` is
ambiguous and fails. An `order_id` reservation must post before the
order's first fill: it is valid while the order is still resting and
unfilled, and a reservation sequenced after a fill is rejected. That
ordering check applies only when the reservation is created
(`reserve_collateral` with an `order_id` and no `fill_id`). A later
`void_unwind` or `settlement_payout` that names the order, and credits
`collateral_reserved`, is a release of that hold. It is not a new
reservation, and it is not rejected for coming after the fill. Before
any fill, that hold is not compared with a fill total of zero, unless
the order is already in a cancel, expire, or reject terminal. A dead
order that still carries the hold fails `balance_mismatch`. A release
that does not name the order leaves the hold in place. A `void_unwind`
or `settlement_payout` that credits `collateral_reserved` has to name
the order when that credit is against an owned hold. The collateral
still reserved for an instrument must cover the positive holds of every
order on it. An unnamed credit, including one sequenced before the
reserve, that leaves those holds larger than the reserved collateral
fails `balance_mismatch`. A checkpoint
open reservation may name its owning `order_id`. That optional id is
this candidate's proposal for which order owns the checkpoint hold.
The reservation's `instrument_id` has to be that order's instrument. A
reservation whose instrument is not the named order's instrument,
including a crossed pair where each reservation names the other order,
fails `balance_mismatch` and does not fund a release. `checkpoint_filled_qty` does not prove the
order held the collateral. A release above the order's available hold
fails `balance_mismatch`. A release may be partial. The ending owned
balance is the checkpoint hold plus later order-linked collateral
changes. When a reported open reservation names that `order_id`, the
order has to exist and the row's `instrument_id` has to be that order's
instrument. The amount is reconciled per `(order_id, instrument_id)`
against that order's ending balance. An unknown order, a row whose
instrument is not the named order's instrument, or a swap of two equal
holds onto each other's instruments fails `balance_mismatch`. A voided market still has to
finish at zero collateral for that instrument; the partial release is
the remainder of an order that is not flattening a void. Once the
order has an accepted fill, the pre-release reservation must equal the
sum of those fills' `qty * price_minor` plus `fee_minor`. Pre-release
means checkpoint holds that name the order, plus `reserve_collateral`
debits in the journal. A release credit does not reduce that total.
An extra checkpoint hold on top of a journal reserve that already
matches the fills fails `balance_mismatch`. A reserve that exceeds or
falls short of that executed total fails the same way. When the order's
fills exist only as `checkpoint_filled_qty`, with no fill row carrying
the notional, a post-checkpoint `reserve_collateral` is compared the
same way: the pre-release total has to equal the checkpoint hold
already attributed to that order. A new reserve on top of those
checkpoint fills fails `balance_mismatch`. A hold for the
still-unfilled remainder, above that executed total, is not represented
in this candidate. A fill is funded by the collateral that order still
holds at the fill's sequence. The checkpoint hold counts from the start.
An order-linked reserve or release counts when its sequence is at or
before the fill, so a release that already returned the collateral does
not fund a later fill. When that balance still covers the fill's
`qty * price_minor`, the fill does not re-post premium, and a second
premium posting fails `balance_mismatch`. When a partial release leaves
a hold below that premium, the fill posts only the uncovered part:
`qty * price_minor` minus the hold still in place. That remaining hold
is consumed across the order's accepted fills in sequence order.
Collateral an earlier fill already counted as covered is not available
to a later fill. Two fills that each post only the shortfall against
the same remaining hold fail `balance_mismatch`. Posting the full
premium charges the held part twice and fails `balance_mismatch`. A
full refund leaves no hold, so the fill posts `qty * price_minor`
itself. Fees still
reconcile per fill.
Only `fill`,
`reserve_collateral`, and `trade_fee` entries contribute to a fill's
posted premium and fee. A `void_unwind` is checked on the entry itself,
not only when a market row says `voided`. Its lines are a cash debit and
a collateral credit for the same positive amount on each instrument,
checked separately, and it does not carry
a `fill_id`. Cash posted on one instrument does not balance collateral
posted on another. The cause is an `order_id` on the entry, or `market_void`
evidence for every instrument the entry posts. A reserve relabeled as
`void_unwind` — collateral debit, cash credit, or a fill cause — fails
`balance_mismatch`. A `void_unwind` that carries a `fill_id` does not
satisfy that fill.
A `fill` debits `position_cost` and any `fees_expense` and credits
`cash_available` for the same total. A `reserve_collateral` debits
`collateral_reserved` and credits `cash_available` for the same total.
A `trade_fee` debits `fees_expense` and credits `cash_available` for
the same total. Another account or side on those reasons, including a
realized-P&L credit in place of the cash credit, fails
`balance_mismatch`.
Every line on a referenced entry must post the referenced fill's or
order's own `instrument_id`, never a different declared instrument. A
posting with a missing, unknown, checkpointed, conflicting, or
cross-instrument reference fails the fixture.

## Fills

Position quantity changes from accepted fills, corporate actions that change
quantity, settlement, void, or an explicit correction. An order intent does
not change quantity.

- **Partial.** Accepted fill quantity cannot exceed order quantity. A
  post-checkpoint fill is measured against the quantity still open:
  `checkpoint_filled_qty` plus fills already accepted after the
  checkpoint. A later fill that carries that total over `qty` fails
  `partial_fill_divergence`, including when the order is already in a
  cancel-like terminal. Reported quantity that includes the unfilled
  remainder fails `partial_fill_divergence`.
- **Complete.** A `filled` order accounts for its full quantity: retained
  fill rows plus the quantity the checkpoint already applied
  (`checkpoint_filled_qty`) must equal `qty`. An order that declares
  `filled` with no retained fills and no checkpoint evidence fails
  `partial_fill_divergence`.
- **Duplicate.** A `fill_id` applies once. A second copy fails
  `duplicate_fill_divergence` and does not increase quantity again.
  An `order_id` applies once too. A second order row with the same id
  fails `position_divergence` and does not replace the earlier row.
- **Checkpoint quantity.** The sum of `checkpoint_filled_qty` for the
  orders of one instrument equals that instrument's checkpoint position.
  A checkpointed buy the checkpoint does not hold fails
  `position_divergence`. A fill retained at or below the checkpoint
  sequence is already applied, so that order's `checkpoint_filled_qty`
  has to include it. This holds for a cancel, expire, or reject as well
  as for a resting or filled order. A retained quantity the checkpoint
  omits fails `position_divergence`. Fills that were not retained may
  still be represented only by `checkpoint_filled_qty`.
- **After settlement.** A fill sequenced at or after that instrument's
  `settlement_payout` fails `settlement_unreconciled`. A shared sequence
  is rejected, not treated as already in the settled position. Sequence
  is the only clock in this candidate, and a tie does not show that the
  fill was inside the position the payout closed.
- **Fill reference order.** A journal entry that names a fill and is
  sequenced before that fill fails `position_divergence`. The check does
  not depend on a settlement. An entry at the fill's own sequence is
  ordered with the fill.
- **Late.** A fill sequenced after a cancel-like terminal
  (`cancelled`, `expired`, `rejected`, `canary_breach`,
  `rejected_insufficient_collateral`, `expired_unfilled`,
  `trade_cancelled`, `void_ab_initio`) fails `late_fill_divergence`
  unless a correction record admits that fill id. The original entry stays
  in the journal either way.

## Corporate actions

Equity only. A cash dividend debits `cash_available` and credits
`realized_pnl` and does not change share quantity. Every line on a
corporate-action entry must name an instrument that has a declared
corporate action in the fixture — booking a dividend on one security to
another's accounts fails `corporate_action_mismatch`. The candidate vocabulary
also names `split`, `stock_dividend`, `symbol_change`, and `merger_cash`.
The validator fail-closes those kinds (`corporate_action_unsupported`) until
a human receipt admits fixtures for them. An event-contract book rejects
any corporate action (`family_crossed`).

## Resolution, void, and correction

A fixture has one current market-state row per event instrument. A
missing row fails `settlement_unreconciled`, as does a second row,
including an `open` row beside `settled`. An equity book has no market
rows. Those rows carry no sequence, so they are not lifecycle history.
Terminal evidence has to agree with that row. `market_settlement`
evidence on a market that is not `settled` fails
`settlement_unreconciled`. `market_void` evidence on a market that is
not `voided` fails `void_unreconciled`, unless a correction supersedes
that void. `listed`, `open`, `closed_pending_determination`,
`determined`, and `corrected` do not carry either record.

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
over-release this one. The pre-settlement reservation is the state at the
settlement entry's sequence: a collateral posting sequenced after the
settlement cannot count toward it, so a reserve raised after the payout
fails `settlement_unreconciled`.

Every line on a `settlement_payout` names the same instrument. That
market is `settled`, or the whole entry sits on a `voided` market and
the void check rejects it. A line on another instrument, a mix of
instruments, or an instrument that is neither settled nor voided fails
`settlement_unreconciled`.

The P&L side of a binary settlement reconciles too: when the reserved
premium exceeds the payout the journal debits `realized_pnl` for the
loss, and when the payout exceeds the reservation it credits
`realized_pnl` for the gain — each equal to that difference per
instrument. A losing contract is not a fee: `fees_expense` on the
settlement entry must equal the evidence's `misc_fee_minor` when the fee
is not already deducted from the payout, and must not exist at all when
it is (`fee_double_count`).

A correction may reverse one settlement and leave a corrected settlement
in its place. The reversal is itself a `settlement_payout`. It qualifies
only when the correction's `cause` is `settlement_payout`,
`instrument_id` is that settlement's instrument, and `reverses_entry_id`
names the original entry, and another journal entry is the exact mirror:
same instrument, same accounts and amounts, debit and credit swapped, and
the amounts positive. The original has to be an ordinary settlement
before it can be paired: it may credit `collateral_reserved` and debit
`fees_expense`, and it may not debit collateral or credit fees. The
mirror is the only entry that may debit `collateral_reserved` (and
credit `fees_expense` when the original debited it). Its sequence is
strictly after the original. An earlier mirror, or one posted at the
same sequence, is not a reversal. A journal `entry_id` is used once. A
second entry with the same id fails `schema_invalid` and is not paired,
so it cannot inherit the reversal exemption. In the payout,
collateral-release, fee, and gain/loss identity the original and the
mirror cancel as a pair and drop out. The settlement that remains has to
match the evidence on its own. A zero-value pair, a partial swap, a
backward original, a mirror that is not strictly later, a reused entry
id, or a reversal whose correction names a missing entry does not cancel
a settlement and does not permit the collateral debit.

`market_void` evidence on an instrument that also has settlement evidence,
a `settled` market row, or a `settlement_payout` fails `void_unreconciled`.
A correction supersedes that void only when all of the following hold.
`supersedes_source_id` equals the void record's `source_id`.
`instrument_id` equals the voided instrument. `cause` is
`settlement_payout` and equals the reason of the journal entry named by
`reverses_entry_id`. That entry's lines all use the voided instrument.
Another journal entry posts the reversal: the same accounts, amounts,
and instrument, with debit and credit swapped. The reversed entry has to
be a positive `settlement_payout` on that instrument, and every positive
`settlement_payout` on that instrument needs its own reversal partner.
One partner covers one posting. The partner is a reversal the settlement
check accepts: the reversed entry is an ordinary settlement, the mirror
is a `settlement_payout` sequenced strictly after it, and a reused
`entry_id` does not pair. An earlier mirror, a tie, or a backward
posting does not supersede the void. A zero-value pair reverses itself and
does not hide an unreversed settlement. `admits_fill_id` has to name a
fill of the voided instrument; a missing fill does not supersede the
void. Naming a live settlement,
a missing fill, or a different instrument or cause does not supersede
the void. A checkpoint id with no posted reversal does not supersede it
either. Any other correction leaves the contradiction in place.

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
Reported position rows are one per instrument. A second row for the same
`instrument_id` fails `position_divergence`. The last row does not replace
the first.
A checkpoint stores the sequence, the applied idempotency keys, the ids
of the entries it already applied (`applied_entry_ids`), cash,
collateral, positions, and open reservations. The checkpoint's own open
reservations must sum to its `collateral_reserved`; a checkpoint that
disagrees with itself fails `collateral_double_count` before replay
starts. Checkpoint instruments must also be declared. A correction may
reverse a checkpointed entry: `reverses_entry_id` matches either a
replayed journal entry or an id in the checkpoint's applied history,
and either match admits the fill it names. An applied entry id protects
against replay too: a journal entry whose `entry_id` appears in
`applied_entry_ids` is not posted again. Replay applies only entries
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
| `event_resting_order_reservation` | positive | A filled position plus an order-linked reservation posted before the first match. After a partial fill the order state is `partially_filled`, and the reservation equals the executed premium and fee. The instrument has one `open` market-state row. |
| `neg_offsetting_fill_prices` | negative | Two fills whose prices disagree with their postings but net out in aggregate. Per-fill linkage fails it. |
| `event_no_settlement` | positive | A losing NO-side binary: zero payout, reserved premium released, realized loss debited, position flat. |

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
- **Unfilled remainder.** After a partial fill, this candidate requires
  the order-linked reserve to equal the executed premium and fees. It
  does not represent an additional hold for the unfilled quantity. That
  needs a limit price on the order, which is not decided here.
