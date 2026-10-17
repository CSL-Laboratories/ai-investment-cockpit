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

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Mapping

SCHEMA_VERSION = 1
ADMITTED_ENVIRONMENTS = frozenset({"paper", "dry_run"})
RUNTIME_PROFILE_CLASSES = frozenset({"A", "B"})
FUTURE_PROFILE_CLASS = "C"
MAX_PROFILE_LIFETIME = timedelta(days=7)
_MONEY = Decimal("0.01")
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


def model_output_grants_authority(model_output: object) -> bool:
    """Provider output is evidence. It is never a credential or a grant."""
    del model_output
    return False


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
        revoke_reason TEXT
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
        order_ref TEXT
    )
    """,
)


class AuthorityProfileStore:
    """Durable profile, consumption, idempotency and stop ledger."""

    def __init__(self, path: Path | str) -> None:
        file_path = Path(path)
        if not file_path.is_absolute() or file_path.suffix != ".sqlite":
            raise ProfileRefused(
                "store_path", "profile store path must be an absolute sqlite file"
            )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = file_path
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(
                str(file_path), check_same_thread=False, isolation_level=None, timeout=30
            )
        except sqlite3.Error as exc:
            raise ProfileRefused("store", "profile store is unreadable") from exc
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def install(self, document: Mapping, *, actor: str, now: datetime) -> AuthorityProfile:
        profile = load_profile(document, now=now)
        self._require_actor(actor, expected=profile.stop_owner)
        with self._txn() as conn:
            existing = conn.execute(
                "SELECT canonical, revoked FROM profiles WHERE profile_id = ?",
                (profile.profile_id,),
            ).fetchone()
            if existing is not None:
                if existing["canonical"] != profile.canonical():
                    raise ProfileRefused("profile_changed", "installed profile body cannot change")
                if existing["revoked"]:
                    raise ProfileRefused("revoked", "profile is revoked")
                return profile
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
            conn.execute(
                """
                INSERT INTO profiles (
                    profile_id, canonical, account_id, venue, environment, stop_owner,
                    recovery_owner, expires_at, installed_by, installed_at, revoked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO controls (
                    account_id, venue, environment, kill, hold
                ) VALUES (?, ?, ?, 0, 0)
                """,
                profile.binding(),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO consumption (
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
            self._tighten_ceiling(conn, profile)
        return profile

    def revoke(self, profile_id: str, *, actor: str, now: datetime, reason: str) -> None:
        _require_now(now)
        _reason(reason)
        with self._txn() as conn:
            profile = self._profile_row(conn, profile_id)
            self._require_actor(actor, expected=profile.stop_owner)
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

    def kill(self, profile_id: str, *, actor: str, now: datetime, reason: str) -> None:
        self._set_stop(profile_id, actor=actor, now=now, reason=reason, column="kill")

    def hold(self, profile_id: str, *, actor: str, now: datetime, reason: str) -> None:
        self._set_stop(profile_id, actor=actor, now=now, reason=reason, column="hold")

    def release_stop(
        self, profile_id: str, *, actor: str, now: datetime, reconciled: bool
    ) -> None:
        """Clear a profile kill and hold. Recovery ownership cannot do this."""
        _require_now(now)
        if reconciled is not True:
            raise ProfileRefused("not_reconciled", "stop release requires reconciliation")
        with self._txn() as conn:
            profile = self._profile_row(conn, profile_id)
            self._require_actor(actor, expected=profile.stop_owner)
            conn.execute(
                """
                UPDATE controls
                SET kill = 0, hold = 0, kill_actor = NULL, hold_actor = NULL,
                    kill_reason = NULL, hold_reason = NULL, updated_at = ?
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                (now.astimezone(timezone.utc).isoformat(), *profile.binding()),
            )

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
    ) -> Reservation:
        """Reserve one action, or return the original reservation for the same id."""
        if model_output_grants_authority(model_output):
            raise ProfileRefused("model_authority", "model output cannot grant authority")
        if order_ref is not None:
            order_ref = _ident(order_ref, code="order_ref")
        _require_now(now)
        client_id = _ident(client_order_id, code="client_order_id")
        amount = _parse_money(notional, code="notional")
        with self._txn() as conn:
            profile = self._load_installed(conn, profile_id, now=now)
            self._require_actor(actor, expected=profile.stop_owner)
            if action not in profile.allowed_actions:
                raise ProfileRefused("action_refused", "action is outside the profile")
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
                    action, notional, created_at, handoff_done, order_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
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

    def claim_handoff(self, client_order_id: str) -> str:
        """Mark a reservation handed off. A stop claimed here never reaches the gateway."""
        with self._txn() as conn:
            row = conn.execute(
                "SELECT * FROM reservations WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise ProfileRefused("unknown_reservation", "reservation is missing")
            if row["handoff_done"]:
                return "replay"
            control = conn.execute(
                """
                SELECT kill, hold FROM controls
                WHERE account_id = ? AND venue = ? AND environment = ?
                """,
                (row["account_id"], row["venue"], row["environment"]),
            ).fetchone()
            blocked = _control_code(control)
            if blocked:
                # A blocked reservation stays pending so a retry after release can run.
                return blocked
            conn.execute(
                "UPDATE reservations SET handoff_done = 1 WHERE client_order_id = ?",
                (client_order_id,),
            )
            return "claimed"

    def _set_stop(
        self, profile_id: str, *, actor: str, now: datetime, reason: str, column: str
    ) -> None:
        _require_now(now)
        _reason(reason)
        if column not in {"kill", "hold"}:
            raise ProfileRefused("store", "unknown stop column")
        with self._txn() as conn:
            stored = self._profile_row(conn, profile_id)
            self._require_actor(actor, expected=stored.profile.stop_owner)
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

    def _ensure_schema(self) -> None:
        with self._txn() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, SCHEMA_VERSION):
                raise ProfileRefused("store", "profile store version is not admitted")
            for statement in _SCHEMA:
                conn.execute(statement)
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(reservations)").fetchall()
            }
            if "order_ref" not in columns:
                # Legacy v1 store: legacy rows keep NULL and bind once at handoff.
                conn.execute("ALTER TABLE reservations ADD COLUMN order_ref TEXT")
            conn.execute("PRAGMA user_version = 1")

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
        if code == "kill_active":
            raise ProfileRefused("kill_active", "kill is active for this perimeter")
        if code == "hold_active":
            raise ProfileRefused("hold_active", "hold is active for this perimeter")

    def _require_actor(self, actor: str, *, expected: str) -> None:
        if type(actor) is not str or actor != expected:
            raise ProfileRefused("unauthenticated", "actor is not the stop owner")

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise ProfileRefused("store", "profile store transaction failed") from exc
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                try:
                    self._conn.commit()
                except sqlite3.Error as exc:
                    self._conn.rollback()
                    raise ProfileRefused("store", "profile store commit failed") from exc


