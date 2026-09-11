# Swarm tools

Deterministic helpers for the Cloud pipeline. No LLM.

- `validate_filings.py` — jsonschema intake + frozen manifest
- `spawn_devin_leaf.py` — refuse-by-default Devin argv (`accept-edits`,
  no `--sandbox` on filing leaves), then spawn. `--dry-run` prints argv
  without calling Devin.
- `fixtures/` — schema fixtures, not live evidence

Requires `jsonschema`. Filings at runtime go to `.swarm-runs/`.
