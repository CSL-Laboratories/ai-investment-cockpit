---
title: CSL-392 docs-read audit (G16)
type: evidence
status: audit
last-verified: 2026-10-26
---

# CSL-392: which docs the linux job actually opens

Agent (Grok Bot Liaison, for Lucas). Read-only audit. No `.github/` or filter
edits. Lucas admitted "audit first" on 2026-10-20 (CSL-392). Any filter change
still needs its own human-assigned issue and Lucas's own-words OK for the
`ci.yml` edit.

## Result

Head audited: `main` `139f255599408a553631d40ac8984bd3856a2fa6`
(2026-10-26 16:19 PT, after #287 and #271).

**Required `linux` job: one mismatch.**

| File | Reader | Filter |
| --- | --- | --- |
| `docs/ci-runbook.md` | `tools/test_ci_dispatch_scope.py::CrossLaneAdmissionTests.test_runbook_names_the_github_token_dispatch_exception` (`tools/test_ci_dispatch_scope.py:1657`; CSL-428, `41f90b03`, merged in #271) | skip (`^docs/`) |

A PR that only edits `docs/ci-runbook.md` takes the docs-only `linux-gates`
no-op, but the linux job pins that file's section-4 text (`workflow_dispatch`,
`repository_dispatch`, `forward-conflict-scan`, `github_token: ${{ github.token }}`,
`CSL-428`). The static guard `tools/test_ci_docs_filter.py` passes because the
test finds the file with `(ROOT / 'docs').iterdir()` and a name comparison,
which the guard's literal-path scan does not see (its own docstring lists
this limit).

**AGENTS.md and the proceed-or-escalate skill: closed.** CSL-491 (`81c2ce9a`,
2026-10-25) added `^AGENTS\.md$` and
`^\.claude/skills/proceed-or-escalate/SKILL\.md$` to the second run-tests
pattern (`.github/workflows/ci.yml:303-304`). `skipped_by_filter()` returns False
for both at this head. The earlier report's residual and its options
(a)/(b)/(c) are withdrawn.

**Outside the linux job (informational):** `tools/test_codex_gate.py` (run
by `claude.yml` and `codex-gate.yml`, not by `ci.yml`) opens
`docs/runbooks/w25-worm-audit-witness.md` in
`WorkflowStructureTests.test_agents_code_review_rules`
(`tools/test_codex_gate.py:1726`). Adding that path to the run-tests pattern
would not help: the linux job does not run `test_codex_gate.py`. Whether a
runbook-only change should trigger those workflows is a path-trigger
question for those workflows, not for the `ci.yml` docs filter.

## Options for Lucas (`docs/ci-runbook.md` only)

Neither option is admitted by CSL-392. (a) edits `.github/workflows/ci.yml`.

- **(a) Add the path to the filter.** Append `|^docs/ci-runbook\.md$` to the
  second run-tests `grep -Eq` (`ci.yml:303-304`) and add the path to `AUDITED`
  in `tools/test_ci_docs_filter.py`, as CSL-457 did for `CLAUDE.md` and
  CSL-491 for `AGENTS.md`. Two files. Cost: every runbook-only PR runs the
  two-cell Python matrix.
- **(b) Make the read visible, not the filter.** Change the test to read
  `ROOT / 'docs' / 'ci-runbook.md'`, so `test_ci_docs_filter.py`'s static
  guard fails until (a) lands. One test file, no workflow edit. On its own it
  turns the guard red, so it only makes sense together with (a).

Recommendation (not a decision): (a), the same move as CSL-457 and CSL-491;
(b) in the same change is optional hardening.

## Method

Tool: `tools/csl_392_docs_read_audit.py`, copied onto `PYTHONPATH` as
`sitecustomize.py`. It wraps `builtins.open`, `io.open` (needed for
`Path.read_text` / `read_bytes`), `os.open` (without `dir_fd`) and
`io.open_code`, and records each tracked file opened, with the current
pytest or unittest name. It also records each child process a test starts
and whether that child loads the hook.

The command list is built from the parsed `linux` job in `ci.yml` and the
`run-gates` composite action it calls; neither file is modified.
`python tools/csl_392_docs_read_audit.py plan` prints it. Planning fails
when a python/pytest/unittest line there is neither a runnable test/check
(`python [flags] tools/(test|check)_*.py`, `-m pytest`, `-m unittest`,
without `-I`/`-E`/`-s`) nor a reviewed `EXCLUDED` entry, when an `EXCLUDED`
entry no longer matches a job line, or when the run-gates call or the app
pytest run is missing. Ran at this head:

```text
(.)   python -X utf8 -B tools/test_run_gates_action.py
(app) python -m pytest -q
(.)   python -X utf8 -B tools/test_ci_dispatch_scope.py
(.)   python -X utf8 -B tools/test_ci_settle.py
(.)   python -X utf8 -B -m unittest tools.test_review_gate tools.test_queue_gates
(.)   python -X utf8 -B tools/test_ci_cancel_controller.py
(.)   python -X utf8 -B tools/test_candidate_workflows.py
(.)   python -X utf8 -B tools/test_ci_docs_filter.py
(.)   python -X utf8 -B tools/test_workflow_size.py
(.)   python -X utf8 -B tools/test_sandbox_e3.py
(.)   python -X utf8 -B tools/check_replacement_program.py
(.)   python -X utf8 -B tools/check_o7_operator_contract.py
(.)   python -X utf8 -B -m unittest -q tools.test_replacement_program
(.)   python -X utf8 -B tools/check_checker_test_floor.py
```

