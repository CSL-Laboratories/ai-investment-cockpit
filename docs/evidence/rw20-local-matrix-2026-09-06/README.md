# Local matrix artifacts

These files record local compatibility checks. They grant no selection, order,
independent-witness or deployment authority. See the adjacent
`rw20-matrix-isolation-2026-09-06.md` for the source commits, scope and limitations.

- `w311`, `w312`, `l311`, `l312`: native Windows and Ubuntu 24.04 WSL2 application
  suites, using Python 3.11 and 3.12 respectively. Each directory contains exact
  command arguments, versions, exit statuses, logs, JUnit XML and source hashes.
- `s311w`, `s312w`, `s311l`, `s312l`: the separately committed research scorer's
  ten unit tests, Ruff and its no-input refusal to report measurements.
- `h311w`, `h312w`, `h311l`, `h312l`: the separately committed held-excerpt
  companion's seven checks and Ruff. Some checks document intended refusals or
  the still-open SAFT false positive; they are not a grammar-repair PASS.
- `environment.json`, `wsl-environment.log` and the setup-failure log preserve
  environment provenance and the recovered initial resource failure.
- `matrix-summary.json` is generated only after all twelve directories exist.
  It verifies required commands, source identity, interpreter/platform identity,
  exit statuses, original and supplemental source hashes, per-artifact hashes,
  and the unmeasured CLI output before reporting a complete local matrix.

The scripts are the actual local evidence runners. Their arguments are:

```text
<matching-venv-python> run_cell.py <full-git-archive.tar> <new-run-directory> <commit> <git-tree>
<matching-venv-python> run_score_cell.py <two-file-scorer-archive.tar> <new-score-run-directory>
<matching-venv-python> run_held_cell.py <held-excerpts-archive.tar> <completed-app-run-directory> <new-held-run-directory>
<python> aggregate_matrix.py <this-artifact-directory>
```

Use the source commits and archive paths recorded in the adjacent evidence
document. Full runs require a new directory and the documented CI dependency
profile. The held-excerpt runner adds only its five new files to a completed
source extraction; it refuses to overwrite any original file and rechecks the
original 499-file manifest. It never runs the excerpt regeneration tool.

Generated test databases, temporary source extractions, interpreters and package
environments are deliberately excluded. Actual run directories remain under
`C:\t\a26` and the separately named `Cockpit-Audit-2404` distro's `/audit`.
No deployed soak directory or ledger was used.

The scoped `.gitattributes` preserves exact hashed bytes, including CRLF. These
same-host hashes detect changes; they are not independent signatures or a WORM
witness. Do not normalize, reformat or replace evidence files without retaining
and distinguishing the original artifacts.
