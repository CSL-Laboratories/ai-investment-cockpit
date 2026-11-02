# engine/tests/

Tests for `engine/**` go here, as named `test_*.py` files.

They are **not collected yet.** `ci.yml` job `linux` (required check `linux-gates`) has
explicit steps for `successor/` and `cockpit_spine/`, but none for `engine/tests/`.
`python -m pytest` in `app/` collects only `app/tests/**`, and
`tools/required_ci_gate.py` runs no tests.

Under Rule CA (CP0-v2 §6.1), a test here counts as a verifier only once a `ci.yml` step
in job `linux` runs it, for example:

```yaml
- name: engine tests
  if: hashFiles('engine/tests/test_*.py') != ''
  run: python -X utf8 -B -m pytest -q engine/tests
```

That step is a workflow edit. It needs a founder-assigned issue and Lucas's own-words OK
before an agent writes it. The command above is only an example.

The X1 parity-cutover amendment (csl-research
`contracts/ai-investment-cockpit/amendments/X1-PARITY-CUTOVER-2026-11-02.md`, section 3)
counts a successor test only when its items appear in `pytest --collect-only -q`, run in
the same `ci.yml` job, working directory and pytest configuration as the step that runs
`engine/tests/**`. A step that runs these tests with `unittest discover` alone does not
meet that, so the real step runs them through pytest. If the F-3 spikes choose a language
other than Python for `engine/`, the successor tests stay pytest-collected unless a later
receipted amendment changes the definition.
