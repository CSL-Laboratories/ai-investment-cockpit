"""Typed OAuth-revoke reply and probe classification (F44, CSL-743), paper and mock only.

Shared by the Coinbase and Gemini kills only. No network, credential or keyring code:
the transport that returns a ``RevokeReply`` and answers the probes is supplied by the
credential-owning host, and this slice tests it with mocks only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

__all__ = ["PROBE_VALUES", "RevokeReply", "classify_probe"]

PROBE_VALUES = ("unauthorized", "authorized")


@dataclass(frozen=True)
class RevokeReply:
    """The venue's HTTP answer to the revoke call.

    The venue answering 200 is NOT proof that access is gone: RFC 7009 servers answer 200
    even for an invalid or already-revoked token. Proof is only the two unauthorized
    probes made after it (an authenticated API call and a refresh-token exchange).
    """

    http_status: int

    @property
    def answered_200(self) -> bool:
        return type(self.http_status) is int and self.http_status == 200


def classify_probe(call: Callable[[], object]) -> str:
    """Run one probe and return a value from the closed set.

    ``unauthorized`` or ``authorized`` only when the reply is exactly that string;
    ``unreadable_probe`` for any other reply (no part of it is stored);
    ``error:<Exc>`` when the probe raises.
    """
    try:
        reply = call()
    except Exception as exc:  # noqa: BLE001 - any failure is "unknown", never "unauthorized"
        return "error:" + type(exc).__name__
    if type(reply) is str and reply in PROBE_VALUES:
        return reply
    return "unreadable_probe"
