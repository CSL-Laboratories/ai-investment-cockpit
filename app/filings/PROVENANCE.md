# `app/filings/` — vendored from `edgar-morning-brew`

This tree is **not cockpit-authored code**. It is the deterministic core of the sibling
repository `edgar-morning-brew` (Lucas, Chaandan, Samir — private), vendored in-process by
**RW-10** (`docs/refactor-plan.md` §5.2). Upstream is preserved and still developed: it keeps
its LLM brief composer, its approve-before-send path, its digest, and its 14-day soak. The
cockpit takes only the part that computes filing deltas and binds every claim to raw bytes.

**Read `docs/refactor-plan.md` §5.2 before changing anything here.**

## Pins

Vendored from `edgar-morning-brew` at `92027d7990a98f9d9bcf8ff4e3ef0452f86d16f9`
(2026-08-10, `cockpit/pre-plan-bug-sweep` — the 2026-08-09 sweep's seven fixes flowed UP and
were re-pinned per this document's contract; merge that branch to upstream `main` when
convenient, the pin is to the commit, not the branch name). Per-package pins are the last
upstream commit that touched each path —
they are what a re-sync diffs against, so they are recorded separately rather than assuming
one repo-wide SHA covers everything:

| Vendored path | Upstream path | Pinned at |
|---|---|---|
| `filings/edgar/` | `app/edgar/` | `92027d7990a98f9d9bcf8ff4e3ef0452f86d16f9` |
| `filings/parse/` | `app/parse/` | `92027d7990a98f9d9bcf8ff4e3ef0452f86d16f9` |
| `filings/delta/` | `app/delta/` | `92027d7990a98f9d9bcf8ff4e3ef0452f86d16f9` |
| `filings/cite/` | `app/cite/` | `92027d7990a98f9d9bcf8ff4e3ef0452f86d16f9` |
| `filings/raw_store.py` | `app/storage/raw_store.py` | `982693cb78fad454379b83faa6341ff9248a6dae` |
| `tests/fixtures/sec/` | `app/tests/fixtures/sec/` | `a61c2515efa9f4b786eb631584409bf6d62ea29a` |

## Checking for upstream fixes

**Do this periodically — the core is not frozen.** Measured over `db55e2f..bb90c53`
(2026-07-09 → 07-16) the take-set gained +432 lines, `submissions_poll.py` (+272) and
`fetch.py` (+121) among them. That is the SEC client: the module most exposed to a live
external service and therefore the one most likely to need upstream's next fix.

```bash
git -C ../edgar-morning-brew fetch origin
git -C ../edgar-morning-brew diff 982693cb78fad454379b83faa6341ff9248a6dae..origin/main \
    -- app/edgar app/delta app/cite app/storage/raw_store.py
git -C ../edgar-morning-brew diff 0f9f3263e6e9d399173c9794a2adcbcba683d6c4..origin/main \
    -- app/parse
```

Apply what is relevant, replay the rename map below, re-run GR-10, and update the pins in
the same commit. A pin that no longer matches what is on disk is worse than no pin.

## The rename map

Upstream roots its imports at the repository (`app.foo`); the cockpit roots at `app/`
(`pyproject.toml: pythonpath = ["."]`), so cockpit modules import as `foo`. Every rewrite is
therefore a **pure prefix substitution** — no logic, no reordering, no reformatting:

