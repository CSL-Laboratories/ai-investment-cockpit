"""Cockpit consumer adapter and decision profile for Route Contract v0 (CSL-515, WP12).

Draft. The contract is Recursion's CSL-496 draft, vendored at a pinned digest
(``contract/PIN.json``). No live route exists: every live adapter is
``unsupported`` until CSL-460, and X2 live integration is deferred.
"""

from successor.route_consumer.consumer import (
    ACCEPTED,
    FAIL_CLOSED,
    NEEDS_HUMAN_REVIEW,
    DecisionOutcome,
    RouteConsumer,
    RoutePort,
)
from successor.route_consumer.profile import (
    DECISION_PROFILE,
    WorkloadProfile,
    engines_allowed,
)
from successor.route_consumer.records import RouteBinding

__all__ = (
    "ACCEPTED",
    "DECISION_PROFILE",
    "FAIL_CLOSED",
    "NEEDS_HUMAN_REVIEW",
    "DecisionOutcome",
    "RouteBinding",
    "RouteConsumer",
    "RoutePort",
    "WorkloadProfile",
    "engines_allowed",
)
