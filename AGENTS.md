# Codex operating guide — AI Investment Cockpit

This project is a solo-developer, subscription-powered, human-gated investing
copilot. It is paper-first: no live capital moves until the pre-live gates in
`docs/security-hardening-backlog.md` are satisfied.

The ratified [PRODUCT.md](PRODUCT.md) owns the autonomous paper-product target and
acceptance; [docs/PLAN.md](docs/PLAN.md) owns delivery order. The current human-gated
implementation and the boundaries below remain authoritative until their separately
scoped, reviewed implementation amendments. Product ratification grants no runtime
permission or empirical PASS.

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

## Cursor Cloud research/development boundary (amended 2026-09-14)

RW-33 replaces the retired Devin/SWE-2 dispatch policy. Head is the Cursor
Cloud Agent on its Linux VM; Cursor-native fresh sessions are preferred for
research, REGISTER-claimed development and adversarial review. Codex and
OpenCode are named for these bounded development roles off the order path.
Naming and installation alone do not qualify a CLI or grant production authority.

- No new Devin jobs. Keep `spawn_devin_leaf.py` frozen for historical audit.
- No swarm changes to risk, OrderGateway, mode/approval, audit, eligibility or
  secrets; no broker tools/credentials, soaks or auto rungs. Current production
  Codex decision jobs still use the unchanged hardened runner above.
- Maximum depth: Head → middle → leaf; at most eight leaves per middle within
  an admitted aggregate budget. Fresh attempts; no bulk inherit or leaf spawning.
- Verify model/version/capabilities on the actual Cloud host. Unknown profile,
  missing reservation, deadline or disclosure admission refuses dispatch. No
  auto/force/trust/permission bypass or automatic MCP approval. The scoped helper
  is preview-only and cannot launch any CLI until a separately claimed adapter
  is implemented and qualified; do not add an override to skip qualification.
- Muse is the preferred second-gate synthesizer only when a verified different
  family from producers/reviewers. Preserve dissent. OpenRouter/Hive are fallback
  synthesis only, under enforceable aggregate ceilings of $5/$50; no automatic
  resets. ChatGPT is manual steering only. Chiron/Recursion remain future PD-029
  integrations, not a prerequisite or a current routing destination.
- Head prepares reviewable changes and draft PR material. Existing operator
  commit/publication instructions apply; Lucas remains the merger. Swarm tests
  and receipts never satisfy empirical, paper-day or pre-live gates.

Current process: `docs/research/agent-swarm-rebase-2026-09-14.md`.
Cloud start: `docs/research/cursor-cloud-rdt-handoff-2026-09-14.md`.
Market/paper soaks and DEC-09 operator cells stay on their designated hosts.

## Invariants

**`CLAUDE.md` holds the canonical list of 9 invariants — read it there.** This file
used to restate a shortened, differently-numbered set of 6, so "Inv 4" in a code
comment resolved to *secrets* against one file and *`risk/` purity* against the
other. Code comments here cite invariants constantly, so one numbering is the only
workable arrangement. The summary below is a pointer, not a second source:

Orders only via risk → mode → approval → `OrderGateway` (Inv 1); the decision path
is pinned to Claude in auto modes (Inv 2); every model output is schema-validated
(Inv 3); secrets live only in the OS credential store (Inv 4); `app/risk/` is pure
and deterministic (Inv 5); the human gate is client-side and hash-chain audited
(Inv 6); vendor limit / stale feed / hung orchestrator fails closed to `watch_only`
(Inv 7); raw inputs are stored separately from summaries (Inv 8); post-PDT risk
model (Inv 9).

Note on Inv 4: the store is the **OS credential store** — Windows Credential
Manager, macOS Keychain, or Linux SecretService. `core/secrets.py` calls the
generic `keyring` API with no platform branch, so it is already portable; only the
wording was Windows-only. Do not add a platform branch to satisfy the old phrasing.
On headless Linux there is no SecretService by default: the test suite installs an
in-memory backend (`app/tests/conftest.py`), but a real Ubuntu deployment needs one
provisioned.

## Working conventions

1. Read `docs/README.md` and the relevant plan before editing.
2. Keep changes to one work package and add tests with the change.
3. Use the project virtual environment. Create it once with `python -m venv .venv`
   from the repo root — note the `py` launcher is not present on every dev box —
   then install the full runtime and validation dependency set CI installs (see
   `.github/workflows/ci.yml` and the command in `CLAUDE.md`), including `discord.py`.
   Every matrix cell must run the real offline Discord command-tree tests; the absent
   dependency fallback is covered separately in an isolated subprocess.
   Run the gates from `app/`:
   - Windows: `..\.venv\Scripts\python.exe -m pytest -q` and `..\.venv\Scripts\ruff.exe check .`
   - Ubuntu / WSL2: `../.venv/bin/python -m pytest -q` and `../.venv/bin/ruff check .`

   On Windows, point `TMP`/`TEMP` at a SHORT path (e.g. `C:\t`) first: pytest's
   nested tmp dirs plus this repo's deep fixture paths cross MAX_PATH and produce
   ~80 spurious `WinError 206` failures that are not real defects. Also clear any
   ambient `COCKPIT_*` vars — real operator config in the shell fails at least one
   test that asserts an unconfigured default.
4. Keep the repository private and commit only when the operator asks.
5. Any order-path change needs an adversarial-review round.
6. The release-blocking local matrix is **native Windows plus Ubuntu under WSL2**, each on
   Python 3.11 and 3.12. Run all four cells on the operator's Windows workstation; do not
   require GitHub-hosted execution or Hetzner for current package acceptance. macOS is a
   best-effort portability target, not a release gate. Continue to write platform-neutral
   code: review subprocess spawn, path construction, credential lookup, and temp-file use,
   and prefer inspecting the failure (as `orchestrator/halt.py` does) over branching on
   `os.name`. See `DEC-09` in `docs/REGISTER.md`.
