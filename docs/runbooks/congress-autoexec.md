---
title: RW-17 — the zero-touch PAPER copy lane (operations runbook)
type: runbook
status: live
last-verified: 2026-08-10
related:
  - README
  - REGISTER
  - congress-copytrade
  - refactor-plan
  - security-hardening-backlog
  - setup/accounts-and-keys
  - rw01-live-smoke
---

# Congressional auto-execution — runbook

**This lane trades without you.** When it is armed, a congressional copy proposal is notified to
you, waits out a cancel window, and then executes on the **paper** transport with no approval
tap. Your only lever is `/cancel` inside the window; you never have to act for a trade to
proceed. That is the operator's stated requirement, not an accident.

You are probably reading this because something looks wrong. Start at §1, then §7 (the refusal
table) or §8 (quiet day vs broken lane).

> **Paper only, by construction.** The lane resolves only at `mode == paper`, submits only
> through the paper-scoped `OrderGateway`, and `brokers/auto_paper_submit.auto_paper_place`
> raises `CapabilityError` if handed anything else. **`RW-16-LIVE` / SEC-08 is still open, so
> live is unreachable** — see [[congress-copytrade]] §8. Nothing in this runbook can move real
> capital.

---

## 1. Stop it now

| You want to… | Do this | Effect on tickets already queued | Reversing it |
|---|---|---|---|
| **Stop every trade, immediately, out of band** | `.venv/bin/python -m orchestrator.halt --set --reason "..."` | **Transient.** Tickets stay live, are refused as `halt` each tick (notified once), and fire when you clear it — unless they hit `ticket_expired` first (6h default) | `--clear`, then they resume |
| **Void every queued ticket permanently** | Move the ladder off `paper` (`GET/POST /mode`, or `ModeLadder.set_mode`) | **Terminal.** Every live ticket is refused `mode_not_paper` and is never re-queued at the new rung | Returning to `paper` does *not* revive them |
| **Stop ONE trade** | `/cancel proposal_id:<id>` in Discord, inside the window | That ticket only. Audited as a `veto`; nothing is sent to the broker | Not reversible — the disclosure is consumed |
| **Turn the lane off for good** | `COCKPIT_CONGRESS_AUTOEXEC=false`, restart | Nothing fires. On the next boot `recover_orphans` settles each announced-but-unresolved ticket as `abandoned_restart`, notified | Set it back to `true` and restart |

Two things worth knowing before you pull one:

- **The killswitch (`orchestrator/killswitch.py`) does *not* change the mode on this lane.**
  `drop_to_review_live` is a no-op at or below `review_live`, and this lane runs at `paper`. What
  actually stops it is the HALT sentinel the killswitch sets first. So a killswitch is the
  *transient* row above, not the *terminal* one.
- **A restart is not a stop.** It is a stop *for the tickets in flight* (they settle as
  `abandoned_restart`), but the cycle will queue new ones on the next 10:40/12:40/14:40 tick
  unless the flag is off.

---

## 2. What is armed, and how to tell

The lane is composed by `main.build_paper_autoexec` and is **all-or-nothing**. It refuses to
exist — rather than degrading — if any of these is missing: the opt-in flag, a paper-scoped
`OrderGateway`, a delivery seam with **every** channel in `copytrade.notify.CHANNEL_BY_EVENT`
mapped, the gate-(d) risk re-check seam off `PaperExecutionService`, or a valid `CopyPolicy`.

**Boot log, one line, the ground truth:**

```
RW-17 PAPER auto-execution is ARMED: congressional copy trades will execute on the PAPER
transport 300s after you are notified, with no approval tap. Your only lever is /cancel inside
that window. Rails/day: 500 notional, 3 trades, 10 open positions, 250 per symbol. Tickets
abandoned by the last restart and settled now: 0.
```

If it is **not** armed, the same boot writes an `unprovisioned` line naming the reason, and
`app.state.autoexec.status()` returns `{"kind": "not_provisioned", "reason": "..."}` instead of
`{"kind": "armed", "execution_scope": "paper", "cancel_window_seconds": …, "budget": {…}}`.

