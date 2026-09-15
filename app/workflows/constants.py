"""WP8 — workflow audit event-type constants (contract 'New audit event types').

Implements the WP8 substrate section of docs/build-scaffold-ultracode.md (WP8) and the
audit-event registry convention from app/orchestrator/state.py: events only WP8 reads are
module-local to workflows (the gateway precedent). Referenced by SEC-09 (outbound_scrub),
SEC-14 (grounding_check), SEC-18 (interrogation) and SEC-21 (deep_escalation budget ledger)
in docs/security-hardening-backlog.md.

Do NOT emit ``proposal_decision`` or any BYPASS_EVENTS string from workflows — those are
gate-read metrics owned by WP5/WP6 (docs/security-hardening-backlog.md SEC-20).
"""
from __future__ import annotations

# payload: {raw_key, date, content_hash, count}
EVENT_INGEST_BATCH = "ingest_batch"
# via record_state_change on AgentJob rows
EVENT_AGENT_JOB = "agent_job"
# payload: {batch_id, window_start, window_end,
#           scored:[{event_id, article_id, classification, score}],
#           deferred:[event_id], degraded:bool}
EVENT_TRIAGE_BATCH = "triage_batch"
# payload: {date, event_id, symbol, reason, corroborated:bool}  <- SEC-21 budget ledger
EVENT_DEEP_ESCALATION = "deep_escalation"
# payload: {event_id, symbol, reason}
EVENT_ESCALATION_DEFERRED = "escalation_deferred"
# via record_state_change on BriefRecord
EVENT_BRIEF = "morning_brief"
# via record_state_change on ProposalRecord
EVENT_PROPOSAL = "trade_proposal"
# payload: {target, hits:[kind], blocked:bool}  (SEC-09)
EVENT_SCRUB = "outbound_scrub"
# payload: {proposal_id, flagged:bool, grounded, injection_suspected,
#           corroborated:bool, reasons:[str]}  (SEC-14)
EVENT_GROUNDING = "grounding_check"
# payload: {proposal_id?, job_id?, pattern:"fresh"}  (SEC-18)
EVENT_INTERROGATION = "interrogation"
# payload: {claim_id, latest_screen_id, qualify_count, meets_cohort_gate,
#           next_trigger, delivered, empirical_pass_claimed:false}  (RW-35)
EVENT_RESEARCH_UPDATE = "research_update"
