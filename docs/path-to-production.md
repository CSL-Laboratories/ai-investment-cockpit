---
title: Path to production — from green build to live-capable product
type: roadmap
status: active
last-verified: 2026-07-08
related:
  - build-scaffold-ultracode
  - ultracode-build-playbook
  - security-hardening-backlog
  - security-architecture
  - cockpit-review-and-integrated-plan
  - draft-discord-control-plane
  - setup/accounts-and-keys
  - setup/moving-targets-refresh-2026-07-06
  - production-work-packages
  - production-playbook
---

# Path to production

> The build is done; the product is not live. This is the guide from a green
> build to a live-capable, polished cockpit. Every gate, threshold, and step
> below is cited to a doc — nothing here is invented. Where the docs leave a
> number to the operator, it says so ("per the plan" / TBD) rather than
> guessing.

**PAPER-FIRST invariant (do not weaken):** No real money has moved and none may
until the explicit pre-live gates below pass. The human approval gate is
client-side — the broker does not enforce it — so the app-side guardrails are
the only protection ([[README]] Status blockquote; Invariant 1 single order
path; [[draft-discord-control-plane]] §"Phase 3 additions").

---

## 1. Where we are

The phased ultracode build **WP0 → WP9 is COMPLETE and green on `main`** as of
2026-07-08. Build order was
`WP0 → WP1a → WP1b → WP2 → {WP3, WP4} → WP5 → {WP6, WP7} → WP8 → WP9`
([[build-scaffold-ultracode]] §"Build order & session plan", line 189). WP9 (the
system test harness) is commit `9f6ff44`; the suite reports **586 passed / 3
skipped, `ruff` clean**, and the gate table **G0–G9 is fully green**
([[ultracode-build-playbook]] §5 gate table). The autonomy ladder, deterministic
risk engine, hash-chained audit spine, Discord control plane, and broker
connectors all exist in code and pass their gates. **But a green build is not a
live-ready system:** the system remains paper-first, no capital has moved, and
the go-live gates — chief among them the still-**OPEN** P0 audit-spine blocker
(SEC-11) — have not been cleared. The build validated the *shape* of the plan; it
did not authorize live trading ([[cockpit-review-and-integrated-plan]] §4).

---

## 2. Hard pre-live BLOCKERS (must close before ANY real money)

These are the P0 controls the hardening register lists as **required before any
auto mode or live capital**. The ladder-gate rule is explicit: no `review_live`
without the P0 "live" set (SEC-02/03/04/05/06/07/09/13, OPS-01/03); no
`auto_veto`/`auto_full` without the P0 "auto" set
(SEC-01/08/10/11/12/14/15/16/19/20/21/23) ([[security-hardening-backlog]]
§"Ladder gates", lines 744–748). The lead blocker:

### SEC-11 — audit-spine "delete-then-heal" (MECHANISM DONE @ W10 `ea7e0fc`; STAYS OPEN pending witness ACTIVATION — P0, THE lead blocker)

- **What.** The per-`AuditEntry` sequence was non-monotonic and reusable, and
  there was no durable *out-of-domain* witness for the chain head. A keyless local
  writer could tail-delete signed rows; natural appends then refill the freed
  sequence numbers and `verify_chain()` re-greened — erasing blocking events,
  over-cap exposure, or over-escalation with no trace.
- **W10 closure — MECHANISM shipped (`ea7e0fc`, 2026-07-08), G10 passed a fresh
  red-team.** (b) monotonic non-reusable HMAC-covered `seq` (`storage/audit.py`
  signed high-water; a refill is now a `verify_chain` gap — caught WITHOUT the
  witness); (c) `MarketEvent.event_type` (+ the `calendar:%` corroboration row)
  bound into the signed chain and reconciled on the budget read-path; (a) concrete
  witness adapters (`bridge/witness.py`) behind `GitAuditLogAnchorStore`, wired at
  `main.compose()`. The four consult sites already fail closed, so W10 auto-resolved
  the SEC-07/20/21 audit-spine residuals.
