---
type: evidence
status: pending-human-review
last-verified: 2026-08-18
---

# RW-11 precision label sheet — 2026-08-18

## Purpose and boundary

This sheet seeds, but does not complete, RW-11's hand-labelled post-cutoff precision sample.
It covers four real text-section deltas emitted from NVIDIA's 2026-08-17 Form 8-K, accession
`0001045810-26-000069`. The source filing is the public
[SEC archive document](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000069/nvda-20260817.htm).

The parser output cannot label itself. All human-review cells below therefore remain
`UNREVIEWED`, the precision numerator and denominator remain unset, and this artifact grants no
strategy, paper-order, live-order, or autonomous authority. Four rows alone are not claimed to
be a meaningful sample. RW-11 and RW-12 remain open.

## Mechanical precheck

The ignored write-once raw blob at
`app/data/raw/sec/0001045810-26-000069/raw.bin` was independently re-read at every stored
half-open byte range. All four ranges existed, decoded as UTF-8, had the same byte length as the
stored snippet, and reproduced that snippet byte-for-byte. This proves citation-anchor integrity;
it does not prove semantic correctness, materiality, or extraction precision.

| Delta ID | Claimed source field | Byte range | Span bytes | Exact raw round-trip |
|---|---|---:|---:|---|
| `delta_2b02a92d15a572fa31905e5d` | `item_7.01` | `[21114, 25837)` | 4,723 | yes |
| `delta_3d0725075fc772355a919b17` | `item_1.01` | `[15309, 20357)` | 5,048 | yes |
| `delta_6b8a47935754158de3b24a57` | `item_9.01` | `[25997, 31168)` | 5,171 | yes |
| `delta_e5305830463f1436aa62e43c` | `item_2.03` | `[20517, 20954)` | 437 | yes |

## Independent human labels

The reviewer must inspect the SEC source and local cited span without using the parser's label as
ground truth. Record `CORRECT`, `INCORRECT`, or `ABSTAIN` for each reviewable field. An
`INCORRECT` emitted field is a false positive. An `ABSTAIN` is excluded from scoring but must be
explained and cannot help satisfy sample sufficiency.

| Delta ID | Section/item field | Material-change extraction | False-positive row? | Reviewer notes |
|---|---|---|---|---|
| `delta_2b02a92d15a572fa31905e5d` | UNREVIEWED | UNREVIEWED | UNREVIEWED | — |
| `delta_3d0725075fc772355a919b17` | UNREVIEWED | UNREVIEWED | UNREVIEWED | — |
| `delta_6b8a47935754158de3b24a57` | UNREVIEWED | UNREVIEWED | UNREVIEWED | — |
| `delta_e5305830463f1436aa62e43c` | UNREVIEWED | UNREVIEWED | UNREVIEWED | — |

- Reviewer: `UNASSIGNED`
- Review date: `UNSET`
- Review independence/procedure notes: `UNSET`

## Scoring record

For emitted semantic fields, precision is `correct / (correct + incorrect)`. Machine-verified
accession, URL, byte offsets, and snippet round-trip are reported separately and are not added to
the semantic-precision numerator. A false-positive row makes its material-change extraction
incorrect even if its section locator is correct.

- Correct semantic fields: `UNSET`
- Incorrect semantic fields: `UNSET`
- Abstentions: `UNSET`
- Precision: `UNSCORED`
- Sample-sufficiency decision and rationale: `PENDING`
- RW-11 ≥95% gate: `OPEN`

## Required completion

1. Assign an independent human reviewer and freeze the review procedure before revealing or
   changing expected labels.
2. Expand the post-cutoff sample across enough real documents and emitted field types to justify
   the word "meaningful"; do not select only known-success outputs.
3. Preserve all incorrect fields and false-positive rows in the denominator.
4. Record the completed numerator, denominator, abstentions, reviewer, and rationale here.
5. Close RW-11 only if the full frozen sample reaches at least 95% field precision and includes a
   cited live material delta. Otherwise keep the system fail-closed and remediate under a separate
   bounded package.
