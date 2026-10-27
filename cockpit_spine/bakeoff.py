"""Bakeoff plumbing for CP3, and an in-memory seal that stays inconclusive.

The trial runner plans arms and drives them through the route consumer. It
returns a raw batch: no score, ranking or comparison. ``seal_result_record``
reads the candidate CP7 revision 2 protocol from a local csl-research checkout
and seals that batch in memory.

The pin is the candidate recorded for CSL-518 (commit
``cfefc77b1c43a941ae986dca877f02c251be42e1``, ``result.md`` SHA-256
``fa3e486cf256f485b5cf73849757df825685b56633505d842f37bc6c5bbcf2d1``).
Acceptance of that protocol takes Lucas's own exact-digest receipt. Samir
reviews. This module does not accept the protocol, and the pin may change.
It does not copy the protocol. It checks the pinned sha256 of the vault file
and the sibling ``record.json``. The loader never fetches. With no checkout
it fails closed.

The only ``verdict_class`` this module emits is ``inconclusive``. That is the
default: there is no registration, the role is not ``confirmatory_forward``,
and no estimand is measured. ``power_established`` is a hard-coded block. A
powered seal, any other class, and a durable write stay blocked. Ledger
entries use the CP7 section 6.5 fields, and the citation uses section 6.4.
The protocol's ledger location is still ``PENDING_OWNER``, so this chain is
the shape of one batch, not the program-wide registry, and it is not written.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from cockpit_spine.projections import (
    REPO_ROOT,
    VAULT_REPOSITORY,
    ProjectionError,
    read_cited_bytes,
)
from successor.route_consumer import DECISION_PROFILE, DecisionOutcome
from successor.route_consumer.profile import canonical_digest

# Candidate pin. Not an acceptance. A later receipt may replace it.
PROTOCOL_PIN: dict[str, str] = {
    "repository": VAULT_REPOSITORY,
    "commit": "cfefc77b1c43a941ae986dca877f02c251be42e1",
    "path": "submissions/20261026-csl518-cp7-prereg-power-contract-02/result.md",
    "sha256": "fa3e486cf256f485b5cf73849757df825685b56633505d842f37bc6c5bbcf2d1",
}

# Open items on CP7 rev 2 section 5. They are reported. They do not grant power.
OPEN_GAPS: tuple[str, ...] = (
    "PENDING_OWNER: Lucas exact-digest receipt",
    "proposed default, not decided: alpha 0.05 and power 0.80",
    "PENDING_OWNER: SESOI",
    "PENDING_DATA: sigma_a, rho, mbar, rho_c",
    "PENDING_OWNER: VC-CRY comparators and SPY+cash weights",
    "PENDING_REGISTRY: family size m",
    "PENDING_OWNER: forward-window length and seal date",
    "PENDING_OWNER: VC-EVT eligibility filter",
    "PENDING_OWNER: ledger location",
    "NOT_DECIDED: non-default verdict class wording",
    "NOT_DECIDED: category roll-up beyond its default",
    "NOT_DECIDED: verdict-on-Nov-11 wording",
)
_PIN_KEYS = {"repository", "commit", "path", "sha256"}
_HEX = set("0123456789abcdef")
_TRIAL_KEYS = {"arm", "trial", "disposition", "reason", "request_digest", "output_digest"}
_BATCH_KEYS = {"kind", "sealed", "protocol_ref", "workload_id", "mode", "plan", "trials"}
_PLAN_KEYS = {"engines", "trials"}
_ENTRY_KEYS = {
    "schema_version", "seq", "entry_id", "entry_type", "recorded_at_utc",
    "prev_entry_sha256", "entry_sha256", "contract_ref", "trial_id", "role",
    "venue_class", "venue_cell", "status", "authored_by",
}
# Section 6.5 entry_type enum includes observation_seal. These paper trials are
# not confirmatory_forward. pre_contract is the role Samir named for them.
_ENTRY_TYPE = "observation_seal"
_ROLE = "pre_contract"
_VENUE_CLASS = "VC-EQ"
_STATUS = "recorded"
_AUTHORED_BY = "cockpit_spine.bakeoff"
_SCHEMA_VERSION = 1
_CONTRACT_STATUS = "draft_candidate_not_accepted"
_WORDING_STATUS = "provisional, inconclusive by default"
_PUBLICATION_LABEL = "provisional"
_DISPLAY = "Provisional: inconclusive by default"

BATCH_KIND = "raw trial batch; plumbing only; not a result record; no verdict"
RECORD_KIND = (
    "cp3 plumbing seal; provisional; not a CP7 section 7 result record; "
    "inconclusive by default"
)
BLOCKED_ON = (
    "CP7 rev 2 open items (wording, roll-up, SESOI, comparators, window, "
    "ledger location); Lucas exact-digest receipt absent; Samir reviews"
)
ENGINES = ("claude", "codex")
WORKLOAD = "workload-d04"
MODE = "paper"
MAX_TRIALS = 20
VERDICT_INCONCLUSIVE = "inconclusive"
_VERDICT_TOKEN = '"' + VERDICT_INCONCLUSIVE + '"'
# Quoted JSON strings the pinned schema must contain before these constants are used.
_LEDGER_TOKENS = ('"' + _ENTRY_TYPE + '"', '"' + _ROLE + '"', '"' + _VENUE_CLASS + '"')
_REASON = (
    "by default: no registration, no confirmatory_forward role, no estimand measured"
)


class BlockedOnProtocol(Exception):
    """The seal cannot proceed: the protocol is missing, or the seal is out of scope."""


class Consumer(Protocol):
    def decide(
        self, *, request_id: str, profile: Any, mode: str, prompt: str,
        output_schema: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
        """The WP12 consumer's one entry point."""


