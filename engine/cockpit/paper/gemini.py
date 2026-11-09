"""Gemini venue kill (B12/B28, F44, CSL-743), paper and mock only.

No Gemini order code exists in ``engine/``. This module has no network, credential or
keyring code either: the kill takes a transport capability that only the
credential-owning host may supply, and this slice tests it with mocks only.

PRODUCT.md 4.2 (fail mode and kill switch): the venue credential is an OAuth grant, and
the kill cancels working orders. After the order halt is set in one committed step:

* one cancel-all operation is attempted, then the open orders are listed. Cancel is
  ``confirmed`` only when the venue lists none open. A timeout, error, unreadable reply
  or any order still listed keeps it ``pending``. A failure never skips the revoke;
* the OAuth grant is revoked, and the revoke is ``confirmed`` only when an authenticated
  call made after it is refused as unauthorized. Otherwise it is ``pending``.

Which Gemini API operations back ``cancel_all`` and ``list_open_orders`` under an OAuth
grant is not verified here; the transport is a mock (see the PR's gaps).

The overall status is ``confirmed`` only when halt, cancel and revoke all are. Calling
``kill`` again keeps the halt and retries only what is not yet confirmed.
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
        else:
            self._state["cancel_all"] = "confirmed"
            self._detail.pop("cancel_all", None)

    def _revoke(self) -> None:
        try:
            out = self._transport.revoke_oauth_grant()
        except Exception as exc:  # noqa: BLE001
            self._detail["revoke_grant"] = "error:" + type(exc).__name__
            out = None
        if type(out) is dict and out.get("accepted") is False:
            self._detail["revoke_grant"] = "rejected"
        # Confirmation is only an authenticated call refused after the revoke.
        try:
            probe = self._transport.probe_authenticated()
        except Exception as exc:  # noqa: BLE001
            self._detail.setdefault("revoke_grant", "probe_error:" + type(exc).__name__)
            return
        if probe == "unauthorized":
            self._state["revoke_grant"] = "confirmed"
            self._detail.pop("revoke_grant", None)
        else:
            self._detail.setdefault("revoke_grant", "probe:" + str(probe)[:32])

    def kill(self) -> dict:
        with self._lock:
            try:
                self._halt()
                self._state["halt"] = "confirmed"
            except Exception as exc:  # noqa: BLE001 - still try the risk-reducing calls
                self._detail["halt"] = "error:" + type(exc).__name__
            if self._state["cancel_all"] != "confirmed":
                self._cancel()
            if self._state["revoke_grant"] != "confirmed":
                self._revoke()
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "gemini", "status": status, **self._state, "detail": dict(self._detail)}
