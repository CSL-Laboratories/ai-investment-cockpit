# Swarm tools — RW-33

Current policy: [Cursor Cloud rebase](../agent-swarm-rebase-2026-09-14.md).

- `spawn_scoped_leaf.py`: offline route preview only. Historical filename;
  there is no child-process implementation. `--runtime cursor|codex|opencode`,
  `--brief`, `--export`, `--model` and `--dry-run` produce JSON describing
  an unqualified requested route. Model availability stays unknown. Omitting
  `--dry-run` refuses before input/output access. No guessed runnable argv.
- `validate_filings.py`: bounded final-JSON admission, no model calls.
  Rejects duplicate keys/attempts, empirical PASS and mismatched declared family/
  leaf coverage. Hashes exactly the validated bytes. A failure writes no manifest;
  a successful manifest is created exclusively and cannot overwrite an earlier one.
- `spawn_devin_leaf.py`: retired, byte-frozen historical reference. Never invoke
  it for new cockpit work. Its existing tests preserve historical behavior only.
- `fixtures/`: updated schema fixtures, not live model evidence. Current schemas
  require route_family for every filing and attempt/source_families for synthesis.
  Archived filings keep their dated contract; do not rewrite evidence to migrate it.

From the repo root, using the project Python:

```text
python docs/research/swarm-tools/spawn_scoped_leaf.py --runtime cursor --brief docs/research/swarm-briefs/ping-example-cursor.md --export .swarm-runs/preview/attempt-1/filing.json --model muse-spark-1.3-medium --dry-run
python docs/research/swarm-tools/validate_filings.py --run-dir .swarm-runs/fixture-unique --leaf docs/research/swarm-tools/fixtures/valid-leaf.json --review docs/research/swarm-tools/fixtures/valid-review.json --synthesis docs/research/swarm-tools/fixtures/valid-synthesis.json
```

Choose a fresh run directory for every validation attempt. Requires jsonschema.
The validator accepts final filing JSON, not raw CLI JSONL/events. A future
qualified adapter owns bounded event parsing, raw retention, reservation/receipt
accounting and cancellation. Preview success and internally consistent declared
model families are not proof that a runtime ran or that a review is independent.
See [Cloud qualification handoff](../cursor-cloud-rdt-handoff-2026-09-14.md).
