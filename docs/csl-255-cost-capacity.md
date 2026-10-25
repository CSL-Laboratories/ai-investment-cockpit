# CSL-255: measured cost of superseded Recursion CI runs

Read-only measurement for reviewers who want the actual cost and capacity
impact of obsolete / superseded CI **before** anyone builds a privileged
cancellation system (option C).

**Primary subject:** `CSL-Laboratories/recursion` `ci.yml` (settle,
linux-tests, linux) and `ci-full-label.yml` Windows/macOS tails.
**Secondary:** Cockpit run-level counts, plus job minutes only for Cockpit CI
runs that *completed* after a newer SHA appeared.

Retrieved 2026-10-25, 04:04–04:21 UTC, with `gh api` GET only. No
`workflow_dispatch`, reruns, cancels, PRs, or secrets.

This is not an invoice, not empirical PASS, and not authorization to change
workflows, permissions, or spend.

## Bottom line

Recursion’s native `concurrency` already cancelled every superseded `ci.yml`
pull-request run in the window. Those 31 runs still consumed **26 Blacksmith
minutes ($0.128 list price)** and 91 GitHub-hosted minutes before GitHub
stopped them — **1.88% of Recursion CI Blacksmith minutes** and **3.76% of
Recursion CI/full-label/nightly runner minutes**. Zero superseded Recursion CI
runs ran to success or failure. A privileged canceller cannot refund minutes
already started, so its incremental Blacksmith saving on Recursion CI is at
most the **10 Blacksmith minutes ($0.052)** that continued after the newer SHA
appeared. Against the operator-stated ~$80.49 month-to-date Blacksmith spend
and $100 monthly budget, that residue does **not** justify option C on cost.
The live Recursion issue is **same-SHA current-head cancellation** (21 `ci.yml`
PR runs), which is a correctness problem, not a bill.

## Window and what “paid” means

- Query window: runs **created** `2026-09-25T00:00:00Z` .. `2026-10-25T04:00:00Z`
  (30 days before retrieval).
- Recursion `ci.yml` did not exist before **2026-10-13T17:57:51Z** (commit
  `2b30d55a0b`). The first listed CI run is `2026-10-13T17:58:24Z`. The 30-day
  query is complete for this workflow; it is ~12 days of CI, not 30.
- **Blacksmith jobs:** any job whose Actions `labels` contain `blacksmith`.
  Observed: `blacksmith-2vcpu-ubuntu-2404`, `blacksmith-2vcpu-windows-2025`.
- **Claude / model-calling jobs** (from workflow YAML, not from invoices):
  Recursion `claude.yml` jobs `claude-mention` and `devin-review-fix`, and
  `claude-conflict-writer.yml` job `model`, all `runs-on: ubuntu-latest` and
  using `anthropics/claude-code-action`. `codex-gate.yml` posts `@codex review`
  from `ubuntu-latest`; it is not a Blacksmith job. Model/token dollars were
  **not measurable** from the Actions API.
- Current Recursion YAML (after CSL-408, commit `1942371732`, 2026-10-21
  00:53Z) has **no** Blacksmith labels. Historical YAML did: linux tests on
  `blacksmith-2vcpu-ubuntu-2404`, windows on `blacksmith-2vcpu-windows-2025`,
  macos always `macos-latest`.

**Triggers that start those paid Recursion jobs** (from the workflow files):

| Workflow | Events | Paid / heavy jobs |
| --- | --- | --- |
| `ci.yml` | `pull_request` (opened, synchronize, reopened, ready_for_review), `push` to `master`, `workflow_dispatch` | Historically Blacksmith linux/windows; now GitHub-hosted `linux-tests` / `linux` / optional windows+macos |
| `ci-full-label.yml` | `pull_request` `labeled` (`ci:full`) | reusable `ci-full-matrix.yml` windows+macos |
| `ci-macos-nightly.yml` | `schedule` 07:17 UTC | `macos-latest` |
| `claude.yml` | comments, reviews, `workflow_dispatch`, `repository_dispatch`, path-filtered `pull_request` | Claude Code action on `ubuntu-latest` |
| `claude-conflict-writer.yml` | `workflow_call` from conflict-scan | Claude Code action on `ubuntu-latest` (0 listed standalone runs) |

Recursion has **no** `pull_request_target` workflow runs in the window
(`total_count` 0). Cockpit `linear-pr-sync.yml` does use `pull_request_target`;
that is outside the Recursion primary set.

## Price basis

