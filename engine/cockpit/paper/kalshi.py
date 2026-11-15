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
  empty listing that still carries a ``cursor``, a ``truncated`` or ``has_next``
  other than ``False`` or a ``remaining`` other than ``0`` is unfinished, so ``pending``;
* the key revoke is ``confirmed`` only when the revoke call itself was accepted
  (``accepted`` exactly ``True``) and an authenticated call made after it is refused as
  unauthorized. Otherwise it is ``pending``. The ``revoke_key`` detail holds only the
  revoke attempt (``rejected``, ``error:<Exc>`` or ``unreadable_response``; absent after
  an accepted revoke). The last probe is reported apart, under ``revoke_probe``:
  ``unauthorized``, ``authorized``, ``unreadable_probe`` (any other reply; no part of it
  is stored) or ``error:<Exc>``. It stays in the detail after a confirm, and an
  unauthorized probe after a rejected or raised revoke confirms nothing.

The halt leg is ``confirmed`` only on verified evidence: the halt callable returned and
the ``halted`` read-back given to the constructor (``PaperBook.halted``, say) then
returned exactly ``True``. The halt callable returning is not evidence. Without that the
leg is not confirmed in this ``kill`` and the ``halt`` detail says why: ``unverified`` (no
read-back was given), ``not_halted`` (the read returned anything but ``True``),
``read_error:<Exc>`` (the read raised) or ``error:<Exc>`` (the halt call raised).

Each leg (``halt``, ``cancel_all``, ``revoke_key``) is one of ``confirmed``,
``pending`` or, for the cancel leg only, ``pending_credential_revoked``: the key's
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


def _list_finished(listed: dict) -> bool:
    """True only when the list payload does not say more orders may follow."""
    if listed.get("cursor") is not None:
        return False
    if listed.get("truncated", False) is not False or listed.get("has_next", False) is not False:
        return False
    remaining = listed.get("remaining", 0)
    return type(remaining) is int and remaining == 0


class KalshiKill:
    def __init__(self, halt: Callable[[], object], transport: KalshiKillTransport, *,
                 halted: Callable[[], object] | None = None) -> None:
        self._halt = halt
        self._halted = halted
        self._transport = transport
        self._lock = threading.Lock()
        self._state = {"halt": "pending", "cancel_all": "pending", "revoke_key": "pending"}
        self._detail: dict[str, str] = {}
        self._evidence: dict[str, object] = {}
        # Labels and the kept-cancel rule only (see the module doc); neither confirms a leg alone.
        self._revoked_before = False
        self._cancel_then_revoke_confirmed = False

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
            accepted = type(out) is dict and out.get("accepted") is True
            if accepted:
                self._evidence["accepted"] = True
            elif type(out) is dict and out.get("accepted") is False:
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
        self._evidence["probe"] = self._detail["revoke_probe"]
        if accepted and probe == "unauthorized":
            self._state["revoke_key"] = "confirmed"

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
            self._state = {"halt": "pending", "cancel_all": "pending", "revoke_key": "pending"}
            self._detail = {}
            self._evidence = {"accepted": False, "probe": None}
            halt_failed = False
            try:
                self._halt()
            except Exception as exc:  # noqa: BLE001 - still try the risk-reducing calls
                halt_failed = True
                self._detail["halt"] = "error:" + type(exc).__name__
            else:
                self._confirm_halt()
            self._cancel()
            if halt_failed and self._state["cancel_all"] == "confirmed":
                # The halt call failed in this kill: the confirm does not count.
                self._state["cancel_all"] = "pending"
                self._detail["cancel_all"] = "halt_failed"
            cancelled_now = self._state["cancel_all"] == "confirmed"
            self._revoke()
            revoked_now = self._state["revoke_key"] == "confirmed"
            if cancelled_now:
                # Reuse requires cancellation and revocation behind a verified halt.
                self._cancel_then_revoke_confirmed = revoked_now and self._state["halt"] == "confirmed"
            elif (self._cancel_then_revoke_confirmed and revoked_now
                  and self._detail.get("cancel_all", "").startswith("error:")):
                # The dead credential cannot list or cancel. This kill's own revoke and probes
                # say it is still dead, so the earlier cancel-then-revoke confirm stands.
                self._state["cancel_all"] = "confirmed"
                self._detail["cancel_all"] = "kept:" + self._detail["cancel_all"]
            else:
                self._cancel_then_revoke_confirmed = False
                if self._revoked_before:
                    # Still pending and retried on every kill, but on a credential already revoked.
                    self._state["cancel_all"] = "pending_credential_revoked"
            if revoked_now:
                self._revoked_before = True
            status = "confirmed" if all(v == "confirmed" for v in self._state.values()) else "pending"
            return {"venue": "kalshi", "status": status, **self._state, "detail": dict(self._detail),
                    "revoke_evidence": dict(self._evidence)}
