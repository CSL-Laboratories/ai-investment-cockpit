"""RW-16 — congressional copy-trading: disclosures -> TradeProposal -> risk gate -> Order row.

The deterministic twin of ``workflows/proposals.py``. Same path, same gates, same audit
records, ONE difference that matters: **there is no model anywhere in it.** The proposal
text is composed from the filing's own facts, the size comes from ``copytrade/sizing.py``,
and the verdict comes from ``risk.engine.evaluate`` — nothing on this path can be talked
into anything.

WHERE THIS STOPS (Invariant 1)
------------------------------
``run_copy_cycle`` ends at an ``Order`` row in state ``proposed``. It does not call
``OrderGateway``, does not mint a capability token, does not approve, does not post. The
existing approval / veto path carries it from there, and the gateway re-runs every
pre-trade check at submit time. A reader auditing this file for Invariant 1 should find
exactly one order-adjacent call — ``brokers.order_state_machine.create_order`` — and it is
reachable only through ``risk decision == "approved"``.

================================================================================
SEC-08 / INVARIANT 2 — THIS FEATURE IS PAPER-COMPLETE AND LIVE-BLOCKED. READ THIS.
================================================================================
``producing_runtime`` for this path is ``"deterministic:copytrade"``.
``orchestrator.mode_ladder.assert_execution_runtime`` REFUSES any runtime that is not
literally ``"claude"`` once the ladder is at ``review_live``, ``auto_veto`` or
``auto_full``. That gate is not weakened here and is not routed around: ``run_copy_cycle``
calls it FIRST and, on refusal, produces nothing at all — every disclosure comes back as a
counted ``Skip`` with reason ``sec08_runtime_refused``.

The consequence, stated plainly so nobody discovers it in production:

  * in ``paper`` (and ``read_only``, where the risk engine rejects on ``mode_allows_order``)
    the feature works end to end;
  * in ANY live/auto rung it emits nothing. Congressional copy-trading is unavailable with
    real capital.

That is not a bug to be patched at this layer. SEC-08 exists because Invariant 2 pins the
DECISION path to Claude in live/auto modes, and this path's decisions are made by
deterministic code whose provenance string is, correctly, not "claude". Making it live
requires an explicit operator decision about whether a model-free, human-gated deterministic
producer is admissible under SEC-08 — **a decision that has not been made.** Until it is,
the honest posture is a feature that visibly refuses rather than one that quietly widens
what "claude" means. Do not add a special case for this string.

LINEAGE — WHY A MODEL-FREE PROPOSAL STILL GETS AN ``AgentJob``
--------------------------------------------------------------
``brokers.paper_execution`` (the only wired human gate) refuses to adjudicate an order whose
proposal has no producing job: ``incomplete_lineage``. That guard is right — an order with no
provenance must never reach a human — and the first build of this module simply set
``job_id=None``, which meant every copy order was un-adjudicable and the feature could not
complete a trade even in paper.

The fix is a REAL lineage, not a hole in the guard. A deterministic run IS a job: it has a
producer (``COPYTRADE_RUNTIME``), inputs it saw (the disclosure's raw-store locators), an
output contract (``TradeProposal``), a start and an end, and — because nothing here is
sampled — a reproducibility hash over the exact bytes it emitted. ``_lineage_job`` records
exactly that, through the same audited ``EVENT_AGENT_JOB`` write ``agent_calls`` uses, with
``model="none"`` so no reader can ever mistake this for a model run. The guard in
``paper_execution`` is unchanged and still refuses a proposal with genuinely absent lineage.

Other closures carried over from ``proposals.py``:
  * SEC-15 — ``RiskContext`` from the AUDITED limits + the latest ``PortfolioSnapshot``;
    absent snapshot -> zeros -> the risk gate fails conservatively.
  * SEC-15 (allowlist) — the watchlist handed to the risk engine is the operator's static
    list UNION ``copytrade.universe.current_universe`` (see that module: an audited,
    bounded, TTL-expiring promotion lane, not a widened operator watchlist).
  * SEC-09 — the one UNTRUSTED string on this path (the filer's display name, off a filing)
    goes through the deterministic scrubber before it is composed into anything.
  * Invariant 3 — every proposal is validated against ``TradeProposal.schema.json``; an
    invalid one is quarantined and never persisted or posted.
  * Invariant 5 — risk verdicts come ONLY from ``risk.engine.evaluate``.
  * Invariant 7 — a watch-only vendor window disables the whole cycle; HALT blocks the
    ``Order`` row and demotes to human review.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Mapping

import jsonschema
from sqlalchemy import select

from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from copytrade.engine import PlanContext, plan
from copytrade.models import CopyIntent, Skip
from copytrade.sizing import portfolio_value
from copytrade.universe import current_universe, promote
from core.ids import new_id
from ingest.normalize import escape_untrusted
from orchestrator.breakers import BreakerEngine
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.mode_ladder import (
    ExecutionRuntimeError,
    ModeLadder,
    assert_execution_runtime,
)
from risk.engine import evaluate, order_notional
from schemas.models.agent_job import AgentJob
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision
from workflows.constants import EVENT_AGENT_JOB, EVENT_PROPOSAL
from workflows.proposals import build_risk_context
from workflows.schema_loader import load_schema
from workflows.scrubber import scrub_output_fields

log = logging.getLogger(__name__)

COPYTRADE_RUNTIME = "deterministic:copytrade"
"""THE provenance string of this path. A MODULE CONSTANT, not a caller-supplied value.

It was a defaulted keyword argument, which made the SEC-08 boundary a PARAMETER: any caller
could pass ``producing_runtime="claude"`` and thereby (a) satisfy
``assert_execution_runtime`` in ``review_live``/``auto_veto``/``auto_full``, unlocking the
live rungs for a path that is deliberately paper-only, and (b) write "claude" into the
persisted proposal's ``generated_by``, so the stored provenance of a model-free order would
name a model runtime that never ran. A security boundary whose value is chosen by the code on
the far side of it is not a boundary.

