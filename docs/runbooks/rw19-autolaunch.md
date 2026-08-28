---
title: RW-19 unattended capture autolaunch
type: runbook
status: live
last-verified: 2026-08-28
---

# RW-19 unattended capture autolaunch

Per-window, one-shot scheduling for the plan-bound news-reconnect collector. Automates
**launch timing only**: every capture still requires a sealed prospective plan pinned to
clean `main == origin/main`, a fresh operator authorization for that specific plan hash,
and the collector's own runtime gates. Nothing here grants strategy, mode, order, or
autonomous authority.

## Pieces

- `tools/rw19_preflight.py` — strictly read-only, fail-closed generator for the 24-field
  safety preflight. Every field is derived live (never defaulted); any failed or opaque
  probe refuses with no output file; the result is self-validated through the collector's
  own `_load_preflight`. Requires `COCKPIT_ENV_FILE` to name an existing `.env` and
  `COCKPIT_HALT_PATH` to be pinned in its environment (the launcher does both).
- `tools/rw19_autolaunch.py --config <cfg.json> [--rehearsal]` — one-shot, no-retry
  launcher. Live sequence: bootstrap (absolute paths, plan hash, plan↔config output-dir
  binding, tools-inside-repo, cross-process window lock, evidence dirs absent) → hold to
  T−2 → bounded `git fetch` + `HEAD == origin/main == plan.source_commit` on a clean tree
  → fresh preflight → hold to T0 (refusing past `scheduled_start + late_cutoff_seconds`
  at every stage) → exactly one `collect` → outcome machine: exit 0 → `verify`; exit 3 →
  `verify-aborted`; exit 2 → pre-start refusal; anything else, any directory mismatch, or
  `.recovery.*` / `.rw19-news-reconnect.*` residue → `INTEGRITY_ERROR`, manual inspection,
  never a retry. A timestamped step log lands in `log_dir` (operational, not evidence).
  `--rehearsal` runs bootstrap + source verify + a real collector-validated preflight into
  `scratch_dir` and exits; it is structurally unable to build a `collect` command.
- `tools/rw19_schedule_task.ps1` — `-Mode Register|Inspect|Census|Unregister`. Register
  creates a one-time task (InteractiveToken, WakeToRun, `MultipleInstances IgnoreNew`,
  trigger EndBoundary, bounded ExecutionTimeLimit, `DeleteExpiredTaskAfter`,
  `StartWhenAvailable` deliberately off) and immediately self-inspects the exported XML.
  Census lists every scheduled task whose action references given paths (repo, DB,
  evidence root) — run it before arming.

## Config (all paths absolute)

`plan_path`, `expected_plan_sha256`, `python_exe` (the repo `.venv` interpreter),
`repo_root` (the main checkout), `preflight_tool`, `collector_tool` (both inside
`repo_root`), `output_dir` / `aborted_dir` (must equal the plan's `output_paths`),
`scratch_dir`, `log_dir` (external, outside the write-once evidence root), `env_file`
(the cockpit `.env`), `halt_path` (the pinned HALT sentinel location), `scheduled_start`
(RFC3339 UTC), `late_cutoff_seconds` (default 240).

## Per-window procedure

1. Seal a prospective plan pinned to current clean `main == origin/main`; record its
   SHA-256; obtain the operator's authorization **for that hash**.
2. Write the config JSON (external ops dir); fast-forward and freeze the main checkout.
3. Rehearse: register a rehearsal task firing ~20–30 min out; review its log end to end
   (proves scheduler → wake → launcher → git → preflight on this machine).
4. Census scheduled tasks; verify power posture (AC, wake timers, user logged on,
   `w32tm` in sync).
5. Register the live task at T−15 with the same config; inspect the XML; delete the task
   after the window.
6. Freeze: no repo/DB/evidence writes from any session between task start and terminal
   verification (a mid-capture edit fails the collector's post-capture source check).
7. Triage from the launch log after the collector's wall cap has elapsed; only then
   write evidence/docs, on a fresh branch.

## Standing limits

- One task per authorized window; never a recurring trigger.
- The launcher never touches credentials (the collector loads its own from keyring) and
  never writes inside the repo or the evidence root (the collector owns those).
- A missed window is a refusal, not a retry: author a new plan for the next window.
