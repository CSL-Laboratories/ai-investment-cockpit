---
title: RW-21 independent filing grammar and refusal review
type: evidence
status: live
last-verified: 2026-09-06
related:
  - ../runbooks/w24-merger-semantic-verifier.md
  - w24-bhf-date-saft-audit-2026-09-02.md
---

# Scope and authority

This review pressure-tests source extraction and keeps W-24 authority unavailable until its
separate prospective, completeness, witness, calibration, and current-deal checks pass. It
does not add an execution symbol, create a selection commitment, alter date grammar, or touch
the running soak. Historical bug fixtures and intentionally adversarial strings are development
evidence; they are not an independent held-out population or real selection authority.

The checked-in SAFT test covers repurchase boilerplate but omits the numeric repurchase sentence
responsible for the observed false positive. Passing that reduced excerpt was insufficient to
close the whole-document bug. The complete primary document is needed for a valid regression.

# Official source inventory

The following historical SEC primaries were independently resolved and inspected on 2026-09-06
through the web retrieval tool. This establishes source identity and language, **not** an exact-byte
capture. A local SEC client capture remains pending an operator-declared SEC User-Agent/contact;
the local environment did not contain `COCKPIT_SEC_USER_AGENT`. No identity was invented and no
direct SEC request was attempted without it.