> **`GET /status` does not currently include the lane.** The wiring object lives on
> `app.state.autoexec`; the `/status` route reports mode, usage, breakers and ingest only. Read
> the arming decision from the boot log.

`Settings` refuses to boot at all if `COCKPIT_CONGRESS_AUTOEXEC=true` in a configuration that
could never trade — congress ingest off, an empty follow list, a daily or per-symbol rail below
`COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE`, or a ticket max-age at or below the cancel window.
That is deliberate: each of those is a lane that boots clean and copies nobody, which is
indistinguishable from a quiet month.

---

## 3. The schedule — what runs when

All crons are **US/Eastern**. Every job swallows its own exceptions (a report that kills the
scheduler is worse than the gap it was reporting).

| Job | Cron | What it does | If it never runs |
|---|---|---|---|
| congress ingest | `:20` hourly, Mon–Fri | Stores disclosures. Engine 1; runs under HALT like any feed | Nothing to copy — looks like a quiet month |
| `congress_copy_cycle` | `10:40, 12:40, 14:40`, Mon–Fri, **skipped when the market is closed** | Loads the disclosure window, runs the deterministic cycle, queues each resulting `Order` as a ticket | No tickets are ever created |
| `autoexec_resolve` | **every minute, all week** | Fires or refuses every ticket whose window has closed; retries queued + terminal notices | Tickets never resolve and no outcome is ever delivered |
| `autoexec_fills` | every 5 min, all week | Turns signed `filled` transitions on lane orders into `filled` notices, once each | Your last word on every trade stays "submitted" |
| `autoexec_digest` | `16:20`, Mon–Fri | The end-of-day statement (§8) | You lose the only unconditional message |
| `order_status_poll` | every 5 min, all week | RW-15 (minimal): drains resting/partial paper orders, which is what keeps the SEC-07 pending-exposure cap from filling forever | The lane eventually refuses everything as `pending_exposure` |

Three ticks a day is also the retry budget: `MAX_COPY_ATTEMPTS = 3`, so a transient risk
rejection is retried hours later at a genuinely different market state, not 60 seconds later
against the same broken quote.

---

## 4. What a notification looks like

Five message shapes, plus the digest, on three logical channels. All are plain text through the
abstract `deliver(channel, text) -> bool` seam — no Discord types in `copytrade/notify.py`.

| Event | Channel | Env var for the id | Gates a trade? |
|---|---|---|---|
| `queued` | `#proposals` | `COCKPIT_PROPOSALS_CHANNEL_ID` | **Yes.** Unconfirmed ⇒ the trade never fires |
| `submitted` | `#alerts` | `COCKPIT_ALERTS_CHANNEL_ID` | No — it already happened |
| `filled` | `#alerts` | `COCKPIT_ALERTS_CHANNEL_ID` | No |
| `cancelled` | `#alerts` | `COCKPIT_ALERTS_CHANNEL_ID` | No |
| `refused` | `#alerts` | `COCKPIT_ALERTS_CHANNEL_ID` | No |
| `daily_digest` | `#brief` | `COCKPIT_BRIEF_CHANNEL_ID` | No |

The **queued** message is the whole decision surface. Field order is a literal, so the shape is
deterministic; the cancel line sits above everything because `bridge.deliver.clamp` truncates the
**tail**:

```
**AUTO-TRADE QUEUED — BUY <SYMBOL> $<size>**
Congressional copy, PAPER account. This WILL execute unless you cancel it.

**CANCEL:** `/cancel <proposal_id>`
**Time left:** 5m 00s

__Filing__
Filer: <filer as disclosed>
Trade date: <ISO> · disclosed: <ISO>
Disclosure lag: <N> days (statutory; this is public, delayed information — not an edge)
Disclosed range: <the PTR bucket>

__Why this size__
<the deterministic sizing sentence>

__Risk (deterministic RiskDecision)__
verdict: approved | ceiling: $<…> | failed: none | warnings: none

_proposal <proposal_id> · source <chamber:doc_id:line_no>_
```

