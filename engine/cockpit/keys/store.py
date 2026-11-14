"""Where Cockpit keeps a key (CSL-59, candidate; CSLR-D-011; PRODUCT.md 055cc7a5, A44, 3.8 and 5.4).

Lucas, Nov 8: "Owner-only file" (a host with no OS keychain keeps saved API keys in an owner-only file
rather than refusing them), and for Cockpit bar 2, "Yes, model keys only": the owner-only file is
allowed for user-entered model-provider keys only; broker and venue keys stay keychain-only.

``KeyStore`` decides one key at a time and fails closed:

* a ``model_provider`` key goes in the OS keyring when the host has one, and otherwise in one
  owner-only file (``0600`` file in a ``0700`` directory, both owned by the running user, neither a
  symlink). Every directory from ``/`` to that directory is root-owned or the running user's, and
  no other user can rename an entry along that chain (a root-owned sticky directory such as ``/tmp``
  may be world-writable; same rule as Recursion ``_owned_chain``). Each component is opened with
  ``O_DIRECTORY|O_NOFOLLOW`` and the file is read and replaced through that directory fd;
* saves and deletes take an exclusive ``fcntl.flock`` on ``.keys.lock`` in that directory around the
  read-modify-write, so one save cannot drop a key another save just wrote;
* a ``broker`` or ``venue`` key goes only in the OS keyring; with no keyring it is refused, never
  written to a file;
* an existing key file, lock or directory that is not owner-only is refused on read and on write,
  and is never repaired silently; a key file, lock or keychain record that is not a regular file (a
  pipe, socket, device or folder) is refused without being opened (B29, ``_open_existing_regular``);
* the first save creates the missing key folder (the leaf) ``0700`` after every ancestor passed the
  check. When the store is given ``config_home`` (``store_for_host`` does so only for the default
  file ``~/.config/csl-cockpit/model-keys.json``) and ``<config_home>/.config`` is missing, the first
  save also creates that ``.config`` ``0700``, but only when the home folder is a directory owned by
  the running user and not group- or world-writable; every component is still opened with
  ``O_NOFOLLOW``, so nothing on the path may be a symlink. Nothing else is ever created, and an
  entry that exists, or that a competing process creates first, must pass the same checks as any
  existing one. Before that first save, ``get`` returns ``None`` and ``delete`` does nothing.

Keychain record (B24). Lucas, Nov 13, 2026, asked what Cockpit should do when a later run selects a
different OS keychain than the one it saved a broker or venue key in: "Remember and refuse
(Recommended)". On a keychain host the first broker or venue save writes ``.keychain.json`` in the key
file's folder, under the key file's own rules (the owned chain, ``0600`` in ``0700``, no link followed,
replaced atomically under the lock, never repaired). It holds only Cockpit's own name for the keychain
(``{"version": 1, "keychain": "SecretService Keyring"}``, plus ``"previous"`` after an accepted switch),
never a key. With it, Cockpit writes one non-secret marker into that keychain (account
``cockpit-keychain-record``), so a record deleted later is not taken for "never recorded". Broker and
venue ``get``, ``put`` and ``delete`` are then refused, and nothing is re-recorded, when the record names
another keychain (``keychain_differs``), when the keychain holds the marker but the record is gone
(``keychain_record_missing``), or when the record is malformed, unreadable, a link or not owner-only. The
way out is named in each refusal: make the recorded keychain available again, or record the change with
``accept_keychain`` (``python -m cockpit.keys accept-keychain "<name>"`` at a terminal). With no record and
no marker (keys saved before this rule, or none yet) there is nothing to compare, and the next broker or
venue save records the keychain selected then. Model-provider keys are neither recorded nor refused. No
record is kept where this module has no owner-only file (Windows, where only the Windows vault can be
selected).

The keyring is injected (``get``/``set``/``delete``); this module imports no keyring library and makes
no provider, venue or network call. Error messages name the key, never its value. Frozen ``app/`` keeps
its own keyring code and is untouched. This module does not place orders; paper-only remains
PRODUCT.md 5.2 / CP0-v2.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol

try:
    import fcntl
except ImportError:  # Windows has no fcntl; backend_for refuses the file there
    fcntl = None  # type: ignore[assignment]

__all__ = ["KEY_KINDS", "RECORD_KINDS", "KeyRefused", "KeyStore", "Keyring", "key_file_state",
           "keychain_record_state", "record_way_out"]

KEY_KINDS = ("model_provider", "broker", "venue")
FILE_KINDS = ("model_provider",)
# A key name, matched against the whole string only (``fullmatch``): ``$`` with ``match`` also accepts a
# name that ends in a line break (R6 finding 10(d); B23, Lucas, Nov 13, 2026: "Fix before landing").
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_FILE_VERSION = 1  # compared as an int that is not a bool: ``True == 1`` and ``1.0 == 1`` are both true
_LOCK_NAME = ".keys.lock"
# B24: the keychain record beside the key file, and the marker Cockpit leaves in the recorded keychain.
RECORD_KINDS = ("broker", "venue")
_RECORD_NAME = ".keychain.json"
_RECORD_VERSION = 1
_RECORD_LIMIT = 4096  # bytes; a record is a few dozen
_RECORD_FIELDS = frozenset(("version", "keychain", "previous"))
_MARKER_ACCOUNT = "cockpit-keychain-record"  # has no ":", so it is never a key's account ("kind:name")


class KeyRefused(Exception):
    code = "key_refused"

    def __init__(self, cause: str, detail: str = "") -> None:
        super().__init__(cause + (f" ({detail})" if detail else ""))
        self.cause, self.detail = cause, detail


class Keyring(Protocol):
    def get(self, service: str, name: str) -> str | None: ...
    def set(self, service: str, name: str, secret: str) -> None: ...
    def delete(self, service: str, name: str) -> None: ...


class KeyStore:
    SERVICE = "csl-cockpit"

    def __init__(self, *, keyring: Keyring | None, key_file: str | os.PathLike[str] | None = None,
                 config_home: str | os.PathLike[str] | None = None, keychain: str | None = None,
                 keychain_names: tuple[str, ...] = ()) -> None:
        self._keyring = keyring
        self._path = os.fspath(key_file) if key_file is not None else None
        # The home folder whose missing ``.config`` the first save may create (see module docstring).
        self._config_home = os.fspath(config_home) if config_home is not None else None
        # B24: Cockpit's name for the selected OS keychain, and every name a record may hold. Given by
        # ``store_for_host``; a store built without it (tests, no production caller) keeps no record.
        if keychain is not None and keychain not in keychain_names:
            raise ValueError("keychain must be one of keychain_names")
        self._keychain = keychain if keyring is not None else None
        self._keychain_names = tuple(keychain_names)

    # -- public ---------------------------------------------------------------------------
    def backend_for(self, kind: str) -> str:
        """``"keyring"`` or ``"owner_only_file"`` for this kind on this host, or raise ``KeyRefused``."""

        kind = _kind(kind)
        if self._keyring is not None:
            return "keyring"
        if kind not in FILE_KINDS:
            raise KeyRefused("keychain_required", f"{kind} keys stay in the OS keychain")
        if os.name == "nt" or fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise KeyRefused("owner_only_file_unsupported", "no owner-only file on this platform")
        if self._path is None:
            raise KeyRefused("key_file_not_configured")
        return "owner_only_file"

    def put(self, kind: str, name: str, secret: str) -> str:
        name = _name(name)
        if not isinstance(secret, str) or not secret or "\x00" in secret:
            raise KeyRefused("secret_malformed", name)
        backend = self.backend_for(kind)
        if backend == "keyring":
            if self._recorded(kind):
                with self._locked_dir(create_leaf=True) as dir_fd:
                    assert dir_fd is not None
                    self._settle_record(dir_fd)
                    self._keyring.set(self.SERVICE, _account(kind, name), secret)  # type: ignore[union-attr]
                return backend
            self._keyring.set(self.SERVICE, _account(kind, name), secret)  # type: ignore[union-attr]
            return backend
        with self._locked_dir(create_leaf=True) as dir_fd:
            keys = self._read_file(dir_fd)
            keys[name] = secret
            self._write_file(dir_fd, keys)
        return backend

    def get(self, kind: str, name: str) -> str | None:
        name = _name(name)
        if self.backend_for(kind) == "keyring":
            if self._recorded(kind):
                self._check_record()
            return self._keyring.get(self.SERVICE, _account(kind, name))  # type: ignore[union-attr]
        assert self._path is not None
        dir_fd = _open_owned_dir(os.path.dirname(os.path.abspath(self._path)), missing_leaf_ok=True,
                                 config_home=self._config_home)
        if dir_fd is None:
            return None
        try:
            _refuse_existing_unless_owner_only(dir_fd, _LOCK_NAME)
            return self._read_file(dir_fd).get(name)
        finally:
            os.close(dir_fd)

    def delete(self, kind: str, name: str) -> None:
        name = _name(name)
        if self.backend_for(kind) == "keyring":
            if self._recorded(kind):
                self._check_record()
            self._keyring.delete(self.SERVICE, _account(kind, name))  # type: ignore[union-attr]
            return
        with self._locked_dir() as dir_fd:
            if dir_fd is None:
                return
            keys = self._read_file(dir_fd)
            if keys.pop(name, None) is not None:
                self._write_file(dir_fd, keys)

    def accept_keychain(self, name: str) -> str:
        """After a real change of OS keychain, record the keychain this run selected as the one broker and
        venue keys are kept in (B24). ``name`` must be exactly Cockpit's name for it. Returns the keychain the
        record named before, or "". A record that is a link, not owner-only or unreadable is refused, as the
        key file is (the user removes it); a malformed one is replaced. The CLI runs this only at a terminal."""

        if self._keychain is None or not _owner_only_supported():
            raise KeyRefused("keychain_record_unsupported", "no OS keychain record is kept on this host")
        if type(name) is not str or name != self._keychain:
            raise KeyRefused("keychain_name_mismatch", f"this run selected {self._keychain}")
        if self._path is None:
            raise KeyRefused("key_file_not_configured", "the keychain record is kept beside the key file")
        with self._locked_dir(create_leaf=True) as dir_fd:
            assert dir_fd is not None
            try:
                recorded = _read_record(dir_fd, self._keychain_names)
            except KeyRefused as exc:
                if exc.cause != "keychain_record_malformed":
                    raise
                recorded = None
            previous = None
            if recorded is not None:
                previous = recorded[0] if recorded[0] != self._keychain else recorded[1]
            _write_record(dir_fd, self._keychain, previous)
            self._keyring.set(self.SERVICE, _MARKER_ACCOUNT, self._keychain)  # type: ignore[union-attr]
        return previous or ""

    # -- keychain record (B24) --------------------------------------------------------------
    def _recorded(self, kind: str) -> bool:
        """Whether this operation is checked against the keychain record: a broker or venue key, on a
        keychain host whose keychain this store was told, where an owner-only file can be kept."""

        if self._keychain is None or kind not in RECORD_KINDS or not _owner_only_supported():
            return False
        if self._path is None:
            raise KeyRefused("key_file_not_configured", "the keychain record is kept beside the key file")
        return True

    def _settle_record(self, dir_fd: int) -> None:
        # Under the lock, before a broker or venue key is saved: the first save records the keychain and
        # leaves the marker in it; any later save must find the same keychain recorded.
        recorded = self._record_or_refuse(dir_fd)
        if recorded is None:
            if self._keyring.get(self.SERVICE, _MARKER_ACCOUNT) is not None:  # type: ignore[union-attr]
                raise self._missing()
            _write_record(dir_fd, self._keychain)  # type: ignore[arg-type]
            self._keyring.set(self.SERVICE, _MARKER_ACCOUNT, self._keychain)  # type: ignore[union-attr]
        elif recorded[0] != self._keychain:
            raise self._differs(recorded[0])

    def _check_record(self) -> None:
        # Before a broker or venue key is read or deleted. Creates nothing.
        try:
            dir_fd = _open_owned_dir(os.path.dirname(os.path.abspath(self._path)),  # type: ignore[arg-type]
                                     missing_leaf_ok=True, config_home=self._config_home)
        except KeyRefused as exc:
            if exc.cause != "key_dir_missing":
                raise
            dir_fd = None  # no folder on the way, so no record
        recorded = None
        if dir_fd is not None:
            try:
                recorded = self._record_or_refuse(dir_fd)
            finally:
                os.close(dir_fd)
        if recorded is None:
            if self._keyring.get(self.SERVICE, _MARKER_ACCOUNT) is not None:  # type: ignore[union-attr]
                raise self._missing()
        elif recorded[0] != self._keychain:
            raise self._differs(recorded[0])

    def _record_or_refuse(self, dir_fd: int) -> tuple[str, str | None] | None:
        try:
            return _read_record(dir_fd, self._keychain_names)
        except KeyRefused as exc:
            raise KeyRefused(exc.cause, f"{exc.detail}; {record_way_out(exc.cause, self._keychain or '', None)}") from None

    def _differs(self, recorded: str) -> KeyRefused:
        return KeyRefused("keychain_differs", f"broker and venue keys were saved in {recorded}, this run selected "
                                              f"{self._keychain}; "
                                              f"{record_way_out('keychain_differs', self._keychain or '', recorded)}")

    def _missing(self) -> KeyRefused:
        return KeyRefused("keychain_record_missing", f"{self._keychain} holds Cockpit's record marker but "
                                                     f"{_RECORD_NAME} is gone; "
                                                     f"{record_way_out('keychain_record_missing', self._keychain or '', None)}")

    # -- owner-only file ------------------------------------------------------------------
    @contextmanager
    def _locked_dir(self, *, create_leaf: bool = False) -> Iterator[int | None]:
        """Directory fd for the key file, held with an exclusive lock on ``.keys.lock``."""

        assert self._path is not None and fcntl is not None
        dir_fd = _open_owned_dir(os.path.dirname(os.path.abspath(self._path)),
                                 create_leaf=create_leaf, missing_leaf_ok=not create_leaf,
                                 config_home=self._config_home)
        if dir_fd is None:
            yield None
            return
        lock_fd = -1
        try:
            lock_fd = _open_lock(dir_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield dir_fd
        finally:
            if lock_fd >= 0:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(lock_fd)
            os.close(dir_fd)

    def _read_file(self, dir_fd: int) -> dict[str, str]:
        _require_final_dir(dir_fd)
        fd = _open_existing_regular(dir_fd, _entry_name(self._path), os.O_RDONLY, "key_file_not_owner_only")
        if fd is None:
            return {}
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise KeyRefused("key_file_not_owner_only", oct(st.st_mode & 0o777))
            raw = fh.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise KeyRefused("key_file_malformed") from None
        if not isinstance(data, Mapping) or not _is_version(data.get("version")) or not isinstance(data.get("keys"), Mapping):
            raise KeyRefused("key_file_malformed")
        if type(data.get("kind")) is not str or data.get("kind") != "model_provider":
            raise KeyRefused("key_file_malformed", "kind")
        keys = data["keys"]
        if not all(isinstance(k, str) and _NAME.fullmatch(k) and isinstance(v, str) for k, v in keys.items()):
            raise KeyRefused("key_file_malformed")
        return dict(keys)

    def _write_file(self, dir_fd: int, keys: Mapping[str, str]) -> None:
        payload = {
            "version": _FILE_VERSION,
            "kind": "model_provider",
            "keys": dict(sorted(keys.items())),
        }
        _replace_owner_only(dir_fd, _entry_name(self._path), json.dumps(payload, indent=1).encode("utf-8"),
                            "key_file_not_owner_only")


def _replace_owner_only(dir_fd: int, name: str, body: bytes, cause: str) -> None:
    """Replace ``name`` in ``dir_fd`` with ``body``: a new ``0600`` file (``O_EXCL|O_NOFOLLOW``), written and
    flushed, renamed over the old one, then the folder flushed. An existing ``name`` that is not an owner-only
    regular file is refused with ``cause`` and left as it is."""

    _require_final_dir(dir_fd)
    _refuse_existing_unless_owner_only(dir_fd, name, cause)
    tmp = f".keys-{os.getpid()}-{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(body)
        while view:
            wrote = os.write(fd, view)
            if wrote <= 0:
                raise OSError(errno.EIO, "short write")
            view = view[wrote:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _unlink_quiet(dir_fd, tmp)
        raise
    os.close(fd)
    try:
        _refuse_existing_unless_owner_only(dir_fd, name, cause)
        os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        _unlink_quiet(dir_fd, tmp)
        raise
    try:
        os.fsync(dir_fd)  # the rename is durable before put/delete returns
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)):
            raise  # a filesystem that cannot fsync a directory is the only exception


def _owner_only_supported() -> bool:
    return (os.name != "nt" and fcntl is not None and hasattr(os, "O_NOFOLLOW")
            and hasattr(os, "O_DIRECTORY"))


def _read_record(dir_fd: int, names: tuple[str, ...]) -> tuple[str, str | None] | None:
    """The keychain record in ``dir_fd`` as ``(keychain, previous or None)``, or None when there is none.

    Opened with ``O_NOFOLLOW`` (a link is refused, never followed) and ``O_NONBLOCK`` (a FIFO cannot stall
    the read; it is then refused as not a regular file). Only an owner-only regular file of at most
    ``_RECORD_LIMIT`` bytes holding exactly ``version`` (the integer 1, not a bool or a float), ``keychain``
    and optionally ``previous``, each name one of ``names`` and the two different, is a record.
    """

    fd = _open_existing_regular(dir_fd, _RECORD_NAME, os.O_RDONLY, "keychain_record_not_owner_only",
                                unreadable="keychain_record_unreadable")
    if fd is None:
        return None
    try:  # the owner and the mode, on the raw fd, before any read
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise KeyRefused("keychain_record_not_owner_only", oct(st.st_mode & 0o777))
        chunks: list[bytes] = []
        size = 0
        while size <= _RECORD_LIMIT:
            chunk = os.read(fd, _RECORD_LIMIT + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b"".join(chunks)
    except OSError as exc:
        raise KeyRefused("keychain_record_unreadable", _errno_name(exc)) from None
    finally:
        os.close(fd)
    if len(raw) > _RECORD_LIMIT:
        raise KeyRefused("keychain_record_malformed", "too large")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (UnicodeDecodeError, ValueError):
        raise KeyRefused("keychain_record_malformed", "not JSON") from None
    if type(data) is not dict or not set(data) <= _RECORD_FIELDS:
        raise KeyRefused("keychain_record_malformed", "unexpected fields")
    if not _is_version(data.get("version"), _RECORD_VERSION):
        raise KeyRefused("keychain_record_malformed", "version")
    keychain, previous = data.get("keychain"), data.get("previous", None)
    if type(keychain) is not str or keychain not in names:
        raise KeyRefused("keychain_record_malformed", "not a keychain Cockpit knows")
    if "previous" in data and (type(previous) is not str or previous not in names or previous == keychain):
        raise KeyRefused("keychain_record_malformed", "previous keychain")
    return keychain, previous


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("a field appears twice")
    return fields


def _write_record(dir_fd: int, keychain: str, previous: str | None = None) -> None:
    payload: dict[str, object] = {"version": _RECORD_VERSION, "keychain": keychain}
    if previous is not None:
        payload["previous"] = previous
    _replace_owner_only(dir_fd, _RECORD_NAME, json.dumps(payload, indent=1).encode("utf-8"),
                        "keychain_record_not_owner_only")


def record_way_out(cause: str, current: str, recorded: str | None) -> str:
    """The step a refusal over the keychain record names (B24). Names only; never a key."""

    accept = f"to keep broker and venue keys in {current}, run at a terminal: python -m cockpit.keys accept-keychain '{current}'"
    if cause == "keychain_differs" and recorded:
        return f"{accept}; or make {recorded} available again"
    if cause in ("keychain_record_not_owner_only", "keychain_record_unreadable"):
        return f"Cockpit never repairs {_RECORD_NAME}: remove it yourself, then {accept}"
    return accept


def keychain_record_state(path: str, names: tuple[str, ...]) -> dict[str, str]:
    """Doctor view of the keychain record beside the key file at ``path`` (B24): ``state`` is ``none``, ``ok``
    (with ``keychain`` and, after an accepted switch, ``previous``) or a refusal cause (with ``detail``).
    Read-only: creates nothing and never opens the keychain, so a record missing while the keychain holds
    Cockpit's marker reads ``none`` here; the refusal itself names that case."""

    if not _owner_only_supported():
        return {"state": "none"}
    try:
        fd = _open_owned_dir(os.path.dirname(os.path.abspath(path)))
    except KeyRefused as exc:
        return {"state": "none"} if exc.cause == "key_dir_missing" else {"state": exc.cause, "detail": exc.detail}
    try:
        recorded = _read_record(fd, names)
    except KeyRefused as exc:
        return {"state": exc.cause, "detail": exc.detail}
    finally:
        os.close(fd)
    if recorded is None:
        return {"state": "none"}
    out = {"state": "ok", "keychain": recorded[0]}
    if recorded[1] is not None:
        out["previous"] = recorded[1]
    return out


