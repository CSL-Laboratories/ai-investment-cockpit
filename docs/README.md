---
title: AI Investment Copilot — research, architecture & build index
type: index
status: active
last-verified: 2026-07-08
related: [research-report, research-supplement, cockpit-plan-original, cockpit-review-and-integrated-plan, draft-discord-control-plane, build-scaffold-ultracode, ultracode-build-playbook, security-architecture, prompt-injection-defense, security-hardening-backlog, enhancements-and-additions, capability-expansion-master-plan, upside-and-roi, path-to-production, production-work-packages, production-playbook, refactor-plan, integration-and-remote-operations-plan, quant-engine, thesis-memory, quant-strat-branch]
---

# AI Investment Copilot — index

Research and architecture for a **solo-developer, subscription-powered AI investing
copilot**: a morning-brew daily briefing plus human-approved short/long-term trade
decisions, run from one central Windows workstation and operated from anywhere over
Discord.

**Feasibility verdict: FEASIBLE** — verified by two multi-agent deep-research runs
on 2026-07-02 (143 agents total; 33 of 37 verified claims confirmed by 3-vote
adversarial verification, 4 refuted, 0 left unverified).

## Reading pipeline

The corpus is a one-way pipeline — each stage constrains the next. Walk it in order
before non-trivial work; settled questions are settled downstream, don't re-litigate.

```text
research  →  architecture  →  build  →  security  →  enhancements
(verified)   (settled)        (WPs)     (threat model + hardening)  (roadmap)
```

> **Build status (2026-07-08): WP0–WP9 COMPLETE and green on `main`** (WP9 system-test
> harness = commit `9f6ff44`; 586 tests pass / 3 skipped; ruff clean; gates G0…G9 green).
> The system is **paper-first — no live capital has moved and none may** until the
> pre-live gates pass, including the OPEN P0 SEC-11 audit-spine (delete-then-heal)
> blocker in [[security-hardening-backlog]]. Go-live is formalized as the gated work
> packages **W10→W19** in [[production-work-packages]] (drive them one-per-session with
> [[production-playbook]]); [[path-to-production]] is the narrative guide behind them.

## Files

**Research (VERIFIED — the constraint source; 3-vote adversarial):**

| File | What it contains |
|---|---|
| [[research-report]] | Main verified findings: Robinhood Agentic MCP, subscription-vs-API terms, model-hierarchy precedents, the four refuted claims, open questions, sources |
| [[research-supplement]] | Gap-fill verification: the $0/mo Alpaca-news-WS + Finnhub data stack, FINRA PDT elimination, AI-provider policy, broker-policy nuance |

**Architecture (settled):**

| File | What it contains |
|---|---|
| [[cockpit-plan-original]] | The external "Local AI Investment Cockpit" architecture draft — preserved verbatim for traceability |
| [[cockpit-review-and-integrated-plan]] | Review of that draft against the verified research: the CLI-vs-SDK verdict, corrections, integrated phase plan |
| [[draft-discord-control-plane]] | Loopback FastAPI orchestrator + Discord operator surface + the graduated-autonomy ladder with performance gates and circuit breakers |

**Build:**

| File | What it contains |
|---|---|
| [[build-scaffold-ultracode]] | The build spec: all-Python stack, repo layout, the **9 INVARIANTS**, work packages WP0–WP9 with acceptance tests, build order |
| [[ultracode-build-playbook]] | *(Opus 4.8)* How to drive the WP0–WP9 build with many Opus 4.8 agents: runnable Workflow scripts, adversarial-review harness for the order path, verification gates, per-WP kickoff prompts |

**Status (2026-07-08): the WP0–WP9 build described by these two docs is COMPLETE and
shipped to `main`** — build order WP0→WP1a→WP1b→WP2→{WP3,WP4}→WP5→{WP6,WP7}→WP8→WP9,
all gates green (WP9 system-test harness = commit `9f6ff44`; 586 tests pass / 3 skipped;
ruff clean). These two docs are now the historical spec-of-record for the build; live
rollout beyond paper mode is governed by [[path-to-production]].

**Security:**

| File | What it contains |
|---|---|
| [[security-architecture]] | *(Opus 4.8)* End-to-end threat model + controls: assets, adversaries, defense-in-depth mapped to the 9 invariants, secrets, network posture, supply chain, audit integrity, incident runbook |
| [[prompt-injection-defense]] | *(Opus 4.8)* Hardening the untrusted-content ingest surface (news/MCP → LLM context): structural containment, canary, cross-model grounding, operator-facing defense |
| [[security-hardening-backlog]] | *(Opus 4.8)* The adversarial-review register — SEC-01…SEC-24 + OPS-01…OPS-07: every red-team finding (control-flow, injection, secret/PII/audit) with the mechanical control that closes it. **Required before any auto mode or live capital.** |

**Why + roadmap:**

