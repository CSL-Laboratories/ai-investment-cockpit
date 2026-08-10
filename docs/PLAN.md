---
title: The plan — MVP definition, milestone sequence, and how scope gets in
status: live
supersedes:
  - path-to-production
  - capability-expansion-master-plan
  - integration-and-remote-operations-plan
  - cockpit-review-and-integrated-plan
  - remaining-work-session-plan
last-verified: 2026-08-09
---

# The plan

> **This is the only document that defines what we are building next and in what order.**
> Everything else is either a live register ([[REGISTER]], [[security-hardening-backlog]]),
> a subsystem contract ([[build-scaffold-ultracode]], [[quant-engine]]), reference research,
> or archived. If another doc disagrees with this one, this one wins and that doc is stale —
> say so in a PR rather than working around it.

Written 2026-08-09, after the branch-consolidation audit (PRs #8/#9/#10) and the
[[llm-hybrid-trading]] viability research.

---

## 1. What we are building

**A human-gated, paper-first investing copilot with two engines**, per [[CLAUDE]]:

1. **Awareness + discipline** — the reliable value. Time saved, things not missed, discipline
   enforced. Powered by what LLMs are actually good at: information work.
2. **Execution** — mechanically reliable, financially experimental, capped, human-gated.

Engine 1 makes Engine 2 safer and better-informed. It never makes it *profitable*.

### The MVP, concretely

**End-to-end paper trades, with heavy Discord integration.**

A proposal reaches an executed paper order through the full path — risk pass → mode ladder →
human approval → `OrderGateway` — and the operator can see, question and approve the whole
thing from Discord without touching a terminal. Autonomous execution against a real account is
a later phase and is gated on the paper engine having demonstrably worked first.

**Done means:** a market day runs unattended from ingest to a delivered brief, a proposal it
raises can be approved in Discord, the resulting paper order fills, and the audit chain
verifies over the whole sequence.

### Bring-up universe vs edge universe — DO NOT CONFLATE THESE

We are building and proving on **tech/finance large-cap**. That is the correct *bring-up*
universe: transaction costs are negligible at our size, so cost stops being the binding
constraint while we prove the machinery; coverage is dense, so the extraction and brief
pipeline gets exercised every single day; there are no borrow or ADV problems.

**It is the wrong universe to expect edge from, and nothing in this repo should ever imply
otherwise.** Mega-cap tech is the most efficiently priced, most heavily covered segment in
existence. [[llm-hybrid-trading]] is explicit that residual edge lives in capacity-constrained
situations institutions cannot fit into — the structural opposite.

> **A working pipeline on liquid names is not evidence of alpha.** Write this on anything that
> renders a paper-trading P&L. The failure mode is someone reading a green dashboard in four
> months as a reason to fund the thing.

Niche selection — [[llm-hybrid-trading]] argues for closed-end-fund discounts and small
merger-arb, and against index-inclusion (decayed to ~0.3%) and post-bankruptcy equities (high
dispersion) — is a **later decision, made after the engine is proven.** Not now.

---

## 2. What the research changed, and what it confirmed

[[llm-hybrid-trading]] was commissioned to test the premise. Summary of what it means here.

### Confirmed: the architecture is right

Its central recommendation is that *"confining the LLM to a text-to-structured-data extraction
layer is the single best design choice available"*, and that the contamination literature
*"indicts prediction, not fact-extraction."*

That is already Invariant 5 and "execution stays model-free". We built the recommended
architecture before reading the recommendation. **This is the plan's strongest asset and the
thing least likely to be appreciated later** — the temptation to let the model "just score this
one thing" will recur, and the answer is in the research: Benhenda's matched test saw LLM
strategies go from +20.7% in-sample to −1.0% out-of-sample. Prompting fixes do not work
(Sarkar & Vafa). The only reliable mitigations are post-cutoff-only evaluation or
chronologically-consistent models.

### Reframed: `app/filings/` is the Stage 1 deliverable, not cleanup

The research's Stage 1 is *"build and audit the extraction layer first"*, sourced from free SEC
EDGAR. **That is `app/filings/`** — 4,594 LOC across 20 modules, plus
`app/ingest/edgar_pipeline.py`, already written and with **zero production importers**. It is
not debt. It is the thing the research says to build, sitting unwired.

It also hands us an acceptance gate the repo lacks: **field-level extraction precision ≥95% on
a hand-labelled, post-training-cutoff sample of the documents we actually parse.** Not "the
tests pass" — measured accuracy on real filings. Published F1 (~0.82) is from *news* text, and
organization entities are the weak category (~0.68–0.80), so our own numbers on 8-Ks are the
only ones that count.

### Exposed: three gaps in the risk gate

Verified against the code, not assumed:

| Gap | Current state | What the research says binds |
|---|---|---|
| **No ADV-based sizing** | `RiskContext` is entirely dollar-denominated: `max_notional`, `concentration_pct`, `pending_exposure_cap`, `buying_power` | Cap position at 10–15% of median daily dollar volume |
| **No borrow cost on the risk path** | `quant/costs.py` models fees for the *simulator*; the live gate has nothing | Sub-$100M names averaged >30% borrow fees in 2023; a short leg can consume the entire spread |
| **No net-edge threshold** | `max_execution_cost_bps` is an *absolute* ceiling | Do not trade unless expected net return is **2–3× modelled all-in cost** — a *relative* test |

Note `quant/verify.py`'s `max_participation` is a fraction of **displayed book depth**, not
ADV. Different measures, different horizons. It does not close the first gap.

---

## 3. Market data: the decision, and the problem we are deferring

### Decided: poll L1 quotes + daily bars. No live order book.

`alpaca_paper.latest_quote` already polls Alpaca's L1 endpoint per symbol and is wired through
`quote_fn`. We extend that rather than building anything new.

**We are not building an L2 depth feed.** Three reasons, in order of weight:

1. **The research's own cost model does not use depth.** The square-root impact law is
   `I = Y·σ·√(Q/V)` — it needs **volatility and daily volume**. Spread comes from L1. Borrow
   comes from the broker. Nothing in the recommended cost stack needs a depth ladder.
2. **Our current feed would make depth modelling actively wrong.** `alpaca_paper.py:167` uses
   `params={"feed": "iex"}`. IEX is ~2–3% of consolidated volume: the prices are a fair NBBO
   proxy, the **sizes are not** representative of available liquidity. Walking that as
   "displayed depth" produces confident, precise, wrong numbers — worse than no model, because
   it looks authoritative.
3. **L2 is the highest-maintenance data a small team can take on** — streaming sockets,
   per-symbol book state, sequence-gap recovery, forever.

**`quant/book/lob.py` is not wasted by this.** A real FIFO book is correct for the *simulator*,
where synthetic and replay data are free and deterministic. Live gets L1 plus statistics. Two
jobs, two models — a sound split, not a redundancy.

### Consequence: this resolves the blocked staleness control

[[REGISTER]] item `BUG-040` (`verify_proposal`'s `max_staleness_ns` inert) was blocked because
`book.ts` had no defined time base. **A polled quote has an unambiguous one:** the exchange
timestamp, in epoch nanoseconds. Choosing poll-based ingestion answers the contract question as
a side effect.

### Deferred, and named: fixed-cadence polling is a real obstacle to autonomy

**This is a known limitation, recorded here so it is not rediscovered as a surprise.**

Everything today is cron-driven — premarket brew, intraday loop, EOD review. That is fine for a
human-gated awareness engine: the operator reads a brief on a schedule. It is **not** fine for
anything approaching autonomous execution, because the interesting moment is rarely on the
quarter hour. A halt, an 8-K, a gap — all of them happen between polls, and a system that
learns about them on the next tick has already missed the tradeable window.

**The path out already exists in the tree and is not being used for this.** `FEED_ALPACA_NEWS`
is a *streaming* feed that runs on its own thread — the codebase already knows how to consume a
socket and fold events into storage. What is missing is that the **intraday loop is not driven
by it**; it is driven by a clock.

The architectural requirement, to be honoured from now on: **`book_fn` and `quote_fn` are
seams.** Whether a snapshot is produced by a poll or by the last frame off a socket must be a
property of the *fetcher*, never of the caller. Build the poll version so the stream version is
a fetcher swap, not a refactor.

Scheduled as `RW-13` in [[REGISTER]]. **Not in the MVP** — do not let it expand M1–M4.

---

## 4. Milestones

Ordered by dependency, not by appetite. Each has one owner and a gate that is a measurement,
not an opinion.

### M1 — Wire the extraction layer  (`RW-11`)

`app/filings/` has no production importer; `main.py` never imports it. The live path is still
`app/ingest/edgar_brew.py` (1,094 LOC), reading a foreign SQLite DB from a sibling process.
[[refactor-plan]] correctly says do not delete the old path until the new one has run a live
market day — but **no package was ever scheduled to make that possible**, so the temporary
state became permanent and we pay for both.

- Add a `sec:edgar` branch to `build_ingest_fetchers`, backed by `run_edgar_pipeline` when
  `sec_user_agent` is set, preferred over `brew_db_path`.
- **Gate:** the in-process path produces cited deltas on a real market day, *and* field-level
  extraction precision ≥95% on a hand-labelled post-cutoff sample.
- Then `RW-12` deletes `edgar_brew.py` + its tests (~100 KB). **`RW-12` is scheduled in the
  register at the same time as `RW-11` is opened, not afterwards.**

### M2 — Live market data on the ingestion trigger  (`RW-14`)

- Add a daily-bars poller alongside the quote poller (same Alpaca credentials, free). Cache per
  symbol, refresh daily. Yields **ADV and realized volatility**.
- Synthesize `book_fn` from the L1 quote — one level per side, timestamped from the quote.
- Stamp `last_quote_ts` per symbol as the poller observes quotes; the intraday tick samples
  `max_data_age_s` into `BreakerSignals` — the stale-data breaker's wire (`BUG-058` left it
  honestly UNSAMPLED until this package exists). Gate the sampling on market hours, and keep
  the 120s threshold coherent with the poll cadence.
- **Gate:** `quant_book_seq` advances between observations on a live day, so the staleness
  control in `risk/engine.py` can actually fire (it currently cannot — see `BUG-040`).

### M3 — Make the cost model match reality  (`W-21`)

The change M2 makes possible, and the one the research says decides viability.

- Replace `verify.py`'s displayed-depth walk with **spread (L1) + square-root impact (ADV, σ) +
  borrow on any short leg**.
- `max_participation` becomes a fraction of **ADV**, which is the measure the 10–15% rule is
  actually stated in.
- Add the **relative** net-edge test: refuse when expected net return < 2–3× modelled all-in
  cost. This is a new check, not a re-tuning of `max_execution_cost_bps`.
- **Gate:** a cost-sensitivity curve for our universe showing net return against position size,
  and the dollar threshold where a trade goes cash-flow negative.

### M4 — The paper loop, end to end  (`W-22`)

The MVP. Proposal → risk → mode → Discord approval → `OrderGateway` → filled paper order →
audited.

- **Fills modelled at bid/ask, never mid.** The research is explicit that mid-quote fills are
  the single most common way a paper result flatters itself.
- Discord carries the whole loop: brief, proposal, question, approve/veto, fill confirmation.
- Loss signals into `BreakerSignals` (the two `BUG-058` left unsampled): daily loss derived
  from portfolio snapshots (day-open vs current equity — decide whether the breaker means
  realized-only or drawdown, and rename accordingly, one home); consecutive losses derived
  from round trips once fills flow. Neither is wired before this package because neither
  input exists before it.
- **Gate:** an unattended market day, end to end, with `verify_chain().ok` over the whole
  sequence — and a second one, a week later, with no human intervention in between.

**`RW-15` — order-status poll + real broker reconciliation. A named prerequisite of this
gate, scheduled NOW so the temporary state cannot become permanent (the RW-10 lesson).**
The 2026-08-09 sweep made fills-at-submit work (`BUG-052`), which covers marketable orders
only: without a status poll, a RESTING limit order holds the SEC-07 cap forever, a partial
never progresses, a lost-response submit stays an `ambiguous_abort` the operator must clear
by hand, and `reconcile_startup` can only alert (never heal) because `_no_broker_history`
returns nothing. A degenerate M4 using only market orders would LOOK done while all of that
stays broken — which is precisely how a gate gets gamed by accident. Scope, per §5:

1. **Operator change:** resting/partial orders settle without intervention; ambiguous
   aborts clear themselves; the reconcile report can heal.
2. **Extends:** the gateway's existing ACK machinery (`_sanitize_broker_response` →
   `_BROKER_STATUS_TO_STATE` → `_ack_consistent`, reused verbatim — one parser, one home)
   plus a scheduler job over orders in open states; lookups by ``broker_order_id``, and by
   ``client_order_id`` (= ``cockpit-{order.id}``) for the lost-response case.
