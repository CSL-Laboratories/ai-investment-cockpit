"""The signed caps and allowlist source: one file, signed with the installer's key, carrying
the operator's approval record.

This module reads and checks the file. It does not size, gate or book anything, and it
is passed to nothing: handing a loaded ``Limits`` to the paper gateway's ``compose()``
is the plugin entry's line (lane COCKPIT-PLUGIN), not this lane's. ``Limits.gateway_caps``
and ``Limits.allowlist`` give plain values in the shapes the paper gateway's ``Caps``
(``max_notional_minor``, ``max_quantity``) and allowlist (a ``frozenset`` of exact ``str``)
take, without importing it.

The file (``cockpit.limits/1``), in canonical bytes (``cockpit.research.canonical``),
optionally followed by one ``\\n``::

    {"document": {
        "schema": "cockpit.limits/1",
        "limits_id": ..., "sequence": <int >= 1>, "issuer_key_id": ...,
        "scope": {"account": ..., "venue": ..., "mode": "paper"},
        "not_before": <ms>, "expires_at": <ms>,
        "currency": "USD",
        "caps": {"max_notional_minor": <int>, "max_quantity": <int>},
        "allowlist": ["SYMBOL", ...],
        "operator_approval": {"approval_id": ..., "approved_by": ..., "approved_at": <ms>}},
     "signature": "<lowercase hex>"}

The signature is over ``SIGNING_PREFIX + canonical(document)``. Which signature scheme
the installer key uses, and where its public half comes from, are not decided in this
lane (questions.md): the caller injects a ``verifier`` with a ``key_id`` and a
``verify(message, signature) -> bool``, captured once at construction. Only an exact
``True`` from it counts; anything else, an exception included, is a refusal.

What is refused, in order: bytes that are not exactly canonical; a file with no
signature (``unsigned``); a malformed signature; a document whose ``issuer_key_id`` is
not the verifier's (``wrong_key``); a signature that does not verify
(``signature_invalid``); any missing, unknown or wrongly typed field; a scope for another
account, venue or mode (``scope_mismatch``); any mode but ``paper``
(``live_not_authorised``: live capital is not authorised); a document outside its window
(``not_yet_valid``, ``expired``); an operator approval dated after ``now``; and replay:
a ``sequence`` lower than the highest one accepted for the scope (``rollback``), or the
same ``sequence`` with a different document (``sequence_conflict``). The highest sequence
is kept in a high-water store; ``FileHighWater`` keeps it in an owner-only file that the
installer creates (``create_high_water``) and that is never re-created when missing, so
deleting it does not reset replay protection.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass
from typing import Protocol

from cockpit.research.canonical import MAX_INT, canonical, parse
from cockpit.research.errors import Refused

__all__ = ["HIGH_WATER_SCHEMA", "LIMITS_SCHEMA", "SIGNING_PREFIX", "FileHighWater", "Limits",
           "LimitsSource", "MemoryHighWater", "Verifier", "create_high_water", "signing_input"]

LIMITS_SCHEMA = "cockpit.limits/1"
HIGH_WATER_SCHEMA = "cockpit.limits_high_water/1"
SIGNING_PREFIX = b"cockpit.limits/1\n"
MAX_FILE = 256 * 1024
MAX_ALLOWLIST = 1_000
PAPER = "paper"

_DOC_FIELDS = frozenset({"schema", "limits_id", "sequence", "issuer_key_id", "scope", "not_before",
                         "expires_at", "currency", "caps", "allowlist", "operator_approval"})
_SCOPE_FIELDS = frozenset({"account", "venue", "mode"})
_CAP_FIELDS = frozenset({"max_notional_minor", "max_quantity"})
_APPROVAL_FIELDS = frozenset({"approval_id", "approved_by", "approved_at"})
_ID = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")
_ACCOUNT = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_VENUE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_MODE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_PRINCIPAL = re.compile(r"[a-z0-9][a-z0-9_.:@-]{0,127}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_SIG_HEX = re.compile(r"(?:[0-9a-f]{2}){1,512}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

_POSIX = hasattr(os, "getuid")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class Verifier(Protocol):
    key_id: str

    def verify(self, message: bytes, signature: bytes) -> bool: ...


def signing_input(document: object) -> bytes:
    """The exact bytes the installer signs for ``document``."""
    return SIGNING_PREFIX + canonical(document)


@dataclass(frozen=True, slots=True)
class Limits:
    limits_id: str
    sequence: int
    account: str
    venue: str
    mode: str
    not_before: int
    expires_at: int
    currency: str
    max_notional_minor: int
    max_quantity: int
    allowlist: frozenset
    approval_id: str
    approved_by: str
    approved_at: int
    digest: str  # of the signed document, in DIGEST_FORM

    def allows(self, instrument: object) -> bool:
        """True only for an exact ``str`` on the allowlist."""
        return type(instrument) is str and instrument in self.allowlist

    def is_current(self, now_ms: object) -> bool:
        return type(now_ms) is int and self.not_before <= now_ms < self.expires_at

    def gateway_caps(self) -> dict:
        """The two caps by the names the paper gateway's ``Caps`` uses."""
        return {"max_notional_minor": self.max_notional_minor, "max_quantity": self.max_quantity}