Things that are true about every message here and worth knowing when one looks odd:

- **Nothing is model-authored.** Every value is a stored fact off the filing or off
  `copytrade/sizing.py`; the risk block is rendered from the stored `RiskDecision` **row**, read
  back by id (SEC-17). A `dict` in that position is refused.
- **A message that cannot be rendered honestly is not sent** — and an unsent `queued` message
  means the trade does not happen. `missing_queued_facts` refuses on a missing symbol, side,
  notional, filer, trade date, filing date, negative disclosure lag, amount label, sizing reason,
  cancel command or window.
- **Only a literal `True` from the deliver seam counts as delivered.** `None`, `1` or an object
  is `delivery_not_boolean` and reads as undelivered.
- **A refusal always names a gate** (§7). An unrecognised code renders as
  `⚠ (unrecognised refusal code)` and is counted as `refused.unknown_gate` — that means the
  executor and the notifier have drifted, not that your lane has a new failure mode.

---

## 5. Cancelling one trade

```
/cancel proposal_id:<id>
```

The id is in the queued message. `/cancel` with no id prints the usage line **and** what is
currently queued.

| Reply | Means |
|---|---|
| `🛑 CANCELLED <id> — it will not be submitted. (cancelled)` | Done. Audited as a `veto`; nothing was sent |
| `⚠ NOT cancelled: <id> — already_executed` | The window closed before you typed. Check `#alerts` for the `submitted` notice |
| `🛑 CANCELLED <id> … (already_cancelled)` | Idempotent second tap. No second effect |
| `⚠ NOT cancelled: <id> — unknown_ticket` | This process does not hold that ticket. Usually a **restart** — look for an `abandoned_restart` refusal notice |
| `⚠ NOT cancelled: <id> — already_resolved` | It was refused by a gate first. The refusal notice says which |
| `Not authorized.` | Your user id is not in `COCKPIT_OPERATOR_IDS`. The attempt is audited as `unauthorized_actor` |
| `⚠ /cancel … returned an unreadable outcome` | Treat the trade as **still queued** and check the notification channel |

**How long you actually have.** The window is `COCKPIT_CONGRESS_AUTOEXEC_WINDOW_SECONDS` (default
300s) measured on a **monotonic** clock from the instant the queued notice was *confirmed*, and
the ticket fires strictly **after** that deadline, on the next 60s resolve tick. So the real
window is the advertised one **plus up to one tick** — never less. Extra cancel time can only
ever prevent a trade.

A cancel landing at T−1s wins: `cancel` and `resolve_due` both take `st.lock`.

---

## 6. Every budget rail, and its default

These four rails are what replaced your approval tap. They are not configuration garnish —
deleting one deletes a safety control (`copytrade/budget.py`).

| Rail | Setting | Default | Counts | Resets | Refusal reason |
|---|---|---|---|---|---|
| Daily notional | `COCKPIT_CONGRESS_AUTOEXEC_MAX_DAILY_NOTIONAL` | `500` | Total $ adjudicated by this lane | Each **UTC** day | `budget_daily_notional` |
| Trades per day | `COCKPIT_CONGRESS_AUTOEXEC_MAX_TRADES_PER_DAY` | `3` | How many things may happen unattended | Each **UTC** day | `budget_trades_per_day` |
| Open positions | `COCKPIT_CONGRESS_AUTOEXEC_MAX_OPEN_POSITIONS` | `10` | Distinct names held at once | **Never** — standing bound | `budget_open_positions` |
| Per-symbol per day | `COCKPIT_CONGRESS_AUTOEXEC_MAX_NOTIONAL_PER_SYMBOL_PER_DAY` | `250` | $ in one ticker | Each **UTC** day | `budget_symbol_notional` |

Non-obvious behaviour you will eventually hit:

- **Spend is re-derived from the HMAC-signed chain on every check, never from a counter.**
  Restarting the process does not reset a rail. An unreadable or unverifiable chain means the
  budget is **exhausted**, not zero (`budget_chain_unreadable`).
