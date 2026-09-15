---
title: RW-34 Cloud json-child adapter — Cloud Linux evidence
status: reference
last-verified: 2026-09-15
---

# RW-34 / RW-35 Cloud Linux evidence

Cloud Agent `bc-cde5f49d-5354-4503-85c3-2f06851f7968` on Ubuntu 24.04.4,
Linux 6.12.94+, Python 3.12.3. Branch `cursor/rw34-cursor-adapter-7968`
from reviewed `codex/swarm-rebase-2026-09-14` (`cd8c86c`), whose merge is
now on `origin/main` as PR #96 (`cfb4f7c`). RW-32 is not reused. This is
not a DEC-09 cell and not empirical PASS.

## Inventory (no Devin install, no CLI auth)

| Route | Path / version | Result |
|---|---|---|
| Cursor-native Task | this Head session (`CURSOR_AGENT=1`) | Unqualified: cannot pin argv, tools, MCP, network, or USD |
| Cursor remote-cli | `.../f09fca3.../remote-cli/cursor` app 3.20.21; also `.../6496ea8...` app 3.19.19 | Unqualified: `--help`/`--version` refuse outside a VS Code terminal. `cursor agent` would auto-install |
| Codex / OpenCode / Devin | not on PATH | Unqualified / not reinstalled |
| Admitted | `/workspace/.venv/bin/python` + `docs/research/swarm-tools/json_child.py` | Profile `cursor-cloud-json-child`, `reserved_usd=0` |

## Admitted fixture (real child)

Pinned argv:

```text
/workspace/.venv/bin/python
docs/research/swarm-tools/json_child.py
--role leaf
--output-limit 32768
```

| Object | SHA-256 |
|---|---|
| `json_child.py` (policy / profile digest) | `ecbf584b60f46c18b2c3905e91431c76ddb24d8c4ae79ad94d277864a9def37a` |
| `bounded_job.py` | `2d87278ddb07c908b959f53f9fd6dfa7b3e4874fd4d9768732f8c51de6c7dd3b` |
| fixture brief | `4bdcc8efaf09b1f8c551dcbddf2686458fb7181095675e00e7df045752493121` |
| leaf raw.jsonl | `8efd54b7c5c30d2db9c1a1ba340a9e99a1d3f14d59ed1d37bb4b79856e2b85c1` |
| leaf filing.json | `f5d394529518ca0b00745c3c0a23d762e84accaa9c73be3729b67d0a81175708` |
| leaf receipt_sha256 (pre-hash object) | `fd7acfef78bce6b1b8d3fc7833c31818d80875b0fa911c1128628660709f803c` |
| first-gate raw.jsonl | `01ca742d17b23c74e37ec30de306752497a628bc570a012d3cd174cef1a587d2` |
| first-gate filing.json | `6aceaabfee12052fea6776a46eb40ef53233e7c2162029785140f82ebe32506b` |
| first-gate receipt_sha256 (pre-hash object) | `f69fb94cf33f54287c99282c42b6e50dc688caa49fb5ee715c02e5071fe3205a` |

Copies: [rw34-cloud-json-child-2026-09-15](rw34-cloud-json-child-2026-09-15/).
Admitted/observed usage: `$0` / `$0`, class `measured`. No broker secrets.
Same-family first-gate verdict `pass-with-dissent`. Muse/OpenRouter/Hive
second-gate roles are admission-refused (no distinct qualified family;
provider names are not families; Task cannot enforce budget). No empirical PASS.

`receipt_sha256` hashes the receipt object before the digest field is filled;
it is not the SHA-256 of the written `receipt.json` bytes.

## Checks

Targeted: `tests/swarm/test_bounded_job.py` and
`tests/workflows/test_research_update.py` (23 passed) plus Ruff clean on
`app/`. Full Cloud Linux pytest on this candidate: **3292 passed / 6 skipped**
in 5m52s with project venv Python 3.12.3. That does **not** replace operator
Windows/WSL2 Python 3.11/3.12 DEC-09 cells. GitHub Actions are optional
diagnostics.

## RW-35

Retained freeze/evaluation/cases/protocol SHA-256 unchanged:

- freeze `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8`
- evaluation `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31`
- cases `1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41`
- protocol `a7bb528863b0a43073f1fbfbb91b6145fa6b6d85018a4dd5828c79ef76b6c528`

`qualify_count` stays 0. Cohort gate stays 30. No soak, no order-path edit.

## Remaining

- Operator DEC-09 matrix
- Distinct-family synthesizer once a qualified non-Task route exists
- Paid provider fallback until budget can be enforced
- Slice C/D authority packages remain separately claimed
