# CLAUDE.md — operating manual for the AI Investment Cockpit

> Auto-loaded each session. Keep it tight and high-signal. The detailed playbooks
> and the full design corpus live in `docs/` — start at `docs/README.md`.

## What this is
A solo-developer, subscription-powered, **human-gated** AI investing copilot.
**Paper-first** — no live capital moves until the pre-live gates pass. Think of it
as **two engines**:
1. **Awareness + discipline** — the reliable value (time saved, not missing things,
   enforced discipline). Powered by the AI's real strength: information work.
2. **Execution** — mechanically reliable, financially *experimental*, capped, and
   gated. Engine 1 makes Engine 2 safer and better-informed; it never makes it
   "reliable growth."

The reliable ROI is **time, awareness, and discipline — not alpha.** Any plan that
leads with "the AI will beat the market" is wrong; the corpus documents why.

## The 9 invariants — NEVER weaken silently
1. **Single order path** — an order reaches a broker ONLY via risk-pass → mode →
   approval → `OrderGateway`. This includes **human-initiated** trades.
2. **Decision path pinned to Claude in auto modes** — Codex/other runtimes are
   barred from the decision/order path.
3. **Schema-validate every model output** (repair-then-quarantine).
4. **Secrets only in Windows Credential Manager** (`keyring`) — never in repo,
   prompts, chat, or logs.
5. **`risk/` is pure deterministic functions** — NO LLM, NO I/O, NO subprocess.
   Enforced by the no-LLM-in-risk CI gate.
6. **Human gate is client-side; hash-chained append-only audit** — every
   state change is audited.
7. **Fail closed** — vendor-limit / stale-data / hung orchestrator → `watch_only`,
   never "trade without fresh analysis."
8. **Raw inputs stored separately from summaries** — enables replay + interrogation.
9. **Post-PDT risk model** — Reg-T $2,000 min equity, no day-trade counts.

**PAPER-FIRST** overrides all: no live capital until the P0 pre-live gates pass —
chief among them SEC-11 (`docs/security-hardening-backlog.md`).

## New load-bearing rules (this refactor era — see `docs/refactor-plan.md`)
- **New data feeds are UNTRUSTED input** — wrap in `<untrusted-content>`,
  canary-scan (SEC-02). Inv 3 validates model *output*; this governs tool/feed *input*.
- **The operator surface is ABSTRACTED** — code against the `OperatorSurface`
  interface, never hardwire Discord. Discord is one adapter among several.
- **The universe is DYNAMIC** — discovery → promotion → on-demand deep fetch →
  thesis → exit. Not a fixed watchlist.
- **Execution stays model-free.** Models serve awareness/reasoning only.

## Session workflow
1. Read `docs/README.md` (corpus index) + the relevant plan doc
   (`docs/refactor-plan.md`, `docs/capability-expansion-master-plan.md`,
   `docs/path-to-production.md`).
2. Pick **ONE** work package (RW-xx refactor, or W-xx production). Confirm its
   scope and its deterministic gate before writing code.
3. Implement. Preserve the invariants. Add tests alongside.
4. Run the gates (below). Green before you stop.
5. **Do not re-litigate settled decisions** — the corpus is a one-way pipeline
   (research → architecture → build → security → enhancements).
6. Commit only when the operator asks. Message convention:
   `feat(RW-xx): …` / `fix(RW-xx): …` / `docs(RW-xx): …`.

## Hard rules
- Never put an LLM on the order or risk path.
- Never move live capital or unlock an auto rung without its gate.
- **Never weaken a gate or invariant to make a test pass — fix the code.**
- Prompts live in git; fail any soak/replay run on a dirty working tree.
- Any change touching the order path gets an **adversarial-review round** (the
  attack→fix→regression pattern; see `docs/ultracode-build-playbook.md`).
- When unsure whether something is settled, grep the corpus before deciding.

## Run the gates
```bash
cd app
python -m pip install pydantic pydantic-settings keyring pyyaml jsonschema pytest ruff
python -m pytest -q      # incl. Invariant-1 call-site / no-LLM-in-risk / secrets-grep gates
ruff check .
```

## Where things are
- **Core engines (solid — change with care + adversarial review):** `app/risk/`,
  `app/brokers/` (gateway, state machine, capability), `app/orchestrator/`,
  `app/storage/audit.py`.
- **Data feeds:** `app/ingest/`. **LLM pipelines:** `app/workflows/`.
- **Operator surface:** `app/bridge/` → `app/surfaces/` after RW-02.
- **Design corpus:** `docs/` — index at `docs/README.md`.