| File | What it contains |
|---|---|
| [[upside-and-roi]] | Honest ROI: the ~$0 marginal cost stack, four return streams (time / alpha-as-capped-option / discipline / infrastructure), 3-year scenarios, risk ledger |
| [[enhancements-and-additions]] | *(Opus 4.8)* Post-scaffold value-adds (E-01…E-12): benchmark scorecard, cross-model verification, record/replay, heartbeat/DR, lineage graph, shadow-live — each tagged to its phase/gate |
| [[path-to-production]] | *(2026-07-08)* Post-build go-live + polishing guide: the gates that must pass before any live capital — SEC-11 (P0, **OPEN**) closure, the performance-ladder unlocks (per the plan's paper-soak requirement), moving-target re-verification, and the operational hardening needed to leave paper mode |

**Production (go-live — the W-series, 2026-07-08):**

| File | What it contains |
|---|---|
| [[production-work-packages]] | The pre-live/go-live build SPEC — the production analog of [[build-scaffold-ultracode]]: the **coverage matrix** (every open/partial SEC-01…24 + OPS-01…07 → exactly one W, or done/deferred), the **W10→W19 dependency DAG + build order**, and each work package (scope, `Accept:`, the adversarial gate G10…G19, dependencies). W10 (audit-spine / SEC-11) is the keystone everything auto/live depends on. |
| [[production-playbook]] | How to DRIVE the W-series with Opus 4.8 agents — the production analog of [[ultracode-build-playbook]]: the property-closure / adversarial-gate philosophy, the session bootstrap, reusable Workflow scripts, the G10…G19 gate table, and **paste-ready per-W kickoff prompts** for one-work-package-per-session execution. |

**Refactor and integration expansion (2026-08):**

| File | What it contains |
|---|---|
| [[refactor-plan]] | From fixture-driven build to a running dual-surface cockpit: the three architectural decisions (abstract the operator surface, dynamic universe, data-provider abstraction), the RW-01…RW-09 work packages with their GR-xx gates, and the honesty ledger of what the refactor does NOT do |
| [[integration-and-remote-operations-plan]] | The CXP-series for Discord/Telegram thread + group compatibility, Claude/Codex provider conformance, future OpenRouter/OpenCode spikes, credential/MCP isolation, multi-principal/account handling, and a restricted `cockpitctl` + SSH control path |
| [[runbooks/rw01-live-smoke]] | **Operator-run.** RW-01's acceptance evidence: provision the keys + private guild, run one real market day, confirm the morning brew lands in `#morning-brew`. Read-only throughout — it proves the system *runs*, nothing about whether it should trade |
| [[quant-strat-branch]] | **Start here for the `quant-strat` branch.** The complete change record: all four commits, what was missing and what now exists, the three authority tiers and the Invariant-1 containment argument, the operator surface, every bug found (in pre-existing code and in my own), the honest limitations, and what is deliberately NOT built |
| [[thesis-memory]] | **MEM-01.** The thesis memory layer: the `invalidation` field a proposal must state was written and never read back, and every draft was produced with no memory of prior theses on the same symbol. Adds standing theses fed into the deep call as prompt context (never as citable evidence — the self-grounding attack and the two mechanisms that close it are the core of the design), plus the `/thesis` operator loop that is the only way to retire a claim |
| [[quant-engine]] | **QT.** The microstructure half the cockpit never had: a real FIFO order book with queue position, strategies, replay, realistic per-share costs, PnL attribution. Consumed at three tiers — advisory scoring, a hard pre-trade check inside `risk/`, and an autonomous loop that is contained to a simulated venue by an AST import gate rather than by convention. Includes the honest limitations (the replay counterfactual, aggregate-feed queue estimates) and the A/B showing that requoting on every tick wins *fewer* passive fills |

The RW-series runs **alongside** the W-series, not instead of it: RW work makes the AI
smarter and the operator better-informed, and does not skip a single go-live gate. Live
capital is still gated on SEC-11 → paper soak → performance gate.

## The five facts everything else hangs on (all verified against primary sources)

1. **Robinhood Agentic Trading is live** (beta since 2026-05-27) at
   `https://agent.robinhood.com/mcp/trading`; Claude Code and Codex are officially
   documented clients. Trades are sandboxed to a separately funded Agentic account,
   but the agent can **read all accounts**, and **human approval is not
   platform-enforced** — the approval gate must be built client-side.
2. **Claude subscription headless use is sanctioned but un-subsidized**: the announced
   $200/mo Max-20x SDK credit was **paused 2026-06-15** — headless usage burns normal
   plan limits. Policy in flux.
3. **Codex on a ChatGPT plan is officially supported headless** (device-auth
   documented), with an opaque "agentic usage limit"; the sanction covers OpenAI's own
   clients, not harvested OAuth tokens.
4. **The pattern-day-trader rule is gone**: FINRA Rule 4210 amendments eliminated the
   PDT designation and $25k minimum **effective 2026-06-04**, replaced by intraday
   margin standards.
5. **A $0/month data stack is real**: Alpaca's Benzinga news WebSocket wildcard
   firehose + Finnhub's free tier (60 calls/min, real-time US quotes).

## Re-verify before building (moving targets)

- Anthropic Agent SDK credit un-pause status (changes the economics of the triage loop).
- Robinhood Agentic beta scope — "equities-only" and "waitlisted/desktop-only" were
  **refuted 0-3** in verification; asset classes and availability are moving fast.
- Each broker's post-PDT house margin rules.

## Provenance

- **Research + first architecture drafts (2026-07-02, Fable 5):** main run
  `wf_088f91dd-236` (106 agents, 24 sources, 120 claims, 25 verified) + gap-fill run
  `wf_44d4e594-090` (37 agents, 12/12 confirmed). Full per-claim evidence journals
  persist under
  `~/.claude/projects/C--Users-drfoo-github/02f3c12f-8ecf-4581-aa7e-bd4f9d48b38c/subagents/workflows/<run-id>/journal.jsonl`.
- **Ultracode fleshing (2026-07-03, Opus 4.8):** the build playbook, security
  architecture, prompt-injection defense, hardening backlog, and enhancements docs were
  authored + adversarially reviewed by a 17-agent Opus 4.8 workflow (run
  `wf_ae4b2c6b-c82`: 5 authors → per-doc fact-check + a 3-lens red-team on the two
  security docs → cross-doc completeness critic). They extend — never override — the
  verified research; the red-team output is preserved in full in
  [[security-hardening-backlog]].
