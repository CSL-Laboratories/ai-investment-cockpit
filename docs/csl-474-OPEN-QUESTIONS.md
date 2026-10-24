# CSL-474 OPEN-QUESTIONS (frozen D10, not weakened)

Items that cannot be closed inside this stack without a D10 amendment or a
base-tree change. Fail-closed behaviour is kept. Round-2 design choices that
stay inside D10 are recorded here for Lucas.

Lucas decided P1–P10 on 2026-10-24 at 1:22pm PT (Linear CSL-474, Liaison
comment 2026-10-24T20:23:30Z): "Approve and start the fixes, make sure that
the acceptance conditions are also referenced." Each approval holds only
while its conditions hold. Condition IDs C1.1…C10.2 are that comment.

## DR-4 / N1 — launcher-owned empty MCP (chosen)

D10:339 / D10:668 originally `stat`s `<cwd>/.mcp.json` and
`<cwd>/.claude/settings.json` / `settings.local.json` and fails closed if any
exist. Cockpit `main` tracks `.mcp.json` and `.claude/settings.json`, so that
check would refuse every honest run.

**P1 (C1.1–C1.4):** keep fail-closed on `settings.local.json`. Ignore cwd
`.mcp.json` / `.claude/settings.json` only because the launcher requires
`--setting-sources user` and injects
`--strict-mcp-config --mcp-config /opt/csl-claude/mcp.empty.json` whose bytes
are exactly `{"mcpServers":{}}\n` (root-owned 0444, compiled-in path). Incoming
`--mcp-config` from the action is discarded, not forwarded. This is the
fail-closed design that still lets honest runs start. A D10 amendment should
record it (C1.1, still owed); this stack does not wait for `main` to drop
those files and does not edit the frozen DESIGN or addendum.

## G9 / F7 — cgroup v2 leaf

D10:341: if hosted cgroup v2 is absent, do not claim `setpgid` covers `setsid`.
This stack does not create a cgroup v2 leaf (C4.3). Subreaper wait for all
orphaned descendants outside a nested pid namespace; WNOHANG until a
few-second deadline, then SIGKILL the process group and remaining children
(C4.1: reap zombies with `waitpid(pid, WNOHANG)` before counting; drain
`waitpid(-1, WNOHANG)` and sleep ~10ms between rounds), then a final bounded
reap. Writer veto covers whatever is left after that; not a cgroup; F7
partial. Worst outcome is the R4 `writer_held` veto, which now closes
`fifo_w_held` after the F4c send (N5) so POLLHUP can fire. Not claimed as
PASS.

## D10:331 N8 vs #256 workspace Write

Frozen N8 `allowedTools` is
`Edit(/${{ github.workspace }}/**),Read,Glob,Grep,Bash(python3 …/model-api --:*)`
with **no Write**. #256 / U-8 added `Write(/${{ github.workspace }}/**)` because
a `Write(path)` deny does not bind Write, and the model must create files.
User instruction for M2 was workspace-scoped Edit/Write. The C argv allowlist
requires that exact workflow set (Edit+Write workspace, Read, Glob, Grep,
helper). Exact N8 equality without Write would fail every honest #256-shaped
run. **P3 C3.1/C3.2** keep that set. Hosted proof that the Edit deny works
(U-8, C3.3) is still owed under P9.

## first_bash parent parser

D10:343: the launcher parent should parse Bash `tool_use` / `tool_result` ids.
This stack always emits `first_bash=missing` (D1: `bound` is not clause 3).
E4 stays parked. `origin=unattributed`. **P5 C5.1–C5.3:** attribution only;
accept both `sudo_fork` and `sudo_exec`; P9 records the live parent chain
before either shape is dropped.

## N9 bun MCP spawn (chosen)

Forced launcher PATH `/usr/bin:/bin` does not contain `bun` (C2.1). Combined
with the empty launcher-owned MCP, the action `github_comment` MCP does not
spawn. **P2 C2.2:** helper (`Bash(python3 …/model-api --:*)`) is the only
GitHub write path. Do not add `/opt/csl-bun` to PATH. Recorded so a later D10
amendment can restore bun MCP if founders want it.

## U-18 fork vs exec

Hooks record `[A]` and accept both `sudo_fork` and `sudo_exec`. They do not
pick which hosted sudo uses.

## P1–P10 (decided by Lucas 2026-10-24 1:22pm PT)

R3 REVIEW-R3.md (sha256 3331c04a…) and R4 REVIEW-R4.md listed these as
accepted-risk / policy choices. Lucas approved the refined recommendations
from REPORT-A (P1/P2/P6), REPORT-B (P4/P9/P10) and REPORT-C (P3/P5/P7/P8)
with the conditions below.

**Code status (this stack, after C4/C6):** R3-B1, R3-M1/N7, R3-L1–L6, N19,
the H4 namespace check, C4.1 (zombie reap), C4.2 (behavioural N14 native
test), and C6.1–C6.3 (pluginConfigs, AGENTS.md stub+unstage, prompt) are in
this tree. N14 is a subreaper wait for all orphaned descendants outside a
nested pid namespace (session/`setsid` does not change reparenting). Writer
veto covers leftovers after the kill; not a cgroup; F7 partial. The H4 sweep
still only records (I-4 / C10.1).

