---
title: RW-34 Cloud adapter inventory and qualification
status: reference
last-verified: 2026-09-15
---

# RW-34 — one admitted development route

## Source

Cloud Agent `bc-cde5f49d-5354-4503-85c3-2f06851f7968` on Ubuntu 24.04.4,
Linux 6.12.94+, 4 vCPU / 16 GiB. Checkout `cursor/rw34-cursor-adapter-7968`
from reviewed `codex/swarm-rebase-2026-09-14` at
`cd8c86cf9f0c14ff7c849e4e5768103f8268d479`, which includes the RW-33
handoff and evidence. RW-32 is not reused.

## Inventory (no model work, no Devin install)

| Route | Path / version | Help / capabilities | Qualification |
|---|---|---|---|
| Cursor-native Task | Head Task tool in this session (`CURSOR_AGENT=1`, socket `/run/cursor/api.sock`) | Fresh subagents, selectable models, tools enabled; no argv pin, no mechanical no-tools/MCP/network denial, no enforceable USD budget | Unqualified. Missing capability pin and budget enforcement. |
| Cursor CLI (remote) | `/home/ubuntu/.cursor-server/bin/f09fca384ceca23f7bf21f9c23655b162641d740/bin/remote-cli/cursor` — app 3.20.21 commit `f09fca3` | `--help` prints “Command is only available in WSL or inside a Visual Studio Code terminal.” `cursor agent` would curl-install `~/.local/bin/cursor-agent` | Unqualified. Not runnable here; auto-install forbidden. |
| Codex CLI | not on PATH | — | Unqualified. Not installed. |
| OpenCode CLI | not on PATH | — | Unqualified. Not installed. |
| Devin CLI | not on PATH | — | Not inspected for new jobs. Do not reinstall. |
| `cursorsandbox` | `/exec-daemon/cursorsandbox` | `--policy` JSON; `--preflight-only` | Host helper, not an admitted model route. |
| Python | `/usr/bin/python3` 3.12.3 | local subprocess, sanitizable env | Admitted as the json-child executable only. |

Authentication: this Cloud Agent session is authenticated as Lucas Guerra. No
CLI login files for Codex/OpenCode/Devin were found. Credentials were not
read. MCP servers exist under `~/.cursor/projects/workspace/mcps/` for Head;
they are not attached to the admitted child argv.

Cursor-native Task is distinct from Cursor CLI. Task cannot satisfy “pin
actual argv/config/capabilities before spawn.” A read-only filesystem would
not disable its shell, network or MCP tools.

## Admitted profile

- id: `cursor-cloud-json-child`
- executable: `sys.executable` + `docs/research/swarm-tools/json_child.py`
- family / model: `cursor-cloud-json-child` / `json-child` (controller-bound)
- roles: `leaf`, `first-gate-review`
- budget: `reserved_usd=0`; concurrency default 1; timeout ≤ 120 s; output ≤ 128 KiB
- stdin excerpts only; controller writes exclusive `raw.jsonl` / `filing.json` / `receipt.json`
- unknown completion retains the reservation hold
- Muse / OpenRouter / Hive second-gate: admission refused (no distinct qualified family)

`spawn_scoped_leaf.py` remains preview-only.

## Limits

This qualifies a development-job controller on Cloud Linux. It does not
qualify a paid model CLI, DEC-09 Windows/WSL2 cells, paper soaks, or
empirical PASS. Same-host Cloud is not an independent witness.

## Fixture hashes (this host)

See [[evidence/rw34-cloud-adapter-2026-09-15]]. Leaf filing SHA-256
`f5d394529518ca0b00745c3c0a23d762e84accaa9c73be3729b67d0a81175708`.
Policy digest (`json_child.py`)
`ecbf584b60f46c18b2c3905e91431c76ddb24d8c4ae79ad94d277864a9def37a`.
Admitted and observed inference: `$0`. First-gate same-family
`pass-with-dissent`. Second-gate synthesizer roles refused.
