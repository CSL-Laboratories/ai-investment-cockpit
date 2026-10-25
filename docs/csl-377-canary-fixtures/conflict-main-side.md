# §3 setup (founder PR to main, after D5 go)

Not applied on this prep branch. After the disposable canary PR exists and
is on a human (or Devin-reviewed) head:

1. Open a **separate** PR against `main` that:
   - changes the last sentence of `docs/canary-csl-377.md` so it no longer
     matches the canary PR (same-line conflict);
   - appends this line to `.gitattributes`:

     `docs/canary-csl-377.md conflict-marker-size=10`

2. Review and merge that PR to `main`. The push triggers `conflict-scan`.
3. Leftover-marker refusal is offline-only (runbook §3).
4. Close the setup PR's follow-up cleanup only after evidence is on CSL-377,
   the same way Trial E's export-doc cleanup was handled. Never merge the
   canary PR.

Suggested replacement sentence on `main` (must differ from the canary PR):

```
The conflict trial changed this sentence on main so that the disposable canary PR conflicts here.
```
