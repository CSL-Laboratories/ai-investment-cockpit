# Codex operating guide — AI Investment Cockpit

This project is a solo-developer, subscription-powered, human-gated investing
copilot. It is paper-first: no live capital moves until the pre-live gates in
`docs/security-hardening-backlog.md` are satisfied.

## Codex boundary

- Use Codex for repository maintenance and explicit research/coding jobs.
- Do not put Codex, another model runtime, or an MCP server on the risk or
  order path.
- Codex research jobs use `codex exec --sandbox read-only`; prompts are passed
  over standard input, never on the command line.
- The current architecture deliberately keeps Codex out of decision-stage and
  auto-trading jobs. Do not change that boundary without an explicitly scoped,
  adversarially reviewed safety redesign.

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
