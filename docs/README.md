---
title: AI Investment Copilot — research, architecture & build index
type: index
status: active
last-verified: 2026-07-03
related: [research-report, research-supplement, cockpit-plan-original, cockpit-review-and-integrated-plan, draft-discord-control-plane, build-scaffold-ultracode, ultracode-build-playbook, security-architecture, prompt-injection-defense, security-hardening-backlog, enhancements-and-additions, upside-and-roi]
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
