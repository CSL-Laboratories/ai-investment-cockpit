---
title: Integration and remote-operations plan — conversations, AI providers, MCP isolation, accounts, and SSH
type: roadmap
status: draft
last-verified: 2026-08-06
related:
  - refactor-plan
  - capability-expansion-master-plan
  - draft-discord-control-plane
  - security-architecture
  - security-hardening-backlog
  - setup/accounts-and-keys
  - setup/low-priv-agent-account
---

# Integration and remote-operations plan

This plan turns five related requests into one safe expansion of the cockpit:

1. Keep Discord fully supported while making Telegram a possible second surface.
2. Support threaded and group conversations without treating group membership as trade authority.
3. Normalize Claude and OpenAI/Codex support, with OpenRouter and OpenCode as later research-only options.
4. Support multiple users, external accounts, agents, and MCP connections without sharing credentials or capabilities.
5. Add a custom CLI that can be used locally or through a tightly restricted SSH deployment.

This extends [[refactor-plan]] — especially RW-02 — and does not replace it. The paper-first gates and every existing invariant remain in force.

## 1. Decisions

### 1.1 Keep Discord primary; add Telegram after the surface boundary exists

Do not switch the application from Discord to Telegram first. Finish the existing Discord bring-up and RW-02 surface abstraction, then implement Telegram against the same interface.

- **Discord remains the default** for alerts, commands, approvals, and proposal threads.
- **Telegram becomes an optional adapter**, initially read-only and then approval-capable only after an adversarial review.
- **Group conversation support means private Discord guild channels/threads and Telegram supergroup topics.** Do not depend on Discord group DMs for the primary workflow.
- A canonical server-side conversation ID maps to a Discord thread or Telegram `message_thread_id`; neither platform is the source of truth.
- Telegram should use Bot API long polling, not a webhook, so it adds outbound traffic without opening a public inbound application port.

Discord already supports text-channel threads and forum channels; Telegram supports supergroup forum topics and thread IDs. The difference is an adapter concern, not a reason to choose one platform for the entire system.

### 1.2 “ChatGPT support” has two distinct meanings

Treat these as separate authentication and billing paths:

| Path | Meaning | Plan |
|---|---|---|
| **Codex CLI signed in with ChatGPT** | Subscription-backed Codex usage through `codex exec` | Supported for research/maintenance and the same schema-gated analysis/proposal roles as Claude in human-gated `read_only`/`paper` |
| **OpenAI API** | Usage-based API access through an API key and an API adapter | Optional future provider; not implied by a ChatGPT subscription |

Claude remains the only allowed decision-stage runtime in `review_live`/auto modes. Codex may be selected for human-gated `read_only`/`paper` analysis and proposal drafting, but stays outside risk and broker capabilities. A provider switch must not turn that distinction into a configurable live/auto bypass.

### 1.3 Multiple MCPs belong on an isolated research plane

The current runner rejects MCP flags for every cockpit-spawned agent. Relaxing that globally would violate the security architecture. Instead, add a second, explicit job class:

- **Human-gated decision jobs:** Claude- or Codex-selectable, schema-required, tool-starved, MCP-free; Codex uses ignored config/rules, ephemeral read-only execution, and explicit feature denials.
- **Live/auto decision jobs:** Claude-pinned in addition to the same schema/tool/MCP controls.
- **Research jobs:** provider-selectable, read-only by default, isolated by agent profile, and granted only an allowlisted MCP profile.
- **Broker MCP:** owned by `brokers/` and reachable only through the existing risk → mode → approval/veto → `OrderGateway` path. A model never receives broker credentials or direct order tools.

For context quality, prefer several narrow workers — for example filings, news, portfolio-read, and macro — over one agent with every MCP. A tool-starved synthesis step consumes their stored, scrubbed, schema-validated outputs.

### 1.4 Separate four kinds of “account”

Do not use one ambiguous account identifier. Model these separately:

1. **Principal** — a human or service identity inside the cockpit.
2. **Surface identity** — a Discord user ID, Telegram user ID, or CLI credential mapped to a principal.
3. **External account** — a provider/MCP/OAuth identity whose secrets are referenced by a credential profile.
4. **Broker account scope** — the portfolio/order boundary used by risk, audit, and the gateway.

