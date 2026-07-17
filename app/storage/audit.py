"""WP2 — the tamper-evident audit ledger.

A hash-chained, HMAC-keyed, externally-anchorable append-only log:
  * SEC-10  — each link is an HMAC keyed from keyring; a DB-only writer cannot forge or
              recompute a valid chain without the key. The coverage set includes the row
              ``id`` (a reconciliation join key) so the PK is tamper-evident too.
  * SEC-11  — a strictly-monotonic, NON-REUSABLE ``seq`` + a key-SIGNED high-water mark
              (``audit_meta``) catch tail truncation/rollback by a keyless writer who has NOT
              captured a prior validly-signed watermark. W10 closure (2026-07-08): the
              high-water NEVER decreases, even across a keyless ``DELETE`` — ``_insert``
              allocates ``seq = max(high_water, max_existing) + 1`` so a freed tail seq is
              never reissued, and a delete-then-refill therefore lands as a seq GAP that
              ``verify_chain()`` rejects (the WP8 "delete-then-heal" hole: before this, the
              watermark was reassigned to the current head on every append, so refilling the
              freed seqs re-greened the chain). Appending onto a chain whose signed high-water
              was deleted (rows present, watermark absent) is REFUSED — healing the watermark
              at a refilled head is exactly the bypass. CAVEAT (WP5 adversarial re-verify,
              2026-07-07 — still load-bearing, the monotonic seq does NOT close it): a keyless
              writer who captured a past validly-signed ``(seq, sig)`` pair (DB read over time)
              can DELETE the tail and REPLAY that captured watermark; ``verify_chain()`` cannot
              distinguish a rollback-to-a-genuine-past-state from that state. Only the EXTERNAL
              out-of-DB witness closes THAT class: verify_against_anchor() pins a specific
              external anchor point (head posted to #audit-log + committed to git). That anchor
              is consulted on the gate + mode-read + gateway + budget paths (wired WP6/WP8;
              the durable witness is wired at ``main.compose()`` in W10) and fails closed when
              the verified head is below or absent from it.
  * SEC-12  — the HMAC covers ALL load-bearing columns under a pinned canonical form
              (sorted keys, no lossy ``default=`` coercion); the authoritative timestamp is
              ``payload._ts`` and verify reconciles it against the ``created_at`` column so a
              time-of-event edit is flagged.
  * SEC-13 / OPS-01 — record_state_change() writes a state row AND its audit entry in ONE
              transaction (no state change without a covering record); two-phase intent/result
              keyed on the immutable ``seq`` with existence + double-resolve checks;
              in-process lock + cross-process seq-collision retry.

The HMAC key is held only by this storage-layer object — never handed to an agent. WP5
wires the remaining operational pieces: persist the head anchor to an external witness
(git / #audit-log) and verify at startup, route every state write through
record_state_change, and run a periodic reconciliation (unresolved_intents /
orphan_results / verify_completeness).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from datetime import timezone

from sqlalchemy import Column, String, Table, select
from sqlalchemy.exc import IntegrityError, OperationalError

from core.clock import default_clock
from core.ids import new_id
from schemas.models.audit_entry import AuditEntry
from schemas.models.base import Base

# storage-internal, key-signed high-water mark for truncation detection (SEC-11).
audit_meta = Table(
    "audit_meta", Base.metadata,
    Column("k", String, primary_key=True),
    Column("v", String),
)


def _canonical(entry_id, seq, prev_hash, event_type, actor, refs, payload) -> bytes:
    doc = {
        "id": entry_id, "seq": seq, "prev_hash": prev_hash, "event_type": event_type,
        "actor": actor, "refs": refs or {}, "payload": payload or {},
    }
    # No ``default=``: a non-JSON-native value raises here (fail-closed) instead of being
    # coerced to a string that could collide with a genuine string (SEC-12).
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")


class AuditIntegrityError(Exception):
    """A keyless-writer tamper made an append unsafe (SEC-11 W10): the signed monotonic
    high-water is absent on a non-empty chain, or its signature is invalid. Refusing to
    append is fail-closed — healing the watermark at a refilled head is exactly the
    delete-then-heal bypass this closes. Distinct from ``IntegrityError``/``OperationalError``
    (a benign cross-process seq collision, which ``append`` retries) so it is NEVER retried."""


@dataclass
class VerifyResult:
    ok: bool
    head_seq: int
    head_hash: str | None
    error: str | None = None


class AuditLog:
    def __init__(self, session_factory, hmac_key, clock=default_clock):
        self._sf = session_factory
        self._key = hmac_key if isinstance(hmac_key, bytes) else str(hmac_key).encode()
        self._clock = clock
        self._lock = threading.Lock()   # in-process serialization; cross-process => seq UNIQUE + retry

    # ---- hashing / signing -------------------------------------------------
    def _hash(self, entry_id, seq, prev_hash, event_type, actor, refs, payload) -> str:
        return hmac.new(
            self._key, _canonical(entry_id, seq, prev_hash, event_type, actor, refs, payload),
            hashlib.sha256,
        ).hexdigest()

    def _sign(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    def verify_entry(self, entry) -> bool:
        """Recompute ONE entry's HMAC and compare it constant-time. An O(1) integrity probe
        for a hot read path that must trust the signed record, not a mutable domain row: a
        local writer without the key cannot forge ``entry_hash``. Used by the mode ladder's
        ``current()`` (SEC-20/SEC-23) and the gate's verify-bound counting (SEC-20)."""
        recomputed = self._hash(entry.id, entry.seq, entry.prev_hash, entry.event_type,
                                entry.actor, entry.refs, entry.payload)
        return hmac.compare_digest(recomputed, entry.entry_hash or "")

    # ---- signed monotonic high-water mark (SEC-11) -------------------------
    def _bump_hwm(self, s, seq: int) -> None:
        """Advance the signed high-water to ``max(current, seq)`` and re-sign. It NEVER
        decreases, even across a keyless tail ``DELETE`` — that monotonicity is what makes a
        freed seq non-reissuable (``_insert`` allocates above it) so a delete-then-refill
        lands as a seq GAP ``verify_chain`` rejects, instead of the old re-greening refill."""
        cur = self._read_hwm(s)
        hwm = seq if cur is None or cur < 0 else max(cur, seq)
        s.execute(audit_meta.delete().where(audit_meta.c.k.in_(["head_seq", "head_seq_sig"])))
        s.execute(audit_meta.insert().values(k="head_seq", v=str(hwm)))
        s.execute(audit_meta.insert().values(k="head_seq_sig", v=self._sign(str(hwm))))

    def _read_hwm(self, s) -> int | None:
        """The signed monotonic high-water (highest ``seq`` EVER allocated). ``None`` on a
        virgin chain (never appended); ``-1`` if the signature is invalid (tamper)."""
        rows = dict(s.execute(select(audit_meta.c.k, audit_meta.c.v)).all())
        if "head_seq" not in rows:
            return None
        if not hmac.compare_digest(rows.get("head_seq_sig", ""), self._sign(rows["head_seq"])):
            return -1   # signature invalid => watermark tampered
        return int(rows["head_seq"])

    # ---- core insert (shared by append / append_in) ------------------------
    def _insert(self, s, event_type, actor, refs, payload) -> AuditEntry:
        head = s.execute(select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)).scalar_one_or_none()
        max_existing = head.seq if head else 0
        hwm = self._read_hwm(s)
        # SEC-11 (W10) fail-closed guards on the signed high-water BEFORE allocating a seq:
        if hwm == -1:
            raise AuditIntegrityError("audit high-water signature invalid (tamper) — refusing to append")
        if hwm is None and max_existing > 0:
            # Rows present but the signed high-water was deleted: the delete-then-heal signature.
            # Re-creating the watermark at a refilled head is exactly the bypass -> refuse.
            raise AuditIntegrityError(
                "audit high-water missing on a non-empty chain (tamper) — refusing to append")
        # Allocate ABOVE the high-water, so a freed tail seq is NEVER reissued (non-reusable):
        # a delete-then-refill then shows as a contiguity gap, not a silent re-green.
        seq = max(hwm or 0, max_existing) + 1
        prev_hash = head.entry_hash if head else None
        created = self._clock.now().astimezone(timezone.utc).replace(tzinfo=None)   # naive UTC (stable round-trip)
        p = dict(payload or {})
        p["_ts"] = created.isoformat()                 # authoritative, hashed timestamp (SEC-12)
        r = dict(refs or {})
        entry_id = new_id("audit")
        entry_hash = self._hash(entry_id, seq, prev_hash, event_type, actor, r, p)
        row = AuditEntry(id=entry_id, seq=seq, prev_hash=prev_hash, entry_hash=entry_hash,
                         event_type=event_type, actor=actor, refs=r, payload=p, created_at=created)
        s.add(row)
        s.flush()
        self._bump_hwm(s, seq)
        return row

    def append(self, event_type: str, actor=None, refs=None, payload=None) -> AuditEntry:
        for attempt in range(1, 7):
            with self._lock:
                try:
                    with self._sf() as s, s.begin():
                        row = self._insert(s, event_type, actor, refs, payload)
                        s.expunge(row)
                        return row
                except (IntegrityError, OperationalError):
                    if attempt >= 6:
                        raise
            time.sleep(0.02 * attempt)   # cross-process seq collision -> retry against fresh head
        raise RuntimeError("append: exhausted retries")

    def append_in(self, session, event_type, actor=None, refs=None, payload=None) -> AuditEntry:
        """Append WITHIN a caller's active transaction so a state-row write and its audit
        entry commit atomically (SEC-13). The caller owns the session + commit."""
        with self._lock:
            return self._insert(session, event_type, actor, refs, payload)

    def record_state_change(self, model_row, event_type, actor=None, payload=None):
        """Persist a load-bearing state row AND its AuditEntry in ONE transaction — the only
        sanctioned way to mutate RiskDecision / ApprovalRecord / order-state rows (never a
        bare Repo.add). Guarantees no state change without a covering audit record."""
        with self._sf() as s, s.begin():
            s.add(model_row)
            s.flush()
            refs = {"model": model_row.__tablename__, "row_id": model_row.id}
            entry = self.append_in(s, event_type, actor=actor, refs=refs, payload=payload)
            s.expunge(model_row)
            s.expunge(entry)
            return model_row, entry

    # ---- two-phase (SEC-13), keyed on the immutable, hashed ``seq`` ---------
    def intent(self, op: str, refs=None, payload=None) -> AuditEntry:
        p = dict(payload or {})
        p["_phase"], p["_op"] = "intent", op
        return self.append(f"intent:{op}", refs=refs, payload=p)

    def result(self, intent_entry: AuditEntry, op: str, refs=None, payload=None) -> AuditEntry:
        with self._sf() as s:
            rows = s.execute(select(AuditEntry)).scalars().all()
        by_seq = {r.seq: r for r in rows}
        it = by_seq.get(intent_entry.seq)
        if it is None or (it.payload or {}).get("_phase") != "intent":
            raise ValueError(f"result: no intent at seq {intent_entry.seq}")
        if any((r.payload or {}).get("_phase") == "result" and (r.refs or {}).get("intent_seq") == intent_entry.seq
               for r in rows):
            raise ValueError(f"result: intent seq {intent_entry.seq} already resolved")
        r = dict(refs or {})
        r["intent_seq"] = intent_entry.seq
        p = dict(payload or {})
        p["_phase"], p["_op"] = "result", op
        return self.append(f"result:{op}", refs=r, payload=p)

    def _rows(self):
        with self._sf() as s:
            return s.execute(select(AuditEntry)).scalars().all()

    def unresolved_intents(self) -> list[int]:
        rows = self._rows()
        intents = {r.seq for r in rows if (r.payload or {}).get("_phase") == "intent"}
        resolved = {(r.refs or {}).get("intent_seq") for r in rows if (r.payload or {}).get("_phase") == "result"}
        return sorted(intents - resolved)

    def orphan_results(self) -> list[int]:
        rows = self._rows()
        intents = {r.seq for r in rows if (r.payload or {}).get("_phase") == "intent"}
        return sorted(r.seq for r in rows
                      if (r.payload or {}).get("_phase") == "result"
                      and (r.refs or {}).get("intent_seq") not in intents)

    def verify_completeness(self, model_class) -> list[str]:
        """Every row of a load-bearing model must have a covering AuditEntry (refs.row_id).
        Returns the ids of any uncovered rows (SEC-13).

        IN-SCOPE guarantee (honest boundary — do NOT over-claim): this detects a row with NO
        covering AuditEntry — a FRESH row a keyless writer bare-``session.add``ed, bypassing
        ``record_state_change``. It does NOT detect a deleted-then-reused-id tamper: an arbitrary
        keyless ``DELETE`` of an audited row followed by a bare ``INSERT`` of a NEW, unaudited row
        under the SAME id whose ORIGINAL covering AuditEntry SURVIVES reads as covered here,
        because that surviving entry's ``row_id`` still matches. That id-reuse is the
        KEYLESS-WRITER / SEC-11 domain-row-binding class (SEC-11 remains OPEN pending its external
        witness) — and for the order table it is already caught at the order path by the gateway's
        signed ``EVENT_ORDER_TRANSITION`` reconcile, which fails closed when the chain lacks the
        new row's signed history. Binding each row's covering entry ``seq`` into the row at write
        time (the general closure) is a cross-model schema change, out of scope for this thin gate.

        C2 (cross-table collision) fail-closed: the coverage set is SCOPED to this model's
        table via ``refs.model`` (stamped by ``record_state_change`` AND ``append_in``). An
        UNSCOPED coverage set let an unaudited row of one table pass because a covered row of a
        DIFFERENT table shared the same id — so a bare-added Order whose id collided with a
        covered ApprovalRecord read as covered. Requiring ``refs.model == model_class.__tablename__``
        binds coverage to the right table, so a cross-table id collision no longer masks the gap.

        R3-C2 (append_in batch coverage) fail-closed: the covered set is the UNION of the SINGULAR
        ``refs.row_id`` (stamped by ``record_state_change`` for a single state row) AND every id in
        the PLURAL ``refs.row_ids`` list (stamped by ``append_in`` for a BATCH — e.g. the
        ``EVENT_INGEST_BATCH`` that atomically persists a whole MarketEvent batch,
        ``workflows/events.py``). Reading only the singular ``row_id`` would leave every legitimately
        batch-audited row uncovered (cry wolf); reading only the plural would miss single-row audits.
        Both scoped by ``refs.model`` so the cross-table guarantee above still holds for either shape."""
        table = model_class.__tablename__
        with self._sf() as s:
            covered: set = set()
            for r in s.execute(select(AuditEntry)).scalars().all():
                refs = r.refs or {}
                if refs.get("model") != table:
                    continue
                rid = refs.get("row_id")
                if rid is not None:
                    covered.add(rid)                       # singular: record_state_change
                row_ids = refs.get("row_ids")
                if isinstance(row_ids, (list, tuple)):
                    covered.update(row_ids)                # plural: append_in batch (e.g. ingest)
            ids = {row.id for row in s.execute(select(model_class)).scalars().all()}
        return sorted(ids - covered)

    def completeness_report(self, model_classes) -> dict:
        """SEC-13 — aggregate the two-phase + state-coverage gaps in one snapshot for the
        startup reconciler (``orchestrator.reconcile``) and ops probes: any unresolved intent,
        any orphan result, and any load-bearing domain row lacking a covering AuditEntry. A
        THIN read-only roll-up over the existing primitives — it does NOT touch the chain/HMAC."""
        return {
            "unresolved_intents": self.unresolved_intents(),
            "orphan_results": self.orphan_results(),
            "uncovered": {m.__tablename__: self.verify_completeness(m) for m in model_classes},
        }

    # ---- verification ------------------------------------------------------
    def head(self) -> tuple[int, str | None]:
        with self._sf() as s:
            h = s.execute(select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)).scalar_one_or_none()
        return (h.seq, h.entry_hash) if h else (0, None)

    def verify_chain(self) -> VerifyResult:
        with self._sf() as s:
            rows = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
            wm = self._read_hwm(s)
        prev, expected = None, 1
        for r in rows:
            if r.seq != expected:
                return VerifyResult(False, r.seq, prev, f"seq gap/rollback: expected {expected}, got {r.seq}")
            if r.prev_hash != prev:
                return VerifyResult(False, r.seq, prev, f"prev_hash mismatch at seq {r.seq}")
            recomputed = self._hash(r.id, r.seq, r.prev_hash, r.event_type, r.actor, r.refs, r.payload)
            if not hmac.compare_digest(recomputed, r.entry_hash):
                return VerifyResult(False, r.seq, prev, f"hash mismatch (tamper) at seq {r.seq}")
            if (r.payload or {}).get("_ts") != r.created_at.isoformat():
                return VerifyResult(False, r.seq, prev, f"created_at diverges from signed _ts at seq {r.seq}")
            prev, expected = r.entry_hash, expected + 1
        head_seq = expected - 1
        if wm == -1:
            return VerifyResult(False, head_seq, prev, "watermark signature invalid (tamper)")
        # SEC-11: every append bumps the signed high-water, so a NON-EMPTY chain always has one.
        # rows-present + watermark-absent is itself the tail-truncation signature (a keyless
        # writer deletes the audit_meta rows to dodge the head_seq<hwm check below) -> fail CLOSED.
        if rows and wm is None:
            return VerifyResult(False, head_seq, prev,
                                "watermark missing on a non-empty chain (truncation/tamper)")
        # SEC-11 (W10): the high-water is MONOTONIC (never decreases across a delete), so a
        # verified head below it is a tail truncation — even one whose freed seqs were refilled
        # (the refill allocates ABOVE the high-water and shows as the seq gap caught above).
        if wm is not None and head_seq < wm:
            return VerifyResult(False, head_seq, prev, f"truncation: head seq {head_seq} < high-water {wm}")
        return VerifyResult(True, head_seq, prev)

    def verify_against_anchor(self, anchored_seq: int, anchored_hash: str) -> VerifyResult:
        """SEC-11: reject a chain whose head is BEHIND a specific external anchor point."""
        base = self.verify_chain()
        if not base.ok:
            return base
        if base.head_seq < anchored_seq:
            return VerifyResult(False, base.head_seq, base.head_hash,
                                f"rollback: head seq {base.head_seq} < anchored {anchored_seq}")
        with self._sf() as s:
            at = s.execute(select(AuditEntry).where(AuditEntry.seq == anchored_seq)).scalar_one_or_none()
        if at is None or not hmac.compare_digest(at.entry_hash, anchored_hash):
            return VerifyResult(False, base.head_seq, base.head_hash,
                                f"anchor hash mismatch at seq {anchored_seq}")
        return base
