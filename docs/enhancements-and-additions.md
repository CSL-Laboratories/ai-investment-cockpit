---
title: Enhancements & additions — value-adds beyond the Fable 5 baseline
type: design-proposal
status: superseded
last-verified: 2026-08-09
related: [research-report, research-supplement, cockpit-review-and-integrated-plan, draft-discord-control-plane, build-scaffold-ultracode, security-architecture, prompt-injection-defense, security-hardening-backlog, upside-and-roi]
---

> **[SUPERSEDED]** Idea backlog predating the MVP definition. Superseded by [[PLAN]] §1 for scope and [[REGISTER]] for claims. Nothing here is scheduled until it has an ID.

# Enhancements & additions

Everything upstream of this file — [[research-report]], [[research-supplement]],
[[cockpit-review-and-integrated-plan]], [[draft-discord-control-plane]],
[[build-scaffold-ultracode]] — is **settled spec**. This document does not
re-open it. It adds a curated set of genuinely new value-adds, each concrete
enough to build, ranked by priority and tagged to a phase of the existing plan.

The bias is deliberate: every addition here raises **safety, honesty, or
operator trust**, because that is where a human-gated money system earns the
right to move up the autonomy ladder. Nothing in this file puts an LLM on the
order path or weakens a gate. Several additions are *deliberately* deterministic
or advisory-only (E-01, E-06, E-09) precisely so they can be trusted as inputs to
the performance gate without reintroducing model risk onto the safety surface.

Figures marked **[est.]** are reasoned estimates, not verified research. Claims
cited to a source doc passed the 3-vote adversarial verification recorded there.

## Summary

| ID | Enhancement | Pri | Phase | Effort [est.] | Advances |
|---|---|---|---|---|---|
| E-01 | Benchmark scorecard harness | **P0** | 2 (feeds 3 gate) | 2–3 d | ROI honesty; performance gate; Inv. 6 |
| E-02 | Deterministic record/replay + regression harness | **P0** | 0 → 2 (soak) | 2–3 d | Safe change; 2-wk soak; Inv. 3/8 |
| E-03 | Heartbeat + dead-man's switch + run-as-service DR | **P0** | 0.5 → 3 | 1–2 d | Fail-closed liveness; Inv. 1/7 |
| E-04 | Cross-model proposal verification | P1 | 2 (req. before 3.5) | 1–2 d | Hallucination + injection defense; Inv. 1/2/3 |
| E-05 | Thesis lifecycle & invalidation watcher | P1 | 2 | 2 d | Discipline stream; Inv. 5/8 |
| E-06 | Provenance & lineage graph | P1 | 2 | 1–2 d | Attribution; model pinning; Inv. 6/8 |
| E-07 | Observability & cost telemetry | P1 | 0.5 / 1 | 1–2 d | Backpressure visibility; Inv. 7 |
| E-08 | Data redundancy / source failover | P1 | 1 → 2 | 2 d | Stale-quote refusal; Inv. 7 |
| E-09 | Explainability surface (deterministic "why") | P1 | 2 | ~1 d | Trust; concordance; Inv. 5/6 |
| E-10 | Shadow-live divergence detector | P1 | 3 (gates 3.5) | 2 d | Paper≈live proof; Inv. 1 |
| E-11 | Post-mortem automation | P2 | 3 | 1–2 d | Incident review; Inv. 8 |
| E-12 | Personal-data minimization / redaction | P2 | 1 / 3 | ~1 d | Privacy (read-all-accounts); Inv. 4/8 |

---

## P0 — the safety & honesty spine

### E-01 — Benchmark scorecard harness

