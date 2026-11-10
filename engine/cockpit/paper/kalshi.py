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
  remaining count, and any order the listing still shows is ``pending``. An
  empty listing that still carries a ``cursor``, a ``truncated`` other than
  ``False`` or a ``remaining`` other than ``0`` is unfinished, so ``pending``.
  Each reply has to be exactly the contracted shape. The cancel reply is only
  ``remaining`` as an int. The listing is only ``order_ids`` plus an optional
  ``cursor``, ``truncated`` and ``remaining``.   Anything else stays ``pending``:
  a fill fragment or an order record missing fill fields, an error body
  (including one that also says HTTP 200), an order id, a client id, a status,
  or a late body after a timeout.
  A body carried on the timeout itself is not read;
* the key revoke is ``confirmed`` only when the revoke call itself was accepted
  (the reply is exactly ``{"accepted": True}``) and an authenticated call made after
  it is refused as unauthorized. Otherwise it is ``pending``. The ``revoke_key``
  detail holds only the revoke attempt (``rejected``, ``error:<Exc>`` or
  ``unreadable_response``; absent after an accepted revoke). The last probe is
  reported apart, under ``revoke_probe``: ``unauthorized``, ``authorized``,
  ``unreadable_probe`` (any other reply, including a non-string whose equality
  method claims otherwise; no part of it is stored) or ``error:<Exc>``. It stays
  in the detail after a confirm, and an unauthorized probe after a rejected or
  raised revoke confirms nothing.

The overall status is ``confirmed`` only when both are. Calling ``kill`` again keeps
the halt and retries what is not yet confirmed. A cancel confirm is kept only once the
revoke is confirmed too: while the revoke is pending, each ``kill`` cancels and lists
again before the revoke. A cancel that confirms in a ``kill`` whose halt call failed is
not kept either.
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


_CANCEL_KEYS = frozenset({"remaining"})
_LIST_KEYS = frozenset({"order_ids", "cursor", "truncated", "remaining"})
_REVOKE_KEYS = frozenset({"accepted"})


def _list_finished(listed: dict) -> bool:
    """True only when the list payload does not say more orders may follow."""
    if listed.get("cursor") is not None:
        return False
    if listed.get("truncated", False) is not False:
        return False
    remaining = listed.get("remaining", 0)
    return type(remaining) is int and remaining == 0


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
        # Only ``{"remaining": <int>}``. An error body, a 200, an order id, a client
        # id, a status or a late-after-timeout flag is not an acceptance.
        if type(out) is not dict or set(out) != _CANCEL_KEYS:
            self._detail["cancel_all"] = "unreadable_response"
            return
        remaining = out["remaining"]
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
        # Fill fragments, an error body, a status, an order id or a client id are
        # not a finished empty listing, even when ``order_ids`` is empty.
        if type(listed) is not dict or "order_ids" not in listed or not set(listed) <= _LIST_KEYS:
            self._detail["cancel_all"] = "unreadable_list"
            return
        ids = listed["order_ids"]
        if type(ids) is not list or not all(type(i) is str and i for i in ids):
            self._detail["cancel_all"] = "unreadable_list"
            return
        if ids:
            self._detail["cancel_all"] = "remaining:%d" % len(ids)
            return
        if not _list_finished(listed):
            self._detail["cancel_all"] = "unfinished_list"
            return
        self._state["cancel_all"] = "confirmed"
        self._detail.pop("cancel_all", None)

    def _revoke(self) -> None:
        accepted = False
        try:
            out = self._transport.revoke_api_key()
        except Exception as exc:  # noqa: BLE001
            self._detail["revoke_key"] = "error:" + type(exc).__name__
        else:
            # Only ``{"accepted": true|false}``. A 200, an error body or a client id
            # beside ``accepted`` is not an acceptance.
            if type(out) is dict and set(out) == _REVOKE_KEYS and out["accepted"] is True:
                accepted = True
                self._detail.pop("revoke_key", None)  # an earlier attempt's detail is not this one's
            elif type(out) is dict and set(out) == _REVOKE_KEYS and out["accepted"] is False:
                self._detail["revoke_key"] = "rejected"
            else:
                self._detail["revoke_key"] = "unreadable_response"
        # Confirmation needs both: this revoke accepted, and an authenticated call refused
        # after it. An unauthorized probe after a rejected or raised revoke confirms nothing.
        # The last probe result is reported under its own key, apart from the revoke attempt's.
        try:
            probe = self._transport.probe_authenticated()
        except Exception as exc:  # noqa: BLE001
            self._detail["revoke_probe"] = "error:" + type(exc).__name__
            return
        if type(probe) is not str:
            self._detail["revoke_probe"] = "unreadable_probe"
            return
        # Only a declared reply is stored; no part of any other string is.
        self._detail["revoke_probe"] = probe if probe in ("unauthorized", "authorized") else "unreadable_probe"
        if accepted and probe == "unauthorized":
            self._state["revoke_key"] = "confirmed"

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
            if self._state["cancel_all"] != "confirmed" or self._state["revoke_key"] != "confirmed":
                self._state["cancel_all"] = "pending"
                self._cancel()
                if halt_failed and self._state["cancel_all"] == "confirmed":
                    # The halt call failed in this kill: do not keep the confirm.
                    self._state["cancel_all"] = "pending"
                    self._detail["cancel_all"] = "halt_failed"
            if self._state["revoke_key"] != "confirmed":
                self._revoke()
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "kalshi", "status": status, **self._state, "detail": dict(self._detail)}
