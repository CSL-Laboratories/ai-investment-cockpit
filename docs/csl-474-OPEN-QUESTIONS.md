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
This stack does not create a cgroup v2 leaf. N14 is a subreaper wait of
non-`setsid` descendants, not a substitute for a cgroup. F7 stays
**partial**: worst outcome is the R4 `writer_held` veto, which now closes
`fifo_w_held` after the F4c send (N5) so POLLHUP can fire. Not claimed as PASS.

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

## Round-3 / Round-4 policy (waiting for Lucas)

R3 REVIEW-R3.md (sha256 3331c04a…) and R4 REVIEW-R4.md list these as
accepted-risk / policy choices, not in-design defects. This stack does
not treat them as decided.

**Code status (R4):** R3-B1, R3-M1/N7, R3-L1–L6, N19, and the H4
namespace check are fixed. N14 is a subreaper wait of descendants that
did not `setsid` (R4-L1); it is not a cgroup and does not cover `setsid`.
The H4 sweep still only records (I-4). P1–P10 stay here.

1. **P1 / N1.** Accept tracked and PR-head-controlled `.claude/settings.json`,
   hooks, skills, `.mcp.json`, CLAUDE.md and AGENTS.md in the working
   directory, relying on `--setting-sources user` plus `--strict-mcp-config`
   with the empty launcher-owned config, instead of D10's `stat` refusal.
   Needs a D10 amendment. Frozen DESIGN and addendum are not touched.
2. **P2 / N9.** No bun on PATH; helper is the only GitHub write path. Under
   P1 the launcher discards the action MCP config, so this is "no action MCP
   at all".
3. **P3 / N8.** Keep `Write(/workspace/**)`; the launcher requires that exact
   set.
4. **P4 / G9/F7.** Writer-held veto without a cgroup. N14: `PR_SET_CHILD_SUBREAPER`
   then `waitpid(-1)` so grandchildren that did not `setsid` are reaped
   before END. `setsid` is still the veto path. Not claimed as PASS for F7.
5. **P5.** `first_bash=missing` in every run; U-18 accepts both `sudo_fork`
   and `sudo_exec`.
6. **P6.** Residual inputs the flag does not control: server-managed
   settings, and AGENTS.md through the built-in plugin.
7. **P7.** Stay on 2.1.306 even though 2.1.309 fixes `--mcp-config` file
   mutation. Acceptable only while `/opt/csl-claude/mcp.empty.json` stays
   root 0444.
8. **P8.** Merge commits in #264. Do not merge #264 to main in this form.
   Either #262 and #256 land first, or Lucas explicitly accepts the merges.
   This stack does not rebase. A later linear rebase must keep the ordinary
   E1 "wire static launcher" commit (R4-I3).
9. **P9.** Any live validation needs separately approved probes: setting-
   sources behaviour on 2.1.306, the real SDK argv, and U-16/17/18.
10. **P10 / I-4.** `ALLOW_PIDS` is unused and the 0b sweep only records.
    Extra runner-uid pids are Worker/Listener; this stack does not
    fail-closed them. H4 namespace compare is fixed; this sweep residual
    is a new accepted-risk item for Lucas (R4-I2).