``run_copy_cycle`` still ACCEPTS the keyword (the pinned build contract lists it) but only
ever emits this constant, and REFUSES the whole cycle — counted, named, nothing built — if a
caller passes anything else. Refusing rather than silently correcting is the point: a caller
that thinks it is choosing a runtime has a bug, and a bug about provenance must be loud.
"""

# ---- the pinned, deterministic shape of a copy proposal -------------------------------
_ASSET_TYPE = "equity"
"""ALWAYS equity. Options were already translated to an underlying position (or dropped) by
``copytrade/options.py``; ``risk.engine.SUPPORTED_ASSET_TYPES`` is ``{"equity"}`` and this
path must never be the reason that changes."""

_ORDER_TYPE = "limit"
"""A limit order priced at the gate quote. Not ``market``: the risk engine demotes a market
order to human review (``priced_not_market``) because an unpriced fill has no firm gate
price — and a signal carrying a 30-45 day statutory disclosure lag has no urgency that could
justify paying for immediacy. Deterministic input, deterministic order type."""

_TIME_HORIZON = "long"
_TIF = "day"
_CENTS = Decimal("0.01")
_TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_ID_CHUNK = 400          # bound the SQL IN() list when checking prior proposals

PROPOSAL_ID_PREFIX = "cp"


@dataclass(frozen=True)
class CopyCycleResult:
    """One cycle's counted outcome. ``proposed == approved + needs_human + rejected``.

    The three verdict counts are the FINAL disposition, not the raw risk verdict: an
    approved decision whose ``Order`` row could not be created (HALT sentinel set, or an
    unresolvable quantity) is counted as ``needs_human`` and carries its reason, so
    ``approved`` is always exactly ``len(orders)``. Anything that never reached the risk
    gate at all — engine skips, duplicates, quarantines — is a named ``Skip``.

    ``order_intents`` maps ``Order.id`` -> the :class:`~copytrade.models.CopyIntent` that
    produced it. RW-17 needs it and nothing else can supply it: the operator's queued
    notification has to name the filer, the trade date, the disclosure LAG and the disclosed
    range (``copytrade/notify.py``), and those live on the intent as discrete fields — the
    persisted proposal only carries them woven into a composed thesis sentence, which is not
    something a message may be re-parsed out of. A MAP rather than a tuple parallel to
    ``orders`` on purpose: parallel sequences are a mis-zip away from attaching one filing's
    evidence to another filing's order, which is exactly the lie the operator could not detect.
    """

    proposed: int
    approved: int
    needs_human: int
    rejected: int
    skipped: tuple[Skip, ...] = ()
    orders: tuple[Order, ...] = ()
    reasons: tuple[str, ...] = ()
    order_intents: Mapping[str, CopyIntent] = field(default_factory=dict)


def proposal_id_for(source_key: str, attempt: int = 0) -> str:
    """The DETERMINISTIC proposal id for one disclosure line. ``cp_<sha256[:32]>``.

    Idempotency lives here. Re-running a cycle over the same disclosures must not mint a
    second proposal, a second risk decision and a second order for a trade a human may
    already be looking at, so the id is a pure function of ``Disclosure.key``
    (``chamber:doc_id:line_no``) and an existing ``ProposalRecord`` under that id is treated
    as "already handled" rather than as a collision.

    Note the deliberate INVERSION of ``proposals._resolve_proposal_id``, which REMINTS on
    collision: there, a repeated id means a hostile model is replaying someone else's
    proposal, and reminting is the defence. Here the id is derived from a key this code
    computed, a collision means "same filing", and reminting would defeat the whole point.
    Hashing also guarantees the schema's ``^[A-Za-z0-9_:.\\-]{1,64}$`` pattern regardless of
    what characters the clerk put in the document id.

    ``attempt`` — A REJECTED PROPOSAL IS NOT A COMPLETED COPY (see ``_dispositions``). A risk
    rejection is very often ENVIRONMENTAL and transient: no quote wired yet, a stale quote, an
    absent ``PortfolioSnapshot``, buying power that recovers on the next snapshot. The first
    build burnt the idempotency key on ANY persisted record, so one bad minute meant that
    filing was never copied again — the same failure ``ingest``'s ``Dedup.discard`` exists to
    prevent ("a failed emit is not a drop"), re-introduced one layer up.

    So a retry gets its own id, derived from the same key plus the attempt ordinal. Attempt 0
    is BYTE-IDENTICAL to the original scheme (existing ids and their tests are unaffected);
    later attempts hash ``"<key>#<n>"``. Each attempt therefore keeps its own
    ``ProposalRecord`` + ``RiskDecision``, which is better evidence than an overwritten row —
    the operator can read why each attempt failed — and ``_persist_record``'s insert-only
    collision tripwire stays exactly as strict as it was.
    """
    raw = str(source_key) if attempt <= 0 else f"{source_key}#{int(attempt)}"
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{PROPOSAL_ID_PREFIX}_{digest[:32]}"


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


MAX_COPY_ATTEMPTS = 3
"""How many times one disclosure may be re-proposed after a REJECTED risk decision.

Bounded for the obvious reason, but note the gate that really bounds it: ``engine.plan``'s
staleness check drops the disclosure once it is older than ``policy.max_age_days``, so a
filing that keeps failing ages out on its own. This cap is the belt — it stops a permanently
un-tradeable name (delisted, no quote will ever come) from re-proposing every cycle for six
weeks — and the staleness gate is the braces.
"""

# A record whose disclosure is FINISHED WITH — and the ONLY thing that finishes one off is an
# ``Order`` row (or a record already posted). See ``_consuming``.
_CONSUMING_STATUSES = frozenset({"posted"})


def _consuming(status: str | None, order_id: str | None) -> bool:
    """Did this record actually COPY the trade? An order exists, or one already posted.

    ``needs_human`` used to be enough on its own, and it entombed filings. There are two ways to
    reach ``needs_human`` WITH NO ORDER — the SEC-04 HALT sentinel was set when an approved
    decision tried to create the row, and ``_resolve_order`` could not resolve one (no quote, or
    a name priced above ``max_notional_per_trade`` so the size floors to zero shares) — and
    neither of them created anything a human can act on. ``brokers.paper_execution`` adjudicates
    ORDERS (``preview``/``decide`` take an ``order_id``); a ``ProposalRecord`` with
    ``order_id IS NULL`` is not reachable from any wired surface. So the record was permanently
    consuming and permanently un-actionable: the next cycle reported the filing as
    ``already_copied`` — a false statement, it was never copied — and an amendment of it hit
    ``trade_already_copied``. Every disclosure seen during a killswitch window was lost forever.

    That is the same "a failed attempt is not a completed copy" class ``MAX_COPY_ATTEMPTS`` and
    ``_dispositions`` were built to close, re-entering through the other branch. A HALT window is
    transient by design; a retry is the correct response to it, and the retry is bounded by
    ``MAX_COPY_ATTEMPTS`` and by ``engine.plan``'s staleness gate.
    """
    return order_id is not None or str(status) in _CONSUMING_STATUSES


@dataclass(frozen=True)
class _Disposition:
    """What an earlier cycle already did with one disclosure."""

    consumed: bool          # an order exists, or a human owns it -> never re-propose
    attempts: int           # how many ProposalRecords this key already has
    next_id: str | None     # the id to use now, or None when consumed/exhausted

    @property
    def exhausted(self) -> bool:
        return not self.consumed and self.next_id is None


def _dispositions(state, keys) -> dict[str, _Disposition]:
    """Resolve, per disclosure key, whether it is finished with and which id a retry gets.

    A REJECTED — OR UN-ACTIONABLE — PROPOSAL IS NOT A COMPLETED COPY (see ``_consuming`` for the
    second half of that, which is why the consuming test is a function and not a set lookup).
    The predecessor of this function asked only
    "does a ``ProposalRecord`` exist under this id", which meant a risk rejection — usually
    ENVIRONMENTAL (no quote seam wired, a stale quote, an absent ``PortfolioSnapshot``,
    momentary buying power) — permanently consumed the filing. A transient outage silently
    became "we never copy that trade", with the cycle reporting a tidy ``already_copied`` skip
    and looking healthy. That is the exact shape of loss ``ingest``'s ``Dedup.discard`` exists
    to prevent, one layer up, and it is why ``_CONSUMING_STATUSES`` excludes ``draft``.

    Reads every attempt id for each key in ONE chunked query, so this stays a single round
    trip regardless of ``MAX_COPY_ATTEMPTS``.
    """
    keys = list(dict.fromkeys(keys))
    if not keys:
        return {}
    # id -> (key, attempt), for every attempt slot this key could occupy.
    slots: dict[str, tuple[str, int]] = {}
    for key in keys:
        for n in range(MAX_COPY_ATTEMPTS):
            slots[proposal_id_for(key, n)] = (key, n)

    wanted = sorted(slots)
    rows: list[tuple[str, str | None, str | None]] = []
    with state.session_factory() as s:
        for i in range(0, len(wanted), _ID_CHUNK):
            rows.extend(s.execute(
                select(ProposalRecord.id, ProposalRecord.status, ProposalRecord.order_id)
                .where(ProposalRecord.id.in_(wanted[i:i + _ID_CHUNK]))
            ).all())

    consumed: set[str] = set()
    used: dict[str, int] = {k: 0 for k in keys}
    for pid, status, order_id in rows:
        key, _n = slots[pid]
        used[key] += 1
        # An ORDER is the unambiguous signal: a human may already be approving it, so a second
        # proposal for the same filing is the one mistake here that costs real money.
        if _consuming(status, order_id):
            consumed.add(key)

    out: dict[str, _Disposition] = {}
    for key in keys:
        n = used[key]
        is_consumed = key in consumed
        nxt = None if (is_consumed or n >= MAX_COPY_ATTEMPTS) else proposal_id_for(key, n)
        out[key] = _Disposition(consumed=is_consumed, attempts=n, next_id=nxt)
    return out


_TRADE_SCAN_LIMIT = 20_000
"""A MEMORY bound on the amendment guard's read, and no longer its window.

