# MEM-01 — the thesis memory layer

> Code: `app/workflows/theses.py`, `app/schemas/models/thesis_record.py`,
> `app/bridge/thesis_view.py`. Operator surface: `/thesis`.

## The gap this closes

`TradeProposal` has required an `invalidation` field since WP8 — *"the condition under which
this thesis is wrong and the position should be exited."* Until now that field was written,
scrubbed, rendered to the operator once, and **never read by anything**. Nothing in the tree
revisited it.

`draft_proposal` likewise read no prior proposals: `ProposalRecord` was touched only by
`interrogation.py` and a duplicate-id check. Every draft was produced with **zero memory of
earlier theses on the same symbol**.

So the system could propose buying a name on Monday and again on Thursday with no awareness
that Monday's thesis existed, or that its stated invalidation had already fired.

That is a **statefulness** gap, not a retrieval-latency gap — and it sits on the axis the
corpus calls the reliable ROI: **discipline**. A thesis you cannot be held to is not a thesis.

## Why this is not a vector store

The obvious way to "give the model memory" is semantic retrieval over past material. In *this*
system that is unsafe, and the reason is worth stating precisely.

SEC-14 corroboration is anchored on `Packet.dispatched_refs`: a proposal is grounded only if
every `source_ref` it cites was in the dispatched set **and** resolves out of the verified raw
store. A thesis is not raw source data — it is a previous model's prose. If thesis material
entered the dispatched set, a hijacked model could **bootstrap its own evidence**: emit a
confident thesis today, then tomorrow cite that thesis as the corroborating source for acting
on it. The citation would check out. Grounding would pass. Nothing external would ever have
said it.

**Two independent mechanisms prevent this, and both are tested:**

1. `thesis_context_block` produces **prompt text only**. Thesis refs are never added to
   `Packet.items`, so they never reach `dispatched_refs`, so a citation fails the
   *"cited ref not in dispatched packet"* check in `grounding.corroborate`.
2. Thesis refs use the `thesis://` namespace, which is not a parquet ref. Even if mechanism 1
   were bypassed, `events.resolve_raw` raises `RawUnavailableError` and corroboration fails
   closed on *resolution*.

`test_a_proposal_citing_its_own_prior_thesis_is_NOT_corroborated` exercises the full attack.

The block is still wrapped in `<untrusted-content>` and re-escaped, because its text
originated from a model and carries the same injection risk as a headline. Prior scrubbing
(SEC-09) is a different control, not a reason to skip the wrapper.

## Opening is automatic; closing is not

| | Who | Why |
|---|---|---|
| **Open** | automatic, on a **drafted + unflagged** proposal | a flagged or needs-human draft is review-only; recording its thesis would launder a rejected draft into durable belief |
| **Invalidate / close / supersede** | **an allowlisted operator only**, via `/thesis` | *"a close below support"* needs judgement, and this system does not put a model on judgement that carries authority |

`closed_by` records the acting operator. There is deliberately no scheduled job and no model
path into `close_thesis` — a model that can retire its own invalidation conditions has an
invalidation field that means nothing.

## The operator loop

```
/thesis                                    → standing claims + invalidated count
/thesis action:invalidate id:<id> reason:<why>
```

A reason is **required**: a thesis retired without one leaves no record of *why*, which is the
only part worth keeping. The **invalidated count** is reported alongside the open book on
purpose — a system that reports only its live positions cannot distinguish discipline from a
run of luck, and the count of times its own stated disqualifying condition fired is the number
it would otherwise never volunteer.

Standing theses also appear on the proposal embed (`embed["standing_theses"]`), so *"we
already said the opposite on Monday"* is visible at the moment of approval.

## Additive by construction

No open thesis ⇒ `thesis_context_block` returns `""` ⇒ prompt text is **byte-identical** to
pre-MEM-01. The change cannot silently rewrite every prompt in the system.

## What this does not do

- **It does not evaluate invalidation conditions.** It makes sure the question gets *asked*.
  Automatic evaluation would need either a price feed plus a deterministic rule language, or a
  model — and the model option is the one this design refuses.
- **It does not carry theses into the triage/brew prompts.** Only the deep draft sees them.
- **It does not supersede automatically** when a newer proposal arrives on the same name.
  Replacing a standing claim is a judgement about whether the old reasoning still holds.

## Where the three MIT papers landed

Assessed against this system rather than adopted wholesale:

- **Abacus** (cost-based optimizer for semantic operator systems) — the strong fit, and *not
  yet built*. The cockpit is a semantic-operator pipeline (triage filter → deep map →
  grounding verifier) with a hard cost constraint, currently governed by two hand-set
  constants: `triage_threshold=60` and `deep_model_daily_budget=5`. Neither has ever been
  validated against outcomes. The audit trail already records what a cost model would need.
- **CopyRight** (layout-aware partial replication) — **poor fit today.** Its win is
  accelerating *scans*; the raw store is written once per batch and read by exact `#idx` point
  lookup. There is no scan to accelerate. It becomes relevant only if a scan-heavy retrieval
  path is added.
- **Interpretable attribute discretization** — marginal; possibly for bucketing continuous
  signals into operator-legible bands.
