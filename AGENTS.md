# Codex operating guide — AI Investment Cockpit

This project is a solo-developer, subscription-powered, human-gated investing
copilot. It is paper-first: no live capital moves until the pre-live gates in
`docs/security-hardening-backlog.md` are satisfied.

## Codex boundary

- Codex and Claude may perform schema-gated analysis jobs (briefing, triage,
  grounding, proposal drafting, and fresh interrogation) in `read_only` and
  human-approved `paper` mode.
- Do not put either model runtime or an MCP server inside `app/risk/`, give a
  model broker credentials/tools, or add a direct route from a model to
  `OrderGateway`.
- Codex decision jobs use the hardened profile in `app/agent_runner/runner.py`:
  prompt over standard input, read-only sandbox, ephemeral session, ignored
  user config/rules, and shell/web/apps/browser/computer/MCP capabilities
  disabled. The actual argv is inspected before spawn.
- `review_live`, `auto_veto`, and `auto_full` remain outside the Codex decision
  boundary. Do not relax that pin without a separately scoped, adversarially
  reviewed safety redesign and the pre-live gates.

## Invariants

Never weaken these silently:

1. Orders only flow through risk pass → mode → approval/veto → `OrderGateway`.
2. Every model output is schema-validated; repair once, then quarantine.
3. Secrets are only in Windows Credential Manager, never the repository,
   prompts, command-line arguments, or logs.
4. `app/risk/` is deterministic and pure: no LLM, I/O, or subprocesses.
5. The human gate is client-side and every state change is hash-chain audited.
6. A stale feed, vendor limit, or failed orchestrator fails closed to
   `watch_only`.

## Working conventions

1. Read `docs/README.md` and the relevant plan before editing.
2. Keep changes to one work package and add tests with the change.
3. Use the project virtual environment on Windows:
   `\.venv\Scripts\python.exe -m pytest -q` and
   `\.venv\Scripts\ruff.exe check .` from `app/`.
4. Keep the repository private and commit only when the operator asks.
5. Any order-path change needs an adversarial-review round.
