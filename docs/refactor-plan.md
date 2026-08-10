---
title: Refactor plan — from fixture-driven build to a running dual-surface cockpit
type: roadmap
status: live
last-verified: 2026-08-09
related:
  - README
  - capability-expansion-master-plan
  - path-to-production
  - production-work-packages
  - draft-discord-control-plane
  - enhancements-and-additions
  - security-architecture
  - security-hardening-backlog
---

> **[LIVE]** STILL LIVE — owns the RW-series. RW-11/RW-12 (wiring, then retiring, the EDGAR path) are scheduled in [[REGISTER]] and detailed here.

# Refactor plan

> **This is a refactor, not a rewrite.** The core engines — the deterministic risk
> engine, the `OrderGateway`, the hash-chained audit spine, the order state
> machine, the agent-runner — are well-built and heavily tested. **Leave them
> almost entirely alone.** The refactor is about what *wraps* them:
> operationalizing the system, decoupling the operator surface, making the universe
> dynamic, and adding the data + awareness layers from
> [[capability-expansion-master-plan]]. Every change preserves the 9 invariants and
> the paper-first / gated discipline.

## 1. What drives this refactor

1. **It doesn't run yet end-to-end.** `ingest/` is not wired to the scheduler
   (`main.build_ingest_fetchers` is called by nothing); `main.run()` is untested;
   both brokers are in-process stubs. The system executes only against fixtures.
2. **The universe is closed.** The triage pre-filter drops out-of-universe names,
   so the system can't *discover* a new company — the "next Palantir" blind spot.
3. **The data spine is thin.** News / quotes / EDGAR / calendars / FINRA, with no
   fundamentals, estimates, valuation, ownership, or macro — for either the AI or
   the human.
4. **The surface is hardwired to Discord.** Good for push, poor for browsing rich
   data; and betting the whole operator experience on one channel is fragile.
5. **New requirement: dual-surface.** The same data must serve the AI *and* the
   human (trade alongside, or delegate).
6. **New requirement: fit an intermittent (student) workflow.** Significance-tuned
   notifications, digests, EOD wrap, catch-up — decrease babysitting.

## 2. What NOT to change

- The 9 invariants (see [[README]] and `CLAUDE.md`). Non-negotiable.
- `risk/engine.py` — pure deterministic gate. No LLM, ever.
- `brokers/gateway.py`, `order_state_machine.py`, `capability.py` — the single
  order path and its TOCTOU-hardened submit. Touch only with an adversarial round.
- `storage/audit.py` — the HMAC hash-chained spine. The performance gate reads it.
- The paper-first / ladder-gated go-live discipline in [[path-to-production]] and
  [[production-work-packages]]. Autonomy is still earned through the gates.

## 3. The three big architectural decisions

### 3.1 Decouple the operator surface (the "is Discord right?" answer)

**Decision: don't rip out Discord and pick a new single surface. Abstract the
surface behind an interface, and split the two jobs it's doing.** The operator
surface has two distinct responsibilities that want different tools:

| Job | Needs | Good tools |
|---|---|---|
| **PUSH** — alerts, approval prompts, notifications | Reach the phone reliably, low friction | Discord / Telegram / ntfy / Pushover |
| **BROWSE / INTERACT** — data terminal, charts, research, richer approval | A real UI | Web dashboard over a private overlay (Tailscale/WireGuard) |

The refactor introduces an `OperatorSurface` / `NotificationChannel` interface. The
existing `bridge/` (Discord) becomes **one adapter**. This preserves the
zero-inbound posture (the orchestrator API stays loopback-only; the Discord adapter
holds an *outbound* socket; any optional web dashboard is served over Tailscale,
**never** a public port — [[security-architecture]] §6) while removing the
single-channel bet.

