"""The one recorded Cockpit run through Recursion's in-process adapter (decision 2, X2).

Every cell drives one Cockpit workload through ``RouteConsumer`` into the fake
driver with a scripted transport. This is a plumbing record: it shows that the
profile, the pinned contract and the Inv 2 pin work end to end. It compares
nothing about models, runs no provider, and carries no verdict. X2 live
integration is deferred until CSL-460 provides a live route.

The recorded bytes are not stored in this repo. ``records/x2-fake-driver-bakeoff.ref.json``
pins the csl-research commit, path, sha256 and blob. That pin matches
``render(run())`` on this tree. Printing the rebuilt record is local only;
``--write`` refuses, so the evidence file cannot land here again. The tests
do not fetch the vault.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from successor.route_consumer import pin
from successor.route_consumer.pin import PACKAGE_DIR
from successor.route_consumer.profile import DECISION_PROFILE, canonical_digest, load_output_schema
from successor.route_consumer.tests.support import rig as rigmod
from successor.route_consumer.tests.support.recursion_bridge import recursion

REF_PATH = PACKAGE_DIR / "records" / "x2-fake-driver-bakeoff.ref.json"
LABEL = "X2 live integration deferred"
MODES = ("paper", "auto_full")
ENGINES = ("claude", "codex")

SAMPLES: dict[str, dict[str, Any]] = {
    "workload-d01": {
        "date": "2026-10-26", "market_regime": "neutral", "portfolio_changes": [],
        "watch_items": [], "thesis_updates": [], "risk_flags": [], "proposed_actions": [],
        "source_refs": [],
    },
    "workload-d02": {
        "batch_id": "batch-1", "window_start": "2026-10-26T00:00:00Z",
        "window_end": "2026-10-26T01:00:00Z",
        "scored": [{"article_id": "a1", "symbols": ["AAA"], "classification": "watch",
                    "score": 40, "rationale": "synthetic"}],
    },
    "workload-d03": {
        "proposal_id": "prop-1", "grounded": True, "injection_suspected": False,
        "unsupported_claims": [], "source_refs_checked": ["parquet://2026-10-26/events-1#0"],
    },
    "workload-d04": {
        "proposal_id": "prop-1", "symbol": "AAA", "side": "buy", "asset_type": "equity",
        "order_type": "limit", "quantity": 1, "notional": None, "time_horizon": "short",
        "thesis": "synthetic", "invalidation": "synthetic", "sizing_basis": "fixed-fraction",
        "source_refs": ["parquet://2026-10-26/events-1#0"],
    },
    "workload-d05": {
        "question": "synthetic?", "answer": "synthetic", "pattern": "fresh", "session_id": None,
        "risk_decision_ref": None, "generated_at": "2026-10-26T00:00:00Z",
    },
}


def _script(engine: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    text = json.dumps(payload)
    if engine == "claude":
        stdout = json.dumps({"session_id": "s1", "result": text})
    else:
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "item.completed",
                        "item": {"id": "i", "type": "agent_message", "text": text}}),
            json.dumps({"type": "turn.completed"}),
        ])
    return [{"returncode": 0, "stdout": stdout, "stderr": "", "timed_out": False}]


def run() -> dict[str, Any]:
    rec = recursion()
    cells = []
    for mode in MODES:
        for workload_id, profile in sorted(DECISION_PROFILE.items()):
            for engine in ENGINES:
                rig = rigmod.make_rig(
                    rec, engine, script=_script(engine, SAMPLES[workload_id]),
                    output_schema=load_output_schema(profile),
                )
                outcome = rig.consumer.decide(
                    request_id=f"req-{workload_id}-{engine}",
                    profile=profile,
                    mode=mode,
                    prompt="synthetic prompt",
                )
                reply = outcome.reply_record
                cells.append({
                    "mode": mode,
                    "workload_id": workload_id,
                    "engine_id": engine,
                    "disposition": outcome.disposition,
                    "reason": outcome.reason,
                    "request_digest": (
                        canonical_digest(outcome.request_record) if outcome.request_record else None
                    ),
                    "output_digest": reply["output_digest"] if reply else None,
                    "route_calls": len(rig.port.submitted),
                })
    pinned = pin.load_pin()
    return {
        "label": LABEL,
        "kind": "plumbing record on the fake driver; not a model comparison; no CP7 result; no verdict",
        "adapter_kind": "fake",
        "live_calls": False,
        "contract": {
            "repository": pinned["source"]["repository"],
            "pull_request": pinned["source"]["pull_request"],
            "head_sha": pinned["source"]["head_sha"],
            "schema_sha256": pinned["vendored"][pin.SCHEMA_KEY]["sha256"],
        },
        "cells": cells,
    }


def render(record: dict[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    if "--write" in argv:
        sys.stderr.write(
            "refusing to write the X2 record into this repo; "
            "see records/x2-fake-driver-bakeoff.ref.json\n"
        )
        return 2
    sys.stdout.write(render(run()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
