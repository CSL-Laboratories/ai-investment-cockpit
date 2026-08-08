---
title: Security architecture — Local AI Investment Cockpit
type: security-architecture
status: active
last-verified: 2026-07-03
related: [research-report, research-supplement, cockpit-review-and-integrated-plan, draft-discord-control-plane, build-scaffold-ultracode, prompt-injection-defense, security-hardening-backlog, upside-and-roi, enhancements-and-additions]
---

# Security architecture — Local AI Investment Cockpit

The security spine the whole system hangs on. This document is normative: where it
conflicts with convenience, security wins. It builds on the nine invariants in
[[build-scaffold-ultracode]] and the autonomy ladder in
[[draft-discord-control-plane]], and it treats the two research docs
([[research-report]], [[research-supplement]]) as the constraint source — every
load-bearing claim below is cited to a verified fact or marked **[est.]**.

> **The one structural fact everything else defends.** The LLM has **no order
> authority**. Order placement is reachable through exactly one path —
> deterministic risk-engine pass → mode check → approval/veto gate → broker
> connector (**Invariant 1**). No prompt, no hallucination, no injected news item,
> and no compromised model session can reach `place_equity_order` by any other
> route. Everything in sections 2–12 is defense-in-depth *around* that single
> gated path. If you internalize one thing from this file, internalize that the
> risk engine and the human gate are the trust root, and the models are untrusted
> input generators sitting outside it.

> **Scope of the structural claim — read this.** "No injected text can reach
> `place_equity_order`" holds **fully in the human-gated modes** (`read_only`,
> `paper`, `review_live`). In the auto modes (`auto_veto`, `auto_full`) there is no
> per-trade human, so the deterministic risk engine, the symbol allowlist, the
> grounding verifier, the injection/anomaly breaker, and the canary — **not a
> human** — are the last line. This baseline document specifies the intended
> architecture; the **required hardening that closes the auto-mode and enforcement
> gaps found by adversarial review is tracked in [[security-hardening-backlog]]**
> (SEC-01…SEC-24) and is a prerequisite before any auto mode or live capital. Treat
> that backlog as normative, not optional.

---

## 1. Security objectives & non-negotiables

| # | Objective | Meaning | Primary control |
|---|---|---|---|
| **O1** | Capital protection | Realized loss is bounded by the deliberately-funded, loss-tolerable Agentic-account balance (**outer bound**, the verified funds-level sandbox) and *further* limited by the risk engine's daily-loss/notional caps (**inner bound**). The caps tighten loss within the sandbox; they do not add to it. | Segregated Agentic account ([[research-report]] §1) + deterministic caps (Inv 5, 9) |
| **O2** | No unapproved or unintended live trade | Every live order traces to a risk pass **and** an explicit human decision (or an armed, gated auto-mode that itself required the gate). | Single order path (Inv 1) + approval gate |
| **O3** | No secret disclosure; minimize PII in context | Secrets (broker keys, bot token, AI-session material) never appear in prompts, env files, logs, Discord, or model context. Portfolio data — which the copilot *must* see to size trades — enters context only in **minimized** form (never raw all-account PII; [[security-hardening-backlog]] SEC-09). | keyring-only secrets (Inv 4) + outbound PII scrubber |
| **O4** | Tamper-evident audit | Any after-the-fact alteration of the decision record is detectable. | Hash-chained `AuditEntry` (Inv 6) |
| **O5** | Fail-safe, never fail-open | Every degradation mode (stale data, limit exhaustion, breaker trip, Discord outage, MCP anomaly) reduces authority; none silently grants it. | Watch-only degradation (Inv 7) + breakers → `review_live` |

**Non-negotiables** (violation = build defect, not a tuning knob):

1. There is no second code path to the broker. The invariant-1 call-site test is a
   merge blocker.
2. No LLM call executes inside `risk/` (Inv 5). Risk is deterministic pure
   functions over typed inputs.
3. No LLM output enters the pipeline before JSON-Schema validation (Inv 3).
4. Live/auto modes (`review_live`/`auto_veto`/`auto_full`) refuse execution if any decision-stage job
   runs on a non-Claude runtime (Inv 2 — OpenAI's own policy bars automating
   high-stakes financial decisions without human review; [[research-supplement]] §3).