[Blacksmith pricing](https://www.blacksmith.sh/pricing) fetched 2026-10-25:

| Runner (2 vCPU, as used here) | USD / minute |
| --- | ---: |
| Ubuntu x64 | 0.004 |
| Windows x64 | 0.008 |
| macOS M4 (not used on Recursion CI) | 0.08 |

The page also advertises **3,000 free minutes / month**. This estimate does
**not** subtract them. [ci-cost-budget.md](ci-cost-budget.md) already records
that advertised free minutes must not be deducted without an invoice. Same
rounding as that ledger: each **started** job (non-null `runner_id`, or steps
that started) rounded **up** to a whole minute, minimum one.

Operator context (not from the API): monthly Blacksmith budget **$100**,
current spend about **$80.49** this month. That figure was not reproduced
here; it is a different window (calendar month, both repos, invoice) than
this 30-day Actions export.

GitHub-hosted `ubuntu-latest` / `ubuntu-24.04` / `windows-2025` / `macos-latest`
on these public repositories are **$0** runner compute (CSL-408 /
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)).

## Recursion CI / full-label / nightly (job-complete)

867 workflow runs, 867/867 job pages fetched, 0 missing.

| | runs | started jobs | GitHub-hosted min | Blacksmith min | Blacksmith $ (list) |
| --- | ---: | ---: | ---: | ---: | ---: |
| All three workflows | 867 | 1880 | 1732 | 1383 | 6.896 |
| `ci.yml` | 851 |  |  |  |  |
| `ci-full-label.yml` | 6 | 8 | 11 | 6 | 0.048 |
| `ci-macos-nightly.yml` | 10 | 17 | 24 | 1 | 0.004 |
| of which `pull_request` | 748 |  |  |  |  |
| `ci.yml` pull_request only | 742 |  |  |  |  |
| `push` / `workflow_dispatch` / `schedule` | 93 / 16 / 10 |  |  |  |  |

`ci.yml` PR conclusions: 544 success, 129 failure, 64 cancelled, 5 skipped.

Blacksmith minutes by label: 1042 Linux 2 vCPU + 341 Windows 2 vCPU.
List price: `1042 × 0.004 + 341 × 0.008 = $6.896`.

All 867 runs were created in **October 2026** (CI starts 2026-10-13).
Split at CSL-408 (`1942371732`, 2026-10-21 00:53:28Z), by run `created_at`:

| Era | runs | GH min | Blacksmith min | Blacksmith $ |
| --- | ---: | ---: | ---: | ---: |
| Before CSL-408 | 698 | 836 | 1352 | 6.728 |
| After CSL-408 `created_at` | 169 | 896 | 31 | 0.168 |

The 31 post-cutover Blacksmith minutes are not a leak of current `main` YAML.
They are jobs whose **run used the workflow file from that ref**: master
pushes and open PRs still on pre-408 YAML (linux-tests / windows still
`blacksmith-2vcpu-*`). One later PR (`samirshahahmed/csl-307-perf-ci-finite-env`,
run 39659518028, 2026-10-21 19:12Z) still ran `linux` on Blacksmith. Current
`main` YAML has no Blacksmith labels.

Nightly’s 1 Blacksmith minute is `recent-commits` on
`blacksmith-2vcpu-ubuntu-2404` on 2026-10-15 only; later nights are
`ubuntu-latest`.

### Superseded Recursion CI runs

**Definition used (matches the request):** a `ci.yml` `pull_request` run for
which a later run on the **same head branch** with a **different** `head_sha`
was created after this run’s `created_at` and before this run finished
(last job `completed_at`, else `updated_at`).

608 of 742 PR CI runs had an empty `pull_requests` payload, so grouping is by
`head_branch` (128 branches), not PR number. Two PRs reusing one branch name
would be merged; none was observed as a collision in this export.

| | count |
| --- | ---: |
| Superseded `ci.yml` PR runs | **31** |
| of those, conclusion `cancelled` | **31** |
| of those, success or failure | **0** |

Minutes on those 31 runs (jobs that got a runner, including work *before*
the newer SHA):

| | GitHub-hosted min | Blacksmith min | Blacksmith $ |
| --- | ---: | ---: | ---: |
| Whole superseded run | 91 | 26 | 0.128 |
| Overlap *after* the newer SHA’s first CI `created_at` | 40 | 10 | 0.052 |

Share of the 867-run CI/full-label/nightly totals:

- 117 / 3115 runner minutes = **3.76%**
- 26 / 1383 Blacksmith minutes = **1.88%**
- $0.128 / $6.896 list = **1.86%** of this Recursion CI estimate
- $0.128 / ~$80.49 stated MTD spend ≈ **0.16%** (different window; not an
  invoice reconciliation)

By job name on the 31 runs (Blacksmith and GitHub-hosted mixed): preflight 20,
settle 39, linux 35, Linux tests 9, macos 8, windows 6. Settle is the
intentional 120 s debounce on `ubuntu-latest` ($0).

Heaviest branches (superseded run count):
`lucassalvador987/csl-385-ci-event-contract` 8, then three branches with 3
each.

### What native `concurrency` already cancels

Recursion `ci.yml` throughout this window:

