---
title: RW-16 — congressional copy-trading (paper-complete, live-blocked)
type: implementation-plan
status: live
last-verified: 2026-08-10
related:
  - README
  - REGISTER
  - refactor-plan
  - runbooks/congress-autoexec
  - security-hardening-backlog
  - security-architecture
  - prompt-injection-defense
  - ultracode-build-playbook
  - setup/accounts-and-keys
---

# RW-16 — congressional copy-trading

> Work package **RW-16**. Code lives in `app/ingest/congress/` (ingest),
> `app/copytrade/` (the pure engine), `app/workflows/copytrade_orders.py` (the order
> path) and `app/schemas/models/congress_disclosure.py` (the store). Tests in
> `app/tests/ingest/test_congress_parse.py`, `app/tests/copytrade/`,
> `app/tests/workflows/test_copytrade_orders.py`, `app/tests/system/test_congress_wiring.py`.
>
> **Read `app/copytrade/options.py` and `app/workflows/copytrade_orders.py`'s module
> docstrings first.** They carry the operator's verbatim requirement and the SEC-08
> limitation; this document is the map, not the specification.

---

## ⚠️ READ FIRST — RW-17 changed who approves, and the two things it did not change

Two statements this document used to make are **no longer true**: that the copy cycle is
deliberately unscheduled, and that the feature is human-gated. **RW-17** schedules the cycle and
replaces the operator's approval tap with an audited auto-adjudication. Details in §8 and the
operations runbook, [[runbooks/congress-autoexec]]. The three facts that matter before you read
anything else:

1. **The auto-execution lane is PAPER-ONLY, by construction.** It resolves only at
   `mode == paper`, re-checked under the lock at the instant of every trade; it submits only
   through the paper-scoped `OrderGateway`; and `brokers/auto_paper_submit.auto_paper_place`
   raises `CapabilityError` rather than place an auto-adjudicated order through anything else.
   `auto_veto` / `auto_full` are **live** rungs here — nothing in RW-17 touches, arms, or routes
   around one.
2. **`RW-16-LIVE` / SEC-08 is still OPEN, so live is still unreachable.** `producing_runtime`
   remains the module constant `"deterministic:copytrade"`, and
   `mode_ladder.assert_execution_runtime` still refuses it in `review_live` / `auto_veto` /
   `auto_full`. In those rungs the cycle emits *nothing* (§8). Scheduling the cycle did not
   weaken that gate and cannot: the refusal is checked by the cycle, again by
   `PaperExecutionService`, and again by `OrderGateway` at submit. Congressional copy-trading is
   still **unavailable with real capital**, and unblocking it is an explicit operator decision
   plus a security review, not a code change ([[REGISTER]] `RW-16-LIVE`).
3. **What the human gate was doing, and what replaced it.** The tap was not only a decision —
   incidentally, it was the thing that bounded how much damage one bad day could do, and the
   thing that made a stuck feature visible (an empty approval queue is something an operator
   notices). Removing it gave up **both**. In their place:
   - **an audited auto-adjudication, not a bypass.** The lane writes a real, persisted,
     HMAC-covered `ApprovalRecord` plus its covering `EVENT_ADJUDICATION`, in one transaction,
     exactly as a human tap does — because `OrderGateway` gate (e) re-loads the row by id and
     re-verifies the signed payload. Invariant 1 is intact: risk-pass → mode → approval →
     `OrderGateway`, one path, and the auto path is routed through `brokers/` so it appears in
     the Invariant-1 call-site gate like every other placement site;
   - **four budget rails** (`copytrade/budget.py`) as the replacement damage bound: daily
     notional, trades per day, open positions, per-symbol per day — re-derived from the signed
     chain on every check, never from a counter, and **exhausted** rather than zero when the
     chain cannot be read;
   - **notification as a precondition of execution** (SEC-23: silence is not consent). A ticket
     whose `queued` message was not *confirmed* delivered never fires;
   - **a closed, named, counted refusal vocabulary plus an unconditional daily digest**, so
     "the lane did nothing today" stays a positive statement rather than an absence.

   What is *not* replaced: nobody looks at the trade. The operator's only lever is `/cancel`
   inside the window, and they never have to act for a trade to proceed. That is what was asked
   for, and it is the residual.

