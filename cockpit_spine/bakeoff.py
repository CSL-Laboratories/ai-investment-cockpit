"""Bakeoff plumbing for CP3, and an in-memory seal of an under-powered result.

The trial runner plans arms and drives them through the route consumer. It
returns a raw batch: no score, ranking or comparison. ``seal_result_record``
turns that batch into a CP3 result record only after it has read the CP7
preregistration protocol (WP8, CSL-518) from a local csl-research checkout.

The pin is the candidate CP7 revision 2 that Lucas cited on CSL-518
(commit ``cfefc77b1c43a941ae986dca877f02c251be42e1``,
``result.md`` SHA-256 ``fa3e486cf256f485b5cf73849757df825685b56633505d842f37bc6c5bbcf2d1``).
Samir has not accepted that revision, so the pin may change. This module does
not copy the protocol. It checks the pinned sha256 of the vault file and cites
the commit. The sha256 is the check; the commit says where a reader should look.
The loader never fetches. With no checkout it fails closed.

An under-powered run seals with verdict ``inconclusive``. Power is not
established while SESOI and the comparators are still ``PENDING_OWNER``, so
every batch this module can see is under-powered. A powered seal, any other
verdict, and a durable ledger write stay blocked: the non-default class wording
and the roll-up are ``NOT_DECIDED``, and the ledger location is ``PENDING_OWNER``.
Nothing here is called a verdict. The seal is not written to disk.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cockpit_spine.projections import (
    REPO_ROOT,
    VAULT_REPOSITORY,
    ProjectionError,
    read_cited_bytes,
)
from successor.route_consumer import DECISION_PROFILE, DecisionOutcome
from successor.route_consumer.profile import canonical_digest

# Candidate pin. Not Samir's acceptance. A later acceptance may replace it.
PROTOCOL_PIN: dict[str, str] = {
    "repository": VAULT_REPOSITORY,
    "commit": "cfefc77b1c43a941ae986dca877f02c251be42e1",
    "path": "submissions/20261026-csl518-cp7-prereg-power-contract-02/result.md",
    "sha256": "fa3e486cf256f485b5cf73849757df825685b56633505d842f37bc6c5bbcf2d1",
}
# Kept so a reader of the stub still finds the pin under the old name.
WP8_PROTOCOL: dict[str, str] = PROTOCOL_PIN

# Open items on CP7 rev 2 (CSL-518). They block a powered or persisted seal.
OPEN_GAPS: tuple[str, ...] = (
    "NOT_DECIDED: non-default verdict class wording",
    "NOT_DECIDED: category roll-up beyond its default",
    "PENDING_OWNER: SESOI",
    "PENDING_OWNER: comparators",
    "PENDING_OWNER: forward-window length and seal date",
    "PENDING_OWNER: ledger location",
)
_POWER_GAPS = ("PENDING_OWNER: SESOI", "PENDING_OWNER: comparators")
_PIN_KEYS = {"repository", "commit", "path", "sha256"}
_HEX = set("0123456789abcdef")
_GENESIS = "sha256:" + ("0" * 64)
_TRIAL_KEYS = {"arm", "trial", "disposition", "reason", "request_digest", "output_digest"}
_BATCH_KEYS = {"kind", "sealed", "protocol_ref", "workload_id", "mode", "plan", "trials"}
_PLAN_KEYS = {"engines", "trials"}

BATCH_KIND = "raw trial batch; plumbing only; not a result record; no verdict"
RECORD_KIND = "cp3 result record; provisional; inconclusive unless power is established"
BLOCKED_ON = (
    "CP7 rev 2 open items (non-default wording, roll-up, SESOI, comparators, "
    "forward window, ledger location); Samir has not accepted the pin"
)
ENGINES = ("claude", "codex")
WORKLOAD = "workload-d04"
MODE = "paper"
MAX_TRIALS = 20
VERDICT_INCONCLUSIVE = "inconclusive"
LABEL_PREFIX = "Provisional:"


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
        "acceptance": "not accepted by Samir; pin may change",
        "protocol": {
            "repository": PROTOCOL_PIN["repository"],
            "commit": PROTOCOL_PIN["commit"],
            "path": PROTOCOL_PIN["path"],
            "sha256": PROTOCOL_PIN["sha256"],
        },
        "open_gaps": list(OPEN_GAPS),
        "todo": (
            "TODO(WP8): Samir's acceptance may replace the pin. "
            "A powered or non-default verdict stays blocked, and the ledger is not written."
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
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path:
        raise BlockedOnProtocol("protocol pin path is not a vault-relative path")
    return {
        "repository": repository,
        "commit": commit,
        "path": path,
        "sha256": digest,
    }


def load_protocol_text(vault_root: Any = None, pin: dict[str, str] | None = None) -> str:
    """Read the pinned protocol from the vault checkout. Fail closed otherwise.

    ``vault_root`` wins over ``CSL_RESEARCH_CHECKOUT``. Neither one is fetched.
    The returned text is for the seal's class-name check; callers must not store it.
    """

    chosen = _check_pin(PROTOCOL_PIN if pin is None else pin)
    try:
        data = read_cited_bytes(
            chosen["repository"], chosen["path"], root=REPO_ROOT, vault_root=vault_root,
        )
    except ProjectionError as exc:
        raise BlockedOnProtocol(f"protocol unreadable: {exc}") from exc
    if hashlib.sha256(data).hexdigest() != chosen["sha256"]:
        raise BlockedOnProtocol("protocol bytes do not match the pinned sha256")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise BlockedOnProtocol("protocol is not utf-8") from None
    if VERDICT_INCONCLUSIVE not in text:
        raise BlockedOnProtocol("protocol does not name the inconclusive verdict class")
    return text


def power_established() -> bool:
    """False while the protocol's power inputs are still open.

    SESOI and the comparators are ``PENDING_OWNER`` on CP7 rev 2, so this
    module cannot show that a run was powered. It does not read the MDE tables.
    """

    return not any(gap in OPEN_GAPS for gap in _POWER_GAPS)


def verdict_for(*, power_established: bool) -> tuple[str, str]:
    """The only sealable class is ``inconclusive``, and only when power is absent."""

    if power_established:
        raise BlockedOnProtocol(
            "a powered run cannot be sealed: non-default verdict wording is NOT_DECIDED"
        )
    return VERDICT_INCONCLUSIVE, "under-powered"


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
    if (
        not isinstance(engines, list) or not engines
        or isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1
    ):
        raise ValueError("batch plan is malformed")
    expected = []
    for engine in engines:
        if not isinstance(engine, str) or not engine:
            raise ValueError("batch plan engine is not text")
        for number in range(1, repeats + 1):
            expected.append((engine, number))
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


def _ledger(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Hash-chain every trial, in order. Every trial counts, including a refusal."""

    previous = _GENESIS
    ledger = []
    for index, row in enumerate(rows, start=1):
        body = {"seq": index, "kind": "trial", "data": row, "prev": previous}
        body["hash"] = canonical_digest(body)
        ledger.append(body)
        previous = body["hash"]
    return ledger, previous