@dataclass(frozen=True)
class Plan:
    """Arms are engines; trials are repeats of one fixed synthetic request."""

    engines: tuple[str, ...] = ENGINES
    trials: int = 2
    prompt: str = "synthetic prompt"

    def check(self) -> None:
        if not self.engines or not set(self.engines) <= set(ENGINES):
            raise ValueError(f"engines must be a non-empty subset of {ENGINES}")
        if len(set(self.engines)) != len(self.engines):
            raise ValueError("an engine may appear once")
        if not isinstance(self.trials, int) or isinstance(self.trials, bool):
            raise ValueError("trials must be an integer")
        if not 1 <= self.trials <= MAX_TRIALS:
            raise ValueError(f"trials must be 1 to {MAX_TRIALS}")


def run_trials(plan: Plan, consumer_for: Callable[[str], Consumer]) -> dict[str, Any]:
    """Drive every planned trial through the consumer and return the raw batch.

    The batch lists what the route said, trial by trial. It holds no score,
    ranking, return or comparison, and it is explicitly unsealed.
    """

    plan.check()
    trials = []
    for engine in plan.engines:
        consumer = consumer_for(engine)
        for number in range(1, plan.trials + 1):
            outcome = consumer.decide(
                request_id=f"bk-{engine}-{number}", profile=DECISION_PROFILE[WORKLOAD],
                mode=MODE, prompt=plan.prompt,
            )
            reply = outcome.reply_record
            trials.append({
                "arm": engine,
                "trial": number,
                "disposition": outcome.disposition,
                "reason": outcome.reason,
                "request_digest": (
                    canonical_digest(outcome.request_record) if outcome.request_record else None
                ),
                "output_digest": (
                    reply["output_digest"]
                    if isinstance(reply, dict) and "output_digest" in reply else None
                ),
            })
    return {
        "kind": BATCH_KIND,
        "sealed": False,
        "protocol_ref": None,
        "workload_id": WORKLOAD,
        "mode": MODE,
        "plan": {"engines": list(plan.engines), "trials": plan.trials},
        "trials": trials,
    }


