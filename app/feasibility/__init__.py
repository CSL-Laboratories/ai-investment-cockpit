"""Slice A feasibility freeze and evaluation.

Pure deterministic validation. No LLM, no broker, no OrderGateway, no
activation. RW-27 froze the shortlist. RW-28 freezes metrics and emits
watch/reject. No candidate qualifies in this corpus.
"""

from feasibility.evaluate import (
    apply_metrics,
    load_evaluation,
    load_metrics,
    validate_evaluation,
)
from feasibility.validate import (
    CLAIM_ID,
    FORBIDDEN_IDS,
    FROZEN_IDS,
    SCHEMA_VERSION,
    DecisiveVerdictBlocked,
    FeasibilityError,
    load_freeze,
    validate_freeze,
)

__all__ = [
    "CLAIM_ID",
    "FORBIDDEN_IDS",
    "FROZEN_IDS",
    "SCHEMA_VERSION",
    "FeasibilityError",
    "DecisiveVerdictBlocked",
    "load_freeze",
    "validate_freeze",
    "apply_metrics",
    "load_evaluation",
    "load_metrics",
    "validate_evaluation",
]
