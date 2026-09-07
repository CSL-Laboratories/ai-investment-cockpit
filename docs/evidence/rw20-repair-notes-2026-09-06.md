# RW-20 repair record — 2026-09-06

Base: main `b4a199ff41ed84d78f21602a84d9665c1072ec6a`, after PR #68.
Original findings were committed separately in `94b8b4b`. This package implements the three
high-priority repairs and two additional independently reproduced authority defects.

| Defect | Final behavior |
|---|---|
| BUG-115 | Claude decision jobs explicitly remove built-in tools, deny wildcard tools, ignore ordinary settings/customizations and MCP configuration, disable skills/Chrome, and use fresh nonpersistent sessions. The actual argv must match a closed grammar. Repair is fresh; decision-session resume refuses. |
| BUG-116 | Draft persistence signs a canonical hash of the full proposal body plus the resolved-order hash and status/side/flag identity. Approval consumes these bindings from the verified read snapshot. Missing legacy bindings require redrafting; no historical event is backfilled. Both cost verification and fresh risk use the fixed order's quantity/side/type/symbol. Risk notional is conservatively bounded by the larger of the fresh quote and limit price. The account snapshot returned by the fresh broker read is used directly. |
| BUG-117 | Approval, gateway review and locked final submit reconstruct performance from signed equity/fill evidence without waiting for the sampler. Missing/stale/future evidence refuses; newly tripped drawdown/consecutive-loss breakers refuse immediately. Cached risk context also requires a current-day, nonfuture observation no older than 120 seconds. |
| BUG-118 | `AuditLog.result` explicitly pins the SQLite snapshot before its duplicate-result guard. A competing commit makes the stale writer retry; the retry sees the completed intent and refuses. BUG-114's watermark ordering remains intact. No existing fork is rewritten or healed. |
| BUG-119 | Codex's existing denial profile is retained, but the inspector now rejects duplicate overrides, aliases, unknown switches, repeated/unknown disable entries, missing values and extra positional prompts. The reproduction concerns inspector acceptance; it is not a demonstrated model-controlled argv mutation. |

The installed Claude CLI is 2.1.252. Its help and
[official CLI reference](https://code.claude.com/docs/en/cli-reference) distinguish tool availability
from permission pre-approval. The selected safe/restricted profile preserves subscription
authentication, whereas bare mode does not. Administrator-managed policy still applies; this
profile is not proof of OS account isolation. An older CLI that rejects required flags must fail
closed through the existing nonzero-exit/vendor-health path. No real model inference was launched
for the audit. A deployment capability probe remains distinct from unit argv validation.

Installed `codex exec --help` confirms stdin, sandbox, ignored config/rules and ephemeral switches.
See also the [official CLI reference](https://developers.openai.com/codex/cli/reference/).
This patch does not choose a new model, enable a feature, grant broker credentials to a runtime,
change the auto-mode runtime pin or place I/O in `app/risk/`.

Independent attacks and their exact scope are recorded in
[the adversarial review](rw20-adversarial-review-2026-09-06.md).
The root independently reran 171 focused CLI/storage/broker tests (39.97 seconds), then 162
CLI/authority/restart/storage tests after the Codex inspector repair (36.09 seconds), on the local
Python 3.13 environment. Those runs are diagnostics, not DEC-09 certification. Final full-suite
3.11/3.12 Windows/Ubuntu-WSL2 results are recorded only in
[the platform evidence](rw20-matrix-isolation-2026-09-06.md) after they execute.

Operational consequences: old pending proposals refuse rather than acquire new signatures;
performance reconstruction adds synchronous audit work and must be measured during a later
authorized soak; unavailable witness/broker/quote data still closes the path. This branch has not
been deployed or merged. The current-target, independent-label, prospective cohort, real broker,
time/value and SEC-11 evidence gates remain open.

Related: [selection-to-order map](rw20-selection-order-map-2026-09-06.md),
[witness activation plan](rw20-independent-witness-plan-2026-09-06.md),
[cohort and value assessment](../research/gate-feasibility-and-value-2026-09-06.md).