**Default surface: Discord-primary.** For this use case — awareness, daily
summaries, intraday pings, on-demand snapshots — Discord is *sufficient as the
data-relay layer*, and going Discord-only eliminates the entire web-dashboard
workstream (RW-08). Discord serves data as: rich **embeds** (structured snapshots),
**edit-in-place pinned boards** (a live market-status board, positions, watchlist),
**chart images** (rendered server-side to PNG — trivial compute, no GPU — attached
to `/research` / `/chart`), and **slash commands** (`/research`, `/portfolio`,
`/screen`, `/compare`, `/catchup`). Accepted tradeoff: charts are *static images*,
not interactive; dense tables are awkward on mobile; there is no fluid multi-panel
exploration. For a student who wants to *stay aware*, that is fine.

- Keep **Discord (or Telegram)** as the primary push + approval + data-relay
  surface. Already built — wrap it behind the interface, don't replace it.
- Add a **phone-push adapter** (ntfy/Pushover) for critical escalations (N-09).
- **Web dashboard (RW-08) is OPTIONAL / deferred.** Build it only if you later need
  *interactive* exploration (zoomable charts, click-to-sort screeners, multi-panel
  live view) — and even then, consider letting **Koyfin ($39/mo) be the deep-browse
  tab** rather than building one. Keep the `OperatorSurface` abstraction (RW-02)
  regardless: cheap insurance to add a dashboard or swap channels later without
  touching the pipeline.

Net: the *transport* is decoupled from the *decision*. Ship Discord-only now; add a
push adapter and (only if ever needed) a dashboard later — without rewriting the
pipeline.

### 3.2 Dynamic universe + discovery lane (the "next Palantir" fix)

Refactor the closed-universe assumption into a **flow**. Three tiers, matching the
cost curve (cheap breadth for discovery, expensive depth only on demand):

1. **Discovery signals — all-ticker, cheap.** News firehose (already all-ticker),
   insider buys (Form 4 via EDGAR, already parsed), volume/momentum anomalies. A
   deliberate **promotion lane** surfaces out-of-universe names instead of dropping
   them.
2. **Shallow fundamentals — all-ticker, affordable.** A lightweight key-metrics
   table (growth, margins, market cap, insider activity, 52-wk range), refreshed
   quarterly, enabling a real full-market **screener**.
3. **Deep data — on-demand, narrow.** Full history + estimates fetched *only* when
   a name is promoted. Any US ticker, pulled when it matters — never a 50,000-ticker
   warehouse.

The **watchlist becomes dynamic**: names enter via discovery, deepen via on-demand
fetch, carry a thesis + invalidation, and exit when the thesis dies. This kills the
blind spot at a fraction of a full terminal's cost. *(Honest limit: this improves
awareness, not prediction — no system reliably calls a 25×; the edge is not missing
candidates and holding them with discipline.)*

### 3.3 Data provider abstraction

All external data sits behind ONE swappable `DataProvider` interface (mirrors the
existing injectable-`fetch` pattern in `ingest/`). Free sources first (FRED macro,
EDGAR filings/insider), one prosumer vendor (~$30–60/mo) for fundamentals +
estimates. No vendor lock-in; failover per E-08.

## 4. Target module map

```text
app/
  ingest/
    providers/        NEW  DataProvider abstraction + vendor adapters
    fundamentals.py   NEW  D-01
    estimates.py      NEW  D-02
    macro.py          NEW  D-06 (FRED, free)
    technicals.py     NEW  D-08 (derived, deterministic)
    corp_actions.py   NEW  D-05
    discovery.py      NEW  broad discovery signals (news/insider/volume)
    (news/finnhub/edgar/finra/calendars — existing)
  universe/           NEW  dynamic universe
    promotion.py      NEW  discovery → watchlist promotion rules
    state.py          NEW  universe-as-a-store (in/out flow)
  surfaces/           NEW  operator-surface abstraction (was bridge/)
    base.py           NEW  OperatorSurface / NotificationChannel interface
    discord/          MOVE existing bridge/ becomes one adapter
    web/              NEW  read-only dashboard adapter (Tailscale-served)
    push/             NEW  ntfy/Pushover escalation adapter
  notify/             NEW  transport-agnostic notification policy
    policy.py         NEW  significance tiering, quiet hours (N-01/N-02)
    digest.py         NEW  batching, EOD wrap, catch-up (N-03/N-04/N-08)
  analytics/          NEW
    scorecard.py      NEW  E-01 benchmark scorecard (deterministic)
    screener.py       NEW  H-04 full-market screener (deterministic)
    portfolio.py      NEW  H-06 portfolio analytics (deterministic)
  workflows/
    research.py       NEW  A-01 on-demand deep-dive / research agent
    (morning_brew/triage_loop/proposals — existing, upgraded)
  risk/ brokers/ orchestrator/ storage/   UNCHANGED core engines
```

