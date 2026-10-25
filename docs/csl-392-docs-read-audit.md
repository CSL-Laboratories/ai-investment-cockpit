---
title: CSL-392 docs-read audit (G16)
type: evidence
status: audit
last-verified: 2026-10-25
---

# CSL-392: which docs the linux job actually opens

Read-only audit. No `.github/` or filter edits. Lucas admitted “audit first”
on 2026-10-20 (CSL-392). Any later filter change still needs its own
human-assigned issue.

## Result

**Required `linux` job: no mismatches.** At `main`
`31cad9e5f62d74df0ec202c08bbb98a4bbb52549`, every tracked file that job
opened is already classified `run-tests` by the current decide step
(`.github/workflows/ci.yml`, the two `grep -Eq` run patterns then the skip
pattern). CSL-397 (#223) closed the 21-file set from the 2026-10-20 comment
on this issue; this re-run still opens all 21, and the filter no longer
skips them.

**Residual (not in the linux job, still skipped by the same filter):**

| File | Evidence | Filter |
| --- | --- | --- |
| `AGENTS.md` | `tools/test_codex_gate.py` `WorkflowStructureTests.setUpClass` (`AGENTS.read_text`, line 1453), consumed by `test_agents_code_review_rules` | skip (`\.md$`); not `^CLAUDE\.md$` |
| `.claude/skills/proceed-or-escalate/SKILL.md` | `tools/test_codex_gate.py::WorkflowStructureTests.test_agents_code_review_rules` (`Path.read_text`, lines 1710–1713) | skip (`\.md$`) |

Those two files are why an `AGENTS.md`-only (or skill-only) PR still takes
the docs-only `linux-gates` green no-op, while the tests that actually read
the file live in `claude.yml` / `codex-gate.yml`.

The open E-stack PRs
[#264](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/264)
(E3),
[#263](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/263)
(E2) and
[#265](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/265)
(E1) just hit this class: they edit `claude.yml` and fixer tests, and
`claude.yml` runs `tools/test_codex_gate.py`, which pins live `AGENTS.md`
and the proceed-or-escalate skill. A stacked or sibling change that only
touches `AGENTS.md` still skips the required Python matrix. The linux-job
static guard (`tools/test_ci_docs_filter.py`) does not scan
`test_codex_gate.py`; it only allow-lists `AGENTS.md` *string fixtures* in
`app/tests/test_linear_pr_sync.py` and `app/tests/test_stuck_thread_watch.py`,
which this open-hook run confirmed do **not** open the tracked file.

`CLAUDE.md` is already in the run-tests pattern (CSL-457). The linux job did
not open it; `test_codex_gate.py` did.

## Method

Head: `31cad9e5f62d74df0ec202c08bbb98a4bbb52549` (`origin/main` 2026-10-25).
CPython 3.12.3 only (CI’s linux matrix also has 3.11; file-open set is the
same). Pinned run-gates requirements from
`.github/actions/run-gates/requirements.txt`. This VM has no
`python3.12-venv` / `ensurepip`, so the venv was created with `virtualenv`
and then the same pins; `python -P .github/actions/run-gates/install.py`
cannot run here.

Hook: `tools/csl_392_docs_read_audit.py` copied onto `PYTHONPATH` as
`sitecustomize.py`. Wraps `builtins.open`, `io.open` (required for
`Path.read_text` / `Path.read_bytes`), `os.open`, and `io.open_code`.
Classifies each opened *tracked* path with the decide step’s three
`grep -Eq` patterns, same split as `tools/test_ci_docs_filter.py`.

Commands (the linux job’s test steps; needles are checked against
`ci.yml` + `run-gates/action.yml` before the runner starts):

```bash
python tools/csl_392_docs_read_audit.py
```

which runs:

```text
python -X utf8 -B tools/test_run_gates_action.py
cd app && python -m pytest -q
python -X utf8 -B tools/test_ci_dispatch_scope.py
python -X utf8 -B tools/test_ci_settle.py
python -X utf8 -B -m unittest tools.test_review_gate tools.test_queue_gates
python -X utf8 -B tools/test_ci_cancel_controller.py
python -X utf8 -B tools/test_candidate_workflows.py
python -X utf8 -B tools/test_ci_docs_filter.py
```

Not run, on purpose: `ruff check .` (not a file-open suite) and the linux
job’s later maturin/batch-parity step (re-runs
`tests/quant/book/test_book_batch.py` after a wheel; that file is already
in `pytest -q`).

Residual reader, **not** part of the linux job:

```bash
PYTHONPATH=<audit-dir> CSL392_AUDIT_LOG=... CSL392_REPO=. \
  python -X utf8 -B tools/test_codex_gate.py
```

## Suite results

| Step | Result |
| --- | --- |
| `test_run_gates_action` | 18 tests, 1 skipped, OK |
| `app` pytest | 3848 passed, 136 skipped, 272 s |
| `test_ci_dispatch_scope` | 25 OK |
| `test_ci_settle` | 4 OK |
| `test_review_gate` + `test_queue_gates` | 36 OK |
| `test_ci_cancel_controller` | 66 tests, 1 skipped, OK |
| `test_candidate_workflows` | 12 OK |
| `test_ci_docs_filter` | 13 OK |
| `test_codex_gate` (extra) | 94 tests, 1 skipped, OK |

Opened tracked files in the linux job: 589. Mismatches: **0**.

## Linux-job doc-like opens (already run-tests)

These are the skip-looking paths the linux job opened. All match a
run-tests pattern after CSL-397 / the swarm `.py|.json|.ya?ml` rule.

| File | Example test |
| --- | --- |
| `.gitignore` | `tests/test_invariants.py::test_runtime_artifact_directories_are_ignored_at_any_depth` |
| `app/filings/PROVENANCE.md` | `tests/filings/test_provenance.py::test_pins_are_wellformed` |
| `app/workflows/prompts/grounding_check.v1.md` | `tests/system/test_paper_replay.py::test_g9_0_replay_did_real_work` |
| `app/workflows/prompts/interrogation_fresh.v1.md` | `tests/test_bugfix_audit_2026_09_07.py::test_interrogation_wraps_unresolvable_refs` |
| `app/workflows/prompts/morning_brief.v1.md` | `tests/system/test_one_market_day_dryrun.py::test_one_market_day_dryrun_reaches_no_broker` |
| `app/workflows/prompts/trade_proposal.v1.md` | `tests/system/test_paper_replay.py::test_g9_0_replay_did_real_work` |
| `app/workflows/prompts/triage_batch.v1.md` | `tests/system/test_one_market_day_dryrun.py::test_one_market_day_dryrun_reaches_no_broker` |
| `docs/PLAN.md` | `tests/test_replacement_program_registry.py::test_replacement_program_registry` |
| `docs/evidence/w24-natural-head-rescreen-2026-09-09.md` | `tests/feasibility/test_screens.py::test_checked_in_screens_load` |
| `docs/evidence/w24-natural-head-rescreen-2026-09-12.md` | same |
| `docs/evidence/w24-natural-head-rescreen-2026-09-15.md` | same |
| `docs/evidence/w24-natural-head-rescreen-2026-09-15-tue.md` | same |
| `docs/replacement/AUTHORITY-COMPLIANCE-RESEARCH.md` | `tests/test_replacement_program_registry.py::test_replacement_program_registry` |
| `docs/replacement/ISSUE-CONTRACT.md` | `tests/test_o7_operator_contract.py::test_exact_candidate_command_shows_negative_controls_failing` |
| `docs/replacement/MONTH-PROGRAM.md` | same |
| `docs/replacement/README.md` | `tests/test_replacement_program_registry.py::test_replacement_program_registry` |
| `docs/replacement/SCOPE-AUDIT.md` | `tests/test_o7_operator_contract.py::test_exact_candidate_command_shows_negative_controls_failing` |
| `docs/replacement/SUCCESSOR-PRODUCT.md` | same |
| `docs/replacement/outcomes/O7/CONTRACT.md` | same |
| `docs/research/gate-feasibility-and-value-2026-09-06.md` | `tests/feasibility/test_protocol.py::test_checked_in_protocol_loads` |
| `docs/research/swarm-briefs/rw34-json-child-fixture.md` | `tests/swarm/test_bounded_job.py::test_expired_admission_refuses_before_spawn` |
| `docs/setup/accounts-and-keys.md` | `tests/system/test_rw01_market_day.py::test_discord_secret_name_matches_the_provisioning_doc` |

Plain docs the linux job did **not** open, and the filter still skips:
`README.md`, `PRODUCT.md`, `CONTRIBUTING.md`, `docs/README.md`,
`docs/ci-event-contract.md`, `LICENSE`. That skip is working as designed
for those paths.

An empty diff still takes `ci.yml` “No changed files; skip test jobs”.

## Options for Lucas

Any of (a) or a filter-side (c) still needs a **new** human-assigned issue
that explicitly asks to edit `.github/workflows/ci.yml`. This issue does
not admit that edit. Effort is in files/invasiveness, not calendar time.

### (a) Narrow the filter

Add `^AGENTS\.md$` and the proceed-or-escalate skill (either the exact
path or `^\.claude/skills/`) to the second run-tests `grep -Eq`, the same
way CSL-457 added `^CLAUDE\.md$`. Extend `tools/test_ci_docs_filter.py`
with `test_agents_md_alone_runs_tests` (mirror `test_claude_md_alone_runs_tests`)
and put both paths in `AUDITED`.

- **Effort:** two files (`.github/workflows/ci.yml` decide step +
  `tools/test_ci_docs_filter.py`). Same size as the CLAUDE.md follow-up.
- **Risk:** every `AGENTS.md`-only PR runs the Python linux matrix (two
  CPython cells). That is the intended G16 close for this residual.
  Directory-prefix `^\.claude/` would also run tests for skill/settings
  edits; `.claude/settings.json` already runs because of `\.json$`.
  Does not fix G18 (`rust/*.md` / `.github/*.md` still skip Python and
  still start Rust). Residual: a new reader of some other skipped `.md`
  still needs a later audit.

This is the same move Lucas already chose on 2026-10-20 for the first 21
files (“Go with option B”, CSL-397).

### (b) Change the tests

Stop `tools/test_codex_gate.py` from reading the live files. Copy the
pinned phrases into the test module (or a fixture under `tools/`) and
assert against that.

- **Effort:** one file (`tools/test_codex_gate.py`). No workflow edit, so
  it can land under a test-only issue.
- **Risk:** `AGENTS.md` can change and `linux-gates` still no-ops. The
  E-stack tests would no longer notice a live-file drift. This removes
  the read rather than closing G16. Worse for #264/#263/#265, whose
  `claude.yml` path is exactly the one that currently *does* re-run
  `test_codex_gate.py` when the workflow changes, but not when only
  `AGENTS.md` changes.

### (c) An allowlist

Keep the skip regex. Replace (or feed) the decide step with an explicit
list of paths that must run tests — the `AUDITED` tuple in
`tools/test_ci_docs_filter.py` is already that list for the linux job.
Promote it into the decide script (exact paths, not prefixes) and add
`AGENTS.md` plus the skill path. Optionally point
`linux_job_test_sources()` at the test files named in `claude.yml` /
`codex-gate.yml` so the static guard sees `test_codex_gate.py`.

- **Effort:** same two files as (a), plus a small decide-script change to
  iterate a list instead of (or in addition to) the prefix regex. More
  coupling: every new test-read doc is a filter edit.
- **Risk:** more precise than directory prefixes (`docs/evidence/` today
  runs tests for *any* evidence markdown, including ones no test reads).
  Higher drift: a forgotten allowlist row recreates G16. The static
  guard still cannot see `os.path.join` / f-string paths. G18 unchanged
  unless `rust/*.md` is listed.

A guard-only change that scans `test_codex_gate.py` *without* adding the
paths to run-tests would fail until those paths are allowlisted as
“not a real read” (they are a real read) or added to the filter (a).

## Recommendation (not a decision)

**(a)**, same pattern as CSL-397 and CSL-457: put `AGENTS.md` and the
proceed-or-escalate skill on the run-tests side, and make the static
guard see `test_codex_gate.py`. Do not treat the linux-job empty
mismatch list as “G16 is done”; the E-stack PRs are the counterexample.

## Limitations

- Python open family only. `git show`, `cat`, and non-Python helpers are
  not logged. unittest `setUpClass` opens can attribute to a file:line
  instead of `Class.test_name` (the `AGENTS.md` row above).
- One CPython. No ruff, no batch-parity wheel.
- First hook pass wrapped only `builtins.open` and missed
  `Path.read_text`; the committed runner wraps `io.open` as well. Figures
  in this report are from the second pass.

## Reproduce

```bash
python tools/csl_392_docs_read_audit.py /tmp/csl392-opens.jsonl
python tools/csl_392_docs_read_audit.py classify /tmp/csl392-opens.jsonl
```