**What.** A deterministic analytics module (new `analytics/scorecard.py`,
addition to the WP layout) that scores every closed position and every reporting
window **relative to a same-capital, same-period SPY buy-and-hold** (dividend-
adjusted). Metrics: per-trade R-multiple (realized P&L ÷ initial risk), hit rate,
average win vs. loss, expectancy, max drawdown vs. the configured limit, approval
concordance (operator approve-rate on proposals), veto quality (shadow-tracked
outcome of vetoed proposals), and benchmark-relative excess return. Persisted as
`ScorecardSnapshot` rows; rendered by a `/scorecard` slash command and a weekly
`#system` embed. The honesty rule is a computed boolean `beats_benchmark` over
the trailing window with a minimum-sample guard; when false, the embed **leads**
with "Not beating SPY buy-and-hold (Δ = −X% over N trades)."

**Why.** The performance gate in [[draft-discord-control-plane]] is defined in
terms of drawdown-inside-limit and ≥80% approval concordance but has no canonical
computation home. [[upside-and-roi]] §3 makes benchmark-relative reporting a
load-bearing honesty commitment — "if the system can't beat the benchmark it must
say so in its own scorecard." This module *is* that engine, and it is the source
of the numbers the gate reads.

**Fits.** Pure functions over the hash-chained audit / lineage store — no LLM,
mirroring the `risk/` discipline so its verdict is trustworthy as a gate input.
Feeds the performance-gate calculator in WP5. Extends **Inv. 6** (reads the audit
chain); the `beats_benchmark` line is *computed*, never LLM-narrated.

**Caveat.** For a concentrated few-position account, tracking error vs. SPY is
large and short-window comparisons are noise — the ≥30-adjudicated-proposal
minimum-sample guard (already the gate threshold) is mandatory or the verdict
misleads. R-multiple needs a defined initial risk (stop distance); positions
without a stop fall back to notional as the denominator and must be flagged as
such, not silently mixed into the R distribution.

### E-02 — Deterministic record/replay + regression harness

**What.** Grow WP9's "fixture-driven end-to-end" and "2-week paper replay runner"
into a first-class harness that records a full market day — every `MarketEvent`
from the WS/poller, every quote and calendar entry, **and every model call's**
`{prompt_hash, validated_output, model_id, model_version, session_id}` — as an
immutable bundle under `tests/replays/<date>/`. Replay injects a frozen
`core/clock.py`, a seeded RNG, and a `runner.backend: recorded` agent-runner that
returns the stored output for a matching prompt hash instead of spawning
`claude -p`. The entire pipeline (ingest → triage → escalate → propose → verify →
risk → approval) then runs deterministically, offline, in seconds, burning zero
tokens.

**Why.** Three payoffs: (1) the mandatory 2-week paper soak becomes
*reproducible* instead of a one-shot live run; (2) no risk rule or mode-ladder
change ships without a regression guard — a behavior diff on a known day fails in
CI; (3) prompt changes can be regressed against yesterday's real packets without
re-billing the subscription (material given Fact 2 — headless burns normal plan
limits, [[research-report]] §3).

**Fits.** The `recorded` backend is config-selected in `agent_runner`.
**Inv. 8** (raw inputs stored separately from summaries) is exactly what makes
model-call recording possible; **Inv. 3** validation still runs on replayed
outputs. This is also the home of the **model/version-pinning discipline**:
aliases (`triage`, `deep`) for daily `models.yaml` config, but fixtures record
the *resolved pinned ID + version* so a replay reproduces even after aliases move.

**Caveat — why replay is not a safety proof of the models.** A replayed model
output is frozen, so the harness proves the *deterministic code around the model*
(risk, gates, state machine) is stable; it does **not** prove the live models
still behave the same. "Replay green" ≠ "live safe." Prompt regressions surface
only on a deliberate live re-record. Keep a periodic live-smoke run — do not let
the cheap green suite breed false confidence in model behavior.

### E-03 — Heartbeat + dead-man's switch + run-as-service DR

