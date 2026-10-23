"""Paper and dry-run authority profiles (CSL-218).

Profile A is proposal-only and is the qualification reference. Profile B is
model-assisted and may reserve a paper or dry-run action. Profile C is a
named future target and has no runtime in this module.

The loader admits only the environments ``paper`` and ``dry_run``. A live,
unknown, or widened environment is refused. A loaded profile is not broker
authority: ``submit_paper`` is handed to the existing ``OrderGateway.submit``
path, which still applies risk pass, mode, approval and the single-use
capability. Provider output, including a confidence value, is never read as
a grant.

Cumulative notional and order-count consumption is stored per account, venue
and environment. A second active profile cannot open a fresh cap for that
perimeter. Kill and hold are rows in the same store, so they survive a new
process that reopens the file. This module does not set or clear the global
SEC-04 HALT sentinel: releasing a profile stop must not clear an operator kill.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Mapping

from core.clock import Clock

SCHEMA_VERSION = 1
# Profile documents stay at schema 1. The sqlite user_version is separate so a
# column migration does not require a document bump. Version 1 is the order_ref
# store. Version 2 adds the durable handoff state. Version 3 adds exclusive
# handoff claim ownership (claim_generation) and the failed-handoff reconcile
# flag. Version 4 adds the claim lease (claimed_at): an abandoned in_progress
# claim can be re-owned only after its lease expires, so two reconcilers
# racing the same crashed claim cannot both bump the generation and both reach
# the gateway. Version 5 adds the process transport hash and the restart
# binding hash. Version 6 records the protective limit sent for a market
# order. A version-4 or version-5 store already has the stop capability and
# the claim lease, so its rows migrate in place. Earlier versions that hold
# rows still refuse.
STORE_USER_VERSION = 6
ADMITTED_STORE_VERSIONS = frozenset({0, 1, 2, 3, 4, 5, STORE_USER_VERSION})
ADMITTED_ENVIRONMENTS = frozenset({"paper", "dry_run"})
_MODEL_OUTPUT_KEYS = frozenset({"provider", "model_version", "summary", "confidence"})
_HANDOFF_STATES = frozenset({"pending", "in_progress", "failed", "completed"})
RUNTIME_PROFILE_CLASSES = frozenset({"A", "B"})
FUTURE_PROFILE_CLASS = "C"
MAX_PROFILE_LIFETIME = timedelta(days=7)
# How long an in_progress claim is presumed to still be a live attempt. A
# reconcile against an abandoned claim younger than this is refused outright:
# the original owner may still be mid-flight. Only once the lease has expired
# AND the chain shows no submission may a new generation be minted.
CLAIM_LEASE = timedelta(seconds=90)
# A version-4 in-flight row has no credential digest. This marker is not a secret
# and does not match any client until an authenticated startup transport binds one.
_MIGRATED_UNBOUND = "migrated-unauthenticated"
_MONEY = Decimal("0.01")
# Market exposure is the touch inflated by this fraction. Buys use the ask and
# sells use the bid, so a cap cannot be understated by order.notional.
MARKET_REFERENCE_BUFFER = Decimal("0.05")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_MONEY_TEXT = re.compile(r"^[0-9]{1,15}(?:\.[0-9]{1,2})?$")

# These names are refused examples, not switches. Admission is the set above.
NAMED_REFUSED_ENVIRONMENTS = frozenset({
    "live",
    "limited_live",
    "production",
    "shadow",
    "demo",
    "review_live",
})

# environment, venue, adapter, venue_family, account_kind, jurisdiction
ADMITTED_BINDINGS = frozenset({
    ("paper", "alpaca", "alpaca_paper", "us_equity", "individual_personal", "US"),
    ("dry_run", "dry_run", "dry_run", "us_equity", "individual_personal", "US"),
})

_ACTIONS = {
    ("A", "paper"): frozenset({"propose"}),
    ("A", "dry_run"): frozenset({"propose"}),
    ("B", "paper"): frozenset({"propose", "submit_paper"}),
    ("B", "dry_run"): frozenset({"propose", "submit_dry_run"}),
}

_REQUIRED = (
    "schema_version",
    "profile_id",
    "profile_class",
    "account_id",
    "account_kind",
    "jurisdiction",
    "venue",
    "venue_family",
    "adapter",
    "environment",
    "provider",
    "model_version",
    "allowed_actions",
    "expires_at",
    "notional_cap",
    "order_count_cap",
    "max_order_notional",
    "stop_owner",
    "recovery_owner",
    "evidence_refs",
)


class ProfileRefused(Exception):
    """Fail-closed refusal. ``code`` is the stable negative-control name."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AuthorityProfile:
    schema_version: int
    profile_id: str
    profile_class: str
    account_id: str
    account_kind: str
    jurisdiction: str
    venue: str
    venue_family: str
    adapter: str
    environment: str
    provider: str
    model_version: str
    allowed_actions: frozenset[str]
    expires_at: datetime
    notional_cap: Decimal
    order_count_cap: int
    max_order_notional: Decimal
    stop_owner: str
    recovery_owner: str
    evidence_refs: tuple[str, ...]

    def binding(self) -> tuple[str, str, str]:
        return (self.account_id, self.venue, self.environment)

    def canonical(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_class": self.profile_class,
            "account_id": self.account_id,
            "account_kind": self.account_kind,
            "jurisdiction": self.jurisdiction,
            "venue": self.venue,
            "venue_family": self.venue_family,
            "adapter": self.adapter,
            "environment": self.environment,
            "provider": self.provider,
            "model_version": self.model_version,
            "allowed_actions": sorted(self.allowed_actions),
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat(),
            "notional_cap": format(self.notional_cap, "f"),
            "order_count_cap": self.order_count_cap,
            "max_order_notional": format(self.max_order_notional, "f"),
            "stop_owner": self.stop_owner,
            "recovery_owner": self.recovery_owner,
            "evidence_refs": list(self.evidence_refs),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Reservation:
    client_order_id: str
    profile_id: str
    action: str
    notional: Decimal
    replayed: bool
    handoff_done: bool


@dataclass(frozen=True)
class ProfiledHandoff:
    """Result of a profile check followed, only for paper submit, by the gateway."""

    client_order_id: str
    reached_gateway: bool
    replayed: bool
    outcome: object | None = None


@dataclass(frozen=True)
class StopCapability:
    """Capability minted at install. The stop-owner string is not this token."""

    profile_id: str
    token: str


@dataclass(frozen=True)
class ClaimTicket:
    """Exclusive, durable ownership of one handoff claim (a fencing token).

    ``generation`` is stored on the reservation row and strictly increases per
    claim. ``complete_handoff`` and ``fail_handoff`` write only when the row's
    stored generation still equals the ticket's, so a paused or stale owner
    cannot overwrite a newer claim.
    """

    client_order_id: str
    generation: int
    state: str
    reconciled: int = 0


@dataclass(frozen=True)
class TransportPermit:
    """One-time proof that the final fence passed under the store lock.

    Only ``AuthorityProfileStore`` mints these, after it has re-checked the
    profile and the caller's claim generation. A caller-built object is not a
    permit: the nonce has to be one this process just issued. Consumption
    repeats that check inside the store transaction and requires the order id
    the reservation was bound to.

    ``remaining_s`` is set when the renewed lease has less than the admission
    budget left, so the gateway does not wait longer than the lease.
    """

    store: object
    nonce: str
    remaining_s: float | None = None


def model_output_grants_authority(model_output: object) -> bool:
    """Provider output is evidence. It is never a credential or a grant."""
    del model_output
    return False


def _validate_model_output(model_output: object) -> None:
    """Accept an empty output or a closed evidence object. Never read it as a grant.

    Unknown keys are the grant-shaped fields a caller could use to smuggle
    authority, so they fail closed before a profile is loaded or a budget is
    reserved.
    """
    if model_output is None:
        return
    if not isinstance(model_output, dict):
        raise ProfileRefused("model_output", "model output must be an object")
    if set(model_output) - _MODEL_OUTPUT_KEYS:
        raise ProfileRefused("model_output", "model output contains an unknown field")
    for key in ("provider", "model_version", "summary"):
        if key in model_output and type(model_output[key]) is not str:
            raise ProfileRefused("model_output", "model output field is malformed")
    if "confidence" in model_output:
        value = model_output["confidence"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ProfileRefused("model_output", "model output confidence is malformed")


def load_profile(document: Mapping, *, now: datetime) -> AuthorityProfile:
    """Validate one profile document. Expired, widened, or unknown input fails closed."""
    if not isinstance(document, Mapping):
        raise ProfileRefused("profile_document", "profile document must be an object")
    unknown = set(document) - set(_REQUIRED)
    if unknown:
        raise ProfileRefused("unknown_field", "profile document contains an unknown field")
    missing = [key for key in _REQUIRED if key not in document]
    if missing:
        raise ProfileRefused("missing_field", "profile document is missing a required field")
    _require_now(now)

    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise ProfileRefused("schema_version", "unsupported profile schema version")

    profile_class = document["profile_class"]
    if type(profile_class) is not str:
        raise ProfileRefused("profile_class", "profile class is not admitted")
    if profile_class == FUTURE_PROFILE_CLASS:
        raise ProfileRefused("profile_class", "profile C has no runtime")
    if profile_class not in RUNTIME_PROFILE_CLASSES:
        raise ProfileRefused("profile_class", "profile class is not admitted")

    environment = document["environment"]
    if type(environment) is not str or environment not in ADMITTED_ENVIRONMENTS:
        raise ProfileRefused("environment", "environment is not admitted")

    if any(
        type(document[key]) is not str
        for key in ("venue", "adapter", "venue_family", "account_kind", "jurisdiction")
    ):
        raise ProfileRefused("perimeter", "account or venue perimeter is not admitted")

    binding = (
        environment,
        document["venue"],
        document["adapter"],
        document["venue_family"],
        document["account_kind"],
        document["jurisdiction"],
    )
    if binding not in ADMITTED_BINDINGS:
        raise ProfileRefused("perimeter", "account or venue perimeter is not admitted")

    allowed = _ACTIONS[(profile_class, environment)]
    actions = document["allowed_actions"]
    if (
        not isinstance(actions, list)
        or not actions
        or any(type(item) is not str for item in actions)
        or len(actions) != len(set(actions))
        or not set(actions) <= allowed
    ):
        raise ProfileRefused("allowed_actions", "allowed actions exceed the profile class")

    notional_cap = _parse_money(document["notional_cap"], code="notional_cap")
    max_order = _parse_money(document["max_order_notional"], code="max_order_notional")
    if max_order > notional_cap:
        raise ProfileRefused("notional_cap", "single-order cap exceeds the cumulative cap")
    count_cap = document["order_count_cap"]
    if type(count_cap) is not int or not 1 <= count_cap <= 10000:
        raise ProfileRefused("order_count_cap", "order count cap is not admitted")

    expires_at = _parse_expiry(document["expires_at"])
    if expires_at <= now:
        raise ProfileRefused("expired", "profile is expired")
    if expires_at > now + MAX_PROFILE_LIFETIME:
        raise ProfileRefused("expiry_too_long", "profile expiry exceeds the admitted lifetime")

    evidence = document["evidence_refs"]
    if (
        not isinstance(evidence, list)
        or not evidence
        or len(evidence) > 8
        or any(type(item) is not str or not _ID.fullmatch(item) for item in evidence)
    ):
        raise ProfileRefused("evidence", "evidence reference is missing or malformed")

    return AuthorityProfile(
        schema_version=SCHEMA_VERSION,
        profile_id=_ident(document["profile_id"], code="profile_id"),
        profile_class=profile_class,
        account_id=_ident(document["account_id"], code="account_id"),
        account_kind=document["account_kind"],
        jurisdiction=document["jurisdiction"],
        venue=document["venue"],
        venue_family=document["venue_family"],
        adapter=document["adapter"],
        environment=environment,
        provider=_ident(document["provider"], code="provider"),
        model_version=_ident(document["model_version"], code="model_version"),
        allowed_actions=frozenset(actions),
        expires_at=expires_at,
        notional_cap=notional_cap,
        order_count_cap=count_cap,
        max_order_notional=max_order,
        stop_owner=_ident(document["stop_owner"], code="stop_owner"),
        recovery_owner=_ident(document["recovery_owner"], code="recovery_owner"),
        evidence_refs=tuple(evidence),
    )


def load_profile_file(path: Path | str) -> dict:
    """Read a JSON profile document. YAML and unreadable files fail closed."""
    file_path = Path(path)
    if file_path.suffix != ".json":
        raise ProfileRefused("profile_file", "profile file must be a JSON document")
    try:
        raw = file_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileRefused("profile_file", "profile file is unreadable") from exc
    if not isinstance(parsed, dict):
        raise ProfileRefused("profile_document", "profile document must be an object")
    return parsed


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS profiles (
        profile_id TEXT PRIMARY KEY,
        canonical TEXT NOT NULL,
        account_id TEXT NOT NULL,
        venue TEXT NOT NULL,
        environment TEXT NOT NULL,
        stop_owner TEXT NOT NULL,
        recovery_owner TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        installed_by TEXT NOT NULL,
        installed_at TEXT NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0,
        revoked_by TEXT,
        revoked_at TEXT,
        revoke_reason TEXT,
        stop_capability_hash TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS controls (
        account_id TEXT NOT NULL,
        venue TEXT NOT NULL,
        environment TEXT NOT NULL,
        kill INTEGER NOT NULL DEFAULT 0,
        hold INTEGER NOT NULL DEFAULT 0,
        kill_actor TEXT,
        hold_actor TEXT,
        kill_reason TEXT,
        hold_reason TEXT,
        updated_at TEXT,
        PRIMARY KEY (account_id, venue, environment)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS consumption (
        account_id TEXT NOT NULL,
        venue TEXT NOT NULL,
        environment TEXT NOT NULL,
        reserved_notional TEXT NOT NULL,
        reserved_count INTEGER NOT NULL,
        ceiling_notional TEXT NOT NULL,
        ceiling_count INTEGER NOT NULL,
        ceiling_order TEXT NOT NULL,
        PRIMARY KEY (account_id, venue, environment)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reservations (
        client_order_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        venue TEXT NOT NULL,
        environment TEXT NOT NULL,
        action TEXT NOT NULL,
        notional TEXT NOT NULL,
        created_at TEXT NOT NULL,
        handoff_done INTEGER NOT NULL DEFAULT 0,
        handoff_state TEXT NOT NULL DEFAULT 'pending',
        order_ref TEXT,
        claim_generation INTEGER NOT NULL DEFAULT 0,
        reconciled INTEGER NOT NULL DEFAULT 0,
        claimed_at TEXT,
        transport_client_hash TEXT,
        transport_binding_hash TEXT,
        protective_limit TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS clock_mark (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        stamp TEXT NOT NULL
    )
    """,
)


class AuthorityProfileStore:
    """Durable profile, consumption, idempotency and stop ledger."""

    def __init__(self, path: Path | str, *, clock=None) -> None:
        file_path = Path(path)
        if not file_path.is_absolute() or file_path.suffix != ".sqlite":
            raise ProfileRefused(
                "store_path", "profile store path must be an absolute sqlite file"
            )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = file_path
        self._lock = threading.Lock()
        # Authority time is this clock, read inside ``_txn`` after both locks.
        # A ``now`` argument on a public method is not that clock.
        self._clock_default = clock is None
        self._clock = clock if clock is not None else Clock()
        self._clock_high_water: datetime | None = None
        self._clock_mark_dirty = False
        self._inflight: dict[str, str] = {}
        self._authority_generation = 0
        self._authoritative_now: datetime | None = None
        self._live_permits: dict[str, tuple[str, int, str]] = {}
        # order id -> client that consumed the permit. A different client cannot
        # prove that cockpit order absent.
        self._admitted_clients: dict[str, object] = {}
        try:
            self._conn = sqlite3.connect(
                str(file_path), check_same_thread=False, isolation_level=None, timeout=30
            )
        except sqlite3.Error as exc:
            raise ProfileRefused("store", "profile store is unreadable") from exc
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        self._load_clock_mark()

    def install(
        self, document: Mapping, *, actor: str, now: datetime, capability: object = None
    ) -> tuple[AuthorityProfile, StopCapability]:
        _require_now(now)
        with self._txn() as conn:
            now = self._authoritative_now
            profile = load_profile(document, now=now)
            self._require_actor(actor, expected=profile.stop_owner)
            existing = conn.execute(
                "SELECT canonical, revoked FROM profiles WHERE profile_id = ?",
                (profile.profile_id,),
            ).fetchone()
            if existing is not None:
                if existing["canonical"] != profile.canonical():
                    raise ProfileRefused("profile_changed", "installed profile body cannot change")
                if existing["revoked"]:
                    raise ProfileRefused("revoked", "profile is revoked")
                self._require_capability(conn, profile.profile_id, capability)
                return profile, _as_capability(profile.profile_id, capability)
            # A perimeter with no profile history has no controls row yet; that is
            # a new perimeter, not corruption. A missing row for a perimeter that
            # already has profiles stays fail-closed in _refuse_if_blocked.
            has_history = conn.execute(
                """
                SELECT 1 FROM profiles
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                profile.binding(),
            ).fetchone()
            if has_history is None:
                conn.execute(
                    """
                    INSERT INTO controls (
                        account_id, venue, environment, kill, hold
                    ) VALUES (?, ?, ?, 0, 0)
                    """,
                    profile.binding(),
                )
                conn.execute(
                    """
                    INSERT INTO consumption (
                        account_id, venue, environment, reserved_notional, reserved_count,
                        ceiling_notional, ceiling_count, ceiling_order
                    ) VALUES (?, ?, ?, '0.00', 0, ?, ?, ?)
                    """,
                    (
                        *profile.binding(),
                        format(profile.notional_cap, "f"),
                        profile.order_count_cap,
                        format(profile.max_order_notional, "f"),
                    ),
                )
            else:
                # A historical perimeter whose ledgers were lost in a damaged
                # restore must not be reseeded at zero. That would let the next
                # profile spend the cap again.
                control = conn.execute(
                    """
                    SELECT 1 FROM controls
                    WHERE account_id = ? AND venue = ? AND environment = ?
                    """,
                    profile.binding(),
                ).fetchone()
                if control is None:
                    raise ProfileRefused("store", "stop ledger is missing")
                consumption = conn.execute(
                    """
                    SELECT 1 FROM consumption
                    WHERE account_id = ? AND venue = ? AND environment = ?
                    """,
                    profile.binding(),
                ).fetchone()
                if consumption is None:
                    raise ProfileRefused("store", "consumption ledger is missing")
            self._refuse_if_blocked(conn, profile)
            occupied = conn.execute(
                """
                SELECT profile_id FROM profiles
                WHERE account_id = ? AND venue = ? AND environment = ? AND revoked = 0
                  AND expires_at > ?
                """,
                (*profile.binding(), now.astimezone(timezone.utc).isoformat()),
            ).fetchone()
            if occupied is not None:
                raise ProfileRefused(
                    "perimeter_occupied", "an active profile already binds this perimeter"
                )
            token = secrets.token_urlsafe(32)
            conn.execute(
                """
                INSERT INTO profiles (
                    profile_id, canonical, account_id, venue, environment, stop_owner,
                    recovery_owner, expires_at, installed_by, installed_at, revoked,
                    stop_capability_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    profile.profile_id,
                    profile.canonical(),
                    profile.account_id,
                    profile.venue,
                    profile.environment,
                    profile.stop_owner,
                    profile.recovery_owner,
                    profile.expires_at.isoformat(),
                    actor,
                    now.astimezone(timezone.utc).isoformat(),
                    _capability_hash(token),
                ),
            )
            self._tighten_ceiling(conn, profile)
        return profile, StopCapability(profile.profile_id, token)

    def revoke(
        self, profile_id: str, *, actor: str, now: datetime, reason: str, capability: object = None
    ) -> None:
        _require_now(now)
        _reason(reason)
        with self._txn() as conn:
            profile = self._profile_row(conn, profile_id)
            self._require_actor(actor, expected=profile.stop_owner)
            self._require_capability(conn, profile_id, capability)
            if profile.revoked:
                return
            conn.execute(
                """
                UPDATE profiles
                SET revoked = 1, revoked_by = ?, revoked_at = ?, revoke_reason = ?
                WHERE profile_id = ?
                """,
                (actor, now.astimezone(timezone.utc).isoformat(), reason, profile_id),
            )
            self._authority_generation += 1

    def kill(
        self, profile_id: str, *, actor: str, now: datetime, reason: str, capability: object = None
    ) -> None:
        self._set_stop(
            profile_id, actor=actor, now=now, reason=reason, column="kill", capability=capability
        )

    def hold(
        self, profile_id: str, *, actor: str, now: datetime, reason: str, capability: object = None
    ) -> None:
        self._set_stop(
            profile_id, actor=actor, now=now, reason=reason, column="hold", capability=capability
        )

    def release_stop(
        self,
        profile_id: str,
        *,
        actor: str,
        now: datetime,
        reconciled: bool,
        capability: object = None,
    ) -> None:
        """Clear a profile kill and hold. Recovery ownership cannot do this.

        The caller's ``reconciled`` flag is only an assertion of intent: the
        durable check below derives the real reconcile state from the store.
        A perimeter with an unresolved reservation (pending, in-progress, or a
        failed handoff that has not been reconciled against the gateway's
        durable order identity) is refused.
        """
        _require_now(now)
        if reconciled is not True:
            raise ProfileRefused("not_reconciled", "stop release requires reconciliation")
        with self._txn() as conn:
            now = self._authoritative_now
            profile = self._profile_row(conn, profile_id)
            self._require_actor(actor, expected=profile.stop_owner)
            self._require_capability(conn, profile_id, capability)
            if profile.revoked or profile.expires_at <= now:
                code = "revoked" if profile.revoked else "expired"
                raise ProfileRefused(
                    code, "a revoked or expired profile cannot clear the perimeter stop"
                )
            occupant = conn.execute(
                """
                SELECT profile_id FROM profiles
                WHERE account_id = ? AND venue = ? AND environment = ?
                  AND revoked = 0 AND expires_at > ?
                """,
                (*profile.binding(), now.astimezone(timezone.utc).isoformat()),
            ).fetchone()
            if occupant is None or occupant["profile_id"] != profile.profile_id:
                raise ProfileRefused(
                    "revoked", "only the current unrevoked profile can clear the perimeter stop"
                )
            unresolved = conn.execute(
                """
                SELECT COUNT(*) FROM reservations
                WHERE account_id = ? AND venue = ? AND environment = ?
                  AND handoff_done = 0
                  AND (handoff_state IN ('pending', 'in_progress')
                       OR (handoff_state = 'failed' AND reconciled = 0))
                """,
                profile.binding(),
            ).fetchone()[0]
            if unresolved:
                raise ProfileRefused(
                    "not_reconciled",
                    "perimeter has a reservation that is not durably reconciled",
                )
            updated = conn.execute(
                """
                UPDATE controls
                SET kill = 0, hold = 0, kill_actor = NULL, hold_actor = NULL,
                    kill_reason = NULL, hold_reason = NULL, updated_at = ?
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                (now.astimezone(timezone.utc).isoformat(), *profile.binding()),
            )
            if updated.rowcount != 1:
                raise ProfileRefused("store", "stop ledger is missing")

    def authorize(
        self,
        profile_id: str,
        *,
        action: str,
        client_order_id: str,
        notional: Decimal | str,
        actor: str,
        now: datetime,
        model_output: object = None,
        order_ref: str | None = None,
        provider: str | None = None,
        model_version: str | None = None,
        adapter: str | None = None,
        venue: str | None = None,
        venue_family: str | None = None,
        capability: object = None,
    ) -> Reservation:
        """Reserve one action, or return the original reservation for the same id."""
        _validate_model_output(model_output)
        if model_output_grants_authority(model_output):
            raise ProfileRefused("model_authority", "model output cannot grant authority")
        if order_ref is not None:
            order_ref = _ident(order_ref, code="order_ref")
        _require_now(now)
        client_id = _ident(client_order_id, code="client_order_id")
        amount = _parse_money(notional, code="notional")
        with self._txn() as conn:
            now = self._authoritative_now
            profile = self._load_installed(conn, profile_id, now=now)
            self._require_actor(actor, expected=profile.stop_owner)
            self._require_capability(conn, profile_id, capability)
            if action not in profile.allowed_actions:
                raise ProfileRefused("action_refused", "action is outside the profile")
            _require_match(provider, profile.provider, "binding_provider")
            _require_match(model_version, profile.model_version, "binding_model")
            _require_match(adapter, profile.adapter, "binding_adapter")
            _require_match(venue, profile.venue, "binding_venue")
            _require_match(venue_family, profile.venue_family, "binding_venue_family")
            self._refuse_if_blocked(conn, profile)
            existing = conn.execute(
                "SELECT * FROM reservations WHERE client_order_id = ?",
                (client_id,),
            ).fetchone()
            if existing is not None:
                if not _same_reservation(existing, profile.profile_id, action, amount):
                    raise ProfileRefused(
                        "client_order_conflict", "client order id is already bound"
                    )
                if existing["order_ref"] is None and order_ref is not None:
                    if existing["handoff_done"]:
                        raise ProfileRefused(
                            "client_order_conflict", "client order id is already bound"
                        )
                    # One-time atomic binding of a legacy/unreferenced reservation.
                    bound = conn.execute(
                        "UPDATE reservations SET order_ref = ? "
                        "WHERE client_order_id = ? AND order_ref IS NULL "
                        "AND handoff_done = 0",
                        (order_ref, client_id),
                    )
                    if bound.rowcount != 1:
                        raise ProfileRefused(
                            "client_order_conflict", "client order id is already bound"
                        )
                    existing = conn.execute(
                        "SELECT * FROM reservations WHERE client_order_id = ?",
                        (client_id,),
                    ).fetchone()
                elif existing["order_ref"] != order_ref and not (
                    existing["order_ref"] is None and order_ref is None
                ):
                    raise ProfileRefused(
                        "client_order_conflict", "client order id is already bound"
                    )
                return _reservation_from_row(existing, replayed=True)
            self._tighten_ceiling(conn, profile)
            ceiling_order, ceiling_notional, ceiling_count = self._ceilings(conn, profile)
            if amount > ceiling_order:
                raise ProfileRefused("order_notional", "order exceeds the single-order cap")
            consumption = conn.execute(
                """
                SELECT reserved_notional, reserved_count FROM consumption
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                profile.binding(),
            ).fetchone()
            if consumption is None:
                raise ProfileRefused("store", "consumption ledger is missing")
            reserved = Decimal(consumption["reserved_notional"])
            count = int(consumption["reserved_count"])
            if reserved + amount > ceiling_notional:
                raise ProfileRefused("aggregate_notional", "cumulative notional cap would be exceeded")
            if count + 1 > ceiling_count:
                raise ProfileRefused("aggregate_count", "cumulative order-count cap would be exceeded")
            updated = reserved + amount
            conn.execute(
                """
                UPDATE consumption
                SET reserved_notional = ?, reserved_count = ?
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                (format(updated, "f"), count + 1, *profile.binding()),
            )
            conn.execute(
                """
                INSERT INTO reservations (
                    client_order_id, profile_id, account_id, venue, environment,
                    action, notional, created_at, handoff_done, handoff_state, order_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
                """,
                (
                    client_id,
                    profile.profile_id,
                    profile.account_id,
                    profile.venue,
                    profile.environment,
                    action,
                    format(amount, "f"),
                    now.astimezone(timezone.utc).isoformat(),
                    order_ref,
                ),
            )
        return Reservation(
            client_order_id=client_id,
            profile_id=profile.profile_id,
            action=action,
            notional=amount,
            replayed=False,
            handoff_done=False,
        )

    def consumption(self, profile_id: str) -> tuple[Decimal, int]:
        with self._txn() as conn:
            profile = self._profile_row(conn, profile_id)
            row = conn.execute(
                """
                SELECT reserved_notional, reserved_count FROM consumption
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                profile.binding(),
            ).fetchone()
        if row is None:
            raise ProfileRefused("store", "consumption ledger is missing")
        return Decimal(row["reserved_notional"]), int(row["reserved_count"])

    def claim_handoff(
        self, client_order_id: str, *, now: datetime, awaiting_submit: bool = False
    ) -> str | ClaimTicket:
        """Advance a reservation under exclusive, durable claim ownership.

        Every claim that enters the gateway is fenced by ``claim_generation``,
        a monotonically increasing token stored on the row. A row already owned
        (``in_progress``) refuses a second claim: two retries can never both
        enter ``OrderGateway.submit``. An abandoned claim is re-owned only
        through ``reconcile_handoff_to_gateway``, after its lease expires and
        after checking the gateway's durable order identity. ``completed`` is
        still the only state that skips a later submit; the replay path is
        unchanged.
        """
        _require_now(now)
        client_id = _ident(client_order_id, code="client_order_id")
        with self._txn() as conn:
            now = self._authoritative_now
            row = conn.execute(
                "SELECT * FROM reservations WHERE client_order_id = ?",
                (client_id,),
            ).fetchone()
            if row is None:
                raise ProfileRefused("unknown_reservation", "reservation is missing")
            state = row["handoff_state"]
            if state not in _HANDOFF_STATES:
                raise ProfileRefused("store", "handoff state is not admitted")
            if state == "completed" or int(row["handoff_done"]):
                return "replay"
            try:
                self._load_installed(conn, row["profile_id"], now=now)
            except ProfileRefused as exc:
                if exc.code in {"revoked", "expired"}:
                    return exc.code
                raise
            control = conn.execute(
                """
                SELECT kill, hold FROM controls
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                (row["account_id"], row["venue"], row["environment"]),
            ).fetchone()
            blocked = _control_code(control)
            if blocked == "store":
                raise ProfileRefused("store", "stop ledger is missing")
            if blocked:
                # A blocked reservation stays pending so a retry after release can run.
                return blocked
            if state == "in_progress":
                raise ProfileRefused(
                    "handoff_in_progress",
                    "handoff claim is already owned; reconcile before re-claiming",
                )
            # The durable action, not the caller flag, decides whether a gateway
            # submit is owed; a disagreeing flag must never complete a paper
            # submit that never reached the gateway.
            if awaiting_submit != (row["action"] == "submit_paper"):
                raise ProfileRefused(
                    "store", "handoff claim disagrees with the reservation action"
                )
            generation = int(row["claim_generation"]) + 1
            prior_reconciled = int(row["reconciled"] or 0)
            if row["order_ref"]:
                self._refuse_other_live_order(conn, row["order_ref"], client_id)
            if state == "failed":
                if not awaiting_submit:
                    # A non-gateway action (propose / dry-run) only reaches
                    # failed through reconcile after a stop; nothing was
                    # submitted, so finish it without any gateway call.
                    if not int(row["reconciled"]):
                        return "failed"
                    updated = conn.execute(
                        """
                        UPDATE reservations
                        SET handoff_state = 'completed', handoff_done = 1,
                            claim_generation = ?
                        WHERE client_order_id = ? AND handoff_state = 'failed'
                          AND handoff_done = 0 AND claim_generation = ?
                        """,
                        (generation, client_id, int(row["claim_generation"])),
                    )
                    if updated.rowcount != 1:
                        raise ProfileRefused("store", "handoff claim was not admitted")
                    return "completed"
                updated = conn.execute(
                    """
                    UPDATE reservations
                    SET handoff_state = 'in_progress', handoff_done = 0,
                        claim_generation = ?, reconciled = 0, claimed_at = ?
                    WHERE client_order_id = ? AND handoff_state = 'failed'
                      AND handoff_done = 0 AND claim_generation = ?
                    """,
                    (
                        generation,
                        now.astimezone(timezone.utc).isoformat(),
                        client_id,
                        int(row["claim_generation"]),
                    ),
                )
                if updated.rowcount != 1:
                    raise ProfileRefused("store", "handoff claim was not admitted")
                return ClaimTicket(client_id, generation, "retry", prior_reconciled)
            if not awaiting_submit:
                updated = conn.execute(
                    """
                    UPDATE reservations
                    SET handoff_state = 'completed', handoff_done = 1,
                        claim_generation = ?
                    WHERE client_order_id = ? AND handoff_state = 'pending'
                      AND handoff_done = 0 AND claim_generation = ?
                    """,
                    (generation, client_id, int(row["claim_generation"])),
                )
                if updated.rowcount != 1:
                    raise ProfileRefused("store", "handoff claim was not admitted")
                return "completed"
            updated = conn.execute(
                """
                UPDATE reservations
                SET handoff_state = 'in_progress', handoff_done = 0,
                    claim_generation = ?, claimed_at = ?
                WHERE client_order_id = ? AND handoff_state = 'pending'
                  AND handoff_done = 0 AND claim_generation = ?
                """,
                (
                    generation,
                    now.astimezone(timezone.utc).isoformat(),
                    client_id,
                    int(row["claim_generation"]),
                ),
            )
            if updated.rowcount != 1:
                raise ProfileRefused("store", "handoff claim was not admitted")
            return ClaimTicket(client_id, generation, "claimed", prior_reconciled)

    def complete_handoff(
        self, client_order_id: str, *, generation: int, gateway=None, ladder=None
    ) -> None:
        """Complete only after the stored order has signed gateway submission evidence.

        A claim generation proves attempt ownership, not submission. Resolve
        the stored order identity through the pinned gateway implementation
        within this transaction before making the generation-fenced write.
        """
        client_id = _ident(client_order_id, code="client_order_id")
        with self._txn() as conn:
            row = conn.execute(
                "SELECT * FROM reservations WHERE client_order_id = ?", (client_id,)
            ).fetchone()
            if (row is None or row["handoff_state"] != "in_progress"
                    or int(row["claim_generation"]) != generation):
                raise ProfileRefused(
                    "stale_claim", "handoff claim generation is stale or the row is not in progress"
                )
            if row["action"] != "submit_paper" or not _gateway_submitted(
                gateway, row["order_ref"], ladder
            ):
                raise ProfileRefused(
                    "gateway_identity", "handoff completion requires signed submission evidence"
                )
            updated = conn.execute(
                """
                UPDATE reservations
                SET handoff_state = 'completed', handoff_done = 1
                WHERE client_order_id = ? AND handoff_state = 'in_progress'
                  AND claim_generation = ?
                """,
                (client_id, generation),
            )
            if updated.rowcount != 1:
                raise ProfileRefused(
                    "stale_claim", "handoff claim generation is stale or the row is not in progress"
                )

    def fail_handoff(self, client_order_id: str, *, generation: int) -> None:
        """Record a returned refusal. The client id stays reusable and is not an order."""
        client_id = _ident(client_order_id, code="client_order_id")
        with self._txn() as conn:
            updated = conn.execute(
                """
                UPDATE reservations
                SET handoff_state = 'failed', handoff_done = 0
                WHERE client_order_id = ? AND handoff_state = 'in_progress'
                  AND claim_generation = ?
                """,
                (client_id, generation),
            )
            if updated.rowcount != 1:
                raise ProfileRefused(
                    "stale_claim", "handoff claim generation is stale or the row is not in progress"
                )

    def reconcile_failed_handoff(self, client_order_id: str) -> str:
        """Read-only probe: confirm a reservation row is a failed handoff.

        It sets no flags and does not write ``completed``. The identity-checked
        reconcile that clears the durable ``reconciled`` flag is
        ``reconcile_handoff_to_gateway`` (or ``reconcile_non_gateway_handoff``
        for an action that never reaches a gateway).
        """
        client_id = _ident(client_order_id, code="client_order_id")
        with self._txn() as conn:
            row = conn.execute(
                "SELECT handoff_state, handoff_done FROM reservations WHERE client_order_id = ?",
                (client_id,),
            ).fetchone()
            if row is None or row["handoff_state"] != "failed" or int(row["handoff_done"]):
                raise ProfileRefused("store", "failed handoff is not reconcilable")
        return "failed"

    def _reconcile_handoff(
        self,
        client_order_id: str,
        *,
        order_ref: str | None,
        now: datetime,
        actor: str,
        gateway=None,
        ladder=None,
        require_no_gateway: bool = False,
        capability: object = None,
    ) -> str | ClaimTicket:
        """Trusted-boundary reconcile against the gateway's durable order identity.

        Derive submission from the gateway's signed transition chain inside
        the reservation transaction. Gateway-bound rows require that evidence
        even for a direct private call; only non-gateway rows may reconcile
        without it. The lookup and the state transition share this call so a
        direct caller cannot present a forged verdict to mark a submitted
        reservation failed/reconciled and so clear a stop out from under it.

        A durably-submitted handoff is recorded ``completed`` so no second
        submit is possible. An abandoned ``in_progress`` claim is re-owned
        under a new generation only once its lease has expired, the gateway
        shows no submission, and the profile and controls still admit the
        claim — the lease is what stops two reconcilers racing the same
        crashed claim from both minting a generation and both reaching the
        gateway. A ``pending`` or ``failed`` row is marked ``failed`` and
        ``reconciled`` when nothing reached the gateway.
        """
        _require_now(now)
        client_id = _ident(client_order_id, code="client_order_id")
        with self._txn() as conn:
            now = self._authoritative_now
            row = conn.execute(
                "SELECT * FROM reservations WHERE client_order_id = ?",
                (client_id,),
            ).fetchone()
            if row is None:
                raise ProfileRefused("unknown_reservation", "reservation is missing")
            self._require_capability(conn, row["profile_id"], capability)
            if require_no_gateway and (
                row["action"] == "submit_paper" or row["order_ref"] is not None
            ):
                raise ProfileRefused(
                    "store", "reservation requires gateway-identity reconcile"
                )
            # An unbound reservation (no ``order_ref`` on the row) must never
            # accept an order identity at reconcile time: that would let a
            # caller bind an arbitrary order to someone else's reservation
            # slice. Only an exact match against an already-bound row passes.
            if order_ref is not None and row["order_ref"] != order_ref:
                raise ProfileRefused(
                    "client_order_conflict", "reservation is bound to a different order"
                )
            # A submit_paper row with no bound order ref is not reconcilable.
            # Do not treat the absence as "not submitted".
            if row["order_ref"] is None and row["action"] == "submit_paper":
                raise ProfileRefused(
                    "order_ref", "reconcile refuses a reservation with no order ref"
                )
            state = row["handoff_state"]
            if state not in {"pending", "in_progress", "failed"} or int(row["handoff_done"]):
                raise ProfileRefused("store", "handoff is not reconcilable")
            order_submitted = False
            if row["action"] == "submit_paper" or row["order_ref"] is not None:
                order_submitted = _gateway_submitted(gateway, row["order_ref"], ladder)
            generation = int(row["claim_generation"])
            if order_submitted:
                if not self._complete_reconciled(conn, client_id, state, generation):
                    raise ProfileRefused("store", "handoff reconcile was not admitted")
                return "completed"
            # A pending row has not entered transport. Do not ask the broker.
            if state == "pending":
                if not self._fail_reconciled(conn, client_id, state, generation):
                    raise ProfileRefused("store", "handoff reconcile was not admitted")
                return "failed"
            order_ref = row["order_ref"]
        # The send arms this under the same lock before it touches the broker.
        # Recovery must not mint a retry while that attempt is still in flight.
        if self._attempt_inflight(client_id):
            raise ProfileRefused("claim_leased", "handoff attempt is in flight")
        # Broker I/O stays outside the store lock so kill and hold are not stalled.
        # Presence completes the reservation. Absence continues. An unreadable
        # broker stays unknown and does not release the stop.
        if not order_ref:
            visibility = "absent"
        else:
            visibility = _broker_order_visibility(gateway, order_ref, store=self)
        if visibility == "unknown":
            raise ProfileRefused(
                "broker_unknown",
                "broker must confirm the cockpit order is absent before release or retry",
            )
        if visibility == "present":
            _resolve_landed_submission(gateway, order_ref, store=self)
        with self._txn() as conn:
            now = self._authoritative_now
            row = conn.execute(
                "SELECT * FROM reservations WHERE client_order_id = ?",
                (client_id,),
            ).fetchone()
            if (
                row is None
                or row["handoff_state"] != state
                or int(row["claim_generation"]) != generation
                or int(row["handoff_done"])
            ):
                raise ProfileRefused("store", "handoff reconcile was not admitted")
            if row["action"] == "submit_paper" or row["order_ref"] is not None:
                if _gateway_submitted(gateway, row["order_ref"], ladder):
                    if not self._complete_reconciled(conn, client_id, state, generation):
                        raise ProfileRefused("store", "handoff reconcile was not admitted")
                    return "completed"
            if visibility == "present":
                if _open_submit_intent(gateway, row["order_ref"]) or not _gateway_submitted(
                    gateway, row["order_ref"], ladder
                ):
                    raise ProfileRefused(
                        "order_state",
                        "broker presence was not recorded on the order and its submit intent",
                    )
                if not self._complete_reconciled(conn, client_id, state, generation):
                    raise ProfileRefused("store", "handoff reconcile was not admitted")
                return "completed"
            if state == "failed":
                if not self._fail_reconciled(conn, client_id, state, generation):
                    raise ProfileRefused("store", "handoff reconcile was not admitted")
                return "failed"
            # An in_progress row whose lease has not expired is still an active
            # submit. A stop set during broker I/O must not mark it reconciled:
            # the chain can still be missing the transition while the call lands.
            claimed_at = row["claimed_at"]
            if claimed_at is None:
                raise ProfileRefused(
                    "store", "in-progress claim is missing its lease timestamp"
                )
            if now < _parse_claimed_at(claimed_at) + CLAIM_LEASE:
                raise ProfileRefused(
                    "claim_leased", "handoff claim lease has not expired"
                )
            # Abandoned in_progress and nothing reached the gateway: the claim
            # is dead. Re-own it under a new generation only when a fresh claim
            # would be admitted; otherwise resolve it failed+reconciled so the
            # reservation consumes only its own slice and never bricks the
            # perimeter.
            admittable = True
            try:
                self._load_installed(conn, row["profile_id"], now=now)
            except ProfileRefused as exc:
                if exc.code in {"revoked", "expired"}:
                    admittable = False
                else:
                    raise
            if admittable:
                control = conn.execute(
                    """
                    SELECT kill, hold FROM controls
                    WHERE account_id = ? AND venue = ? AND environment = ?
                    """,
                    (row["account_id"], row["venue"], row["environment"]),
                ).fetchone()
                blocked = _control_code(control)
                if blocked == "store":
                    raise ProfileRefused("store", "stop ledger is missing")
                admittable = blocked is None
            if not admittable:
                if not self._fail_reconciled(conn, client_id, "in_progress", generation):
                    raise ProfileRefused("store", "handoff reconcile was not admitted")
                return "failed"
            # The lease was checked above, before any reconciled write. Only an
            # expired lease with no signed submission may mint a new generation.
            updated = conn.execute(
                """
                UPDATE reservations
                SET claim_generation = ?, reconciled = 0, claimed_at = ?
                WHERE client_order_id = ? AND handoff_state = 'in_progress'
                  AND handoff_done = 0 AND claim_generation = ?
                """,
                (generation + 1, now.astimezone(timezone.utc).isoformat(), client_id, generation),
            )
            if updated.rowcount != 1:
                raise ProfileRefused("store", "handoff reconcile was not admitted")
            return ClaimTicket(client_id, generation + 1, "retry")

    def _complete_reconciled(self, conn, client_id: str, state: str, generation: int) -> bool:
        updated = conn.execute(
            """
            UPDATE reservations
            SET handoff_state = 'completed', handoff_done = 1,
                reconciled = 1, claim_generation = ?
            WHERE client_order_id = ? AND handoff_state = ?
              AND handoff_done = 0 AND claim_generation = ?
            """,
            (generation + 1, client_id, state, generation),
        )
        return updated.rowcount == 1

    def _fail_reconciled(self, conn, client_id: str, state: str, generation: int) -> bool:
        updated = conn.execute(
            """
            UPDATE reservations
            SET handoff_state = 'failed', reconciled = 1,
                claim_generation = ?
            WHERE client_order_id = ? AND handoff_state = ?
              AND handoff_done = 0 AND claim_generation = ?
            """,
            (generation + 1, client_id, state, generation),
        )
        return updated.rowcount == 1

    def reconcile_non_gateway_handoff(
        self, client_order_id: str, *, now: datetime, actor: str, capability: object = None
    ) -> str | ClaimTicket:
        """Reconcile a ``propose`` or ``submit_dry_run`` reservation after a stop.

        Neither action ever reaches a gateway, so nothing can have been
        submitted — the verdict is fixed ``False``, not caller-supplied. A
        reservation bound to a gateway order (``submit_paper``, or any row
        carrying an ``order_ref``) is refused here; it must be reconciled
        through ``reconcile_handoff_to_gateway`` instead, which derives the
        verdict from the gateway's own signed chain.
        """
        return self._reconcile_handoff(
            client_order_id, order_ref=None, now=now, actor=actor,
            require_no_gateway=True, capability=capability,
        )

    def _confirm_claim_before_broker_call(
        self, client_order_id: str, generation: int, *, now: datetime | None = None
    ) -> TransportPermit:
        """Renew only our still-current claim, under both locks, before transport.

        ``now`` is accepted and ignored. The store clock is read after the
        Python lock and the SQLite write lock. The same transaction reloads
        the profile and its kill/hold controls, and it renews only the
        generation this caller still owns. A stopped or slow owner cannot
        resurrect an expired lease, even if no reconciler has taken over yet.
        """
        if now is not None:
            _require_now(now)
        client_id = _ident(client_order_id, code="client_order_id")
        with self._txn() as conn:
            now = self._authoritative_now
            stamp = now.astimezone(timezone.utc).isoformat()
            cutoff = (now.astimezone(timezone.utc) - CLAIM_LEASE).isoformat()
            row = conn.execute(
                """
                SELECT * FROM reservations
                WHERE client_order_id = ? AND claim_generation = ?
                  AND handoff_state = 'in_progress' AND handoff_done = 0
                """,
                (client_id, generation),
            ).fetchone()
            if row is None:
                raise ProfileRefused(
                    "stale_claim", "handoff claim generation is stale"
                )
            try:
                profile = self._load_installed(conn, row["profile_id"], now=now)
            except ProfileRefused as exc:
                if exc.code in {"revoked", "expired"}:
                    raise ProfileRefused(
                        exc.code, "profile authority is no longer valid"
                    ) from exc
                raise
            self._refuse_if_blocked(conn, profile)
            claimed_at = row["claimed_at"]
            # All writers use canonical UTC ISO timestamps. Validate before using
            # exact text ordering in SQL (SQLite date functions round subseconds).
            if claimed_at != _parse_claimed_at(claimed_at).isoformat():
                raise ProfileRefused("store", "stored claim lease timestamp is not canonical")
            updated = conn.execute(
                """
                UPDATE reservations
                SET claimed_at = ?
                WHERE client_order_id = ? AND claim_generation = ? AND handoff_state = 'in_progress'
                  AND handoff_done = 0 AND claimed_at = ?
                  AND claimed_at > ? AND claimed_at <= ?
                """,
                (stamp, client_id, generation, claimed_at, cutoff, stamp),
            )
            if updated.rowcount != 1:
                raise ProfileRefused(
                    "stale_claim", "handoff claim generation or lease is stale before broker submit"
                )
            order_ref = row["order_ref"]
            if type(order_ref) is not str or not order_ref:
                raise ProfileRefused("order_ref", "paper submit requires an order id")
            self._refuse_other_live_order(conn, order_ref, client_id)
        return self._issue_transport_permit(client_id, generation, order_ref)

    def _set_stop(
        self,
        profile_id: str,
        *,
        actor: str,
        now: datetime,
        reason: str,
        column: str,
        capability: object = None,
    ) -> None:
        _require_now(now)
        _reason(reason)
        if column not in {"kill", "hold"}:
            raise ProfileRefused("store", "unknown stop column")
        with self._txn() as conn:
            now = self._authoritative_now
            stored = self._profile_row(conn, profile_id)
            self._require_actor(actor, expected=stored.profile.stop_owner)
            self._require_capability(conn, profile_id, capability)
            stamp = now.astimezone(timezone.utc).isoformat()
            binding = stored.profile.binding()
            if column == "kill":
                updated = conn.execute(
                    """
                    UPDATE controls
                    SET kill = 1, kill_actor = ?, kill_reason = ?, updated_at = ?
                    WHERE account_id = ? AND venue = ? AND environment = ?
                    """,
                    (actor, reason, stamp, *binding),
                )
            else:
                updated = conn.execute(
                    """
                    UPDATE controls
                    SET hold = 1, hold_actor = ?, hold_reason = ?, updated_at = ?
                    WHERE account_id = ? AND venue = ? AND environment = ?
                    """,
                    (actor, reason, stamp, *binding),
                )
            if updated.rowcount != 1:
                raise ProfileRefused("store", "stop ledger is missing")
            self._authority_generation += 1

    def _ensure_schema(self) -> None:
        with self._txn() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in ADMITTED_STORE_VERSIONS:
                raise ProfileRefused("store", "profile store version is not admitted")
            if version < 4:
                # A pre-v4 store holding data cannot be upgraded: its profile
                # rows predate the install-minted stop capability (and durable
                # claim ownership), so there is no authenticated token to carry
                # forward and no unauthenticated one may be minted or accepted.
                # Refuse; the store must be recreated or its profiles
                # re-installed through the normal install path. A store that is
                # empty in EVERY table may be upgraded in place. Version 4
                # already has that capability, so its rows migrate to version 5.
                for table in ("profiles", "controls", "consumption", "reservations"):
                    try:
                        rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    except sqlite3.Error:
                        rows = 0
                    if rows:
                        raise ProfileRefused(
                            "legacy_store",
                            "pre-current profile store holds rows; recreate the store "
                            "or re-install profiles through the authenticated install path",
                        )
            if version in {4, 5, STORE_USER_VERSION}:
                # CREATE TABLE IF NOT EXISTS would silently rebuild a ledger
                # lost in a damaged restore, including an admitted version-4
                # file. Refuse before any recreation or column migration.
                names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                required_tables = {"profiles", "controls", "consumption", "reservations"}
                if not required_tables <= names:
                    raise ProfileRefused(
                        "store", "current profile store is missing a ledger table"
                    )
                pre_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(reservations)").fetchall()
                }
                pre_profile_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()
                }
                v4_reservation_columns = {
                    "order_ref",
                    "handoff_state",
                    "claim_generation",
                    "reconciled",
                    "claimed_at",
                }
                transport_columns = {"transport_client_hash", "transport_binding_hash"}
                if version in {5, STORE_USER_VERSION}:
                    if not v4_reservation_columns | transport_columns <= pre_columns:
                        raise ProfileRefused(
                            "store", "reservations ledger is missing a column"
                        )
                elif not v4_reservation_columns <= pre_columns:
                    raise ProfileRefused("store", "reservations ledger is missing a column")
                if version == STORE_USER_VERSION and "protective_limit" not in pre_columns:
                    raise ProfileRefused(
                        "store", "reservations ledger is missing a column"
                    )
                if "stop_capability_hash" not in pre_profile_columns:
                    raise ProfileRefused("store", "profile ledger is missing a column")
            for statement in _SCHEMA:
                conn.execute(statement)
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(reservations)").fetchall()
            }
            profile_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()
            }
            v4_reservation_columns = {
                "order_ref", "handoff_state", "claim_generation", "reconciled", "claimed_at",
            }
            required_reservation_columns = v4_reservation_columns | {
                "transport_client_hash", "transport_binding_hash", "protective_limit",
            }
            if version == STORE_USER_VERSION:
                # A current-version store missing a column is corruption, not
                # a migration. Fail closed.
                if not required_reservation_columns <= columns:
                    raise ProfileRefused("store", "reservations ledger is missing a column")
                if "stop_capability_hash" not in profile_columns:
                    raise ProfileRefused("store", "profile ledger is missing a column")
            elif version == 4:
                if not v4_reservation_columns <= columns:
                    raise ProfileRefused("store", "reservations ledger is missing a column")
                if "stop_capability_hash" not in profile_columns:
                    raise ProfileRefused("store", "profile ledger is missing a column")
                if "transport_client_hash" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN transport_client_hash TEXT"
                    )
                if "transport_binding_hash" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN transport_binding_hash TEXT"
                    )
                conn.execute(
                    """
                    UPDATE reservations
                    SET transport_binding_hash = ?
                    WHERE handoff_done = 0
                      AND handoff_state IN ('in_progress', 'failed')
                      AND (
                        transport_binding_hash IS NULL OR transport_binding_hash = ''
                      )
                    """,
                    (_MIGRATED_UNBOUND,),
                )
                if "protective_limit" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN protective_limit TEXT"
                    )
            else:
                # Empty pre-current store: add every missing column in place.
                if "order_ref" not in columns:
                    # ADD COLUMN appends. A nullable TEXT column needs no default.
                    conn.execute("ALTER TABLE reservations ADD COLUMN order_ref TEXT")
                if "handoff_state" not in columns:
                    # NOT NULL on an added column requires a non-null default so
                    # existing rows satisfy the constraint.
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN handoff_state "
                        "TEXT NOT NULL DEFAULT 'pending'"
                    )
                if "claim_generation" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN claim_generation "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                if "reconciled" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN reconciled "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                if "claimed_at" not in columns:
                    conn.execute("ALTER TABLE reservations ADD COLUMN claimed_at TEXT")
                if "transport_client_hash" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN transport_client_hash TEXT"
                    )
                if "transport_binding_hash" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN transport_binding_hash TEXT"
                    )
                if "protective_limit" not in columns:
                    conn.execute(
                        "ALTER TABLE reservations ADD COLUMN protective_limit TEXT"
                    )
                if "stop_capability_hash" not in profile_columns:
                    conn.execute(
                        "ALTER TABLE profiles ADD COLUMN stop_capability_hash TEXT"
                    )
                conn.execute(
                    """
                    UPDATE reservations
                    SET handoff_state = 'completed'
                    WHERE handoff_done = 1
                      AND (handoff_state IS NULL OR handoff_state = 'pending')
                    """
                )
            conn.execute(f"PRAGMA user_version = {STORE_USER_VERSION}")

    def _load_installed(self, conn: sqlite3.Connection, profile_id: str, *, now: datetime):
        sql = conn.execute(
            "SELECT canonical, revoked FROM profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if sql is None:
            raise ProfileRefused("unknown_profile", "profile is not installed")
        if sql["revoked"]:
            raise ProfileRefused("revoked", "profile is revoked")
        try:
            document = json.loads(sql["canonical"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProfileRefused("store", "installed profile failed validation") from exc
        if not isinstance(document, dict):
            raise ProfileRefused("store", "installed profile failed validation")
        return load_profile(document, now=now)

    def _profile_row(self, conn: sqlite3.Connection, profile_id: str) -> _Stored:
        row = conn.execute(
            "SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise ProfileRefused("unknown_profile", "profile is not installed")
        try:
            canonical = json.loads(row["canonical"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProfileRefused("store", "installed profile failed validation") from exc
        if not isinstance(canonical, dict):
            raise ProfileRefused("store", "installed profile failed validation")
        # Expiry is enforced by authorize. This lookup only rejects a corrupted body.
        expires_at = _parse_expiry(canonical["expires_at"])
        try:
            loaded = load_profile(canonical, now=expires_at - timedelta(seconds=1))
        except ProfileRefused as exc:
            raise ProfileRefused("store", "installed profile failed validation") from exc
        if loaded.profile_id != row["profile_id"]:
            raise ProfileRefused("store", "installed profile identity drifted")
        return _Stored(loaded, revoked=bool(row["revoked"]))

    def _ceilings(
        self, conn: sqlite3.Connection, profile: AuthorityProfile
    ) -> tuple[Decimal, Decimal, int]:
        row = conn.execute(
            """
            SELECT ceiling_notional, ceiling_count, ceiling_order FROM consumption
            WHERE account_id = ? AND venue = ? AND environment = ?
            """,
            profile.binding(),
        ).fetchone()
        if row is None:
            raise ProfileRefused("store", "consumption ledger is missing")
        return (
            min(Decimal(row["ceiling_order"]), profile.max_order_notional),
            min(Decimal(row["ceiling_notional"]), profile.notional_cap),
            min(int(row["ceiling_count"]), profile.order_count_cap),
        )

    def _tighten_ceiling(self, conn: sqlite3.Connection, profile: AuthorityProfile) -> None:
        order_cap, notional_cap, count_cap = self._ceilings(conn, profile)
        conn.execute(
            """
            UPDATE consumption
            SET ceiling_notional = ?, ceiling_count = ?, ceiling_order = ?
            WHERE account_id = ? AND venue = ? AND environment = ?
            """,
            (
                format(notional_cap, "f"),
                count_cap,
                format(order_cap, "f"),
                *profile.binding(),
            ),
        )

    def _refuse_if_blocked(self, conn: sqlite3.Connection, profile: AuthorityProfile) -> None:
        control = conn.execute(
            """
            SELECT kill, hold FROM controls
            WHERE account_id = ? AND venue = ? AND environment = ?
            """,
            profile.binding(),
        ).fetchone()
        code = _control_code(control)
        if code == "store":
            raise ProfileRefused("store", "stop ledger is missing")
        if code == "kill_active":
            raise ProfileRefused("kill_active", "kill is active for this perimeter")
        if code == "hold_active":
            raise ProfileRefused("hold_active", "hold is active for this perimeter")

    def _require_actor(self, actor: str, *, expected: str) -> None:
        if type(actor) is not str or actor != expected:
            raise ProfileRefused("unauthenticated", "actor is not the stop owner")

    def _require_capability(self, conn: sqlite3.Connection, profile_id: str, capability: object) -> None:
        token = _capability_token(capability)
        row = conn.execute(
            "SELECT stop_capability_hash FROM profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        stored = None if row is None else row["stop_capability_hash"]
        if type(stored) is not str or not stored:
            raise ProfileRefused("unauthenticated", "stop capability is not bound")
        if not hmac.compare_digest(_capability_hash(token), stored):
            raise ProfileRefused("unauthenticated", "stop capability does not match")

    def _refuse_other_live_order(self, conn, order_ref: str, client_id: str) -> None:
        """One live reservation owns an order. A second claim cannot send it."""
        other = conn.execute(
            """
            SELECT client_order_id FROM reservations
            WHERE order_ref = ? AND client_order_id != ?
              AND handoff_state = 'in_progress' AND handoff_done = 0
            """,
            (order_ref, client_id),
        ).fetchone()
        if other is not None:
            raise ProfileRefused(
                "client_order_conflict", "another reservation owns this order"
            )

    def _issue_transport_permit(
        self, client_order_id: str, generation: int, order_ref: str
    ) -> TransportPermit:
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._live_permits = {
                key: value
                for key, value in self._live_permits.items()
                if value[0] != client_order_id
            }
            self._live_permits[nonce] = (client_order_id, generation, order_ref)
        return TransportPermit(self, nonce)

    def _matching_permit(self, nonce: str) -> tuple[str, tuple[str, int, str]] | None:
        if type(nonce) is not str:
            return None
        for key, value in self._live_permits.items():
            if hmac.compare_digest(key, nonce):
                return key, value
        return None

    def _assert_live_claim(
        self, conn, client_id: str, generation: int, order_ref: str, now,
        *, order=None, producing_runtime=None, market=None, now_quote=None,
    ):
        """Authority, generation, lease, and the reservation bindings, inside ``_txn``."""
        row = conn.execute(
            "SELECT * FROM reservations WHERE client_order_id = ?",
            (client_id,),
        ).fetchone()
        if (
            row is None
            or int(row["claim_generation"]) != generation
            or row["handoff_state"] != "in_progress"
            or int(row["handoff_done"])
        ):
            raise ProfileRefused(
                "stale_claim", "handoff claim generation is stale"
            )
        if row["order_ref"] != order_ref:
            raise ProfileRefused(
                "client_order_conflict", "permit is bound to a different order"
            )
        try:
            profile = self._load_installed(conn, row["profile_id"], now=now)
        except ProfileRefused as exc:
            if exc.code in {"revoked", "expired"}:
                raise ProfileRefused(
                    exc.code, "profile authority is no longer valid"
                ) from exc
            raise
        self._refuse_if_blocked(conn, profile)
        claimed_at = row["claimed_at"]
        claimed = None if not claimed_at else _parse_claimed_at(claimed_at)
        if claimed is None or claimed > now or now >= claimed + CLAIM_LEASE:
            raise ProfileRefused(
                "stale_claim",
                "handoff claim generation or lease is stale before broker submit",
            )
        if row["action"] != "submit_paper":
            raise ProfileRefused("action_refused", "action is outside the paper submit handoff")
        if order is None:
            raise ProfileRefused("order_required", "paper submit requires the gateway order")
        if getattr(order, "id", None) is None or str(order.id) != order_ref:
            raise ProfileRefused(
                "client_order_conflict", "permit is bound to a different order"
            )
        if type(producing_runtime) is not str or producing_runtime != profile.provider:
            raise ProfileRefused(
                "binding_provider", "producing runtime does not match the profile provider"
            )
        if getattr(order, "account", None) != row["account_id"]:
            raise ProfileRefused("account_mismatch", "order account is outside the profile")
        self._refuse_other_live_order(conn, order_ref, client_id)
        if _tuple_notional(order, market=market, now=now_quote) != _parse_money(
            row["notional"], code="notional"
        ):
            raise ProfileRefused(
                "notional_mismatch", "order notional does not match the reservation"
            )
        return row

    def _consume_transport_permit(
        self, nonce: str, *, order_ref: str, client: object = None,
        order=None, producing_runtime=None, market=None, now_quote=None,
    ) -> tuple[object, str, int, str]:
        """Re-check the live claim, then burn the nonce. Returns the binding."""
        if type(nonce) is not str or type(order_ref) is not str or not order_ref:
            raise ProfileRefused(
                "claim_required", "paper submit requires a validated profile claim"
            )
        with self._txn() as conn:
            now = self._authoritative_now
            matched = self._matching_permit(nonce)
            if matched is None:
                raise ProfileRefused(
                    "claim_required", "paper submit requires a validated profile claim"
                )
            key, (client_id, generation, bound_order) = matched
            if bound_order != order_ref:
                raise ProfileRefused(
                    "client_order_conflict", "permit is bound to a different order"
                )
            row = self._assert_live_claim(
                conn, client_id, generation, bound_order, now,
                order=order, producing_runtime=producing_runtime,
                market=market, now_quote=now_quote,
            )
            if client is not None:
                digest = _remember_transport_client(client)
                binding = _binding_digest(client)
                stored_hash = row["transport_client_hash"]
                stored_binding = row["transport_binding_hash"]
                if stored_binding == _MIGRATED_UNBOUND and not _authenticated_startup(
                    self, client
                ):
                    raise ProfileRefused(
                        "broker_unknown", "broker client is not the admitted transport"
                    )
                token_ok = (
                    type(stored_hash) is str and stored_hash
                    and hmac.compare_digest(digest, stored_hash)
                )
                binding_ok = _binding_matches(stored_binding, binding)
                if type(stored_hash) is str and stored_hash and not token_ok and not binding_ok:
                    raise ProfileRefused(
                        "broker_unknown", "broker client is not the admitted transport"
                    )
                if not token_ok or (
                    binding is not None and not _binding_matches(stored_binding, binding)
                ):
                    conn.execute(
                        """
                        UPDATE reservations
                        SET transport_client_hash = ?,
                            transport_binding_hash = COALESCE(?, transport_binding_hash)
                        WHERE client_order_id = ?
                        """,
                        (digest, binding, client_id),
                    )
                self._admitted_clients[bound_order] = _unwrap_client(client)
            del self._live_permits[key]
        return (self, client_id, generation, bound_order)

    def _recheck_transport_authority(
        self, client_order_id: str, generation: int, order_ref: str,
        *, order=None, producing_runtime=None, market=None, now_quote=None,
    ) -> None:
        """The transport call re-reads authority and the reservation bindings."""
        client_id = _ident(client_order_id, code="client_order_id")
        if type(generation) is not int or type(order_ref) is not str or not order_ref:
            raise ProfileRefused(
                "claim_required", "paper submit requires a validated profile claim"
            )
        with self._txn() as conn:
            self._assert_live_claim(
                conn, client_id, generation, order_ref, self._authoritative_now,
                order=order, producing_runtime=producing_runtime,
                market=market, now_quote=now_quote,
            )
            return self._authority_generation

    def _require_authority_generation(self, seen: int) -> None:
        """Refuse when kill, hold, or revoke committed after this generation."""
        if type(seen) is not int:
            raise ProfileRefused("authority", "authority changed before the send")
        with self._txn() as conn:
            del conn
            if self._authority_generation != seen:
                raise ProfileRefused("authority", "authority changed before the send")

    def _read_store_clock(self) -> datetime:
        """The authority clock, and only after both the Python and SQLite locks.

        A reading behind the high-water mark is a rewind and is refused.
        The mark only moves forward.
        """
        clock = self._clock
        if clock is None or not self._lock.locked() or not self._conn.in_transaction:
            raise ProfileRefused("clock", "store clock is read only after both locks")
        try:
            stamp = clock.now()
        except ProfileRefused:
            raise
        except Exception as exc:
            raise ProfileRefused("clock", "store clock is unavailable or invalid") from exc
        _require_now(stamp)
        high = self._clock_high_water
        if high is not None and stamp < high:
            raise ProfileRefused(
                "stale_claim",
                "store clock rewound; handoff claim lease is stale",
            )
        if high is None or stamp > high:
            self._clock_high_water = stamp
            self._clock_mark_dirty = True
        return stamp

    def _load_clock_mark(self) -> None:
        """The high-water mark survives a new store object on the same file."""
        try:
            row = self._conn.execute(
                "SELECT stamp FROM clock_mark WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise ProfileRefused("store", "profile store is unreadable") from exc
        if row is None or not row["stamp"]:
            return
        stamp = _parse_claimed_at(row["stamp"])
        if self._clock_high_water is None or stamp > self._clock_high_water:
            self._clock_high_water = stamp

    def _flush_clock_mark(self) -> None:
        """Persist the mark after the reservation transaction ends.

        An expired authorize rolls that transaction back. The mark is written
        on the autocommit connection afterwards so a restart still refuses a
        rewind.
        """
        if not self._clock_mark_dirty or self._clock_high_water is None:
            return
        stamp = self._clock_high_water.astimezone(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO clock_mark (id, stamp) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET stamp = excluded.stamp
            WHERE excluded.stamp > clock_mark.stamp
            """,
            (stamp,),
        )
        self._clock_mark_dirty = False

    def _arm_inflight(self, client_order_id: str, attempt_id: str) -> None:
        """Record the attempt that is about to send. Recovery reads this lock."""
        if type(client_order_id) is not str or type(attempt_id) is not str:
            raise ProfileRefused("approval", "submission attempt is missing")
        with self._lock:
            self._inflight[client_order_id] = attempt_id

    def _clear_inflight(self, client_order_id: str) -> None:
        with self._lock:
            self._inflight.pop(client_order_id, None)

    def _attempt_inflight(self, client_order_id: str) -> bool:
        if type(client_order_id) is not str:
            return False
        with self._lock:
            return client_order_id in self._inflight

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise ProfileRefused("store", "profile store transaction failed") from exc
            try:
                self._authoritative_now = self._read_store_clock()
                yield self._conn
            except Exception:
                self._conn.rollback()
                self._flush_clock_mark()
                raise
            else:
                try:
                    self._conn.commit()
                except sqlite3.Error as exc:
                    self._conn.rollback()
                    self._flush_clock_mark()
                    raise ProfileRefused("store", "profile store commit failed") from exc
                self._flush_clock_mark()
            finally:
                self._authoritative_now = None


@dataclass(frozen=True)
class _Stored:
    profile: AuthorityProfile
    revoked: bool

    def __getattr__(self, name: str):
        return getattr(self.profile, name)


def _fresh_reference_price(order, *, market, now) -> Decimal:
    """Ask for a buy and bid for a sell, from a fresh trusted quote.

    ``order.notional`` is not a reference price.
    """
    if market is None or now is None:
        raise ProfileRefused("quote_unavailable", "market order requires a fresh quote")
    symbol = getattr(order, "symbol", None)
    limit_s = getattr(market, "max_age_s", None)
    if limit_s is None:
        from orchestrator.breakers import BreakerThresholds

        limit_s = BreakerThresholds().data_staleness_s
    try:
        reason = market.stale_reason(symbol, now=now, limit_s=float(limit_s))
    except ProfileRefused:
        raise
    except Exception as exc:
        raise ProfileRefused(
            "quote_unavailable", "market order quote could not be read"
        ) from exc
    if reason:
        raise ProfileRefused("stale_quote", "market order quote is not fresh")
    try:
        quote = market.quote(symbol)
    except ProfileRefused:
        raise
    except Exception as exc:
        raise ProfileRefused(
            "quote_unavailable", "market order quote could not be read"
        ) from exc
    side = str(getattr(order, "side", "") or "").strip().lower()
    field = "ask" if side == "buy" else "bid" if side == "sell" else None
    if field is None or not isinstance(quote, dict):
        raise ProfileRefused("quote_unavailable", "market order reference price is missing")
    try:
        price = Decimal(str(quote.get(field)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProfileRefused(
            "quote_unavailable", "market order reference price is missing"
        ) from exc
    if not price.is_finite() or price <= 0:
        raise ProfileRefused("quote_unavailable", "market order reference price is missing")
    return price


def _tuple_notional(order, *, market=None, now=None) -> Decimal:
    """Reserved size is qty times limit price, or qty times a buffered touch.

    ``order.notional`` is a detached attribute. A caller can lower it without
    changing the order the gateway will send. A market order has no limit
    price; its exposure is qty times the fresh ask (buy) or bid (sell),
    inflated by ``MARKET_REFERENCE_BUFFER``. No fresh quote refuses.
    """
    from brokers.order_tuple import canonical_order_tuple

    try:
        canon = canonical_order_tuple(
            symbol=getattr(order, "symbol", None),
            side=getattr(order, "side", None),
            qty=getattr(order, "qty", None),
            order_type=getattr(order, "order_type", None),
            limit_price=getattr(order, "limit_price", None),
            tif=getattr(order, "tif", None) or "day",
        )
    except (TypeError, ValueError) as exc:
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        ) from exc
    if canon["limit_price"] is None:
        price = _fresh_reference_price(order, market=market, now=now)
        product = (
            Decimal(canon["qty"]) * price * (Decimal("1") + MARKET_REFERENCE_BUFFER)
        ).quantize(_MONEY)
    else:
        product = (Decimal(canon["qty"]) * Decimal(canon["limit_price"])).quantize(_MONEY)
    return _parse_money(product, code="notional")


def _protective_unit(price: Decimal, *, side: str) -> Decimal:
    """The broker limit for one share.

    A buy pays no more than the ask inflated by the buffer, rounded down.
    A sell accepts no less than the bid reduced by the buffer, rounded up,
    so the order can still fill at the touch. Exposure stays on the
    inflated touch either way.
    """
    side_l = str(side or "").strip().lower()
    if side_l == "buy":
        raw = price * (Decimal("1") + MARKET_REFERENCE_BUFFER)
        rounding = ROUND_DOWN
    elif side_l == "sell":
        raw = price * (Decimal("1") - MARKET_REFERENCE_BUFFER)
        rounding = ROUND_CEILING
    else:
        raise ProfileRefused(
            "quote_unavailable", "market order protective limit is missing"
        )
    unit = raw.quantize(_MONEY, rounding=rounding)
    if not unit.is_finite() or unit <= 0:
        raise ProfileRefused(
            "quote_unavailable", "market order protective limit is missing"
        )
    return unit


def _reserved_notional(store, client_order_id: str) -> Decimal:
    with store._txn() as conn:
        row = conn.execute(
            "SELECT notional FROM reservations WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
    if row is None:
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        )
    return _parse_money(row["notional"], code="notional")


def _collar_market_submission(order, canonical, *, market, now, reserved: Decimal) -> dict:
    """Copy a market payload into a protective limit.

    Exposure sizing stays qty times the touch inflated by the buffer. The
    broker payload is a limit: ask times the buffer rounded down for a buy,
    bid times one minus the buffer rounded up for a sell. Alpaca paper
    accepts ``ioc`` on equity limit orders, so this limit is immediate-or-cancel.
    An unfilled remainder does not rest. The local order tuple, its hash, its
    day time-in-force, and the approval stay a market order. The sent limit
    is recorded beside this copy. One quote read supplies both the exposure
    check and the limit. A quote that no longer matches the reservation
    refuses before the send.
    """
    if not isinstance(canonical, dict):
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        )
    if str(canonical.get("order_type") or "").strip().lower() != "market":
        return canonical
    price = _fresh_reference_price(order, market=market, now=now)
    try:
        qty = Decimal(str(canonical.get("qty")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        ) from exc
    exposure = _parse_money(
        (qty * price * (Decimal("1") + MARKET_REFERENCE_BUFFER)).quantize(_MONEY),
        code="notional",
    )
    if exposure != _parse_money(reserved, code="notional"):
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        )
    side = str(getattr(order, "side", "") or canonical.get("side") or "").strip().lower()
    canon_side = str(canonical.get("side") or "").strip().lower()
    if canon_side and canon_side != side:
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        )
    unit = _protective_unit(price, side=side)
    if qty * unit > exposure:
        raise ProfileRefused(
            "notional_mismatch", "protective limit exceeds the reserved exposure"
        )
    from brokers.order_tuple import _canonical_decimal_str

    collared = dict(canonical)
    collared["order_type"] = "limit"
    collared["limit_price"] = _canonical_decimal_str(unit)
    collared["tif"] = "ioc"
    return collared


def _bind_sent_protective_limit(store, gateway, client_order_id: str, order, limit_text: str) -> None:
    """Record the limit the broker is about to receive.

    The local order stays a market order. The reservation column and a signed
    audit row are what recovery and reconcile read.
    """
    if type(limit_text) is not str or not limit_text or type(client_order_id) is not str:
        raise ProfileRefused("notional_mismatch", "protective limit is missing")
    with store._txn() as conn:
        updated = conn.execute(
            """
            UPDATE reservations
            SET protective_limit = ?
            WHERE client_order_id = ?
              AND (protective_limit IS NULL OR protective_limit = ?)
            """,
            (limit_text, client_order_id, limit_text),
        )
        if updated.rowcount != 1:
            raise ProfileRefused(
                "notional_mismatch", "protective limit does not match the reservation"
            )
    st = None if gateway is None else object.__getattribute__(gateway, "_st")
    audit = None if st is None else getattr(st, "audit", None)
    if audit is None or not callable(getattr(audit, "append", None)):
        raise ProfileRefused("intent", "protective limit was not signed")
    audit.append(
        "paper_protective_limit",
        actor="paper-gateway",
        refs={
            "order_id": "" if getattr(order, "id", None) is None else str(order.id),
            "client_order_id": client_order_id,
        },
        payload={
            "protective_limit": limit_text,
            "broker_order_type": "limit",
            "local_order_type": "market",
            "side": str(getattr(order, "side", "") or "").strip().lower(),
            "exposure": str(_reserved_notional(store, client_order_id)),
        },
    )


def _signed_protective_limits(st, order_ref: str) -> list[str]:
    """Verified protective limits recorded for this order. Disagreement is a refusal."""
    audit = None if st is None else getattr(st, "audit", None)
    verify = None if audit is None else getattr(audit, "verify_entry", None)
    if audit is None or not callable(verify) or not order_ref:
        return []
    found: list[str] = []
    for row in audit._rows():
        if getattr(row, "event_type", None) != "paper_protective_limit":
            continue
        if str((row.refs or {}).get("order_id") or "") != str(order_ref):
            continue
        try:
            signed = verify(row)
        except Exception:
            signed = False
        if not signed:
            continue
        limit = (row.payload or {}).get("protective_limit")
        if type(limit) is not str or not limit or limit in found:
            continue
        found.append(limit)
    return found


def _state_quote(gateway):
    """The gateway state's trusted quote source and the state clock's now."""
    st = None if gateway is None else getattr(gateway, "_st", None)
    if st is None:
        return None, None
    clock = getattr(st, "clock", None)
    now = None
    if clock is not None:
        try:
            now = clock.now()
        except Exception:
            now = None
    return getattr(st, "market_data", None), now


def _order_matches_book(gateway, order) -> bool:
    """The presented order is the booked row, not a substitute with the same id."""
    from schemas.models.order import Order

    st = None if gateway is None else getattr(gateway, "_st", None)
    order_id = getattr(order, "id", None)
    if st is None or order_id is None:
        return False
    with st.session_factory() as session:
        row = session.get(Order, order_id)
        if row is None:
            row = session.get(Order, str(order_id))
        if row is None:
            return False
        session.expunge(row)
    if row.limit_price is None or getattr(order, "limit_price", None) is None:
        limit_ok = row.limit_price is None and getattr(order, "limit_price", None) is None
    else:
        limit_ok = Decimal(str(row.limit_price)) == Decimal(str(order.limit_price))
    try:
        qty_ok = Decimal(str(row.qty)) == Decimal(str(getattr(order, "qty", None)))
    except (InvalidOperation, ValueError, TypeError):
        qty_ok = False
    return bool(
        qty_ok
        and limit_ok
        and str(row.symbol) == str(getattr(order, "symbol", None))
        and str(row.side) == str(getattr(order, "side", None))
        and str(row.order_type) == str(getattr(order, "order_type", None))
        and str(row.tif or "day") == str(getattr(order, "tif", None) or "day")
    )


def _recovery_sized_notional(order, notional, *, gateway, market, now) -> Decimal:
    """Size a recovery against the reservation when the quote has moved.

    A limit order is still qty times its limit. A market order that is the
    booked row keeps the notional frozen at send; the live quote is not read.
    An object that does not canonicalize, or a market order that is not the
    booked row, fails closed.
    """
    from brokers.order_tuple import canonical_order_tuple

    try:
        canon = canonical_order_tuple(
            symbol=getattr(order, "symbol", None),
            side=getattr(order, "side", None),
            qty=getattr(order, "qty", None),
            order_type=getattr(order, "order_type", None),
            limit_price=getattr(order, "limit_price", None),
            tif=getattr(order, "tif", None) or "day",
        )
    except (TypeError, ValueError) as exc:
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        ) from exc
    if canon["limit_price"] is not None:
        product = (Decimal(canon["qty"]) * Decimal(canon["limit_price"])).quantize(_MONEY)
        return _parse_money(product, code="notional")
    if not _order_matches_book(gateway, order):
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        )
    return _parse_money(notional, code="notional")


def _check_paper_submit_binding(
    store: AuthorityProfileStore,
    profile_id: str,
    *,
    action: str,
    order: object,
    notional: Decimal | str,
    producing_runtime: object,
    market=None,
    now=None,
    frozen: bool = False,
    gateway=None,
) -> str:
    """The account/notional/provider/action checks a paper submit must pass.

    Shared by ``handoff_to_gateway`` and ``reconcile_handoff_to_gateway`` so a
    crash-recovery resubmit cannot skip what a fresh handoff enforces: a
    recovery call that substitutes a different order, account, notional,
    producing runtime, or action is refused here, before the chain lookup or a
    second broker call. Returns the order's ``str`` id.

    This reads the installed profile's bound account/provider only — it does
    NOT re-check revoked/expired. Current authority is a separate question,
    already enforced by ``authorize`` (fresh handoff) or the reconcile
    admittable check (recovery); a profile revoked after an order already
    landed must not block recording that landed fact.
    """
    if action != "submit_paper":
        raise ProfileRefused("action_refused", "action is outside the paper submit handoff")
    if order is None:
        raise ProfileRefused("order_required", "paper submit requires the gateway order")
    if type(producing_runtime) is not str:
        raise ProfileRefused(
            "binding_provider", "producing runtime does not match the profile provider"
        )
    with store._txn() as conn:
        profile = store._profile_row(conn, profile_id).profile
    if producing_runtime != profile.provider:
        raise ProfileRefused(
            "binding_provider", "producing runtime does not match the profile provider"
        )
    order_account = getattr(order, "account", None)
    if order_account != profile.account_id:
        raise ProfileRefused("account_mismatch", "order account is outside the profile")
    if frozen:
        sized = _recovery_sized_notional(
            order, notional, gateway=gateway, market=market, now=now,
        )
    else:
        sized = _tuple_notional(order, market=market, now=now)
    if sized != _parse_money(notional, code="notional"):
        raise ProfileRefused(
            "notional_mismatch", "order notional does not match the reservation"
        )
    order_ref = getattr(order, "id", None)
    if order_ref is None:
        raise ProfileRefused("order_required", "paper submit requires an order id")
    return str(order_ref)


def handoff_to_gateway(
    store: AuthorityProfileStore,
    gateway,
    *,
    profile_id: str,
    action: str,
    client_order_id: str,
    notional: Decimal | str,
    actor: str,
    now: datetime,
    submit_args: tuple = (),
    submit_kwargs: Mapping | None = None,
    model_output: object = None,
    provider: str | None = None,
    model_version: str | None = None,
    adapter: str | None = None,
    venue: str | None = None,
    venue_family: str | None = None,
    capability: object = None,
) -> ProfiledHandoff:
    """Reserve the action, then call ``OrderGateway.submit`` only for paper submit.

    Paper submit invokes the class-pinned ``PaperOrderGateway`` implementation
    with the admitted paper adapter. Dry-run and proposal actions stop at the
    reservation. A refused transport does not reserve budget. ``completed``
    skips submit; an owned ``in_progress`` claim requires reconciliation.
    """
    _validate_model_output(model_output)
    if model_output_grants_authority(model_output):
        raise ProfileRefused("model_authority", "model output cannot grant authority")
    _adopt_state_clock(store, gateway)
    kwargs = dict(submit_kwargs or {})
    order_ref = None
    if action == "submit_paper":
        _require_paper_gateway(gateway)
        order = submit_args[0] if submit_args else kwargs.get("order")
        market, quote_now = _state_quote(gateway)
        order_ref = _check_paper_submit_binding(
            store, profile_id, action=action, order=order, notional=notional,
            producing_runtime=kwargs.get("producing_runtime"),
            market=market, now=quote_now,
        )
    elif action not in {"propose", "submit_dry_run"}:
        raise ProfileRefused("action_refused", "action is outside the profile handoff")

    reservation = store.authorize(
        profile_id,
        action=action,
        client_order_id=client_order_id,
        notional=notional,
        actor=actor,
        now=now,
        model_output=model_output,
        order_ref=order_ref,
        provider=provider,
        model_version=model_version,
        adapter=adapter,
        venue=venue,
        venue_family=venue_family,
        capability=capability,
    )
    claim = store.claim_handoff(
        reservation.client_order_id,
        now=now,
        awaiting_submit=(action == "submit_paper"),
    )
    if claim in {"replay", "completed"}:
        return ProfiledHandoff(
            reservation.client_order_id, False, claim == "replay" or reservation.replayed, None
        )
    if claim in {"kill_active", "hold_active", "revoked", "expired"}:
        raise ProfileRefused(claim, "handoff claim was refused")
    if not isinstance(claim, ClaimTicket):
        raise ProfileRefused("store", "handoff claim was not admitted")
    from brokers.paper_gateway import PaperOrderGateway

    kwargs.pop("claim_fence", None)
    _arm_or_release(gateway, store, reservation.client_order_id, claim)
    outcome = PaperOrderGateway.submit(
        gateway,
        *submit_args,
        **kwargs,
    )
    chain_kwargs = dict(kwargs)
    if getattr(outcome, "ok", None) is not True:
        if _settle_failed(
            store, gateway, reservation.client_order_id, order_ref,
            claim.generation, chain_kwargs, now, outcome=outcome, capability=capability,
        ):
            return ProfiledHandoff(reservation.client_order_id, False, True, None)
        return ProfiledHandoff(
            reservation.client_order_id,
            True,
            claim.state != "claimed" or reservation.replayed,
            outcome,
        )
    _settle_completed(
        store, gateway, reservation.client_order_id, order_ref,
        claim.generation, chain_kwargs, now, capability=capability,
    )
    return ProfiledHandoff(
        reservation.client_order_id,
        True,
        claim.state != "claimed" or reservation.replayed,
        outcome,
    )


def reconcile_handoff_to_gateway(
    store: AuthorityProfileStore,
    gateway,
    *,
    client_order_id: str,
    submit_args: tuple = (),
    submit_kwargs: Mapping | None = None,
    now: datetime,
    actor: str,
    capability: object = None,
) -> ProfiledHandoff:
    """Reconcile an abandoned claim against the gateway's durable order identity.

    The recovery order and execution metadata (account, notional, producing
    runtime, action) are checked against the stored reservation and profile
    with the exact same ``_check_paper_submit_binding`` a fresh handoff runs
    (CSL-218 recovery hardening) — a recovery call cannot substitute a
    different order, account, notional, runtime, or action and have it
    silently accepted. The gateway's durable identity for a paper submit is
    the order id bound at reservation time (``order_ref``), transported as
    ``cockpit-<order.id>`` and recorded on the HMAC-signed
    ``EVENT_ORDER_TRANSITION`` chain. If the chain already shows 'submitted',
    the crashed submit landed and the reservation is completed without a
    second submit. Otherwise the claim is re-owned under a new generation —
    gated by the claim lease, so a concurrent reconciler cannot also take
    over — and submitted exactly once.
    """
    _adopt_state_clock(store, gateway)
    kwargs = dict(submit_kwargs or {})
    _require_paper_gateway(gateway)
    order = submit_args[0] if submit_args else kwargs.get("order")
    client_id = _ident(client_order_id, code="client_order_id")
    with store._txn() as conn:
        row = conn.execute(
            "SELECT action, profile_id, notional FROM reservations WHERE client_order_id = ?",
            (client_id,),
        ).fetchone()
    if row is None:
        raise ProfileRefused("unknown_reservation", "reservation is missing")
    market, quote_now = _state_quote(gateway)
    order_ref = _check_paper_submit_binding(
        store, row["profile_id"], action=row["action"], order=order,
        notional=Decimal(row["notional"]), producing_runtime=kwargs.get("producing_runtime"),
        market=market, now=quote_now, frozen=True, gateway=gateway,
    )
    verdict = store._reconcile_handoff(
        client_id,
        order_ref=order_ref,
        now=now,
        actor=actor,
        gateway=gateway,
        ladder=kwargs.get("ladder"),
        capability=capability,
    )
    if verdict == "completed":
        return ProfiledHandoff(client_order_id, False, True, None)
    if verdict in {"failed", "revoked", "expired", "kill_active", "hold_active"}:
        raise ProfileRefused(verdict, "handoff reconcile was refused")
    if not isinstance(verdict, ClaimTicket):
        raise ProfileRefused("store", "handoff reconcile was not admitted")
    from brokers.paper_gateway import PaperOrderGateway

    kwargs.pop("claim_fence", None)
    _arm_or_release(gateway, store, client_id, verdict)
    outcome = PaperOrderGateway.submit(
        gateway,
        *submit_args,
        **kwargs,
    )
    if getattr(outcome, "ok", None) is not True:
        if _settle_failed(
            store, gateway, client_order_id, order_ref, verdict.generation, kwargs, now,
            outcome=outcome, capability=capability,
        ):
            return ProfiledHandoff(client_order_id, False, True, None)
        return ProfiledHandoff(client_order_id, True, True, outcome)
    _settle_completed(
        store, gateway, client_order_id, order_ref, verdict.generation, kwargs, now,
        capability=capability,
    )
    return ProfiledHandoff(client_order_id, True, True, outcome)


def _adopt_state_clock(store, gateway) -> None:
    """Share the state clock when the store was built without one.

    Identity is shared. A reading is not copied into a new clock. An explicit
    clock stays where it is, so a distinct clock still refuses at arm. A
    default clock is not replaced when the state clock would rewind a time
    that clock already shows, which is what kept an expired profile expired.
    Once this binds, the clock is no longer a default and a later gateway
    cannot replace it.
    """
    state = None if gateway is None else getattr(gateway, "_st", None)
    state_clock = None if state is None else getattr(state, "clock", None)
    store_clock = getattr(store, "_clock", None)
    if state_clock is None or store_clock is None or store_clock is state_clock:
        return
    if not getattr(store, "_clock_default", False):
        return
    try:
        store_now = store_clock.now()
        state_now = state_clock.now()
    except Exception:
        return
    if (
        not isinstance(store_now, datetime)
        or not isinstance(state_now, datetime)
        or store_now.tzinfo is None
        or store_now.utcoffset() is None
        or state_now.tzinfo is None
        or state_now.utcoffset() is None
        or state_now < store_now
    ):
        return
    high = getattr(store, "_clock_high_water", None)
    if high is not None and state_now < high:
        return
    store._clock = state_clock
    store._clock_default = False


def _revert_unarmed_claim(
    store, client_id: str, generation: int, claim_state: str, reconciled: int = 0
) -> None:
    """Arm failed before transport. Put the reservation back so it is not stranded."""
    previous = "failed" if claim_state == "retry" else "pending"
    with store._txn() as conn:
        conn.execute(
            """
            UPDATE reservations
            SET handoff_state = ?, claimed_at = NULL, claim_generation = ?,
                reconciled = ?
            WHERE client_order_id = ? AND handoff_state = 'in_progress'
              AND handoff_done = 0 AND claim_generation = ?
            """,
            (previous, generation - 1, int(reconciled), client_id, generation),
        )


def _arm_or_release(gateway, store, client_id: str, claim) -> None:
    try:
        gateway.arm_profile_claim(store, client_id, claim.generation)
    except ProfileRefused:
        _revert_unarmed_claim(
            store, client_id, claim.generation, claim.state,
            reconciled=getattr(claim, "reconciled", 0),
        )
        raise


def _fence_now(gateway, kwargs: Mapping):
    """Caller clocks are not authority. The store reads its own clock under both locks."""
    del gateway, kwargs
    raise ProfileRefused("clock", "caller clock is not the store clock")


def accept_transport_permit(
    permit: object, *, order_ref: str, client: object = None,
    order=None, producing_runtime=None, market=None, now_quote=None,
) -> tuple[object, str, int, str]:
    """Consume a permit only if it still matches this order and the live claim."""
    if type(permit) is not TransportPermit or type(permit.store) is not AuthorityProfileStore:
        raise ProfileRefused(
            "claim_required", "paper submit requires a validated profile claim"
        )
    return permit.store._consume_transport_permit(
        permit.nonce, order_ref=order_ref, client=client,
        order=order, producing_runtime=producing_runtime,
        market=market, now_quote=now_quote,
    )


def _unwrap_client(client):
    real = getattr(client, "_real_client", None)
    return client if real is None else real


def _transport_secret(client) -> str | None:
    """The credential, read only to key an HMAC. It is not stored."""
    real = _unwrap_client(client)
    headers = getattr(real, "_headers", None)
    if isinstance(headers, dict):
        secret = headers.get("APCA-API-SECRET-KEY")
        if type(secret) is str and secret.strip():
            return secret.strip()
    return None


def _transport_key_id(client) -> str | None:
    real = _unwrap_client(client)
    headers = getattr(real, "_headers", None)
    if isinstance(headers, dict):
        key_id = headers.get("APCA-API-KEY-ID")
        if type(key_id) is str and key_id.strip():
            return key_id.strip()
    key_id = getattr(real, "key_id", None)
    if type(key_id) is str and key_id.strip():
        return key_id.strip()
    return None


def _binding_digest(client) -> str | None:
    """HMAC of the key id under the secret, or under the startup transport key.

    A copied key id has neither key, so it does not match. The secret and the
    startup key stay in memory.
    """
    real = _unwrap_client(client)
    secret = _transport_secret(real)
    key_id = _transport_key_id(real)
    if secret is not None and key_id is not None:
        return hmac.new(
            secret.encode("utf-8"),
            ("alpaca-paper-key:" + key_id).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    startup = getattr(real, "_cockpit_startup_mac_key", None)
    if type(startup) is str and len(startup) >= 32:
        return hmac.new(
            startup.encode("utf-8"),
            b"cockpit-paper-transport",
            hashlib.sha256,
        ).hexdigest()
    return None


def _authenticated_startup(store, client) -> bool:
    expected = getattr(store, "_startup_binding", None)
    digest = _binding_digest(client)
    return (
        type(expected) is str and bool(expected)
        and type(digest) is str and bool(digest)
        and hmac.compare_digest(digest, expected)
    )


def bind_startup_transport(store, client) -> None:
    """Record the composed transport's digest and adopt migrated in-flight rows.

    The secret is not written. A client that only copies a key id has no digest
    and cannot bind. A second, different credential cannot replace the first.
    """
    digest = _binding_digest(client)
    if digest is None:
        raise ProfileRefused(
            "broker_unknown", "startup transport has no credential"
        )
    current = getattr(store, "_startup_binding", None)
    if current is not None and not hmac.compare_digest(current, digest):
        raise ProfileRefused("broker_unknown", "startup transport does not match")
    store._startup_binding = digest
    with store._txn() as conn:
        conn.execute(
            """
            UPDATE reservations
            SET transport_binding_hash = ?
            WHERE transport_binding_hash = ?
            """,
            (digest, _MIGRATED_UNBOUND),
        )


def _binding_matches(stored, digest) -> bool:
    return (
        type(stored) is str and bool(stored)
        and type(digest) is str and bool(digest)
        and hmac.compare_digest(digest, stored)
    )


def _remember_transport_client(client) -> str:
    """Mint a process-local token and return only its hash for the store."""
    real = _unwrap_client(client)
    token = getattr(real, "_cockpit_transport_token", None)
    if type(token) is not str or len(token) < 20:
        token = secrets.token_urlsafe(32)
        try:
            setattr(real, "_cockpit_transport_token", token)
        except Exception as exc:
            raise ProfileRefused(
                "broker_unknown", "broker client cannot carry its transport identity"
            ) from exc
    return _capability_hash(token)


def _transport_client_matches(store, order_ref: str, client) -> bool:
    with store._txn() as conn:
        rows = conn.execute(
            """
            SELECT transport_client_hash, transport_binding_hash
            FROM reservations WHERE order_ref = ?
            """,
            (order_ref,),
        ).fetchall()
    bound = []
    migrated = False
    for row in rows:
        token = row["transport_client_hash"]
        binding = row["transport_binding_hash"]
        if binding == _MIGRATED_UNBOUND:
            migrated = True
            continue
        if (type(token) is str and token) or (type(binding) is str and binding):
            bound.append((token, binding))
    if migrated and not _authenticated_startup(store, client):
        return False
    if not bound:
        return True
    real = _unwrap_client(client)
    token = getattr(real, "_cockpit_transport_token", None)
    token_digest = _capability_hash(token) if type(token) is str else None
    binding_digest = _binding_digest(real)
    for stored_token, stored_binding in bound:
        token_ok = (
            type(stored_token) is str and stored_token
            and token_digest is not None
            and hmac.compare_digest(token_digest, stored_token)
        )
        if token_ok or _binding_matches(stored_binding, binding_digest):
            continue
        return False
    return True


def _intent_binds_order(refs, order) -> bool:
    """The submit intent must name this order's proposal, risk, and tuple."""
    if order is None or getattr(order, "id", None) is None:
        return False
    return (
        str((refs or {}).get("order_id") or "") == str(order.id)
        and (refs or {}).get("proposal_id") == getattr(order, "proposal_id", None)
        and (refs or {}).get("risk_decision_id") == getattr(order, "risk_decision_id", None)
        and (refs or {}).get("order_tuple_hash") == getattr(order, "order_tuple_hash", None)
    )


def _signed_submit_intent(st, order) -> bool:
    """True when a verified ``order_submit`` intent binds this order.

    A resolved intent still counts: accept-then-timeout writes the intent
    before the response is lost. An intent that only copies the order id,
    or that names a different proposal, risk decision, or tuple, does not
    adopt the order. Broker presence without a fully bound signed row is
    not a submission this process can adopt.
    """
    audit = None if st is None else getattr(st, "audit", None)
    verify = None if audit is None else getattr(audit, "verify_entry", None)
    order_ref = "" if getattr(order, "id", None) is None else str(order.id)
    if audit is None or not callable(verify) or not order_ref:
        return False
    for row in audit._rows():
        payload = row.payload or {}
        if payload.get("_phase") != "intent" or payload.get("_op") != "order_submit":
            continue
        if not _intent_binds_order(row.refs or {}, order):
            continue
        try:
            signed = verify(row)
        except Exception:
            signed = False
        if signed:
            return True
    return False


def _open_submit_intent(gateway, order_ref: str) -> bool:
    st = None if gateway is None else getattr(gateway, "_st", None)
    audit = None if st is None else getattr(st, "audit", None)
    if audit is None or not order_ref:
        return False
    rows = {row.seq: row for row in audit._rows()}
    for seq in audit.unresolved_intents():
        row = rows.get(seq)
        if row is None:
            continue
        payload = row.payload or {}
        if payload.get("_phase") != "intent" or payload.get("_op") != "order_submit":
            continue
        if (row.refs or {}).get("order_id") == order_ref:
            return True
    return False


def _approval_consumed_on_chain(st, approval_id: str) -> bool:
    """True when a verified adjudication already records this approval as consumed."""
    from sqlalchemy import select

    from orchestrator.state import EVENT_ADJUDICATION
    from schemas.models.audit_entry import AuditEntry

    with st.session_factory() as session:
        entries = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
        ).scalars().all()
    for entry in entries:
        if (entry.refs or {}).get("row_id") != approval_id:
            continue
        if (entry.payload or {}).get("kind") != "approval_consumed":
            continue
        if st.audit.verify_entry(entry):
            return True
    return False


def _verified_audit_row(audit, row) -> bool:
    try:
        return bool(audit.verify_entry(row))
    except Exception:
        return False


def _aborted_intent_seqs(rows) -> set[int]:
    """Intent seqs whose result is an abort. A timeout abort is still an abort."""
    seqs: set[int] = set()
    for row in rows:
        payload = row.payload or {}
        refs = row.refs or {}
        if payload.get("_phase") != "result":
            continue
        if not payload.get("aborted") and not refs.get("aborted"):
            continue
        seq = refs.get("intent_seq")
        if type(seq) is int:
            seqs.add(seq)
    return seqs


def _bind_sent_submission(
    gateway, client_order_id: str, order, approval_id, *, attempt_id: str | None = None,
) -> None:
    """Sign the approval and attempt id before the broker call.

    The broker client order id is ``cockpit-<order id>`` for every retry.
    The attempt id is this dispatch. Recovery reads this row and ignores
    aborted submit intents. A later submit of the same approval is refused
    until this attempt is marked absent.
    """
    if (
        type(client_order_id) is not str
        or not client_order_id
        or type(approval_id) is not str
        or not approval_id
        or getattr(order, "id", None) is None
        or type(attempt_id) is not str
        or not attempt_id
    ):
        raise ProfileRefused("approval", "submission approval is missing")
    order_ref = str(order.id)
    st = None if gateway is None else object.__getattribute__(gateway, "_st")
    audit = None if st is None else getattr(st, "audit", None)
    if audit is None or not callable(getattr(audit, "append", None)):
        raise ProfileRefused("intent", "submission approval was not signed")
    audit.append(
        "paper_submission",
        actor="paper-gateway",
        refs={
            "order_id": order_ref,
            "approval_id": approval_id,
            "client_order_id": client_order_id,
            "broker_client_order_id": f"cockpit-{order_ref}"[:128],
            "proposal_id": getattr(order, "proposal_id", None),
            "risk_decision_id": getattr(order, "risk_decision_id", None),
            "order_tuple_hash": getattr(order, "order_tuple_hash", None),
            "attempt_id": attempt_id,
        },
        payload={"status": "sent", "attempt_id": attempt_id},
    )


def _approval_attempt_blocked(gateway, approval_id: str, *, except_client_id: str | None = None) -> bool:
    """True when this approval already has a live dispatched attempt.

    A live attempt is a verified ``paper_submission`` that no
    ``paper_submission_absent`` row has closed. The attempt currently being
    sent is excluded by its reservation id.
    """
    if type(approval_id) is not str or not approval_id:
        return False
    st = None if gateway is None else object.__getattribute__(gateway, "_st")
    audit = None if st is None else getattr(st, "audit", None)
    verify = None if audit is None else getattr(audit, "verify_entry", None)
    if audit is None or not callable(verify):
        return False
    absent: set[str] = set()
    live: list[str] = []
    for row in audit._rows():
        try:
            signed = bool(verify(row))
        except Exception:
            signed = False
        if not signed:
            continue
        refs = row.refs or {}
        if refs.get("approval_id") != approval_id:
            continue
        event = getattr(row, "event_type", None)
        client_id = refs.get("client_order_id")
        if event == "paper_submission_absent" and type(client_id) is str:
            absent.add(client_id)
        elif event == "paper_submission" and type(client_id) is str:
            live.append(client_id)
    return any(client_id not in absent and client_id != except_client_id for client_id in live)


def _mark_submission_absent(gateway, client_order_id: str, order_ref: str) -> None:
    """The broker confirmed this reservation's order is absent."""
    if type(client_order_id) is not str or not client_order_id or not order_ref:
        raise ProfileRefused("intent", "absent submission was not signed")
    st = None if gateway is None else getattr(gateway, "_st", None)
    audit = None if st is None else getattr(st, "audit", None)
    if audit is None or not callable(getattr(audit, "append", None)):
        raise ProfileRefused("intent", "absent submission was not signed")
    audit.append(
        "paper_submission_absent",
        actor="paper-gateway",
        refs={"order_id": str(order_ref), "client_order_id": client_order_id},
        payload={"absent": True},
    )


def _landed_approval_ids(st, order) -> list[str]:
    """The approval whose live submission matches this broker order.

    Aborted submit intents are not candidates. A signed submission is live
    when its reservation was not confirmed absent and its broker client order
    id is the cockpit id of this order. Two live approvals refuse.
    """
    order_ref = "" if getattr(order, "id", None) is None else str(order.id)
    broker_client_order_id = f"cockpit-{order_ref}"[:128]
    audit = None if st is None else getattr(st, "audit", None)
    if audit is None or not callable(getattr(audit, "verify_entry", None)) or not order_ref:
        return []
    rows = list(audit._rows())
    aborted = _aborted_intent_seqs(rows)
    absent: set[str] = set()
    submissions: list[tuple[str, str]] = []
    found: list[str] = []

    def add(approval_id) -> None:
        if type(approval_id) is not str or not approval_id or approval_id in found:
            return
        found.append(approval_id)

    for row in rows:
        if not _verified_audit_row(audit, row):
            continue
        refs = row.refs or {}
        if refs.get("order_id") != order_ref and str(refs.get("order_id") or "") != order_ref:
            continue
        event = getattr(row, "event_type", None)
        if event == "paper_submission_absent":
            client_id = refs.get("client_order_id")
            if type(client_id) is str and client_id:
                absent.add(client_id)
            continue
        if event == "paper_submission":
            if refs.get("broker_client_order_id") != broker_client_order_id:
                continue
            client_id = refs.get("client_order_id")
            approval_id = refs.get("approval_id")
            if type(client_id) is not str or type(approval_id) is not str:
                continue
            submissions.append((client_id, approval_id))
            continue
        payload = row.payload or {}
        if payload.get("_phase") != "intent" or payload.get("_op") != "order_submit":
            continue
        if not _intent_binds_order(refs, order):
            continue
        if row.seq in aborted:
            continue
        add(refs.get("approval_id"))
    for client_id, approval_id in submissions:
        if client_id in absent:
            continue
        add(approval_id)
    return found


def _signed_approval_binds(st, approval_id: str, order) -> bool:
    """True when a verified approve adjudication binds this order."""
    from sqlalchemy import select

    from orchestrator.state import EVENT_ADJUDICATION
    from schemas.models.audit_entry import AuditEntry

    proposal_id = getattr(order, "proposal_id", None)
    risk_id = getattr(order, "risk_decision_id", None)
    tuple_hash = getattr(order, "order_tuple_hash", None)
    with st.session_factory() as session:
        entries = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
        ).scalars().all()
    for entry in entries:
        if (entry.refs or {}).get("row_id") != approval_id:
            continue
        try:
            signed = st.audit.verify_entry(entry)
        except Exception:
            signed = False
        if not signed:
            continue
        payload = entry.payload or {}
        if (
            payload.get("decision") == "approve"
            and payload.get("proposal_id") == proposal_id
            and payload.get("risk_decision_id") == risk_id
            and payload.get("order_tuple_hash") == tuple_hash
        ):
            return True
    return False


def _consume_landed_approval(st, order) -> None:
    """Burn the approval bound to the submission that matches the broker order.

    Accept-then-timeout returns before ``OrderGateway`` writes
    ``order.approval_id``. Recovery reads the signed submission for this
    broker client order id and ignores aborted intents and reservations the
    broker confirmed absent. Two live approvals refuse before either is
    consumed. A second recovery that finds the signed consumption does not
    append another one.
    """
    from orchestrator.state import EVENT_ADJUDICATION
    from schemas.models.approval_record import ApprovalRecord

    approval_ids = _landed_approval_ids(st, order)
    if len(approval_ids) != 1:
        raise ProfileRefused(
            "approval", "landed order has no single signed approval to consume"
        )
    approval_id = approval_ids[0]
    row_id = getattr(order, "approval_id", None)
    if type(row_id) is str and row_id and row_id != approval_id:
        raise ProfileRefused(
            "approval", "order approval disagrees with the signed submit intent"
        )
    if not _signed_approval_binds(st, approval_id, order):
        raise ProfileRefused(
            "approval", "signed approval does not bind the landed order"
        )
    with st.session_factory() as session:
        approval = session.get(ApprovalRecord, approval_id)
        if approval is not None:
            session.expunge(approval)
    if approval is None:
        raise ProfileRefused("approval", "landed order approval is not persisted")
    if (
        approval.proposal_id != getattr(order, "proposal_id", None)
        or approval.risk_decision_id != getattr(order, "risk_decision_id", None)
        or approval.order_tuple_hash != getattr(order, "order_tuple_hash", None)
        or approval.user_decision != "approve"
    ):
        raise ProfileRefused(
            "approval", "mutable approval disagrees with the landed order"
        )
    if type(row_id) is not str or not row_id:
        order.approval_id = approval_id
    if bool(approval.single_use_consumed) and _approval_consumed_on_chain(st, approval.id):
        return
    approval.single_use_consumed = True
    tuple_hash = getattr(order, "order_tuple_hash", None)
    st.audit.record_state_change(
        approval,
        EVENT_ADJUDICATION,
        actor="paper-recovery",
        payload={
            "kind": "approval_consumed",
            "proposal_id": getattr(order, "proposal_id", None),
            "order_id": getattr(order, "id", None),
            "order_tuple_hash": tuple_hash,
        },
    )


def _resolve_landed_submission(gateway, order_ref: str, *, store=None) -> None:
    """Close this order's open submit intent and move the row to submitted.

    Presence recovery must not mark the reservation complete over a proposed
    order or an unresolved intent. An intent that is already resolved is left
    alone. The broker id is read back; it is not invented. The approval is
    consumed before the reservation can complete.
    """
    from brokers.alpaca_paper import AlpacaPaperBroker
    from brokers.order_state_machine import IllegalOrderTransition, transition
    from schemas.models.order import Order

    st = None if gateway is None else getattr(gateway, "_st", None)
    broker = None if gateway is None else getattr(gateway, "_broker", None)
    if st is None or not isinstance(broker, AlpacaPaperBroker) or not order_ref:
        raise ProfileRefused("broker_unknown", "landed paper order cannot be resolved")
    client_order_id = f"cockpit-{order_ref}"[:128]
    try:
        found = AlpacaPaperBroker.find_order(
            broker, broker_order_id=None, client_order_id=client_order_id
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable fill is not presence
        raise ProfileRefused(
            "broker_unknown", "landed paper order cannot be resolved"
        ) from exc
    if not isinstance(found, dict) or not found.get("broker_order_id"):
        raise ProfileRefused("broker_unknown", "landed paper order cannot be resolved")
    with st.session_factory() as session:
        bound_order = session.get(Order, order_ref)
        if bound_order is not None:
            session.expunge(bound_order)
    if not _signed_submit_intent(st, bound_order):
        raise ProfileRefused(
            "intent", "broker presence requires a signed order_submit intent"
        )

    class _Seq:
        def __init__(self, seq: int) -> None:
            self.seq = seq

    rows = list(st.audit._rows())
    unresolved = set(st.audit.unresolved_intents())
    for row in rows:
        if row.seq not in unresolved:
            continue
        payload = row.payload or {}
        if payload.get("_phase") != "intent" or payload.get("_op") != "order_submit":
            continue
        if not _intent_binds_order(row.refs or {}, bound_order):
            continue
        filled = found.get("filled_qty")
        price = found.get("avg_fill_price")
        st.audit.result(
            _Seq(row.seq),
            "order_submit",
            refs={
                "order_id": order_ref,
                "broker_order_id": str(found["broker_order_id"]),
            },
            payload={
                "status": str(found.get("status") or "accepted"),
                "filled_qty": str(filled if filled is not None else 0),
                "avg_fill_price": None if price is None else str(price),
            },
        )
    with st.session_factory() as session:
        order = session.get(Order, order_ref)
        if order is not None:
            session.expunge(order)
    if order is None:
        raise ProfileRefused("order_state", "landed order is not in the local book")
    if str(getattr(order, "order_type", "") or "").strip().lower() == "market":
        limits = _signed_protective_limits(st, order_ref)
        if len(limits) != 1:
            raise ProfileRefused(
                "intent", "landed market order has no signed protective limit"
            )
        if store is not None:
            with store._txn() as conn:
                rows = conn.execute(
                    "SELECT protective_limit FROM reservations WHERE order_ref = ?",
                    (order_ref,),
                ).fetchall()
            bound = [
                row["protective_limit"]
                for row in rows
                if type(row["protective_limit"]) is str and row["protective_limit"]
            ]
            if not bound or set(bound) != {limits[0]}:
                raise ProfileRefused(
                    "intent", "protective limit disagrees with the reservation"
                )
    _consume_landed_approval(st, order)
    if order.state == "submitted":
        return
    if order.state not in {"proposed", "approved"}:
        raise ProfileRefused("order_state", "landed order is not in a submittable state")
    try:
        if order.state == "proposed":
            order = transition(
                st, order, "approved", actor="paper-recovery",
                reason="broker presence", clock=st.clock,
            )
        transition(
            st, order, "submitted", actor="paper-recovery",
            reason="submitted to broker", clock=st.clock,
            broker_order_id=str(found["broker_order_id"]),
            filled_qty=found.get("filled_qty") if found.get("filled_qty") is not None else 0,
            avg_fill_price=found.get("avg_fill_price"),
        )
    except IllegalOrderTransition as exc:
        raise ProfileRefused(
            "order_state", "landed order could not be recorded"
        ) from exc


def _broker_order_visibility(gateway, order_ref: str, *, store=None) -> str:
    """``present``, ``absent``, or ``unknown`` for ``cockpit-<order_ref>``.

    The lookup is the admitted adapter class, not a replacement instance method.
    When a permit consume recorded the client for this order, a different client
    cannot prove the order absent.
    """
    from brokers.alpaca_paper import AlpacaPaperBroker

    broker = None if gateway is None else getattr(gateway, "_broker", None)
    if not isinstance(broker, AlpacaPaperBroker):
        return "unknown"
    client = getattr(broker, "_client", None)
    real = _unwrap_client(client)
    if store is not None:
        if not _transport_client_matches(store, order_ref, real):
            return "unknown"
        admitted = getattr(store, "_admitted_clients", {}).get(order_ref)
        if admitted is not None and real is not _unwrap_client(admitted):
            return "unknown"
    client_order_id = f"cockpit-{order_ref}"[:128]
    try:
        found = AlpacaPaperBroker.find_order(
            broker, broker_order_id=None, client_order_id=client_order_id
        )
    except Exception:  # noqa: BLE001 - an unclear broker read must not release the stop
        return "unknown"
    return "absent" if found is None else "present"


def _order_submitted_in_snapshot(st, order_ref: str) -> bool:
    """Lookup and full-chain/anchor verification share one audit snapshot.

    A submitted row that lands between two sessions must not turn a stale
    negative into an authoritative "not submitted".
    """
    from sqlalchemy import select

    from orchestrator.state import EVENT_ORDER_TRANSITION
    from schemas.models.audit_entry import AuditEntry

    audit = st.audit
    anchor_store = getattr(st, "anchor_store", None)
    try:
        anchor = None if anchor_store is None else anchor_store.latest()
    except Exception as exc:
        raise ProfileRefused(
            "chain_unreadable", "external anchor could not be read"
        ) from exc
    if anchor_store is not None and anchor is None:
        raise ProfileRefused("chain_unreadable", "required external anchor is unavailable")
    try:
        with audit._sf() as session, session.begin():
            if session.get_bind().dialect.name == "sqlite":
                connection = session.connection()
                dbapi = connection.connection.driver_connection
                if not dbapi.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            verified = audit.verify_session(session)
            if not getattr(verified, "ok", False):
                raise ProfileRefused("chain_unreadable", "signed chain did not verify")
            if anchor is not None:
                anchored = audit.verify_session_against_anchor(session, anchor[0], anchor[1])
                if not getattr(anchored, "ok", False):
                    raise ProfileRefused(
                        "chain_unreadable", "signed chain does not match its anchor"
                    )
            entries = session.execute(
                select(AuditEntry).where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
            ).scalars().all()
            for entry in entries:
                refs = entry.refs or {}
                payload = entry.payload or {}
                if refs.get("row_id") != order_ref or payload.get("to") != "submitted":
                    continue
                if audit.verify_entry(entry):
                    return True
            return False
    except ProfileRefused:
        raise
    except Exception as exc:
        raise ProfileRefused("chain_unreadable", "signed chain could not be read") from exc


def _gateway_submitted(gateway, order_ref: str | None, ladder) -> bool:
    """Read signed evidence from one audit snapshot, never instance overrides."""
    from brokers.gateway import OrderGateway

    if gateway is None or not order_ref:
        raise ProfileRefused(
            "gateway_identity", "gateway-bound reservation requires signed-chain evidence"
        )
    _require_paper_gateway(gateway)
    try:
        st = OrderGateway._resolve_state(gateway, ladder)
    except Exception as exc:
        raise ProfileRefused(
            "gateway_identity", "gateway durable order identity could not be resolved"
        ) from exc
    return _order_submitted_in_snapshot(st, order_ref)


def _chain_submitted(gateway, order_ref: str, kwargs: Mapping) -> bool:
    """Return signed submission evidence. An unreadable chain is not a negative."""
    return _gateway_submitted(gateway, order_ref, kwargs.get("ladder"))


def _settle_completed(
    store, gateway, client_order_id, order_ref, generation, kwargs, now, *, capability=None
):
    """Require signed submission evidence even for a successful gateway return.

    If a concurrent reconcile re-owned the claim while this submit was in
    flight, the order may still have landed. The durable chain is the truth.
    An unreadable chain leaves the row in progress and propagates.
    """
    try:
        store.complete_handoff(
            client_order_id, generation=generation, gateway=gateway, ladder=kwargs.get("ladder")
        )
    except ProfileRefused as exc:
        if exc.code != "stale_claim":
            raise
        try:
            submitted = _chain_submitted(gateway, order_ref, kwargs)
        except ProfileRefused as chain_exc:
            if chain_exc.code in {"chain_unreadable", "gateway_identity"}:
                raise
            raise
        if not submitted:
            raise
        _record_landed(
            store, gateway, client_order_id, order_ref, kwargs, now, capability=capability
        )


def _settle_failed(
    store, gateway, client_order_id, order_ref, generation, kwargs, now,
    *, outcome=None, capability=None,
) -> bool:
    """Record a refusal only if the order did not already land on the chain.

    Returns True when the signed chain shows the order already submitted (the
    reservation is then completed), so callers do not report a refusal. An
    unreadable or incomplete chain leaves the row in progress: do not call
    ``fail_handoff``, and do not free the client id.
    """
    try:
        submitted = _chain_submitted(gateway, order_ref, kwargs)
    except ProfileRefused as exc:
        if exc.code in {"chain_unreadable", "gateway_identity"}:
            raise
        raise
    if submitted:
        _record_landed(
            store, gateway, client_order_id, order_ref, kwargs, now, capability=capability
        )
        return True
    # ``order_state`` is also how a post-submit FSM failure is reported. Ask the
    # broker before fail_handoff for every stage that can follow transport.
    if getattr(outcome, "stage", None) in {"broker_error", "order_state"}:
        visibility = (
            "unknown" if not order_ref
            else _broker_order_visibility(gateway, order_ref, store=store)
        )
        if visibility == "present":
            _record_landed(
                store, gateway, client_order_id, order_ref, kwargs, now, capability=capability,
            )
            return True
        if visibility != "absent":
            raise ProfileRefused(
                "broker_unknown",
                "ambiguous broker acceptance must be reconciled before the profile stop is released",
            )
        _mark_submission_absent(gateway, client_order_id, order_ref)
    store.fail_handoff(client_order_id, generation=generation)
    return False


def _record_landed(
    store, gateway, client_order_id, order_ref, kwargs, now, *, capability=None
) -> None:
    try:
        store._reconcile_handoff(
            client_order_id, order_ref=order_ref, now=now,
            actor="store",
            gateway=gateway, ladder=kwargs.get("ladder"),
            capability=capability,
        )
    except ProfileRefused:
        # Suppress only when the other owner already completed the reservation.
        if not _reservation_completed(store, client_order_id):
            raise


def _reservation_completed(store, client_order_id: str) -> bool:
    with store._txn() as conn:
        row = conn.execute(
            "SELECT handoff_state, handoff_done FROM reservations WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
    return (
        row is not None
        and row["handoff_state"] == "completed"
        and bool(int(row["handoff_done"]))
    )


def _require_paper_gateway(gateway) -> None:
    """Admit only the paper gateway type. A caller-set scope string is not proof."""
    from brokers.alpaca_paper import AlpacaPaperBroker
    from brokers.paper_gateway import PaperOrderGateway

    if type(gateway) is not PaperOrderGateway:
        raise ProfileRefused("transport", "paper handoff requires the paper order gateway type")
    if not isinstance(getattr(gateway, "_broker", None), AlpacaPaperBroker):
        raise ProfileRefused(
            "binding_adapter", "paper handoff requires the admitted paper adapter"
        )


def _capability_token(capability: object) -> str:
    if isinstance(capability, StopCapability):
        token = capability.token
    elif type(capability) is str:
        token = capability
    else:
        raise ProfileRefused("unauthenticated", "stop capability is required")
    if len(token) < 20:
        raise ProfileRefused("unauthenticated", "stop capability is required")
    return token


def _capability_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_capability(profile_id: str, capability: object) -> StopCapability:
    if isinstance(capability, StopCapability):
        if capability.profile_id != profile_id:
            raise ProfileRefused("unauthenticated", "stop capability does not match")
        return capability
    return StopCapability(profile_id, _capability_token(capability))


def _require_match(value: object, expected: str, code: str) -> None:
    if type(value) is not str or value != expected:
        raise ProfileRefused(code, "execution binding does not match the profile")


def _control_code(control) -> str | None:
    if control is None:
        return "store"
    if int(control["kill"]):
        return "kill_active"
    if int(control["hold"]):
        return "hold_active"
    return None


def _same_reservation(row, profile_id: str, action: str, amount: Decimal) -> bool:
    return (
        row["profile_id"] == profile_id
        and row["action"] == action
        and Decimal(row["notional"]) == amount
    )


def _reservation_from_row(row, *, replayed: bool) -> Reservation:
    return Reservation(
        client_order_id=row["client_order_id"],
        profile_id=row["profile_id"],
        action=row["action"],
        notional=Decimal(row["notional"]),
        replayed=replayed,
        handoff_done=bool(row["handoff_done"]),
    )


def _require_now(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ProfileRefused("clock", "clock must be timezone-aware")


def _parse_expiry(value: object) -> datetime:
    if type(value) is not str:
        raise ProfileRefused("expires_at", "expiry must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProfileRefused("expires_at", "expiry must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProfileRefused("expires_at", "expiry must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _parse_claimed_at(value: str) -> datetime:
    """Parse a stored lease timestamp. Written only by this module as
    ``now.astimezone(timezone.utc).isoformat()``, so a parse failure is store
    corruption, not untrusted input.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ProfileRefused("store", "stored claim lease timestamp is malformed") from exc
    if parsed.tzinfo is None:
        raise ProfileRefused("store", "stored claim lease timestamp is malformed")
    return parsed.astimezone(timezone.utc)


def _parse_money(value: object, *, code: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ProfileRefused(code, "money values must be exact decimals")
    if isinstance(value, str):
        if not _MONEY_TEXT.fullmatch(value):
            raise ProfileRefused(code, "money values must be exact decimals")
        parsed = Decimal(value)
    elif isinstance(value, Decimal):
        try:
            exact = value.is_finite() and value == value.quantize(_MONEY)
        except InvalidOperation:
            exact = False
        if not exact:
            raise ProfileRefused(code, "money values must be exact decimals")
        parsed = value
    else:
        raise ProfileRefused(code, "money values must be exact decimals")
    try:
        quantized = parsed.quantize(_MONEY)
    except InvalidOperation as exc:
        raise ProfileRefused(code, "money values must be exact decimals") from exc
    if quantized <= 0:
        raise ProfileRefused(code, "money values must be positive")
    return quantized


def _ident(value: object, *, code: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ProfileRefused(code, "identifier is malformed")
    return value


def _reason(reason: str) -> None:
    if type(reason) is not str or not reason.strip() or len(reason) > 200:
        raise ProfileRefused("reason", "reason is required")