5. Live mode is off by default (`read_only`), re-arms daily, and drops on any
   breaker trip. "The models feel good lately" is never an unlock condition.

---

## 2. Asset inventory — what an attacker wants

Ranked by blast radius. This is the target list; sections 3–12 defend it.

| Asset | Why an attacker wants it | Where it lives | Worst-case if lost |
|---|---|---|---|
| **Live-order authority** (Robinhood Agentic MCP session) | Move real capital in the funded account | Outbound MCP connection held by the bridge/broker connector | Bounded by the funded Agentic balance — the verified **funds-level sandbox** caps it; agent cannot move money in/out ([[research-report]] §1) |
| **Funded Agentic-account balance** | Direct theft via bad trades / wash into illiquid names | Robinhood, reachable only via the gated order path | = O1 max downside; that funding *is* the priced option premium ([[upside-and-roi]] §3) |
| **Broker/API keys + Discord bot token** | Bypass the app entirely; connect a rogue client | Windows Credential Manager (DPAPI) | Full broker/bot impersonation until rotated → **kill switch + rotate** (§10) |
| **Portfolio data + PII** | Financial surveillance, targeting, deanonymization | Robinhood (MCP grants **read access to ALL accounts**), SQLite, Parquet | **Verified privacy exposure**: all-account read + that data flows to the AI provider running the agent ([[research-report]] §1). Mitigation is architectural, not a patch — keep the main portfolio at a *different* broker so the agent sees only the Agentic account |
| **Audit-log integrity** | Hide a manipulation after the fact; forge a "clean" replay | SQLite `AuditEntry` (hash-chained) | Undetected tamper would poison the performance gate that unlocks auto modes — hence Inv 6 |
| **AI subscription session/tokens** | Ride the paid plan; exfiltrate context | `claude`/`codex` CLI auth stores on the workstation | Plan abuse + context leak; note the subsidy landscape is volatile (SDK credit **paused 2026-06-15**, [[research-report]] §3) so usage anomalies also have a cost signal |
| **Discord operator channel** | Forge an approval; issue a slash command; read balances | Private guild + bridge process | Forged approval → unintended trade **if** user-ID allowlist is bypassed; balances on Discord's servers unless redaction mode is on (§9) |

---

## 3. Adversary model & trust boundaries

### 3.1 Adversaries

| Adversary | Vector | Realistic goal |
|---|---|---|
| **Prompt injection via untrusted news/market data** | A crafted headline/summary in the Alpaca Benzinga firehose or a Finnhub payload instructs the model to propose or place a trade | Turn the model into a confused deputy that emits a malicious `TradeProposal` |
| **Compromised / spoofed MCP endpoint** | DNS/TLS interception, a look-alike host, or a malicious response from `agent.robinhood.com/mcp/trading` | Redirect orders, exfiltrate the all-account read, or feed poisoned tool results back as "trusted" |
| **Discord account / interaction forgery** | Stolen operator session, a second guild member, replayed/retried interactions | Approve a trade the operator never approved |
| **Dependency supply-chain compromise** | A malicious or typosquatted PyPI package, or a compromised transitive dep | Code execution inside the trusted process; read keyring; add a covert order path |
| **LAN network attacker** | Another host on the local network probing for open ports | Reach the orchestrator API or sniff traffic |
| **Internal: buggy / hallucinated LLM output** | The model emits malformed, over-sized, or nonsensical proposals | An unintended trade slips through weak validation |
| **Internal: operator error** | Fat-fingered approval, wrong mode, mis-set limit | Self-inflicted bad trade |

### 3.2 Trust boundary