**What.** Three coupled reliability layers. **(a)** Run the orchestrator as a
Windows service via NSSM (already recommended in
[[cockpit-review-and-integrated-plan]] §3), not a login/Task-Scheduler task, so a
reboot self-heals mid-day. **(b)** The scheduler writes a monotonic `heartbeat`
row every N seconds; the separate discord-bridge process (already outbound-only)
or a tiny `core/watchdog.py` reads it. If the heartbeat goes stale beyond
threshold *while the market is open*, the watchdog forces mode to a safe floor —
disarm `auto_veto`/`auto_full`, cancel unapproved proposals via kill-switch
semantics — and pings `@operator` in `#system`. **(c)** A dead-man's switch on
the dangerous end: each pending auto-execution carries a liveness precondition;
the veto-window timer *and* the execution step atomically re-check
`heartbeat_fresh ∧ ingest_healthy ∧ breakers_clear` before the order fires. A
hung or partitioned orchestrator therefore fails **closed** (no trade), never open.

**Why.** **Inv. 7** covers the *vendor-limit* failure mode (degrade to
watch-only). But a hung thread, an OS-update reboot, a crashed WS reader, or a
wall-clock jump can equally leave an armed auto mode in an undefined state. This
makes "orchestrator unhealthy" a first-class, deterministic, fail-safe signal.

**Fits.** Heartbeat is a `storage/` row + `orchestrator/` scheduler write; the
atomic re-check reuses the veto-window race logic already specified for WP6; the
mode floor uses the existing kill-switch path — strengthening **Inv. 1 & 7**.

**Caveat — single-failure-domain honesty.** The watchdog is only as trustworthy
as its own liveness. On one box, a hard crash or power loss kills orchestrator
*and* bridge together and no Discord ping is sent. True external dead-man coverage
needs a second failure domain — reached by an **outbound heartbeat push** (the box
POSTs a liveness beat to a healthchecks.io-style endpoint or the operator's phone; the
external service alerts on *absence*), **never** by a VPS polling `/status`, which
would require opening an inbound port and breach the zero-inbound / loopback-only
posture ([[security-architecture]] §6). On a single box the guarantee is **"fail
closed on hang," not "always alert."** Document that gap; do not oversell it as fully
external.

---

## P1 — high-value

### E-04 — Cross-model proposal verification

**What.** This is the enhancement-catalog entry for the **cross-model grounding
verifier canonically specified in [[prompt-injection-defense]] §7** (schema
`GroundingCheck`) — one stage, one schema, one failure semantics, *not* a second
definition. Before a `TradeProposal` posts to `#proposals`, an independent cheap model
(Haiku-class, separate session, `--allowedTools none`) receives **only** the
proposal's stored source packet + the drafted proposal and answers: does every thesis
claim trace to the packet? Are the numbers (price, size, notional) internally
consistent? Is there instruction-like text in the source the proposal appears to have
obeyed (injection signature)? A flag (`grounded=false` / `injection_suspected=true` /
non-empty `unsupported_claims`) forces the item to `review_live`/`needs_human_review`;
the embed shows the flags either way. The verifier can only **downgrade/flag, never
clear** — and in auto modes it is never the sole gate (deterministic corroboration
required, [[security-hardening-backlog]] SEC-14).

**Why.** Two problems, one mechanism. (1) The deep model can assert a catalyst
the packet doesn't support — a second model grading proposal-against-source
catches it cheaply. (2) The Alpaca news WS is a firehose of attacker-controllable
third-party text; a verifier that never sees the deep model's system prompt and
only checks "did the output follow instructions embedded in the data" is a
documented injection defense. Cross-link [[prompt-injection-defense]].

**Fits.** A new stage in `workflows/proposals.py` between draft and post;
`VerificationResult` schema in WP1a; the verifier is an ordinary schema-gated
`agent_runner` call (**Inv. 3**). It is **advisory-toward-safety only** — it can
downgrade, flag, or block, never approve or upgrade — so **Inv. 1**'s order path
is untouched.