---

## 0. What this is, and what it is not

**It is:** a deterministic, model-free reader of two public government feeds that turns a
*delayed public filing* into a small, **paper-only** order proposal — adjudicated by a human by
default, or, with `COCKPIT_CONGRESS_AUTOEXEC` on, by RW-17's audited auto-adjudication behind a
notified cancel window and four budget rails (see the banner above and §8).

**It is not:**

- **not a signal edge.** Every number in it is public, statutory, and 30–45 days old by the
  time it is readable. Anyone can read the same form.
- **not investment advice.** Nothing here is a recommendation, and the cockpit is not a
  licensed adviser.
- **not backed by a backtest.** This document contains no performance figures, no hit rates
  and no expected returns, because none were measured and none would be trustworthy if they
  had been.
- **not the reliable part of the system.** [[CLAUDE]] states the position this corpus
  holds and RW-16 does not amend: *the reliable ROI is time, awareness and discipline — not
  alpha.* This is an Engine-2 feature — mechanically reliable, financially experimental,
  capped, and gated. What it buys reliably is **awareness** (a followed member's disclosed
  trades reach you as structured, cited, deduplicated evidence instead of a news headline)
  and **discipline** (any action is bounded by a policy you wrote down in advance and sized off
  *your* portfolio — and approved by you, unless you have turned RW-17 on, in which case it is
  bounded by the budget rails instead).

If you take one thing from this document: **the feature mirrors a delayed public filing. That
is all it does.**

---

## 1. The data sources, and the real reporting lag

| | House | Senate |
|---|---|---|
| Index | Clerk's per-year filing ZIP (`<YEAR>FD.xml` inside it) | eFD search UI |
| Documents | PTR **PDFs** | electronically-filed **HTML** tables (and PDFs) |
| Provider | `app/ingest/congress/house_clerk.py` | `app/ingest/congress/senate_efd.py` |
| Default | opt-in (`COCKPIT_CONGRESS_ENABLED`) | separately opt-in, **off** even when congress is on |

**The index is not a trade feed.** `HouseIndexEntry` is a deliberately separate type from
`Disclosure`: it carries who filed, what type, and when — no ticker, no amount, no
transaction. Treating an index row as a signal is the single most dangerous misreading
available in this feed, so the type makes it impossible.

**The PDF seam.** This package never opens a PDF. Extracting text from one means a heavy
third-party dependency on the ingest path of a security-sensitive cockpit, and RW-16's scope
does not justify that. The provider takes an injected `pdf_extractor` callable
`(bytes, doc_id) -> str` that defaults to `None`; with none wired, every PTR document is
counted as `pdf_extractor_unavailable` — a loud, named "wired but cannot read its documents"
state. **Out of the box the House provider yields awareness of who filed and when, and zero
transactions.** That is an honest partial feed, not a silent zero.

### The lag is the whole story

The STOCK Act requires a periodic transaction report within **30 days of becoming aware of a
transaction, and no later than 45 days after it**. So:

- by the time a filing is readable the trade is up to **45 days old** — and members do file
  late;
- `Disclosure.age_days` is measured from the **transaction date**, not the filing date,
  because the filing date is when *we* learned, and the trade date is when the information
  was worth something;
- a signal with a statutory six-week lag has **no urgency**. That is why the copy path emits
  a `limit` order priced at the gate quote and never a `market` order: there is nothing here
  that could justify paying for immediacy.

Anyone reading this feed as "front-running Congress" has the direction of time wrong.

### Untrusted input, treated as such

A filing is typed by a third party and is **untrusted input** ([[prompt-injection-defense]],
SEC-02). Every free-text field that survives into a `Disclosure` — the filer's display name
and the asset description — goes through `ingest.normalize.escape_untrusted` at the parse
boundary, not at some later display site a future caller might bypass. The asset description
is also the *only* field on a House PTR row that the member types; every other column is the
form's. That asymmetry is the security model of the row parser (§7).

---

## 2. Disclosed amounts are RANGES, never fills

A PTR discloses a **bucket**: `$1,001 - $15,000`, `$1,000,001 - $5,000,000`, or the
open-ended `$50,000,001 +`. It never states a share count, never states a price, and the
bucket is frequently two orders of magnitude wide. It is also a number about **someone
else's balance sheet** — a $250k purchase may be 0.5% of one member's portfolio and 40% of
another's.