```text
            UNTRUSTED (validate / gate everything crossing inward)
  ┌───────────────────────────────────────────────────────────────┐
  │  news + market data (Alpaca WS, Finnhub)                       │
  │  ALL LLM outputs (claude -p / codex exec results)             │
  │  inbound Discord interactions (buttons, slash cmds, replies)   │
  │  MCP tool responses (Robinhood Agentic, incl. read-all data)  │
  └───────────────────────────────┬───────────────────────────────┘
                                   │  schema validation · allowlist ·
                                   │  ID check · TLS+pin · risk rules
  ┌────────────────────────────────▼──────────────────────────────┐
  │  TRUSTED (the decision root — deterministic, no LLM inside)    │
  │  • deterministic risk engine (risk/, pure functions)          │
  │  • keyring / Windows Credential Manager                       │
  │  • hash-chained audit log                                     │
  │  • operator user-ID allowlist                                 │
  └───────────────────────────────────────────────────────────────┘
```

**The news → LLM surface is the largest untrusted-input attack surface** and has
its own dedicated doc — see [[prompt-injection-defense]] for the full treatment
(input framing, provenance tagging, capability starvation, canary detection). It
is summarized here and deferred there; do not duplicate its content. The
load-bearing point for *this* doc: even a fully successful injection cannot place
an order, because the injected text produces at most a `TradeProposal`, and a
proposal is inert until it clears the deterministic risk engine **and** the human/
gated approval path (Inv 1). Injection is a proposal-quality problem, not an
execution problem.

---

## 4. Defense-in-depth layers mapped to the nine invariants

| Layer | What it defends | Invariant | How it's enforced (code · test) |
|---|---|---|---|
| **L1 — Single order path** | O2: no alternate route to the broker | Inv 1 | `brokers/` order calls reachable only from the approval/veto path. **Call-site grep test** (WP7) fails CI if any other module imports the place-order function |
| **L2 — Deterministic risk engine** | O1/O2: no LLM authority over eligibility | Inv 5 | `risk/` is pure functions; a test asserts **no LLM/subprocess import** under `risk/`; property test: no input yields an approved decision with a failed hard check (WP4) |
| **L3 — Schema-gated LLM output** | Internal buggy/hallucinated output; injection payloads | Inv 3 | `jsonschema` validate → one repair attempt → quarantine + `needs_human_review`. Mocked-subprocess tests for malformed/repair/quarantine (WP1b) |
| **L4 — Human/approval gate** | O2: the safety layer the platform does **not** provide | Inv 1 | Robinhood does **not** enforce approval — verified ([[research-report]] §2); the gate is hard-coded in `approval-center`, not prompt-based. `ApprovalRecord` written before any live submit |
| **L5 — Mode ladder + performance gate + live/auto Claude-runtime pin** | O2: premature or non-compliant automation | Inv 2 | Mode state machine rejects illegal transitions; gate calculator evaluates from stored proposals/approvals; Codex may analyze and draft in human-gated `read_only`/`paper`, while live/auto modes **refuse to arm** with a Codex-configured decision stage (WP5 + CXP-05a) |
| **L6 — Deterministic circuit breakers** | O5: runaway loss / degraded inputs | Inv 5, 7 | Five breakers — loss limit, N consecutive losses, schema-failure spike, stale data, broker-error spike — **auto-drop to `review_live`** + `#system` ping. (Usage-window exhaustion is handled separately by L8 → **watch-only**, not `review_live`; and a synchronous **injection/anomaly breaker** is required — [[security-hardening-backlog]] SEC-01.) Deliberate breaker-trip test (WP9) |
| **L7 — Global kill switch** | O2/O5: instant containment | Inv 1 | `POST /killswitch`: cancel unapproved proposals, disable order path, disarm auto modes, and **disconnect the MCP** (this last is an explicit **extension to the WP5 `/killswitch` contract** in [[draft-discord-control-plane]] — noted so the two stay in sync). An in-process handler cannot preempt a wedged loop, so it is backed by an **out-of-band HALT sentinel** the order gateway checks before every submit ([[security-hardening-backlog]] SEC-04). |
| **L8 — Usage-window degradation** | O5: never trade on stale analysis | Inv 7 | **Watch-only** = order path disabled, no new trades, events queued + expired (never dropped, never `review_live`). Fail-closed trigger: any limit-hit **or** any empty/truncated/timed-out/non-schema-valid decision response trips it (not only a known limit-error string). A monotonic `usage_window_generation` stamps every analysis so the submit path refuses anything produced before exhaustion; recovery is fail-closed to `review_live` (never a silent auto re-arm). [[security-hardening-backlog]] SEC-23. Mid-loop limit-hit test (WP9) |
| **L9 — Secrets isolation** | O3 | Inv 4 | keyring only; secrets never reach agents (they hit the broker only via MCP). Secrets-in-logs grep test (WP9) |
| **L10 — Tamper-evident audit** | O4 | Inv 6 | Hash-chained `AuditEntry` (`prev_hash` per row); chain-break detection test (WP2) |
| **L11 — Input/summary separation** | Traceability; fresh-context interrogation | Inv 8 | Raw inputs in Parquet keyed to `raw_ref`; model summaries stored separately; every memo traces to source |
| **L12 — Post-PDT risk model** | O1: correct margin/settlement logic | Inv 9 | Intraday-margin-deficit estimate + Reg-T $2k min + settlement rules; **no day-trade-count logic** (PDT eliminated 2026-06-04, [[research-supplement]] §1) |