- **Counted at adjudication, not at fill.** An approval whose submit then failed still consumes
  budget. That over-counts, and over-counting is the safe direction.
- **A slot is opened by an adjudicated buy and released by any signed SELL that reached the
  broker** — including one *you* did by hand. A buy the gateway provably refused before any
  broker call releases its slot; anything ambiguous stays open.
- **`max_open_positions` can never block an exit**, and lowering it stops new names rather than
  stranding the ones you hold. The other three rails *do* apply to exits — a refused exit is a
  named, notified refusal you can act on by hand.
- **UTC vs Eastern.** The rails and the digest are keyed to the **UTC** day; the crons are
  Eastern. A ticket settled after 20:00 ET lands in the *next* day's digest.

### The other bounds (not budget, but they refuse trades)

| Bound | Where | Default | What happens at the limit |
|---|---|---|---|
| Cancel window | `COCKPIT_CONGRESS_AUTOEXEC_WINDOW_SECONDS` | `300` (min 60 = the resolve tick) | — |
| Ticket max age | `COCKPIT_CONGRESS_AUTOEXEC_TICKET_MAX_AGE_SECONDS` | `21600` (6h) | `ticket_expired`, terminal |
| Live ticket queue | `PaperAutoExecutor._MAX_LIVE_TICKETS` | `64` | `queue_full`, terminal, on the new ticket |
| Settled ticket memory | `_MAX_SETTLED_TICKETS` | `1024` | Oldest dropped; a ticket still owing a notice is never dropped |
| Terminal-notice retries | `_MAX_NOTICE_ATTEMPTS` | `5`, backoff `0/60/300/900/1800s` | Abandoned, logged at **error**; survives in the chain + digest |
| Deliver attempts per send | `CopyTradeNotifier(max_attempts=…)` | `2` | Counted under `<event>.<status>` |
| Fill notices per tick | `_MAX_FILL_NOTICES_PER_TICK` | `20` | Backlog drains a tick at a time |
| Copy attempts per filing | `workflows.copytrade_orders.MAX_COPY_ATTEMPTS` | `3` | The filing ages out of the staleness gate |
| SEC-07 pending exposure | gateway `pending_exposure_cap` | deployment config | `pending_exposure` (see §7) |

---

## 7. Every condition that stops a trade — and how to tell which fired

**Read this table with the gate name from the refusal message.** The message prints
``Gate: `<gate>` `` and a `Detail:` line; the table maps it back to the control that fired.

*Terminal* = the ticket is dead and the disclosure stays consumed (`_consuming` will not
re-propose it). *Transient* = the ticket stays live, is re-evaluated on the next 60s tick, and is
re-notified only when the reason **changes** — bounded by `ticket_expired`.