**Caveat — independence you don't fully have.** In auto modes, **Inv. 2** forces
the verifier onto a Claude runtime, so it is *same-family*: a systematic bias or
jailbreak that fools Opus may also fool Haiku. The stronger *cross-family*
verifier (Codex) is barred from the auto decision path by OpenAI policy
([[research-supplement]] §3), so it is available only in `review_live` where a
human reviews. Claim the deterministic injection heuristics + same-family check in
auto modes; do not claim independence the runtime constraint denies you.

### E-05 — Thesis lifecycle & invalidation watcher

**What.** Every accepted proposal already carries a written thesis +
invalidation condition. Promote invalidation from a static string to a
**monitored predicate**: on approval, a cheap model compiles the free-text
invalidation into a structured `InvalidationTrigger` (e.g. `price < 142.00`,
`earnings_miss(SYM)`, `10-K filed`, `sector_etf drawdown > 8%`,
`catalyst_date passed with no move`) — the same *LLM-drafts / risk-engine-enforces
/ human-confirms-once* pattern as `/automation` in
[[draft-discord-control-plane]]. The triage loop's deterministic pre-filter
evaluates open positions' triggers each cycle; a fired trigger raises a `#alerts`
embed with a pre-drafted exit proposal.

**Why.** The discipline stream in [[upside-and-roi]] §4 is only real "if the
operator actually routes decisions through it." An invalidation string nobody
watches is theater. Actively watching for the condition and pushing it back in
front of the operator converts a pre-commitment into an *enforced* one.

**Fits.** `InvalidationTrigger` schema (WP1a); compiled by a cheap model,
evaluated as a pure function in the `triage_loop` pre-filter (**Inv. 5** — the LLM
only drafts the predicate; the engine enforces). A `ThesisState` per open
position in storage (**Inv. 8**).

**Caveat.** Not every thesis reduces to a machine-checkable predicate
("management execution deteriorates" is not a trigger). The compiler must mark
triggers `machine_watchable` vs. `manual_review_only` and never let an
un-watchable invalidation masquerade as monitored — a false "the system is
watching my thesis" is worse than none.

### E-06 — Provenance & lineage graph

**What.** Extend the append-only, hash-chained audit (**Inv. 6**) into a
queryable lineage DAG so any artifact answers "where did you come from?":
`MarketEvent(raw_ref→parquet)` → triage `AgentJob` → escalation `AgentJob` →
`TradeProposal` → `VerificationResult` → `RiskDecision` → `ApprovalRecord` →
broker `order_id` → fill → `ScorecardSnapshot`. Each edge stores
`{prompt_hash, model_id (pinned, not alias), model_version, runtime, session_id,
prompt_git_sha}`. Surfaced via `GET /lineage/{artifact_id}` and a
`/trace <proposal_id>` slash command that renders the chain in Discord.

**Why.** Performance attribution and post-mortems are only real if you can
reconstruct exactly what produced a decision. "Which prompt version + model made
the calls that lost money in the last drawdown?" must be a query, not archaeology.
This also operationalizes model/version pinning: aliases for daily use, but the
lineage records the *resolved* pinned ID so a result is reproducible and a model
swap is attributable.

**Fits.** Extends WP2 storage — every `AgentJob` and state-changing row already
exists; this adds `parent_ref` / `prompt_hash` / `model_id` / `prompt_git_sha`
columns and a recursive traversal query. **Inv. 8** (raw separate from summaries)
is the substrate. No order-path impact.

**Caveat.** Lineage is only as honest as the pins — if a prompt template changes
without a commit, or an alias silently re-points, the captured
`prompt_git_sha`/`model_id` is the *only* thing preventing "same lineage,
different behavior." Enforce prompts-live-in-git (WP8 already requires it) and
**fail any recorded/soak run on a dirty working tree.** Cheap to add early,
expensive to backfill — land the columns before the paper soak so soak data is
traceable.

### E-07 — Observability & cost telemetry

