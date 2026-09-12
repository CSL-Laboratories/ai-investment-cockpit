"""Slice A feasibility freeze, evaluation, cases, protocol, and screens.

Pure deterministic validation. No LLM, no broker, no OrderGateway, no
activation. RW-27 froze the shortlist. RW-28 emits watch/reject with
zero qualify. RW-29 inspects cited operational cases. RW-30 freezes the
pre-measurement and model-judge protocol without starting measurement.
RW-31 records GET-only screen counts and a cohort/label plan only.
RW-32 pins the 2026-09-12 operator seal without lowering the cohort gate.
"""

from feasibility.evaluate import (
    apply_metrics,
    load_evaluation,
    load_metrics,
    validate_evaluation,
)
from feasibility.inspect import load_cases, validate_cases
from feasibility.protocol import load_protocol, validate_protocol
from feasibility.screens import load_screens, validate_screens
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
    "load_cases",
    "validate_cases",
    "load_protocol",
    "validate_protocol",
    "load_screens",
    "validate_screens",
]
