"""RW-10 — the EDGAR filing-delta pipeline, vendored from ``edgar-morning-brew``.

READ ``PROVENANCE.md`` (this directory) BEFORE CHANGING ANYTHING HERE. This tree is not
cockpit-authored: it is a pinned copy of a sibling repository's deterministic core, and its
files deliberately keep upstream's internal structure so that ``git diff <pin>..origin/main``
stays appliable. Restructuring them silently forfeits the only mechanism that lets upstream's
SEC-client fixes reach this copy.

WHAT THIS PACKAGE IS
--------------------
``edgar/``      SEC client + submissions poller. ``edgar/fetch.py`` is the ONLY module in the
                cockpit permitted to touch sec.gov: declared User-Agent, strictly <10 req/s.
``parse/``      8-K items, 10-K/Q sections, XBRL facts — filing bytes to structured content.
``delta/``      What CHANGED between two filings: section text diffs + XBRL numeric deltas,
                scored for materiality.
``cite/``       Binds every delta to the exact byte range of the filing it came from, and
                refuses to publish a claim whose citation does not resolve.
``raw_store``   Write-once storage for filing bytes (Inv 8: raw kept separate from derived,
                so a delta can always be re-verified against the bytes it describes).

WHAT THIS PACKAGE IS NOT
------------------------
It contains **no model call and may never contain one** — enforced by the
``no-LLM-in-filings`` gate, not merely by convention. Upstream's LLM brief composer,
approve-before-send path, digest publisher and scheduler were deliberately NOT taken: the
cockpit already has a brief pipeline, an approval path with a hash-chained audit (Inv 6), an
operator surface, and a scheduler. Absorbing those would have been a second, redundant
inference on the same evidence.

The value here is evidentiary, not predictive. Cited filing deltas tell the operator WHAT
CHANGED and WHERE TO LOOK, with a link to the bytes. They do not forecast (§7 of
``docs/refactor-plan.md``).
"""
