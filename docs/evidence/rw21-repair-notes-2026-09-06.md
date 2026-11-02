# RW-21 semantic repairs — 2026-09-06

This package follows RW-20 commit `be1ff13`. It repairs reproduced authority mistakes and a
source-set refusal. It does not broaden `_CASH`, `_EXPECTED_DATE` or `_OUTSIDE_DATE`, admit
a strategy name, generate an edge or claim a selection PASS.

| Defect | Repair and regression boundary |
|---|---|
| BUG-120 / A3 | A stateful semantic text parser excludes script/style/template/ix:hidden content, explicit hidden attributes, inline display:none/visibility:hidden and aria-hidden ancestors, including unclosed hidden blocks. Comments and quoted attribute contents cannot become terms. Hidden islands are separated so their removal cannot join incomplete clauses. Raw bytes and citation locators stay separate and unchanged. |
| BUG-121 / A4 | Cash, definitive-agreement, outcome and exact-date authority now requires an affirmative assertion. Negations outside the matched predicate, uncertainty, denial, questions and same-sentence retractions refuse. The pinned affirmative failure `transaction will not proceed` remains valid. Literal month May inside a matched calendar date is not treated as a modal. |
| BUG-122 / A6 | A different current accession is no longer itself an amendment. Current-deal verification uses the complete source set's existing merger-amendment predicate, consistent with selection/calibration. An ordinary quarterly filing or unrelated compensation amendment does not invent a merger amendment; an intervening real merger amendment cannot be hidden by a later ordinary head. |

Root intermediate verification: **99 passed in 74.53 seconds** for the original semantic
suite plus 61 independently authored new cases. A final round added five structural probes;
all **66 independent cases passed** (root 2.11 seconds, reviewer 2.32 seconds), with Ruff clean.
The final fixes refuse duplicate attributes, preserve hidden non-void self-closing ancestry,
normalize CSS comments, refuse ambiguous escaped inline styles, and separate block paragraphs.
These are Python 3.13 diagnostics. See [independent filing review](rw21-filing-grammar-2026-09-06.md) for the adversarial
rounds and scope, and [DEC-09 evidence](rw20-matrix-isolation-2026-09-06.md) for actual release cells.
The first repair's May-date regression was found independently and fixed before this result.

This is a deliberately narrow semantic recognizer, not a browser renderer or general natural-
language proof. Generic stylesheets, external CSS and every possible rhetorical construction are
outside the tested visibility/assertion contract. Broader support requires separately pinned
fixtures and refusal tests; these results cannot certify an uninspected filing population.

Held cash-language work remains visibly pending: SAFT's repurchase false-positive and the
FSEA/SLP/CCO alternate cash structures have identified official source URLs, but the exact original
response bytes are not in this checkout. The operator was asked for the declared SEC User-Agent
contact; the one-shot capture tool refuses without it. No identity, raw filing, hash, independent
label or failed-capture success was fabricated. The existing BHF/SAFT excerpts, the explicitly
reconstructed CRNX grammar control, and the year-only/conditional/auto-extension refusal controls
remain in the tests. Five historical BHF/SAFT review excerpts are now separately
[pinned with provenance and seven offline checks](rw21-held-excerpts/README.md). These preserve
the known SAFT stock false positive as an open defect; they do not replace full-primary capture.

Historical fixtures can validate a grammar repair; they are not independent held-out precision
or recall evidence. That measurement requires a frozen population including rejected documents
and separately reviewed labels. See [measurement protocol](../research/gate-feasibility-and-value-2026-09-06.md).
