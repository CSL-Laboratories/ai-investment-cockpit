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
(``live_not_authorised``: live capital is not authorised, checked before the scope so a
live document is named as such); a currency other than ``USD`` (``currency_unsupported``:
the caps are the paper gateway's minor units); a document outside its window
(``not_yet_valid``, ``expired``); an operator approval dated after ``now``; and replay:
a ``sequence`` lower than the highest one accepted for the scope (``rollback``), or the
same ``sequence`` with a different document (``sequence_conflict``). The highest sequence
is kept in a high-water store; ``FileHighWater`` keeps it in an owner-only file that the
installer creates (``create_high_water``) beside an owner-only lock file. Every floor
transaction (read, check, write, replace) runs under an ``flock`` on the lock file, which
is never replaced, so two loaders in two processes cannot both accept one sequence or
move the floor back. A new floor is written in full (short writes completed) to a
temporary file, synced, and moved over the old one, so a failed write keeps the previous
floor; a floor that would outgrow what its reader reads is refused before it is written
(``high_water_full``); a failed write or sync is ``high_water_unwritable``, never a raw
``OSError``. Neither file is re-created when missing (deleting the floor alone does not
reset it). Without POSIX owners and ``fcntl`` (native Windows) the file floor refuses
(``platform_unsupported``) rather than run unprotected.
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

try:  # POSIX advisory lock: one floor transaction across processes; absent on Windows
    import fcntl
except ImportError:  # pragma: no cover - platform without fcntl
    fcntl = None

from cockpit.research.canonical import MAX_INT, canonical, parse
from cockpit.research.errors import Refused

__all__ = ["HIGH_WATER_SCHEMA", "LIMITS_SCHEMA", "SIGNING_PREFIX", "FileHighWater", "Limits",
           "LimitsSource", "MemoryHighWater", "Verifier", "create_high_water", "signing_input"]

LIMITS_SCHEMA = "cockpit.limits/1"
HIGH_WATER_SCHEMA = "cockpit.limits_high_water/1"
SIGNING_PREFIX = b"cockpit.limits/1\n"
MAX_FILE = 256 * 1024
CURRENCY = "USD"  # the paper gateway's minor units; any other currency is refused
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


def _require_platform() -> None:
    """Owner checks and the process lock are this floor's contract; without them, refuse."""
    if not _POSIX or fcntl is None:
        raise Refused("platform_unsupported", "needs POSIX owners and fcntl")


def _fsync_file(fd: int) -> None:
    os.fsync(fd)


def _fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY | _CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("no progress writing")
        view = view[n:]


def _read_bounded(fd: int, limit: int) -> bytes:
    """Read up to ``limit + 1`` bytes, so a caller can tell 'more than limit'."""
    chunks, total = [], 0
    while total <= limit:
        chunk = os.read(fd, min(1 << 20, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _floor_bytes(entries: dict) -> bytes:
    return canonical({"schema": HIGH_WATER_SCHEMA, "entries": {
        k: {"sequence": s, "digest": d} for k, (s, d) in entries.items()}})


def _open_lock(path: str) -> int:
    """Open the floor's lock file, which is never replaced, and check it is owner-only."""
    try:
        fd = os.open(path + ".lock", os.O_RDWR | _NOFOLLOW | _BINARY | _CLOEXEC)
    except FileNotFoundError:
        raise Refused("high_water_missing", "lock file") from None
    except OSError:
        raise Refused("high_water_invalid", "lock file") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refused("high_water_invalid", "lock file")
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise Refused("high_water_not_owner_only", "lock file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_high_water(path: str) -> None:
    """The installer's step: create an empty, owner-only floor and its lock file.

    Refuses if either exists, so deleting the floor alone and calling this again does not
    reset it (deleting both does: same-uid code is outside this file's protection)."""
    _require_platform()
    if type(path) is not str or not path:
        raise Refused("path_invalid")
    for existing in (path,):  # the lock file is refused below by O_EXCL
        try:
            st = os.lstat(existing)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise Refused("high_water_unwritable", exc.strerror or "") from None
        if not stat.S_ISREG(st.st_mode):
            raise Refused("path_invalid")
        raise Refused("high_water_exists")
    try:
        lock = os.open(path + ".lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
    except FileExistsError:
        raise Refused("high_water_exists") from None
    except OSError as exc:
        raise Refused("high_water_unwritable", exc.strerror or "") from None
    created = False
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _fsync_file(lock)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
        try:
            _write_all(fd, _floor_bytes({}))
            _fsync_file(fd)
        finally:
            os.close(fd)
        _fsync_dir(os.path.dirname(os.path.abspath(path)))
        created = True
    except OSError as exc:
        raise Refused("high_water_unwritable", exc.strerror or "") from None
    finally:
        if not created:
            for leftover in (path, path + ".lock"):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
        os.close(lock)


class FileHighWater:
    """Replay floor in an owner-only file the installer made. A missing file refuses.

    Every ``get`` and ``advance`` is one transaction under an ``flock`` on ``<path>.lock``, a
    file that is never replaced, so instances in this process and in other processes see
    one floor: it never goes back, and an update for one scope never drops another's."""

    def __init__(self, path: str) -> None:
        _require_platform()
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
            if not stat.S_ISREG(st.st_mode):
                raise Refused("high_water_invalid")
            if st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise Refused("high_water_not_owner_only")
            raw = _read_bounded(fd, MAX_FILE)
        finally:
            os.close(fd)
        if len(raw) > MAX_FILE:
            raise Refused("high_water_invalid", "too large")
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
        _require_platform()
        with self._lock:
            lock = _open_lock(self._path)
            try:
                fcntl.flock(lock, fcntl.LOCK_SH)
                return self._read().get(key)
            finally:
                os.close(lock)

    def advance(self, key: str, sequence: int, digest: str) -> None:
        _require_platform()
        with self._lock:
            lock = _open_lock(self._path)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)  # the whole read, check and replace runs under it
                entries = self._read()
                _advance_check(entries.get(key), sequence, digest)
                if entries.get(key) == (sequence, digest):
                    return
                entries[key] = (sequence, digest)
                raw = _floor_bytes(entries)
                if len(raw) > MAX_FILE:
                    raise Refused("high_water_full")
                self._replace(raw)
            finally:
                os.close(lock)

    def _replace(self, raw: bytes) -> None:
        """Write the new floor beside the old one and move it over; the old floor stays
        whole until the move, so a failed write leaves the previous floor in force."""
        directory = os.path.dirname(os.path.abspath(self._path))
        tmp = os.path.join(directory, f".{os.path.basename(self._path)}.{secrets.token_hex(8)}.tmp")
        replaced = False
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
            try:
                _write_all(fd, raw)
                _fsync_file(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self._path)
            replaced = True
            _fsync_dir(directory)
        except OSError as exc:
            if not replaced:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise Refused("high_water_unwritable", exc.strerror or "") from None


class LimitsSource:
    """Loads the signed caps and allowlist for exactly one scope (account, venue, mode)."""

    def __init__(self, *, verifier: Verifier, account: str, venue: str, mode: str, high_water) -> None:
        self._account = _str(account, _ACCOUNT, "account")
        self._venue = _str(venue, _VENUE, "venue")
        self._mode = _str(mode, _MODE, "mode")
        if self._mode != PAPER:
            raise Refused("live_not_authorised")
        try:
            key_id = getattr(verifier, "key_id", None)
            verify = getattr(verifier, "verify", None)
        except Exception:
            raise Refused("verifier_invalid") from None
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
        if fields["mode"] != PAPER:
            raise Refused("live_not_authorised")
        if (fields["account"], fields["venue"], fields["mode"]) != (self._account, self._venue, self._mode):
            raise Refused("scope_mismatch")
        if fields["currency"] != CURRENCY:
            raise Refused("currency_unsupported")
        if now_ms < fields["not_before"]:
            raise Refused("not_yet_valid")
        if now_ms >= fields["expires_at"]:
            raise Refused("expired")
        if fields["approved_at"] > now_ms:
            raise Refused("invalid_value", "operator_approval.approved_at is in the future")
        digest = "sha256:" + hashlib.sha256(canonical(document)).hexdigest()
        self._high_water.advance(self._scope_key, fields["sequence"], digest)
        return Limits(digest=digest, **fields)