## 5. Refactor work packages (RW-series)

Each RW ships with a deterministic gate (GR-xx) in the spirit of the existing
G-gates. Order-path-touching packages get an adversarial-review round.

| ID | Scope | Accept / gate | Depends |
|---|---|---|---|
| **RW-01** | **Operationalize** — wire `ingest/` → scheduler; put `main.run()` under test; Phase-0.5 bring-up (API + one surface delivering the brew, `/status`, `/killswitch`) against live feeds | One market-day dry-run passes end-to-end on live feeds; `main.run()` tested | — |
| **RW-02** | **Surface abstraction** — define `OperatorSurface`; refactor `bridge/` → `surfaces/discord/` as an adapter; behavior identical | All existing bridge tests pass unchanged behind the interface | RW-01 |
| **RW-03** | **Notification policy** — `notify/` significance tiering, quiet hours, digests, EOD wrap, catch-up; emits via surface adapters | Deterministic tiering tests; only critical/material interrupt; EOD wrap renders | RW-02 |
| **RW-04** | **Data spine** — `providers/` abstraction; FRED macro; fundamentals + estimates; derived valuation/technicals; corp actions | Point-in-time correctness test (`report_date` vs `available_date`); untrusted-wrap on free-text | RW-01 |
| **RW-05** | **Dynamic universe + discovery** — `universe/` promotion lane; shallow screener table; on-demand deep fetch; watchlist-as-flow | A seeded out-of-universe event promotes a name + triggers deep fetch; universe in/out tested | RW-04 |
| **RW-06** | **AI upgrades** — synthesis brief (A-03), research agent (A-01), thesis tracking (A-02); optional cheap-triage-offload adapter (Inv-2 boundary held) | Grounding still enforced; Inv-2 keeps decision stages on Claude for auto | RW-04 |
| **RW-07** | **Human surface + analytics** — `/research`, `/screen`, `/portfolio`, `/compare`; human-initiated `/trade` (paper) through the SAME order path; `analytics/` | Human-initiated trade provably routes through risk→approval→gateway (Inv 1) | RW-05, RW-06 |
| **RW-08** *(optional / deferred)* | **Web dashboard** — read-only Tailscale-served data terminal. Build ONLY if interactive exploration is needed; else Discord embeds + edit-in-place boards + chart images (+ Koyfin for deep browse) cover it | Zero inbound ports; read-only | RW-04, RW-07 |
| **RW-09** | **Optimization** — cost governor (X-03), backtest-lite (X-02), provider failover (E-08); scorecard/shadow-live as ladder rungs approach | Budget cap fails to `watch_only` on exhaustion; backtest honest under PIT | RW-04 |
| **RW-10** | **EDGAR core absorption** — vendor `edgar-morning-brew`'s DETERMINISTIC core (SEC client, parser, delta engine, citation gate) into `app/filings/`; leave its LLM/publish/scheduler layers upstream | Ported golden tests reproduce upstream deltas byte-for-byte inside the cockpit; no-LLM-in-filings gate passes | RW-01 |

