---
title: Reusable G-gate red-team templates
type: reference
status: active
last-verified: 2026-07-16
related: [production-playbook, production-work-packages, remaining-work-session-plan]
---

# Reusable G-gate red-team templates

The W-series gate philosophy (`docs/production-playbook.md` §1) is that a property is closed
only when a **FRESH adversarial repro can no longer construct the bypass** — not when the
suite is green. These are the harnesses that drive that.

They lived only in ephemeral session scratchpads until Session 0 (2026-07-16) committed them.
**`w10_redteam.js` was already lost by then** — see below.

## Why these are here

`production-playbook.md:621` says *"reuse it as the template for every P0 W-gate."* A template
that exists only in `%TEMP%` is one cleanup away from gone, and one already is. Committing them
makes the instruction executable.

## What survives

| File | Shape | Drove | Use when |
|---|---|---|---|
| `w11_capability_redteam.js` | Flat `parallel()` over **N named attack angles** → one `xhigh` verdict | G11 / W11 (3 fresh rounds, round 4 unanimous 0 repros) | **One property, N attack angles** |
| `w12_per_property_redteam.js` | **Per-property scope-bounded**, 5 skeptics × 3 properties + verdict; takes `args {round, app_path}` | G12 / W12 (8 fresh rounds) | **N properties, each needing its own scope bound** |

**`w10_redteam.js` — LOST.** The audit-spine shape (a `pipeline()` over **N consuming paths** ×
`SKEPTICS_PER_TARGET = 5` with angle rotation, plus a `used_only_file_anchor` boolean that counts
as a breach on its own) survives **only as prose in `production-playbook.md` §4a**, which is
therefore its canonical source. It was not reconstructed from memory — a fabricated red-team is
worse than none, because it would carry the authority of a template without the provenance of one.

## Picking a shape

| Package shape | Template |
|---|---|
| One property, N consuming paths | §4a prose (w10 shape — **not recoverable as code**) |
| One property, N attack angles | `w11_capability_redteam.js` |
| N properties, each scope-bounded | `w12_per_property_redteam.js` |

## The non-negotiables (playbook §1)

1. **Run against the W-package's UN-MERGED worktree branch**, never the main checkout. Pass
   `args.worktree_path`. An absent target file is a **GATE FAIL** ("target not built"), never a
   trivial pass — anti-pattern #8: reviewing an empty tree falsely passes.
2. **Re-invoke FRESH — never resume.** The standard anti-pattern is letting the fixer certify
   their own fix. New agents, or the round is worthless.
3. **One reproducible bypass = the property is OPEN.** Vote counts rank confidence; they never
   overrule a single real repro.
4. **Expect fixes to introduce defects.** Three G11 findings and several G12 findings were
   exactly that — caught only by the *next* fresh round.

## The technique that makes multi-round work

**The `focus` strings are rewritten each round to encode the previous round's finding.** They
are a living changelog, not static prompts. From `w11_capability_redteam.js`:

> `"ROUND-4: round-3 found a NAMED unwrap handle (guarded._order_tracer_original) that let
> vars(type(b).submit_order)['_order_tracer_original'](b,canonical) reach the broker around the
> guard — NOW REMOVED... CONFIRM it is gone... Then hunt for ANY OTHER by-NAME/by-attribute
> unwrap or dispatch bypass"`

## Scope-bounding is what prevents false gate failures

Each angle enumerates what is **out of scope**, and the verdict agent gets a matching rule.
This is what lets a property-closure converge instead of chasing another W's residuals:

> `"OUT OF SCOPE (do NOT count — inherent to any Python wrapper + require TRUSTED in-process
> power): reading guard.__closure__ cell_contents, delattr/monkeypatching the class attr,
> capturing the bound method BEFORE install(). Count only an UNTRUSTED-INPUT-reachable bypass."`

Without this, an unbounded-shape-space property (W12's minimize) whack-a-moles forever. Set a
convergence budget up front.

## Permission envelope given to skeptics

> `"You MAY run READ-ONLY commands to validate a repro (grep, and \`python -m pytest\` from the
> app dir). You may write a THROWAWAY repro ONLY under the scratchpad dir. Do NOT modify ANY
> file under app/ or docs/. Do NOT commit or push."`

## Retargeting for a new gate

1. Copy the closest shape to a scratchpad — **do not edit these in place**; they are the
   pattern of record.
2. Rewrite `ANGLES[].focus` for the new property, encoding each round's finding as you go.
3. Rewrite the schema's `control_defeated` enum and the verdict's counts-as-a-breach rule.
4. Set `args.worktree_path` to the un-merged branch.
5. Re-invoke FRESH each round (never `resumeFromRunId` — that would reuse skeptics who already
   saw the code).