The layers are independent: defeating L3 (a malformed proposal slips validation)
still meets L2 (risk rejects) and L4 (no human approval). Defeating L4 (forged
approval) still meets L2/L5/L6. There is no single control whose failure places a
trade.

---

## 5. Secrets management

**Store.** All secrets live in **Windows Credential Manager via `keyring`** (DPAPI
at rest, tied to the Windows user). Scope: Alpaca key/secret, Robinhood Agentic
MCP auth material, Finnhub key, Discord bot token. Never in env files, `.env`,
source, `models.yaml`, prompts, logs, or Discord (Inv 4).

**Distribution — least privilege by process.**

| Process | Secrets it holds | Rationale |
|---|---|---|
| Orchestrator / bridge (WP0 `core/secrets.py`) | Loads broker/data/bot creds from keyring **at start** | Single trusted loader; holds the outbound connections |
| `claude -p` / `codex exec` agents | **None** | Agents never receive credentials **and never hold an MCP connection**. The `brokers/` layer owns the broker session and performs all reads/submits through the gated order path; portfolio/quote data reaches a model only as a pre-fetched, `<untrusted-content>`-wrapped packet ([[prompt-injection-defense]] §4/§9). Claude uses `--allowedTools none`; Codex decision jobs ignore user config/rules, run ephemeral/read-only, explicitly disable shell/web/apps/browser/computer/MCP features, and are argv-inspected before spawn. Prompts never carry keys ([[cockpit-review-and-integrated-plan]] §1). |

This is the concrete meaning of "the LLM has no order authority" at the secrets
layer: the model literally never holds anything that could authenticate an order
outside the gated path.

**Audit records key NAMES, not values.** `AuditEntry` logs `alpaca_key` /
`robinhood_mcp` as identifiers; never the material ([[cockpit-review-and-integrated-plan]] §3).
The secrets-in-logs grep test (WP9) is a merge blocker.

**Rotation & revocation.**

1. Rotate at the provider (regenerate Alpaca keys / re-auth MCP / reset bot token).
2. `keyring.set_password` the new value under the same name — no code change
   (names are stable; values rotate).
3. Restart the trusted process so connections re-open with fresh creds.
4. Write an `AuditEntry` (`event=secret_rotated`, key name only).
5. Kill switch (`POST /killswitch`) **disconnects the MCP** as part of
   containment, so a suspected leak is contained before rotation completes.

---

## 6. Network posture

**Zero inbound ports.** The workstation opens no listening socket to the network.
The orchestrator API binds **`127.0.0.1` only**, with API-key auth on every route;
the Discord bridge holds an **outbound WebSocket** to Discord and nothing else
([[draft-discord-control-plane]] topology). There is no LAN port to probe — this
is why the design chose Discord-as-surface over a dashboard-first design.

| Property | Setting | Defends |
|---|---|---|
| API bind | `127.0.0.1` loopback, API-key auth | LAN attacker (§3) has no reachable surface |
| Inbound ports | **none** | No tunnel/VPN, no open port to misconfigure |
| Bridge → Discord | outbound WSS only | Push/history/mobile without exposure |
| Remote operation | via Discord's own apps | No custom remote endpoint on the box |
| Future PWA (Phase 4, optional) | **Tailscale (or equivalent) only** | Never an open LAN port; the single sanctioned remote path if added |
| MCP endpoint | pinned `https://agent.robinhood.com/mcp/trading` + strict TLS verify | Spoofed/compromised endpoint (§3) |