def _exact(value: object, fields: frozenset, where: str) -> dict:
    if type(value) is not dict:
        raise Refused("wrong_type", where)
    for key in value:
        if key not in fields:
            raise Refused("unknown_field", f"{where}.{key}" if key.isprintable() else where)
    for key in sorted(fields):
        if key not in value:
            raise Refused("missing_field", f"{where}.{key}")
    return value


def _str(value: object, pattern: re.Pattern, where: str) -> str:
    if type(value) is not str:
        raise Refused("wrong_type", where)
    if not pattern.fullmatch(value):
        raise Refused("invalid_value", where)
    return value


def _int(value: object, low: int, high: int, where: str) -> int:
    if type(value) is not int:
        raise Refused("wrong_type", where)
    if not low <= value <= high:
        raise Refused("out_of_range", where)
    return value


def _scope_key(account: str, venue: str, mode: str) -> str:
    return f"{account}|{venue}|{mode}"


def _document(doc: dict) -> dict:
    """Field rules of a signed document; returns the values ``Limits`` is built from."""
    doc = _exact(doc, _DOC_FIELDS, "document")
    if type(doc["schema"]) is not str or doc["schema"] != LIMITS_SCHEMA:
        raise Refused("schema_mismatch", "document")
    out = {
        "limits_id": _str(doc["limits_id"], _ID, "limits_id"),
        "sequence": _int(doc["sequence"], 1, MAX_INT, "sequence"),
    }
    scope = _exact(doc["scope"], _SCOPE_FIELDS, "scope")
    out["account"] = _str(scope["account"], _ACCOUNT, "scope.account")
    out["venue"] = _str(scope["venue"], _VENUE, "scope.venue")
    out["mode"] = _str(scope["mode"], _MODE, "scope.mode")
    out["not_before"] = _int(doc["not_before"], 0, MAX_INT, "not_before")
    out["expires_at"] = _int(doc["expires_at"], 0, MAX_INT, "expires_at")
    if out["expires_at"] <= out["not_before"]:
        raise Refused("invalid_value", "expires_at is not after not_before")
    out["currency"] = _str(doc["currency"], _CURRENCY, "currency")
    caps = _exact(doc["caps"], _CAP_FIELDS, "caps")
    out["max_notional_minor"] = _int(caps["max_notional_minor"], 1, MAX_INT, "caps.max_notional_minor")
    out["max_quantity"] = _int(caps["max_quantity"], 1, MAX_INT, "caps.max_quantity")
    allow = doc["allowlist"]
    if type(allow) is not list:
        raise Refused("wrong_type", "allowlist")
    if not 1 <= len(allow) <= MAX_ALLOWLIST:
        raise Refused("invalid_length", "allowlist")
    seen = set()
    for item in allow:
        item = _str(item, _INSTRUMENT, "allowlist[]")
        if item in seen:
            raise Refused("duplicate_value", "allowlist[]")
        seen.add(item)
    out["allowlist"] = frozenset(seen)
    approval = _exact(doc["operator_approval"], _APPROVAL_FIELDS, "operator_approval")
    out["approval_id"] = _str(approval["approval_id"], _ID, "operator_approval.approval_id")
    out["approved_by"] = _str(approval["approved_by"], _PRINCIPAL, "operator_approval.approved_by")
    out["approved_at"] = _int(approval["approved_at"], 0, MAX_INT, "operator_approval.approved_at")
    return out


class MemoryHighWater:
    """Replay floor kept in memory: enough for one process, gone at restart."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict = {}

    def get(self, key: str):
        with self._lock:
            return self._entries.get(key)

    def advance(self, key: str, sequence: int, digest: str) -> None:
        with self._lock:
            _advance_check(self._entries.get(key), sequence, digest)
            self._entries[key] = (sequence, digest)


def _advance_check(current, sequence: int, digest: str) -> None:
    if current is None:
        return
    seq, dig = current
    if sequence < seq:
        raise Refused("rollback")
    if sequence == seq and digest != dig:
        raise Refused("sequence_conflict")


def create_high_water(path: str) -> None:
    """The installer's step: create an empty, owner-only high-water file. Refuses if it exists."""
    if type(path) is not str or not path:
        raise Refused("path_invalid")
    raw = canonical({"schema": HIGH_WATER_SCHEMA, "entries": {}})
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
    except FileExistsError:
        raise Refused("high_water_exists") from None
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


