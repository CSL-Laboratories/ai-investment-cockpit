"""Slice A feasibility freeze — versioned strategy contracts.

Pure deterministic validation. No LLM, no broker, no OrderGateway, no
activation. A later package freezes metrics before any qualify/watch/reject.
"""

from feasibility.validate import (
    CLAIM_ID,
    FORBIDDEN_IDS,
    FROZEN_IDS,
    SCHEMA_VERSION,
    FeasibilityError,
    QualifyBlocked,
    load_freeze,
    validate_freeze,
)

__all__ = [
    "CLAIM_ID",
    "FORBIDDEN_IDS",
    "FROZEN_IDS",
    "SCHEMA_VERSION",
    "FeasibilityError",
    "QualifyBlocked",
    "load_freeze",
    "validate_freeze",
]
