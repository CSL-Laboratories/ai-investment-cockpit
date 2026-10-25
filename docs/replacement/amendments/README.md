# Proposed successor amendments

Unaccepted, versioned proposals to pinned replacement contracts. Naming:
`<CONTRACT>-YYYY-MM-DD.md`. These files are not governing.

`docs/replacement/SUCCESSOR-PRODUCT.md` and the `program.json` pins remain the
last accepted bytes until an accepted amendment is promoted onto the governing
path and the pins move. This folder states a proposed acceptance path for
Samir/Dan-authored spec changes; the pinned checker and receipts still require
Lucas's exact-digest Linear receipt until a follow-up amends them.

## Living research register

The Cockpit spec is dynamic: it changes with the issues and the research that
is published. That living state lives in
[`RESEARCH-REGISTER.md`](RESEARCH-REGISTER.md), keyed to Cockpit parents
CSL-511..CSL-519 plus shared CSL-520..CSL-522 (umbrella CSL-493). The register
is **not** part of any exact-digest product receipt and is **not** copied into
`SUCCESSOR-PRODUCT.md` on promotion.

### Update rule

1. When a csl-research record for a parent is published at
   `submissions/<record_id>/`, update that row in a **docs-only PR**. Set
   `research_record` to that path and `status` to `findings-published`. Do not
   edit `SUCCESSOR-PRODUCT.md`, `program.json`, or any pin or receipt.
2. Founders are Lucas, Samir (`samir-shah-ahmed`), and Dan/Chaandan
   (`CDanTheMan`). Samir and Dan (or their agents, attributed) may change the
   product spec as research is synthesized, via a **versioned amendment PR**.
   A spec change they author is **accepted when a founder other than the author
   approves that PR**; that approval counts as acceptance. (Relayed by Grok Bot
   Liaison from Lucas's 2026-10-24 chat answers, 11:58 PM PT.) A reviewed
   promotion then moves the pins. This proposed path is not yet wired into
   `program.json`, `tools/check_replacement_program.py`, or `AGENTS.md`.
3. Allowed `status` values: `open`, `findings-published`, `proposed`,
   `accepted`. `accepted` on a row means that parent's amendment was accepted
   under the rule above (or, until the follow-up, under the current Lucas
   exact-digest receipt), not that this register file is accepted.
4. Check: `python3 -X utf8 -B tools/check_cockpit_research_register.py`.