def key_file_state(path: str) -> str:
    """Doctor view of the owner-only file. Read-only: creates nothing, returns no key value."""

    parent = os.path.dirname(os.path.abspath(path))
    try:
        fd = _open_owned_dir(parent)
    except KeyRefused as exc:
        if exc.cause == "key_dir_missing":
            return "dir_missing"
        if exc.detail.startswith("0o"):
            return f"dir_not_owner_only({exc.detail})"
        return "dir_not_owner_only"
    try:
        try:
            st = os.lstat(_entry_name(path), dir_fd=fd)
        except FileNotFoundError:
            return "no_file_yet"
        except OSError:
            return "file_not_owner_only"
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            return f"file_not_owner_only({oct(st.st_mode & 0o777)})"
        try:
            lock = os.lstat(_LOCK_NAME, dir_fd=fd)
        except FileNotFoundError:
            return "owner_only"
        except OSError:
            return "file_not_owner_only(lock)"
        if not stat.S_ISREG(lock.st_mode) or lock.st_uid != os.geteuid() or lock.st_mode & 0o077:
            return f"file_not_owner_only(lock {oct(lock.st_mode & 0o777)})"
        return "owner_only"
    finally:
        os.close(fd)


def _open_owned_dir(directory: str, *, create_leaf: bool = False,
                    missing_leaf_ok: bool = False, config_home: str | None = None) -> int | None:
    """Open ``directory`` and return its fd, after the owned-chain rule. Caller closes it.

    Ancestors follow Recursion's ``_owned_chain``: each is root's or ours, and not writable by
    anyone else, except a root-owned sticky directory. The directory itself must be a real
    ``0700`` directory owned by the running user. ``O_NOFOLLOW`` on every component means a
    symlink, including one swapped in above the directory, is refused instead of followed.

    ``create_leaf`` creates a missing ``directory``; ``missing_leaf_ok`` returns ``None`` for it.
    When ``directory``'s parent is exactly ``<config_home>/.config`` and that ``.config`` is
    missing, the same two flags apply to it too, but only after ``_require_home`` has passed on
    the pinned fd of the home folder. Nothing above ``.config`` is ever created.
    """

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise KeyRefused("owner_only_file_unsupported", "no owner-only file on this platform")
    directory = os.path.abspath(directory)
    names = [part for part in directory.split(os.sep) if part not in ("", ".")]
    if any(part == ".." for part in names):
        raise KeyRefused("key_dir_not_owner_only", "bad component")
    parent = len(names) - 2  # index of ``.config`` in ``names`` when the B14 rule applies
    may_init_parent = (config_home is not None and parent >= 0 and (create_leaf or missing_leaf_ok)
                       and os.path.dirname(directory) == os.path.join(os.path.abspath(config_home), ".config"))
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(os.sep, flags)
    except OSError as exc:
        raise KeyRefused("key_dir_not_owner_only", _errno_name(exc)) from None
    try:
        if not names:
            _require_final_dir(fd)
            return fd
        _require_ancestor(fd)
        for index, name in enumerate(names):
            try:
                nxt = os.open(name, flags | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if index == parent and may_init_parent:
                    # ``fd`` is the home folder, reached and checked through pinned fds.
                    _require_home(fd, directory)
                    if not create_leaf:
                        os.close(fd)
                        return None
                    nxt = _make_dir(fd, name, flags)
                elif index != len(names) - 1:
                    raise KeyRefused("key_dir_missing", directory) from None
                elif create_leaf:
                    # All ancestors were checked through pinned directory fds.
                    nxt = _make_dir(fd, name, flags)
                elif missing_leaf_ok:
                    os.close(fd)
                    return None
                else:
                    raise KeyRefused("key_dir_missing", directory) from None
            except OSError as exc:
                raise KeyRefused("key_dir_not_owner_only", _errno_name(exc)) from None
            os.close(fd)
            fd = nxt
            if index == len(names) - 1:
                _require_final_dir(fd)
            else:
                _require_ancestor(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _make_dir(parent_fd: int, name: str, flags: int) -> int:
    """Create directory ``name`` (``0700``) in ``parent_fd`` and open it. Caller checks the result.

    Only creates; never chmods or repairs an existing entry. A directory that a competing process
    created first is opened the same way and must pass the caller's checks, like any existing one.
    """

    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise KeyRefused("key_dir_not_owner_only", _errno_name(exc)) from None
    try:
        return os.open(name, flags | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise KeyRefused("key_dir_not_owner_only", _errno_name(exc)) from None


def _require_home(fd: int, directory: str) -> None:
    # B14: ``.config`` is created only in a home folder that is the running user's own and that no
    # one else can write to. A root-owned or shared home, even a sticky one such as /tmp, gets nothing.
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise KeyRefused("key_dir_missing", f"{directory}; not created: home folder is not the user's own "
                                            f"or is writable by others ({oct(st.st_mode & 0o777)})")


def _require_ancestor(fd: int) -> None:
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, os.geteuid()):
        raise KeyRefused("key_dir_not_owner_only", oct(st.st_mode & 0o777))
    if st.st_mode & 0o022 and not (st.st_uid == 0 and st.st_mode & stat.S_ISVTX):
        raise KeyRefused("key_dir_not_owner_only", oct(st.st_mode & 0o777))


def _require_final_dir(fd: int) -> None:
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise KeyRefused("key_dir_not_owner_only", oct(st.st_mode & 0o777))


def _open_lock(dir_fd: int) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    created = False
    try:
        try:
            fd = os.open(_LOCK_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
            created = True
        except FileExistsError:
            existing = _open_existing_regular(dir_fd, _LOCK_NAME, os.O_RDWR, "key_file_not_owner_only")
            if existing is None:  # removed between the two opens
                raise KeyRefused("key_file_not_owner_only", "ENOENT") from None
            fd = existing
    except OSError as exc:
        raise KeyRefused("key_file_not_owner_only", _errno_name(exc)) from None
    try:
        if created:
            os.fchmod(fd, 0o600)
        st = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        if created:
            _unlink_quiet(dir_fd, _LOCK_NAME)
        raise KeyRefused("key_file_not_owner_only", _errno_name(exc)) from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        os.close(fd)
        if created:
            _unlink_quiet(dir_fd, _LOCK_NAME)
        raise KeyRefused("key_file_not_owner_only", oct(st.st_mode & 0o777))
    return fd


_KIND_NAMES = ((stat.S_ISFIFO, "a pipe"), (stat.S_ISDIR, "a folder"), (stat.S_ISSOCK, "a socket"),
               (stat.S_ISCHR, "a device"), (stat.S_ISBLK, "a device"))


def _open_existing_regular(dir_fd: int, name: str, flags: int, cause: str, unreadable: str | None = None) -> int | None:
    """Open ``name`` in ``dir_fd``, an entry this store did not just create, only if it is a regular file;
    None when there is none. Caller closes the fd and still checks the owner and the mode.

    B29 (Lucas, Nov 13, 2026: "Fix it in #330 (Recommended)"): decided from ``lstat`` before any open, so a
    pipe, socket, device or folder there is refused without being opened (an open can wait on a pipe, or
    act on a device). The open adds ``O_NOFOLLOW|O_NONBLOCK``, so an entry swapped in after the check cannot
    make it wait, and ``fstat`` of the open file must show the same regular file the check saw. A link is
    refused as ``ELOOP``, as the no-follow open always answered; other errors give ``unreadable``.
    """

    unreadable = unreadable or cause
    try:
        before = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise KeyRefused(unreadable, _errno_name(exc)) from None
    if stat.S_ISLNK(before.st_mode):
        raise KeyRefused(cause, "ELOOP")
    if not stat.S_ISREG(before.st_mode):
        raise KeyRefused(cause, _not_regular(before.st_mode))
    try:
        fd = os.open(name, flags | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
                     dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise KeyRefused(cause, "ELOOP") from None
        raise KeyRefused(unreadable, _errno_name(exc)) from None
    try:
        st = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        raise KeyRefused(unreadable, _errno_name(exc)) from None
    if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
        os.close(fd)
        raise KeyRefused(cause, _not_regular(st.st_mode))
    return fd


def _not_regular(mode: int) -> str:
    kind = next((name for test, name in _KIND_NAMES if test(mode)), "not the file that was checked")
    return f"{oct(mode & 0o777)}, {kind}"


def _refuse_existing_unless_owner_only(dir_fd: int, name: str, cause: str = "key_file_not_owner_only") -> None:
    try:
        st = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise KeyRefused(cause, oct(st.st_mode & 0o777))


def _unlink_quiet(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _entry_name(path: str | None) -> str:
    if path is None:
        raise KeyRefused("key_file_not_configured")
    name = os.path.basename(os.path.abspath(path))
    if name in ("", ".", "..", _LOCK_NAME, _RECORD_NAME):  # the key file never shares a name with these
        raise KeyRefused("key_file_not_configured")
    return name


def _errno_name(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, type(exc).__name__)


def _kind(kind: object) -> str:
    if kind not in KEY_KINDS:
        raise KeyRefused("key_kind_unknown", str(kind))
    return str(kind)


def _name(name: object) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise KeyRefused("key_name_malformed")
    return name


def _is_version(value: object, expected: int = _FILE_VERSION) -> bool:
    return type(value) is int and value == expected  # not a bool (a subclass of int), not a float


def _account(kind: str, name: str) -> str:
    return f"{kind}:{name}"