| Upstream import | Vendored import |
|---|---|
| `app.edgar.…` | `filings.edgar.…` |
| `app.parse.…` | `filings.parse.…` |
| `app.delta.…` | `filings.delta.…` |
| `app.cite.…` | `filings.cite.…` |
| `app.storage.raw_store` | `filings.raw_store` |
| `app.core.clock` | `core.clock` *(cockpit's own — shared, not vendored)* |
| `app.core.ids` | `core.ids` *(cockpit's own — shared, not vendored)* |
| `app.schemas.models.…` | `schemas.models.…` *(rebased onto the cockpit `Base`)* |
| `app.config.settings` | `config.settings` *(cockpit's own; see divergence 4)* |

**Do not restructure these files.** Their internal layout is upstream's on purpose: a
reorganised tree makes the re-sync diff unappliable, which destroys the only mechanism
keeping this copy honest. Cockpit-side adaptation belongs in the adapter that consumes this
package, not inside it.

## Deliberate divergences from upstream

Kept to the minimum, and each one recorded here rather than discovered later in a diff.
**No file under `app/filings/` itself was edited beyond the import prefixes** — every other
divergence below lives in the models, the settings, or the tests.

1. **Import prefixes** — the rename map above. Mechanical, applied by `sed`, nothing else.
2. **SQLAlchemy models are the cockpit's.** Upstream carries `app/schemas/models/` on its own
   `Base`. The seven models the core needs (`filing`, `delta`, `citation`, `company`,
   `xbrlfact`, `pollcursor`, `pollbaseline`) were copied into the cockpit's
   `schemas/models/` so `create_all`, the session factory, and the Inv-6 hash chain see ONE
   metadata, not two. None of their table names collide with an existing cockpit table.
   **Upstream filenames are kept** (`pollcursor.py`, not the cockpit's usual `poll_cursor.py`)
   so a future upstream field addition stays a one-line diff; cosmetic naming consistency is
   worth less than an appliable patch.
3. **`cite/digest.py` is vendored but unused.** It renders upstream's plain-text digest, which
   the cockpit's MorningBrief replaces. It stays because `cite/__init__.py` re-exports it and
   deleting it would fork the package's public surface for no gain.
4. **`sec_user_agent` is optional here, required upstream.** Upstream's process refuses to
   start without it — correct, because the brew IS the EDGAR app. The cockpit fails closed
   *per feed* instead (see `config/settings.py`, RW-10 block). This does not weaken SEC fair
   access: `filings/edgar/fetch.py:452` raises on a blank UA before building any request, and
   `:345` refuses a rate at or above the 10 req/s ceiling at construction. The control lives
   in the client, not in settings.
5. **Test-tree adaptations** (`app/tests/filings/`):
   - `test_wp4_structural.py` — `APP` is `parents[2]` (the file sits one level deeper) and
     the core dirs point at `app/filings/`. The LLM-free sweep was **widened** from upstream's
     `parse/ + delta/` to all four vendored packages plus `raw_store.py` and the package
     `__init__`, because the cockpit has an agent runner two imports away and upstream did not.
   - `_code_only()` was added to that sweep so it reads code rather than prose — widening it
     made `filings/edgar/companyfacts.py` fail for *documenting* that it spawns no subprocess.
     `test_llm_free_gate.py` (cockpit-authored) exists to prove that change caught nothing less
     than before.
   - Fixtures moved to `app/tests/filings/fixtures/sec/`. Both helpers derive their root from
     `Path(__file__).parent`, so **moving the data left their code byte-identical to upstream** —
     the deliberate choice over patching a path.
   - `test_wp3_poll.py` imports the cockpit's `schemas.models.audit_entry`. Upstream's audit
     was pattern-sourced from the cockpit's in the first place, so `AuditLog(sf, hmac_key=,
     clock=)` and `verify_chain()` line up without further change.
6. **NOT TAKEN, and the tests that go with them:** upstream's `test_wp5_fixture_run.py` and
   the WP6–WP9 suites exercise the brief composer, publish path and scheduler, none of which
   were vendored. Their absence is scope, not lost coverage.

## What must stay true

- **No model call, ever.** Nothing here may import `agent_runner`, `workflows`, or any
  model seam. Upstream guarantees this by construction (its LLM boundary lives in `brief/`,
  which was not taken); the cockpit *enforces* it with the `no-LLM-in-filings` gate, because
  a property that is only guaranteed by an absent directory is not guaranteed at all.
- **`filings/edgar/fetch.py` is the only module that may touch sec.gov** — upstream's rule,
  carried over. It declares a User-Agent and holds itself strictly under 10 req/s (the SEC
  maximum). No test and no build agent may make a live SEC call; fixtures only.
- **Filing bytes are write-once** (`raw_store.py`), which is what satisfies Inv 8 for this
  feed: raw filing bytes are stored separately from anything derived from them, so a delta
  can always be re-verified against the bytes it claims to describe.
