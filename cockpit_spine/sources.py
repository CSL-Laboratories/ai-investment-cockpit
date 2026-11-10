"""Where proposals come from: the route consumer, and a fake for offline tests.

A source returns a ``DecisionOutcome`` from the WP12 consumer. Whatever it
returns, the session re-validates and never trusts it further than that.
"""

from __future__ import annotations

from typing import Protocol

from successor.route_consumer import DECISION_PROFILE, DecisionOutcome, RouteConsumer

PROPOSAL_WORKLOAD = "workload-d04"


class ProposalSource(Protocol):
    adapter_kind: str

    def fetch(self, *, request_id: str, mode: str, prompt: str) -> DecisionOutcome:
        """Return the consumer's outcome for one proposal-drafting request."""


class RouteProposalSource:
    """Asks the one route the consumer is bound to. Picks nothing, retries nothing."""

    def __init__(self, consumer: RouteConsumer, *, adapter_kind: str) -> None:
        self._consumer = consumer
        self.adapter_kind = adapter_kind

    def fetch(self, *, request_id: str, mode: str, prompt: str) -> DecisionOutcome:
        return self._consumer.decide(
            request_id=request_id,
            profile=DECISION_PROFILE[PROPOSAL_WORKLOAD],
            mode=mode,
            prompt=prompt,
        )
