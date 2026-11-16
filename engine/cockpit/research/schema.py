"""``cockpit.research_case/1`` and ``cockpit.proposal/1``: the case a research run answers
and the typed proposal it may return.

A proposal carries conviction, never a quantity (grill Q30: "Proposals carry conviction,
not quantity, and the gateway sizes orders"). Sizing belongs to the gateway, against the
sourced caps, and is not in this package. So a proposal is refused when it has:

* any field outside the seven below (``unknown_field``), and with the more specific code
  ``quantity_forbidden`` when the field's name reads as a size, an amount, a price or a
  share of a book (``quantity``, ``qty``, ``size``, ``notional_minor``, ``limit_price``,
  ``weight``, ``allocation_pct`` and so on), so a price cannot stand in for a size;
* a number other than ``conviction_bps``, which is an integer in basis points,
  ``1..10_000`` (the paper gateway's ``CONVICTION_FULL_BPS`` scale);
* a value of the wrong type, including a ``bool`` where an integer is meant, a float, a
  subclass of ``str``, ``int``, ``list`` or ``dict``, or text outside printable ASCII
  (the canonical form is ASCII-only; see ``canonical``).

A proposal's text comes from a model and is untrusted: ``parse_proposal`` takes exactly
one JSON object and nothing around it. The rationale is kept as text; nothing here reads
a number out of it, and nothing downstream may.

The case's ``evidence_refs`` name evidence by id and SHA-256 so that the raw inputs are
stored apart from any summary (invariant 8); a proposal's ``citations`` must each name one
of them. That check needs the case: ``parse_proposal`` and ``proposal_from_value`` make it
only when ``case=`` is given (the decision record always gives it); without it they check
only that each citation is a well-formed id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .canonical import digest, loads_untrusted
from .errors import Refused

__all__ = [
    "CASE_SCHEMA", "CONVICTION_FULL_BPS", "PROPOSAL_SCHEMA", "Proposal", "ResearchCase",
    "case_from_value", "parse_case", "parse_proposal", "proposal_from_value",
]

CASE_SCHEMA = "cockpit.research_case/1"
PROPOSAL_SCHEMA = "cockpit.proposal/1"
CONVICTION_FULL_BPS = 10_000

MAX_CASE_CHARS = 64 * 1024
MAX_PROPOSAL_CHARS = 64 * 1024
MAX_QUESTION = 2_000
MAX_RATIONALE = 4_000
MAX_EVIDENCE = 64
MAX_CITATIONS = 32
MAX_TURNS = 200
MAX_OUTPUT_BYTES = 1 << 20

_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}")
_ROUTE = re.compile(r"[a-z0-9][a-z0-9_.:/@-]{0,127}")
_HORIZON = re.compile(r"P[1-9][0-9]{0,2}[DWM]")
_SHA256 = re.compile(r"[0-9a-f]{64}")

_CASE_FIELDS = frozenset({"schema", "case_id", "question", "instrument", "evidence_refs", "route",
                          "budget"})
_EVIDENCE_FIELDS = frozenset({"ref_id", "sha256"})
_BUDGET_FIELDS = frozenset({"max_turns", "max_output_bytes"})
_PROPOSAL_FIELDS = frozenset({"schema", "instrument", "side", "conviction_bps", "horizon",
                              "rationale", "citations"})
_SIDES = ("buy", "sell")

# A field name that reads as a size is refused by name. Matched on the key lower-cased with
# everything but letters and digits removed, as a substring, so ``orderQty``, ``max-size``,
# ``notional_minor`` and ``Limit Price`` all match. Only keys outside the seven fields are
# tested, so the list can be broad.
_QUANTITY_WORDS = (
    "qty", "quantit", "size", "sizing", "notional", "amount", "share", "contract", "unit", "lot",
    "volume", "price", "dollar", "usd", "cash", "exposure", "weight", "alloc", "position",
    "leverage", "stake", "minor", "budget", "percent", "pct", "fraction", "count", "number",
    "num", "value", "cost", "spend", "risk", "kelly", "margin",
)


def _quantity_like(key: str) -> bool:
    folded = "".join(ch for ch in key.lower() if ch.isascii() and ch.isalnum())
    return any(word in folded for word in _QUANTITY_WORDS)


def _exact_fields(value: object, fields: frozenset, where: str, *, quantity_words: bool = False) -> dict:
    if type(value) is not dict:
        raise Refused("wrong_type", where)
    for key in value:
        if type(key) is not str:
            raise Refused("wrong_type", f"{where} key")
        if key not in fields:
            if quantity_words and _quantity_like(key):
                raise Refused("quantity_forbidden", key if key.isascii() and key.isprintable() else "")
            raise Refused("unknown_field", key if key.isascii() and key.isprintable() else "")
    for key in sorted(fields):
        if key not in value:
            raise Refused("missing_field", f"{where}.{key}")
    return value


def _str(value: object, field: str, pattern: re.Pattern) -> str:
    if type(value) is not str:
        raise Refused("wrong_type", field)
    if not pattern.fullmatch(value):
        raise Refused("invalid_value", field)
    return value


def _text(value: object, field: str, max_len: int) -> str:
    """Printable ASCII plus newline and tab, 1..max_len characters."""
    if type(value) is not str:
        raise Refused("wrong_type", field)
    if not value.isascii():
        raise Refused("text_not_ascii", field)
    if not 1 <= len(value) <= max_len:
        raise Refused("invalid_length", field)
    for ch in value:
        if not (ch.isprintable() or ch in "\n\t"):
            raise Refused("text_control_character", field)
    if not value.strip():
        raise Refused("invalid_value", f"{field} is blank")
    return value


def _int(value: object, field: str, low: int, high: int) -> int:
    if type(value) is not int:
        raise Refused("wrong_type", field)
    if not low <= value <= high:
        raise Refused("out_of_range", field)
    return value


def _list(value: object, field: str, low: int, high: int) -> list:
    if type(value) is not list:
        raise Refused("wrong_type", field)
    if not low <= len(value) <= high:
        raise Refused("invalid_length", field)
    return value


@dataclass(frozen=True, slots=True)
class ResearchCase:
    case_id: str
    question: str
    instrument: str
    evidence_refs: tuple  # ((ref_id, sha256_hex), ...) in the case's order
    route: str
    max_turns: int
    max_output_bytes: int

    def value(self) -> dict:
        return {
            "schema": CASE_SCHEMA,
            "case_id": self.case_id,
            "question": self.question,
            "instrument": self.instrument,
            "evidence_refs": [{"ref_id": r, "sha256": h} for r, h in self.evidence_refs],
            "route": self.route,
            "budget": {"max_turns": self.max_turns, "max_output_bytes": self.max_output_bytes},
        }

    def digest(self) -> str:
        return digest(self.value())

    def ref_ids(self) -> frozenset:
        return frozenset(r for r, _ in self.evidence_refs)


@dataclass(frozen=True, slots=True)
class Proposal:
    instrument: str
    side: str
    conviction_bps: int
    horizon: str
    rationale: str
    citations: tuple

    def value(self) -> dict:
        return {
            "schema": PROPOSAL_SCHEMA,
            "instrument": self.instrument,
            "side": self.side,
            "conviction_bps": self.conviction_bps,
            "horizon": self.horizon,
            "rationale": self.rationale,
            "citations": list(self.citations),
        }

    def digest(self) -> str:
        return digest(self.value())


def case_from_value(value: object) -> ResearchCase:
    """Validate a decoded case and return it. Every field is required and exact."""
    value = _exact_fields(value, _CASE_FIELDS, "case")
    if type(value["schema"]) is not str or value["schema"] != CASE_SCHEMA:
        raise Refused("schema_mismatch", "case")
    case_id = _str(value["case_id"], "case_id", _ID)
    question = _text(value["question"], "question", MAX_QUESTION)
    instrument = _str(value["instrument"], "instrument", _INSTRUMENT)
    refs = []
    seen = set()
    for item in _list(value["evidence_refs"], "evidence_refs", 1, MAX_EVIDENCE):
        item = _exact_fields(item, _EVIDENCE_FIELDS, "evidence_refs[]")
        ref_id = _str(item["ref_id"], "evidence_refs[].ref_id", _ID)
        sha = _str(item["sha256"], "evidence_refs[].sha256", _SHA256)
        if ref_id in seen:
            raise Refused("duplicate_value", "evidence_refs[].ref_id")
        seen.add(ref_id)
        refs.append((ref_id, sha))
    route = _str(value["route"], "route", _ROUTE)
    budget = _exact_fields(value["budget"], _BUDGET_FIELDS, "budget")
    max_turns = _int(budget["max_turns"], "budget.max_turns", 1, MAX_TURNS)
    max_output = _int(budget["max_output_bytes"], "budget.max_output_bytes", 1, MAX_OUTPUT_BYTES)
    return ResearchCase(case_id, question, instrument, tuple(refs), route, max_turns, max_output)


def parse_case(text: object) -> ResearchCase:
    """Parse a case from JSON text."""
    return case_from_value(loads_untrusted(text, max_chars=MAX_CASE_CHARS))


def proposal_from_value(value: object, *, case: ResearchCase | None = None) -> Proposal:
    """Validate a decoded proposal. With ``case``, its instrument and citations must match it."""
    value = _exact_fields(value, _PROPOSAL_FIELDS, "proposal", quantity_words=True)
    if type(value["schema"]) is not str or value["schema"] != PROPOSAL_SCHEMA:
        raise Refused("schema_mismatch", "proposal")
    instrument = _str(value["instrument"], "instrument", _INSTRUMENT)
    side = value["side"]
    if type(side) is not str:
        raise Refused("wrong_type", "side")
    if side not in _SIDES:
        raise Refused("invalid_value", "side")
    conviction = _int(value["conviction_bps"], "conviction_bps", 1, CONVICTION_FULL_BPS)
    horizon = _str(value["horizon"], "horizon", _HORIZON)
    rationale = _text(value["rationale"], "rationale", MAX_RATIONALE)
    citations = []
    for ref in _list(value["citations"], "citations", 1, MAX_CITATIONS):
        ref = _str(ref, "citations[]", _ID)
        if ref in citations:
            raise Refused("duplicate_value", "citations[]")
        citations.append(ref)
    if case is not None:
        if type(case) is not ResearchCase:
            raise Refused("wrong_type", "case")
        if instrument != case.instrument:
            raise Refused("instrument_mismatch")
        known = case.ref_ids()
        for ref in citations:
            if ref not in known:
                raise Refused("citation_unknown", ref)
    return Proposal(instrument, side, conviction, horizon, rationale, tuple(citations))


def parse_proposal(text: object, *, case: ResearchCase | None = None) -> Proposal:
    """Parse a model's output as exactly one ``cockpit.proposal/1`` object, untrusted."""
    return proposal_from_value(loads_untrusted(text, max_chars=MAX_PROPOSAL_CHARS), case=case)