| Refusal reason (chain, counters) | Gate in the message | Kind | What it means | What to do |
|---|---|---|---|---|
| `halt` | `halt` | transient | The SEC-04 HALT sentinel is set — you or the killswitch set it | `python -m orchestrator.halt --clear` when you mean to resume; the ticket resumes on the next tick |
| `mode_not_paper` | `mode_not_paper` | **terminal** | The ladder left `paper` mid-window. A ticket queued in paper is void off it | Nothing to fix on the lane. Returning to `paper` does not revive the ticket |
| `not_paper_transport` | `mode_not_paper` | **terminal** | The composed gateway's `execution_scope` is not `'paper'` | **Wiring bug.** Stop the lane and fix composition — this should be impossible past `build_paper_autoexec` |
| `watch_only` | `breaker_watch_only` | transient | A vendor usage window is exhausted; the whole order path is disabled (SEC-01/L8) | Wait for the window to roll, or check `GET /limits` / `GET /status` |
| `breaker_tripped` | `breaker_order_path` | transient | One or more breakers are TRIPPED and not cleared, per the signed trip/clear ledger. The detail lists them | Investigate the named breaker. In `paper` a trip does **not** move the ladder, so this is the only signal |
| `breaker` | `breaker_order_path` | transient | The synchronous SEC-01 injection/anomaly breaker fired at the firing edge | Treat as a security event; the ticket's `context` is what was scanned |
| `audit_chain_unverified` | `audit_chain_unverified` | transient | `verify_chain()` is not ok. Nothing may be executed or spent against an untrusted ledger | **Investigate before clearing anything.** See SEC-11 / `bridge/witness.py`. Checked *before* the mode so a tamper is not misreported as a config problem |
| `budget_daily_notional` | `budget_daily_notional` | transient | Today's dollar rail is exhausted (or the order's notional is unreadable) | Nothing — it is the rail doing its job. Fresh at the next UTC day |
| `budget_trades_per_day` | `budget_trades_per_day` | transient | Today's trade-count rail is exhausted | As above |
| `budget_symbol_notional` | `budget_symbol_notional_per_day` | transient | This ticker's daily concentration cap is exhausted | As above |
| `budget_open_positions` | `budget_open_positions` | transient | Opening a new name would exceed the standing cap. Detail lists the open names | Close a position (by hand is fine — the lane sees it) or raise the cap deliberately |
| `budget_chain_unreadable` | `budget_unreadable` | transient | The spend could not be derived: chain unverified, anchor mismatch (SEC-11), or DB fault | Same response as `audit_chain_unverified`. Unknown spend must never read as zero spend |
| `pending_exposure` | `pending_exposure` | transient | **SEC-07.** The ledger of approved-but-unfilled orders is at the cap. This is not about your filing | Check `order_status_poll` is registered and running (§3). Resting limit orders that never drain are the cause |
| `positions_unavailable` | `positions_unavailable` | transient | The positions snapshot / risk re-check seam raised. **Nothing was adjudicated and no budget was spent** | Check the broker seam; the ticket retries next tick |
| `resolve_fault` | `resolve_fault` | transient | An unclassified exception inside one ticket's resolve. Isolated — the other tickets were unaffected | **Send us the log line.** `autoexec: ticket <id> raised during resolve` |
| `clock_backwards` | `clock_backwards` | **terminal** | The monotonic clock regressed mid-window. Treated as an attack signal; it also trips the injection breaker | Investigate the host clock/time source. Not a retry |
| `ticket_expired` | `ticket_expired` | **terminal** | The ticket sat unresolved past `..._TICKET_MAX_AGE_SECONDS`. Its quote is stale, so it is abandoned rather than executed late | Look at *what* kept refusing it (the earlier transient notices). The disclosure is consumed |
| `order_missing` | `order_missing` | **terminal** | The `Order` row is no longer in the database | Bookkeeping/DB problem, not a market one |
| `approval_replay` | `approval_replay` | **terminal** | This proposal already carries an auto-approval (or lost the unique-constraint race). Single-use working | Expected after certain faults. If it recurs, look for a duplicate scheduler process |
| `approval_write_failed` | `approval_write_failed` | **terminal** | The approval + its signed adjudication could not be written | DB/chain fault. Nothing reached the broker |
| `gateway_refused` | `order_gateway_refused` | **terminal** | The `OrderGateway` refused. **The detail begins with the gateway stage**: `halt`, `mode`, `breaker`, `risk`, `approval`, `runtime`, `capability`, `order_state`, `broker_error` | Read the stage. `risk` = gate (d) re-run rejected it; `runtime` = SEC-08; `broker_error` = the broker call itself |
| `superseded` | `superseded` | **terminal** | The same proposal was re-queued with a different order while this ticket was live. The earlier one is settled first, so your `/cancel` never silently re-targets | Usually a duplicate cycle. Check for two schedulers |
| `queue_full` | `queue_full` | **terminal** | More than `_MAX_LIVE_TICKETS` (64) tickets are already live | Something upstream produced far too many orders — suspect a parse bug, not the market |
| `abandoned_restart` | `abandoned_restart` | **terminal** | The process restarted inside this ticket's cancel window. **Nothing was sent to the broker.** Deliberately not resumed — the facts and the quote are gone | Expected on every deploy inside a window. The disclosure is consumed; see §9 |
| `undelivered` | `notification_undelivered` | *not a refusal* | The `queued` notice was never confirmed delivered, so the trade will not proceed. The ticket stays live and retries with backoff | Fix the surface. Until then the lane is correctly trading nothing |