Excluded, with the reason recorded in `EXCLUDED`: the two candidate-policy
`merge_candidate.py` calls (need the Actions env), the run-gates installer,
`pip install maturin`, `maturin build`, the wheel install `python -P -c`,
and the batch-parity re-run of `tests/quant/book/test_book_batch.py`
(the same file runs in `python -m pytest -q`). Ruff is not a Python
command line and is not run.

Exit codes: 0 clean, 1 incomplete (any suite non-zero; the summary carries
`"complete": false` and `failed_suites`), 2 mismatches.

Residual run (not the linux job), same hook:
`tools/test_claude_pr_fixer.py`, `test_sandbox_e1/e2/native.py`,
`test_claude_fixer_receipts/guard/publish.py`,
`test_fixer_failure_escalation/watcher.py`, `test_codex_gate.py`,
`test_codex_gate_identity.py`, and
`-m unittest tools.test_merge_candidate tools.test_candidate_gate`.

## Suite results

| Step | Result |
| --- | --- |
| `test_run_gates_action` | 18, 1 skipped, OK |
| `app` pytest | 3877 passed, 135 skipped, 449 s |
| `test_ci_dispatch_scope` | 39 OK |
| `test_ci_settle` | 4 OK |
| `test_review_gate` + `test_queue_gates` | 36 OK |
| `test_ci_cancel_controller` | 66, **1 failure** (see below) |
| `test_candidate_workflows` | 12 OK |
| `test_ci_docs_filter` | 19 OK |
| `test_workflow_size` | 5 OK |
| `test_sandbox_e3` | 45 OK |
| `check_replacement_program` | exit 0 |
| `check_o7_operator_contract` | exit 0 |
| `tools.test_replacement_program` | 34 OK |
| `check_checker_test_floor` | PASS |

Tracked files opened by the linux job: 618. Mismatches: 1. Runner exit 2.

**Incomplete suite.** `test_ci_cancel_controller.py::QueuePolicyTests.test_queue_lint_exception_is_exact_and_limited_to_named_paths`
fails the same way without the hook. It runs the VM's `actionlint` 1.7.7
(`/usr/local/bin/actionlint`) on fixtures and skips when `actionlint` is not
on `PATH` (`tools/test_ci_cancel_controller.py:1076-1078`). That is a
local-environment result, not caused by the hook. Opens from the other 65
tests in that suite are recorded. Unverified: whether hosted runners have
`actionlint` on `PATH`.

Residual suites: all exit 0 (722 tests in all). One residual mismatch,
listed above.

## Blind spot: child processes

The hook sees Python opens only. These tests start children whose reads it
cannot see. "Hook not loaded" means a Python child started without the
audit `PYTHONPATH` or with `-I`/`-E`/`-s`.

Linux job:

| Child | Test files |
| --- | --- |
| `git` | `tests/test_invariants.py`, `tests/bridge/test_witness_adapters.py`, `tools/test_candidate_workflows.py`, `tools/test_ci_dispatch_scope.py`, `tools/test_ci_docs_filter.py` |
| `bash` | `tests/test_claude_settings.py`, `tests/test_cloud_session_hook.py`, `tools/test_candidate_workflows.py`, `tools/test_ci_cancel_controller.py`, `tools/test_ci_dispatch_scope.py`, `tools/test_ci_docs_filter.py`, `tools/test_ci_settle.py`, `tools/test_queue_gates.py`, `tools/test_sandbox_e3.py` |
| Python, hook not loaded | `tests/benchmarks/test_perf_compare.py`, `tests/bridge/test_bot.py`, `tests/swarm/test_bounded_job.py` |
| `actionlint` | `tools/test_ci_cancel_controller.py` |
| `install`, `unshare`, test `wrapper` | `tools/test_sandbox_e3.py` |
| `claude` | `tests/agent_runner/test_transport_resolve.py` |
| `gcc`, `ld`, `ldconfig` (library lookup) | `tests/agent_runner/test_account_guard.py` |
| `lsb_release`, `rustc`, `uname` | `tests/test_cloud_session_hook.py` |

Most `git` and `bash` children run the decide script or fixtures in a
temporary repo, not this checkout. The rows to check by hand, if a later
pass wants to close the gap, are the three "hook not loaded" Python
children and `tests/test_invariants.py`'s `git` calls.

Residual suites: `test_sandbox_native.py` and `test_sandbox_e2.py` (compiled
helpers, `gcc`), `test_claude_pr_fixer.py` (`gh`, `jq`, `bash`,
`actionlint`), `test_claude_fixer_publish.py` (`git`, `bash`, `env`, one
Python child without the hook), `test_codex_gate.py` (`actionlint`).

## Limitations

- Python open family only; child-process reads are listed above, not
  traced. unittest `setUpClass` opens attribute to `file:line`.
- One interpreter: this VM's venv is CPython 3.13.17, outside the DEC-09
  3.11/3.12 matrix. The open set is not expected to differ by version
  (unverified).
- No ruff, no batch-parity wheel, no Windows/macOS.
- This file is a new doc in a product repo. The current rule puts new docs
  in CSL-Laboratories/csl-research with a link from here; moving it is left
  to Lucas.

## Reproduce

```bash
python tools/csl_392_docs_read_audit.py plan
python tools/csl_392_docs_read_audit.py /tmp/csl392-opens.jsonl
python tools/csl_392_docs_read_audit.py classify /tmp/csl392-opens.jsonl
python -X utf8 -B tools/csl_392_docs_read_audit_test.py
```

The tool's tests live in `tools/csl_392_docs_read_audit_test.py`, not
`tools/test_*.py`: that name needs founder-approved allow rules in
`.claude/settings.json` (`app/tests/test_claude_settings.py`), and a new
`app/tests/` file needs an X1 inventory row (`tools/check_x1_inventory.py`).
CI does not run them.