**What.** A first-class telemetry surface over what `/status` and `#system`
gesture at. Per-vendor `UsageWindow` gauges (window %, time-to-reset,
requests/tokens this window), deep-model daily-budget burn (used/limit, default
5/day), ingest health (WS connected? last-event age; Finnhub calls-this-minute
vs. the 60 cap; dedup rate), breaker states, and scheduler liveness — rendered as
an edit-in-place "state of the system" embed (like `#positions`). Queryable via
`/status` and `/status deep`; a threshold rule warns at 70% window (the signal
[[draft-discord-control-plane]] already specifies) and again at 90%.

**Why.** **Inv. 7** backpressure can only fire correctly if window state is
measured and visible; the entire subscription-economics risk
([[upside-and-roi]] §7; Fact 2 — SDK credit paused, headless burns normal limits)
is managed by watching the burn. Usage that isn't surfaced gets discovered as a
surprise mid-market-day when the window empties — windows *can* empty fast (a
ChatGPT-Pro window depleting in ~20 minutes was reported, [[research-report]] §3; the
Claude Max-20x rate is opaque/unmeasured, **not** a Claude 20-minute figure).

**Fits.** Reads `UsageWindow` / `BreakerState` / ingest metrics already modeled
in WP1a; render + thresholds live in `bridge/` + a small
`orchestrator/telemetry.py`. No new external dependency.

**Caveat.** Vendor windows are opaque and self-reported — the gauges are an
*estimate* reconstructed from CLI responses and limit-hit errors
([[research-report]] §3: "opaque… highly variable"), not a metered bill. Treat
them as directional; the authoritative signal remains the real limit-hit response
→ breaker. A green gauge must never override a real 429.

### E-08 — Data redundancy / source failover

**What.** Graceful degradation for the $0 stack (Fact 5) when a source drops.
(1) The Alpaca news WS already reconnects with backoff (WP3); add a "last-good-
event age" clock feeding the risk engine's staleness check. (2) Quotes: if
Finnhub rate-limits (60/min) or errors, fail over to a tagged secondary source —
Alpaca's free market-data tier (key already held) is a candidate, but it is the
**IEX feed only** (partial-book, single-venue, not full SIP/NBBO), so the risk engine
applies a wider staleness/spread tolerance to it and refuses on thin names
(real-time-ness/coverage **[est.]**, re-verify at build; only Finnhub free real-time
US quotes are verified, [[research-supplement]] §2). If both are stale, serve
cached-last-good tagged with explicit `staleness_seconds` — which the risk engine
treats as a **hard** failed check (no live order on a stale price). (3) News-gap detection:
on WS reconnect, backfill via Finnhub `/news` `minId` cursor so a disconnect
creates no silent blind spot.

**Why.** [[research-supplement]] §2 explicitly flags the Alpaca news feed as a
single Benzinga source — a single point of failure. The correct behavior is not
"crash" or "trade blind," it is "degrade visibly and let the risk engine refuse on
staleness." This makes the stale-quote invariant enforceable instead of
aspirational.

**Fits.** `ingest/` gains `quote_router.py` (primary Finnhub → secondary Alpaca →
cached-last-good); quote records carry `source` + `staleness_seconds`; `risk/`
reads that tag. Directly serves **Inv. 7** (never trades on stale; the `minId`
backfill is the "don't drop events" mechanism).

**Caveat + why we stopped short of paid redundancy.** Two "free" sources with
correlated outages (both hosted, both API-key gated) are not truly independent —
a partition at the box takes out both. The honest guarantee is **"detect
staleness and refuse," not "always have a fresh quote."** A fully redundant *paid*
feed (e.g. Polygon) was considered and set aside: it breaks the $0/mo thesis
([[upside-and-roi]] §1) for a solo experiment whose downside is already capped —
the cheaper correct answer is refuse-on-stale. Revisit only if the soak shows a
material outage rate. Cached-last-good is an input to the *refuse* decision,
never a price shown to the model as current.