Two outcomes in the counters that are **not** refusals:

- `window_open` — the window is still counting down. Not news, not notified.
- `notification_undelivered` — a **counter** incremented every time any notice fails to confirm.
  A non-zero value here on a day with executions is the alarming one: it means a trade happened
  and the message about it may not have reached you. It survives in the signed
  `autoexec_ticket` chain and in the digest.

---

## 8. Is this a quiet day, or a broken lane?

This is the question the whole package is built around, so answer it in this order. **Do not
infer anything from silence** — silence is the one thing this lane is designed never to mean.

### Step 1 — did the 16:20 digest arrive?

It is the only message that arrives whether or not anything happened.

| Digest | Reading |
|---|---|
| **No digest at all** | The lane is not armed, the scheduler is not running, or `#brief` is undeliverable. Check the boot log (§2) and grep `the daily digest for … was NOT delivered` |
| `Executed: 0 · Refused: 0` + `• nothing traded` + `• nothing refused` | Genuinely quiet — **but confirm at step 2** that the cycle actually ran and saw disclosures |
| `__Refused, by gate__` with rows | Not quiet. Read the gate names against §7 |
| `__Budget remaining__ • unavailable — budget rails could not be read (the lane trades nothing)` | **Broken.** The chain is unverifiable or unreadable. Treat as a security event |
| Any gate name flagged `⚠` | Executor/notifier drift — a reachable refusal with no mapping. Report it |

### Step 2 — did the cycle run, and did it see anything?

`run_congress_copy_cycle` returns a dict per tick:
`{"proposed", "approved", "needs_human", "rejected", "skipped", "reasons",
"queued_for_autoexec", "queue_failures"}`, or `{"skipped": "market_closed"}`, or
`{"error": …}`. `reasons` is `copytrade.engine.ALL_SKIP_REASONS` — a closed vocabulary. A cycle
that saw zero disclosures and a cycle that skipped all of them for `stale_disclosure` are
different facts and they look different here.

If `queued_for_autoexec` is 0 while `proposed` is not, read `queue_failures`
(`risk_decision_missing` and exception type names).

### Step 3 — the counters

- `PaperAutoExecutor.counts` — every name in `ALL_RESOLUTION_REASONS`, in memory, **reset by a
  restart**. Fine for "what is happening right now", useless across a deploy.
- `CopyTradeNotifier.counters()` — `"<event>.<status>"` over
  `confirmed / delivery_failed / delivery_raised / delivery_not_boolean / no_deliver_seam /
  render_refused`, plus `refused.unknown_gate` and `<event>.unknown_event`.
  `queued.render_refused` climbing means facts are missing from the tickets, not that the surface
  is down.

### Step 4 — the signed chain (survives restarts; this is the authority)

| Event type | Written by | Read it to answer |
|---|---|---|
| `autoexec_ticket` (`state=queued` / `state=settled`) | `PaperAutoExecutor` | Which trades were promised, and which were settled. A `queued` with no `settled` **is** an abandoned promise |
| `adjudication` with `payload.lane == "copytrade_autoexec"` | `_mint_approval` | Every auto-approval, its symbol/side/notional — this is what the budget rails re-derive from |
| `order_transition`, `order_refused`, `intent:order_submit` | `OrderGateway` | Whether an adjudicated order actually reached the broker |
| `autoexec_fill_notice` | `FillAnnouncer` | Which fills you were already told about |

Every one is HMAC-covered; entries that fail `verify_entry` are skipped in **both** directions,
so a forged `settled` cannot hide an abandoned promise and a forged fill cannot invent a notice.

### Step 5 — log lines worth grepping