**Safety go-live (the existing W-series in [[production-work-packages]]) runs as the
gate on autonomy** — SEC-11 witness → paper soak → performance gate → `review_live`
→ `auto_veto` → `auto_full`. The RW-series makes the AI smarter and the human
better-informed; it does **not** skip a single go-live gate.

### 5.1 Bring-up backlog — the RW-01 tail

RW-01's gate is met: 2026-08-02, one live market day ran end-to-end (659 window events
→ 559 `prefiltered_ignore` + 100 `scored`, 0 deferred, `degraded=false`, delivered).
What follows is everything the live run proved is *built but not reachable by the
operator*. It is the RW-01 tail, not new scope — RW-02 should not start until the
surface is actually usable, because RW-02 abstracts a surface nobody has yet driven.

**Ordered by operator value.** Each item names its blocker, because three of the six
are blocked on provisioning rather than on code.

| # | Item | Blocker | Done when |
|---|---|---|---|
| **B1** | **Bot lifecycle / status management** — start, observe, and stop the bot itself. `build_bot` (`app/bridge/bot.py:98`) is called by NOTHING outside tests: the Discord *gateway* has never started, so no slash command has ever executed | code only | The gateway runs alongside `main.run()`; the operator can see the bot online and read its own health without tailing a log |
| **B2** | **Components V2 delivery** — replace plaintext with `IS_COMPONENTS_V2` Container / TextDisplay / Separator so the brief reads as an official bot surface. Today: `bridge/deliver.py`, a `(channel, payload) -> bool` seam over stdlib `urllib`, plaintext capped at `MAX_CONTENT = 1900` | code only | The brief renders as a container; the 1900-char cap becomes a per-component budget, not a truncation cliff |
| **B3** | **Real slash commands** — every body is still a stub replying `"/{name} acknowledged."`. Implement against the loopback API and sync the tree to the guild | code only | Read-only `/status` + `/brief` first; `/mode` `/limits` `/killswitch` second. **Approval buttons are order-path (Inv 1) and need their own adversarial round** |
| **B4** | ~~**EDGAR brew feed** — provision `COCKPIT_BREW_DB_PATH`~~ **SUPERSEDED by RW-10 (§5.2)** — the cross-process SQLite handoff is being replaced by absorbing the brew's deterministic core in-process | — | See RW-10's gate |
| **B5** | **FINRA short-interest / Reg SHO** — the last unwired feed. See below; it has an operator half AND a code half | **operator + code** | `fetchers["finra"]` is non-None in a real `run()` and a settlement row reaches the packet |
| **B6** | **Robinhood MCP** — not connected. **This is a new BROKER: it lands on the order path (Inv 1), and MCP responses are UNTRUSTED input (SEC-02).** `robinhood_mcp` is a stub until W17 | gated by W17 + an adversarial round | Not before B1–B5. Scope deliberately; do not treat it as "just another connector" |

#### B5 in detail — what FINRA support actually requires

FINRA is **enrichment only** and must be calibrated as such before anyone spends a
session on it: short interest publishes **twice monthly, 7 business days after the
settlement date**, so a row is ~2 weeks stale on arrival. `ingest/finra_short_interest.py`
enforces this — `_assert_enrichment_event_type` refuses to emit `halt` or `filing`, the
entire membership of `_HARD_EVENT_TYPES`, so it can never drive SEC-21 urgency. The Reg
SHO threshold list is **OTC-only**; exchange-listed names are published by each listing
SRO, not FINRA. Treat this feed as context for a name you are already looking at, never
as a trigger.

The connector itself is **done and tested** (W20, six adversarial rounds). What is missing:

1. **The dataset identifier is unverifiable from any primary source** — the base
   `https://api.finra.org` is confirmed, but the dataset path pattern gates behind
   `gateway.finra.org/app/dfo-console`. `make_finra_fetch` **raises rather than invent
   one**, and that refusal is correct; do not guess a path to make the wiring compile.
   *Operator act:* register for a free FINRA API account, use the **Mock credential
   tier** to pin (a) the real dataset path and (b) the wire field names.
