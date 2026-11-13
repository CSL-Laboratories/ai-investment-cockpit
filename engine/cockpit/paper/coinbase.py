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
  the two listings, a list error, or a page cap reached keeps it ``pending``. A first
  listing that cannot be finished (a list error, unreadable page, the page cap, or an
  indicator that disagrees with the cursor) also keeps it ``pending``, but cancellation
  is still attempted once for each readable order id it showed before the revoke.
  An order the venue still lists after a success reply is dropped from ``cancelled``
  and is cancelled again on the next ``kill``;
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

The halt leg is ``confirmed`` only on verified evidence: the halt callable returned and
the ``halted`` read-back given to the constructor (``PaperBook.halted``, say) then
returned exactly ``True``. The halt callable returning is not evidence. Without that the
leg is not confirmed in this ``kill`` and the ``halt`` detail says why: ``unverified`` (no
read-back was given), ``not_halted`` (the read returned anything but ``True``),
``read_error:<Exc>`` (the read raised) or ``error:<Exc>`` (the halt call raised).

Each leg (``halt``, ``cancel_orders``, ``revoke_grant``) is one of ``confirmed``,
``pending`` or, for the cancel leg only, ``pending_credential_revoked``: the grant's
revoke was already confirmed before this ``kill`` tried the cancel and the cancel is
still not confirmed, so the retry ran on a revoked credential. It is still pending, not
done: every later ``kill`` tries the cancel again and the detail holds that attempt's
error. In the ``kill`` where the revoke first confirms, an unconfirmed cancel is plain
``pending``.

The overall status is ``confirmed`` only when halt, cancel and revoke all are exactly
``confirmed``; otherwise it is ``pending``. Nothing confirms on an earlier ``kill``'s
calls: every ``kill`` starts with every leg ``pending`` and an empty detail, calls the
halt and reads it back again, tries the cancel again, and makes a fresh revoke call and
fresh probes (a venue answering a second revoke of a dead credential is expected). A halt
read that now says ``not_halted`` or fails therefore leaves this ``kill`` ``pending``,
whatever an earlier one reported. ``revoke_evidence`` in the result holds only what this
``kill``'s own revoke call and probes returned; ``killswitch.KillSwitch`` confirms a venue
on it.

One fact is remembered across calls, and it never confirms anything on its own. A
credential that is revoked can no longer list or cancel, so once a ``kill`` has confirmed
the cancel and then the revoke, a later ``kill`` whose cancel attempt raises on its first
venue call keeps the cancel leg ``confirmed`` only if that same ``kill``'s fresh revoke
and probes confirm the credential is still dead; the detail then reads
``kept:<this attempt's error>``. If the fresh revoke does not confirm, or the venue
answers the cancel attempt at all, the earlier confirm is dropped. A cancel that confirms
in a ``kill`` whose halt call raised does not count. Reuse on a later kill also
requires that the earlier cancel and revoke ran with the halt read-back confirmed.
After a venue is re-authorized, the host builds new venue-kill objects, and a new
``KillSwitch`` over them, before the next kill, whether or not trading has resumed.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

from .revoke import RevokeReply, classify_probe

__all__ = ["CoinbaseKill", "CoinbaseKillTransport", "MAX_LIST_PAGES"]

MAX_LIST_PAGES = 100


class CoinbaseKillTransport(Protocol):
    """Supplied only by the credential-owning host. Each method is one venue API operation."""

    # Host-normalized pages: {"order_ids": [str], "cursor": nonempty str | None}.
    # Optional has_next/truncated must be bools agreeing with cursor presence;
    # remaining must be an int >= 0, positive exactly when a cursor is present.
    # Both the initial list and confirmation re-list enforce these indicators.
    def list_open_orders(self, cursor: str | None) -> dict: ...
    def cancel_order(self, order_id: str) -> dict: ...           # {"order_id": str, "success": bool}
    def revoke_oauth_grant(self) -> RevokeReply: ...             # RevokeReply(http_status); 200 is not proof
    def probe_api_call(self) -> str: ...                         # "unauthorized" | "authorized"
    def probe_refresh_token_exchange(self) -> str: ...           # "unauthorized" | "authorized"


class _Unreadable(Exception):
    """The listing cannot be finished. ``ids`` holds every order id read before that."""

    def __init__(self, why: str, ids: list[str] | None = None) -> None:
        super().__init__(why)
        self.ids = list(ids or ())


