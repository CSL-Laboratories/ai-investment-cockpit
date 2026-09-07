# Astra fresh-install smokes — 2026-09-06

**RESULT: LOCAL INSTALL / OFFLINE SMOKES PASS. LIVE READINESS BLOCKED / UNVERIFIED.**

Executed on 2026-09-07 UTC (September 6 in Pacific time); the requested filename retains the operator's September 6 date. This is a bounded install and smoke record, not a selection, paper-day, deployment, or live-capital attestation.

The existing project venv was repaired, and a separate genuinely fresh venv was created at `/workspace/ai-investment-cockpit/.venv/astra-fresh-install-2026-09-06`. The fresh CI dependency profile passed **184 targeted tests, zero skips, in 64.57 seconds**, Ruff, dependency imports, and `pip check`. Adding the runtime Discord/CryptFile packages also passed imports and dependency checks; the Discord adapter supplement passed **7 tests / 1 expected skip**. No repository source fix, branch, commit, or PR was required.

Live credential validation, authenticated service health and the serving process SHA remain unverified: external probes fail at network resolution, and loopback socket access returns `PermissionError`, errno 1. The required soak pin also changed externally during this run. These findings do not authorize paper execution or a soak restart.

## Source, environment and authority

| Item | Observation |
|---|---|
| Install repository | `/workspace/ai-investment-cockpit`, clean `main` at `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e` (merged PR #70; newer than the prompt's through-#69 context) |
| Application tree | `5125e7edd2489c027364bbbfbd4556d928109817`, identical to `69a1c9c:app` in the committed September 6 platform-matrix evidence |
| Host interpreter | Linux x86_64, Python **3.13.5**; `uv python list --only-installed` found no 3.11/3.12 interpreter |
| Fresh isolated venv | `.venv/astra-fresh-install-2026-09-06`; no existing venv deletion or relocation |
| Initial soak checkout | At 00:19:59Z, clean detached `61ca0af27dc903ff97237b3cc2c8e83c735ebb09` |
| Later soak checkout | At 00:27:06Z and the final config check, clean detached `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e` |
| External change evidence | Soak `.git` now points to `.git/worktrees/cockpit-soak2`; its creation reflog records `c5ef0ad` at **00:25:36Z**. This agent made no worktree/ref/process operation against soak. Authorization and serving-process identity cannot be inferred from that reflog. |
| Frozen BUG-114 incident | `/workspace/cockpit-soak-bug114-incident` was not rewritten, repaired, opened as an application database, or used for test state |
| Credentials | Existing `~/.config/cockpit/run_with_cryptfile.py`, unlocking CryptFileKeyring from `~/.config/cockpit/cryptfile-key`; no value output, provisioning, migration, rotation, or new audit key |
| Test isolation | Clear inherited `COCKPIT_*`; pin a nonexistent test dotenv before imports; use null keyring discovery followed by the repository's in-memory fixture. The repository fixture gives every test a temporary audit-head file. |

No model jobs or subagents were launched. No service was started/stopped, no production composition was called, and no configuration or production ledger was edited. Unit tests exercised existing isolated fixtures, including their simulated approval paths. No production OrderGateway authority or `/paper approve` was created. No Discord post, command synchronization, or gateway connection was attempted.

## Canonical install and smoke entrypoints

Read `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/README.md`, `docs/PLAN.md`, relevant `docs/REGISTER.md` entries, `app/pyproject.toml`, `.github/workflows/ci.yml`, and the RW-01, W-22 and W-24 runbooks before execution. There is **no root Makefile** and no single all-in-one smoke target.

- `AGENTS.md` supplies `python -m venv .venv` and requires running gates from `app/` with that environment. Here the available executable is `python3`; `python` is absent.
- `CLAUDE.md` / `.github/workflows/ci.yml` supply the CI dependency set. CI deliberately omits `discord.py` so the unavailable-adapter branch is exercised. `app/pyproject.toml` additionally declares `discord.py` for runtime. CryptFile is the operator-authorized host addition.
- `tools/bootstrap_windows.ps1` is the Windows bootstrap, not a Linux entrypoint. Its list omits `boto3`, so use the complete CI/pyproject dependency list for operator matrix preparation. This did not require a Linux source fix.
- Gates: `python -m pytest -q` and `ruff check .`, from `app/`. This task executed the requested bounded targets and supplements below, not the full application suite.
- Presence check: `python tools/verify_keys.py`, with **no** `--migrate`, `--force`, or `--set`.
- Vendor check: `python tools/probe_credentials.py`, with **no** `--post`.
- Status: authenticated **GET** `http://127.0.0.1:8787/status`; keep the API key in memory and print only approved status fields. Also require unauthenticated GET to return 401.

## Ordered fail-closed smoke matrix

Run in this order for a new installation. A blocked prerequisite blocks its dependent readiness claim; independent offline diagnostics may continue. Missing observations never count as passes.

| Order | Check / entrypoint | Required outcome and stop rule | Observed result |
|---|---|---|---|
| 0 | Clean source and soak pin; explicit frozen-ledger exclusion | Bind source to full SHA; do not repin/restart/reuse an incident ledger. Stop service-readiness claims on drift. | **PASS initially; PIN DRIFT at final check.** Install remains clean `c5ef0ad`; soak disk checkout changed externally from `61ca0af` to `c5ef0ad`. |
| 1 | Create venv; install the CI dependency set; imports; `pip check` | Every required package must import. `pip check` alone cannot detect missing top-level packages. | **PASS after repair, and PASS from a fresh empty venv.** Python 3.13 diagnostic only. |
| 2 | `ruff check .` from `app/` | Zero lint findings; do not modify gates to pass. | **PASS**, both repaired project and isolated fresh venv. |
| 3 | Audit + merger pytest, invariant/adapter/PR #70 pressure supplements | Nonzero collection; BUG-114 concurrency, tamper/refusal and merger-source tests pass. Production secrets/config/state must remain unavailable to tests. | **PASS: 184 / 0 skipped** in fresh CI profile. Includes all 94 audit/merger/schema/tool cases plus 90 safety cases. |
| 4 | Add runtime `discord.py` + `keyrings.cryptfile`; imports; `pip check`; adapter supplement | Full runtime dependencies available without connecting/synchronizing a bot. | **PASS.** Discord 2.7.1, CryptFile 1.3.9. Adapter supplement **7 pass / 1 skip** because Discord is installed; that fallback case passed in step 3. |
| 5 | CryptFile-wrapped `verify_keys.py` | Five feed/operator credentials present, clean whitespace. Missing witness credentials retain SEC-11 refusal; never invent credentials. | **CORE PASS; canonical tool exits 1.** Alpaca key+secret, Finnhub, Discord and API key present. Existing audit HMAC present. Both WORM S3 credentials absent; SEC-11 remains blocked. |
| 6 | Settings secret guard; SEC UA; guild/channels; merger watchlist | Guard passes; UA is nonempty with a contact; guild and five channels configured; `mode=read_only`; merger count exactly 0. Any nonempty merger watchlist stops bring-up; do not clear/seed it automatically. | **PASS on both disk configs.** Contact and IDs withheld; no SEC request needed to prove presence. This is disk configuration evidence, not proof of process-loaded settings. |
| 7 | CryptFile-wrapped `probe_credentials.py` (no flags) | Actual Finnhub news/calendar, Discord identity/channel and Alpaca news authentication; failures block connected-smoke readiness. | **BLOCKED: 5/5 probes report failure**, exit 1. HTTP probes `URLError`, Alpaca `gaierror`; no vendor HTTP rejection was observed. Do not rotate keys based on these network errors. |
| 8 | Redacted unauthenticated + authenticated GET `/status` | 401 without key; 200 with key; `mode.mode=read_only`; valid audit chain; inspect watch-only/feed/performance reasons before any health claim. | **BLOCKED**, both attempts: `URLError` caused by `PermissionError`, errno 1. No response body, audit health, runtime mode, or HTTP status was obtained. |
| 9 | Discord local adapter awareness + GET `/api/v10/gateway/bot` | Library available; discovery response only. Existing READY/session state needs host evidence. Never start `run_bot`: `on_ready` synchronizes commands. | **IMPORT PASS / DISCOVERY BLOCKED** (`gaierror`, errno -3). `/status` has no gateway-connection field. No sends, gateway IDENTIFY, session creation or command sync. |
| 10 | Recheck source/soak SHA and runtime/process pin | Serving process, working directory, interpreter, startup SHA and ledger epoch must agree with the approved clean pin. | **BLOCKED.** Disk SHA drift confirmed; host process not visible in the command PID namespace; loopback blocked. Do not claim either SHA is currently serving 8787. |
| 11 | Platform acceptance | Native Windows and Ubuntu/WSL2 × Python 3.11/3.12, on the operator workstation under DEC-09. | **NOT RUN HERE.** Existing committed September 6 matrix covers the identical app subtree; this Python 3.13 Linux run does not replace or newly attest those four cells. |

The earlier shared-environment diagnostics passed 18 audit tests, 76 merger/schema/tool tests, and 89 additional tests with one skip. They are retained separately rather than added to the fresh count. The fresh environment was necessary because both soak and the shared dependency environment changed externally during the session.

## Install repair and retained failures

The initial `.venv` lacked `pydantic-settings`, `PyYAML`, `jsonschema` and `SQLAlchemy`; five requested test modules failed collection. `pip check` nevertheless returned success because it checks installed distributions' requirements, not the project's uninstalled top-level dependency declarations.

Two online installation attempts, including an explicit PyPI index, could not resolve `pydantic-settings`. No source package/version restriction was loosened. The existing pip HTTP cache contained complete wheel archives. `cache_wheels.py` exposed **76 unchanged cached wheels** through correctly named local links, recording their source paths, sizes and SHA-256 hashes. Pip then installed through `--no-index --find-links ... --no-cache-dir`. This is offline reuse of the existing host cache, not a new externally verified hash lock. Exact installed dependency inventories and the wheel manifest are included with the evidence.

The main environment received the missing dependencies and required transitive/runtime extras. Discord was absent at initial inventory but was already installed when this agent later issued its explicit runtime install command; that change was external. To get reviewable fresh evidence, this agent created the isolated venv, installed the CI package set without Discord, ran all 184 cases, and only then added Discord/CryptFile. The fresh venv retains its ensurepip-provided pip 25.1.1; installed packages were not gratuitously upgraded (`-U` was omitted).

One early repaired-environment pytest run was manually interrupted after yielding no output; its tool exit was 130 and it supplied no pass/fail verdict. It was replaced by completed, bounded diagnostic runs and the successful fresh run. No application bug is inferred from that interrupted run. No full-suite or supported-platform pass is claimed here.

## Soak recommendation: prefer a verified new clean epoch, preserve old evidence

At the initial state, keeping `61ca0af` as the existing awareness-only baseline would have been appropriate while preparing a reviewed new pin. PR #70's `c5ef0ad` contains additional audit-resolution concurrency, proposal authority and semantic-source hardening. It is the preferred **candidate for a separately authorized fresh soak epoch** for aggressive testing; silently relabeling an old ledger or rewriting failed evidence cannot provide that proof.

The external worktree replacement changed the situation during this run. **Leave the observed `c5ef0ad` checkout/process alone pending Admin/Noble's host-side verification; do not automatically restore `61ca0af` or bounce anything.** A clean checkout is insufficient evidence that a clean ledger was used or that the server was restarted on that SHA. This report does not infer authorization from another process's activity.

Admin/Noble's concrete next checks, without another unsolicited restart:

1. Confirm the external cutover was authorized. Match the listener PID on 8787 to its working directory, interpreter, start time and startup SHA. Record whether the old process still exists and which ledger/raw/audit-head paths each process uses. Never identify a running binary solely from the checkout's current HEAD.
2. Obtain a redacted authenticated `/status` from the host: require `read_only` and `audit_chain.ok=true`; retain head sequence and understood watch-only, ingest and paper-performance reasons. Require 401 without the key. Confirm the running configuration has the empty merger watchlist and intended SEC UA.
3. Establish that any new epoch uses a **separate fresh DB/raw/audit-head location**, with provenance recorded. Do not copy, heal, reset, truncate, or migrate the frozen BUG-114 ledger to achieve a green chain. Audit the new epoch as new evidence; retain prior failures as failures.
4. If no authorized cutover has occurred, prepare these artifacts first; Admin/Noble then performs the separately authorized stop/start under their normal procedure. Ensure only one process owns the service port. Do not launch a second composed stack as a smoke workaround: composition and bot startup may write audit state and synchronize/post to Discord.
5. Bind subsequent W-24 parser/intake evidence to its actual source SHA. Neither a new SHA nor green local tests supplies a cohort, witness, dates, precision evidence or paper authority. SEC-11 remains open.

## Next Monday morning plan

Preserve the already-armed **Monday 2026-09-07 05:30** natural-head re-screen. Prior wake records use Pacific time; if that remains the scheduler's zone, this is **12:30 UTC**. The actual scheduler entry was not inspected or changed here; Admin should confirm its recorded zone and execution receipt, without creating a duplicate wake.

The application's calendar marks Monday September 7 as Labor Day (non-trading) and Tuesday September 8 as a trading day. Keep Monday's job a bounded **GET-only discovery/intake screen**, not a market-session, paper-day or freshness-success claim. Use Tuesday for regular-session observations after the host-side smoke prerequisites pass.

At the wake: capture the actual parser and serving-process pins; confirm read-only state, understood audit/watch-only status, SEC UA and empty merger watchlist; then run the existing natural-head workflow. Require the absolute current SEC head to be supported **8-K / 10-Q / 10-K**, a complete ordered source set, cash-only source-derived eligibility, and exact expected/outside dates for current-target readiness. Preserve the distinct counts for supported heads, semantic/source-set selection eligibility and date-ready targets. Do not broaden forms, forge symbols, relax date parsing or substitute a cached screen for fresh evidence.

The last committed September 4 screen is **selection-ready 0, date-ready 0**; no fresh screen was performed in this task. Preserve the user's conservative selection hold and the **≥30** cohort requirement. The production manifest gate is 30 same-date eligible identities; current-target date authority is a separate later gate. Recording that distinction does not loosen either gate or authorize selection in this session. If the screen is still empty or below threshold, record refusal and stop. Even qualifying discovery would require separate reviewed selection/witness authority; this wake must not post a manifest, seed `COCKPIT_MERGER_WATCHLIST`, or issue `/paper approve`.

## Commands and artifacts

The companion command records retain exact argv, working directory, UTC start, duration and exit code; logs contain only sanitized output. `run_command.py` redacts URL output and bounds child processes. Test commands receive the isolated environment described above. Read-only inspection also used `git status`, `git rev-parse`, `git log`, `git diff --name-status`, targeted `rg`, and source reads; neither a Git mutation nor `main.py` execution occurred.

The command table below is generated from the completed command records. `dependencies-ci.json`, `dependencies-runtime.json` and `cached-wheel-manifest.json` bind the package observations. `check_config.py`, `status_get.py` and `discord_awareness.py` are reviewable evidence helpers, not application changes. To rerun a helper, use its retained path with the fresh venv and, where needed, the existing CryptFile wrapper. Only output from a new successful host run may close a currently blocked live check.

| Check / log | Exit | Command (cwd in companion JSON) |
|---|---:|---|
| [install-deps](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/install-deps.log) | 1 | `.venv/bin/python -m pip install --disable-pip-version-check --retries 0 --timeout 15 pip pydantic pydantic-settings keyring pyyaml jsonschema sqlalchemy pyarrow duckdb fastapi 'uvicorn[standard]' httpx websockets apscheduler boto3 pytest ruff` |
| [python-inventory](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/python-inventory.log) | 0 | `uv python list --only-installed` |
| [pip-check-before](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pip-check-before.log) | 0 | `.venv/bin/python -m pip check` |
| [install-missing-explicit-index](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/install-missing-explicit-index.log) | 1 | `.venv/bin/python -m pip install --index-url https://pypi.org/simple --no-cache-dir --disable-pip-version-check --retries 0 --timeout 10 -v pydantic-settings pyyaml jsonschema sqlalchemy discord.py` |
| [ruff-app](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/ruff-app.log) | 0 | `../.venv/bin/ruff check .` |
| [pytest-audit-merger](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pytest-audit-merger.log) | 2 | `../.venv/bin/python -m pytest -q tests/storage/test_audit.py tests/schemas/test_audit_entry.py tests/strategies/test_merger_edge.py tests/strategies/test_merger_semantics.py tests/tools/test_provision_merger_edge.py --tb=short` |
| [cache-wheels](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/cache-wheels.log) | 0 | `python3 /tmp/astra-fresh-install-2026-09-06/cache_wheels.py` |
| [install-deps-offline](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/install-deps-offline.log) | 0 | `.venv/bin/python -m pip install --no-index --find-links /tmp/astra-fresh-install-2026-09-06/wheelhouse --no-cache-dir --disable-pip-version-check pip pydantic pydantic-settings keyring pyyaml jsonschema sqlalchemy pyarrow duckdb fastapi 'uvicorn[standard]' httpx websockets apscheduler boto3 pytest ruff` |
| [verify-keys](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/verify-keys.log) | 1 | `.venv/bin/python /home/box/.config/cockpit/run_with_cryptfile.py tools/verify_keys.py` |
| [pip-check-repaired](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pip-check-repaired.log) | 0 | `.venv/bin/python -m pip check` |
| [probe-credentials](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/probe-credentials.log) | 1 | `.venv/bin/python /home/box/.config/cockpit/run_with_cryptfile.py tools/probe_credentials.py` |
| [pytest-audit-diagnostic](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pytest-audit-diagnostic.log) | 0 | `../.venv/bin/python -m pytest -vv -s tests/storage/test_audit.py -o faulthandler_timeout=20 --tb=short` |
| [imports-core](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/imports-core.log) | 0 | `.venv/bin/python -c 'import pydantic_settings, yaml, sqlalchemy, jsonschema; print("Core install imports passed")'` |
| [config-soak-pin](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/config-soak-pin.log) | 1 | `.venv/bin/python /tmp/astra-fresh-install-2026-09-06/check_config.py` |
| [status-get](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/status-get.log) | 1 | `.venv/bin/python /home/box/.config/cockpit/run_with_cryptfile.py /tmp/astra-fresh-install-2026-09-06/status_get.py` |
| [pytest-merger-diagnostic](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pytest-merger-diagnostic.log) | 0 | `../.venv/bin/python -m pytest -vv -s tests/schemas/test_audit_entry.py tests/strategies/test_merger_edge.py tests/strategies/test_merger_semantics.py tests/tools/test_provision_merger_edge.py -o faulthandler_timeout=25 --tb=short` |
| [pytest-install-safety](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pytest-install-safety.log) | 0 | `../.venv/bin/python -m pytest -q -s tests/test_invariants.py tests/bridge/test_bot.py tests/storage/test_rw20_concurrency_pressure.py tests/strategies/test_rw21_real_filing_grammar.py -o faulthandler_timeout=30 --tb=short` |
| [install-discord-runtime](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/install-discord-runtime.log) | 0 | `.venv/bin/python -m pip install --no-index --find-links /tmp/astra-fresh-install-2026-09-06/wheelhouse --no-cache-dir --disable-pip-version-check discord.py keyrings.cryptfile` |
| [create-fresh-venv](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/create-fresh-venv.log) | 0 | `python3 -m venv .venv/astra-fresh-install-2026-09-06` |
| [install-fresh-ci](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/install-fresh-ci.log) | 0 | `.venv/astra-fresh-install-2026-09-06/bin/python -m pip install --no-index --find-links /tmp/astra-fresh-install-2026-09-06/wheelhouse --no-cache-dir --disable-pip-version-check pip pydantic pydantic-settings keyring pyyaml jsonschema sqlalchemy pyarrow duckdb fastapi 'uvicorn[standard]' httpx websockets apscheduler boto3 pytest ruff` |
| [pytest-fresh-targeted](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pytest-fresh-targeted.log) | 0 | `../.venv/astra-fresh-install-2026-09-06/bin/python -m pytest -q -s tests/storage/test_audit.py tests/schemas/test_audit_entry.py tests/strategies/test_merger_edge.py tests/strategies/test_merger_semantics.py tests/tools/test_provision_merger_edge.py tests/test_invariants.py tests/bridge/test_bot.py tests/storage/test_rw20_concurrency_pressure.py tests/strategies/test_rw21_real_filing_grammar.py -o faulthandler_timeout=30 --tb=short -r s` |
| [ruff-fresh-app](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/ruff-fresh-app.log) | 0 | `../.venv/astra-fresh-install-2026-09-06/bin/ruff check .` |
| [pip-check-fresh-ci](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pip-check-fresh-ci.log) | 0 | `.venv/astra-fresh-install-2026-09-06/bin/python -m pip check` |
| [imports-fresh-ci](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/imports-fresh-ci.log) | 0 | `.venv/astra-fresh-install-2026-09-06/bin/python /tmp/astra-fresh-install-2026-09-06/install_inventory.py ci` |
| [install-fresh-runtime](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/install-fresh-runtime.log) | 0 | `.venv/astra-fresh-install-2026-09-06/bin/python -m pip install --no-index --find-links /tmp/astra-fresh-install-2026-09-06/wheelhouse --no-cache-dir --disable-pip-version-check discord.py keyrings.cryptfile` |
| [imports-fresh-runtime](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/imports-fresh-runtime.log) | 0 | `.venv/astra-fresh-install-2026-09-06/bin/python /tmp/astra-fresh-install-2026-09-06/install_inventory.py runtime` |
| [pytest-fresh-discord](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pytest-fresh-discord.log) | 0 | `../.venv/astra-fresh-install-2026-09-06/bin/python -m pytest -q -s tests/bridge/test_bot.py --tb=short -r s` |
| [verify-keys-fresh](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/verify-keys-fresh.log) | 1 | `.venv/astra-fresh-install-2026-09-06/bin/python /home/box/.config/cockpit/run_with_cryptfile.py tools/verify_keys.py` |
| [discord-gateway-awareness](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/discord-gateway-awareness.log) | 1 | `.venv/astra-fresh-install-2026-09-06/bin/python /home/box/.config/cockpit/run_with_cryptfile.py /tmp/astra-fresh-install-2026-09-06/discord_awareness.py` |
| [pip-check-fresh-runtime](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pip-check-fresh-runtime.log) | 0 | `.venv/astra-fresh-install-2026-09-06/bin/python -m pip check` |
| [probe-credentials-fresh](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/probe-credentials-fresh.log) | 1 | `.venv/astra-fresh-install-2026-09-06/bin/python /home/box/.config/cockpit/run_with_cryptfile.py tools/probe_credentials.py` |
| [status-get-fresh](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/status-get-fresh.log) | 1 | `.venv/astra-fresh-install-2026-09-06/bin/python /home/box/.config/cockpit/run_with_cryptfile.py /tmp/astra-fresh-install-2026-09-06/status_get.py` |
| [config-soak-pin-final](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/config-soak-pin-final.log) | 1 | `.venv/astra-fresh-install-2026-09-06/bin/python /tmp/astra-fresh-install-2026-09-06/check_config.py` |
| [pytest-audit-merger-repaired](/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06/pytest-audit-merger-repaired.log) | 130 | `../.venv/bin/python -m pytest -q tests/storage/test_audit.py tests/schemas/test_audit_entry.py tests/strategies/test_merger_edge.py tests/strategies/test_merger_semantics.py tests/tools/test_provision_merger_edge.py --tb=short` |

Final source observation: `2026-09-07T00:36:22.225098+00:00`. Install HEAD `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e`; soak checkout HEAD `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e`. Both status outputs are empty (clean).

Supporting files: `/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06`.

## Evidence-path limitation

Writing the requested `/workspace/w24-evidence/astra-fresh-install-smokes-2026-09-06.md` was blocked by the filesystem sandbox (`OSError`, errno 30). The report and all supporting commands/logs are saved under the writable project path `/workspace/ai-investment-cockpit/data/astra-fresh-install-2026-09-06`. No permission escalation or alternate route around the sandbox was attempted. Admin can copy this completed report to the requested evidence directory from the host. This publication limitation does not change the smoke verdicts.