2. **OAuth2 is mandatory** (client_credentials against
   `https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token`; Basic Auth dead since
   2022-06-01). *Operator act:* credentials into keyring (Inv 4), never settings.
3. **`run()` never passes the FINRA closures.** `compose()` accepts
   `finra_token_provider` / `finra_http_get` / `finra_row_mapper`, but `main.run()`
   (`app/main.py:645`) calls `compose()` bare — so **even with both env vars set, FINRA
   stays unprovisioned**. This is the one item on the list that is a genuine code gap
   rather than a missing secret. `_urllib_json_get` (`main.py:163`) already satisfies
   `http_get`; what is needed is a keyring-backed, TTL-caching `token_provider` and a
   `row_mapper` onto the canonical keys the poller reads (`kind`, `settlementDate`,
   `symbol`, `issueName`, `notes`, `ts`) — the passthrough default will not match.

**Do not weaken the connector to land it.** The refusals above (no invented endpoint,
no invented UA, no invented rate cap, no `halt`/`filing` emission) are the deliverable
of W20's red-team rounds, not obstacles to it.

### 5.2 RW-10 — absorbing the EDGAR deterministic core

**Decided 2026-08-02.** W20 wired EDGAR as a *cross-process* feed: `edgar-morning-brew`
writes a SQLite file, the cockpit reads it read-only through `ingest/edgar_brew.py`.
RW-10 replaces that handoff by vendoring the brew's deterministic core **into** the
cockpit. The upstream repo is **preserved and unchanged** — it remains Chaandan's and
Samir's product, with its own soak, its own approve-before-send path, and its own digest.

#### Why (the three facts that decided it)

1. **The cockpit never read the brew's LLM output.** `ingest/edgar_brew.py:_SELECT`
   joins `delta` / `filing` / `company` — deterministic WP4/WP5 rows. The brew's WP6
   composer writes a `brief` table the cockpit does not touch. So the second model
   inference was already dead weight *for this consumer*; removing it removes nothing
   that was running.
2. **The upstream blockers all sit in the layers we do not want.** The 14-day soak
   qualifies WP7's approve-before-send path; the 2026-07-15 degraded run died on a
   *scheduled-task* keyring visibility problem in WP8; the fenced-JSON quarantine risk
   lives in the brew's `agent_runner`. Take the deterministic core and all three stop
   being this project's problem — not by being waived, but by not being in scope.
3. **There is no credential blocker and never was.** SEC EDGAR is free and
   unauthenticated: it wants a declared User-Agent and <10 req/s. The brew's only
   keyring secret is a *self-generated* audit HMAC key (`storage/bootstrap_audit_key.py`).

The human gate is **not weakened by dropping WP7** — it moves up a layer. EDGAR deltas
become an *input* to the cockpit's MorningBrief, which already passes through the
cockpit's own approval path and hash-chained audit (Inv 6).

#### Take / drop manifest

**TAKE** (deterministic, no model call anywhere in it):

| Upstream | Vendored as | Why |
|---|---|---|
| `app/edgar/` | `app/filings/edgar/` | SEC client + submissions poller; the only module that may touch sec.gov |
| `app/parse/` | `app/filings/parse/` | 8-K items, 10-K/Q sections, XBRL facts |
| `app/delta/` | `app/filings/delta/` | text + XBRL delta engine, materiality |
| `app/cite/` | `app/filings/cite/` | citation builder, resolver, claim gate |
| `app/storage/raw_store.py` | `app/filings/raw_store.py` | write-once filing bytes (Inv 8) |
| `app/tests/fixtures/sec/` | `app/tests/fixtures/sec/` | real AAPL/NVDA filings — the golden-test ground truth |