3. **Retirement:** ``main._no_broker_history`` is deleted in the same PR.
4. **Reachability test:** a system test that fails when the poll job is not scheduled.
5. **Claims:** `RW-15`; settles `ReconcileReport.ambiguous_aborts`; the partial-fill
   remaining-notional refinement waits on `DEC-04` (see [[REGISTER]]).
6. **Platforms:** pure httpx polling, no subprocess/paths — CI-neutral.
7. **Size:** one package (~300–400 LOC + tests). Order path → adversarial-review round.

### M5 — Event-driven triggers  (`RW-13`)

Post-MVP. See §3. Drive the intraday loop off the existing stream rather than the clock.

---

## 5. How scope gets in

The gate exists because of a measured failure, not a hypothetical one. During the audit, **five
defects were found to have been fixed twice** — once on `rw01-operationalize` and once on `main`
as `BUG-031/032/034/035/039`. Cost: ten merge conflicts, two `add/add` collisions where both
branches wrote the same test file from scratch, and a partial port that left the *actual* bug
(a flat 120 s timeout) unfixed while looking fixed.

We work **ad hoc — whoever picks it up.** That is a legitimate choice for four people and it is
not going to change. It means the fix is a **claim mechanism**, not a process.

### Before writing code for anything larger than a one-line bugfix

