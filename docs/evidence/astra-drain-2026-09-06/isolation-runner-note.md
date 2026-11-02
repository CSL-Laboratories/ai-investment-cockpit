# isolation drain runner note

- Command: `cat prompt-isolation.md | codex exec -m gpt-6-astra -c model_reasoning_effort="xhigh" -s workspace-write -`
- Cwd: `/workspace/ai-investment-cockpit`
- Started: 2026-09-07T00:45:50Z (17:45:50 PT)
- Codex PID 267917 last session event: 2026-09-07T02:54:41Z (19:54:41 PT); process gone by 19:55 PT
- `isolation.log` has **no** trailing `tokens used` footer
- Session rollout ends mid-turn on `write_stdin` yield to monitor session 28142 (before 03:15 UTC / 20:15 PT cutoff)
- Original bash wrapper tracking lost ~02:36Z with Cursor `exit_code: unknown`; codex continued then later exited without clean wrap-up
- No OOM kill of codex PID in dmesg
- **Exit code: unknown (incomplete / non-clean).** Treat as non-success for drain accounting; wrapper never printed `EXIT_CODE=`
- Primary evidence: `astra-soak-isolation-checklist-2026-09-06.full.md` (= report-working.md, 582 lines) and `isolation-artifacts/`
- On-disk `docs/evidence/astra-soak-isolation-checklist-2026-09-06.md` was truncated during overlapping host rebase (copied as `.md`); fuller log extract also retained as `.from-log.md`
- Soak trees not rewritten by this runner; HEADs: live `c5ef0ad`, prior `61ca0af`, incident `1e2d4e3`
