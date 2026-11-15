"""Research cases and AI proposals (lane COCKPIT-PROPOSALS, first slice).

Made-up data only in this slice: no route call, no Recursion dependency, nothing from the
paper stack. ``schema`` holds ``cockpit.research_case/1`` and ``cockpit.proposal/1``;
the module ``canonical`` the restricted canonical JSON every digest and signed file here
uses (import it as a module: its functions are not re-exported, so the name
``cockpit.research.canonical`` always means the module).
"""

from .canonical import DIGEST_FORM
from .errors import Refused
from .schema import (CASE_SCHEMA, CONVICTION_FULL_BPS, PROPOSAL_SCHEMA, Proposal, ResearchCase,
                     case_from_value, parse_case, parse_proposal, proposal_from_value)

__all__ = [
    "CASE_SCHEMA", "CONVICTION_FULL_BPS", "DIGEST_FORM", "PROPOSAL_SCHEMA", "Proposal", "Refused",
    "ResearchCase", "case_from_value", "parse_case", "parse_proposal", "proposal_from_value",
]