**DROP** (every one has a hardened cockpit equivalent already running):
`brief/` (WP6 — the duplicate inference), `publish/` (WP7 — the cockpit has an approval
path and an operator surface), `scheduler/` (WP8 — the cockpit has APScheduler),
`agent_runner/`, `core/`, `config/`, `storage/audit.py`, `storage/engine.py`.

#### The core is NOT frozen — pin it, do not fork-and-forget

Measured over `db55e2f..bb90c53` (2026-07-09 → 07-16): the take-set gained **+432 lines
across 7 files**, almost all in the SEC client (`submissions_poll.py` +272,
`fetch.py` +121) — real hardening of the code most exposed to a live external service.
The drop-set gained +4,865. So the split is right, but a blind one-time copy **will**
drift away from upstream fixes to exactly the wrong module.

`app/filings/PROVENANCE.md` therefore pins the upstream commit per package and records
the rename map. Checking for upstream fixes stays one command:

```bash
git -C ../edgar-morning-brew diff <pinned-sha>..origin/main -- app/edgar app/parse app/delta app/cite
```

**Vendored files keep their upstream internal structure on purpose.** Restructuring them
would make that diff unappliable, which is the whole value of the pin. Import rewriting is
a pure prefix substitution (see PROVENANCE.md) precisely so the same map can be replayed
against a future upstream diff.

#### Deterministic gate (GR-10)

1. The pre-existing cockpit suite still passes **unchanged** — absorption adds tests, it
   does not edit them.
2. **Golden reproduction**: the ported WP4 golden tests compute the same deltas from the
   same AAPL/NVDA fixture bytes inside the cockpit as upstream does. A vendoring that
   silently changes a computed delta is a failed vendoring.
3. **`no-LLM-in-filings`** grep gate, mirroring `no-LLM-in-risk`: nothing under
   `app/filings/` may import `agent_runner`, `workflows`, or any model-call seam. The
   absorbed core is deterministic *by construction*; that must be enforced, not assumed.
4. `ruff check .` clean.
5. PROVENANCE.md pins resolve to real upstream commits.

#### What RW-10 explicitly does NOT do

- **It does not delete `ingest/edgar_brew.py`.** That reader survived six adversarial
  rounds and is the only working EDGAR path today. It is retired in a *later* package,
  after the in-process path has produced deltas on a real market day — never in the same
  change that introduces its replacement.
- **It does not change the order path, `risk/`, or the mode ladder.** Nothing in the
  take-set can reach them.
- **It does not make the cockpit predict better.** More and better-cited filing evidence
  improves *awareness* — what changed, where, with a link to the bytes. Per §7 and
  [[capability-expansion-master-plan]] §0, it does not manufacture alpha, and no part of
  this package should be justified on that basis.

## 6. Sequencing

```text
RW-01 Operationalize ──┬─► RW-02 Surface abstraction ──► RW-03 Notifications
                       └─► RW-04 Data spine ──┬─► RW-05 Dynamic universe ──┐
                                              └─► RW-06 AI upgrades ───────┼─► RW-07 Human surface
                                                                          └─► RW-08 Web dashboard
                                              RW-09 Optimization (as rungs approach)
     ── in parallel / as the gate on autonomy ──► W-series safety go-live
```

Highest-leverage first: **RW-01 (make it run) → RW-03 + RW-04 (notifications + data
= the babysitting-reduction payoff, all pre-live-safe) → RW-05/06/07** outward.

## 7. Honesty ledger — what this refactor does NOT do

- It does **not** rewrite the working core. If you're editing `risk/` or the
  gateway, stop and ask whether you actually need to.
- It does **not** manufacture alpha. Better data + discovery improve awareness, not
  prediction ([[capability-expansion-master-plan]] §0).
- It does **not** open the box. The web dashboard is overlay/loopback only.
- It does **not** skip the ladder. Autonomous trading still earns its way up through
  SEC-11, the paper soak, and the performance gate.
- It does **not** put a model on the execution path — including for human-initiated
  trades (RW-07).