def record_step_status() -> dict[str, Any]:
    """What the record step can do, and what it still will not do."""

    return {
        "step": "result record",
        "state": "candidate pin; in-memory inconclusive seal; durable write blocked",
        "blocked_on": BLOCKED_ON,
        "acceptance": (
            "draft_candidate_not_accepted: Samir reviews; "
            "Lucas exact-digest receipt absent"
        ),
        "protocol": {
            "repository": PROTOCOL_PIN["repository"],
            "commit": PROTOCOL_PIN["commit"],
            "path": PROTOCOL_PIN["path"],
            "sha256": PROTOCOL_PIN["sha256"],
        },
        "open_gaps": list(OPEN_GAPS),
        "todo": (
            "TODO(WP8): a later receipt may replace the pin. "
            "A powered or non-default class stays blocked, and the ledger is not written."
        ),
        "result_records_written": 0,
    }


def _is_hex(value: Any, size: int) -> bool:
    if not isinstance(value, str) or len(value) != size:
        return False
    for character in value:
        if character not in _HEX:
            return False
    return True


def _pin_parts(path: str) -> list[str]:
    if not isinstance(path, str) or not path or path.startswith("/") or path.startswith("\\"):
        raise BlockedOnProtocol("protocol pin path is not a vault-relative path")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise BlockedOnProtocol("protocol pin path is not a vault-relative path")
    if parts[-1] != "result.md" or len(parts) < 2:
        raise BlockedOnProtocol("protocol path is not a submission result.md")
    return parts


def _check_pin(pin: Any) -> dict[str, str]:
    if not isinstance(pin, dict) or set(pin) != _PIN_KEYS:
        raise BlockedOnProtocol("protocol pin is missing a field or has an extra one")
    repository, commit, path, digest = (
        pin["repository"], pin["commit"], pin["path"], pin["sha256"],
    )
    if repository != VAULT_REPOSITORY:
        raise BlockedOnProtocol("protocol pin is not a csl-research citation")
    if not _is_hex(commit, 40) or not _is_hex(digest, 64):
        raise BlockedOnProtocol("protocol pin commit or sha256 is malformed")
    _pin_parts(path)
    return {
        "repository": repository,
        "commit": commit,
        "path": path,
        "sha256": digest,
    }


def _read_vault(relative: str, vault_root: Any) -> bytes:
    try:
        return read_cited_bytes(
            VAULT_REPOSITORY, relative, root=REPO_ROOT, vault_root=vault_root,
        )
    except ProjectionError as exc:
        raise BlockedOnProtocol(f"protocol unreadable: {exc}") from exc


def _check_record(data: bytes, record_id: str, digest: str) -> None:
    try:
        meta = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise BlockedOnProtocol("protocol record.json is not utf-8 json") from None
    if not isinstance(meta, dict):
        raise BlockedOnProtocol("protocol record.json is not an object")
    result = meta.get("result")
    sha_ok = isinstance(result, dict) and result.get("sha256") == digest
    if meta.get("record_id") != record_id or not sha_ok:
        raise BlockedOnProtocol("protocol record.json does not describe the pinned result.md")
    if meta.get("contract_status") != _CONTRACT_STATUS:
        raise BlockedOnProtocol(
            "protocol record.json is not the unaccepted candidate"
        )


def _load_protocol(vault_root: Any) -> tuple[str, dict[str, str]]:
    """Read and check the committed protocol once. Return the text and the pin."""

    chosen = _check_pin(PROTOCOL_PIN)
    parts = _pin_parts(chosen["path"])
    data = _read_vault(chosen["path"], vault_root)
    if hashlib.sha256(data).hexdigest() != chosen["sha256"]:
        raise BlockedOnProtocol("protocol bytes do not match the pinned sha256")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise BlockedOnProtocol("protocol is not utf-8") from None
    if _VERDICT_TOKEN not in text:
        raise BlockedOnProtocol("protocol does not name the inconclusive verdict class")
    for token in _LEDGER_TOKENS:
        if token not in text:
            raise BlockedOnProtocol(f"protocol does not name the ledger value {token}")
    record_path = "/".join(parts[:-1] + ["record.json"])
    _check_record(_read_vault(record_path, vault_root), parts[-2], chosen["sha256"])
    return text, chosen