### E-09 — Explainability surface (deterministic "why")

**What.** The proposal embed already shows each risk check ✅/❌; make the "why"
complete and deterministic. For every `RiskDecision`, render check + operative
number + limit + margin — e.g. `Max notional ✅ $1,840 / $2,500 (74%)`,
`Concentration ❌ AAPL 34% / 25% cap`, `Quote freshness ✅ 3s (<30s)`,
`Intraday margin ✅ deficit $0`. A `/why <proposal_id>` command re-renders on
demand. Because `risk/` is pure functions (**Inv. 5**), this "why" is generated
from the *same* computation that made the decision — not an LLM narration of it.

**Why.** Correct approvals and operator trust depend on seeing the actual
numbers, not prose that could drift from the decision. It also makes the
approval-concordance metric (E-01) meaningful — the operator approves a legible
decision, not a black box — and removes any temptation to have a model "explain"
a deterministic result, which would smuggle hallucination onto the risk surface.

**Fits.** `RiskDecision` already stores per-check results (WP4); this is a
renderer in `risk/explain.py` + the embed formatter in `bridge/`. Zero LLM. The
rendered "why" is part of the audit record (**Inv. 6**).

**Caveat.** Deterministic "why" explains the *risk* decision, not the *thesis* —
it says the trade is within limits, not that it is a good idea. Keep the two
visually separate so a green risk panel is never misread as endorsement. Thesis
soundness is the model's claim (and E-04's verification target); the risk panel
is only "within your rails."

### E-10 — Shadow-live divergence detector

**What.** Before trusting `auto_full`, run identical decisions in paper *and*
live simultaneously and measure divergence. In `review_live`/`auto_veto`, every
proposal that executes live is also executed on the Alpaca paper account
(shadow); the scorecard (E-01) tracks the paper-vs-live gap on the same decision
— fill-price slippage, timing, and any case where the live risk gate blocked but
paper didn't (or vice-versa). A large or systematic divergence beyond a
configured band is a deterministic breaker input that **blocks the `auto_full`
gate**.

**Why.** The performance gate unlocks auto modes from paper / review_live
history — but paper fills are frictionless and optimistic (LiveTradeBench:
static/paper winners underperform live, [[upside-and-roi]] §3). Shadow-live
quantifies exactly that gap on the operator's *own* decisions before real capital
rides on the assumption that paper ≈ live. It is the empirical answer to "does the
paper track record transfer?"

**Fits.** Reuses `alpaca_paper.py` (WP7) as the shadow executor; the divergence
metric is a scorecard computation and a `BreakerState` input (WP5). The shadow
order still goes through the same risk + approval path — **no bypass of Inv. 1.**

**Caveat — chicken-and-egg + imperfect counterfactual.** Shadow-live needs a
funded live account to shadow against, so it gates `auto_full` (Phase 3.5) but
*not* `auto_veto` (which unlocks from paper alone). And a shadow paper fill during
live trading is still not a perfect counterfactual — a real order can move a thin
book; the shadow can't. It measures the gap directionally; on liquid large-caps
the gap is small, on thin names treat shadow agreement as weak evidence.

---

## P2 — nice-to-have

### E-11 — Post-mortem automation

**What.** On any circuit-breaker trip or a losing-streak breaker, auto-generate a
schema-gated `PostMortem` from the lineage graph (E-06) using a **fresh-context
cheap model** (the fresh-context review pattern from
[[draft-discord-control-plane]] — no anchoring on the original proposing
sessions). Input: the lineage slice for the trip window (proposals, risk
decisions, fills, scorecard deltas, breaker cause). Output: timeline, proximate
cause, which checks fired/didn't, whether any invariant was stressed, and 1–3
concrete config suggestions — posted to a `#system` thread for review.

**Why.** A breaker trip is exactly when the operator is stressed and least able to
reconstruct events; a cheap, lineage-grounded write-up turns an incident into a
reviewable artifact and builds the corpus that reveals whether breakers trip for
real reasons or are mis-tuned.

