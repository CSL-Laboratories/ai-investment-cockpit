"""Coinbase venue kill (B12/B28, F44, CSL-743), paper and mock only.

No Coinbase order code exists in ``engine/``. This module has no network, credential or
keyring code either: the kill takes a transport capability that only the
credential-owning host may supply, and this slice tests it with mocks only.

PRODUCT.md 4.2 (fail mode and kill switch): Coinbase's trading API has no cancel-all,
and the venue credential is an OAuth grant. So, after the order halt is set in one
committed step:

* the open orders are listed (every page) and each one is cancelled by its own call. An
  order counts as cancelled only when the venue's reply for that order says it succeeded.
  A failed or unreadable cancel never skips the others;
* the open orders are then listed again. Cancel is ``confirmed`` only when every order
  listed was confirmed cancelled and the venue lists none open. An order placed between
  the two listings, a list error, or a page cap reached keeps it ``pending``. An order
  the venue still lists after a success reply is dropped from ``cancelled`` and is
  cancelled again on the next ``kill``;
* the OAuth grant is revoked. The transport returns a typed ``RevokeReply`` carrying the
  venue's HTTP status. A 200 is not proof that access is gone (RFC 7009 servers answer
  200 even for an invalid or already-revoked token), so two probes always run after the
  revoke call, each on its own: an authenticated API call, then a refresh-token exchange
  with the grant's refresh token. The revoke is ``confirmed`` only when the reply
  answered 200 and both probes are refused as unauthorized. Otherwise it is ``pending``:
  the 200 alone, one unauthorized probe, or two unauthorized probes after a rejected,
  unreadable or raised revoke confirm nothing. The ``revoke_grant`` detail holds only
  the revoke attempt (``rejected`` for any other status, ``unreadable_response`` for a
  reply that is not a ``RevokeReply``, or ``error:<Exc>``; absent after a 200). The last
  probes are reported apart, under ``revoke_probe_api`` and ``revoke_probe_refresh``,
  each one of ``unauthorized``, ``authorized``, ``unreadable_probe`` (any other reply;
  no part of it is stored) or ``error:<Exc>``. Both stay in the detail after a confirm.

The overall status is ``confirmed`` only when halt, cancel and revoke all are. Calling
``kill`` again keeps the halt and retries what is not yet confirmed. A cancel confirm is
kept only once the revoke is confirmed too: while the revoke is pending, each ``kill``
lists and cancels again before the revoke. A cancel that confirms in a ``kill`` whose
halt call failed is not kept either.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

from .revoke import RevokeReply, classify_probe

__all__ = ["CoinbaseKill", "CoinbaseKillTransport", "MAX_LIST_PAGES"]

MAX_LIST_PAGES = 100


class CoinbaseKillTransport(Protocol):
    """Supplied only by the credential-owning host. Each method is one venue API operation."""

    def list_open_orders(self, cursor: str | None) -> dict: ...  # {"order_ids": [str], "cursor": str | None}
    def cancel_order(self, order_id: str) -> dict: ...           # {"order_id": str, "success": bool}
    def revoke_oauth_grant(self) -> RevokeReply: ...             # RevokeReply(http_status); 200 is not proof
    def probe_api_call(self) -> str: ...                         # "unauthorized" | "authorized"
    def probe_refresh_token_exchange(self) -> str: ...           # "unauthorized" | "authorized"


class _Unreadable(Exception):
    pass


class CoinbaseKill:
    def __init__(self, halt: Callable[[], object], transport: CoinbaseKillTransport) -> None:
        self._halt = halt
        self._transport = transport
        self._lock = threading.Lock()
        self._state = {"halt": "pending", "cancel_orders": "pending", "revoke_grant": "pending"}
        self._detail: dict[str, str] = {}
        self._cancelled: set[str] = set()

    def _list(self) -> list[str]:
        ids: list[str] = []
        cursor = None
        for _ in range(MAX_LIST_PAGES):
            out = self._transport.list_open_orders(cursor)
            page = out.get("order_ids") if type(out) is dict else None
            if type(page) is not list or not all(type(i) is str and i for i in page):
                raise _Unreadable("unreadable_list")
            ids.extend(page)
            cursor = out.get("cursor")
            if cursor is None:
                return ids
            if type(cursor) is not str or not cursor:
                raise _Unreadable("unreadable_list")
        raise _Unreadable("list_page_cap")

    def _cancel(self) -> None:
        try:
            listed = self._list()
        except _Unreadable as exc:
            self._detail["cancel_orders"] = str(exc)
            return
        except Exception as exc:  # noqa: BLE001 - any failure is "unknown", never "done"
            self._detail["cancel_orders"] = "list_error:" + type(exc).__name__
            return
        failed = 0
        for order_id in listed:
            # The venue listing it open outranks any earlier success reply: cancel it again.
            self._cancelled.discard(order_id)
            try:
                out = self._transport.cancel_order(order_id)
            except Exception:  # noqa: BLE001 - one failure never skips the other orders
                failed += 1
                continue
            if type(out) is dict and out.get("order_id") == order_id and out.get("success") is True:
                self._cancelled.add(order_id)
            else:
                failed += 1
        if failed:
            self._detail["cancel_orders"] = "unconfirmed:%d" % failed
            return
        # Confirmation is the venue listing no open order after the cancels.
        try:
            remaining = self._list()
        except _Unreadable as exc:
            self._detail["cancel_orders"] = "relist:" + str(exc)
            return
        except Exception as exc:  # noqa: BLE001
            self._detail["cancel_orders"] = "relist_error:" + type(exc).__name__
            return
        if remaining:
            # A success reply for an order the venue still lists is not a cancel.
            self._cancelled.difference_update(remaining)
            self._detail["cancel_orders"] = "remaining:%d" % len(remaining)
            return
        self._state["cancel_orders"] = "confirmed"
        self._detail.pop("cancel_orders", None)

    def _revoke(self) -> None:
        answered_200 = False
        try:
            out = self._transport.revoke_oauth_grant()
        except Exception as exc:  # noqa: BLE001
            self._detail["revoke_grant"] = "error:" + type(exc).__name__
        else:
            if type(out) is not RevokeReply:
                self._detail["revoke_grant"] = "unreadable_response"
            elif out.answered_200:
                answered_200 = True
                self._detail.pop("revoke_grant", None)  # an earlier attempt's detail is not this one's
            else:
                self._detail["revoke_grant"] = "rejected"
        # The 200 is not proof (RFC 7009 answers 200 for a dead token too). Confirmation needs
        # all three: this revoke answered 200, and after it both an authenticated API call and
        # a refresh-token exchange are refused. Both probes always run, each on its own, and
        # each last result is reported under its own key, apart from the revoke attempt's.
        api = classify_probe(lambda: self._transport.probe_api_call())
        refresh = classify_probe(lambda: self._transport.probe_refresh_token_exchange())
        self._detail["revoke_probe_api"] = api
        self._detail["revoke_probe_refresh"] = refresh
        if answered_200 and api == "unauthorized" and refresh == "unauthorized":
            self._state["revoke_grant"] = "confirmed"

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
            if self._state["cancel_orders"] != "confirmed" or self._state["revoke_grant"] != "confirmed":
                self._state["cancel_orders"] = "pending"
                self._cancel()
                if halt_failed and self._state["cancel_orders"] == "confirmed":
                    # The halt call failed in this kill: do not keep the confirm.
                    self._state["cancel_orders"] = "pending"
                    self._detail["cancel_orders"] = "halt_failed"
            if self._state["revoke_grant"] != "confirmed":
                self._revoke()
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "coinbase", "status": status, **self._state,
                    "cancelled": sorted(self._cancelled), "detail": dict(self._detail)}