1. **P1 / N1 — accepted with conditions.**
   - **C1.1 owed:** D10 amendment. Frozen DESIGN and addendum are not edited.
   - **C1.2 satisfied:** extra fail-closed refusal on `settings.local.json`.
   - **C1.3 satisfied:** this doc does not claim AGENTS.md or Read of cwd
     files are covered by `--setting-sources user` (that is P6).
   - **C1.4 owed:** P9 Probe 2 on 2.1.306 (cwd hooks/settings not loaded;
     AGENTS.md handling). Needs Lucas to add and dispatch a
     `workflow_dispatch` workflow.

2. **P2 / N9 — accepted.**
   - **C2.1 satisfied:** bun never goes on the CLI PATH.
   - **C2.2 satisfied:** `model-api` helper stays the only GitHub write path
     for the model.

3. **P3 / N8 — accepted with conditions.**
   - **C3.1 satisfied:** exact Edit+Write workspace tool set.
   - **C3.2 satisfied:** no `Write(path)` denies; denies use `Edit(//abs/…)`.
   - **C3.3 owed:** hosted proof that the Edit deny works (U-8), under P9.

4. **P4 / G9/F7 — fix first, then accepted.**
   - **C4.1 satisfied:** `kill_remaining_children()` reaps zombies with
     `waitpid(pid, WNOHANG)` before counting; each round drains
     `waitpid(-1, WNOHANG)` and sleeps ~10ms, then SIGKILLs newly adopted
     children (`PPid == self`). pgid SIGKILL, 3s/200ms bounds and
     writer-held veto kept.
   - **C4.2 satisfied:** `tools/test_sandbox_native.py` compiles and runs
     the launcher cleanup against a zombie child plus a `setsid`
     grandchild (P9 Probe 1; ordinary CI).
   - **C4.3 satisfied in this stack:** no cgroup leaf; F7 stays partial.
     Revisit only if Probe 0 or 1 shows leftovers.

5. **P5 — accepted with conditions.**
   - **C5.1 satisfied:** `first_bash=missing` is attribution only (E4 parked).
   - **C5.2 satisfied:** both `sudo_fork` and `sudo_exec`; no third
     (pty-monitor) shape.
   - **C5.3 owed:** P9 records the live parent chain before either shape
     is dropped.

6. **P6 — fix first, then accepted.**
   - **C6.1 satisfied:** user `settings:` JSON sets
     `pluginConfigs["agents-md@builtin"].options.instructionFiles` =
     `claude-md` (2.1.306 id; plugin README key shape; later versions also
     read `cc-plugin-agents-md@builtin`).
   - **C6.2 satisfied:** trusted post-checkout step stubs `AGENTS.md`;
     helper always unstages it (and lists it in `ACTION_RESTORED`);
     publish already refuses `AGENTS.md`.
   - **C6.3 satisfied:** prompt and `--append-system-prompt` no longer
     invite Read of AGENTS.md/CLAUDE.md.
   - **C6.4 satisfied as policy:** server-managed settings are an org-admin
     residual; managed MCP still fail-closes (`--strict-mcp-config` vs
     `managed-mcp.json`).

7. **P7 — accepted with conditions.**
   - **C7.1 satisfied in this stack:** stay on 2.1.306 while
     `/opt/csl-claude/mcp.empty.json` stays root-owned 0444 and the launcher
     discards foreign `--mcp-config`. Empirical immutability is Probe 2.
   - **C7.2 owed:** follow-up to move to 2.1.310 with a new signed pin and
     SDK argv re-trace. Not in this stack.

8. **P8 — accepted with conditions.**
   - **C8.1 owed:** #262 and #256 land on main first. #264 stays do-not-merge.
   - **C8.2 owed:** rebuild E3 → E2 → E1 linearly as new commits after that,
     including `553ad3dc` and these P4/P6 fixes.
   - **C8.3 owed:** for E1, apply the E1-only diff onto the new E2; do not
     replay the old chain.
   - **C8.4 satisfied:** no rebase or force-push in place. This stack still
     uses normal merge commits down E3 → E2 → E1.

9. **P9 — accepted, reduced scope. Probes 0/2 still owed.**
   - **Probe 0 owed:** host facts (cgroup v2, `cgroup.kill`, runner writing
     `cgroup.procs`, userns, sudo, `ps`) on **both** `ubuntu-24.04` and
     `ubuntu-26.04`. New `workflow_dispatch` workflow; Lucas approves adding
     and dispatching it.
   - **Probe 1 satisfied:** C4.2 native test, ordinary CI.
   - **Probe 2 owed:** 2.1.306 CLI: empty MCP file stays immutable, and
     `--setting-sources user` loads no cwd hooks. Same dispatch workflow;
     second explicit yes if OAuth is required. No write GITHUB_TOKEN. Do
     not use a live `@claude` on #265.

10. **P10 / I-4 — accepted.**
    - **C10.1 satisfied:** 0b sweep stays record-only.
    - **C10.2 satisfied:** 0b still runs before the `head_sha` checkout.

**Open item for Lucas (not a C-id):** `ubuntu-latest` is migrating from
24.04 to 26.04 between 19 Oct and 19 Nov 2026. Either pin `runs-on`, or
accept 26.04 and run Probes 0/2 on both images.

D5 and D8 remain unapproved. E4 stays parked.
