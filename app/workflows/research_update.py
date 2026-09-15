"""RW-35 — deliver a useful empty-run research update through compose.

Uses retained RW-29/RW-31 cited inputs and the existing deliver seam. No LLM,
no OrderGateway, no watchlist mutation, no lowered cohort gate.
"""

from __future__ import annotations

from workflows.constants import EVENT_RESEARCH_UPDATE

from research.empty_run import compose_empty_run_update


def empty_run_update(state, clock, *, deliver=None) -> dict:
    clock = clock if clock is not None else state.clock
    payload = compose_empty_run_update()
    payload = {
        **payload,
        "at": clock.now().isoformat(),
    }
    delivered = False
    if deliver is not None:
        delivered = bool(deliver("#brief", payload))
    payload["delivered"] = delivered
    state.audit.append(
        EVENT_RESEARCH_UPDATE,
        actor="research_update",
        payload={
            "claim_id": payload["claim_id"],
            "latest_screen_id": payload["latest_screen_id"],
            "qualify_count": payload["qualify_count"],
            "meets_cohort_gate": payload["meets_cohort_gate"],
            "next_trigger": payload["next_trigger"],
            "delivered": delivered,
            "empirical_pass_claimed": False,
        },
    )
    return payload