**MCP pinning.** The Robinhood connector pins the exact endpoint URL and enforces
TLS certificate verification (no `verify=False`, no custom CA injection). A
connection to any other host, or a cert failure, aborts and raises a `#system`
alert rather than proceeding. Treat every MCP **response** as untrusted input
(§3, §7) even over a verified channel.

---

## 7. Supply chain & runtime isolation

**Pinned + hash-locked deps.** `pyproject.toml` pins exact versions; a lockfile
with hashes (`pip`/`uv` hash-locking) makes installs reproducible and blocks a
silent malicious republish. Review new/transitive deps before adding; prefer the
small settled stack (FastAPI, discord.py, APScheduler, SQLAlchemy, DuckDB,
`jsonschema`, `keyring`) over breadth.

**Minimal tool grants per agent.** Every `claude -p` decision call passes
`--allowedTools none`. Codex has no equivalent single flag, so decision calls use
`--ignore-user-config`, `--ignore-rules`, `--ephemeral`, `--sandbox read-only`, an
explicitly disabled web-search config, and a deny list covering shell, apps, browser,
computer use, skills/plugins, MCP elicitation, and multi-agent execution. **The
trading/decision agent needs an empty capability set** — no filesystem read/write,
shell, network, or MCP ([[cockpit-review-and-integrated-plan]] §1). A test inspects the
actual subprocess argv and fails if any required denial disappears (capability
starvation, not merely filesystem-write sandboxing) —
[[security-hardening-backlog]] SEC-06.

**Pinned working directory + sandbox.** Build/run agents get a pinned working
directory. The decision-stage agent operates on stored input packets, not free
filesystem roam.

**Feature-detect, never assume, CLI flags.** `--json-schema` and `--effort` are
**unverified flags** ([[cockpit-review-and-integrated-plan]] §1); the runner
feature-detects at startup (`claude --help`) and **the runner's own
`jsonschema` validation stays authoritative regardless** of whether a native flag
exists (Inv 3). Assuming a flag that silently no-ops would be a validation bypass.

**Review the MCP tool surface.** Before wiring the live path (Phase 3), enumerate
the MCP tool set; call `review_equity_order` before `place_equity_order` **even
though the platform does not force it** (WP7). Treat every MCP response as untrusted
— but note the backstop is **not** schema validation: Inv 3 validates *LLM output*,
not the tool results the `brokers/` layer reads. A poisoned read (e.g. a manipulated
buying-power/position value from a compromised-response endpoint) is instead bounded
by the **deterministic config-constant caps** (max notional, daily-loss) that hold
regardless of the read, by the human gate outside the auto modes, and by the funds
sandbox. Free-text MCP fields that re-enter a prompt are wrapped and scrubbed as
untrusted content ([[prompt-injection-defense]] §9, [[security-hardening-backlog]] SEC-02).

**Data at rest & host hardening.** The local box is the crown jewel. The SQLite
audit DB and DuckDB/Parquet snapshots hold portfolio PII and the operator's Discord
ID in plaintext — restrict them to the Windows user via NTFS ACLs, enable
**BitLocker** (full-disk), and consider SQLCipher / a DPAPI-wrapped data key for the
PII stores ([[security-hardening-backlog]] OPS-06, OPS-02). The `claude`/`codex` CLI
session stores and `~/.codex/auth.json` are **bearer-credential files** on disk —
treat them as first-class assets (tight ACLs, bounded TTL, ideally a separate
low-privilege account), because a stolen MCP/CLI token authenticates *outside* this
app's gated path ([[security-hardening-backlog]] SEC-05).

---

## 8. Audit integrity