class CoinbaseKill:
    def __init__(self, halt: Callable[[], object], transport: CoinbaseKillTransport, *,
                 halted: Callable[[], object] | None = None) -> None:
        self._halt = halt
        self._halted = halted
        self._transport = transport
        self._lock = threading.Lock()
        self._state = {"halt": "pending", "cancel_orders": "pending", "revoke_grant": "pending"}
        self._detail: dict[str, str] = {}
        self._evidence: dict[str, object] = {}
        # Labels and the kept-cancel rule only (see the module doc); neither confirms a leg alone.
        self._revoked_before = False
        self._cancel_then_revoke_confirmed = False
        self._cancelled: set[str] = set()

    def _list(self) -> list[str]:
        ids: list[str] = []
        cursor = None
        for _ in range(MAX_LIST_PAGES):
            try:
                out = self._transport.list_open_orders(cursor)
            except Exception as exc:  # noqa: BLE001 - preserve earlier pages for cancellation
                if cursor is None:
                    raise  # Only failure on the first call may reuse earlier kill evidence.
                raise _Unreadable("partial_list_error:" + type(exc).__name__, ids) from exc
            page = out.get("order_ids") if type(out) is dict else None
            if type(page) is not list:
                raise _Unreadable("unreadable_list", ids)
            readable = [i for i in page if type(i) is str and i]
            ids.extend(readable)
            if len(readable) != len(page):
                raise _Unreadable("unreadable_list", ids)
            cursor = out.get("cursor")
            # Optional completion indicators must agree with the cursor. Never
            # turn an incomplete or contradictory empty page into cancel evidence.
            for flag in ("has_next", "truncated"):
                if flag in out and (type(out[flag]) is not bool or out[flag] != (cursor is not None)):
                    raise _Unreadable("unfinished_list", ids)
            if "remaining" in out:
                remaining = out["remaining"]
                if (type(remaining) is not int or remaining < 0
                        or (remaining > 0) != (cursor is not None)):
                    raise _Unreadable("unfinished_list", ids)
            if cursor is None:
                return ids
            if type(cursor) is not str or not cursor:
                raise _Unreadable("unreadable_list", ids)
        raise _Unreadable("list_page_cap", ids)

    def _cancel_each(self, order_ids: list[str]) -> int:
        """Cancel each order by its own call. Returns how many did not confirm."""
        failed = 0
        for order_id in order_ids:
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
        return failed

    def _cancel(self) -> None:
        try:
            listed = self._list()
        except _Unreadable as exc:
            # The listing cannot be finished, so the leg stays pending and nothing is confirmed.
            # The orders it did list are still cancelled: after the revoke nothing here can.
            self._cancel_each(list(dict.fromkeys(exc.ids)))
            self._detail["cancel_orders"] = str(exc)
            return
        except Exception as exc:  # noqa: BLE001 - any failure is "unknown", never "done"
            self._detail["cancel_orders"] = "list_error:" + type(exc).__name__
            return
        failed = self._cancel_each(listed)
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
                self._evidence["http_status"] = 200
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
        self._evidence["probe_api"] = api
        self._evidence["probe_refresh"] = refresh
        if answered_200 and api == "unauthorized" and refresh == "unauthorized":
            self._state["revoke_grant"] = "confirmed"

    def _confirm_halt(self) -> None:
        # Confirm on the read-back, not on the halt callable returning.
        if self._halted is None:
            self._detail["halt"] = "unverified"
            return
        try:
            read = self._halted()
        except Exception as exc:  # noqa: BLE001 - an unknown read confirms nothing
            self._detail["halt"] = "read_error:" + type(exc).__name__
            return
        if read is True:
            self._state["halt"] = "confirmed"
            self._detail.pop("halt", None)
        else:
            self._detail["halt"] = "not_halted"

    def kill(self) -> dict:
        with self._lock:
            # Nothing confirms on an earlier kill's calls: every leg starts pending again.
            self._state = {"halt": "pending", "cancel_orders": "pending", "revoke_grant": "pending"}
            self._detail = {}
            self._evidence = {"http_status": None, "probe_api": None, "probe_refresh": None}
            halt_failed = False
            try:
                self._halt()
            except Exception as exc:  # noqa: BLE001 - still try the risk-reducing calls
                halt_failed = True
                self._detail["halt"] = "error:" + type(exc).__name__
            else:
                self._confirm_halt()
            self._cancel()
            if halt_failed and self._state["cancel_orders"] == "confirmed":
                # The halt call failed in this kill: the confirm does not count.
                self._state["cancel_orders"] = "pending"
                self._detail["cancel_orders"] = "halt_failed"
            cancelled_now = self._state["cancel_orders"] == "confirmed"
            self._revoke()
            revoked_now = self._state["revoke_grant"] == "confirmed"
            if cancelled_now:
                # Only evidence collected behind a verified halt can survive
                # credential revocation and be reused on a later kill.
                self._cancel_then_revoke_confirmed = revoked_now and self._state["halt"] == "confirmed"
            elif (self._cancel_then_revoke_confirmed and revoked_now
                  and self._detail.get("cancel_orders", "").startswith("list_error:")):
                # The dead credential cannot list or cancel. This kill's own revoke and probes
                # say it is still dead, so the earlier cancel-then-revoke confirm stands.
                self._state["cancel_orders"] = "confirmed"
                self._detail["cancel_orders"] = "kept:" + self._detail["cancel_orders"]
            else:
                self._cancel_then_revoke_confirmed = False
                if self._revoked_before:
                    # Still pending and retried on every kill, but on a credential already revoked.
                    self._state["cancel_orders"] = "pending_credential_revoked"
            if revoked_now:
                self._revoked_before = True
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "coinbase", "status": status, **self._state,
                    "cancelled": sorted(self._cancelled), "detail": dict(self._detail),
                    "revoke_evidence": dict(self._evidence)}
