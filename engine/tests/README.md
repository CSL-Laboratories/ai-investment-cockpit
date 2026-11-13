# engine/tests/

Tests for `engine/**` go here, as named `test_*.py` files.

They are collected by the `engine tests` step of `ci.yml` job `linux` (required check
`linux-gates`), which runs `pytest` on `engine/tests` and is skipped while this folder
holds no `test_*.py` file at any depth (pinned by `tools/test_ci_engine_step.py`).
`python -m pytest` in `app/` collects only `app/tests/**`, and
`tools/required_ci_gate.py` runs no tests.

Under Rule CA (CP0-v2 §6.1), a test here counts as a verifier only when that step runs it.

The X1 parity-cutover amendment (csl-research
`contracts/ai-investment-cockpit/amendments/X1-PARITY-CUTOVER-2026-11-02.md`, section 3)
counts a successor test only when its items appear in `pytest --collect-only -q`, run in
the same `ci.yml` job, working directory and pytest configuration as the step that runs
`engine/tests/**`. A step that runs these tests with `unittest discover` alone does not
meet that, so the real step runs them through pytest. If the F-3 spikes choose a language
other than Python for `engine/`, the successor tests stay pytest-collected unless a later
receipted amendment changes the definition.
