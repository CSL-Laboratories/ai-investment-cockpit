---
title: Moving-targets refresh — 2026-07-06
type: verification-note
status: reference
last-verified: 2026-07-06
related: [research-report, research-supplement, security-architecture, upside-and-roi, accounts-and-keys]
---

# Moving-targets refresh — 2026-07-06

**Headline: Nothing material changed since the 2026-07-02 snapshot.** Both externally-verified targets (Anthropic Agent SDK credit, Robinhood Agentic scope) hold in primary sources as of 2026-07-06; the build-economics assumptions in [[research-report]] §3/§5 and [[upside-and-roi]] stand as written. The third target (broker post-PDT house rules) was not re-run this cycle — it is a slow-moving, broker-specific item with no expected drift, flagged below for completeness.

## Status vs. 2026-07-02

| Target | Status | As-of | Confidence | Key source |
|---|---|---|---|---|
| Anthropic Agent SDK credit (un-pause) | **Unchanged** — still paused | 2026-07-06 | High | [support.claude.com art. 15036540](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) — June 15 pause banner still verbatim |
| Robinhood Agentic beta scope | **Unchanged** — beta, equities + long options live | 2026-07-06 | High | [Trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/); [Agentic overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) |
| Broker post-PDT house margin rules | **Not re-verified this cycle** — no drift expected | 2026-07-02 (last check) | n/a (not re-run) | per-broker margin disclosures (re-check at build) |

### 1. Anthropic Agent SDK credit — unchanged (paused)
The June 15 pause still holds. Article 15036540 carries the banner verbatim ("For now, nothing has changed: Claude Agent SDK, `claude -p`, and third-party app usage still draw from your subscription's usage limits") and shows no revision newer than mid-June. The announced separate Agent-SDK credit ($20 Pro / $100 Max-5x / $200 Max-20x at API rates) is **not live** and has not been un-paused, re-scoped, or replaced. Headless subscription use (`claude -p` / Agent SDK / third-party apps) still burns normal plan limits, and subscription-authed headless use remains an explicitly sanctioned category. Anthropic says it is reworking the plan and will give advance notice before any change — **policy remains officially in flux**, but no move occurred between 2026-07-02 and 2026-07-06.

- **Economics implication:** No change. The copilot's headless/agentic Claude workloads still run off a flat Max-20x ($200/mo) subscription against normal interactive limits at no incremental per-token cost — no metered credit pool has kicked in. Plan around the shared rate-limit ceiling (headless jobs compete with interactive use for the same allowance), *not* a dollar-denominated $200 credit. Treat this as a **soft/temporary favorable state**: keep an API-pay-as-you-go fallback in the budget model, since Anthropic has promised advance notice before re-instituting the split.

### 2. Robinhood Agentic scope — unchanged
All load-bearing snapshot claims still verify. Still **beta / gated rollout** (not GA, "we'll email you once you have access"). Asset scope: **equities + long options are live** ("more assets soon"); crypto/futures/event contracts not yet available — consistent with the snapshot's 0-3 refutation of "equities-only." Crypto Agentic Trading was announced [2026-07-01 newsroom](https://robinhood.com/us/en/newsroom/robinhood-accelerates-global-expansion-robinhood-chain-mainnet-stock-tokens-agentic-trading/) as "rolling out soon" — but that date **predates** the 2026-07-02 snapshot, so it is not a change since then, and crypto is still not buildable-against. Containment model unchanged: the agent **reads all accounts** but **writes only to the sandboxed Agentic sub-account** (endpoint still `https://agent.robinhood.com/mcp/trading`). Platform-side per-trade approval is still **not enforced** ("it can place trades without your confirmation"). Still no published rate limits and no OAuth session/token-lifetime specs for long-running headless agents.

- **Economics implication:** No change to the plan. Equities + long options are exploitable today via MCP; read-across-all-accounts with write-contained-to-sub-account gives good blast-radius control for a solo dev. Because platform per-trade approval is **not** enforced, the copilot must keep its own hard-coded guardrails (pre-trade approval gate, position caps, per-trade budget, kill-switch) — Robinhood will not stop a runaway loop (aligns with [[research-report]] §2). Unresolved headless risk: no documented rate limits or session TTLs, and connect is desktop-OAuth — budget a re-authentication / human-in-the-loop refresh path for unattended 24/7 operation. **Do not architect around crypto Agentic Accounts** until access actually lands.

### 3. Broker post-PDT house margin rules — not re-verified
This target was not re-run in this cycle. It is broker-specific and structurally slow-moving (house margin policy, not a fast product rollout), so no material drift is expected — but it remains an explicit build-time re-check, not a confirmed-unchanged item.

## What to do
**Proceed on the 2026-07-02 assumptions unchanged** — no re-plan is warranted. Keep building against: flat Max-20x subscription auth for headless Claude workloads (with an API fallback line kept in the budget), equities + long-options Robinhood MCP with app-side approval/kill-switch guardrails, and no crypto dependency. Carry forward the two standing unknowns as *design constraints, not blockers*: (a) the Anthropic credit split may return on notice, and (b) Robinhood publishes no rate limits or session TTLs, so assume periodic re-auth. Fold the un-re-verified broker post-PDT rule into the pre-Phase-3 checklist.

## Reminder
**Re-verify all three targets again immediately before Phase 3 (live trading).** All three are explicitly moving targets ([[research-report]] §5, [[security-architecture]] "Re-verify at build time"); the Anthropic policy is officially in flux and Robinhood scope is expanding (crypto imminent). Do not commit long-run cost or execution-scope assumptions to a live deployment without a fresh primary-source pass at go-live.