Version 1 should support multiple people collaborating around **one broker account scope**, with one designated owner/approver and read-only analyst/observer roles. Separate users with separate portfolios should run isolated cockpit deployments until a later multi-tenant redesign scopes every mode, limit, risk decision, audit query, and gateway call by account.

### 1.5 Keep secrets in the OS vault; add references and isolation

Credential encryption already exists: `keyring` uses Windows Credential Manager/DPAPI. Do not add home-grown encryption around it.

Add non-secret `SecretRef` and `CredentialProfile` metadata to the database while values remain in the OS vault. Namespace entries by credential profile and field, rotate by version, and audit names/scopes only. Research workers that need MCP credentials run under dedicated low-privilege Windows accounts whose vaults contain only their own credentials.

### 1.6 SSH is an optional deployment profile, not a new public API

The custom CLI runs on the workstation and calls the loopback orchestrator. Remote usage is:

```text
operator -> SSH over a private overlay -> cockpitctl on workstation -> 127.0.0.1 API
```

The FastAPI service stays loopback-only. SSH is key-only, restricted to a dedicated standard Windows account, and limited by firewall to the private overlay. This changes the current “zero inbound ports” rule to “zero public/LAN application ports, with one optional overlay-only SSH ingress,” so the security architecture must be updated before deployment.

## 2. Current repository reality

| Area | Current state | Gap this plan closes |
|---|---|---|
| Discord | REST delivery works; `bridge/bot.py` declares commands and controls | Bot lifecycle is not started, command bodies are acknowledgements, and thread/group routing is not implemented |
| Surface abstraction | Workflows already emit logical channels | `bridge/` is still Discord-specific; RW-02 is not complete |
| Identity | Discord IDs are checked against one allowlist | No canonical principal, roles, Telegram/CLI identities, conversation membership, or account scope |
| Claude | `claude -p`, JSON output, resume, model aliases, schema validation | Runtime capabilities are implicit and provider-specific |
| OpenAI | `codex exec` JSONL parsing, provider-aware triage/deep aliases, structured-output hints, and human-gated paper parity exist | This is Codex/ChatGPT-plan support, not a generic OpenAI API adapter; the full adapter/capability probe remains |
| MCP | `tool_inspector.py` rejects MCP on spawned agents | No separately isolated research-agent/MCP plane |
| Secrets | One keyring service with redacted access | No credential profiles, ownership, scope metadata, or per-worker vault isolation |
| Low-privilege agents | ACL guard and transport contract exist | The production spawn-under-account launcher is not implemented |
| Remote control | Loopback FastAPI with one API key | No operator CLI, scoped credentials, SSH profile, or server-derived actor identity |

One important prerequisite for multi-user or SSH control: request bodies currently carry caller-supplied `actor` strings. Before exposing another surface, authenticated middleware must derive `principal_id` and actor metadata from the surface identity or scoped CLI token; clients must not be allowed to assert their own audit identity.

## 3. Target architecture

```text
Discord gateway       Telegram getUpdates       cockpitctl (local/SSH)
       |                       |                         |
       +-------------- SurfaceAdapter -----------------+
                               |
                      InteractionEnvelope
                  (surface, conversation, actor,
                   event id, reply/action payload)
                               |
                   Identity + RBAC + idempotency
                               |
                    application command services
                               |
                    loopback-only orchestrator API

research request -> AgentCoordinator
                     |-- worker: filings MCP profile
                     |-- worker: news MCP profile
                     |-- worker: portfolio-read profile
                     +-- tool-starved synthesis over stored outputs

broker MCP -> brokers/ -> risk -> mode -> approval/veto -> OrderGateway
                 (never attached to a model runtime)
```

### 3.1 Core contracts

Introduce these contracts before adding a second adapter:

- `OperatorSurface`: send/edit messages, open/resume a conversation, render controls, report delivery, and expose health.
- `InteractionEnvelope`: canonical inbound command/reply/callback with a globally unique idempotency key.
- `ConversationRef`: canonical ID plus surface workspace/chat/thread/topic identifiers and optional linked `AgentJob`.
- `PrincipalContext`: authenticated principal, roles, account scopes, surface identity, and authentication method.
- `ProviderAdapter`: capability probe, start/resume, structured output, usage, timeout/cancel, and health semantics.
- `AgentProfile`: provider, pinned model, job class, OS identity, sandbox, budget, and permitted MCP profile.
- `McpProfile`: explicit server/tool allowlist, credential profile references, read/write classification, and manifest hash.
- `CredentialProfile`: non-secret owner/provider/account/scope/expiry metadata plus `SecretRef` values.

