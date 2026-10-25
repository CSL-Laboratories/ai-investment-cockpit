# Proposed successor amendments

Unaccepted, versioned proposals to pinned replacement contracts. Naming:
`<CONTRACT>-YYYY-MM-DD.md`. These files are not governing.

`docs/replacement/SUCCESSOR-PRODUCT.md` and the `program.json` pins remain the
last accepted bytes until Lucas posts an exact-digest receipt and a reviewed
promotion copies those bytes onto the governing path and moves the pins.

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
2. Accepted product-text changes only via a **new versioned amendment**. Lucas
   accepts it by exact-digest receipt. A reviewed promotion then moves the pins.
3. Allowed `status` values: `open`, `findings-published`, `proposed`,
   `accepted`. `accepted` on a row means Lucas posted an exact-digest receipt
   for that parent's amendment, not that this register is accepted.
4. Check: `python3 -X utf8 -B tools/check_cockpit_research_register.py`.