@dataclass(frozen=True)
class _Stored:
    profile: AuthorityProfile
    revoked: bool

    def __getattr__(self, name: str):
        return getattr(self.profile, name)


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
) -> ProfiledHandoff:
    """Reserve the action, then call ``OrderGateway.submit`` only for paper submit.

    Dry-run and proposal actions stop at the reservation. A non-paper gateway
    transport is refused before any reservation. A replay does not call submit.
    """
    if model_output_grants_authority(model_output):
        raise ProfileRefused("model_authority", "model output cannot grant authority")
    kwargs = dict(submit_kwargs or {})
    order_ref = None
    if action == "submit_paper":
        scope = getattr(gateway, "execution_scope", None)
        if scope != "paper":
            raise ProfileRefused("transport_scope", "paper profile refuses a non-paper transport")
        order = submit_args[0] if submit_args else kwargs.get("order")
        if order is None:
            raise ProfileRefused("order_required", "paper submit requires the gateway order")
        order_account = getattr(order, "account", None)
        profile_account = _profile_account(store, profile_id)
        if order_account != profile_account:
            raise ProfileRefused("account_mismatch", "order account is outside the profile")
        order_notional = getattr(order, "notional", None)
        if order_notional is None or _parse_money(order_notional, code="notional") != _parse_money(
            notional, code="notional"
        ):
            raise ProfileRefused("notional_mismatch", "order notional does not match the reservation")
        order_ref = getattr(order, "id", None)
        if order_ref is None:
            raise ProfileRefused("order_required", "paper submit requires an order id")
        order_ref = str(order_ref)
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
    )
    claim = store.claim_handoff(reservation.client_order_id)
    if claim == "replay":
        return ProfiledHandoff(reservation.client_order_id, False, True, None)
    if claim in {"kill_active", "hold_active"}:
        raise ProfileRefused(claim, "stop is active for this perimeter")
    if claim != "claimed":
        raise ProfileRefused("store", "handoff claim was not admitted")
    if action != "submit_paper":
        return ProfiledHandoff(reservation.client_order_id, False, reservation.replayed, None)
    outcome = gateway.submit(*submit_args, **kwargs)
    return ProfiledHandoff(reservation.client_order_id, True, reservation.replayed, outcome)


def _profile_account(store: AuthorityProfileStore, profile_id: str) -> str:
    with store._txn() as conn:
        row = conn.execute(
            "SELECT account_id FROM profiles WHERE profile_id = ?", (profile_id,)
        ).fetchone()
    if row is None:
        raise ProfileRefused("unknown_profile", "profile is not installed")
    return str(row["account_id"])


def _control_code(control) -> str | None:
    if control is None:
        return None
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