### 3.2 Authorization roles

Start with a small deterministic role matrix:

| Role | Read state | Ask/research | Change notification prefs | Approve/reject | Change limits/mode | Kill switch |
|---|---:|---:|---:|---:|---:|---:|
| Observer | Yes | No | Own only | No | No | Yes |
| Analyst | Yes | Yes | Own only | No | No | Yes |
| Approver | Yes | Yes | Own only | Assigned account only | No | Yes |
| Owner | Yes | Yes | Yes | Assigned account only | Yes | Yes |
| Service agent | Profile-scoped | Profile-scoped | No | No | No | No |

The kill switch is authority-reducing and remains broadly reachable, but every invocation is authenticated, idempotent, and audited. Group membership, channel admin status, or possession of a message link never grants a cockpit role.

## 4. Work packages

Each package is one reviewable change with tests. Anything that changes approval identity, account scoping, broker connectivity, or remote state mutation requires an adversarial review.

### CXP-00 — Architecture and threat-model amendment

**Scope**

- Add the concepts in section 3 to [[security-architecture]] and the hardening backlog.
- Record that research agents may use MCP only through isolated profiles; decision agents remain MCP-free.
- Define the optional overlay-only SSH posture and its new attack surface.
- Define whether the first deployment is single-workspace/multi-principal (recommended) or true multi-tenant.

**Gate**

- Every new trust boundary maps to a control and a test.
- No wording weakens the order-path, schema, secrets, pure-risk, human-gate, audit, or fail-closed invariants.

### CXP-01 — Finish Discord bring-up and RW-02

**Scope**

- Complete [[refactor-plan]] RW-01 tail B1-B3: bot lifecycle, real `/status` and `/brief`, then privileged controls.
- Introduce `app/surfaces/base.py` and move Discord-specific code behind `app/surfaces/discord/` without changing behavior.
- Keep workflows dependent only on logical channels and the surface contract.

**Gate**

- Existing bridge tests pass through the new interface.
- A private-guild smoke test proves login, command sync, delivery, `/status`, `/brief`, and `/killswitch`.
- No Telegram code exists yet; this package proves the abstraction is real.

### CXP-02 — Canonical identity, conversations, and authorization

**Scope**

- Add `Principal`, `SurfaceIdentity`, `Conversation`, `ConversationBinding`, `ConversationParticipant`, and scoped credential/token metadata.
- Migrate the current Discord operator to the `owner` role without changing their authority.
- Replace surface-specific allowlist checks with one authorization service; keep a compatibility adapter while migrating callers.
- Derive actor identity server-side. Remove caller control over audit actor fields.
- Add a uniqueness constraint for normalized external event IDs so retries across adapters are idempotent.

**Gate**

- Two surface identities can map to one principal, but one external identity cannot map to two principals in the same workspace.
- An unauthorized group member cannot approve, change limits/mode, read an account outside scope, or spoof an actor.
- Duplicate button/callback/CLI requests produce one state transition and one audit result.
- Adversarial review covers forged identities, stale roles, replay, confused deputy, and cross-account reads.

### CXP-03 — Discord compatibility and group/thread hardening

**Scope**

- Support private guild text threads and forum posts through `ConversationBinding`.
- Route thread replies to the linked job/session; persist enough state to resume after restart.
- Re-check authorization on every command and component action, not only when the thread is created.
- Handle archived/locked/deleted threads, missing permissions, retries, and bot reconnects explicitly.
- Permit group research conversations while keeping account-sensitive output redacted by default; use private/ephemeral responses for sensitive actions.

**Gate**

- Contract tests cover channel, thread, forum post, DM, multiple participants, archive/reopen, missing permission, reconnect, and retry cases.
- A proposal action remains single-use even when the same proposal is visible in more than one Discord location.
- Discord remains fully functional before Telegram work begins.

### CXP-04 — Telegram adapter and parity rollout

**Scope**

- Add `app/surfaces/telegram/` using Bot API long polling.
- Map `chat_id` plus optional `message_thread_id` to canonical conversations.
- Support supergroups, forum topics, commands, inline keyboards/callback queries, edits, and delivery acknowledgements.
- Ship read-only commands first; keep approve/reject/modify disabled until CXP-02/03 authorization and idempotency gates are green.
- Add a surface preference/failover policy that never interprets delivery failure as consent.

