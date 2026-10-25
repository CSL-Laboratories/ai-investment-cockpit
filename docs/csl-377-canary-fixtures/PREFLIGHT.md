# D5 canary pre-flight (copy at launch)

Tick every box before a founder posts the first `@claude` mention. This
checklist is prepare-only; launching still needs Lucas's own D5 go
([5ece9ff9](https://linear.app/csl-laboratories/issue/CSL-377#comment-5ece9ff9-d00e-4f7c-94b0-859ba26fb239)).

## Merges and SHAs

- [ ] Lucas posted an explicit D5 go on CSL-377 (this kit is not that go).
- [ ] `#260` is on `main`: merge `74b25244d0654f58ccbfc22ef7a59eaa85e182eb`
      (2026-10-25T03:45:29Z). Confirm:
      `git merge-base --is-ancestor 74b25244 origin/main`.
- [ ] `#264` (E3) merged to `main`. Record merge SHA: ________
- [ ] `#263` (E2) merged **after** #264. Record merge SHA: ________
- [ ] `#265` (E1) merged **after** #263. Record merge SHA: ________
- [ ] Post-stack `origin/main` SHA that will serve writer workflows: ________
- [ ] That `main` contains `.github/workflows/claude-conflict-writer.yml`
      and the E1 step named `Relay sandbox fd-2 (E1)` in `claude.yml`.
- [ ] Do **not** start while #264/#263/#265 are open. Do **not** push to them.
      Do **not** touch #187, #211, #226, #232, #235, #238.

## S1 hash re-check (D7)

Re-run on the frozen D10 packet the E-stack implementer used (CSL-450 comments
db07fbb4 and 77dfe070). Mismatch **stops E1 merge** and stops this canary.

```bash
sha256sum SHA256SUMS   # expect b1bca76ff74547891f39d0943bc2a822ab9a033fa539f9a0b8d8def097015dcb
sha256sum -c SHA256SUMS
sha256sum ADDENDUM-D10-L1-L2.md   # expect dad6fc56ab73821f949ac515904145e6885298881248baa1bbeec813fcf45d1c
sha256sum ADDENDUM-SHA256SUMS     # expect 7c3016baf3d58af5f625f14a3c887243f12ffda3d76afb553bb7fecebd989927
sha256sum REVIEW-D10.md           # expect 594ba774c40c117bb2111dd403110cb8c17a52a2b9dd25cebc734c8eefde47a6
sha256sum REVIEW-ADDENDUM-L1-L2.md # expect 424c76c8a968eb3e21fc6c21c8f5411eec4b3d91e12a476eeaec91bb96d2c371
```

`SHA256SUMS` must list:

```
177b5d6e09d47d8d334126e8bb4e43037f0ff0e55a953f0f32bc4fd85f66a5ed  DESIGN-E3-E2-E1-D10.md
4b23da1ea0d4533d5601ec5496a7466e65b39046e0cf9f3864202478642c2cbe  RESOLUTIONS.md
1dffeb5ee207b1c73a6c0218353d720cdb3bf73b7b74e4848309e757e5c6fad9  RECONCILIATION.md
4924755044701856ee66a1893ddf2d2ec51a8598d6d029f2b08a8a79d5889a6a  SOURCES.md
7e5fbd95417a802e7da9bcdfec11280998997fac0a780ed93c3d7a7660656562  GAPS.md
```

- [ ] All OK. Packet path: ________  Date: ________
- [ ] If E1 already merged, attach the S1 transcript that gated **that** merge.
      A missing S1 at E1 merge is a preflight fail.

These design files are not in ai-investment-cockpit. Do not reconstruct them
from workflow YAML and call that S1.

## Publisher hash (gates §3)

```bash
git show origin/main:scripts/ci/claude_fixer_publish.py | sha256sum
```

| Tree | sha256 of `scripts/ci/claude_fixer_publish.py` |
|---|---|
| #227 canary (`2162ff48`) | `cad2bbf1633ee241c27bd877a1e6e994eebb7d8380d80afa4c4a34ead5cddd95` |
| #260 `main` (`74b25244`) | `cd1ab4234edb098748fc24758f12422a3243f74d827a699a16496b5e67d0f452` |
| Launch `main` | ________ |

- [ ] Launch hash recorded.
- [ ] If it is not `cad2bbf1…`, §3 **must** run (this is already true at #260).
- [ ] If it is not the value recorded in this box, stop.

## Repo / PR hygiene

- [ ] `CLAUDE_FIXER_PAUSE` is unset or not exactly `true`.
- [ ] Kill switch known: set that variable, or label `hold` / `claude:pause`.
- [ ] Disposable PR will be **ready, not draft**, and will **not** be merged.
- [ ] Fixtures copied from this kit onto a **new** branch of post-stack `main`
      (not this prep branch, not #227).
- [ ] No `ci:full` on the disposable PR.
- [ ] No workflow_dispatch, no workflow edits, no secrets/settings changes.
- [ ] For §3: canary head is a **human** commit, or Devin has reviewed that
      exact SHA (CSL-478). Do not start conflict-scan on a bare `claude[bot]`
      head.
- [ ] A1 from #227 run 39681321026 is treated as carried; mention path, A2,
      B1, B2, B3, token-free origin, §1.7, and §3 will rerun.
