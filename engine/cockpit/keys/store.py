"""Where Cockpit keeps a key (CSL-59, candidate; PRODUCT.md 055cc7a5, A44, 3.8 and 5.4).

Lucas, Nov 8: "Owner-only file" (a host with no OS keychain keeps saved API keys in an owner-only file
rather than refusing them), and for Cockpit bar 2, "Yes, model keys only": the owner-only file is
allowed for user-entered model-provider keys only; broker and venue keys stay keychain-only.

``KeyStore`` decides one key at a time and fails closed:

* a ``model_provider`` key goes in the OS keyring when the host has one, and otherwise in one
  owner-only file (``0600`` file in a ``0700`` directory, both owned by the running user, neither a
  symlink);
* a ``broker`` or ``venue`` key goes only in the OS keyring; with no keyring it is refused, never
  written to a file;
* an existing key file that is not owner-only, not a regular file, or not owned by the running user
  is refused on read and on write, and is never repaired silently.

The keyring is injected (``get``/``set``/``delete``); this module imports no keyring library and makes
no provider, venue or network call. Error messages name the key, never its value. Frozen ``app/`` keeps
its own keyring code and is untouched.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from typing import Protocol

__all__ = ["KEY_KINDS", "KeyRefused", "KeyStore", "Keyring"]

KEY_KINDS = ("model_provider", "broker", "venue")
FILE_KINDS = ("model_provider",)
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FILE_VERSION = 1


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

    def __init__(self, *, keyring: Keyring | None, key_file: str | os.PathLike[str] | None = None) -> None:
        self._keyring = keyring
        self._path = os.fspath(key_file) if key_file is not None else None

    # -- public ---------------------------------------------------------------------------
    def backend_for(self, kind: str) -> str:
        """``"keyring"`` or ``"owner_only_file"`` for this kind on this host, or raise ``KeyRefused``."""

        kind = _kind(kind)
        if self._keyring is not None:
            return "keyring"
        if kind not in FILE_KINDS:
            raise KeyRefused("keychain_required", f"{kind} keys stay in the OS keychain")
        if os.name == "nt":
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
            self._keyring.set(self.SERVICE, _account(kind, name), secret)  # type: ignore[union-attr]
            return backend
        keys = self._read_file()
        keys[name] = secret
        self._write_file(keys)
        return backend

    def get(self, kind: str, name: str) -> str | None:
        name = _name(name)
        if self.backend_for(kind) == "keyring":
            return self._keyring.get(self.SERVICE, _account(kind, name))  # type: ignore[union-attr]
        return self._read_file().get(name)

    def delete(self, kind: str, name: str) -> None:
        name = _name(name)
        if self.backend_for(kind) == "keyring":
            self._keyring.delete(self.SERVICE, _account(kind, name))  # type: ignore[union-attr]
            return
        keys = self._read_file()
        if keys.pop(name, None) is not None:
            self._write_file(keys)

    # -- owner-only file ------------------------------------------------------------------
    def _check_dir(self) -> str:
        assert self._path is not None
        parent = os.path.dirname(os.path.abspath(self._path))
        try:
            st = os.lstat(parent)
        except FileNotFoundError:
            raise KeyRefused("key_dir_missing", parent) from None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise KeyRefused("key_dir_not_owner_only", "not a real directory")
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise KeyRefused("key_dir_not_owner_only", oct(st.st_mode & 0o777))
        return parent

    def _read_file(self) -> dict[str, str]:
        self._check_dir()
        assert self._path is not None
        try:
            fd = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        except FileNotFoundError:
            return {}
        except OSError as exc:  # ELOOP: a symlink
            raise KeyRefused("key_file_not_owner_only", type(exc).__name__) from None
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise KeyRefused("key_file_not_owner_only", oct(st.st_mode & 0o777))
            raw = fh.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise KeyRefused("key_file_malformed") from None
        if not isinstance(data, Mapping) or data.get("version") != _FILE_VERSION or not isinstance(data.get("keys"), Mapping):
            raise KeyRefused("key_file_malformed")
        keys = data["keys"]
        if not all(isinstance(k, str) and _NAME.match(k) and isinstance(v, str) for k, v in keys.items()):
            raise KeyRefused("key_file_malformed")
        return dict(keys)

    def _write_file(self, keys: Mapping[str, str]) -> None:
        parent = self._check_dir()
        assert self._path is not None
        if os.path.lexists(self._path):
            st = os.lstat(self._path)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise KeyRefused("key_file_not_owner_only", oct(st.st_mode & 0o777))
        body = json.dumps({"version": _FILE_VERSION, "kind": "model_provider", "keys": dict(sorted(keys.items()))},
                          indent=1).encode("utf-8")
        old = os.umask(0o077)
        try:
            fd, tmp = tempfile.mkstemp(prefix=".keys-", dir=parent)
        finally:
            os.umask(old)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


def _kind(kind: object) -> str:
    if kind not in KEY_KINDS:
        raise KeyRefused("key_kind_unknown", str(kind))
    return str(kind)


def _name(name: object) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise KeyRefused("key_name_malformed")
    return name


def _account(kind: str, name: str) -> str:
    return f"{kind}:{name}"