One entry in [[REGISTER]], and answers to these:

1. **What changes for the operator?** If nothing, this is a refactor — say so and justify it.
2. **What existing code path does this replace or extend?** Name the file. If "none", say why
   the job is genuinely new.
3. **If it replaces something: which package deletes the old path, and when?** No package ID,
   no merge. *(This is the RW-10 failure: a second path built, the first never retired.)*
4. **What proves it is reachable in production?** Name the test that fails if the wiring is
   removed. Not "the unit tests pass." *(This is the grounding failure: `decision_agent_cwd`
   had zero test references, so a dead security control looked healthy.)*
5. **Which IDs does this claim?** Cross-check the register for an existing claim.
6. **Windows / macOS / Linux implications.** Subprocess, path, credential, or temp file — say
   how. CI runs all three.
7. **Rough size.** Over ~1,000 LOC is more than one package. Split it before starting.

A change that cannot answer 2, 3 and 4 is a scope change. It goes back to this document before
it goes into a branch.

### Rules that came out of the audit

- **No new code path without a scheduled retirement.** Both packages get IDs in the same PR.
- **Every security or fail-closed control ships with a test that fails when the control is
  removed** — through the real call path, with the setting configured. Three controls were
  found that could not fire; two are fixed, one needed a contract decision first.