```yaml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

Same pattern on `ci-full-label.yml`, `ci-macos-nightly.yml`, `pr-lint.yml`,
and `linear-pr-sync.yml`. `codex-gate.yml` cancels in-progress on
`pull_request` but not on `pull_request_review`. Claude fixer jobs use
`cancel-in-progress: false`. Recursion has **no** equivalent of Cockpit’s
default-branch `cancel-ancestors` controller.

Observed on `ci.yml` pull_request:

| | count |
| --- | ---: |
| Cancelled PR CI runs | 64 |
| … with a later run on the same branch | 59 |
| … with no later run on the same branch | 5 |
| Superseded (different SHA while still running) | 31 — all cancelled |
| Same-SHA overlap cancel (newer run, same `head_sha`, before finish) | 21 |
| Later different SHA only *after* this run finished | 7 |

Job-level: 252 cancelled jobs across the 867 runs; **209 never got a
`runner_id`** (cancelled while queued — $0). 43 got a runner, of which 20
were Blacksmith. Native concurrency is already dropping most superseded work
before a runner starts.

Other Recursion workflows (run conclusions only; **job minutes not
fetched**):

| Workflow | runs | cancelled | notes |
| --- | ---: | ---: | --- |
| PR Lint | 1219 | 137 | `cancel-in-progress: true` |
| Linear PR sync | 751 | 37 | `cancel-in-progress: true` |
| Codex gate | 1771 | 28 | 1256 skipped |
| Stuck-thread watch | 3610 | 466 | `cancel-in-progress: false`; cancel *cause* not measured |
| Claude PR Fixer | 4550 | 62 | 2578 skipped; see model note |
| review-gate | 33 | 3 | short-lived / sparse |
| conflict-scan | 120 | 1 | |
| Claude conflict writer | 0 |  | `workflow_call` only |

Claude listing: 4550 runs after filling an Oct 15–17 page-cap gap with 6-hour
windows (count matched the independent per-workflow `total_count` sum).
Events: 1913 `pull_request_review_comment`, 1679 `pull_request_review`, 779
`issue_comment`, 177 `pull_request`, 2 `push`. Runner compute for the model
jobs is GitHub-hosted `$0`. **Claude/Codex token cost: unmeasured.**

### Queue and contention

For all 742 `ci.yml` PR runs, `run_started_at - created_at` was **0 seconds**
(p50/p90/p99/max). GitHub did not delay admitting the *workflow run*.
**Job-level queue wait is unmeasured** (the jobs API export used here has no
job `created_at`).

Concurrent *started* jobs (sweep of job `started_at`/`completed_at` on the
867 runs):

| | peak | when (UTC) |
| --- | ---: | --- |
| Blacksmith | 10 | 2026-10-16T02:21:31Z |
| GitHub-hosted | 4 | 2026-10-21T00:04:40Z |
| All started jobs | 12 | 2026-10-16T02:22:15Z |

Only one cluster met “hot” (Blacksmith ≥ 8 or all ≥ 20): **2026-10-16
02:21:06Z–02:22:18Z**, max 10 Blacksmith + 3 GitHub-hosted = 12. That is
burst parallelism across PRs, not a long queue. Recursion CI does not set
`queue: max`; `cancel-in-progress: true` keeps one in-flight run per PR.

## Cockpit (secondary, cheaper collection)

Not job-complete. Run metadata only for `ci.yml` (1552 runs) and `rust.yml`
(848), plus jobs **only** for the 91 Cockpit `ci.yml` PR runs that
*completed* (success/failure) after a newer SHA appeared.

| | `ci.yml` | `rust.yml` |
| --- | ---: | ---: |
| Runs in window | 1552 | 848 |
| `pull_request` | 1401 | 759 |
| PR cancelled | 221 | 25 |
| Superseded PR runs (same definition, by `head_branch`) | 267 | 26 |
| … cancelled | 176 | 15 |
| … success | 81 | 11 |
| … failure | 10 | 0 |

Cockpit `ci.yml` currently groups by PR and sets
`cancel-in-progress` **false** for `pull_request` (ordinary runs queue;
the default-branch companion may cancel verified ancestors). Recursion’s
native cancel is the opposite default.

**Job minutes for the 91 completed-superseded Cockpit CI runs** (the waste
native Recursion-style cancel would have truncated):

| | |
| --- | ---: |
| Started jobs | 541 / 687 listed |
| GitHub-hosted minutes | 319 |
| Blacksmith minutes | 1057 priced + **6 unpriced** |
| Blacksmith list $ | **4.992** |

Priced labels: 854 min `blacksmith-2vcpu-ubuntu-2404` + 197 min
`blacksmith-2vcpu-windows-2025`. **Unpriced:** 6 min
`blacksmith-6vcpu-macos-15` (the public pricing page lists macOS M4 at
$0.08/min; this export does not map that 6 vCPU label to a rate).

Calendar split of those 91 (cut `2026-10-19T17:52Z` ≈ Cockpit #166 merge
“10:52 AM PT”; CSL-408 `2026-10-21T00:53Z`):

| When the superseded run was created | n | Blacksmith $ |
| --- | ---: | ---: |
| Before #166 grouping fix | 89 | 4.936 (+ 6 unpriced min) |
| Between #166 and CSL-408 | 1 | 0.056 |
| After CSL-408 | 1 | 0.000 |

Cockpit **total** CI/rust minutes, cancelled-superseded minutes, and
Blacksmith $ for the other 1,461 `ci.yml` runs were **not fetched**. Do not
scale the $4.992 to “all Cockpit CI”.

## Unmeasured (do not invent)

- Blacksmith invoice, credits, and whether the 3,000 free minutes applied.
- Claude / Codex **token** dollars. Subscription OAuth; Actions has no
  usage field for that.
- Job minutes for Recursion workflows other than CI / full-label / nightly.
- Job minutes for Cockpit except the 91 completed-superseded `ci.yml` runs.
- Job-level queue wait.
- Who cancelled a run (concurrency vs human vs another API client). GitHub
  does not put that on the run list used here.
- Recursion CI before 2026-10-13 (workflow absent).
- `blacksmith-6vcpu-macos-15` dollars (6 minutes, Cockpit only).
- Whether the stated $80.49 includes Docker-layer / sticky-disk add-ons,
  both repos, or Codesmith (Linear already records Lucas: GitHub Actions
  usage, 0 Codesmith).

## Method (reproducible)

All calls are `gh api -X GET`. No POST/PATCH/DELETE, no `workflow_dispatch`.

1. List workflows:
   `GET /repos/CSL-Laboratories/recursion/actions/workflows`
   (same for `ai-investment-cockpit`).
2. Count then list runs in **2-day `created=` windows** (6-hour windows for
   Recursion `claude.yml` on 2026-10-15..17) because the runs list caps at
   1,000 results per query:
   `GET /repos/{owner}/{repo}/actions/workflows/{id}/runs?per_page=100&page=N&created={from}..{to}`
3. For every Recursion CI / full-label / nightly run, fetch
   `GET /repos/.../actions/runs/{id}/jobs?per_page=100&filter=all`
   (attempts > 1 would use `/attempts/{n}/jobs`; every run in this dump had
   `run_attempt` 1).
4. A job counts toward minutes only if `conclusion != skipped` and it has
   `started_at` and (`runner_id` not null/0 or `steps` with `started_at`).
   Duration `ceil((completed_at - started_at) / 60)`, minimum 1.
5. Blacksmith vs GitHub-hosted is the job `labels` array, not current YAML.
6. Superseded: among `event=pull_request` runs of that workflow, group by
   `head_branch`; flag run A if some run B has `A.created_at < B.created_at
   < A.finished` and `A.head_sha != B.head_sha`.
7. Native cancel: `conclusion=cancelled` plus whether a later same-branch
   run exists. Same-SHA overlap is reported separately from superseded.
8. Contention: sweep-line on started jobs’ `[started_at, completed_at)`.
9. Cockpit: same run listing for `ci.yml` (id 308361820) and `rust.yml`
   (id 385034450); jobs only for the 91 completed-superseded `ci.yml` PR
   runs.

Workflow ids used (Recursion): CI `383505883`, CI full label `386345185`,
CI macos nightly `384694109`, Claude PR Fixer `384658917`, Codex gate
`385260905`, PR Lint `384964726`, Linear PR sync `385160650`, stuck-thread
`385157671`, conflict-scan `385138459`, review-gate `387067983`.

Independent count check: summing `total_count` over 2-day windows for
Recursion CI gave 851, matching the listed 851 runs.

## Option C

Option C is a privileged canceller (Actions write, cancel only verified
obsolete runs). Cockpit already has a bounded default-branch controller;
Recursion does not.

On Recursion CI, native `cancel-in-progress: true` already cancelled 31/31
superseded PR runs. Incremental list-price Blacksmith left on the table is
**$0.052–$0.128** in this window (minutes after the newer SHA, vs the whole
superseded run). That is not material against a $100 budget or ~$80.49 MTD.
Building option C for Recursion is **not justified by cost**. If it is built,
the reason would have to be **same-SHA current-head cancellation** (21 runs)
or queue correctness, which this export does not price.

Cockpit’s historical completed-superseded CI (almost all before the #166
grouping fix) was **$4.992** Blacksmith on 91 runs. That is the cost native
PR-number cancel-in-progress would have truncated *had those runs used
today’s Recursion concurrency*. It is still small versus MTD spend, and
post-CSL-408 the same pattern is $0 runner compute on these public repos.
Cockpit total CI minutes remain unmeasured here.
