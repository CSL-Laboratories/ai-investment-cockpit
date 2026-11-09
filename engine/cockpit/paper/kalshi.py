"""Kalshi: model-fed use switches (B13, CSL-704) and the Kalshi-only venue kill (B12, CSL-743).

No Kalshi order code exists in ``engine/``, not even for demo. This module has no
network, credential or keyring code either: the kill takes a transport capability that
only the credential-owning host may supply, and this slice tests it with mocks only.

Use switches. Every Kalshi use beyond Lucas's own trading starts off until Lucas or his
counsel answers CX-144. A switch turns on only through an explicit constructor argument
of exactly ``True``; nothing reads the environment, a file or a plugin setting. A
model-fed read or proposal is refused *before* the reader is called.

Kill. The order halt is set first, in one committed step. Then one cancel-all API
operation and one API-key revoke operation are attempted; a failure of either never
skips the other. Status is truthful:

* cancel-all is ``confirmed`` only when a fresh listing after the cancel-all
  shows no open order. The cancel-all reply's own ``remaining: 0`` is not
  confirmation (PRODUCT.md 4.2, K1). A timeout or error is ``pending``
  (unknown), a capped cancel that leaves orders is ``pending`` with the
  remaining count, and any order the listing still shows is ``pending``;
* the key revoke is ``confirmed`` only when an authenticated call made after it is
  refused as unauthorized. Otherwise it is ``pending``.

The overall status is ``confirmed`` only when both are. Calling ``kill`` again keeps
the halt and retries only what is not yet confirmed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from .gateway import Refused

__all__ = ["KalshiKill", "KalshiKillTransport", "KalshiModelFeed", "KalshiUseSwitches"]


@dataclass(frozen=True)
class KalshiUseSwitches:
    """Off by default (CX-144 unanswered). Only an explicit ``True`` turns a switch on."""

    model_fed_read: bool = False
    model_fed_propose: bool = False

    def __post_init__(self) -> None:
        for value in (self.model_fed_read, self.model_fed_propose):
            if type(value) is not bool:
                raise Refused("switch_value_invalid")


class KalshiModelFeed:
    def __init__(self, switches: KalshiUseSwitches) -> None:
        if type(switches) is not KalshiUseSwitches:
            raise Refused("switch_value_invalid")
        self._switches = switches

    def model_read(self, reader: Callable[[], object]) -> object:
        if self._switches.model_fed_read is not True:
            raise Refused("kalshi_model_fed_read_off_cx144")
        return reader()

    def model_propose(self, proposer: Callable[[], object]) -> object:
        if self._switches.model_fed_propose is not True:
            raise Refused("kalshi_model_fed_propose_off_cx144")
        return proposer()


class KalshiKillTransport(Protocol):
    """Supplied only by the credential-owning host. Each method is one venue API operation."""

    def cancel_all(self) -> dict: ...          # {"remaining": int}
    def list_open_orders(self) -> dict: ...    # {"order_ids": [str]}
    def revoke_api_key(self) -> dict: ...      # {"accepted": bool}
    def probe_authenticated(self) -> str: ...  # "unauthorized" | "authorized"


class KalshiKill:
    def __init__(self, halt: Callable[[], object], transport: KalshiKillTransport) -> None:
        self._halt = halt
        self._transport = transport
        self._lock = threading.Lock()
        self._state = {"halt": "pending", "cancel_all": "pending", "revoke_key": "pending"}
        self._detail: dict[str, str] = {}

    def _cancel(self) -> None:
        try:
            out = self._transport.cancel_all()
        except Exception as exc:  # noqa: BLE001 - any failure is "unknown", never "done"
            self._detail["cancel_all"] = "error:" + type(exc).__name__
            return
        remaining = out.get("remaining") if type(out) is dict else None
        if type(remaining) is not int or remaining < 0:
            self._detail["cancel_all"] = "unreadable_response"
            return
        if remaining > 0:
            self._detail["cancel_all"] = "remaining:%d" % remaining
            return
        # remaining == 0 is the cancel reply, not confirmation. Re-list.
        try:
            listed = self._transport.list_open_orders()
        except Exception as exc:  # noqa: BLE001
            self._detail["cancel_all"] = "list_error:" + type(exc).__name__
            return
        ids = listed.get("order_ids") if type(listed) is dict else None
        if type(ids) is not list or not all(type(i) is str and i for i in ids):
            self._detail["cancel_all"] = "unreadable_list"
            return
        if ids:
            self._detail["cancel_all"] = "remaining:%d" % len(ids)
            return
        self._state["cancel_all"] = "confirmed"
        self._detail.pop("cancel_all", None)

    def _revoke(self) -> None:
        try:
            out = self._transport.revoke_api_key()
        except Exception as exc:  # noqa: BLE001
            self._detail["revoke_key"] = "error:" + type(exc).__name__
            out = None
        if type(out) is dict and out.get("accepted") is False:
            self._detail["revoke_key"] = "rejected"
        # Confirmation is only an authenticated call refused after the revoke.
        try:
            probe = self._transport.probe_authenticated()
        except Exception as exc:  # noqa: BLE001
            self._detail.setdefault("revoke_key", "probe_error:" + type(exc).__name__)
            return
        if probe == "unauthorized":
            self._state["revoke_key"] = "confirmed"
            self._detail.pop("revoke_key", None)
        else:
            self._detail.setdefault("revoke_key", "probe:" + str(probe)[:32])

    def kill(self) -> dict:
        with self._lock:
            try:
                self._halt()
                self._state["halt"] = "confirmed"
            except Exception as exc:  # noqa: BLE001 - still try the risk-reducing calls
                self._detail["halt"] = "error:" + type(exc).__name__
            if self._state["cancel_all"] != "confirmed":
                self._cancel()
            if self._state["revoke_key"] != "confirmed":
                self._revoke()
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "kalshi", "status": status, **self._state, "detail": dict(self._detail)}