def seal_result_record(
    batch: dict[str, Any],
    vault_root: Any = None,
    pin: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Seal one raw batch in memory. The verdict of an under-powered run is inconclusive.

    ``vault_root`` is the ``--vault-root`` checkout. When it is omitted, the
    loader uses ``CSL_RESEARCH_CHECKOUT``. A missing checkout, a digest
    mismatch, or a protocol that does not name ``inconclusive`` raises
    ``BlockedOnProtocol`` and returns nothing. The record is not written.
    """

    chosen = _check_pin(PROTOCOL_PIN if pin is None else pin)
    # Touch the bytes before trusting the batch, so a missing vault fails closed
    # even when the batch itself is unusable.
    load_protocol_text(vault_root, chosen)
    checked = _check_batch(batch)
    verdict, reason = verdict_for(power_established=power_established())
    ledger, head = _ledger(checked["trials"])
    material: dict[str, Any] = {
        "kind": RECORD_KIND,
        "sealed": True,
        "label": LABEL_PREFIX,
        "verdict": verdict,
        "called_verdict": False,
        "reason": reason,
        "protocol_ref": chosen,
        "workload_id": WORKLOAD,
        "mode": MODE,
        "plan": checked["plan"],
        "trial_count": len(checked["trials"]),
        "ledger_head": head,
        "ledger": ledger,
        "open_gaps": list(OPEN_GAPS),
        "persisted": False,
    }
    material["result_sha256"] = canonical_digest(material)
    return material