**Append-only, hash-chained — with the caveats that make it real.** Each
`AuditEntry` stores the previous row's hash in `prev_hash`; the chain detects a naive
single-row edit that leaves downstream hashes stale (Inv 6;
[[cockpit-review-and-integrated-plan]] §3). **A plain unkeyed chain is *not*
sufficient** against a local writer who recomputes the whole tail, or who truncates
it — and the gate-unlock threat model is exactly that privileged local actor. The
chain is therefore **HMAC-keyed** (key in keyring, unreachable by agents), covers a
**monotonic sequence number** (rollback/truncation detection) and **all load-bearing
columns under a pinned canonical serialization**, and **anchors its head out-of-band**
(posted to `#audit-log` and committed to git) so a full rewrite shows up as head
divergence — [[security-hardening-backlog]] SEC-10…SEC-13. Every state-changing action
writes its `AuditEntry` in the **same transaction** as the state change (two-phase
intent/result across the broker boundary), so there is no success without a record and
no record without the success.

**Recorded** (the full decision provenance): source-data `raw_ref`, the exact
prompt, model/runtime + version/alias, per-check `RiskDecision` results, approvals
(`ApprovalRecord`: actor user-ID, interaction/message ID, device, timestamp,
surface), order attempts, fills, breaker trips, mode changes, and errors.

**Not recorded**: secret values (key *names* only, §5), and no raw PII beyond what
the decision genuinely requires.

**Trustworthy replay.** The performance gate that unlocks auto modes
([[draft-discord-control-plane]]) is only as trustworthy as the audit chain it
reads. A paper-trading replay is made trustworthy by **verifying the chain first** —
recompute each HMAC from `prev_hash` + the canonically-serialized load-bearing
columns, and compare the live head against the **externally-anchored** head
(git / `#audit-log`), not just internal link continuity; any mismatch, or a head
sequence below the last anchor, invalidates the replay and blocks any gate unlock
([[security-hardening-backlog]] SEC-10…SEC-13, SEC-20). This is the concrete payoff of hash-chaining:
the day you want to trust two weeks of paper results enough to arm `auto_veto`,
you can prove the record wasn't edited.

---

## 9. Discord operator-surface security

Discord is a **delivery surface, not the source of truth** — server-side state in
the orchestrator is authoritative ([[draft-discord-control-plane]]).

| Control | Rule | Defends |
|---|---|---|
| Private guild | Operator-only guild; bot requires only the **Message Content** privileged intent (needed to read free-text thread-reply / Interrogate content), scoped to the private guild; all other intents off | Second-party access; intent over-reach |
| Single-operator allowlist | **Every state-changing interaction** verifies the operator's Discord user-ID against a one-entry allowlist | Interaction forgery / a second guild member (§3) |
| Modal double-confirm | Live-mode approval **above a notional threshold** pops a modal ("type the symbol to confirm") | Fat-finger + a stolen-session single-click |
| Single-use + expiring approvals | An approval is consumed once; if the quote goes stale past the risk threshold, the approval is **void** and the proposal re-prices and re-posts | Replay of an old approval; stale-quote execution |
| Idempotency | Approvals/commands are idempotent against Discord's **interaction retries** | Duplicate execution from gateway retries |
| Redaction mode (**default-on**) | Percentages / R-multiples instead of dollar amounts; opting out writes an `AuditEntry` | Balances otherwise **live on Discord's servers** (a third party — outside the trust boundary); [[draft-discord-control-plane]] ToS table, [[security-hardening-backlog]] SEC-09 |

**Outage fallback is fail-safe.** If Discord is down, the orchestrator **queues**
proposals (which still expire per quote-staleness) and falls back to desktop
notification — it does **not** auto-approve to "keep things moving" (Inv 7 posture).

**Auto-mode friction.** `auto_veto`/`auto_full` re-arm daily and require a one-tap
morning `/mode` confirm; the veto-window race is resolved atomically (a veto at
T-1s wins; execution at T+0 re-checks mode + breakers before submitting) — see the
WP6 interaction tests.

---

## 10. Incident-response runbook

Every incident: **detect → contain → eradicate → review the audit chain →
post-mortem.** Containment is always available via `POST /killswitch` (cancels
unapproved proposals, disables the order path, disarms auto modes, disconnects the
MCP).