**Gate**

- The same read-only command fixture produces equivalent canonical results in Discord and Telegram.
- Duplicate updates, edited messages, topic closure, bot removal, privacy restrictions, and delivery outages fail closed.
- Approval parity gets a separate adversarial round before enablement.
- Discord remains the default until Telegram completes a real private-group soak with no authorization or routing discrepancies.

### CXP-05 — AI provider and runtime conformance

**Status (2026-08-07): CXP-05a complete.** `COCKPIT_DECISION_RUNTIME=claude|codex`
selects all briefing/triage/grounding/proposal/fresh-interrogation roles. Codex is
hardened and argv-inspected, both providers share the validation/repair/quarantine
pipeline, paper execution requires the currently selected runtime, and auto arming
still rejects Codex. The `ProviderAdapter` refactor and startup capability/version
record below remain as CXP-05b.

**Scope**

- Refactor the two branches in `agent_runner/runner.py` behind `ProviderAdapter` implementations for Claude CLI and Codex CLI.
- Separate `provider`, `runtime`, `auth_mode`, `model_alias`, and exact `model_pin` in configuration/audit lineage.
- Add startup feature probes for non-interactive mode, structured output, session resume, sandbox/tool controls, timeouts, and usage metadata.
- Preserve one shared schema-validate → repair once → quarantine pipeline above the adapters.
- Keep live/auto enforcement expressed as a role policy: those profiles must resolve to the approved Claude provider and be tool/MCP-starved. Human-gated `read_only`/`paper` profiles may resolve to Codex only through its hardened capability-denial profile.

**Gate**

- A provider conformance suite runs the same fake start/resume/timeout/malformed/limit fixtures against both adapters.
- A live smoke records exact CLI version, auth mode, model pin, and supported capabilities without exposing credentials.
- ChatGPT-plan Codex and API-key OpenAI are reported as different auth/billing modes.
- No provider configuration can make a non-Claude profile eligible for live/auto or put any model directly on the broker/risk/order path.

### CXP-06 — Future provider spikes: OpenAI API, OpenRouter, and OpenCode

These are research spikes, not production dependencies.

**OpenAI API**

- Evaluate a Responses API adapter only for research jobs that need an API-native runtime.
- Treat API-key billing, data controls, tool approvals, and MCP authorization as distinct from ChatGPT/Codex subscription usage.

**OpenRouter**

- Prove OpenAI-compatible request/response handling with exact model and upstream-provider pins.
- Disable automatic model/provider fallback for audited comparisons unless the resolved route is captured in lineage.
- Store its API key in a credential profile, never an environment file.

**OpenCode**

- Treat OpenCode as an alternate agent runtime/orchestrator, not as a model provider.
- Evaluate its provider, agent, and MCP profiles only inside a research worker.
- Do not adopt it until credential storage, non-interactive structured output, session identity, sandboxing, tool denial, and secret-injection behavior meet the same conformance gates.

**Gate**

- Each spike can run one schema-gated, read-only research fixture and one failure fixture.
- Removing the spike leaves production Claude/Codex behavior unchanged.
- No spike can arm auto mode, access a broker write tool, or weaken model lineage.

### CXP-07 — Credential profiles and external-account registry

**Scope**

- Add a vault interface whose production backend is Windows Credential Manager and whose tests use an in-memory backend.
- Use stable names such as `credential:<profile_id>:<field>:<version>` under the existing service; store only references and metadata in SQL.
- Track owner principal, provider, external account label, granted scopes, read/write classification, expiry, rotation version, and revocation state.
- Configure Codex CLI and MCP OAuth stores to use the OS keyring where supported.
- Add rotation/revocation commands that accept secret values through a hidden prompt/stdin, never argv.

**Gate**

- Secret values never appear in SQL, config, argv, prompts, chat, logs, exceptions, or audit payloads.
- A worker can resolve only credential profiles assigned to its `AgentProfile` and OS identity.
- Rotation invalidates the old version, reconnects the owning adapter, and records names/scopes only.
- A revoked or expired credential fails closed and cannot silently fall back to another user's account.

### CXP-08 — Isolated multi-agent/MCP research coordinator

**Scope**