So the disclosed amount is used for exactly **one** thing:

```
notional = YOUR portfolio value
         x copy_fraction            (policy, default 0.02)
         x tier_weight(bucket)      (a bounded conviction weight, 0.50 .. 2.00)
         x staleness_factor(age)    (§4)
         x options_haircut          (§3, options only)
   then clamped to [min_notional_per_trade, max_notional_per_trade]
```

`copytrade/sizing.py` holds the tier table as an explicit nine-line lookup rather than a
log-scaled curve, so a reviewer can read the whole sizing policy and say whether they agree
with it. The weights are **flat-topped above $1M**: the marginal information in "even bigger"
is small, and letting the weight keep climbing would let one outlier filing dominate.

The parse of the bucket is **exact-tier-match only**. A pair that merely looks plausible
(`$1,001 - $16,000` off a bad text layer) is refused and counted as `unknown_amount`, never
rounded into a neighbouring tier — the tier *is* the conviction signal, so a silently-widened
bucket is a silently-larger position.

**Copying the bucket midpoint — the intuitive move — would import a stranger's risk budget
into your account. The code cannot express it.**

---

## 3. Options: translated, never copied 1:1

> The operator's requirement, verbatim: *"for options trades do not copy them one for one as
> we get delayed information."*

Three independent reasons, any one sufficient (`copytrade/options.py`):

1. **The instrument may not exist any more.** A congressional options position is frequently
   a weekly or a near-dated monthly. Thirty to forty-five days later the contract can be
   expired, deep out of the money, or trading at a price with no relation to what the
   discloser paid.
2. **The risk engine does not support options at all.** `risk.engine.SUPPORTED_ASSET_TYPES`
   is exactly `{"equity"}` and the check is HARD. An options proposal is therefore not a
   risky order — it is an order that **cannot be adjudicated**, and Invariant 1 says nothing
   reaches a broker except through adjudication. Making options copyable would mean widening
   the order-path gate for a feed we do not control (Invariant 5: `risk/` does not change).
3. **The filing does not say enough to price one.** No premium, no quantity, no fill price —
   only a bucket that lumps the premium in with everything else.

So what survives the lag is the **direction** of the discloser's conviction, and the only
vehicle this system can hold is the underlying equity:

| Disclosed | Translation | Why |
|---|---|---|
| long calls (purchase) | small long in the underlying, **haircut** | bullish; the only branch that opens exposure |
| long puts (purchase) | **exit** an existing position | bearish, and shorting is forbidden (`sell_backed`). If nothing is held the signal is structurally unusable and is **dropped**, never inverted into an invented short — counted as `option_exit_not_held` |
| any close (sale / partial / exchange) | **exit if held** | closing? rolling? assigned? expired worthless? The filing does not distinguish them, and every ambiguity here resolves toward *reducing* exposure |

### The expiry gate

`options_min_days_to_expiry` (default 30) drops any options-derived intent whose disclosed
expiry is nearer than that; an expiry already past drops as `option_expired`. An expiry
falling exactly on today reads as *near* expiry, not *expired* — both drop, so nothing unsafe
hangs on which side of the line it lands. **That claim is enforced, not asserted:** the policy
refuses `options_min_days_to_expiry < 1`, because at `0` a 0-DTE contract passes both tests
and a contract worthless within hours would open a fresh multi-week equity position.

An expiry the filing never stated does **not** drop — the gate cannot be evaluated, the
translation is a haircut position in the underlying rather than a contract, and the intent
carries `option_expiry_undisclosed` so an operator can see which ones skipped the check. An
expiry the filing *did* state but that cannot be resolved to one date (several readable dates
in the description, no `exp:` keyword) is a different thing entirely and is a **counted parse
refusal** — `unparsed_row:ambiguous_option_expiry`. "Unknown" is an admission; "unreadable" is
not allowed to become one.

### The haircut, and why it survives the clamp

A call buyer paid a premium for leverage. Buying the underlying at the same notional would
take several times their delta; buying it at the same delta would need a contract count the
filing does not disclose. So the position is deliberately small — `options_haircut`, default
**0.25** — and the number is a policy knob an operator reads, not a model output.

