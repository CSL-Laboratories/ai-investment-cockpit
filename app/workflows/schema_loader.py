"""WP8 — cached JSON-schema loader for decision-stage agent calls (contract Owner A).

Implements the WP8 'schema_loader.py' section of the frozen contract and Invariant 3
(docs/build-scaffold-ultracode.md WP8): every decision-stage ``AgentSpec`` must carry the
loaded jsonschema dict for its output contract (app/schemas/json/*.schema.json). There is
deliberately no shared loader elsewhere in app code — this is it.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas" / "json"


@cache
def load_schema(name: str) -> dict:
    """Load ``app/schemas/json/{name}.schema.json`` as a dict (cached).

    ``name`` is the schema title, e.g. ``"TriageResult"`` / ``"MorningBrief"`` /
    ``"TradeProposal"`` / ``"GroundingCheck"`` / ``"InterrogationAnswer"``. Callers must
    treat the returned dict as read-only (it is shared via the cache).
    """
    path = _SCHEMA_DIR / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))