- Add `ResearchCoordinator` to fan a request out to explicit `AgentProfile`s.
- Launch each MCP-bearing worker under a dedicated low-privilege Windows account/profile home with its own keyring and MCP configuration.
- Finish the production spawn-under-account transport before enabling workers.
- Build each worker's MCP config from an allowlisted manifest; inspect the actual launch configuration and record its hash.
- Store raw tool results separately, treat them as untrusted input, scrub/minimize them, schema-validate worker outputs, then synthesize with a tool-starved model.
- Version 1 permits read-only MCP tools only. Any non-broker write tool needs per-call approval and a later package.

**Gate**

- A worker cannot discover or call a server/tool outside its MCP profile.
- A worker for principal/account A cannot read B's credential, session store, raw result, or conversation.
- Compromise of one worker does not reveal orchestrator, broker, Discord/Telegram, audit-HMAC, or another worker's credentials.
- MCP timeout, prompt injection, malformed output, or partial fan-out is explicit; required-context failure degrades the dependent workflow rather than inventing context.
- Existing `tool_inspector` tests continue to prove that decision-stage agents have zero MCP access.

### CXP-09 — Multi-user and multi-account operating modes

**Stage A: one workspace, one broker account scope (recommended first)**

- Multiple principals may participate in group research and receive notifications.
- Only assigned approvers/owner can adjudicate; only the owner can loosen limits or raise mode.
- Account-sensitive details are redacted in groups and available only to authorized private conversations.

**Stage B: multiple independent broker account scopes**

- Until a full redesign exists, deploy one isolated service/DB/keyring namespace/Windows account per broker account.
- True same-process multi-tenancy is a separate order-path project: scope mode, limits, breakers, audit anchors, proposals, approvals, orders, snapshots, gateway instances, and queries by immutable `account_scope_id`.
- It receives its own adversarial review and paper soak; do not infer account scope from the active chat.

**Gate**

- A deterministic cross-account test corpus proves no data, credential, approval, proposal, limit, or order can cross scopes.
- An omitted/unknown account scope is rejected, never mapped to a default funded account.

### CXP-10 — `cockpitctl` custom CLI

**Scope**

- Add an installable `cockpitctl` entry point that talks only to the authenticated loopback API.
- Start with `status`, `brief`, `jobs`, `conversations`, `providers`, `credentials list/status`, and `killswitch`.
- Add `mode`, `limits`, proposal decisions, and credential rotation only after scoped auth and action challenges exist.
- Support human-readable and `--json` output with stable exit codes.
- Read prompts, confirmations, and secrets from stdin/hidden input; never put them in argv.
- Derive the audit principal from a scoped local credential, not `--actor` input.

**Gate**

- CLI/API contract tests cover auth failure, insufficient role, stale challenge, retry/idempotency, timeout, and server outage.
- Authority-increasing commands require interactive confirmation and cannot be executed by an observer/analyst token.
- `killswitch` remains available to every authenticated human role and works while model providers are down.

### CXP-11 — Restricted SSH deployment profile

**Scope**

- Install/enable the Microsoft-supported Windows OpenSSH Server only when remote CLI access is requested.
- Use a dedicated non-admin `cockpit-remote` account, public-key authentication, an explicit user allowlist, tight NTFS ACLs, and no broker/model/MCP credentials.
- Restrict the account to `cockpitctl` using the controls supported and verified by the installed Windows OpenSSH build; if command restriction cannot be proven, do not ship the profile.
- Limit the firewall rule to the private overlay/interface and approved client addresses. Do not expose SSH or FastAPI directly to the public internet.
- Store only a role-scoped CLI credential in that Windows account's Credential Manager.
- Initially permit read-only commands and `killswitch`; enable other state changes only after an SSH-specific adversarial review.

**Gate**

- Password login, agent forwarding, port forwarding, arbitrary shell/process launch, and access outside the CLI profile are denied or explicitly justified and tested.
- A stolen SSH/CLI credential cannot read broker/AI/MCP secrets and cannot exceed its cockpit role.
- Remote requests carry server-derived principal/authentication/surface metadata into the append-only audit.
- The API is still bound to `127.0.0.1` and is unreachable directly from another host.

### CXP-12 — Soak, incident drills, and default selection

**Scope**

- Run Discord and Telegram read-only parity fixtures, then a private live-message soak.
- Run provider conformance on every pinned CLI upgrade.
- Drill credential rotation/revocation, surface outage, MCP compromise, lost SSH key, and cross-account authorization failure.
- Measure delivery success, duplicate suppression, conversation misrouting, auth denials, provider failure, and recovery time.

