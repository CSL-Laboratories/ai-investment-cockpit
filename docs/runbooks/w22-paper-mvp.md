---
title: W-22 paper MVP bring-up and evidence runbook
type: runbook
status: live
last-verified: 2026-08-14
related:
  - PLAN
  - REGISTER
  - rw01-live-smoke
  - setup/accounts-and-keys
---

# W-22 paper MVP bring-up

This runbook brings the composed background service up for Alpaca paper testing. It does not
authorize live capital or any autonomous mode.

The first dated bring-up record is [[../evidence/w22-paper-mvp-2026-08-14]]. It demonstrates
healthy read-only quote/feed/audit operation and records the still-open evidence gates.

## Hard hold: what cannot pass tomorrow

The operator selected the constrained cash-merger strategy for `DEC-03`, and `W-23` is being
implemented on its dedicated branch. Running authority has not changed: no empirical cohort or
eligible target is provisioned, the separate strategy watchlist remains empty, and the package
still needs adversarial review plus cross-platform CI. Consequently, a newly drafted opening
proposal fails the W-21 `net_edge_available` check and does not mint an executable `Order`. Do
not seed the production database, edit a risk row, or bypass `OrderGateway` to manufacture an
end-to-end trade. Follow [[w23-cash-merger-edge]]; until every evidence and activation gate
there passes, this hard hold remains unchanged.

W-22 also requires two unattended market days at least a week apart. One market day tomorrow
can collect the first evidence set; it cannot close the MVP gate by itself. The safe launch
posture tomorrow is therefore paper infrastructure + awareness + Discord interrogation, with
opening execution still fail-closed.

## Evening-before checklist

1. Restore a bounded GitHub Actions budget and require the Windows/Ubuntu/macOS matrix on the
   RW-15 and W-22 PRs. The prior zero-step billing failures are missing CI attestation.
2. Follow [[rw01-live-smoke]] for keyring and feed provisioning. Alpaca credentials must be
   **paper** credentials. On Ubuntu, provision a real SecretService/keyring backend first.
3. Configure the private Discord guild and logical channels:

   ```powershell
   $env:COCKPIT_OPERATOR_IDS = "<your Discord user id>"
   $env:COCKPIT_GUILD_ID = "<your private guild id>"
   $env:COCKPIT_BRIEF_CHANNEL_ID = "<brief channel id>"
   $env:COCKPIT_ALERTS_CHANNEL_ID = "<alerts channel id>"
   $env:COCKPIT_PROPOSALS_CHANNEL_ID = "<proposals channel id>"
   $env:COCKPIT_SYSTEM_CHANNEL_ID = "<system channel id>"
   ```

4. Run the offline gates from `app/` with `TMP` and `TEMP` on a short Windows path. Confirm
   `ruff check .` and the full suite are green, then run `tools/verify_keys.py` and
   `tools/probe_credentials.py --post` as described in the RW-01 runbook.
5. Start the service before 08:30 ET and leave it running. Startup hydrates completed daily bars
   before serving, and the 07:30 ET cron refreshes them on an uninterrupted process. The first
   one-minute performance job captures a signed Alpaca equity baseline; each cadence reconciles
   orders and then resamples. Require `/quant/status` to list every configured symbol under both
   `market_data.quoted` and `market_data.metrics`; an empty metrics set is not paper-ready.

### Keep the soak revision isolated from development

A qualifying soak is pinned to a clean commit. Continuing development in the same worktree
would make that evidence ambiguous, so run the service from a detached clean worktree while
keeping the runtime working directory at the operational `app/` directory that owns
`cockpit.db` and `data/raw`:

```powershell
$repo = (git rev-parse --show-toplevel).Trim()
$soak = "${repo}-soak"
$commit = (git -C $repo rev-parse HEAD).Trim()
git -C $repo worktree add --detach $soak $commit
if (git -C $soak status --porcelain) { throw "soak worktree is dirty" }

# The source worktree has no operator-local .env by design. Point it at the guarded,
# git-ignored non-secret configuration beside the existing runtime database.
$env:COCKPIT_ENV_FILE = "$repo\app\.env"
$process = Start-Process `
  -FilePath "$repo\.venv\Scripts\python.exe" `
  -ArgumentList "$soak\app\main.py" `
  -WorkingDirectory "$repo\app" `
  -WindowStyle Hidden `
  -PassThru
$process.Id
```

Record `$commit`, the process ID, both paths, and the start time in the dated evidence. The
detached worktree supplies immutable source while the operational working directory preserves
the existing SQLite/audit and raw-evidence history. Only one cockpit process may use that
runtime directory and API port. Re-deploy a new commit by stopping the recorded process,
updating or recreating the detached worktree, re-running the gates, and starting it again; do
not mutate the soak worktree in place.

## First live checks

Create the authenticated header and inspect status:

```powershell
$h = @{ "X-API-Key" = (keyring get ai-investment-cockpit cockpit_api_key).Trim() }
$s = Invoke-RestMethod -Uri http://127.0.0.1:8787/status -Headers $h
$s | ConvertTo-Json -Depth 8
```

Require all of the following before treating the process as healthy:

- `audit_chain.ok` is `true`;
- `paper_performance.ok` is `true`, with non-null `day_open_as_of`, `day_open_equity`,
  `current_equity`, `equity_drawdown_today`, and `consecutive_losses`;
- `watch_only` is `false`, or every active watch-only reason is understood and resolved;
- market data reports no fault and live `quant_book_seq` values progress;
- Discord receives the brief/proposal/system test posts;
- `paper_fill_notifications.undelivered` does not increase.

Missing equity, incomplete signed fill evidence, audit failure, stale market data, or an active
loss breaker disables the order path. Clearing a breaker is an explicit operator action after
the cause is understood; a restart is not a clear.

## Discord loop

The live command bot is guild-scoped when `COCKPIT_GUILD_ID` is set. Use:

- `/ask proposal_id:<id> question:<question>` for a schema-validated fresh-context answer;
- `/paper action:preview order_id:<server order id>` to inspect the stored tuple;
- `/paper action:approve order_id:<id> confirm_symbol:<EXACT SYMBOL>` to authorize;
- `/paper action:veto order_id:<id>` (or `reject`) to terminally reject it.

The `order_id` comes from the server-rendered proposal message, never model prose. Discord uses
the same `PaperExecutionService` and `OrderGateway` as the loopback API. A fill or partial-fill
notification is posted only after the audited order transition commits, and its delivery result
is itself recorded as `paper_fill_notification`.

Until the deterministic source has verified empirical/deal evidence and separate strategy
allowlisting, `/paper approve` should have no eligible new opening order. That refusal is the
expected W-21/W-23 safety result, not a launch failure.

## Evidence to record

For each qualifying market day, record the commit, platform, Python version, start/stop times,
status snapshots, feed/entitlement results, Discord message links, Alpaca paper broker IDs,
resting/partial/terminal reconciliation, lost-response drill result, breaker drills, and final
`audit_chain.head_seq` with `audit_chain.ok: true`.

The W-22 gate closes only after both unattended days (at least a week apart) have green chains,
no manual database repair, no unexplained order/reconciliation gap, no silent notification
failure, and all RW-14/RW-15 online prerequisites recorded. RW-13 remains blocked until then.