The window is TIME (``_trade_history_cutoff``). It used to be this row count, and a count is the
wrong axis for two reasons that compound: ``_persist_record`` writes one ``EVENT_PROPOSAL`` entry
per disclosure INCLUDING every risk-REJECTED draft (an unpromoted symbol is rejected on
``symbol_allowlist`` and still persists a record), and ``load_disclosures`` hands a cycle up to
500 rows a call. So roughly ten busy cycles — or one PTR batch of unallowlisted names from a
hostile followed filer — could evict real copy history and re-enable double-copying, which makes
the guard's memory a quantity the untrusted feed controls.

If the memory bound is ever reached inside the time window the cycle REFUSES
(``copy_history_unreadable``) rather than proceeding on partial history: a guard that silently
forgets is worse than one that stops.
"""

TRADE_DATE_WINDOW_DAYS = 3
"""How far a restated ``tx_date`` may move and still be the SAME real trade.

``trade_identity`` keys on the transaction date, and CORRECTING that date is one of the commonest
reasons a PTR is amended — so an exact-hash guard missed a large share of real amendments: the
same filer, symbol, side and amount with ``tx_date`` moved by one day produced a second
independent ``Order`` for one real trade, and a human approving both doubled the position.

Three days rather than zero, and rather than something wide. The residual is stated: a followed
member who genuinely buys the same name twice inside three days gets one copy, not two. That is
the direction ``trade_identity`` already errs in ("the direction to err in is fewer orders"), and
the suppression is counted under its OWN reason (``trade_restated_nearby``) so a near-miss is
never silently indistinguishable from an exact restatement.
"""


def _trade_history_cutoff(policy, now):
    """How far back the amendment guard has to remember: two staleness windows.

    One window is the most a still-copyable amendment's TRADE can be old (``engine.plan`` drops
    anything older than ``policy.max_age_days``). The ORIGINAL copy of that trade could itself
    have been proposed a full window after the trade, so the entry to match against can be a
    second window back. Twice ``max_age_days`` covers both legs and nothing else.
    """
    return now - timedelta(days=2 * max(int(getattr(policy, "max_age_days", 45)), 1))


@dataclass(frozen=True)
class _CopiedTrade:
    """One real trade this system has already copied, as recorded in the signed chain."""

    base_id: str
    symbol: str
    side: str
    tx_date: date | None


def trade_identity(symbol, side, tx_date) -> str:
    """The identity of the REAL-WORLD TRADE a disclosure describes: symbol + side + trade date.

    Idempotency on ``Disclosure.key`` (``chamber:doc_id:line_no``) protects against the same
    FILING arriving twice. It does nothing about the same TRADE arriving twice, which is a
    routine event: an AMENDED PTR gets a NEW ``doc_id`` from the clerk, so it enters as new
    evidence (by design — see ``schemas/models/congress_disclosure.py``) with a new key, a new
    proposal id, and, before this guard existed, a second independent ``Order`` for one real
    trade. A human approving both would double the intended position.

    Deliberately NOT keyed on the filer. Two followed members disclosing the same side of the
    same name on the same day are one position to this account, not two, and the direction to
    err in is fewer orders. Deliberately NOT keyed on the amount either: an amendment's usual
    purpose is to CORRECT the disclosed range, so including it would defeat the guard.

    THE DATE IS AN EXACT KEY HERE AND A WINDOW AT THE CALL SITE. This hash stays exact so it can
    remain the audit payload's stable identifier; the near-miss that an amendment's corrected
    trade date produces is caught by ``TRADE_DATE_WINDOW_DAYS`` in ``run_copy_cycle``, under its
    own counted reason.
    """
    raw = f"{str(symbol).upper()}|{str(side).lower()}|{tx_date.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _copied_trades(state, *, since) -> tuple[dict[str, _CopiedTrade], bool]:
    """``trade_identity -> the copy that already happened``, from the audit chain.

    Returns ``(copied, overflowed)``. ``overflowed`` is True when ``_TRADE_SCAN_LIMIT`` bit
    INSIDE the time window, i.e. the guard's memory is incomplete; the caller refuses the cycle.

    The chain is the store here for the same reason it is in ``copytrade/universe.py``: this
    is an input to a decision about whether an order may exist, so it must not be a mutable
    table any local writer can edit. Only VERIFIED entries count.

    Fail-closed in the direction that matters. An unverified entry cannot suppress a duplicate,
    so a tampered chain could otherwise re-enable double-copying; ``run_copy_cycle`` therefore
    refuses the whole cycle when ``verify_chain`` fails and only calls this on a good chain.
    """
    with state.session_factory() as s:
        rows = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_PROPOSAL, AuditEntry.actor == "copytrade",
                   AuditEntry.created_at >= since)
            .order_by(AuditEntry.seq.desc())
            .limit(_TRADE_SCAN_LIMIT + 1)
        ).scalars().all()
    overflowed = len(rows) > _TRADE_SCAN_LIMIT
    rows = rows[:_TRADE_SCAN_LIMIT]

    out: dict[str, _CopiedTrade] = {}
    for row in reversed(rows):                     # oldest first: keep the FIRST copy's id
        if not state.audit.verify_entry(row):
            continue
        payload = row.payload or {}
        tid = payload.get("copy_trade_id")
        # Only a CONSUMING record counts: a risk-rejected draft copied nothing, so an
        # amendment of it must still be reachable (same reasoning as ``_consuming``).
        if not isinstance(tid, str) or not payload.get("copy_consuming"):
            continue
        out.setdefault(tid, _CopiedTrade(
            base_id=str(payload.get("copy_base_id") or payload.get("proposal_id") or ""),
            symbol=str(payload.get("copy_symbol") or payload.get("symbol") or "").upper(),
            side=str(payload.get("copy_side") or "").lower(),
            tx_date=_iso_date(payload.get("copy_tx_date")),
        ))
    return out, overflowed


def _iso_date(value) -> date | None:
    """Parse a payload date, or None. An entry written before the amendment guard grew its
    symbol/side/date fields simply cannot participate in the WINDOW match; it still participates
    in the exact-hash match, so the guard never gets weaker on old history."""
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _nearby_copy(copied, symbol: str, side: str, tx_date: date) -> _CopiedTrade | None:
    """An already-copied trade in the same name and direction within ``TRADE_DATE_WINDOW_DAYS``.

    Deterministic: the candidates are scanned in the sorted order of the identities that produced
    them, so two runs over the same chain pick the same prior copy.
    """
    for tid in sorted(copied):
        prior = copied[tid]
        if prior.tx_date is None or prior.symbol != symbol.upper() or prior.side != side.lower():
            continue
        if abs((prior.tx_date - tx_date).days) <= TRADE_DATE_WINDOW_DAYS:
            return prior
    return None


UNIVERSE_BOUNDS_REF = "copy_universe_bounds"
"""The key under which a proposal's lineage records the universe bounds that DRAFTED it.