**Fits.** Reads lineage + audit; a `workflows/postmortem.py` fresh-context
`agent_runner` call (**Inv. 3** schema-gated). Uses **Inv. 8** (fresh-context
interrogation over stored inputs — the design already enables this).

**Caveat.** The post-mortem is an LLM narrative over real data — useful for
orientation, **not ground truth**; the lineage graph is the authority, the
write-up is a reading of it. Suggestions are advisory text only and **never**
auto-apply — auto-modifying a limit or rule would put an LLM on the safety
surface through the back door (violates **Inv. 1 & 5**). Human reads, human
decides.

### E-12 — Personal-data minimization / redaction

**What.** Operationalize the verified privacy fact (Fact 1; [[research-report]]
§1 — the Robinhood MCP grants the agent **read access to ALL accounts**, and that
data flows to the AI provider). Two levers: **(1) broker segregation as the
default posture** — keep the main portfolio at a *different* broker so the
Agentic-connected agent only ever sees the funded Agentic account
([[cockpit-review-and-integrated-plan]] §3). **(2) A Discord redaction mode**
([[draft-discord-control-plane]] floats it) — render % and R-multiples instead of
$ notionals/balances in embeds, toggled by `/redact on|off`; the local audit log
keeps full values (loopback), Discord gets the redacted view.

**Why.** This is the one privacy exposure the verified research explicitly flags
and that the platform cannot remove — a client-side design responsibility exactly
like the approval gate. Minimizing what the agent can *read* (segregation) and
what *leaves the box* (redaction) are the only levers, and both are cheap.

**Fits.** Segregation is an operational choice + a config account-allowlist
assertion; redaction is a render-time transform in `bridge/` (full fidelity stays
local per the spirit of **Inv. 4 & 8**), and redaction is **default-on**
([[security-hardening-backlog]] SEC-09). Cross-link [[security-architecture]] and
[[security-hardening-backlog]] for the full threat model.

**Caveat.** Redaction protects against casual exposure of Discord history, not a
determined adversary who compromises the guild — %/R still leaks strategy, and
the local box remains the crown jewel. **Broker segregation is the stronger
lever** (it defeats the read-all-accounts flow entirely); redaction is
defense-in-depth, not a substitute — and it can't un-send data already posted, so
set the toggle *before* funding a live account, not after.

---

## How these land on the phase plan

| Phase (per plan) | Enhancements that land or begin here |
|---|---|
| **0 — Harness** | E-02 (record/replay foundation, WP9), E-06 columns (cheap to add early) |
| **0.5 — API + bridge** | E-03 (service install + heartbeat), E-07 (telemetry, the moment continuous runs start) |
| **1 — Morning Brew** | E-07 (burn visibility), E-08 (staleness clock + backfill), E-12 (redaction as balances hit Discord) |
| **2 — Triage + paper** | E-01, E-04, E-05, E-06 (full lineage), E-08 (failover router), E-09 — build the honesty/trust stack *during* the soak so Phase 3 has data |
| **3 — Gated live (`auto_veto`)** | E-03 dead-man precondition on auto-exec, E-10 (shadow starts), E-11 (breakers live), E-12 (segregation decided before funding) |
| **3.5 — `auto_full`** | E-04 becomes arm-blocking, E-10 divergence gate must be clear |

**Sequencing rule of thumb.** The P0 spine (E-01/02/03) and the trust layer
(E-06/09) are cheapest to build alongside the WPs they extend and painful to
retrofit after the paper soak has already generated untraceable, unscored data.
The gate-blocking additions (E-04 mandatory-before-`auto_full`, E-10 divergence)
can follow, but must exist before the corresponding rung of the ladder unlocks —
by design, the ladder in [[draft-discord-control-plane]] moves up only through
measured gates, and these enhancements are what make those gates measurable.
