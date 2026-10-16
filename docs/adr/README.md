---
title: Architecture decision records
type: index
status: live
last-verified: 2026-10-16
---

# Architecture decision records

This directory is the durable record of load-bearing architecture and direction
choices for the AI Investment Cockpit.

An ADR is a short, dated note of a decision that later work should treat as
settled unless new evidence appears. Research briefs, REGISTER claims, and
Linear discussion stay where they are; the ADR is the closed record.

## When to write one

Write an ADR when a Linear issue labeled `decision` closes. The issue assignee
authors the file in the same close-out step — not a later cleanup pass, and not
an agent substituting for the assignee.

Do not open an ADR for routine implementation choices, work-package scheduling,
or anything already owned by PRODUCT / PLAN / REGISTER / the nine invariants.

## File naming and linking

- Copy [`0000-template.md`](0000-template.md). Keep the YAML frontmatter; set
  corpus `status:` to `live` when the decision is accepted, `superseded` when a
  later ADR replaces it. That field is the closed docs-index vocabulary
  (`live` / `reference` / `superseded` / `archive`), distinct from the ADR
  Status line (Proposed / Accepted / Superseded).
- Name the file `NNNN-title.md`: a four-digit sequence (`0001`, `0002`, …) plus
  a short kebab-case title. Never reuse a number; never edit a number after
  the file is committed.
- `0000-template.md` is the template only (`status: reference`). It is not a
  decision.
- Link the committed ADR from the Linear `decision` issue (description or
  attachment) so the issue and the file point at each other.

## Index

No accepted ADRs yet. Add a row here when the first numbered file lands.

| ADR | Title | Linear | Status |
| --- | --- | --- | --- |
