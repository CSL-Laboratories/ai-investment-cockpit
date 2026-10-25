# CSL-377 D5 canary fixtures

Prepare-only kit for the founder canary. See
[`../csl-377-canary-plan.md`](../csl-377-canary-plan.md) for the clause map
and [`PREFLIGHT.md`](PREFLIGHT.md) for launch gates.

| File | Role on the disposable PR |
|---|---|
| `canary.md` | Copy to `docs/canary-csl-377.md` (runbook §0; typo `recieve`) |
| `protected-path-file.md` | Copy to `docs/CODEOWNERS` (B1; not a GitHub owners policy) |
| `token-shape-parts.txt` | Keep as-is. Never concatenate in git (B2/B3) |
| `MENTIONS.md` | Founder-only `@claude` text |
| `PREFLIGHT.md` | Tick before the first mention |
| `conflict-main-side.md` | Text for the founder §3 setup PR on `main` (not this branch) |

Do not open this prep branch as the canary PR. Do not merge these files to
`main` except the separate, reviewed §3 setup PR after D5 go.
