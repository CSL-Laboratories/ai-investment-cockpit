"""Canonical binding of the complete proposal body to its signed audit event."""
from __future__ import annotations

import hashlib
import json


def proposal_payload_hash(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError("proposal payload must be an object")
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