| Issuer | Accession / form | Official primary | Bounded defect |
|---|---|---|---|
| SAFT | `0001172052-26-000023` / 10-Q | [saft-20260630x10q.htm](https://www.sec.gov/Archives/edgar/data/1172052/000117205226000023/saft-20260630x10q.htm) | Cash is already extracted in the MD&A merger paragraph. A distinct historical repurchase sentence concerning 3,478,060 shares triggers the stock-consideration pattern. |
| FSEA | `0001193125-26-206551` / 8-K | [d74001d8k.htm](https://www.sec.gov/Archives/edgar/data/1943802/000119312526206551/d74001d8k.htm) | Explicit target common-share conversion gives $17.25 cash for each share; the amount is followed by cash wording without repeating a per-share suffix. |
| SLP | `0001023459-26-000024` / 8-K | [slp-20260615.htm](https://www.sec.gov/Archives/edgar/data/1023459/000102345926000024/slp-20260615.htm) | Explicit common-share conversion gives $18.50 cash. In addition to the absent repeated per-share suffix, its conversion clause puts automatically before be; the current accepted verb order misses this. |
| CCO | `0001334978-26-000025` / 10-Q | [cco-20260630.htm](https://www.sec.gov/Archives/edgar/data/1334978/000133497826000025/cco-20260630.htm) | The bounded common-share clause puts cash before the $2.43 amount and includes cancelled/extinguished/converted. Its later shareholder narrative is a separate, broader construction. |

No source in this review supplies a newly discovered pair of pinned current-deal calendar dates.
Cash recall improvements therefore do not establish date readiness. In particular the CCO primary
describes a quarter rather than an exact expected closing day. The BHF year-only and conditional
extension refusals recorded on September 2 remain the required behavior.

# Minimal repair boundary

1. SAFT: remove the bare conjunction route by which a historical repurchase count is treated as
   merger consideration, while retaining explicit stock-consideration text and mixed cash/share
   receipt constructions. Do not simply erase every sentence containing repurchase: a sentence
   could contain both a real merger stock payout and unrelated repurchase text.
2. FSEA/SLP: keep the anchored each-share target common-stock head, bounded single-clause subject,
   and explicit conversion/receipt verb. Permit the amount followed directly by cash language
   because the anchored head already supplies the denominator. Keep other-security, cross-sentence,
   and across-filing subject-switch refusals. Add only the observed automatic-conversion ordering.
3. CCO: if addressed, support the fully anchored conversion clause before considering the broader
   narrative. Cash-before-amount and the observed cancellation verb sequence need independent
   adversarial controls. Do not accept an amount from option, debt, or preferred-stock treatment.
4. Leave expected/outside date patterns unchanged. Year-only, quarter-only, conditional not-closed-by,
   automatic extension, multiple distinct dates, and missing labels remain unavailable.

# Independent adversarial regressions

`app/tests/strategies/test_rw21_real_filing_grammar.py` was authored independently of the production
repair. Its initial run against the pre-RW-21 production parser produced **11 failures and 7 passes**
in 1.44 seconds on Windows Python 3.13.14. These failures reproduce unsafe acceptance, not a new
PASS claim:

- Script, style, inline-XBRL hidden text, comments containing an angle bracket, and quoted HTML
  attributes containing an angle bracket supplied synthetic consideration/outcome authority.
- Negated completed, terminated, and pending assertions were accepted as their positive outcome.
- Affirmative outcome controls and the retained imprecise/conditional/conflicting-date refusals
  passed.

Follow-up pressure testing added three source-set tests: ordinary current filings and unrelated
compensation amendments must not invent merger amendments, while an intervening explicit merger
amendment must remain reviewed even when followed by an ordinary filing. All three exposed the
existing current-accession shortcut. Another three direct probes confirmed that indirect negation
also bypassed definitive-agreement and exact-date assertions. The test suite now includes these
cases: accepting a negated exact day is unsafe even though no date grammar was broadened.

The existing general filing HTML extractor already handles these five **closed** markup cases.
It does not prove full browser visibility semantics: generic `hidden`, CSS visibility, malformed
unclosed code blocks, and inherited styling require separate analysis. Reusing that extractor
should not be described as a complete proof that every visually hidden construct is excluded.

The bounded capture helper is
`app/tests/strategies/fixtures/rw21/capture_sources.py`. It refuses an absent declared User-Agent
before network access, performs no retries or redirect following, spaces requests by 1.1 seconds,
caps each body at 10 MiB, and stops the batch on a non-200 response. It writes a manifest with raw
and compressed-artifact hashes and gzip-preserves exact bytes. A failed capture is recorded as
incomplete and is not automatically retried. The fifth planned primary is BHF accession
`0001685040-26-000028`, retained specifically to verify the year-only/conditional-extension refusals.

# Independent precision and recall

This four-issuer, failure-selected review cannot estimate either population precision or recall.
There is no held-out number to report. Re-running these known misses after a repair estimates only
development-regression coverage.

The smallest honest measurement is a prospectively frozen filing population with an independent
label sheet and two separately reported confusion matrices:

- Freeze a dated census or declared sample of supported primary 8-K/10-Q/10-K filings, preserving
  exact bytes, identity, inclusion rule, and hashes before exposing parser predictions to the
  labeler. Count every selected filing, including parser refusals; otherwise recall is unmeasurable.
- An independent reviewer labels target common-share consideration, mixed consideration,
  agreement/lifecycle status, expected calendar date, outside calendar date, and outcome from
  visible source spans. Ambiguity is a distinct label. Adjudicate disagreements before scoring.
- Report field exactness separately from decision precision `TP/(TP+FP)` and recall
  `TP/(TP+FN)`, with denominators and confidence bounds. An empty predicted-positive set means
  precision unavailable, not 100%; an empty true-positive population means recall unavailable.
- Separate cash-semantic eligibility from current-deal date readiness and from witnessed selection
  authority. A correct cash extraction is not a true positive for either of the latter gates.
- Preserve this failure-selected four-issuer set in development only. If positive cases are
  oversampled to test recall, report strata and the sample design; do not present pooled precision
  as the natural intake precision.

The production cohort-size, source-set, witness, edge, and date gates remain unchanged by this
measurement plan. A separately approved empirical redesign would be needed to alter them.

# Second independent review of the first repair

After the initial production repair, all original 24 independent cases passed. A second review
then added attack cases and calendar/SAFT controls, producing **12 failures and 49 passes** in
1.86 seconds; Ruff was clean. These were observed on the evolving RW-21 working tree after
RW-20 commit `be1ff13`, not a committed release attestation.

The remaining unsafe acceptances were generic hidden elements, explicit inline hidden styles,
templates, unclosed code/inline-XBRL hidden blocks, denied or question/retracted outcomes, and a
negated cash clause that still bypassed assertion filtering. The repair also introduced a real
refusal regression: the month May was treated as the modal verb when checking pinned expected
and outside calendar dates. All twelve months are now controlled independently for both date
fields; ordinary modal language around a May date must still refuse.

The existing repository SAFT excerpt remains a positive control after assertion filtering; it
still yields the original $105 consideration and no stock flag. This is deliberately distinct
from the still-pending whole-primary repurchase-count regression. The affirmative failure
predicate concerning a transaction that will not proceed also remains valid.

Recommended minimal fix for the remaining HTML boundary is a semantic-only stateful tokenizer
that suppresses script/style/template/inline-XBRL hidden ancestry, native hidden attributes, and
explicit display-none/visibility-hidden inline styles. Unclosed hidden blocks must suppress their
remaining content or refuse the document. This does not prove external stylesheet visibility or
general natural-language entailment; neither should be claimed by the local regression result.

# Final bounded review

The independent final rerun passed **66 tests in 2.32 seconds** on native Windows Python
3.13.14; Ruff passed for the independent test module and the two repaired production modules.
The prior 12 failures are closed. Five final structural counterexamples were also added and
closed: duplicate HTML style attributes with contradictory visibility, a self-closing hidden
non-void element, a self-closing script, an inline CSS comment inside the display-none property,
and synthesis of an outcome from separate paragraphs.

The final implementation rejects duplicate/ambiguous attributes and unsupported escaped or
unterminated inline styles, preserves hidden ancestry for non-void self-closing tags, and inserts
clause boundaries at blocks and removed hidden islands. Date matching still requires the original
pinned patterns; all twelve month names pass the literal calendar controls, while imprecise,
conditional, negated, and conflicting dates refuse. Negated cash and definitive assertions and the
reviewed negative-outcome cases refuse. Both the original SAFT excerpt and affirmative outcome
controls remain accepted in their narrow unit tests. Ordinary current filings no longer invent
merger amendments, and actual intervening amendments cannot be hidden by an ordinary latest head.

Exact reviewed file SHA-256 values before the final package commit:

| File | SHA-256 |
|---|---|
| `app/strategies/merger_semantics.py` | `b87bc0675d95fc4c9cc10696f1ac07b18890aa7115ab04c92acd05cb558339fe` |
| `app/strategies/semantic_text.py` | `89f2abab8346a4f9e2d775e198159ae5c83efef700f373bffff917154b1cdb62` |
| `app/tests/strategies/test_rw21_real_filing_grammar.py` | `2b22fcda20360be225c021d2f07ef6318cbc46ab6e22ca50388413d8a07f8392` |

**Scoped verdict:** no remaining blocker found within these repaired and independently tested
assertion/markup/amendment behaviors. This is local adversarial regression evidence, not DEC-09
matrix evidence, comprehensive browser rendering, a general entailment proof, independent held-out
precision/recall, or selection PASS. Full-primary SAFT repurchase and FSEA/SLP/CCO cash grammar
repairs remain held pending exact-byte capture; no new positive cash grammar or date relaxation
was approved by this review. The static watchlist, witnessed prospective cohort, source-set,
calibration, current-target date, and order gates remain separate and unchanged.