- **A control whose inputs are not yet defined must not ship looking wired.** Either it fails
  closed and says why, or it is absent and the surface reports it as absent. `/quant/status`
  advertising a check that could never fire is the defect, not the missing check.
- **System tests compose the real thing with fake leaves.** A hand-maintained mirror of a
  production wiring function is a defect, not a fixture.
- **A number or an invariant has exactly one home.** Docs link to it. Thresholds that matter go
  in CI where they can fail, not in prose. The test-count baseline was copy-pasted into seven
  docs and every copy was stale.
- **One invariant namespace.** [[CLAUDE]] holds the canonical 9. `AGENTS.md` points at it.
  Vendored code under `app/filings/` keeps upstream numbering with a `PROVENANCE.md` divergence
  entry; cockpit-owned files use cockpit numbers.

---

## 6. Explicitly out of scope

Named so that "we should also…" has somewhere to land.

- **Live capital.** Paper-first overrides everything until the pre-live gates in
  [[security-hardening-backlog]] pass. The research adds its own bar: 6–12 months of realistic
  paper trading before committing money.
- **Managing anyone else's money, or selling signals.** Own capital needs no registration; the
  moment either of those changes, adviser/BD rules apply. An auto-trading signal product
  forfeits the publisher's exclusion outright.
- **Niche selection** (CEF / merger-arb / etc.). After the engine is proven. See §1.
- **L2 depth data.** See §3.
- **Autonomy in judgment.** More breadth, better extraction, richer briefs, less babysitting —
  yes. The model deciding what to trade — no, and the research is the reason, not caution.
