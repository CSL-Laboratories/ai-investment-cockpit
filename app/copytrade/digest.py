"""RW-17 §3 — the end-of-day statement for the zero-touch PAPER lane, derived from the CHAIN.

WHY THIS FILE EXISTS
--------------------
``copytrade/notify.py`` has always been able to RENDER a daily digest. Nothing ever PRODUCED
one — no scheduler job, no call site — which meant the lane's only end-of-day statement was
never sent. That is the single most consequential gap the RW-17 adversarial round found, because
the digest is the message that makes the rest of the design's central claim true:

    *"the lane did nothing today" has to be a positive statement.*

Without it, a lane that refused forty trades and a lane that saw no filings are the same silence,
and every other silent-failure mode in this package (an undelivered notice, a ticket abandoned by
a restart, a budget rail that has quietly locked) hides inside that silence.

AND IT IS DERIVED FROM THE SIGNED CHAIN, NOT FROM ``PaperAutoExecutor.counts``
------------------------------------------------------------------------------
The obvious implementation reads the executor's in-memory refusal histogram. That histogram is a
dict on one object: it is empty after every restart, it does not exist in a process that did not
run the trades, and it is exactly the source that would UNDER-REPORT on the days the digest
matters most — the days something restarted or crashed. So the rows come from HMAC-verified
:data:`~copytrade.autoexec.EVENT_AUTOEXEC_TICKET` entries, the same posture ``budget.py`` takes
toward spend and ``gateway._pending_ok`` takes toward exposure.

Entries that fail ``verify_entry`` are SKIPPED — a forged entry must not be able to write a row
into the operator's end-of-day statement. An unreadable ledger yields ``readable=False`` rather
than a tidy empty digest, and the renderer says so out loud.

NOTHING HERE DECIDES ANYTHING. It is a read-only reporter: no order path, no model, no mutation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from copytrade.autoexec import AUTOEXEC_LANE, EVENT_AUTOEXEC_TICKET, REASON_EXECUTED
from copytrade.notify import REFUSAL_GATE_BY_REASON, DigestRefusal, DigestTrade
from schemas.models.audit_entry import AuditEntry

log = logging.getLogger("cockpit.copytrade.digest")


def _naive_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)


def _dec(value):
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


@dataclass(frozen=True)
class DigestRows:
    """What the lane did on one UTC day, ready for ``CopyTradeNotifier.daily_digest``.

    ``readable=False`` means the ledger could not be read or verified. It is NOT the same as a
    quiet day and must never render as one — that conflation is the whole reason this module
    exists — so the caller passes an empty ``budget_remaining`` and the renderer prints
    "unavailable — budget rails could not be read".
    """

    day: date
    readable: bool = True
    executed: tuple[DigestTrade, ...] = ()
    refused: tuple[DigestRefusal, ...] = ()
    error: str | None = None


def digest_rows(st, *, day: date) -> DigestRows:
    """Every settled ticket for ``day``, split into traded and refused.

    ``day`` is matched against the SIGNED ``payload['_ts']`` that ``AuditLog._insert`` stamps in
    naive UTC (SEC-12), not against the mutable ``created_at`` column — so a keyless writer
    cannot move a refusal out of today's digest by editing a date.
    """
    try:
        verified = st.audit.verify_chain()
        chain_ok = bool(verified.ok)
        chain_error = None if chain_ok else f"audit chain does not verify: {verified.error}"
    except Exception as exc:                      # noqa: BLE001 - a probe fault is unreadable
        return DigestRows(day=day, readable=False,
                          error=f"audit chain verification raised {type(exc).__name__}")
    if not chain_ok:
        return DigestRows(day=day, readable=False, error=chain_error)

    try:
        with st.session_factory() as s:
            rows = s.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type == EVENT_AUTOEXEC_TICKET)
                .order_by(AuditEntry.seq)
            ).scalars().all()
    except Exception as exc:                      # noqa: BLE001 - unreadable ledger fails loud
        return DigestRows(day=day, readable=False,
                          error=f"audit entries unreadable ({type(exc).__name__})")

    executed: list[DigestTrade] = []
    refused: list[DigestRefusal] = []
    for row in rows:
        try:
            if not st.audit.verify_entry(row):
                continue
        except Exception:                         # noqa: BLE001 - an unverifiable row is skipped
            continue
        payload = row.payload or {}
        if payload.get("lane") != AUTOEXEC_LANE or payload.get("state") != "settled":
            continue
        try:
            ts = _naive_utc(datetime.fromisoformat(str(payload.get("_ts"))))
        except (TypeError, ValueError):
            continue                              # unplaceable in time -> not this day's
        if ts.date() != day:
            continue

        symbol = str(payload.get("symbol") or "")
        side = str(payload.get("side") or "")
        if payload.get("executed") is True:
            executed.append(DigestTrade(
                symbol=symbol, side=side, notional=_dec(payload.get("notional")),
                status=str(payload.get("broker_status") or REASON_EXECUTED),
                broker_order_id=str(payload.get("broker_order_id") or ""),
            ))
            continue
        reason = str(payload.get("reason") or "")
        refused.append(DigestRefusal(
            symbol=symbol,
            # Translated through the SAME table the live refusal notification uses, so the gate
            # an operator reads at 10:41 and the gate they read in the digest at 16:20 are the
            # same string. An unmapped reason is rendered verbatim and flagged, never hidden.
            gate=REFUSAL_GATE_BY_REASON.get(reason, reason),
            detail=str(payload.get("detail") or ""),
        ))
    return DigestRows(day=day, readable=True, executed=tuple(executed),
                      refused=tuple(refused))