**Gate**

- Discord remains the default unless Telegram is measurably more reliable for the operator and has equal authorization/idempotency coverage.
- No rollout changes the paper/live mode or counts toward a go-live performance gate.
- All new failure modes reduce authority or availability; none increases trading authority.

## 5. Sequencing

```text
existing RW-01 B1-B3
        |
        v
CXP-00 threat model -> CXP-01 Discord + RW-02 -> CXP-02 identity/conversations
                                                    |             |
                                                    v             v
                                      CXP-03 Discord groups   CXP-05 provider contract
                                                    |             |
                                                    v             v
                                      CXP-04 Telegram       CXP-06 future spikes

CXP-02 -> CXP-07 credential profiles -> CXP-08 multi-agent/MCP research
    |
    +----> CXP-09 multi-user Stage A
    |
    +----> CXP-10 cockpitctl -> CXP-11 SSH

all paths -> CXP-12 soak and drills

multi-account Stage B stays last and separately adversarially reviewed
```

Recommended priority:

1. **P0:** CXP-00 through CXP-03, CXP-05, and CXP-07.
2. **P1:** CXP-04, CXP-08, CXP-09 Stage A, and CXP-10.
3. **P2 / optional:** CXP-06, CXP-11, and CXP-09 Stage B.

## 6. Compatibility matrices that become permanent tests

### Surface matrix

Every surface adapter must prove:

- delivery success/failure is truthful;
- command and reply routing use canonical conversations;
- threads/topics resume after restart;
- external event retries are idempotent;
- group participants are authenticated individually;
- sensitive output follows account scope and redaction policy;
- approval expiry, single use, and quote freshness are unchanged;
- outage/silence never becomes consent.

### Provider matrix

Every provider adapter must declare and prove:

- auth mode and credential store;
- exact runtime/model version capture;
- non-interactive stdin input;
- structured output parsing;
- schema validation and one repair attempt;
- session start/resume behavior;
- timeout, cancel, usage-limit, and malformed-output handling;
- sandbox/tool/MCP capability enforcement;
- whether the profile is eligible for decision, research, or maintenance roles.

### Credential/MCP matrix

Every MCP profile must record:

- server identity and pinned endpoint/command;
- tool allowlist and read/write classification;
- credential profile owner and scopes;
- approval policy;
- OS identity and sandbox;
- context/output size limits;
- manifest hash and review date;
- failure behavior and revocation procedure.

## 7. Verified platform facts (2026-08-06)

- Discord threads are sub-channels with explicit thread permissions; forum channels contain threads: <https://docs.discord.com/developers/topics/threads>.
- Telegram's Bot API exposes long polling through `getUpdates`, and forum topics use `message_thread_id`: <https://core.telegram.org/bots/api> and <https://core.telegram.org/api/forum>.
- Claude Code supports non-interactive print mode, JSON output, session resume, tool controls, and MCP configuration: <https://docs.anthropic.com/en/docs/claude-code/cli-usage>.
- Codex supports ChatGPT subscription sign-in and separate API-key usage-based sign-in; its CLI and MCP credentials can use the OS keyring: <https://learn.chatgpt.com/docs/auth> and <https://learn.chatgpt.com/docs/config-file/config-reference>.
- OpenRouter exposes an OpenAI-compatible API, but route/model lineage still needs cockpit-side pinning: <https://openrouter.ai/docs/quickstart>.
- OpenCode supports multiple providers, agents, and MCP servers; it remains a future research-runtime spike until it passes this plan's credential and sandbox gates: <https://opencode.ai/docs/providers>, <https://opencode.ai/docs/agents/>, and <https://opencode.ai/v2/docs/mcp-servers>.
- Windows OpenSSH supports key-based authentication and user allowlists; enabling it opens an SSH firewall surface that must be restricted: <https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement> and <https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration>.

## 8. Explicit non-goals

- No live-capital unlock, auto-mode shortcut, or order-path provider swap.
- No direct model access to Robinhood or any broker write tool.
- No “all MCPs for all agents” configuration.
- No secrets in environment files, command-line arguments, prompts, chats, SQL, or logs.
- No public FastAPI endpoint and no public SSH deployment.
- No same-process multi-tenant trading until account scoping has its own design, tests, adversarial review, and paper soak.
- No Telegram cutover merely because its topics are convenient; reliability and safety evidence decide the default.
