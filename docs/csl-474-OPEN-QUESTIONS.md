# CSL-474 OPEN-QUESTIONS (frozen D10, not weakened)

Items that cannot be closed inside this stack without a D10 amendment or a
base-tree change. Fail-closed behaviour is kept.

## DR-4 — cwd `.mcp.json` / `.claude/` on `main`

D10:339 / D10:668: the launcher `stat`s `<cwd>/.mcp.json` and
`<cwd>/.claude/settings.json` / `settings.local.json` and **fails closed if any
exist**. Cockpit `main` currently tracks `.mcp.json` and `.claude/`. Honest
runs therefore refuse until those files are absent from the trusted base, or
U-17 is proven not to load them under `--setting-sources user`.

This stack does **not** skip the F6 check.

## G9 / F7 — cgroup v2 leaf

D10:341: if hosted cgroup v2 is absent, do not claim `setpgid` covers `setsid`.
This stack does not create a cgroup v2 leaf. F7 stays **partial**: worst
outcome is the R4 `writer_held` veto. Not claimed as PASS.

## D10:331 N8 vs #256 workspace Write

Frozen N8 `allowedTools` is
`Edit(/${{ github.workspace }}/**),Read,Glob,Grep,Bash(python3 …/model-api --:*)`
with **no Write**. #256 / U-8 added `Write(/${{ github.workspace }}/**)` because
a `Write(path)` deny does not bind Write, and the model must create files.
User instruction for M2 was workspace-scoped Edit/Write. The C argv allowlist
accepts that workspace Write. Exact N8 equality without Write would fail every
honest #256-shaped run.

## first_bash parent parser

D10:343: the launcher parent should parse Bash `tool_use` / `tool_result` ids.
This stack always emits `first_bash=missing` (D1: `bound` is not clause 3).
E4 stays parked. `origin=unattributed`.

## N9 bun MCP spawn

Forced launcher PATH `/usr/bin:/bin` does not contain `bun`. Action MCP
`command: "bun"` is expected to fail to spawn (D10:351). Operational residual.

## U-18 fork vs exec

Hooks record `[A]` and accept both `sudo_fork` and `sudo_exec`. They do not
pick which hosted sudo uses.