Discounting only the raw size is not enough. Above some portfolio value the discounted size
and the full size both exceed `max_notional_per_trade`, both clamp to it, and the two become
byte-identical — the requirement silently stops holding at exactly the account sizes where it
matters most. So `sizing.sized_copy_notional` discounts the **ceiling** by the same
multiplier and guarantees the inequality in three layers:

> for identical inputs, an options-derived notional is **strictly less** than the
> equity-derived one, at every portfolio value, including ones where both would clamp.

And the inequality has to survive **share quantization**, not merely hold in dollars: the
number that reaches a human and a broker is `floor(notional / quote_price)`, and `floor` is
not injective. A collision needs `h/f > 1/2`; because the ceiling is discounted by the same
multiplier, `h <= haircut * f` exactly. So `options_haircut` is bounded at
**`MAX_OPTIONS_HAIRCUT = 0.5`** (`copytrade/policy.py`), which makes a collision
arithmetically impossible at every price and every portfolio value.

`options_policy=ignore` turns the whole translation off: options disclosures are surfaced for
awareness and never acted on.

---

## 4. Staleness: the gate and the decay

Two separate controls, both measured from the **trade date**:

- **the gate** — `max_age_days` (default **45**, the STOCK Act deadline). A disclosure whose
  trade is older is refused outright (`stale_disclosure`). The default means *act on a filing
  that arrived on time, refuse one that did not.* Tightening it is the single most effective
  knob in the policy.
- **the decay** — `staleness_decay` (default **0.5**). Size falls linearly across the window:
  a trade disclosed at day 45 is sized at half of one disclosed today. Set it to 0 and a
  44-day-old signal is priced as if it were fresh; that is allowed and it is not advised.

A trade date in the **future** is a filing typo or a parse error and fails closed
(`future_tx_date`) — the sizing curve rewards freshness, so a future date is the single most
dangerous value the field can hold.

---

## 5. The universe-promotion lane (SEC-15)