- **THE G10 CORRECTION (keep the reason).** The naïve wiring was **FILE-ANCHOR
  THEATER**: `GitCommitWitness.read_latest()` reads a local working-tree head file a
  keyless writer can overwrite; the git commit is procedural (reviewed out-of-band),
  **never consulted at the runtime read path**. So the git-commit witness is a
  MIRROR + reviewed-history detection layer (`out_of_domain=False`), NOT a runtime
  witness — only the Discord `#audit-log` server-stamped post
  (`DiscordAuditLogWitness`, `out_of_domain=True`) is. `GitAuditLogAnchorStore.latest()`
  counts ONLY out-of-domain backends, so a git-only deployment returns `latest()`=None
  and every consult FAILS CLOSED. `FileAnchorStore` is theater too.
- **ACTIVATION (the standing OPEN item — why SEC-11 is still a P0 blocker).** The
  mechanism is fail-closed-safe, but auto/live/deep-budget do NOT operate until the
  RUNTIME out-of-domain witness is provisioned: a `discord-bot-token` in keyring +
  an `audit_log_channel_id` for a private `#audit-log` channel whose ACL denies the
  bot manage-messages. This is a **W17 precondition**. Until then the system is
  fail-closed (never falsely "anchored"), and SEC-11 must never be implied closed.
- **Why it blocks live.** The performance gate (SEC-20) reads the verified audit
  chain; without the out-of-domain witness a replayed-watermark rollback still
  re-greens `verify_chain()` in-DB (the documented CAVEAT). Only the wired Discord
  witness catches that at the gate/mode/gateway/budget consults.
