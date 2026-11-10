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
* the OAuth grant is revoked by the RFC 7009 token revocation
  ``POST https://exchange.gemini.com/auth/token/revoke`` (``REVOKE_ENDPOINT``; the legacy
  revoke-by-token call is not used). The transport returns a typed ``RevokeReply``
  carrying the venue's HTTP status. A 200 is not proof that access is gone (RFC 7009
  servers answer 200 even for an invalid or already-revoked token), so two probes always
  run after the revoke call, each on its own: an authenticated API call, then a
  refresh-token exchange with the grant's refresh token. The revoke is ``confirmed``
  only when the reply answered 200 and both probes are refused as unauthorized.
  Otherwise it is ``pending``: the 200 alone, one unauthorized probe, or two
  unauthorized probes after a rejected, unreadable or raised revoke confirm nothing.
  The ``revoke_grant`` detail holds only the revoke attempt (``rejected`` for any other
  status, ``unreadable_response`` for a reply that is not a ``RevokeReply``, or
  ``error:<Exc>``; absent after a 200). The last probes are reported apart, under
  ``revoke_probe_api`` and ``revoke_probe_refresh``, each one of ``unauthorized``,
  ``authorized``, ``unreadable_probe`` (any other reply; no part of it is stored) or
  ``error:<Exc>``. Both stay in the detail after a confirm.

Which Gemini API operations back ``cancel_all`` and ``list_open_orders`` under an OAuth
grant is not verified here; the transport is a mock (see the PR's gaps).

The halt leg is ``confirmed`` only on verified evidence: the halt callable returned and
the ``halted`` read-back given to the constructor (``PaperBook.halted``, say) then
returned exactly ``True``. The halt callable returning is not evidence. Without that the
leg is not newly confirmed and the ``halt`` detail says why: ``unverified`` (no
read-back was given), ``not_halted`` (the read returned anything but ``True``),
``read_error:<Exc>`` (the read raised) or ``error:<Exc>`` (the halt call raised).

Each leg (``halt``, ``cancel_all``, ``revoke_grant``) is one of ``confirmed``,
``pending`` or, for the cancel leg only, ``pending_credential_revoked``: the grant's
revoke was already confirmed before this ``kill`` tried the cancel and the cancel is
still not confirmed, so the retry ran on a revoked credential. It is still pending, not
done: every later ``kill`` tries the cancel again and the detail keeps that attempt's
error. In the ``kill`` where the revoke first confirms, an unconfirmed cancel is plain
``pending``.

The overall status is ``confirmed`` only when halt, cancel and revoke all are exactly
``confirmed``; otherwise it is ``pending``. Calling ``kill`` again calls the halt again
and retries what is not yet confirmed. A cancel confirm is kept only once the revoke is
confirmed too: while the revoke is pending, each ``kill`` cancels and lists again before
the revoke. A cancel that confirms in a ``kill`` whose halt call raised is not kept
either.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

from .revoke import RevokeReply, classify_probe

__all__ = ["GeminiKill", "GeminiKillTransport", "REVOKE_ENDPOINT"]

# Documented only: the host's transport makes the call. No network code here.
REVOKE_ENDPOINT = "https://exchange.gemini.com/auth/token/revoke"


class GeminiKillTransport(Protocol):
    """Supplied only by the credential-owning host. Each method is one venue API operation.

    ``revoke_oauth_grant`` is the RFC 7009 token revocation ``POST`` to ``REVOKE_ENDPOINT``
    (``https://exchange.gemini.com/auth/token/revoke``), not the legacy revoke-by-token
    call. Its reply carries the HTTP status; a 200 is not proof that access is gone.
    """

    def cancel_all(self) -> dict: ...                   # {"cancelled": [str]}
    def list_open_orders(self) -> dict: ...             # {"order_ids": [str]}
    def revoke_oauth_grant(self) -> RevokeReply: ...    # RevokeReply(http_status)
    def probe_api_call(self) -> str: ...                # "unauthorized" | "authorized"
    def probe_refresh_token_exchange(self) -> str: ...  # "unauthorized" | "authorized"


def _list_finished(listed: dict) -> bool:
    """True only when the list payload does not say more orders may follow."""
    if listed.get("cursor") is not None:
        return False
    if listed.get("truncated", False) is not False:
        return False
    remaining = listed.get("remaining", 0)
    return type(remaining) is int and remaining == 0


class GeminiKill:
    def __init__(self, halt: Callable[[], object], transport: GeminiKillTransport, *,
                 halted: Callable[[], object] | None = None) -> None:
        self._halt = halt
        self._halted = halted
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
            halt_failed = False
            try:
                self._halt()
            except Exception as exc:  # noqa: BLE001 - still try the risk-reducing calls
                halt_failed = True
                self._detail["halt"] = "error:" + type(exc).__name__
            else:
                self._confirm_halt()
            revoked_before = self._state["revoke_grant"] == "confirmed"
            # An earlier cancel confirm is kept only once the revoke is confirmed too.
            if self._state["cancel_all"] != "confirmed" or not revoked_before:
                self._state["cancel_all"] = "pending"
                self._cancel()
                if halt_failed and self._state["cancel_all"] == "confirmed":
                    # The halt call failed in this kill: do not keep the confirm.
                    self._state["cancel_all"] = "pending"
                    self._detail["cancel_all"] = "halt_failed"
                if revoked_before and self._state["cancel_all"] != "confirmed":
                    # Still pending and retried on every kill, but on a credential already revoked.
                    self._state["cancel_all"] = "pending_credential_revoked"
            if self._state["revoke_grant"] != "confirmed":
                self._revoke()
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "gemini", "status": status, **self._state, "detail": dict(self._detail)}