SEC-15 is re-checked at submit (``brokers.paper_execution._watchlist``), and that re-check has to
judge against the same authority the draft did. It re-derived a policy from ``Settings`` instead,
so the two sides read the same audited chain through DIFFERENT bounds: a cycle run with
``universe_ttl_days=1`` drafted an NVDA order, and five days later the drafting policy's universe
was empty while the settings-derived one (TTL 120) still contained NVDA — the submit-time
allowlist strictly WIDER than the authority that drafted the order. ``copytrade/universe.py``
already names that hazard ("a universe bounded by a different policy than the one that sized the
trades would be a silent split-brain"); recording the bounds is how the two sides stop guessing.
"""


def _lineage_job(state, clock, *, proposal: dict, source_key: str, policy) -> AgentJob:
    """Record the DETERMINISTIC producer of one proposal as an auditable ``AgentJob``.

    Not a placeholder to satisfy a foreign key: every field is a true statement about the run
    that produced this proposal, and together they are exactly what an incident review needs
    in order to re-derive the output from the inputs.

      * ``runtime`` — ``COPYTRADE_RUNTIME``. The mode ladder reads this at submit and refuses
        it in every live/auto rung (SEC-08), which is the intended posture, not a defect.
      * ``model`` / ``model_pinned`` — ``"none"``, twice and literally. Invariant 2 and the
        no-LLM-on-the-order-path rule are visible in the STORED provenance, not only in a
        docstring, so a reader of the job table can see there was nothing to hijack.
      * ``prompt_template`` — NULL. There is no prompt; inventing a name for one would be a
        lie in the one place provenance is supposed to be checkable.
      * ``prompt_hash`` — sha256 over the canonical bytes of the emitted proposal. For a
        sampled model run the prompt hash is what makes the result attributable; for a
        deterministic producer the output digest is the stronger form of the same property
        (identical disclosure + identical policy => identical bytes => identical hash).
      * ``input_refs`` — the disclosure's own raw-store locators plus its key (Invariant 8:
        the run is interrogable back to the filing bytes it read).
      * ``usage_estimate`` — 0. Not "unknown": a model-free run costs nothing, and recording
        that honestly keeps the cost ledger complete.

    The id is DERIVED from the proposal id, so a replayed cycle re-derives the same job id
    rather than minting a second lineage for one deterministic run.
    """
    now = clock.now()
    pid = str(proposal["proposal_id"])
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"), default=str)
    job = AgentJob(
        id=f"job_{pid}",
        runtime=COPYTRADE_RUNTIME,
        model="none",
        model_pinned="none",
        prompt_template=None,
        prompt_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        input_refs={
            "source_key": escape_untrusted(str(source_key))[:160],
            "source_refs": [str(ref) for ref in proposal.get("source_refs", ())],
            # The SEC-15 bounds this cycle judged the universe under. See UNIVERSE_BOUNDS_REF.
            UNIVERSE_BOUNDS_REF: {
                "universe_ttl_days": int(policy.universe_ttl_days),
                "max_universe_symbols": int(policy.max_universe_symbols),
            },
        },
        schema_id="TradeProposal",
        status="done",
        usage_estimate=Decimal("0"),
        session_id=None,
        started_at=now,
        completed_at=now,
        created_at=now,
    )
    state.audit.record_state_change(
        job, EVENT_AGENT_JOB, actor="copytrade",
        payload={"status": "done", "runtime": COPYTRADE_RUNTIME, "model": "none",
                 "proposal_id": pid},
    )
    return job


def _refuse_cycle(disclosures, reason: str, detail: str) -> CopyCycleResult:
    """Refuse the WHOLE cycle before anything is built, recording one named ``Skip`` per
    disclosure. Fail-closed paths still have to account for every input (Invariant 7: a
    degradation is a positive statement, never an absence)."""
    skips = tuple(
        Skip(key=d.key, symbol=getattr(d, "symbol", None), reason=reason, detail=detail)
        for d in disclosures
    )
    return CopyCycleResult(0, 0, 0, 0, skips, (), (f"{reason}: {detail}",))


def _texts(intent, policy, filer: str) -> tuple[str, str, str]:
    """The three free-text fields, composed DETERMINISTICALLY from the filing's own facts.

    No model produced these and none ever will: the thesis of a copied trade IS the
    disclosure, and inventing a narrative around it would be manufacturing conviction the
    system does not have. ``filer`` arrives already scrubbed (SEC-09) and everything is run
    through ``escape_untrusted`` — idempotent by construction, so re-escaping the ingest
    layer's already-escaped display name is safe.

    The disclosed dollar RANGE is kept verbatim on purpose. It is the filer's public
    disclosure, not a fact about this account, and the operator cannot judge the signal
    without it — see ``run_copy_cycle`` for why the SEC-09 scrubber is not run over the
    composed sentence.
    """
    lag = (intent.filed_date - intent.tx_date).days
    origin = (f" Translated from a disclosed {intent.translated_from} position."
              if intent.translated_from else "")
    thesis = (
        f"Deterministic copy of a disclosed congressional trade. {filer} disclosed a "
        f"{intent.side} of {intent.symbol}, transacted {intent.tx_date.isoformat()} and "
        f"filed {intent.filed_date.isoformat()} — a {lag}-day disclosure lag; the trade is "
        f"{intent.age_days} days old. Disclosed amount range: {intent.amount_label}."
        f"{origin} The size below is derived from THIS account's portfolio value, never "
        f"from the disclosed range, which describes another person's balance sheet."
    )
    invalidation = (
        f"Wrong when the followed filer discloses a closing transaction in {intent.symbol}, "
        f"or when no followed filer has disclosed a trade in it for "
        f"{policy.universe_ttl_days} days and it leaves the copy universe. There is no "
        f"independent thesis here: the only evidence is a {lag}-day-old filing, so any "
        f"price move between the trade date and now is unexplained by this signal."
    )
    notes = "; ".join(str(n) for n in (intent.sizing_notes or ()))
    sizing_basis = (
        f"congressional copy: {policy.copy_fraction} of portfolio value x disclosed-tier "
        f"weight x staleness decay, clamped to "
        f"[{policy.min_notional_per_trade}, {policy.max_notional_per_trade}]"
        + (f" ({notes})" if notes else "")
    )
    return (escape_untrusted(thesis), escape_untrusted(invalidation),
            escape_untrusted(sizing_basis))


def _json_notional(size: Decimal) -> float | None:
    """The proposal's ``notional`` as a JSON number, or ``None`` if it cannot be one.

    ``TradeProposal.schema.json`` types ``notional`` as a JSON ``number``, so the Decimal the
    sizing rules produced has to cross into binary float exactly once — here, under a guard.
    Three things can go wrong at that boundary and ALL must fail closed rather than propagate:

      * ``quantize`` RAISES ``InvalidOperation`` on a finite Decimal too large to express at
        cent scale within the arithmetic context (``Decimal("1E400")`` is finite, and
        ``.quantize(Decimal("0.01"))`` on it raises). Unguarded, one absurd size — reachable
        from a mistyped ``congress_max_notional_per_trade`` — aborts the WHOLE cycle instead
        of skipping ONE intent, which is a fail-open in the operational sense: the disclosures
        that would have been proposed silently never are;
      * a Decimal above ``sys.float_info.max`` converts to ``inf`` (Python widens rather than
        raising). ``inf`` satisfies jsonschema's ``number``, serialises into
        ``ProposalRecord.payload`` as the literal ``Infinity`` — which is not JSON and does not
        round-trip — and would then size an order tuple. Same class ``agent_runner.runner``
        rejects for model output under Inv 3;
      * rounding DOWN to the cent can land on zero for a sub-cent size, and a zero-notional
        proposal is not an order.

    ``ROUND_FLOOR`` (never ``ROUND_HALF_*``) so the cent-rounding can only ever shrink the
    order: every rounding on this path leans toward less exposure.
    """
    try:
        cents = size.quantize(_CENTS, rounding=ROUND_FLOOR)
    except (InvalidOperation, ValueError, OverflowError):
        return None
    if cents <= 0:
        return None
    amount = float(cents)
    return amount if math.isfinite(amount) and amount > 0 else None


def _build_proposal(intent, policy, *, pid: str, filer: str, notional: float) -> dict:
    """The TradeProposal dict. Every field is pinned or derived; nothing is optional.

    ``notional`` is passed in ALREADY validated (see ``_json_notional``) rather than read off
    ``intent`` here, so this function cannot be the place a non-finite or non-Decimal size
    slips into a schema-valid-looking proposal.
    """
    thesis, invalidation, sizing_basis = _texts(intent, policy, filer)
    return {
        "proposal_id": pid,
        "symbol": intent.symbol,
        "side": intent.side,
        "asset_type": _ASSET_TYPE,
        "order_type": _ORDER_TYPE,
        "quantity": None,
        "notional": notional,
        "time_horizon": _TIME_HORIZON,
        "thesis": thesis,
        "invalidation": invalidation,
        "sizing_basis": sizing_basis,
        # The citation set is the disclosure's own raw-store locators (Invariant 8): the
        # proposal is interrogable back to the filing bytes it came from.
        "source_refs": [str(ref) for ref in intent.source_refs],
        # NOT a parameter. See ``COPYTRADE_RUNTIME``: the provenance of a model-free producer
        # is a property of this module, never of whoever called it.
        "generated_by": {"runtime": COPYTRADE_RUNTIME, "model": "none"},
    }


def _persist_record(state, clock, *, proposal: dict, status: str,
                    risk_decision_id: str | None, order_id: str | None,
                    source_key: str, trade_id: str, tx_date: date,
                    policy) -> ProposalRecord:
    """Persist the lineage job, the ``ProposalRecord`` and its ``EVENT_PROPOSAL`` audit entry.

    No collision remint (see ``proposal_id_for``). A genuine PK collision here means a second
    cycle raced this one over the same filing; the ``IntegrityError`` propagates and the
    cycle fails LOUDLY rather than silently drafting a duplicate order for a human to approve
    twice. The scheduler runs one copy cycle at a time, so this is a tripwire, not a path.

    The lineage job is written FIRST so a ``ProposalRecord.job_id`` never points at a row that
    does not exist yet — ``paper_execution._load_bundle`` resolves that pointer and refuses the
    order if it dangles, which is exactly the guard this ordering keeps honest.

    The audit payload carries the amendment guard's three fields (``copy_trade_id``,
    ``copy_base_id``, ``copy_consuming``). They live in the signed chain rather than in a
    column because they gate whether a second order may exist for one real trade, and an input
    to that decision must not be editable without the HMAC key.
    """
    now = clock.now()
    job = _lineage_job(state, clock, proposal=proposal, source_key=source_key, policy=policy)
    record = ProposalRecord(
        id=str(proposal["proposal_id"]),
        symbol=str(proposal["symbol"]).upper(),
        side=str(proposal["side"]),
        status=status,
        payload=proposal,
        job_id=job.id,                   # the deterministic run that produced it (lineage)
        risk_decision_id=risk_decision_id,
        order_id=order_id,
        flagged=False,                   # SEC-14 grounding is a model-output control
        created_at=now,
        updated_at=now,
    )
    state.audit.record_state_change(
        record, EVENT_PROPOSAL, actor="copytrade",
        payload={"proposal_id": record.id, "symbol": record.symbol, "status": status,
                 "source": "copytrade", "job_id": job.id,
                 "copy_trade_id": trade_id,
                 "copy_base_id": proposal_id_for(source_key),
                 # The identity's COMPONENTS, so the guard can also catch an amendment that
                 # corrected the trade date (``TRADE_DATE_WINDOW_DAYS``) rather than only an
                 # exact restatement. Signed, like everything else it decides on.
                 "copy_symbol": record.symbol,
                 "copy_side": record.side,
                 "copy_tx_date": tx_date.isoformat(),
                 # "did this record finish the trade off" — an ORDER exists, or it posted. A
                 # rejected draft, and a needs_human record with no order, are NOT consuming:
                 # neither copied anything and neither is reachable from a wired human gate.
                 "copy_consuming": _consuming(status, order_id)},
    )
    return record


def _resolve_order(state, clock, *, proposal: dict, ctx, rd_id: str,
                   account: str) -> tuple[Order | None, str]:
    """Resolve the canonical order tuple and create the ``Order`` row (state ``proposed``).

    MIRRORS ``workflows.proposals._resolve_order`` and must stay in step with it — the two
    differences are deliberate: the audit ``actor`` is ``copytrade`` (an order's provenance
    has to name the path that made it, and misattributing it to ``proposals`` would corrupt
    the one field an incident review starts from), and this path never emits a market order,
    so the limit price is always required.

    ``qty = floor(notional / quote price)``, minimum 1. Fail -> ``(None, reason)`` and the
    caller demotes to human review; a copy trade is never worth guessing a size for.
    """
    if ctx.quote_price <= 0:
        return None, "no quote to price the limit order (needs human)"
    qty = (_dec(proposal["notional"]) / ctx.quote_price).to_integral_value(
        rounding=ROUND_FLOOR)
    if not qty.is_finite() or qty < 1:
        return None, "sized below one share at the gate quote (needs human)"
    try:
        canon = canonical_order_tuple(
            symbol=str(proposal["symbol"]), side=str(proposal["side"]), qty=qty,
            order_type=_ORDER_TYPE, limit_price=ctx.quote_price, tif=_TIF,
        )
        oth = order_tuple_hash(canon)
    except ValueError as exc:
        return None, f"order tuple canonicalization failed: {exc}"
    order_row = create_order(
        state,
        proposal_id=str(proposal["proposal_id"]),
        risk_decision_id=rd_id,
        account=account,
        symbol=canon["symbol"],
        side=canon["side"],
        qty=qty,
        order_type=canon["order_type"],
        tif=canon["tif"],
        order_tuple_hash=oth,
        limit_price=ctx.quote_price,
        notional=order_notional(proposal, ctx),
        actor="copytrade",
        clock=clock,
    )
    return order_row, ""


def run_copy_cycle(state, *, policy, disclosures, clock=None,
                   producing_runtime: str = COPYTRADE_RUNTIME,
                   quote_fn=None, account: str = "paper") -> CopyCycleResult:
    """One deterministic copy-trading cycle: disclosures in, counted dispositions out.

    ``quote_fn(symbol) -> {"price", "age_s", "spread_pct"} | None`` is the injected quote
    seam, the SAME shape ``proposals.draft_proposal`` takes and for the same reason: nothing
    in ``workflows/`` fetches a quote. With no ``quote_fn`` wired, ``quote_price`` is 0, the
    risk engine's ``quote_sane`` HARD check fails, and every intent is REJECTED with a full
    audited risk decision explaining why. That is the correct fail-closed posture for a
    cycle that cannot see a price — not an outage to work around.

    Nothing here posts to the operator surface or touches a broker; the caller owns delivery.
    """
    clock = clock if clock is not None else state.clock
    now = clock.now()
    # Read the batch's COVERAGE before it is flattened to a list. See the contract check below.
    batch_truncated = bool(getattr(disclosures, "truncated", False))
    batch_window_start = getattr(disclosures, "window_start", None)
    disclosures = list(disclosures or [])
    reasons: list[str] = []
    skips: list[Skip] = []

    # ---- the provenance string is OURS, not the caller's (see ``COPYTRADE_RUNTIME``) ------
    # Checked before the SEC-08 assertion below, because the value that assertion judges must
    # be this module's constant — otherwise the caller, not the gate, decides the outcome.
    if producing_runtime != COPYTRADE_RUNTIME:
        return _refuse_cycle(
            disclosures, "producing_runtime_override_refused",
            f"the copy path's provenance is fixed at {COPYTRADE_RUNTIME!r}; "
            f"{escape_untrusted(str(producing_runtime))[:48]!r} was supplied",
        )

    # ---- SEC-08 / Inv 2 — FIRST, before anything is built (see the module header) --------
    # Deliberately STRICTER than the gateway's submit-time backstop: refusing here means a
    # live rung never accumulates a queue of proposals the gateway would later refuse one by
    # one. Strictly a narrowing of the gate, never a widening.
    mode = ModeLadder(state).current()
    try:
        assert_execution_runtime(mode, COPYTRADE_RUNTIME)
    except ExecutionRuntimeError as exc:
        return _refuse_cycle(disclosures, "sec08_runtime_refused", str(exc))

    # ---- Inv 7 — a watch-only vendor window disables the order path entirely -------------
    if BreakerEngine(state).is_watch_only():
        return _refuse_cycle(
            disclosures, "watch_only",
            "a vendor usage window is exhausted; the order path is disabled",
        )

    # ---- the batch has to COVER the window the engine reasons over ------------------------
    # ``engine._contested_sides`` reads a symbol's disagreement off the whole batch precisely so
    # that an opposing filing cannot age out and leave a one-sided BUY of a name a followed
    # member exited. Its docstring asserted callers "load a window at least as wide as
    # max_age_days"; nothing enforced that, and the repository's own loader violated it — a bare
    # ``LIMIT 500`` on ``tx_date DESC`` evicts an older sale while keeping the newer buy it
    # contests. So the contract is checked here, from coverage the loader REPORTS, and a batch
    # that cannot support the check refuses the cycle instead of quietly under-counting it.
    # A caller passing a plain list makes no claim and gets no check (nothing to verify).
    if batch_truncated:
        return _refuse_cycle(
            disclosures, "disclosure_window_truncated",
            "the disclosure load hit its row cap, so the batch is not the whole window and a "
            "contested symbol could read as uncontested",
        )
    if batch_window_start is not None:
        needed = now.date() - timedelta(days=int(policy.max_age_days))
        if batch_window_start > needed:
            return _refuse_cycle(
                disclosures, "disclosure_window_too_narrow",
                f"the batch covers trades from {batch_window_start.isoformat()} but "
                f"policy.max_age_days needs {needed.isoformat()}; an opposing filing could be "
                f"outside it",
            )

    if not disclosures:
        return CopyCycleResult(0, 0, 0, 0, (), (), ("no disclosures in window",))

    # ---- portfolio facts (SEC-15: audited limits + latest snapshot; absent -> zeros) ------
    # Built through ``proposals.build_risk_context`` so there is exactly ONE construction of
    # a RiskContext in the tree. The empty watchlist here is intentional: this instance is
    # read only for its reconciled cash/holdings, never used to evaluate anything.
    base_ctx = build_risk_context(state, quote=None, watchlist=frozenset())
    holdings = dict(base_ctx.holdings)
    portfolio = portfolio_value(base_ctx.cash, holdings)

    # ---- idempotency: which disclosures are FINISHED WITH, and which may retry ------------
    # "Finished with" means an order exists or a human owns it — NOT merely "a record exists".
    # A risk rejection is usually environmental and transient, so it stays retryable until it
    # either succeeds, exhausts MAX_COPY_ATTEMPTS, or ages out of the staleness window in
    # ``engine.plan``. See ``_dispositions``.
    # The amendment guard (below) reads the signed chain to decide whether a second order may
    # exist for one real trade. An unverifiable chain cannot answer that question, and the
    # unsafe direction is the permissive one, so the cycle refuses rather than proceeding
    # without the guard. (``copytrade.universe`` fails closed on the same condition by
    # yielding the empty universe; here the tight answer is "build nothing".)
    if not state.audit.verify_chain().ok:
        return _refuse_cycle(
            disclosures, "audit_chain_unverified",
            "the hash chain does not verify; the duplicate-trade guard cannot be trusted",
        )
    copied_trades, history_overflowed = _copied_trades(
        state, since=_trade_history_cutoff(policy, now))
    if history_overflowed:
        # The guard's memory is incomplete inside the window an amendment can still be copied in.
        # A duplicate order is the one mistake on this path that costs real money, so a guard
        # that cannot see all of its own window stops the cycle rather than guessing.
        return _refuse_cycle(
            disclosures, "copy_history_unreadable",
            f"more than {_TRADE_SCAN_LIMIT} copy proposal entries inside the amendment window; "
            f"the duplicate-trade guard cannot see all of its own history",
        )

    disp = _dispositions(state, [d.key for d in disclosures])
    already_copied = frozenset(k for k, v in disp.items() if v.consumed)
    if already_copied:
        reasons.append(f"{len(already_copied)} disclosure(s) already copied")
    retrying = sorted(k for k, v in disp.items() if not v.consumed and v.attempts > 0
                      and v.next_id is not None)
    if retrying:
        reasons.append(
            f"{len(retrying)} disclosure(s) re-proposed after an earlier risk rejection "
            f"(attempt {disp[retrying[0]].attempts + 1} of {MAX_COPY_ATTEMPTS})")
    # Every id already on disk, so a new proposal can never collide with one.
    seen = {proposal_id_for(k, n) for k, v in disp.items() for n in range(v.attempts)}

    # ---- the pure engine decides WHAT to copy; this module decides nothing ---------------
    copy_plan = plan(
        disclosures, policy,
        PlanContext(today=now.date(), portfolio_value=portfolio, holdings=holdings,
                    already_copied=already_copied),
    )
    skips.extend(copy_plan.skips)

    # ---- discovery -> promotion (see copytrade/universe.py) ------------------------------
    # Promotion makes a symbol ELIGIBLE FOR THE RISK ENGINE TO JUDGE. It is not approval to
    # trade, and a refused promotion does not short-circuit anything: the intent is still
    # built and still evaluated, and the risk engine rejects it on ``symbol_allowlist`` with
    # a real audited decision rather than this module quietly dropping it.
    #
    # ONLY symbols that are not ALREADY allowlisted are promoted. ``symbol_allowlist`` is
    # ``holdings OR watchlist``, so a held name (every exit intent is one — the engine only
    # mirrors a sale of something this account owns) and an operator-listed name are already
    # eligible; promoting them would spend capped universe slots to buy nothing, and at the
    # cap that spend is taken from a genuinely-new name that DOES need the slot. Narrowing
    # here can never reduce what the risk engine may judge, only what the allowlist has to
    # carry to say the same thing.
    static_watchlist = frozenset(getattr(state.settings, "watchlist_symbols", frozenset()))
    intents = sorted(copy_plan.intents, key=lambda i: (i.symbol, i.source_key))
    novel_syms = {
        i.symbol for i in intents
        if i.symbol not in holdings and i.symbol not in static_watchlist
    }
    already_eligible = len({i.symbol for i in intents}) - len(novel_syms)
    if already_eligible:
        reasons.append(f"universe: {already_eligible} symbol(s) already allowlisted, not promoted")
    if novel_syms:
        novel = sorted(novel_syms)
        keys = sorted({i.source_key for i in intents if i.symbol in novel_syms})
        eligible = promote(
            state, novel,
            reason="followed-filer disclosure: " + ", ".join(keys),
            clock=clock, policy=policy,
        )
        reasons.append(f"universe: {len(eligible)} of {len(novel)} symbol(s) promoted this cycle")
    universe = current_universe(state, policy, clock=clock)
    watchlist = static_watchlist | universe

    proposed = approved = needs_human = rejected = 0
    orders: list[Order] = []
    order_intents: dict[str, CopyIntent] = {}

    for intent in intents:
        key = intent.source_key
        disposition = disp.get(key)

        # Attempts exhausted: the filing has been through MAX_COPY_ATTEMPTS risk rejections
        # and is not getting a fresh one. Named, counted, and distinct from ``already_copied``
        # so "we gave up on this" never reads as "we already traded this".
        if disposition is not None and disposition.exhausted:
            skips.append(Skip(key, intent.symbol, "copy_attempts_exhausted",
                              f"{disposition.attempts} rejected attempt(s)"))
            continue

        pid = (disposition.next_id if disposition is not None and disposition.next_id
               else proposal_id_for(key))

        if pid in seen:
            # Belt to ``already_copied``'s braces: the engine is free to emit an intent for a
            # key it was told about, and a duplicate order is the one mistake on this path
            # that costs real money.
            skips.append(Skip(key, intent.symbol, "already_proposed", pid))
            continue

        # ---- one REAL TRADE, one copy (amended filings) ----------------------------------
        # Idempotency on the filing key cannot see an AMENDMENT: the clerk gives it a new
        # ``doc_id``, so it is a new key, a new proposal id and — without this — a second
        # independent Order for a trade already copied. Keyed on symbol+side+tx_date; see
        # ``trade_identity``. In-cycle and cross-cycle are the same check because an original
        # and its amendment can perfectly well arrive in one batch.
        tid = trade_identity(intent.symbol, intent.side, intent.tx_date)
        prior = copied_trades.get(tid)
        if prior is not None:
            skips.append(Skip(key, intent.symbol, "trade_already_copied",
                              f"restates a trade already copied as {prior.base_id}"))
            continue
        # ... and an amendment that CORRECTED the trade date is still the same trade. Counted
        # under its own reason, because "the dates differ by a day" is a weaker statement than
        # "the filing restates it exactly" and an operator should be able to tell them apart.
        near = _nearby_copy(copied_trades, intent.symbol, intent.side, intent.tx_date)
        if near is not None:
            skips.append(Skip(
                key, intent.symbol, "trade_restated_nearby",
                f"{intent.side} {intent.symbol} on {intent.tx_date.isoformat()} is within "
                f"{TRADE_DATE_WINDOW_DAYS}d of {near.tx_date.isoformat()}, already copied as "
                f"{near.base_id}"))
            continue

        # The symbol is the one value that reaches an INDEXED column, the audit payload and
        # the order tuple, so it is validated before it is written anywhere — not left for
        # schema validation, which happens after the record would already carry it.
        if type(intent.symbol) is not str or not _TICKER.match(intent.symbol):
            skips.append(Skip(key, None, "invalid_symbol",
                              escape_untrusted(str(intent.symbol))[:24]))
            continue

        # A non-finite or non-positive size must never become a JSON number: ``float('nan')``
        # satisfies jsonschema's ``number`` and serialises to a literal the audit chain's
        # canonical encoder cannot round-trip (the same class ``agent_runner.runner`` rejects
        # for model output under Inv 3 / SEC-23). Checked as a Decimal first, then again
        # across the float boundary in ``_json_notional`` — the overflow case is only
        # observable on the far side of that conversion.
        size = _dec(intent.notional)
        amount = _json_notional(size) if size.is_finite() and size > 0 else None
        if amount is None:
            skips.append(Skip(key, intent.symbol, "invalid_notional",
                              escape_untrusted(str(intent.notional))[:24]))
            continue

        # ---- SEC-09 on the ONE untrusted string ------------------------------------------
        # The filer's display name comes off a filing typed by a third party. It is scrubbed
        # (and BLOCKS on an account-number-shaped hit) BEFORE it is composed into the thesis.
        #
        # The composed sentence is deliberately NOT scrubbed afterwards. SEC-09 governs MODEL
        # free text — a hijacked model stuffing balances into a schema-valid field — and this
        # text has no model in its provenance. Running the scrubber over it would redact the
        # disclosed dollar RANGE to "[amount]", destroying the single fact the operator needs
        # to judge the signal, and would normalise redaction markers in records where they
        # mean nothing.
        scrubbed, blocked = scrub_output_fields(
            state, {"filer": intent.filer_display}, ("filer",),
            subject_symbols=frozenset({intent.symbol}), target="copytrade_filer",
        )
        if blocked:
            skips.append(Skip(key, intent.symbol, "scrub_blocked",
                              "filer display name hit a blocking SEC-09 class"))
            continue

        proposal = _build_proposal(intent, policy, pid=pid,
                                   filer=str(scrubbed["filer"]), notional=amount)

        # ---- Inv 3: schema-validate; an invalid proposal is quarantined, never persisted --
        # No ``ProposalRecord`` is minted, because ``ProposalRecord.payload`` is defined as a
        # SCHEMA-VALID TradeProposal and storing an invalid one would corrupt that contract
        # for every reader. The cost is that a permanently-invalid disclosure re-skips each
        # cycle — visible and counted, which is the right failure mode for what would
        # otherwise be a silent entombment.
        try:
            jsonschema.validate(proposal, load_schema("TradeProposal"))
        except jsonschema.ValidationError as exc:
            skips.append(Skip(key, intent.symbol, "schema_invalid",
                              escape_untrusted(str(exc.message))[:200]))
            continue

        # ---- deterministic risk gate (Invariant 5: evaluate() only) ----------------------
        quote = None
        if quote_fn is not None:
            try:
                quote = quote_fn(intent.symbol)
            except Exception as exc:                       # noqa: BLE001 - degrade, never raise
                # A real quote feed does I/O and raises for ordinary reasons. Degrade to the
                # absent-quote case the risk gate already fails closed on; a quote lookup
                # must not be able to take down the cycle that it merely informs.
                log.warning("copytrade: quote unavailable for %s: %s: %s",
                            intent.symbol, type(exc).__name__, exc)
                reasons.append(f"quote unavailable for {intent.symbol}")
                quote = None
        ctx = build_risk_context(state, quote=quote, watchlist=watchlist)
        rd = evaluate(proposal, ctx)

        rd_row = RiskDecision(
            id=new_id("rd"), proposal_id=pid, decision=rd["decision"], checks=rd["checks"],
            failed_checks=rd["failed_checks"], warnings=rd["warnings"],
            max_allowed_notional=_dec(rd["max_allowed_notional"]),
            human_required=bool(rd["human_required"]),
            evaluated_at=now, created_at=now,
            execution_verdict=None,   # no book feed on this path (QT-06 reads null as "not evaluated")
        )
        state.audit.record_state_change(
            rd_row, "risk_decision", actor="risk",
            payload={"proposal_id": pid, "decision": rd["decision"]},
        )
        proposed += 1

        # ---- Order row ONLY on an approved decision (Invariant 1) -------------------------
        status = "draft"
        order_row: Order | None = None
        if rd["decision"] == "approved":
            if halt_is_set(default_halt_path()):
                status = "needs_human"
                reasons.append(f"{intent.symbol}: HALT sentinel set (SEC-04); order not created")
            else:
                order_row, why = _resolve_order(state, clock, proposal=proposal, ctx=ctx,
                                                rd_id=rd_row.id, account=account)
                if order_row is None:
                    status = "needs_human"
                    reasons.append(f"{intent.symbol}: {why}")
        elif rd["decision"] == "needs_human":
            status = "needs_human"
        else:
            # A rejected decision still persists as a ``draft`` record, exactly as
            # ``proposals.py`` does: the authoritative verdict is the linked RiskDecision,
            # never the record's lifecycle status. Inventing a new status value here would
            # mean every existing reader of ProposalRecord.status silently mishandles it.
            reasons.append(f"{intent.symbol}: risk rejected ({', '.join(rd['failed_checks'])})")

        _persist_record(state, clock, proposal=proposal, status=status,
                        risk_decision_id=rd_row.id,
                        order_id=order_row.id if order_row is not None else None,
                        source_key=key, trade_id=tid, tx_date=intent.tx_date, policy=policy)
        # Close BOTH guards over THIS cycle too. ``seen`` and ``copied_trades`` were read once,
        # before the loop, so without these the duplicate checks above only ever catch
        # cross-cycle repeats — and a duplicate order is the one mistake on this path that
        # costs real money. Same consuming rule as the audit payload: a rejected draft copied
        # nothing and must leave the trade reachable.
        seen.add(pid)
        if _consuming(status, order_row.id if order_row is not None else None):
            copied_trades.setdefault(tid, _CopiedTrade(
                base_id=proposal_id_for(key), symbol=intent.symbol, side=intent.side,
                tx_date=intent.tx_date))

        if order_row is not None:
            orders.append(order_row)
            # The evidence that produced THIS order id, so the RW-17 queued notification can
            # name the filing without re-parsing a composed sentence. Recorded next to the
            # append so the two can never fall out of step.
            order_intents[order_row.id] = intent
            approved += 1
        elif status == "needs_human":
            needs_human += 1
        else:
            rejected += 1

    return CopyCycleResult(
        proposed=proposed, approved=approved, needs_human=needs_human, rejected=rejected,
        skipped=tuple(skips), orders=tuple(orders), reasons=tuple(reasons),
        order_intents=order_intents,
    )