- **Cite.** [[security-hardening-backlog]] SEC-11 (line 46 P0/auto; §325
  external anchoring + monotonic sequence; lines 373–376 domain-row binding);
  [[security-architecture]] §8 ("anchors its head out-of-band"; "Trustworthy
  replay"); [[README]] load-bearing rule 3 / Invariant 6.

### The remaining P0 "live" blockers

| ID | What must be built | Why it blocks live | Cite |
|---|---|---|---|
<<<<<<< Updated upstream
| **SEC-03** (PARTIAL) | Mint a **single-use live-MCP capability token** bound to a signed approval (`proposal_id` + `RiskDecision.id` + resolved-order/quote hash); add a **runtime tracer** asserting `place_equity_order` dispatches only through `OrderGateway` (catches `getattr`/stored-partial/MCP-shim); spawn decision agents with **zero MCP servers**. | The WP7 grep matches a Python symbol, not the live MCP socket that is the real order authority. Any holder of the MCP client can call the order tool by name. | [[security-hardening-backlog]] SEC-03 Closed note, lines 126–145; [[security-architecture]] §4 L1 + §12 test 1 |
| **SEC-05** (OPEN) | Run decision-stage agents under a **separate low-privilege Windows account** with per-file ACLs so they cannot read `~/.codex/auth.json` or the keyring; treat the Robinhood MCP session as short-lived, re-auth'd, monitored. | Bearer stores are plaintext files; a harvested MCP token places orders with zero contact with this app. | [[security-hardening-backlog]] SEC-05 lines 164–179; SEC-03 line 145 |
| **SEC-06** (PARTIAL) | Startup + CI assertion that inspects the **actual constructed subprocess arg list** and refuses any decision-stage job with a non-empty `allowed_tools` (deny-by-default); refuse to arm auto modes if any decision job has tools. | Exfil needs only FS-read + egress, neither forbidden by the write-only sandbox test; the mechanical arg-list inspector is not recorded as built. | [[security-hardening-backlog]] SEC-06 lines 181–195 |
| **SEC-02** (OPEN) | Treat every MCP-response free-text field that re-enters a prompt as untrusted (`<untrusted-content>` wrap + M3 escape); minimize the read-all-accounts payload to the Agentic account **before** any prompt; run the canary/pattern scan over MCP text. | Endpoint-pin/TLS stop a spoofed host, not malicious content from the pinned host; Inv 3 validates model *output*, not tool-result *input*. | [[security-hardening-backlog]] SEC-02 lines 89–103; [[security-architecture]] §7; prompt-injection-defense §9 |
| **SEC-04** (PARTIAL) | Document the **out-of-band, fail-closed kill path**: NSSM service-stop as the hard stop that drops the MCP connection independent of Discord/LLM; a local hotkey/file-drop HALT trigger; a preemption point (set-flag → cancel in-flight → disconnect MCP). | `POST /killswitch` runs inside the orchestrator loop; a wedged/GIL-starved burst queues the kill behind the order path it should preempt. | [[security-hardening-backlog]] SEC-04 lines 147–162; SEC-07 line 220; [[security-architecture]] §4 L7 + §10 |
=======
| **SEC-03** ✅ **DONE @ W11/G11** | CLOSED (2026-07-09): a single-use **live-MCP capability token** bound to the covering signed `EVENT_ADJUDICATION` (`gateway.mint_live_submit_token`); an always-on **CLASS-level runtime tracer** (`brokers/order_tracer.py`) that fails closed on any dispatch outside `OrderGateway.submit`; decision agents with **zero MCP servers**; and the gate-(e) **trust root pinned at construction** (a forged ladder cannot substitute the HMAC key). Pre-W17 residual: chain-anchor `producing_runtime`. | The WP7 grep matches a Python symbol, not the live MCP socket. | [[security-hardening-backlog]] SEC-03 "W11 closure"; [[security-architecture]] §4 L1 + §12 test 1 |
| **SEC-05** ✅ **mechanism DONE @ W11/G11; ACTIVATION pending** | CLOSED mechanism: the startup ACL-assert (`account_guard`) + the **spawn-under-account fail-closed** gate (`runner.run_agent` refuses a decision spawn unless the transport launches the child under the low-priv account — G11 round-1 caught that the ACL alone was theater). ACTIVATES once the account + a `CreateProcessWithLogonW` launcher are provisioned ([[setup/low-priv-agent-account]]); host provisioning is OPS-06/W15. | Bearer stores are plaintext files; a harvested MCP token places orders with zero contact with this app. | [[security-hardening-backlog]] SEC-05 "W11 closure" |
| **SEC-06** ✅ **DONE @ W11/G11** | CLOSED (2026-07-09): `tool_inspector.inspect_argv` — a deny-by-default inspector on the **actual constructed argv** (refuses tools/`--mcp*`/permission-looseners/`--settings`/`--*system-prompt`) wired into `run_agent` (every decision spawn) + `arm_auto_mode` (auto rungs refuse to arm on a tool/MCP-bearing decision job) + a pinned cwd. | Exfil needs only FS-read + egress; the write-only sandbox test did not forbid them. | [[security-hardening-backlog]] SEC-06 "W11 closure" |
| **SEC-02** ✅ **DONE @ W12/G12** | CLOSED (2026-07-09): `brokers/mcp_untrusted.py` minimizes the read-all-accounts payload to the Agentic account **before** any prompt, then wraps every free-text field via the SAME `escape_untrusted`/`wrap_untrusted` (factored out of `packets.py`) + `scrub_text` news uses. | Endpoint-pin/TLS stop a spoofed host, not malicious content from the pinned host; Inv 3 validates model *output*, not tool-result *input*. | [[security-hardening-backlog]] SEC-02 "W12 closure"; [[security-architecture]] §7; prompt-injection-defense §9 |
| **SEC-04** ✅ **DONE @ W12/G12** | CLOSED (2026-07-09): `python -m orchestrator.halt {set,clear,status}` is the OOB CLI trigger (a separate OS process — no watcher thread needed, gate (a) already reads the sentinel synchronously); `killswitch()` extended to set-flag→cancel→disconnect→disarm via an injected `disconnect_hook` (best-effort, never blocks containment). NSSM-stop + hotkey/file-drop runbook docs remain OPS-06/W15. | `POST /killswitch` runs inside the orchestrator loop; a wedged/GIL-starved burst queues the kill behind the order path it should preempt. | [[security-hardening-backlog]] SEC-04 "W12 closure"; SEC-07 line 220; [[security-architecture]] §4 L7 + §10 |
>>>>>>> Stashed changes
| **SEC-07** (PARTIAL) | Chain-anchor the pending-exposure cap and the approval `decided_at` TTL (still read mutable columns). | Single-transaction tampers closed WP8; the tail-delete-then-refill residual is the SEC-11 class and needs the external witness. | [[security-hardening-backlog]] SEC-07 WP8 note, lines 233–257 |
| **SEC-13** ✅ **DONE @ W12/G12** | CLOSED (2026-07-09): `orchestrator/reconcile.py::reconcile_startup` reconciles unresolved intents against a (mocked) broker lookup at startup — backfills a confirmed result, alerts (never silently resolves) otherwise — and `storage/audit.py::verify_completeness_aggregate` gives the 1:1 completeness gate; wired into `main.compose()`, fails closed to `review_live` on any gap. | "Writes AuditEntry before returning success" is temporal, not transactional — a crash between broker ACK and audit commit yields a real order with no audit row. | [[security-hardening-backlog]] SEC-13 "W12 closure"; [[security-architecture]] §8 |
| **OPS-01** (PARTIAL) | Single-writer serialization of the audit-chain head across FastAPI + bridge + APScheduler (append queue or locked head transaction). | Multi-process writers race the chain head; a forked `prev_hash` corrupts the substrate the performance gate reads. | [[security-hardening-backlog]] OPS-01 lines 707–711 |
| **OPS-03** (OPEN) | SQLite **WAL journal mode + `busy_timeout` + single-writer connection** on Windows. | Unspecified locking on the multi-process Windows deployment risks lock errors and corruption of the append-only chain. | [[security-hardening-backlog]] OPS-03 lines 684–685 |

### Supply-chain (pre-live, cross-cutting)

Pin exact dependency versions and generate a **hash-locked lockfile** (`uv`/`pip`
hash-locking), with lockfile-hash verification on install in CI. `app/pyproject.toml`
lists deps unpinned with a literal "Pin + hash-lock before install" TODO and no
lockfile exists, so the §7 reproducible-install protection and the §11 proving
test are both unmet ([[security-architecture]] §7, §11 traceability row, §3.1
adversary table). Covered again in §5 (Polishing) below.

> **Auto-mode-only P0s** (SEC-01/08/14/15/16/19/20/21/23) are not live-blocking
> because no auto execution exists yet, but they gate the auto rungs — see §3.

---

## 3. The go-live PATH (phased)

The build shipped the modules; going live means **composing and operating** them,
then climbing the autonomy ladder through measured gates. `main.run()` is the
compose-and-run entrypoint and is currently **not under test** — operationalizing
it is the first step.

### 3.0 Operationalize `main.run()`

Compose the real system, provision credentials into the keyring (§4), start the
APScheduler loop + the Discord bridge, and bring up the orchestrator API. The
Phase-0.5 control-plane bootstrap (orchestrator API skeleton + discord-bridge
delivering `#morning-brew`, with `/status` and `/killswitch`) is the minimum
operator surface; `POST /killswitch` must cancel unapproved proposals, disable
the order path, and disarm auto modes ([[draft-discord-control-plane]] §"Build
deltas", Phase 0.5, lines 238–240). Enforce single-scheduler deployment (§5).

### 3.1 The autonomy ladder

Server-side state; Discord is only the lever ([[draft-discord-control-plane]]
§"Graduated autonomy", ladder table lines 171–178):

| Mode | Behavior |
|---|---|
| `read_only` | Brew + alerts only; no proposals |
| `paper` | Full pipeline; orders → Alpaca **paper** |
| `review_live` | Live orders require explicit button approval |
| `auto_veto` | Posts to `#proposals`, **auto-executes after a veto window (default 5 min)** unless operator rejects |
| `auto_full` | Executes immediately; Discord log-only |

### 3.2 Soak checkpoints and unlock gates

Two soak figures exist in the docs and gate **different** things — do not collapse
them into one number:

- **Paper operation** (≈2 weeks each): Phase 1 (read-only Morning Brew) exit =
  "two weeks of daily briefs" with burn comfortably inside the 20× window; Phase 2
  (triage + escalation + paper) exit = "≥2-week paper replay with full audit
  trail; zero unapproved executions"
  ([[cockpit-review-and-integrated-plan]] §4, lines 132–133). The scaffold
  likewise mandates a **2-week paper soak before any live-mode work**
  ([[build-scaffold-ultracode]] §"Build order & session plan", line 199).
- **Automation unlock** (≥4 weeks each) — the longer windows govern the auto
  rungs, enforced by the API (not vibes):

| Unlock | All conditions required | Cite |
|---|---|---|
| `→ auto_veto` | ≥4 weeks in `paper`/`review_live`; ≥30 adjudicated proposals; operator approval rate ≥80%; realized max drawdown inside the configured limit; **zero** attempts to bypass a failed risk check | [[draft-discord-control-plane]] §"Performance gate", lines 202–205 |
| `→ auto_full` | **Additionally** ≥4 weeks in `auto_veto` with veto rate ≤5% and no circuit-breaker trips | [[draft-discord-control-plane]] lines 206–207 |
| Both auto modes | **Re-arm daily** — disarm at market close, require a one-tap `/mode` confirm each morning | [[draft-discord-control-plane]] lines 208–210 |

The **performance-gate calculator** (WP5) evaluates these from stored
proposals/approvals; G5 requires it refuse an unlock below threshold. Concrete
threshold *values* live in config and are not enumerated in the build docs —
treat as per the plan / TBD, do not invent ([[build-scaffold-ultracode]] WP5;
[[ultracode-build-playbook]] §5 gate G5). No dollar minimum for the Phase-3 live
fund is specified beyond "a loss-tolerable amount" (operator to set)
([[cockpit-review-and-integrated-plan]] §4, line 134).

### 3.3 Phase 3 (gated live, small fund)

Robinhood Agentic account funded with a loss-tolerable amount; approval gate wired
to the MCP order tools; daily live-mode unlock that expires at close; kill switch.
**Exit:** live path unreachable without mode + risk + approval (test); first live
cycle audited end-to-end ([[cockpit-review-and-integrated-plan]] §4, line 134).
Phase-3 additions: live-mode **modal double-confirm** (type-the-symbol) above a
notional threshold; single-use, expiring approvals that void and re-price on stale
quotes; redaction-mode decision (%/R instead of $) since trade/balance data lands
on Discord servers ([[draft-discord-control-plane]] Phase 3 additions, line 244).
The **Robinhood Agentic MCP live path is a Phase-3-gated stub only**
(`robinhood_mcp.py`), explicitly deferred ([[build-scaffold-ultracode]] §"Deferred",
line 203).

### 3.4 Phase 3.5 (`auto_full`)

`auto_full` unlock logic beyond the mode-ladder state machine is deferred to
Phase 3.5, plus daily re-arm and **breakers proven by at least one deliberate
trip test** ([[draft-discord-control-plane]] Phase 3.5, line 246;
[[build-scaffold-ultracode]] §"Deferred", line 203). Any such unlock must honor
Invariant 2: `auto_veto`/`auto_full` refuse to arm if any decision-stage job is
on a non-Claude runtime ([[draft-discord-control-plane]] §"Design consequence",
lines 189–192).

> **Auto-rung security preconditions** (on top of the performance gate): SEC-01
> synchronous injection/anomaly breaker threaded into the order path; SEC-08
> runtime pin enforced at execution + Claude-exhaustion drop to `review_live`;
> SEC-20 gate computed solely from the verified anchored chain with a bypass-attempt
> detector; SEC-23 `auto_veto` fail-closed on confirmed delivery + monotonic clock;
> SEC-16 on-allowlist canary class; SEC-14 auto-exec coordinator seam wired to
> thread `injection_predicate` and gate on `flagged`; SEC-15 reconciled holdings
> trust root + privileged `/limits` ceremony; SEC-19 CompiledRule hashing before
> any automation-rule compiler ships. All depend on SEC-11.
> ([[security-hardening-backlog]] §"Ladder gates" lines 744–748; §SEC-01/08/14/15/16/19/20/23.)

---

## 4. Operational provisioning checklist

The canonical procedure is [[setup/accounts-and-keys]]. Golden rule / Invariant 4:
secrets live **only** in Windows Credential Manager via `keyring`, never in `.env`,
source, prompts, chat, or commits.

**Keyring secrets** — service name `ai-investment-cockpit`; run each
`keyring set ai-investment-cockpit <name>` (prompts for the hidden value so it
never lands in shell history). Exact names the app reads via
`core.secrets.require_secret(...)`:

| Secret | Source | Used for | Phase |
|---|---|---|---|
| `alpaca_key` / `alpaca_secret` | One Alpaca API key+secret | Benzinga news WS (`wss://stream.data.alpaca.markets/v1beta1/news`), free IEX data, **and** the Phase-2 paper account (paper is API-identical to live) | 1 → 2 |
| `finnhub_key` | Free Finnhub account (60 calls/min, real-time US quotes) | Phase-1 read path | 1 |
| `discord_bot_token` | Developer Portal → New Application → Bot → Reset/Copy Token | Discord operator surface | 0.5 |

Cite: [[setup/accounts-and-keys]] §2, §4, §5 (secret-name table + `keyring set`).

**Discord operator surface** ([[setup/accounts-and-keys]] §3): (1) new PRIVATE
guild only you are in; (2) create the application + bot, copy token; (3) enable
**only** the Message Content intent (needed to read free-text Interrogate replies;
all other privileged intents off); (4) invite via OAuth2 with `bot` +
`applications.commands`, minimal permissions. Record two **non-secret** ids as
config (env `COCKPIT_*` / config file, NOT keyring): `guild_id` and
`operator_user_id`. Every state-changing interaction is checked against the
operator id (single-operator allowlist). Carry `mode` (defaults `read_only`).

**AI runtime** ([[setup/accounts-and-keys]] §1): confirm Claude Code (Max 20×) is
authenticated (`claude` starts logged in) — the copilot drives `claude -p`
subprocesses, not the SDK, and headless use burns normal plan limits. Codex is
optional, research/coding only, **barred** from the decision/order path; decision
agents run with an empty tool set and hold no broker creds or MCP connection.

**Verify** ([[setup/accounts-and-keys]] §5 final): once the app exists,
`python -c "from core.secrets import require_secret; print(require_secret('finnhub_key'))"`
must print `Redacted('***')`, never the raw value.

**Box-hardening posture** ([[setup/accounts-and-keys]] §6): repo PRIVATE; run as a
service; BitLocker on; agents under a separate low-priv Windows account; Discord
redaction default-on; and **do not** put the main portfolio on the Robinhood login
the agent connects to — it reads ALL accounts, so keep the main portfolio at a
different broker.

**Phase 3 live (do NOT fund before the paper soak passes its gate)**
([[setup/accounts-and-keys]] §4, §6): opt into Robinhood Agentic in-app, fund a
**dedicated Agentic sub-account** with only a loss-tolerable amount (that balance is
the entire trading downside), then
`claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`
(desktop OAuth; session lives in the MCP session, not keyring).

**Moving-target re-verification (pre-live blocker, before funding).** Re-verify all
three against fresh primary sources immediately before Phase 3 — build completion
does NOT satisfy this ([[setup/accounts-and-keys]] §7;
[[setup/moving-targets-refresh-2026-07-06]]):

1. Anthropic SDK-credit / plan un-pause status (was paused, officially in flux).
2. Robinhood Agentic scope, rate-limits, OAuth session TTLs (none published for
   headless 24/7 — budget a re-auth path, OPS-05).
3. Per-broker post-PDT house margin rules (**not** re-run on 2026-07-06).

The 2026-07-06 refresh is the latest pass and has not been re-run since; the
platform does **not** enforce per-trade approval, so app-side guardrails are the
only protection.

---

## 5. Polishing

Non-blocking-for-paper, but do before/around live. Each cites its source.

| Item | What | Cite |
|---|---|---|
| **Dependency pin + hash-lock** | Pin exact versions, generate a hash-locked lockfile, verify lockfile hash on install in CI (unpinned today, no lockfile). | [[security-architecture]] §7, §11; also §2 above |
| **Logging secret-scrubber final wiring** | Confirm the default-on outbound scrubber is wired on every log/embed path; finish SEC-09 residual — redact dashed/hyphenated account numbers and lowercase/space-broken foreign ticker+size positions. | [[security-hardening-backlog]] SEC-09 WP8 note, lines 297–310 |
| **SEC-17 deterministic provenance** | Render source excerpts as byte-offset substrings of the raw parquet; risk section strictly from the deterministic `RiskDecision`; test asserting no operator-facing fact/excerpt/risk value originates from an LLM field (model `generated_by` already dropped). | [[security-hardening-backlog]] SEC-17 lines 491–502 |
| **SEC-22 normalize-before-escape** | Add NFKC + confusables-fold before the M3 escape; demote M3 to best-effort obfuscation-reduction (homoglyph/alt-role/no-delimiter injections rely on downstream grounding). | [[security-hardening-backlog]] SEC-22 lines 590–605 |
| **SEC-24 CLI session store** | Pin the CLI session store to a DPAPI/EFS path; bounded-TTL purge; extend the secrets/PII grep to the session dir. | [[security-hardening-backlog]] SEC-24 lines 626–641 |
| **Single-scheduler `try_consume` lock** | Enforce single-scheduler deployment for `DeepBudget.try_consume` (per-process lock) or add a DB-level guard for multi-replica (SEC-21 operational residual). | [[security-hardening-backlog]] SEC-21 WP8 note, lines 582–588 |
| **CI hardening** | Lockfile-hash verification on install; the Invariant-1 call-site test, no-LLM-in-risk, secrets-grep, and the SEC-06 arg-list inspector all wired into CI. | [[security-architecture]] §11/§12; [[security-hardening-backlog]] SEC-06 |
| **Monitoring / alerting** | Per-vendor UsageWindow gauges (window %, time-to-reset), deep-model daily-budget burn (default 5/day), ingest health, breaker states, scheduler liveness as an edit-in-place `#system` embed via `/status`; warn at **70%** and **90%** window (E-07). Heartbeat + dead-man's switch, run-as-NSSM-service, fail-CLOSED on stale heartbeat (E-03). | [[enhancements-and-additions]] E-07, E-03; [[draft-discord-control-plane]] §"Circuit breakers" lines 212–216 |
| **Backups / DR (OPS-02)** | Backup/restore + offsite copy for the SQLite audit chain, DuckDB/Parquet corpus, and keyring; retention/compaction for the unbounded raw + audit stores. | [[security-hardening-backlog]] OPS-02 lines 676–681 |
| **Real session calendar (OPS-04)** | Add a US-equity trading-session calendar (holidays, half-days, DST/early closes) behind the market-hours guard; `ingest/calendars.py` is earnings/macro only. | [[security-hardening-backlog]] OPS-04 lines 685–687 |
| **MCP session lifetime (OPS-05)** | Token refresh, re-auth-on-expiry, auth-failure breaker, foreign-session monitoring for the 24/7 agent. | [[security-hardening-backlog]] OPS-05 lines 687–690 |
| **Host hardening (OPS-06)** | BitLocker full-disk, low-priv agent account (SEC-05), NTFS ACLs on the SQLite/parquet PII stores, orchestrator as an NSSM service. | [[security-hardening-backlog]] OPS-06 lines 691–694 |
| **Vendor rate-cap handling (E-08)** | Respect Finnhub 60/min; fail over to a tagged secondary (Alpaca IEX) with wider staleness tolerance, else serve cached-last-good tagged with `staleness_seconds` the risk engine treats as a HARD failed check. | [[enhancements-and-additions]] E-08 |
| **Corporate actions / tax lots (OPS-07)** | Handle splits/dividends + wash-sale minimally, or **explicitly declare out of scope** for the capped-downside experiment — must not be left silently wrong. | [[security-hardening-backlog]] OPS-07 lines 695–698 |

---

## 6. Nice-to-have / post-build roadmap (NOT required for a working product)

From the scaffold "Deferred" set and the E-01…E-12 enhancement catalog. The system
is complete without these — Discord is the control plane.

**Deferred features** ([[build-scaffold-ultracode]] §"Deferred", lines 201–205):
PWA dashboard (Phase-4 polish, not core — no inbound ports on the box); options
support (re-check beta scope first); Codex research jobs (Codex stays off the
decision path per Invariant 2).

**Enhancement catalog** ([[enhancements-and-additions]] E-01…E-12) — value-adds
beyond the WP0–9 baseline, several gate-blocking for the *auto* rungs but none
required for a paper-first working product:

| ID | Enhancement | Role |
|---|---|---|
| E-01 | Benchmark scorecard vs same-capital SPY buy-and-hold (≥30-proposal min-sample guard) | Feeds the performance gate (P0, Phase 2) |
| E-02 | Deterministic record/replay + regression harness (grows from WP9) | Makes the 2-week soak reproducible (P0) |
| E-04 | Cross-model proposal verification (Haiku-class, downgrade-only) | **Arm-blocking for `auto_full`** (Phase 3.5) |
| E-05 | Thesis lifecycle & invalidation watcher | Compiled predicate, human-confirmed (P1) |
| E-06 | Provenance & lineage DAG + `/trace` | Land columns BEFORE the soak (P1) |
| E-09 | Explainability surface (`/why`) — numbers, no LLM narration | Operator trust (P1) |
| E-10 | Shadow-live divergence detector | **Gates `auto_full`** (Phase 3.5) |
| E-11 | Post-mortem automation on breaker trips (advisory-only, never auto-apply) | P2, Phase 3 |
| E-12 | Personal-data minimization: broker segregation + default-on redaction | Set BEFORE funding live (P2) |

---

## 7. Ordered checklist — do these in this order

> This checklist is formalized as the gated work packages **W10 → W19** in
> [[production-work-packages]] (the coverage matrix + dependency DAG + `Accept:`/gate
> per package). Drive each one, one-per-session, with its paste-ready kickoff prompt in
> [[production-playbook]]. The step numbers below map to those packages.

1. **Bump doc frontmatter / status banners** to 2026-07-08 across the touched docs
   (build complete, paper-first, SEC-11 still OPEN). *(Doc hygiene; separate diff.)*
2. **Operationalize `main.run()`** — compose the system, put it under test, bring
   up the orchestrator API + scheduler + Phase-0.5 Discord bridge with `/status`
   and out-of-band `/killswitch`.
3. **Provision credentials (§4)** — Alpaca, Finnhub, Discord bot into keyring;
   record non-secret `guild_id` + `operator_user_id`; verify the redaction
   round-trip; confirm Claude Code authenticated.
4. **Apply box-hardening (§4)** — private repo, run-as-service, BitLocker, low-priv
   agent account, Discord redaction default-on, separate broker for the main
   portfolio.
5. **Run the read-only / paper soak** — Phase 1 (≥2 weeks daily briefs, burn inside
   the 20× window) → Phase 2 (≥2-week paper replay, full audit trail, zero
   unapproved executions).
6. **Close the P0 "live" blockers (§2)** — **SEC-11 first**: the MECHANISM is DONE
   (W10 `ea7e0fc` — monotonic HMAC-covered `seq` + domain-row binding + concrete
   witness adapters, G10 passed), so what remains is **ACTIVATION**: provision the
   RUNTIME out-of-domain witness (`discord-bot-token` in keyring + `audit_log_channel_id`
   for a private `#audit-log` channel, ACL denies the bot manage-messages) so
   `GitAuditLogAnchorStore.latest()` stops returning None and the gate/mode/gateway/
   budget consults stop failing closed. The git-commit witness is a reviewed-history
   mirror, never the runtime witness. Then W11 SEC-03/05/06, W12 SEC-02/04/13,
   W13 OPS-01/03 + pin/hash-lock deps.
7. **Wire polishing (§5)** — scrubber final wiring, single-scheduler
   `try_consume` lock, CI hardening, monitoring/alerting + heartbeat, backups/DR,
   session calendar, MCP session lifetime.
8. **Re-verify the three moving targets (§4)** against fresh primary sources —
   BEFORE funding.
9. **Enter `review_live`** once the P0 "live" set passes (mode + risk + approval;
   modal double-confirm; single-use expiring approvals; redaction decision).
10. **Fund a dedicated, loss-tolerable Agentic sub-account** and connect the
    Robinhood MCP; run the first live cycle audited end-to-end (Phase 3).
11. **Unlock `auto_veto`** only via the performance gate (≥4wk, ≥30 proposals, ≥80%
    approval, drawdown inside limit, zero bypass attempts) AND the P0 "auto" set
    (SEC-01/08/10/11/12/14/15/16/19/20/21/23).
12. **Unlock `auto_full`** (Phase 3.5) only after ≥4wk in `auto_veto` (veto ≤5%, no
    breaker trips), E-04 + E-10 landed, and a deliberate breaker-trip test passes.
    Both auto modes re-arm daily.

> **Nothing in steps 9–12 may proceed while SEC-11 is OPEN.** It is the P0 that
> gates both `review_live` degradation trust and every auto rung.
