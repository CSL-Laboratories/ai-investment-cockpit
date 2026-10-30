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
  run: python -X utf8 -B -m unittest discover -s engine -t . -p 'test_*.py'
```

That step is a workflow edit. It needs a founder-assigned issue and Lucas's own-words OK
before an agent writes it. The command above assumes Python test files and is only an
example; the real step follows the language the F-3 spikes choose.
