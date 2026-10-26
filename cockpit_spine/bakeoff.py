"""Bakeoff plumbing for CP3, with the result-record step stubbed and BLOCKED on WP8.

Lucas's rule (CSL-522, decision 8): science before capture. The SP1 minimum and
the CP7 preregistration protocol (WP8, CSL-518, csl-research#47) merge before
any CP3 result record, and nothing is called a verdict on Nov 11. So this module
can plan arms, drive trials through the route consumer into a fake driver, and
return the raw trial batch. It cannot turn that batch into a result record, and
there is no code path that writes one.

TODO(WP8): when the CP7 protocol record has merged on csl-research main and its
exact digest is recorded here, implement ``seal_result_record`` in a separate,
reviewed change. Until then it raises ``BlockedOnProtocol`` unconditionally.
Do not set ``WP8_PROTOCOL`` to make a test pass.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from successor.route_consumer import DECISION_PROFILE, DecisionOutcome
from successor.route_consumer.profile import canonical_digest

# TODO(WP8): None until the CP7 protocol (csl-research#47, CSL-518) has merged. Only a human
# sets this, from a merged record's exact digest.
WP8_PROTOCOL: dict[str, str] | None = None

BATCH_KIND = "raw trial batch; plumbing only; not a result record; no verdict"
BLOCKED_ON = "WP8 (CP7 preregistration and power protocol, CSL-518, csl-research#47)"
ENGINES = ("claude", "codex")
WORKLOAD = "workload-d04"
MODE = "paper"
MAX_TRIALS = 20


class BlockedOnProtocol(Exception):
    """The result-record step is blocked, by design, until WP8 has merged."""


class Consumer(Protocol):
    def decide(
        self, *, request_id: str, profile: Any, mode: str, prompt: str,
        output_schema: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
        """The WP12 consumer's one entry point."""


@dataclass(frozen=True)
class Plan:
    """Arms are engines; trials are repeats of one fixed synthetic request."""

    engines: tuple[str, ...] = ENGINES
    trials: int = 2
    prompt: str = "synthetic prompt"

    def check(self) -> None:
        if not self.engines or not set(self.engines) <= set(ENGINES):
            raise ValueError(f"engines must be a non-empty subset of {ENGINES}")
        if len(set(self.engines)) != len(self.engines):
            raise ValueError("an engine may appear once")
        if not isinstance(self.trials, int) or isinstance(self.trials, bool):
            raise ValueError("trials must be an integer")
        if not 1 <= self.trials <= MAX_TRIALS:
            raise ValueError(f"trials must be 1 to {MAX_TRIALS}")


def run_trials(plan: Plan, consumer_for: Callable[[str], Consumer]) -> dict[str, Any]:
    """Drive every planned trial through the consumer and return the raw batch.

    The batch lists what the route said, trial by trial. It holds no score,
    ranking, return or comparison, and it is explicitly unsealed.
    """

    plan.check()
    trials = []
    for engine in plan.engines:
        consumer = consumer_for(engine)
        for number in range(1, plan.trials + 1):
            outcome = consumer.decide(
                request_id=f"bk-{engine}-{number}", profile=DECISION_PROFILE[WORKLOAD],
                mode=MODE, prompt=plan.prompt,
            )
            reply = outcome.reply_record
            trials.append({
                "arm": engine,
                "trial": number,
                "disposition": outcome.disposition,
                "reason": outcome.reason,
                "request_digest": (
                    canonical_digest(outcome.request_record) if outcome.request_record else None
                ),
                "output_digest": reply["output_digest"] if reply else None,
            })
    return {
        "kind": BATCH_KIND,
        "sealed": False,
        "protocol_ref": None,
        "workload_id": WORKLOAD,
        "mode": MODE,
        "plan": {"engines": list(plan.engines), "trials": plan.trials},
        "trials": trials,
    }


def record_step_status() -> dict[str, Any]:
    """What the record step is waiting on, for any surface that wants to say so."""

    return {
        "step": "result record",
        "state": "blocked" if WP8_PROTOCOL is None else "protocol pinned, writer not implemented",
        "blocked_on": BLOCKED_ON,
        "todo": (
            "TODO(WP8): pin the merged CP7 protocol digest, then add the writer in its own change"
        ),
        "result_records_written": 0,
    }


def seal_result_record(batch: dict[str, Any]) -> None:
    """Turn a raw batch into a CP3 result record. Not available: blocked on WP8."""

    if WP8_PROTOCOL is None:
        raise BlockedOnProtocol(f"no result record before the protocol: blocked on {BLOCKED_ON}")
    raise BlockedOnProtocol(
        "the protocol is pinned but the result-record writer is deliberately not implemented; "
        "see TODO(WP8)"
    )