| Incident | Detect | Contain | Eradicate | Review + post-mortem |
|---|---|---|---|---|
| **Suspected injection / anomalous proposal** | Canary trip ([[prompt-injection-defense]]); a proposal that ignores risk context or cites no `raw_ref` | Kill switch; drop to `read_only` | Trace `raw_ref` to the poisoned item; add its pattern to input defenses; requarantine | Verify chain; confirm no order placed; tighten proposal schema/prompts |
| **Leaked key** | Unexpected broker/API activity; secrets-grep hit in a diff | Kill switch → **disconnect MCP**; rotate at provider | New value into keyring under same name; restart trusted process | Audit for any action under the leaked key; find the leak source (log? prompt? dep?) |
| **Breaker storm** (repeated trips) | `#system` breaker pings; mode auto-dropped to `review_live` | Already contained (breaker → `review_live`); optionally kill switch | Diagnose root cause (bad data feed? loss spike? schema-failure spike?) before re-arming | Recompute the day from the audit chain; do **not** re-arm auto modes until the gate re-passes |
| **MCP / broker anomaly** | Cert failure, endpoint mismatch, error spike, unexpected tool result | Kill switch → disconnect MCP; block the order path | Re-verify endpoint pin + TLS; re-enumerate the tool surface; treat prior responses as suspect | Confirm no order executed on a poisoned response; re-check Robinhood beta scope (moving target, [[research-report]] §5) |
| **Discord compromise** | Approval/command from a non-allowlisted ID; unexpected approvals | Kill switch; revoke bot token; drop to `read_only` | Rotate bot token; audit `ApprovalRecord` for forged interaction IDs | Confirm allowlist held (forged IDs should already be rejected — verify the test); rotate operator credentials |

---

## 11. Threat → control traceability

Every adversary from §3 maps to the specific controls that mitigate it and the
test that proves the mitigation.

| Adversary (§3) | Controls | Proving test |
|---|---|---|
| Prompt injection via news/market data | L3 schema gate; L2 risk engine; L4 human gate; input defenses ([[prompt-injection-defense]]) | Injection canary test; "malformed/hostile output cannot create an executable trade" (WP9) |
| Compromised / spoofed MCP endpoint | Endpoint pin + TLS verify (§6); treat MCP responses as untrusted input (§7); deterministic caps bound any resulting order; kill-switch disconnect | Endpoint-mismatch/cert-failure aborts; a poisoned response **cannot produce an out-of-bounds order** and is human-gated outside the auto modes (WP7) |
| Discord interaction forgery | Single-operator user-ID allowlist; modal double-confirm; single-use expiring approvals; idempotency (§9) | Forged/foreign-user interaction rejected; replayed retry is idempotent; stale-quote approval voids (WP6/WP9) |
| Dependency supply-chain compromise | Hash-locked pinned deps; minimal `--allowedTools`; sandboxed working dir (§7) | Agent allowed-tools cannot touch FS outside sandbox (WP5/WP9); lockfile hash verification on install |
| LAN network attacker | Loopback-only API + API-key auth; zero inbound ports (§6) | No listening port beyond loopback (posture test); API rejects unauthenticated calls |
| Buggy / hallucinated LLM output | L3 repair-then-quarantine; L2 deterministic risk; property test (§4) | Malformed JSON → quarantine; no input yields approval with a failed hard check (WP1b/WP4) |
| Operator error | Modal double-confirm; deterministic caps; daily auto-mode re-arm; kill switch (§9) | Mode state machine rejects illegal transitions; caps enforced regardless of approval (WP5) |
| Premature automation | Mode ladder + performance gate + Claude-runtime pin (L5) | Auto mode refuses to arm below the gate or with a Codex decision stage (WP5) |
| Audit tampering | Hash-chained append-only log (L10) | Chain-break detection; replay blocked on hash mismatch (WP2) |
| Secret disclosure | keyring-only; agents get no creds; names-not-values in audit (§5) | Secrets-in-logs grep; no plaintext secret in repo/prompts/logs (WP0/WP9) |

---

## 12. Security test plan

These are security-specific tests layered on the WP acceptance tests in
[[build-scaffold-ultracode]]. All are CI-able pytest; the merge-blockers are
marked **(blocker)**.

1. **Invariant-1 call-site test (blocker).** Static check that the broker
   place-order function is imported/called from **only** the approval/veto path.
   Any other call site fails the build (Inv 1, §4 L1).
