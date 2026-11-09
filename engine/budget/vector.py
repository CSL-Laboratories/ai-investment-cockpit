"""Dollar reservation on BudgetVector (CSL-753, handoff F37, Q44).

Spec pin: ``contracts/PRODUCT.md`` in CSL-Laboratories/csl-research at
``055cc7a5c793b7135e5d0565dc4198cf96230531``,
SHA-256 ``e64783152f6d67d4ede774dc41d112fe46c2d7536d53cb2c04e71a3566044daf``.
Pointer: CSLR-D-011. Candidate bytes. This module does not accept them.

Paper-only is PRODUCT.md section 5.2 and CP0-v2. A charge here is a mock ledger
row. It does not place an order, call a venue, or move capital.

Q44, as the handoff states it: a dollar reservation in BudgetVector defaulting
to 0, an opt-in flag tied to ``metered_spend_allowed``, and a user cap. A route
with no opt-in can never spend. Amounts are integers in the F9 canonical range
(-(2^53-1) .. 2^53-1). Canonical bytes stay with F9 (recursion #339,
``csl-canonical/1``). This module does not reimplement that form.

The other F37 schemas (signer fields, EffectIntent, IsolationMode, grant
envelope, run, event, approval, profile) are not invented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_MINOR = 2**53 - 1
_ROUTE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


class Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _minor(value: object, *, allow_zero: bool) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise Refused("amount_invalid")
    if value > MAX_MINOR or value < -MAX_MINOR:
        raise Refused("amount_invalid")
    if value < 0 or (value == 0 and not allow_zero):
        raise Refused("amount_invalid")
    return value


@dataclass(frozen=True)
class RouteRecord:
    """P0 route field this prototype uses: the metered-spend opt-in. Default off."""

    route_id: str
    metered_spend_allowed: bool = False

    def __post_init__(self) -> None:
        if type(self.route_id) is not str or not _ROUTE_ID.fullmatch(self.route_id):
            raise Refused("route_invalid")
        if type(self.metered_spend_allowed) is not bool:
            raise Refused("opt_in_invalid")


@dataclass(frozen=True)
class BudgetVector:
    """Reserved dollars and the user cap, in integer minor units.

    ``dollar_reservation_minor`` defaults to 0, so a fresh vector can fund no
    charge. ``spent_minor`` is what this vector has already charged.
    """

    user_cap_minor: int
    dollar_reservation_minor: int = 0
    spent_minor: int = 0

    def __post_init__(self) -> None:
        cap = _minor(self.user_cap_minor, allow_zero=True)
        reserved = _minor(self.dollar_reservation_minor, allow_zero=True)
        spent = _minor(self.spent_minor, allow_zero=True)
        if spent > cap:
            raise Refused("invalid_budget")
        object.__setattr__(self, "user_cap_minor", cap)
        object.__setattr__(self, "dollar_reservation_minor", reserved)
        object.__setattr__(self, "spent_minor", spent)


def charge(route: RouteRecord, vector: BudgetVector, amount_minor: object) -> tuple[BudgetVector, dict]:
    """Refuse or return the next vector and one ledger row. Does not mutate inputs."""
    if type(route) is not RouteRecord or type(vector) is not BudgetVector:
        raise Refused("budget_invalid")
    amount = _minor(amount_minor, allow_zero=False)
    if route.metered_spend_allowed is not True:
        raise Refused("metered_spend_not_opted_in")
    if vector.spent_minor + amount > vector.user_cap_minor:
        raise Refused("over_user_cap")
    if amount > vector.dollar_reservation_minor:
        raise Refused("over_reservation")
    nxt = BudgetVector(
        user_cap_minor=vector.user_cap_minor,
        dollar_reservation_minor=vector.dollar_reservation_minor - amount,
        spent_minor=vector.spent_minor + amount,
    )
    return nxt, {
        "route_id": route.route_id,
        "amount_minor": amount,
        "reservation_after": nxt.dollar_reservation_minor,
        "spent_after": nxt.spent_minor,
    }


class MockMeter:
    """In-process metered endpoint. A refused charge appends nothing."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def charge(self, route: RouteRecord, vector: BudgetVector, amount_minor: object) -> BudgetVector:
        nxt, row = charge(route, vector, amount_minor)
        self.rows.append(dict(row, seq=len(self.rows) + 1))
        return nxt