```
RW-17 PAPER auto-execution is ARMED                 # armed, with the rails
RW-17: settled N auto-trade ticket(s)               # restart recovery ran
autoexec: ... was NOT confirmed delivered           # a notice missed; queued for retry
autoexec: ABANDONING the ... notice for             # you were NOT told about an outcome
RW-17: ABANDONING the fill notice for order         # you were NOT told about a fill
RW-17: the daily digest for ... was NOT delivered   # the one unconditional message missed
RW-17: the lane's fills could not be read           # NOT a day without fills
autoexec: ticket ... raised during resolve          # resolve_fault; send this one
RW-17: the PAPER auto-execution lane has NO risk_recheck_factory wired
```

### The signatures, compressed

| What you see | Almost certainly |
|---|---|
| Digest arrives, all-zero, cycle reports `skipped>0` with real reasons | Quiet day. The feed is working and the engine is filtering |
| Digest arrives, all-zero, **cycle never ran** | Market-closed skip, or the copy-cycle job is not registered (lane not armed) |
| Digest arrives, refusals all `budget_*` | The rails did their job. Widen deliberately or do nothing |
| Digest arrives, refusals all `pending_exposure` | SEC-07 cap filled. `order_status_poll` is not draining resting orders (§3) |
| Digest arrives, refusals all `budget_open_positions` and the names look old | Slots held by positions closed outside the lane *before* the release edge existed, or ambiguous refusals. Check the open list in the detail |
| `queued` notices arrive, nothing ever executes, no refusals | Delivery is confirming but the window never closes ⇒ the **resolve tick is not running** |
| No `queued` notices, but the cycle reports `queued_for_autoexec > 0` | The delivery seam is down. Nothing will trade, correctly |
| `submitted` notices with no `filled` ever | Normal for a resting limit order; confirm `autoexec_fills` and `order_status_poll` are registered |
| Nothing at all, no digest, no log lines | The scheduler or the process is down. This is the case the lane cannot self-report |

---

## 9. Residuals — real costs, stated rather than hidden

- **`abandoned_restart` and `ticket_expired` consume the disclosure.** `workflows/copytrade_orders
  ._consuming` treats "an `Order` row exists" as "this filing was copied", so a ticket killed by a
  restart or by expiry means that filing is **never re-proposed**. It is refused loudly and
  notified; it is not recovered.
- **A restart inside a cancel window is deliberately not resumed.** The facts the queued message
  was rendered from live on the in-memory ticket, and the limit price was struck against a quote
  that is now old.
- **RW-15 shipped only the minimal paper reconcile** needed to drain the SEC-07 cap. Full broker
  reconciliation is still open — see [[REGISTER]] `RW-15`.
- **The lane is absent from `GET /status`** (§2).
- **Rails and digest are UTC-day scoped; crons are Eastern** (§6).
- **No measurement of any kind.** There is no backtest, no soak result, and no evaluation of
  whether copying any filer is worth doing. This runbook describes mechanics only.

---

## 10. Where to look

| Question | File |
|---|---|
| the ticket lifecycle, every gate, every refusal | `app/copytrade/autoexec.py` |
| the budget rails and how spend is derived | `app/copytrade/budget.py` |
| what a message says, and what refuses to render | `app/copytrade/notify.py` |
| the end-of-day statement | `app/copytrade/digest.py` |
| the one sanctioned auto-order entry (Invariant 1) | `app/brokers/auto_paper_submit.py` |
| the SEC-07 drain | `app/brokers/order_status_poll.py` |
| composition, arming, and every refusal to arm | `app/main.py` (`build_paper_autoexec`) |
| the crons and their justifications | `app/orchestrator/scheduler.py` |
| `/cancel` | `app/bridge/bot.py` (`CancelCommands`) |
| the kill switch | `app/orchestrator/halt.py`, `app/orchestrator/killswitch.py` |
| the env knobs | [[setup/accounts-and-keys]] §7, `app/.env.example` |
| what the feature is, and what it is not | [[congress-copytrade]] |
