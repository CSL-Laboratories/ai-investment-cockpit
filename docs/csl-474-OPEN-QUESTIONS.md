# CSL-474 OPEN-QUESTIONS (frozen D10, not weakened)

Items that cannot be closed inside this stack without a D10 amendment or a
base-tree change. Fail-closed behaviour is kept. Round-2 design choices that
stay inside D10 are recorded here for Lucas.

## DR-4 / N1 — launcher-owned empty MCP (chosen)

D10:339 / D10:668 originally `stat`s `<cwd>/.mcp.json` and
`<cwd>/.claude/settings.json` / `settings.local.json` and fails closed if any
exist. Cockpit `main` tracks `.mcp.json` and `.claude/settings.json`, so that
check would refuse every honest run.

**Choice for Lucas (this stack):** keep fail-closed on `settings.local.json`.
Ignore cwd `.mcp.json` / `.claude/settings.json` only because the launcher
requires `--setting-sources user` and injects
`--strict-mcp-config --mcp-config /opt/csl-claude/mcp.empty.json` whose bytes
are exactly `{"mcpServers":{}}\n` (root-owned 0444, compiled-in path). Incoming
`--mcp-config` from the action is discarded, not forwarded. This is the
fail-closed design that still lets honest runs start. A D10 amendment should
record it; this stack does not wait for `main` to drop those files.

## G9 / F7 — cgroup v2 leaf

D10:341: if hosted cgroup v2 is absent, do not claim `setpgid` covers `setsid`.
This stack does not create a cgroup v2 leaf. F7 stays **partial**: worst
outcome is the R4 `writer_held` veto, which now closes `fifo_w_held` after the
F4c send (N5) so POLLHUP can fire. Not claimed as PASS.

## D10:331 N8 vs #256 workspace Write

Frozen N8 `allowedTools` is
`Edit(/${{ github.workspace }}/**),Read,Glob,Grep,Bash(python3 …/model-api --:*)`
with **no Write**. #256 / U-8 added `Write(/${{ github.workspace }}/**)` because
a `Write(path)` deny does not bind Write, and the model must create files.
User instruction for M2 was workspace-scoped Edit/Write. The C argv allowlist
requires that exact workflow set (Edit+Write workspace, Read, Glob, Grep,
helper). Exact N8 equality without Write would fail every honest #256-shaped run.

## first_bash parent parser

D10:343: the launcher parent should parse Bash `tool_use` / `tool_result` ids.
This stack always emits `first_bash=missing` (D1: `bound` is not clause 3).
E4 stays parked. `origin=unattributed`.

## N9 bun MCP spawn (chosen)

Forced launcher PATH `/usr/bin:/bin` does not contain `bun`. Combined with the
empty launcher-owned MCP, the action `github_comment` MCP does not spawn.
**Choice for Lucas:** helper (`Bash(python3 …/model-api --:*)`) is the only
GitHub write path. Do not add `/opt/csl-bun` to PATH. Recorded so a later D10
amendment can restore bun MCP if founders want it.

## U-18 fork vs exec

Hooks record `[A]` and accept both `sudo_fork` and `sudo_exec`. They do not
pick which hosted sudo uses.