class FileHighWater:
    """Replay floor in an owner-only file the installer made. A missing file refuses."""

    def __init__(self, path: str) -> None:
        if type(path) is not str or not path:
            raise Refused("path_invalid")
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            fd = os.open(self._path, os.O_RDONLY | _NOFOLLOW | _BINARY | _CLOEXEC)
        except FileNotFoundError:
            raise Refused("high_water_missing") from None
        except OSError:
            raise Refused("high_water_invalid") from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
                raise Refused("high_water_invalid")
            if _POSIX and (st.st_uid != os.getuid() or st.st_mode & 0o077):
                raise Refused("high_water_not_owner_only")
            raw = os.read(fd, MAX_FILE + 1)
        finally:
            os.close(fd)
        try:
            value = parse(raw)
        except Refused:
            raise Refused("high_water_invalid") from None
        if type(value) is not dict or set(value) != {"schema", "entries"} \
                or value["schema"] != HIGH_WATER_SCHEMA or type(value["entries"]) is not dict:
            raise Refused("high_water_invalid")
        entries = {}
        for key, entry in value["entries"].items():
            if type(entry) is not dict or set(entry) != {"sequence", "digest"} \
                    or type(entry["sequence"]) is not int or not 1 <= entry["sequence"] <= MAX_INT \
                    or type(entry["digest"]) is not str or not _DIGEST.fullmatch(entry["digest"]):
                raise Refused("high_water_invalid")
            entries[key] = (entry["sequence"], entry["digest"])
        return entries

    def get(self, key: str):
        with self._lock:
            return self._read().get(key)

    def advance(self, key: str, sequence: int, digest: str) -> None:
        with self._lock:
            entries = self._read()
            _advance_check(entries.get(key), sequence, digest)
            if entries.get(key) == (sequence, digest):
                return
            entries[key] = (sequence, digest)
            raw = canonical({"schema": HIGH_WATER_SCHEMA, "entries": {
                k: {"sequence": s, "digest": d} for k, (s, d) in entries.items()}})
            directory = os.path.dirname(os.path.abspath(self._path))
            tmp = os.path.join(directory, f".{os.path.basename(self._path)}.{secrets.token_hex(8)}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
            try:
                try:
                    os.write(fd, raw)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            if _POSIX:
                dfd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)


class LimitsSource:
    """Loads the signed caps and allowlist for exactly one scope (account, venue, mode)."""

    def __init__(self, *, verifier: Verifier, account: str, venue: str, mode: str, high_water) -> None:
        self._account = _str(account, _ACCOUNT, "account")
        self._venue = _str(venue, _VENUE, "venue")
        self._mode = _str(mode, _MODE, "mode")
        if self._mode != PAPER:
            raise Refused("live_not_authorised")
        key_id = getattr(verifier, "key_id", None)
        verify = getattr(verifier, "verify", None)
        if type(key_id) is not str or not _ID.fullmatch(key_id) or not callable(verify):
            raise Refused("verifier_invalid")
        if not callable(getattr(high_water, "get", None)) or not callable(getattr(high_water, "advance", None)):
            raise Refused("high_water_invalid")
        self._key_id = key_id
        self._verify = verify
        self._high_water = high_water
        self._scope_key = _scope_key(self._account, self._venue, self._mode)

    def load(self, raw: bytes, *, now_ms: int) -> Limits:
        if type(now_ms) is not int or not 0 <= now_ms <= MAX_INT:
            raise Refused("clock_invalid")
        if type(raw) is not bytes:
            raise Refused("not_bytes")
        if len(raw) > MAX_FILE:
            raise Refused("too_large")
        body = raw[:-1] if raw.endswith(b"\n") else raw
        envelope = parse(body)
        if type(envelope) is not dict:
            raise Refused("envelope_invalid")
        if "signature" not in envelope:
            raise Refused("unsigned")
        if set(envelope) != {"document", "signature"}:
            raise Refused("envelope_invalid")
        signature = envelope["signature"]
        if type(signature) is not str or not _SIG_HEX.fullmatch(signature):
            raise Refused("signature_malformed")
        document = envelope["document"]
        if type(document) is not dict:
            raise Refused("envelope_invalid")
        issuer = document.get("issuer_key_id")
        if type(issuer) is not str or issuer != self._key_id:
            raise Refused("wrong_key")
        message = signing_input(document)
        try:
            ok = self._verify(message, bytes.fromhex(signature))
        except Exception:
            ok = False
        if ok is not True:
            raise Refused("signature_invalid")
        fields = _document(document)
        if (fields["account"], fields["venue"], fields["mode"]) != (self._account, self._venue, self._mode):
            raise Refused("scope_mismatch")
        if fields["mode"] != PAPER:
            raise Refused("live_not_authorised")
        if now_ms < fields["not_before"]:
            raise Refused("not_yet_valid")
        if now_ms >= fields["expires_at"]:
            raise Refused("expired")
        if fields["approved_at"] > now_ms:
            raise Refused("invalid_value", "operator_approval.approved_at is in the future")
        digest = "sha256:" + hashlib.sha256(canonical(document)).hexdigest()
        self._high_water.advance(self._scope_key, fields["sequence"], digest)
        return Limits(digest=digest, **fields)

