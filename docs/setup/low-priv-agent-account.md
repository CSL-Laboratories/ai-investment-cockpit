---
title: Low-privilege decision-agent account (SEC-05 / OPS-06)
type: setup
status: active
last-verified: 2026-07-09
related: [accounts-and-keys, security-hardening-backlog, security-architecture, production-work-packages]
---

# Low-privilege decision-agent account

> **Why.** Decision-stage agents (`triage` / `deep`) run as `claude -p` or hardened `codex exec`
> subprocesses. If they run
> under the same Windows account as the orchestrator, an injected/compromised decision agent can
> read `~/.codex/auth.json`, the Claude CLI session store, and the keyring — and a harvested bearer
> places orders with **zero** contact with this app (backlog SEC-05, L201-216). The order path's
> capability boundary (P-B) is only as strong as the credential boundary beneath it.

The runtime half of this control is enforced by `app/agent_runner/account_guard.py`
(`assert_credential_starved`), wired into `main.compose()`: at startup it **fails closed** unless
the configured decision-agent account provably cannot read the bearer stores. This document is the
**host-provisioning half** (OPS-06): create the account and set the ACLs so that check passes.

> **Split of ownership (production-work-packages §3.1).** W11 owns the *runtime ACL assertion*
> (the agent process cannot read the stores); W15/OPS-06 owns the broader *host provisioning*
> (BitLocker, NSSM service, full host hardening). This doc is the SEC-05 slice both reference.

## 1. Create the separate account

A standard (NON-admin) local account, e.g. `cockpit-agent`:

```powershell
# Run as an administrator.
$pw = Read-Host -AsSecureString "Password for cockpit-agent"
New-LocalUser -Name "cockpit-agent" -Password $pw -FullName "Cockpit decision agent" `
  -Description "Low-priv account for claude -p decision subprocesses (SEC-05)"
# Standard user only — do NOT add to Administrators.
```

Record the account name as **non-secret** config (never keyring): `COCKPIT_DECISION_AGENT_ACCOUNT=cockpit-agent`.

## 2. Deny read on the bearer stores (per-file NTFS ACLs)

Deny `cockpit-agent` read on each store the guard checks (`account_guard.default_store_paths()`).
An explicit **deny** ACE wins over any inherited allow:

```powershell
$agent = "cockpit-agent"
$stores = @(
  "$env:USERPROFILE\.codex\auth.json",     # Codex OAuth bearer
  "$env:USERPROFILE\.claude",              # Claude CLI session store (dir)
  "$env:USERPROFILE\.claude.json"          # Claude CLI config / session
)
foreach ($p in $stores) {
  if (Test-Path $p) { icacls $p /deny "${agent}:(R)" | Out-Null }
}
```

The Windows Credential Manager vault (where `keyring` stores `audit-hmac-key`, `discord-bot-token`,
`alpaca_*`, `finnhub_key`) is **per-user** — `cockpit-agent`'s vault is already separate from the
orchestrator account's vault, so the agent cannot read the orchestrator's secrets. Do **not** run
`keyring set` for the cockpit secrets while logged in as `cockpit-agent`.

## 3. Spawn decision subprocesses UNDER the account (not just ACL the files)

> **Load-bearing (G11 round-1 finding).** ACL-denying the stores is theater unless the decision
> subprocess actually runs under `cockpit-agent` — a child spawned with the orchestrator's token
> inherits its (privileged) read access regardless of the file ACLs. The spawn identity is the
> control; the ACLs only bite once the child's token is the low-priv account's.

The runner enforces this fail-closed: when `COCKPIT_DECISION_AGENT_ACCOUNT` is set,
`main.build_decision_transport()` wires `LowPrivSubprocessTransport(account)`, and
`agent_runner/runner.py::run_agent` REFUSES to spawn any decision-stage job unless the transport
launches the child under that exact account (`transport.account == decision_agent_account`) — a
plain `SubprocessTransport` (orchestrator token) is rejected with `CredentialGuardError`. So a
mis-wired or forgotten low-priv transport fails closed rather than running privileged.

Provide the production launcher (`agent_runner/transport.py::_logon_launch`, a
`CreateProcessWithLogonW` spawn using `cockpit-agent`'s credential) and pin the sandbox cwd:

```
COCKPIT_DECISION_AGENT_CWD=C:\cockpit\agent-sandbox
```

`run_agent` also asserts every decision-stage job's cwd matches this pinned dir
(`tool_inspector.assert_decision_cwd`) and that its actual argv is tool/MCP-starved
(`tool_inspector.inspect_argv`) before spawning — deny-by-default (SEC-06). Until the launcher is
provisioned, `_logon_launch` raises (fail closed): a configured account with no launcher cannot run
decision jobs at all — it never silently falls back to the privileged token.

## 4. Verify the guard passes

With the account + ACLs provisioned and `COCKPIT_DECISION_AGENT_ACCOUNT` set, `main.compose()`'s
startup check returns `enforced=True`. If any store is still readable by `cockpit-agent`, compose
**fails closed** with a `CredentialGuardError` naming the store — re-apply the deny ACE for that
path. Until the account is configured the control is reported **inactive** (`enforced=False`); it
is a **pre-live blocker**, not satisfied by a green build (path-to-production §2, SEC-05 row).
