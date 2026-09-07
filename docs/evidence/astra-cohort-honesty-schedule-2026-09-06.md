---
title: Cockpit Admin — cohort honesty and intake schedule
type: evidence
status: recommendation-only-routines-unchanged
date_pt: 2026-09-06
screen_cutoff_utc: 2026-09-04T12:35:34.147997+00:00
scope: W-24 research intake scheduling; unchanged eligibility and authority gates
---

# Cohort honesty: change when work runs, preserve what qualifies

**Recommend filing-triggered intake with a weekday 05:30 PT discovery and coverage
backstop. Repeat the full semantic screen only when changed or uncertain evidence
requires it.**
Refresh discovery and absolute-head metadata; repeat substantive parsing when the
source set, source bytes, identity binding, or approved parser changes. A new
date-ready source set earns an evidence review, never automatic selection or a
watchlist entry.

Adopt this in stages. The daily conditional check is the immediate operational
recommendation. Intraday filing hints are the target schedule **after** a bounded
detector demonstrates delivery, invalidation and recovery. Retain manual coverage
until actual wakes and completed artifacts establish reliability. A pure
event-only replacement is not supported by the evidence available here.

**Monday, September 7 is an EDGAR holiday.** Use its proposed 05:30 wake for status
and any outstanding Friday catch-up, not an expected new-filing selection shot.
Tuesday, September 8 is the next ordinary EDGAR business day. Previously published
filings remain accessible on the holiday; a delayed discovery/backfill is still
possible. The September 4 note's “next market-day” description of Monday is
incorrect. [SEC 2026 EDGAR calendar](https://www.sec.gov/submit-filings/filer-support-resources/edgar-calendar)

This document changes no routine, application scheduler, parser, watchlist, soak,
mode, production evidence store, Discord message or order. It is not a fresh
September 6 issuer census. The zero below belongs to the completed September 4
screen. Research began at 2026-09-07 00:45:56 UTC, or September 6 17:45:56 PDT;
the requested approximately 20:15 PT deadline is September 7 03:15 UTC.

## What the September 2–4 evidence establishes

Counts below were checked against the original intake JSON in
`/workspace/w24-evidence/`, rather than inferred from report titles. “Unique CIKs”
is an additional calculation from the individual screen rows.

| Metric | Sep 2 | Sep 3 | Sep 4 |
|---|---:|---:|---:|
| Discovery ticker rows screened | 190 | 191 | 192 |
| No current SEC ticker mapping | 51 | 50 | 50 |
| Unsupported absolute-head form | 101 | 108 | 112 |
| Supported absolute-head ticker rows | 38 | 33 | 30 |
| Unique CIKs among supported rows | **32** | **30** | **27** |
| Cash-hit ticker rows | 2 | 2 | 0 |
| Selection-ready, as defined by the screen | **1: BHF** | **1: BHF** | **0** |
| Date-ready current-target candidates | **0** | **0** | **0** |
| Minimum-30 gate met | No | No | No |
| FTS reported hits / retrieved hit rows | 595 / 200 | 613 / 200 | 617 / 200 |

Sources: [Sep 2 report](/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-02.md),
[Sep 3 report](/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-03.md),
[Sep 4 report](/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-04.md).

The 30 supported rows on Friday are **not** a nearly qualified 30-deal cohort.
CYCU/CYCUW, FGRS/FIGR, and STRR/STRRP each share a CIK. Even the form-only population
contains only 27 distinct CIKs, before common-equity, cash, lifecycle and other
refusals. The same BHF observed on two days is one identity, not two prospective
observations toward the minimum.

BHF's original cash-bearing source was 10-Q `0001685040-26-000028`; its later
supported current head was 8-K `0001685040-26-000031`. On September 4, submissions
instead placed Form **3** `0001628280-26-060470`, filed September 3, at the absolute
head. BHF therefore left selection-ready under the existing rule. SAFT's head
changed from 10-Q `0001172052-26-000023` to **PREM14A**
`0001104659-26-105083`. SAFT had already failed eligibility despite its cash hit.
These head identities agree with all six corresponding archived BHF/SAFT
submissions snapshots, not just the markdown summaries.

The [BHF date audit](/workspace/w24-evidence/w24-bhf-date-saft-audit-2026-09-02.md)
separately found no unique pinned expected-resolution or outside date. Its
conditional September 6/December 6 extension language and year-only closing
expectation do not become qualifying date assertions as time passes. September 6
is not an intake unlock or an ETA. A future supported source set must actually
supply the facts accepted by the unchanged parser.

There is useful work to skip, but its value should be measured honestly:

| Rechecked work evidence | Sep 3 | Sep 4 |
|---|---:|---:|
| Unique primary documents in fetch plan | 341 | 311 |
| Already cached at planning time | 320 | 299 |
| New primary documents fetched | 21: KR/TXNM | 12: USAR |
| Bytes in those new-document directories | 14,903,744 | 2,472,391 |
| Local document bytes scanned, as reported | 222,374,887 | 183,560,177 |

Hashing the archived sources confirms that **29 of the 30 Friday supported ticker
rows, representing 26 CIKs, had exactly the same absolute head and bounded source
bytes as Thursday**. USAR was the sole new supported row; AON, BHF, CHTR and SAFT
left. All 377 Thursday and 347 Friday planned ticker/document references resolved
locally, representing 341 and 311 unique documents respectively. This checks the
bounded plans, not the complete history needed for production authority.

September 3–4's `bytes_received_estimate` field actually accumulates locally read
document bytes in the offline script. It is **not network traffic**. Their
recorded offline semantic phases lasted about 3.86 and 3.05 seconds. The larger
report windows include acquisition and operator activity; they do not measure
active human minutes. The proposed benefit is less repeated review and better
change detection, with no demonstrated dollar or net-time saving yet.
Sources: [offline script](/workspace/w24-evidence/_offline_semantic_2026_09_04.py),
[Sep 3 fetch plan](/workspace/w24-evidence/rescreen-raw-2026-09-03/doc-fetch-plan.json),
[Sep 4 fetch plan](/workspace/w24-evidence/rescreen-raw-2026-09-04/doc-fetch-plan.json).

## Preserve these distinctions and gates

The historical screen defines `selection-ready` as cash-strategy eligibility on
the combined source set plus `current_matches_set_end`. Its `date-ready` count adds
both unique parsed dates. Neither metric certifies signed sources, complete
coverage, fresh quotes, witness chronology, calibration or execution readiness.

The production manifest requires **at least 30 distinct eligible identities on one
selection date**, with uniqueness across deal, symbol, CIK and source/current
accession fields. Current-target dates are a separate downstream requirement; the
contract is not “30 simultaneous date-ready orders.” Preserve both requirements,
the 30-day strategy horizon, held-out precision, complete signed sources,
prospective witness and outcome chronology, and every later risk/mode/human gate.
Do not accumulate 30 historical arrivals across dates or reuse rejected rows to
make the current manifest pass.
[W-24 contract](../runbooks/w24-merger-semantic-verifier.md),
[selection normalization](../../app/strategies/merger_semantics.py),
[selection-to-order map](rw20-selection-order-map-2026-09-06.md).

The verifier derives the selection date from a **UTC** commitment timestamp. PT
is the operational schedule timezone; it does not redefine that date. Retain both
UTC and PT receipt times, especially for evening PT checks that cross UTC midnight.

“Wait for a cash 8-K/10-Q/10-K with pinned dates” means a supported **absolute** head
and a complete admissible source set that provides the cash and both dates. It
does not require one new filing to restate everything if the existing source-set
contract permits earlier supported sources. Conversely, an old eligible 8-K cannot
be selected by skipping a newer Form 3, 4, proxy or amendment. No form expansion,
exhibit substitution, inferred calendar date or hand-entered label is proposed.

The last completed screen says **0 selection-ready / 0 date-ready**. When intake
has not completed or coverage is degraded, report that zero with its old as-of
timestamp and show current readiness as **unverified**. Do not advance its date
because a cron tick ran. Keep `COCKPIT_MERGER_WATCHLIST` empty; no filler or candidate
seed is authorized by this recommendation.

## Compare the scheduling choices

| Choice | What it accomplishes | Limitation | Decision |
|---|---|---|---|
| Monday-only full re-screen, `30 5 * * 1` | One scheduled discovery and semantic snapshot | Can miss the rest of the week's changes; a holiday run has no ordinary new filing intake | Replace as the sole intake mechanism |
| Weekday full re-screen, `30 5 * * 1-5` | Refreshes the bounded universe and detects supported-head losses by the next completed morning run | Repeats unchanged parsing/review; overnight, weekend and holiday gaps; cannot create missing dates | Retain as fallback when change detection is unverified |
| Filing-triggered only | Can react to changes before the next morning and coalesce duplicate work | Unknown feed delivery/coverage, outages, new-issuer discovery and time-based freshness can leave blind spots | Do not adopt alone |
| Filing-triggered plus daily conditional backstop | Changed-source work intraday; daily discovery, head reconciliation, health and date/horizon checks | Requires a proven bounded detector and measured operating cost | **Recommended staged target** |

Morning screens did provide real negative information: BHF's loss of eligibility
is a reason to retain metadata reconciliation, even when no candidate qualifies.
Triggering only on cash-positive filings would miss precisely that event. Trigger
revalidation on **every form** for known research CIKs, including ownership forms;
only supported absolute heads proceed to semantic eligibility.

As a schedule-only illustration, BHF's archived Form 3 acceptance timestamp is
`2026-09-03T20:17:09Z`. A proposed 13:37 PT check would be 19m51s later; Friday's
recorded screen completion was 16h18m25s later. That is potential notification
latency under successful dissemination and detection, **not a measured feed SLA**,
a date-ready arrival, or an earlier selection commitment.

## Concrete proposed cron shapes

Use five-field cron, with **America/Los_Angeles explicitly configured in the
scheduler**. The table describes jobs for Admin to review; these are not installed
commands, and the intraday worker is not asserted to exist.

| Proposed job | Cron shape in PT | Payload and condition |
|---|---|---|
| Daily intake backstop | `30 5 * * 1-5` | Refresh declared discovery and all-form submissions metadata; reconcile gaps and changed CIK bindings; parse changed/uncertain supported source sets; refresh clock-dependent status. On SEC holidays, record holiday/status and handle outstanding catch-up without claiming fresh filing intake. |
| Morning receipt check | `45 5 * * 1-5` | Inspect expected start, active run/lock, completion, coverage and artifact timestamp. A started/running scan is distinct from a completed scan. |
| Intraday filing-hint checks, **after detector acceptance** | `7,37 3-18 * * 1-5` | Bounded metadata-only discovery-hint polling on SEC business days, 03:07–18:37 PT. Coalesce changed issuer/accession hints into the same GET-only intake path. No model wake for an unchanged tick. |
| Final filing-hint check, same condition | `7 19 * * 1-5` | 19:07 PT drain; unfinished or delayed work remains visible for the daily backstop. |
| Weekly evidence/cost review | `0 6 * * 5` | Review the last completed coverage intervals, unique arrivals, losses, refusals, wake misses, request bytes and active review minutes. This is a review of dated evidence, not a fresh census or selection attempt. |

The intraday pair makes **33 checks per normal SEC business day** and covers the
ordinary 06:00–22:00 ET filing window, rather than stopping at the equity-market
close. Delayed dissemination and overnight index construction still require the
next morning's backstop. Follow the SEC calendar, not just a stock-exchange
calendar: their closed days can differ.
[SEC business hours and index timing](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)

For a Cronie installation that supports it, `CRON_TZ=America/Los_Angeles` expresses
the table's timezone. Do not assume that environment syntax configures the Admin
routine service. Cron expressions alone have no timezone or holiday rule; a
business-calendar check belongs in the payload. A Monday holiday does not move
`30 5 * * 1` to Tuesday automatically. Avoid calendar-pin pseudo-one-shots and
combined day-of-month/day-of-week tricks: ordinary cron uses OR semantics for
restricted day fields, and a five-field calendar pin repeats annually.
[Cronie crontab specification](https://raw.githubusercontent.com/cronie-crond/cronie/master/man/crontab.5)

For timezone verification, Tuesday September 8 at 05:30 PT is **12:30 UTC**;
Monday November 2 at 05:30 PT is **13:30 UTC**. Fixed UTC copies drift after the
November transition; evening PT checks also land on the next UTC calendar day.
Do not transplant numeric day-of-week fields into the application's APScheduler:
its CronTrigger uses Monday as day zero, unlike ordinary cron. The proposed table
does not alter the application's existing five-minute EDGAR ingest schedule.
[APScheduler CronTrigger documentation](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html),
[application scheduler](../../app/orchestrator/scheduler.py).

### Existing routine and immediate handoff

A read-only inspection found this actual Admin entry:

- [mon-w-24-natural-head-rescreen/automation.json](/home/box/agent-data/agents/44671ce8-dc1d-4413-9b75-b176a5943ecf/automations/mon-w-24-natural-head-rescreen/automation.json)
- `name`: `Mon W-24 natural-head rescreen`; `schedule`: `30 5 * * 1`;
  `enabled`: `true`; `lastRunAt`: `null`.
- Its nested trigger repeats that expression; the file contains no explicit
  timezone field. The intended PT reading comes from the ops notes, not this
  JSON alone. The pending Monday run is in the future at inspection; its null
  timestamp is **not another observed miss**.
- Observed SHA-256:
  `b0f42720de88cd91d8cd937461becbe2f04c436c1c55e29c56f6623e73c76700`.

Recommend that Admin replace that lone full-scan payload with the daily conditional
backstop and add the receipt check after reviewing timezone and payload semantics.
Until a safe conditional path is available, use the existing bounded GET-only
screen as the weekday fallback. The dated scripts contain hard-coded dates and
paths; do not paste an old script unchanged into a perpetual cron job.

On September 7, review Friday backlog and schedule health. Start ordinary weekday
intake September 8. If a scheduled morning job has no start receipt by about 05:40,
Admin provides the hand-run after checking the run lock. At 05:45, distinguish
running, completed, failed and never-started; do not launch another scan just
because a longer run has not finished. Specify a duration budget when the payload
is adopted. Retire any replaced one-shot only as part of Admin's later schedule
change; do not let both old and new payloads fire.

The [wake postmortem](/workspace/w24-evidence/wake-miss-postmortem-2026-09-02.md)
records two historical misses and explicitly leaves weekday-cron reliability
unproved. A nicer cron expression does not fix that evidence gap. `lastRunAt`
alone also cannot prove successful SEC intake.

## Filing-trigger contract and practical limits

Use the same declared research discovery population, retaining rejected and
unmapped identities for reconciliation. Its membership is separate from all
execution watchlists. BHF-only or currently-positive-only monitoring would create
selection bias and miss names such as Friday's newly discovered USAR.

The SEC documents Latest Filings RSS filters, including ownership inclusion, and
real-time per-CIK submissions JSON. A filing hint should lead to a fresh submissions
check and primary-source retrieval; it is never itself eligibility authority.
Submissions histories include links to additional history when needed. These
interfaces are discovery and source retrieval mechanisms, not a cash/date webhook.
[SEC RSS documentation](https://www.sec.gov/about/rss-feeds),
[SEC submissions API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

This research verified the documentation but **did not validate a live Atom
detector**: the browser tool could not safely open the attempted `getcurrent`
Atom URL. That tool failure is not evidence of an SEC outage. Feed pagination,
identity attribution, ownership coverage, latency and loss recovery still need
bounded capture/replay evidence. In particular, do not identify an issuer from
the accession prefix: a filing agent can own that prefix. Ownership hints must
resolve the affected issuer, rather than assuming a reporting owner's CIK is the
research issuer. Until that is proved, the daily all-form submissions check is
the backstop for a BHF-style invalidation.

The proposed handler should:

1. Preserve raw hint/submissions responses and receipt times; deduplicate by issuer
   binding and accession, with a durable completed checkpoint. A seen hint is not
   completed intake. Recover overlaps and gaps, including multiple same-day filings.
2. Refresh all-form absolute-head metadata for changed known CIKs. Ownership,
   proxy, amendment, identity-remapping and correction events invalidate cached
   readiness even when they contain no merger keywords. A newly supported head
   must be checked for all intervening supported sources, not just its own cash text.
3. Reuse semantic results only when the ordered source identities, raw hashes,
   parser artifact and scan-policy version agree. Refresh mutable head/coverage
   evidence separately; preserve corrected byte versions. A missing cache or an
   incomplete source interval requires work or an explicit refusal.
4. Recompute cheap date/horizon/freshness status in the daily check even without a
   filing. An already parsed exact date can enter or leave the 30-day horizon as
   time passes; BHF's absent dates cannot. Cached parsing never renews authority TTLs.
5. On a new eligible, date-ready source set, produce one hash-bound evidence packet
   for Admin: issuer, exact absolute head, complete source references, parsed cash,
   exact date spans, exclusions/refusals, parser pin and coverage timestamps.
   Recheck the head before reporting it. Continue the cohort census and leave
   selection, witness publication and watchlist configuration gated.

The existing `sec:edgar` schedule follows the application's configured awareness
universe; it is not evidence that the external W-24 discovery pool has event
coverage. A new detector or broader source collector needs the separately scoped
evidence/source package required by [PLAN, W-24 and RW-13](../PLAN.md). This schedule
recommendation neither implements RW-13 nor routes events toward an order.

Do not label a 33-times-daily sweep of every research CIK “cheap event intake.”
Friday had 127 unique mapped CIKs and 15,456,563 bytes of archived submissions JSON.
Repeating that entire snapshot 33 times would mean **4,191 submissions GETs and
about 510 MB of response bodies per day**, before discovery and primary documents,
assuming the same response sizes and no compression savings. This is an illustrative
budget calculation, not observed traffic. Prefer bounded hints plus changed-CIK
retrieval and one daily census; measure before choosing a faster cadence.

Preserve the fetcher's 403/429 halt with no hot retry, persistent degraded status,
bounded work and the shared SEC request budget. A fresh cron invocation must not
erase a halt. The SEC's ceiling is 10 requests/second per user across machines;
it is a maximum, not a target. Keep a declared identifying User-Agent and account
for the running soak's traffic too.
[SEC fair-access guidance](https://www.sec.gov/about/developer-resources),
[existing fetcher](../../app/filings/edgar/fetch.py).

## Risks, acceptance evidence and review point

| Risk | Required response |
|---|---|
| **Bounded discovery mistaken for a complete market census** | The old FTS recipe searches 8-K cash-merger language and retrieves only 200 rows, with duplicate accessions; Friday returned 187 distinct accessions out of 617 reported hits. It can miss older hits, alternate wording and periodic-only discoveries outside known names. Publish these limits. Any expanded research census needs a declared source/window/coverage contract; it grants no wider eligibility. |
| **Short source window mistaken for complete authority** | The old scripts inspect at most 12 supported 2026 primaries. A missing older source is not an absence proof. Retrieve and bind the complete required interval before a selection or current-target authority claim. |
| **RSS tail overflow, outages, late dissemination or corrections** | Retain daily submissions/discovery reconciliation, bounded overlap and explicit gap status. Never advance a completed checkpoint across failed pages. Nightly indexes can assist backfill, but later corrections can require the SEC's weekly rebuilt full/quarterly indexes; a daily index alone is insufficient. |
| **Unsupported-head invalidations missed** | Include ownership and other forms in change detection for known CIKs; use fresh submissions as the final head check. Preserve the BHF Form 3 and SAFT PREM14A transitions as regression cases. |
| **Routine fires mistaken for completed work** | Record expected/start/end times, outcome, coverage and artifact hashes. Keep manual coverage and one shared run lock. Do not backdate a successful scan or count a holiday skip as fresh coverage. |
| **Activity mistaken for progress or savings** | Track distinct source changes and unique eligible arrivals/losses, not wake count, ticker aliases or repeated BHF parses. Measure active review time and request bytes; parser CPU was already short. |
| **One success used to waive remaining gates** | A date-ready arrival changes one research metric. Same-date minimum 30, independent precision, signed complete sources, prospective chronology, mature calibration, current-target checks and later authority remain mandatory. Watchlist stays empty under this task. |

The correction/index distinction above is documented by the
[SEC's post-acceptance correction guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

Before intraday adoption, demonstrate on frozen responses that the detector plus
backstop recovers every in-scope accession in its declared comparison population,
including non-cash invalidations; records zero-result and incomplete intervals
honestly; and does not dispatch duplicate substantive work after restart. Check
same-day multiple filings, lost checkpoints, page caps, 403/429, parser-version
changes, the two archived head losses, and PT/UTC weekday conversion across DST.
Replay success is apparatus evidence, not proof of global discovery recall or
held-out semantic precision.

If adopted for September 8–14, review after those **five SEC business days** and
the following overnight reconciliation, at September 15 approximately 06:00 PT.
If implementation starts later, move the trial window explicitly. Compare
new/changed source sets, unique eligible/date-ready identities, unsupported-head
losses, incomplete intervals, detection delay, wake misses, active review minutes
and bytes with the daily fallback. Retain the fallback if coverage or net operating
benefit is unproved. A trial with no qualifying arrivals supports continued
fail-closed waiting, not a relaxed bar or an estimated qualification date.

Attainability of 30 same-date eligible identities remains **unproved**. Revisit
experiment feasibility in a separately reviewed decision if the measured intake
remains empty; no rolling-cohort redesign is part of this schedule change.
This makes the earlier [cohort/value recommendation](../research/gate-feasibility-and-value-2026-09-06.md)
operationally specific without changing its evidence claims.

## Evidence identity and verification scope

Original intake JSON SHA-256 values, computed from disk:

| Source | SHA-256 |
|---|---|
| [Sep 2 JSON](/workspace/w24-evidence/w24-prospective-intake-natural-head-rescreen-2026-09-02.json) | `39bd9c75af11e51cb5df81a572243670fc917e42c9edd32e98b8229492b802ce` |
| [Sep 3 JSON](/workspace/w24-evidence/w24-prospective-intake-natural-head-rescreen-2026-09-03.json) | `b322bfafd10752f2b4ca4615603278705d8557c534da965dee2c941b41432bde` |
| [Sep 4 JSON](/workspace/w24-evidence/w24-prospective-intake-natural-head-rescreen-2026-09-04.json) | `f7e5b3d8c5c76c60fb2d34b718b58ee06d8508b377c3426483fef2be7df84b6e` |

The screens identify the PR #62 parser pin as
`efc626f18382fc049297520fa863f4d57aa03603`. This research preserved their recorded
results; it did not rerun the historical population under today's parser or claim
that a hard-coded parser label alone attests the imported code. Canonical contract
and current scheduler reads used checkout
`892e2dbaab0093e91857cea373dc188d638d8d80`.

Verification consisted of read-only JSON/count checks, CIK deduplication, archived
head comparisons, primary-document hash comparisons, fetch-plan/byte accounting,
cron/time arithmetic, source review and a final document/link check. These are
documentation and offline evidence checks. No live issuer re-screen, detector
capture, selection attempt, broker test or release-matrix run is claimed.
