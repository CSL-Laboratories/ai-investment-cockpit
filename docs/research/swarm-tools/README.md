# Swarm tools — RW-34

Current policy: [Cursor Cloud rebase](../agent-swarm-rebase-2026-09-14.md).
Adapter claim: RW-34 in [[REGISTER]].

- `bounded_job.py`: admitted development-job controller. Profile
  `cursor-cloud-json-child` only. Admission, argv pin, reservation,
  exclusive output, bounded transport parse, once-only settlement.
- `json_child.py`: no-tools child. Stdin excerpts in, one JSONL final
  object out. Does not write filings or import subprocess/socket/urllib.
- `spawn_scoped_leaf.py`: still offline route preview only. Historical
  filename; there is no child-process implementation and no bypass flag.
- `validate_filings.py`: bounded final-JSON admission, no model calls.
- `spawn_devin_leaf.py`: retired, byte-frozen historical reference.

Cursor-native Task, Cursor CLI, Codex CLI and OpenCode CLI were inventoried
on the Cloud host and remain unqualified: they cannot pin capabilities and
enforce budget on this VM. Do not reinstall Devin.

From the repo root, using the project Python:

```text
python docs/research/swarm-tools/spawn_scoped_leaf.py --runtime cursor --brief docs/research/swarm-briefs/ping-example-cursor.md --export .swarm-runs/preview/attempt-1/filing.json --model muse-spark-1.3-medium --dry-run
python docs/research/swarm-tools/bounded_job.py preview --envelope <frozen-envelope.json> --run-dir .swarm-runs/<run>
python docs/research/swarm-tools/bounded_job.py run --envelope <frozen-envelope.json> --run-dir .swarm-runs/<run>
```
