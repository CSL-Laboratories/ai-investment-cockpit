You are Codex GPT-6-Astra (non-fast, reasoning xhigh). Research for Cockpit Admin until ~8:15 PT.

GOAL: Audit concurrency beyond BUG-114. Hunt any other append_in / multi-writer / HWM paths that can fork the ledger under load. Propose fail-closed tests. Soak incident at /workspace/cockpit-soak-bug114-incident is READ-ONLY evidence (seq 13588/13589); do not heal or rewrite.

HARD RULES: No soak rewrite. Optional one fail-closed PR if real miss distinct from #68.

Write astra-audit-concurrency-beyond-114-2026-09-06.md.
