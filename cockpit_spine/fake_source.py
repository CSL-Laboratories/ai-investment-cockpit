"""An inline, deterministic stand-in source for offline use and the parity tests.

It speaks the same ``DecisionOutcome`` shape the route consumer returns and
makes no call of any kind. The recorded session runs through Recursion's fake
driver instead (see ``tests/support/record_session.py``); this one exists so
the CLI and the loopback view can run with nothing else installed.

Two prompts are scripted hooks for the failure paths: ``fail-closed`` and
``quarantine``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from successor.route_consumer import ACCEPTED, FAIL_CLOSED, NEEDS_HUMAN_REVIEW, DecisionOutcome

SYMBOLS = ("AAA", "BBB", "CCC")
FAIL_PROMPT = "fail-closed"
QUARANTINE_PROMPT = "quarantine"


def proposal_id_for(request_id: str) -> str:
    """A schema-sized id. Short request ids stay readable; long ones use a digest.

    ``prop-`` plus a 60-character request id does not fit the 64-character
    proposal id, and slicing the prefix would make two different requests collide.
    """

    plain = f"prop-{request_id}"
    if len(plain) <= 64:
        return plain
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return f"prop-{digest}"[:64]


def inline_payload(request_id: str) -> dict[str, Any]:
    seed = int(hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:8], 16)
    return {
        "proposal_id": proposal_id_for(request_id),
        "symbol": SYMBOLS[seed % len(SYMBOLS)],
        "side": "buy" if seed % 2 == 0 else "sell",
        "asset_type": "equity",
        "order_type": "limit",
        "quantity": 1 + seed % 5,
        "notional": None,
        "time_horizon": "short",
        "thesis": "Synthetic fixture thesis; expects a 3 percent move. Not analysis.",
        "invalidation": "Synthetic fixture invalidation below 10 percent.",
        "sizing_basis": "fixed-fraction",
        "source_refs": ["parquet://2026-10-26/events-1#0"],
    }


class FakeProposalSource:
    adapter_kind = "fake-inline"

    def fetch(self, *, request_id: str, mode: str, prompt: str) -> DecisionOutcome:
        request = {"id": request_id}
        if prompt == FAIL_PROMPT:
            return DecisionOutcome(
                FAIL_CLOSED, "fake_route_unavailable", None, request, None, "watch_only")
        if prompt == QUARANTINE_PROMPT:
            return DecisionOutcome(
                NEEDS_HUMAN_REVIEW, "fake_schema_invalid", None, request, None, None)
        reply = {"output_digest": "sha256:" + hashlib.sha256(request_id.encode()).hexdigest()}
        return DecisionOutcome(ACCEPTED, "ok", inline_payload(request_id), request, reply, None)