`risk.engine.evaluate`'s `symbol_allowlist` check is `symbol in holdings or symbol in
watchlist` — the reconciled trust anchor that stops an order being drafted for a name nobody
admitted into the system. A copy-traded congressional name is, **by definition**, not on the
operator's static watchlist: the whole point of the feed is that it surfaces names the
operator did not pick.

Three options existed. Widening the static watchlist was refused (a feed that can write to
the operator's hand-maintained list turns an allowlist into a suggestion box). Weakening the
risk check was refused (Invariant 5, and a check with an exemption for one caller is not a
check). What was built is the third: an **audited, bounded, TTL-expiring promotion lane**
(`copytrade/universe.py`).

- **Promotion is not approval to trade.** It is strictly weaker: it moves a symbol from "the
  risk engine will reject this on sight" to "the risk engine may now evaluate it on its
  merits". Every other hard check, the mode ladder, and the human gate all still run.
- **The audit chain is the store, not a table.** The promoted set is an input to a risk
  check, so a mutable `copy_universe` table would let any local DB writer make an arbitrary
  symbol tradable with no HMAC key and no trace. Promotions are HMAC-signed
  `EVENT_COPY_UNIVERSE` entries; an entry that fails `verify_entry` is skipped, and a chain
  that fails `verify_chain` yields the **empty** universe.
- **Bounded and expiring.** `max_universe_symbols` (default 40) caps it and
  `universe_ttl_days` (default 120) expires it. `promote` **refuses the overflow and counts
  it — it never evicts an incumbent**, because eviction-to-make-room could silently drop a
  held name out of the allowlist under load.
- **It can only ever prevent a NEW BUY, never block an EXIT** — the allowlist is `holdings OR
  watchlist`, and a sell must be a reconciled holding anyway, so expiry can never strand a
  position.
- **Both sides read the same authority AND the same bounds.** The submit-time re-check in
  `brokers/paper_execution.py` reads the same signed chain the draft did, under the
  `universe_ttl_days` / `max_universe_symbols` that **drafted the order** — recorded on the
  proposal's own lineage (`UNIVERSE_BOUNDS_REF`). Re-deriving them from `Settings` made the
  submit-time allowlist potentially *wider* than the authority that drafted the order, which
  is the split-brain `copytrade/universe.py` warns about. No recorded bounds ⇒ empty universe
  ⇒ fail closed.

---

## 6. The order path, idempotency, and retries

`run_copy_cycle` ends at an **`Order` row in state `proposed`**. It does not call
`OrderGateway`, mint a capability token, approve, or post (Invariant 1). There is no model
anywhere on the path: the proposal text is composed from the filing's own facts, the size
comes from `copytrade/sizing.py`, and the verdict comes from `risk.engine.evaluate`.

**That is still where this package stops.** Who picks the `proposed` row up is a separate
decision made one layer out: the human paper gate (`brokers/paper_execution.py`), or — with
`COCKPIT_CONGRESS_AUTOEXEC` on — RW-17's `PaperAutoExecutor`, which queues it behind a notified
cancel window and then adjudicates it itself. Neither is a second order path: both reach the
broker only through the same `OrderGateway` with a real, signed approval (§8, and the banner at
the top).

**A rejected proposal is not a completed copy.** Idempotency is keyed on
`Disclosure.key` = `chamber:doc_id:line_no`, but a risk rejection is usually *environmental*
and transient — no quote seam wired, a stale quote, an absent `PortfolioSnapshot`, momentary
buying power. The first build burnt the idempotency key on any persisted record, so one bad
minute meant that filing was never copied again. So:

- a rejection stays **retryable**, with its own proposal id per attempt
  (`proposal_id_for(key, attempt)`), each keeping its own `ProposalRecord` + `RiskDecision`
  so an operator can read why each attempt failed;
- retries are bounded by **`MAX_COPY_ATTEMPTS = 3`** (the belt) and by the staleness gate,
  which ages the filing out on its own (the braces);
- **and "a human owns it" means an `Order` exists.** A `needs_human` record with
  `order_id IS NULL` — the SEC-04 HALT window, or a size that floors to zero shares — is
  *not* consuming, because `PaperExecutionService` adjudicates orders and nothing wired can
  reach a record without one. Treating it as consuming meant every disclosure seen during a
  killswitch window was lost forever while the cycle reported it as `already_copied`.

**One real trade, one copy.** An amended PTR gets a **new `doc_id`** from the clerk, so it
enters as new evidence with a new key and a new proposal id. `trade_identity`
(symbol + side + trade date) suppresses the second order; correcting the *trade date* is one
of the commonest reasons a PTR is amended, so a restatement whose date moved by up to
`TRADE_DATE_WINDOW_DAYS` (3) is also suppressed, under its own reason
(`trade_restated_nearby`) so a near miss is never indistinguishable from an exact one. The
guard's memory is the signed chain, windowed by **time** (twice `max_age_days`) rather than by
entry count — an entry-count window was a quantity the untrusted feed controlled, since every
risk-rejected draft also writes one. If its memory bound is reached inside that window the
cycle **refuses** rather than proceeding on partial history.

**The batch has to cover the window.** `engine._contested_sides` reads a symbol's
disagreement off the whole batch, ahead of every time-varying gate, so an opposing filing
cannot age out and leave a one-sided buy of a name a followed member exited. That guarantee
only holds while the opposing filing is *in the batch*, so `load_disclosures` reports its
coverage (`window_start`, `truncated`) and `run_copy_cycle` refuses a batch that cannot
support the check (`disclosure_window_truncated` / `disclosure_window_too_narrow`).

**Every non-emitting outcome is counted and named.** `engine.ALL_SKIP_REASONS` is a closed
vocabulary the operator surface aggregates on. "No trades today" has to be a positive
statement — a copy engine that silently drops most of its input is indistinguishable from one
that is broken.

---

## 7. The row parser's security model

On a House PTR **the member types the asset description and the form prints every other
column.** A member can therefore type a convincing copy of the remaining columns into the
asset name. The tier is the entire conviction signal, so a forged `$50,000,001 +` is a forged
maximum-size position.

The columns are located **structurally**, never positionally:

1. the bracketed asset-class code (`[ST]`, `[OP]`, …) is the form's own delimiter, printed
   *after* the asset name — so everything the filer typed lies to its **left**. Each class
   code owns the text up to the next one;
2. **unambiguous or nothing.** The form prints exactly one column group per row. Two groups
   after a class code is filer free text (a House PTR prints the typed FILING STATUS /
   DESCRIPTION sub-fields *after* the amount); two class codes each carrying their own group
   is two printed rows merged onto one extracted line. Both are counted refusals
   (`unparsed_row:ambiguous_columns`), never a choice between them;
3. the **structured class code outranks the description in both directions.** `[ST]` means
   stock even when the filer typed "put options" into the name, and `[OP]` means option even
   when the name says "common stock". Free-text option vocabulary is consulted only on a row
   with no structured class at all — otherwise "Option Care Health Inc. (OPCH) Common Stock"
   is silently dropped and a disclosed stock **purchase** can translate into a **sale** of the
   underlying;
4. every digit gate is **ASCII-only** (`re.ASCII` / `[0-9]`). `\d` is Unicode-aware and
   `int()`/`Decimal()` accept Unicode digits, so a fullwidth-digit amount would otherwise
   launder into a real STOCK Act tier — the same aliasing `validate_ticker` and the doc-id
   gate already refuse;
5. a ticker is validated **raw**, before any normalization, so a padded or look-alike
   spelling cannot impersonate a real one. Two different candidates on one line ⇒ `None`.

And the fail-closed default of the asset classifier is `ASSET_OTHER`, which `copytrade`
never trades. An unknown transaction code does not fall back to "purchase"; an amount outside
the tiers does not fall back to the smallest bucket; an option whose direction is ambiguous
(both "call" and "put", or neither) is `ASSET_OTHER`, because guessing an option's direction
is the most expensive wrong guess available in this package.

---

## 8. ⚠️ SEC-08 — this feature is PAPER-COMPLETE and LIVE-BLOCKED

**Read this before planning anything on top of RW-16.**

The copy path's provenance string is `producing_runtime = "deterministic:copytrade"`, a
**module constant** that a caller cannot override (`run_copy_cycle` refuses the whole cycle,
counted and named, if anything else is passed — a security boundary whose value is chosen by
the code on the far side of it is not a boundary).

`orchestrator.mode_ladder.assert_execution_runtime` **REFUSES** any runtime that is not
literally `"claude"` once the ladder is at `review_live`, `auto_veto` or `auto_full`. That
gate is not weakened and not routed around. `run_copy_cycle` calls it **first** and, on
refusal, produces nothing at all: every disclosure comes back as a counted `Skip` with reason
`sec08_runtime_refused`. `PaperExecutionService` re-checks it and admits the deterministic
runtime **in `paper` and nowhere else**; `OrderGateway` re-checks it again at submit.

Consequences, stated plainly so nobody discovers them in production:

- in **`paper`** the feature works end to end: disclosure → intent → risk gate → `Order` row →
  either human preview → symbol-confirmed approval, **or** (RW-17, opt-in) queued notification →
  cancel window → audited auto-adjudication → the fixed paper transport;
- in **`read_only`** the risk engine rejects on `mode_allows_order`;
- in **any live/auto rung it emits nothing. Congressional copy-trading is unavailable with
  real capital.**

**This is not a bug to be patched.** SEC-08 exists because Invariant 2 pins the *decision*
path to Claude in live/auto modes, and this path's decisions are made by deterministic code
whose provenance string is, correctly, not "claude". Making it live requires an explicit
operator decision about whether a model-free deterministic producer is admissible under SEC-08 —
**a decision that has not been made** ([[REGISTER]] `RW-16-LIVE`, status `blocked`). Note that
RW-17 *narrowed* the question rather than answering it: the producer is no longer human-gated in
paper, so the decision now has to be made about a deterministic producer that also adjudicates
itself. Until it is made, the honest posture is a feature that visibly refuses rather than one
that quietly widens what "claude" means. **Do not add a special case for the string.**
`tests/workflows/test_copytrade_orders.py` pins the refusal and says why.

### The cycle IS scheduled now — for paper, and only behind the RW-17 lane

**This section previously said there was no trigger. That is no longer true, and the change was
deliberate.** The original reasoning was that a cron job "would quietly manufacture proposals off
a 30–45 day-old signal into an approval queue nobody asked to fill". The operator asked to fill
it — and asked for it to be adjudicated without them (`app/copytrade/autoexec.py` carries the
verbatim request). RW-17 is that lane.

What is wired today:

| | |
|---|---|
| `congress_copy_cycle` | 10:40 / 12:40 / 14:40 ET, weekdays, skipped when the market is closed. Three ticks a day is also the retry budget: `MAX_COPY_ATTEMPTS = 3`, spent across one session rather than inside one bad minute |
| `autoexec_resolve` | every minute, all week — fires or refuses each ticket whose cancel window has closed |
| `autoexec_fills` · `autoexec_digest` | the lane's reports: fills every 5 min, the unconditional daily statement at 16:20 ET on weekdays |
| `order_status_poll` | RW-15 (minimal): drains resting paper orders so the SEC-07 pending-exposure cap does not fill forever |
| `/cancel <proposal_id>` | the operator's ONLY lever — a **veto**, never an approval |

**All of it is gated on `COCKPIT_CONGRESS_AUTOEXEC`, which is off by default.** With the flag
off, `build_paper_autoexec` returns an explained `not_provisioned` wiring and **no copy-cycle job
and no resolve job are registered** — a congress-awareness-only deployment keeps exactly the
RW-16 posture it had. `tests/system/test_congress_wiring.py` pins both the absence and the single
call site:
`test_the_scheduler_registers_NO_copy_cycle_job_without_the_autoexec_lane` and
`test_the_copy_cycle_IS_WIRED_from_exactly_ONE_place`.

**Scheduling the cycle cannot make live capital reachable.** The cycle still passes the module
constant `"deterministic:copytrade"`, `assert_execution_runtime` still refuses it above `paper`,
and the executor re-reads `ModeLadder.current() is Mode.paper` under the lock at the instant of
every trade — a mode change mid-window **voids** the ticket rather than following it.

Operating it (how to turn it on and off, the kill switch, every refusal and what to do about it,
and how to tell a quiet day from a broken lane) is [[runbooks/congress-autoexec]].

Ingest **is** scheduled independently of all of this, because reading disclosures is Engine 1:
it is awareness, it moves nothing, and it runs under HALT like every other feed.

---

## 9. Known limitations — what the adversarial rounds found, and what is still open

Three adversarial rounds ran against RW-16 (the attack → fix → regression pattern in
[[ultracode-build-playbook]]). Everything below is a **reproduced** failure, not a
hypothetical.

### Closed, each with a regression test that fails without its fix

| Area | What was wrong |
|---|---|
| parser | filer free text could forge the form's own columns — first from the **left** of the class code, then (after the first fix inverted it) from the **right**, and again with two printed rows merged onto one line. Now: one class code owns one column group, and anything else is a counted refusal |
| parser | the structured `[ST]`/`[OP]` column was outranked by the description, flipping a disclosed stock **purchase** into a **sale** of the underlying, and dropping real equities whose *name* contains "option" |
| parser | bare (keyword-free) expiries were unreadable, so the expiry gate never ran; multi-date option rows still skipped it silently and are now a counted refusal |
| parser | non-ASCII digits laundered into real tiers and dates |
| parser | the XXE/entity gate inspected only the first 4 KB; a legal XML comment walked it |
| parser | `extract_symbol` normalized a ticker *before* validating it, laundering spellings `_canonical_ticker` refuses |
| sizing | the options haircut was erased by the per-trade clamp (byte-identical sizes), and then again by **share quantization** at the layer that actually trades |
| policy | `options_min_days_to_expiry` had no validation at all; `0` (a supported `Settings` value) admitted 0-DTE contracts as fresh multi-week longs |
| policy | `Settings` accepted `options_haircut=1` — the exact value `CopyPolicy` forbids — turning a boot-time config error into a silently dead feature at runtime |
| engine | a two-sided conflict resolved itself as one side aged out, emitting a one-sided **buy** of a name a followed member exited; then the fix's remaining `both_live` branch left the **exit** flipping with the calendar |
| store | `load_disclosures` returned the **oldest** N rows, so past the cap every filing failed the staleness gate and the feature stopped trading while looking healthy; the row cap also silently disabled itself for a falsy `limit`, and could evict an opposing filing out of the contest window |
| order path | copy orders had `job_id=None` and could **never be adjudicated** — the feature could not complete a trade even in paper |
| order path | the submit-time SEC-15 re-check consulted the static watchlist (guaranteeing every copy order failed), then the same audited chain under **different bounds** than the draft |
| order path | a transient risk rejection permanently consumed a filing; then a `needs_human` record with no order did the same thing through the other branch |
| order path | an amended filing produced a second independent order for one real trade — and after the exact-hash guard, one whose *trade date* was corrected still did |
| order path | the amendment guard's memory was an entry count on a stream the untrusted feed controls |
| SEC-08 | `producing_runtime` was a caller-supplied kwarg, so any caller could pass `"claude"` and unlock the live rungs |

### Open, and stated rather than hidden

- **No PDF extractor ships.** Without one the House feed produces awareness and zero
  transactions (`pdf_extractor_unavailable`). The seam is injected and the parsing logic is
  pinned against fixture text, so wiring one is additive — but it is not wired.
- ~~**Nothing is wired to trigger a cycle.**~~ **Closed by RW-17** (§8): the cycle is scheduled
  and `load_disclosures` has a production caller, but **only** when `COCKPIT_CONGRESS_AUTOEXEC`
  is on. With the flag off — the default — the original statement still holds exactly: no
  scheduler job, no route, no command, and nothing reads `CongressDisclosure` rows back.
- **RW-17's terminal refusals consume the disclosure.** `workflows/copytrade_orders._consuming`
  treats "an `Order` row exists" as "this filing was copied", so a ticket killed by a restart
  inside its cancel window (`abandoned_restart`) or by the six-hour ticket expiry
  (`ticket_expired`) means that filing is **never re-proposed**. Both are loud, named and
  notified; neither is recovered. See [[runbooks/congress-autoexec]] §9.
- **`TRADE_DATE_WINDOW_DAYS = 3` has a real false-positive.** A followed member who genuinely
  buys the same name twice inside three days gets one copy, not two. That is the direction
  `trade_identity` errs in by design ("the direction to err in is fewer orders"), and the
  suppression is counted under its own reason — but it is a residual, not a non-issue.
- **A multi-date option row is refused outright**, including honest rows that simply mention
  a second date. Fail-closed, counted, and a real loss of coverage.
- **`line_no` is the candidate-row ordinal**, so changing the candidate-detection rule can
  shift ordinals and re-key rows. Amended filings re-issue under a new `doc_id` and are
  unaffected; a parser change to `_CANDIDATE` is not.
- **The Senate provider is the flakier half.** Its search UI is agreement-gated and its
  documents are far less machine-readable than the Clerk's, which is why it is separately
  opt-in and off by default.
- **`escape_untrusted` corrupted `&`** in filing text (`AT&T` → `AT&amp;amp;T`) because it was
  stacked 2–3 deep and was not idempotent. Fixed by making the escaper a genuine fixed point;
  it is shared by *every* feed, so any future change to it is a cross-feed change.
- **No measurement of any kind.** There is no backtest, no paper soak, and no evaluation of
  whether following any particular filer is worth doing. The feature is mechanically correct
  and financially unproven, and this document will not pretend otherwise.

---

## 10. Where to look

| Question | File |
|---|---|
| what a filing *means* | `app/ingest/congress/ptr_parse.py` (pure; no I/O, ever) |
| how documents are fetched | `app/ingest/congress/{house_clerk,senate_efd,provider,poller}.py` |
| every policy knob in one place | `app/copytrade/policy.py` |
| how a bucket becomes a dollar size | `app/copytrade/sizing.py` |
| the options rule | `app/copytrade/options.py` |
| what gets copied, in what order | `app/copytrade/engine.py` |
| the promotion lane | `app/copytrade/universe.py` |
| disclosure → `Order` row | `app/workflows/copytrade_orders.py` |
| the store and its coverage contract | `app/schemas/models/congress_disclosure.py` |
| the human gate | `app/brokers/paper_execution.py` |
| the zero-touch paper lane (RW-17) | `app/copytrade/autoexec.py`; rails `budget.py`, messages `notify.py`, digest `digest.py` |
| the one sanctioned auto-order entry | `app/brokers/auto_paper_submit.py` |
| operating the lane | [[runbooks/congress-autoexec]] |
| the env knobs | [[setup/accounts-and-keys]] §7, `app/.env.example` |
