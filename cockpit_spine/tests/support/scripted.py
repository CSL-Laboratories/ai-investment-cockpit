"""A scripted proposal source and deterministic helpers for offline tests."""

from __future__ import annotations

import copy
import dataclasses
import itertools
from typing import Any

from successor.route_consumer import ACCEPTED, FAIL_CLOSED, NEEDS_HUMAN_REVIEW, DecisionOutcome
from successor.route_consumer.records import prompt_digest


def proposal_payload(proposal_id: str = "prop-1", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "proposal_id": proposal_id, "symbol": "AAA", "side": "buy", "asset_type": "equity",
        "order_type": "limit", "quantity": 1, "notional": None, "time_horizon": "short",
        "thesis": "synthetic", "invalidation": "synthetic", "sizing_basis": "fixed-fraction",
        "source_refs": ["parquet://2026-10-26/events-1#0"],
    }
    payload.update(overrides)
    return payload


def accepted(payload: dict[str, Any]) -> DecisionOutcome:
    return DecisionOutcome(ACCEPTED, "ok", copy.deepcopy(payload), {"id": "r"},
                           {"output_digest": "sha256:" + "2" * 64}, None)


def fail_closed(reason: str = "timeout") -> DecisionOutcome:
    return DecisionOutcome(FAIL_CLOSED, reason, None, {"id": "r"}, None, "watch_only")


def quarantined(reason: str = "schema_invalid") -> DecisionOutcome:
    return DecisionOutcome(NEEDS_HUMAN_REVIEW, reason, None, {"id": "r"}, None, None)


def bound_to_call(outcome: DecisionOutcome, request_id: str, prompt: str) -> DecisionOutcome:
    """Make an outcome built with the placeholder request record answer this call."""

    if outcome.request_record != {"id": "r"}:
        return outcome
    reply = outcome.reply_record
    return dataclasses.replace(
        outcome,
        request_record={"id": request_id, "prompt_digest": prompt_digest(prompt)},
        reply_record=None if reply is None else {**reply, "request_id": request_id},
    )


class ScriptedSource:
    adapter_kind = "fake"

    def __init__(self, *outcomes: DecisionOutcome) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, str]] = []

    def fetch(self, *, request_id: str, mode: str, prompt: str) -> DecisionOutcome:
        self.calls.append({"request_id": request_id, "mode": mode, "prompt": prompt})
        return bound_to_call(self._outcomes.pop(0), request_id, prompt)


def counter_clock() -> Any:
    ticks = itertools.count(1)
    return lambda: f"2026-10-26T20:00:{next(ticks):02d}Z"