def load_protocol_text(vault_root: Any = None) -> str:
    """Read the committed protocol from the vault checkout. Fail closed otherwise.

    ``vault_root`` wins over ``CSL_RESEARCH_CHECKOUT``. Neither one is fetched.
    The pin is ``PROTOCOL_PIN``. Callers cannot substitute another pin. The
    returned text is checked for the ``verdict_class`` token; callers must not
    store it.
    """

    text, _chosen = _load_protocol(vault_root)
    return text


def power_established() -> bool:
    """Hard-coded block. This does not read the protocol or the MDE tables.

    CP7 rev 2 still has SESOI ``PENDING_OWNER`` and no estimand is computed,
    so a powered seal stays unreachable. Deleting a string from ``OPEN_GAPS``
    does not flip this.
    """

    return False


def verdict_for(*, power_established: bool) -> tuple[str, str]:
    """The only sealable class is ``inconclusive``, and only when power is absent."""

    if power_established:
        raise BlockedOnProtocol(
            "a powered run cannot be sealed: non-default class wording is NOT_DECIDED"
        )
    return VERDICT_INCONCLUSIVE, _REASON


def _trial_row(trial: Any) -> dict[str, Any]:
    if not isinstance(trial, dict) or set(trial) != _TRIAL_KEYS:
        raise ValueError("a trial is missing a field or has an extra one")
    number = trial["trial"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("trial number is not a positive integer")
    for key in ("arm", "disposition", "reason"):
        if not isinstance(trial[key], str) or not trial[key]:
            raise ValueError(f"trial {key} is not text")
    for key in ("request_digest", "output_digest"):
        value = trial[key]
        if value is not None and not isinstance(value, str):
            raise ValueError(f"trial {key} is not text or null")
    return {
        "arm": trial["arm"],
        "trial": number,
        "disposition": trial["disposition"],
        "reason": trial["reason"],
        "request_digest": trial["request_digest"],
        "output_digest": trial["output_digest"],
    }


def _check_batch(batch: Any) -> dict[str, Any]:
    if not isinstance(batch, dict) or set(batch) != _BATCH_KEYS:
        raise ValueError("batch is not a raw trial batch")
    unsealed = batch["sealed"] is False and batch["protocol_ref"] is None
    if batch["kind"] != BATCH_KIND or not unsealed:
        raise ValueError("batch is not an unsealed raw trial batch")
    if batch["workload_id"] != WORKLOAD or batch["mode"] != MODE:
        raise ValueError("batch workload or mode is not the bakeoff's")
    plan = batch["plan"]
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
        raise ValueError("batch plan is malformed")
    engines, repeats = plan["engines"], plan["trials"]
    if not isinstance(engines, list) or not engines:
        raise ValueError("batch plan is malformed")
    try:
        Plan(engines=tuple(engines), trials=repeats).check()
    except TypeError:
        raise ValueError("batch plan is malformed") from None
    expected = [(engine, number) for engine in engines for number in range(1, repeats + 1)]
    trials = batch["trials"]
    if not isinstance(trials, list) or len(trials) != len(expected):
        raise ValueError("batch trial count does not match its plan")
    rows = []
    for index in range(len(expected)):
        row = _trial_row(trials[index])
        engine, number = expected[index]
        if row["arm"] != engine or row["trial"] != number:
            raise ValueError("batch trials do not follow the plan")
        rows.append(row)
    return {"plan": {"engines": list(engines), "trials": repeats}, "trials": rows}


def _bare_sha256(body: dict[str, Any]) -> str:
    """SHA-256 of UTF-8 JSON, keys sorted, no insignificant whitespace. Bare hex."""

    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _recorded_at_utc() -> str:
    """UTC timestamp for one seal. Tests may patch this. The seal has no clock of its own."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger_entries(
    rows: list[dict[str, Any]], contract_ref: str,
) -> list[dict[str, Any]]:
    """One section 6.5 entry per trial. ``seq`` starts at 0. Hashes are bare hex.

    ``entry_sha256`` is the digest of the entry with that field removed.
    ``prev_entry_sha256`` is null only on ``seq`` 0. This chain is not written,
    and it is not the program-wide registry.
    """

    recorded = _recorded_at_utc()
    previous: str | None = None
    entries: list[dict[str, Any]] = []
    for seq, row in enumerate(rows):
        trial_id = f"bk-{row['arm']}-{row['trial']}"
        body: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "seq": seq,
            "entry_id": f"entry-{trial_id}",
            "entry_type": _ENTRY_TYPE,
            "recorded_at_utc": recorded,
            "prev_entry_sha256": previous,
            "contract_ref": contract_ref,
            "trial_id": trial_id,
            "role": _ROLE,
            "venue_class": _VENUE_CLASS,
            "venue_cell": WORKLOAD,
            "status": _STATUS,
            "authored_by": _AUTHORED_BY,
        }
        if set(body) != _ENTRY_KEYS - {"entry_sha256"}:
            raise BlockedOnProtocol("ledger entry does not match section 6.5")
        body["entry_sha256"] = _bare_sha256(body)
        entries.append(body)
        previous = body["entry_sha256"]
    return entries


def seal_result_record(batch: dict[str, Any], vault_root: Any = None) -> dict[str, Any]:
    """Seal one raw batch in memory. The class of this run is inconclusive.

    ``vault_root`` is the checkout. When it is omitted, the loader uses
    ``CSL_RESEARCH_CHECKOUT``. The protocol pin is the committed ``PROTOCOL_PIN``;
    there is no pin argument. A missing checkout, a digest mismatch, or a
    protocol that does not name the inconclusive class raises
    ``BlockedOnProtocol`` and returns nothing. The record is not written.
    """

    # Touch the bytes before trusting the batch, so a missing vault fails closed
    # even when the batch itself is unusable.
    _text, chosen = _load_protocol(vault_root)
    checked = _check_batch(batch)
    verdict_class, reason = verdict_for(power_established=power_established())
    if verdict_class != VERDICT_INCONCLUSIVE:
        raise BlockedOnProtocol("only the inconclusive class can be sealed")
    entries = _ledger_entries(checked["trials"], chosen["sha256"])
    distinct: list[str] = []
    for entry in entries:
        if entry["trial_id"] not in distinct:
            distinct.append(entry["trial_id"])
    material: dict[str, Any] = {
        "kind": RECORD_KIND,
        "sealed": True,
        "publication_label": _PUBLICATION_LABEL,
        "display": _DISPLAY,
        "verdict_class": verdict_class,
        "reason": reason,
        "preregistration_contract": {
            "repository": chosen["repository"],
            "commit": chosen["commit"],
            "path": chosen["path"],
            "result_sha256": chosen["sha256"],
            "wording_status": _WORDING_STATUS,
        },
        "trial_ledger": {
            "ledger_head_sha256": entries[-1]["entry_sha256"],
            "first_seq": entries[0]["seq"],
            "last_seq": entries[-1]["seq"],
            "n_trials_declared": len(distinct),
        },
        "entries": entries,
        "workload_id": WORKLOAD,
        "mode": MODE,
        "plan": checked["plan"],
        "trials": checked["trials"],
        "open_gaps": list(OPEN_GAPS),
        "persisted": False,
    }
    material["seal_digest"] = _bare_sha256(material)
    return material
