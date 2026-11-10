"""Gemini venue kill (B12/B28, F44, CSL-743), paper and mock only.

No Gemini order code exists in ``engine/``. This module has no network, credential or
keyring code either: the kill takes a transport capability that only the
credential-owning host may supply, and this slice tests it with mocks only.

PRODUCT.md 4.2 (fail mode and kill switch): the venue credential is an OAuth grant, and
the kill cancels working orders. After the order halt is set in one committed step:

* one cancel-all operation is attempted, then the open orders are listed. Cancel is
  ``confirmed`` only when the venue lists none open. A timeout, error, unreadable reply
  or any order still listed keeps it ``pending``. So does an empty listing that still
  carries a ``cursor``, a ``truncated`` other than ``False`` or a ``remaining`` other
  than ``0`` (unfinished). A failure never skips the revoke;
* the OAuth grant is revoked, and the revoke is ``confirmed`` only when the revoke call
  itself was accepted (``accepted`` exactly ``True``) and an authenticated call made
  after it is refused as unauthorized. Otherwise it is ``pending``: a rejected or raised
  revoke keeps its ``rejected`` or ``error:`` detail even when the probe says unauthorized.

Which Gemini API operations back ``cancel_all`` and ``list_open_orders`` under an OAuth
grant is not verified here; the transport is a mock (see the PR's gaps).

The overall status is ``confirmed`` only when halt, cancel and revoke all are. Calling
``kill`` again keeps the halt and retries what is not yet confirmed. A cancel confirm is
kept only once the revoke is confirmed too: while the revoke is pending, each ``kill``
cancels and lists again before the revoke. A cancel that confirms in a ``kill`` whose
halt call failed is not kept either.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

__all__ = ["GeminiKill", "GeminiKillTransport"]


class GeminiKillTransport(Protocol):
    """Supplied only by the credential-owning host. Each method is one venue API operation."""

    def cancel_all(self) -> dict: ...          # {"cancelled": [str]}
    def list_open_orders(self) -> dict: ...    # {"order_ids": [str]}
    def revoke_oauth_grant(self) -> dict: ...  # {"accepted": bool}
    def probe_authenticated(self) -> str: ...  # "unauthorized" | "authorized"


def _list_finished(listed: dict) -> bool:
    """True only when the list payload does not say more orders may follow."""
    if listed.get("cursor") is not None:
        return False
    if listed.get("truncated", False) is not False:
        return False
    remaining = listed.get("remaining", 0)
    return type(remaining) is int and remaining == 0


class GeminiKill:
    def __init__(self, halt: Callable[[], object], transport: GeminiKillTransport) -> None:
        self._halt = halt
        self._transport = transport
        self._lock = threading.Lock()
        self._state = {"halt": "pending", "cancel_all": "pending", "revoke_grant": "pending"}
        self._detail: dict[str, str] = {}

    def _cancel(self) -> None:
        try:
            self._transport.cancel_all()
        except Exception as exc:  # noqa: BLE001 - any failure is "unknown", never "done"
            self._detail["cancel_all"] = "error:" + type(exc).__name__
            return
        # Confirmation is the venue listing no open order after the cancel, not its reply.
        try:
            out = self._transport.list_open_orders()
        except Exception as exc:  # noqa: BLE001
            self._detail["cancel_all"] = "list_error:" + type(exc).__name__
            return
        ids = out.get("order_ids") if type(out) is dict else None
        if type(ids) is not list or not all(type(i) is str and i for i in ids):
            self._detail["cancel_all"] = "unreadable_list"
        elif ids:
            self._detail["cancel_all"] = "remaining:%d" % len(ids)
        elif not _list_finished(out):
            self._detail["cancel_all"] = "unfinished_list"
        else:
            self._state["cancel_all"] = "confirmed"
            self._detail.pop("cancel_all", None)

    def _revoke(self) -> None:
        accepted = False
        try:
            out = self._transport.revoke_oauth_grant()
        except Exception as exc:  # noqa: BLE001
            self._detail["revoke_grant"] = "error:" + type(exc).__name__
        else:
            accepted = type(out) is dict and out.get("accepted") is True
            if accepted:
                self._detail.pop("revoke_grant", None)  # an earlier attempt's detail is not this one's
            elif type(out) is dict and out.get("accepted") is False:
                self._detail["revoke_grant"] = "rejected"
            else:
                self._detail["revoke_grant"] = "unreadable_response"
        # Confirmation needs both: this revoke accepted, and an authenticated call refused
        # after it. An unauthorized probe after a rejected or raised revoke confirms nothing.
        try:
            probe = self._transport.probe_authenticated()
        except Exception as exc:  # noqa: BLE001
            self._detail.setdefault("revoke_grant", "probe_error:" + type(exc).__name__)
            return
        if probe != "unauthorized":
            self._detail.setdefault("revoke_grant", "probe:" + str(probe)[:32])
        elif accepted:
            self._state["revoke_grant"] = "confirmed"
            self._detail.pop("revoke_grant", None)

    def kill(self) -> dict:
        with self._lock:
            halt_failed = False
            try:
                self._halt()
                self._state["halt"] = "confirmed"
                self._detail.pop("halt", None)
            except Exception as exc:  # noqa: BLE001 - still try the risk-reducing calls
                halt_failed = True
                self._detail["halt"] = "error:" + type(exc).__name__
            # An earlier cancel confirm is kept only once the revoke is confirmed too.
            if self._state["cancel_all"] != "confirmed" or self._state["revoke_grant"] != "confirmed":
                self._state["cancel_all"] = "pending"
                self._cancel()
                if halt_failed and self._state["cancel_all"] == "confirmed":
                    # The halt call failed in this kill: do not keep the confirm.
                    self._state["cancel_all"] = "pending"
                    self._detail["cancel_all"] = "halt_failed"
            if self._state["revoke_grant"] != "confirmed":
                self._revoke()
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "gemini", "status": status, **self._state, "detail": dict(self._detail)}