2. **Risk-engine adversarial tests (blocker).** Exhaustive per-rule unit tests
   with boundary values, plus a property test: *no* generated input yields an
   approved `RiskDecision` while a hard check fails (Inv 5, WP4). Includes
   post-PDT logic — intraday-margin-deficit + Reg-T $2k, **no** day-trade counts
   (Inv 9, [[research-supplement]] §1).
3. **Schema-bypass tests.** Malformed, oversized, and hostile LLM outputs →
   repair attempt → quarantine + `needs_human_review`; assert none can produce an
   executable proposal (Inv 3).
4. **Forged / foreign-user Discord rejection (blocker).** Interactions from a
   non-allowlisted user-ID are rejected; retried interactions are idempotent;
   an approval on a stale quote voids and re-prices (§9, WP6).
5. **Secrets-in-logs grep (blocker).** Repo, prompts, logs, and audit rows
   contain no plaintext secret; audit shows key *names* only (Inv 4, §5).
6. **Deliberate breaker-trip test (blocker before live).** Force each of the five
   `review_live` breakers (loss limit, consecutive losses, schema-failure spike,
   stale data, broker-error spike) and assert an instant drop to `review_live` +
   `#system` ping; a trip mid-`auto_full` halts before the next order (§4 L6, WP9).
   Usage-window exhaustion is tested separately (test 7 → **watch-only**).
7. **Usage-limit-mid-loop → watch-only (blocker).** Inject a limit-hit response
   mid-triage; assert the system enters **watch-only**, queues + expires events
   (never drops them), and never trades on analysis produced before exhaustion
   (Inv 7, §4 L8).
8. **Audit chain-tamper detection.** Mutate a historical row; assert the chain
   verifier flags it and a replay built on the broken chain is refused (Inv 6, §8).
9. **Network-posture test.** Assert the API binds loopback-only, rejects
   unauthenticated calls, and no non-loopback listening port exists (§6).
10. **MCP endpoint-pin test.** A mismatched host or TLS failure aborts the
    connection and raises a `#system` alert; a poisoned tool response never
    reaches the order path (§6, §7).
11. **Injection canary.** Cross-reference [[prompt-injection-defense]] — a seeded
    canary instruction in the news stream must be detected and must never yield an
    executable proposal (§3, §11).
12. **Live/auto runtime guard (blocker).** `review_live` refuses Codex
    configuration, and auto modes refuse to arm below the performance gate **or**
    with any decision-stage job on a non-Claude runtime (Inv 2, §4 L5). The
    gateway repeats the provenance pin immediately before every live submit.

---

## Appendix — verified facts this architecture leans on

| Fact | Source | Where it binds |
|---|---|---|
| Human approval is **NOT** platform-enforced by Robinhood | [[research-report]] §2 | L4 exists because nobody else provides it |
| Funds-level sandbox = **max trading downside** (agent can't move money in/out) | [[research-report]] §1 | O1 outer bound; asset-inventory blast radius |
| MCP grants **read access to ALL accounts**; data flows to the AI provider | [[research-report]] §1 | Privacy asset; separate-broker mitigation (§2) |
| Agent-SDK $200/mo credit **paused 2026-06-15**; headless burns normal limits | [[research-report]] §3 | Usage-window volatility → L8 watch-only degradation |
| PDT rule **eliminated 2026-06-04**; model intraday margin deficit | [[research-supplement]] §1 | Inv 9 post-PDT risk model (L12) |
| OpenAI bars automating high-stakes financial decisions without human review | [[research-supplement]] §3 | Inv 2 Claude-runtime pin (L5) |
| $0 data stack: Alpaca Benzinga WS + Finnhub free tier | [[research-supplement]] §2 | Untrusted-input surface (§3) |

**Re-verify at build time** (moving targets, [[research-report]] §5): Robinhood
Agentic beta scope ("equities-only" and "waitlisted/desktop-only" were refuted
0–3), the Anthropic SDK-credit un-pause status, and each broker's post-PDT house
margin rules. These do not change the architecture — the gated order path, the
risk root, and the fail-safe posture hold regardless — but they change specific
thresholds and scope checks.
