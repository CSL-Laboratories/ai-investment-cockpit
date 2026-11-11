"""Where Cockpit keeps a key (CSL-59, candidate; CSLR-D-011; PRODUCT.md 055cc7a5, A44, 3.8 and 5.4).

Lucas, Nov 8: "Owner-only file" (a host with no OS keychain keeps saved API keys in an owner-only file
rather than refusing them), and for Cockpit bar 2, "Yes, model keys only": the owner-only file is
allowed for user-entered model-provider keys only; broker and venue keys stay keychain-only.

``KeyStore`` decides one key at a time and fails closed:

* a ``model_provider`` key goes in the OS keyring when the host has one, and otherwise in one
  owner-only file (``0600`` file in a ``0700`` directory, both owned by the running user, neither a
  symlink). Every directory from ``/`` to that directory is root-owned or the running user's, and
  no other user can rename an entry along that chain (a root-owned sticky directory such as ``/tmp``
  may be world-writable; same rule as Recursion ``_owned_chain``). Each directory component is
  opened with ``O_DIRECTORY|O_NOFOLLOW``. The key file and the lock are opened with ``O_NOFOLLOW`` and
  ``O_NONBLOCK`` after an ``lstat`` that refuses a symlink, fifo, socket, or device. The fd is
  checked again after open. Secret bytes are written only after the mode is ``0600``. A failed
  save wipes and unlinks its temp file and fsyncs the directory; a successful rename fsyncs it too;
* saves and deletes take an exclusive ``fcntl.flock`` on ``.keys.lock`` in that directory around the
  read-modify-write, so one save cannot drop a key another save just wrote;
* a ``broker`` or ``venue`` key goes only in the OS keyring; with no keyring it is refused, never
  written to a file;
* an existing key file or directory that is not owner-only is refused on read and on write.
  ``put`` and ``delete`` also refuse a lock that is not owner-only. ``get`` does not open the lock.
  Whether a loose lock should refuse a read is an open owner question (package README); this
  module does not choose. Nothing here repairs a loose file, lock, or directory.

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

__all__ = ["KEY_KINDS", "KeyRefused", "KeyStore", "Keyring", "key_file_state"]

KEY_KINDS = ("model_provider", "broker", "venue")
FILE_KINDS = ("model_provider",)
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FILE_VERSION = 1
_LOCK_NAME = ".keys.lock"


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
            self._keyring.set(self.SERVICE, _account(kind, name), secret)  # type: ignore[union-attr]
            return backend
        with self._locked_dir() as dir_fd:
            keys = self._read_file(dir_fd)
            keys[name] = secret
            self._write_file(dir_fd, keys)
        return backend

    def get(self, kind: str, name: str) -> str | None:
        name = _name(name)
        if self.backend_for(kind) == "keyring":
            return self._keyring.get(self.SERVICE, _account(kind, name))  # type: ignore[union-attr]
        assert self._path is not None
        dir_fd = _open_owned_dir(os.path.dirname(os.path.abspath(self._path)))
        try:
            return self._read_file(dir_fd).get(name)
        finally:
            os.close(dir_fd)

    def delete(self, kind: str, name: str) -> None:
        name = _name(name)
        if self.backend_for(kind) == "keyring":
            self._keyring.delete(self.SERVICE, _account(kind, name))  # type: ignore[union-attr]
            return
        with self._locked_dir() as dir_fd:
            keys = self._read_file(dir_fd)
            if keys.pop(name, None) is not None:
                self._write_file(dir_fd, keys)

    # -- owner-only file ------------------------------------------------------------------
    @contextmanager
    def _locked_dir(self) -> Iterator[int]:
        """Directory fd for the key file, held with an exclusive lock on ``.keys.lock``."""

        assert self._path is not None and fcntl is not None
        dir_fd = _open_owned_dir(os.path.dirname(os.path.abspath(self._path)))
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
        name = _entry_name(self._path)
        try:
            st = os.lstat(name, dir_fd=dir_fd)
        except FileNotFoundError:
            return {}
        problem = _entry_problem(st)
        if problem is not None:
            raise KeyRefused("key_file_not_owner_only", problem)
        try:
            fd = os.open(name, _file_flags(os.O_RDONLY), dir_fd=dir_fd)
        except OSError as exc:
            raise KeyRefused("key_file_not_owner_only", _errno_name(exc)) from None
        try:
            problem = _entry_problem(os.fstat(fd))
            if problem is not None:
                raise KeyRefused("key_file_not_owner_only", problem)
            raw = bytearray()
            while True:
                block = os.read(fd, 65536)
                if not block:
                    break
                raw += block
        finally:
            os.close(fd)
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

    def _write_file(self, dir_fd: int, keys: Mapping[str, str]) -> None:
        _require_final_dir(dir_fd)
        name = _entry_name(self._path)
        _refuse_existing_unless_owner_only(dir_fd, name)
        payload = {
            "version": _FILE_VERSION,
            "kind": "model_provider",
            "keys": dict(sorted(keys.items())),
        }
        body = json.dumps(payload, indent=1).encode("utf-8")
        tmp = f".keys-{os.getpid()}-{secrets.token_hex(8)}"
        fd = -1
        renamed = False
        try:
            fd = os.open(tmp, _file_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o600, dir_fd=dir_fd)
            os.fchmod(fd, 0o600)
            problem = _entry_problem(os.fstat(fd))
            if problem is not None:
                raise KeyRefused("key_file_not_owner_only", problem)
            _write_all(fd, body)
            os.fsync(fd)
            _refuse_existing_unless_owner_only(dir_fd, name)
            os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            renamed = True
            _fsync_dir(dir_fd)
        finally:
            if fd >= 0 and not renamed:
                _discard_temp(dir_fd, fd, tmp)
            elif fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


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
        problem = _entry_problem(st)
        if problem is None:
            return "owner_only"
        return _problem_state(problem)
    finally:
        os.close(fd)


def _open_owned_dir(directory: str) -> int:
    """Open ``directory`` and return its fd, after the owned-chain rule. Caller closes it.

    Ancestors follow Recursion's ``_owned_chain``: each is root's or ours, and not writable by
    anyone else, except a root-owned sticky directory. The directory itself must be a real
    ``0700`` directory owned by the running user. ``O_NOFOLLOW`` on every component means a
    symlink, including one swapped in above the directory, is refused instead of followed.
    """

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise KeyRefused("owner_only_file_unsupported", "no owner-only file on this platform")
    directory = os.path.abspath(directory)
    names = [part for part in directory.split(os.sep) if part not in ("", ".")]
    if any(part == ".." for part in names):
        raise KeyRefused("key_dir_not_owner_only", "bad component")
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
    flags = _file_flags(os.O_RDWR)
    for _ in range(2):
        try:
            st = os.lstat(_LOCK_NAME, dir_fd=dir_fd)
        except FileNotFoundError:
            try:
                fd = os.open(_LOCK_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
            except FileExistsError:
                continue
            except OSError as exc:
                raise KeyRefused("key_file_not_owner_only", _errno_name(exc)) from None
            return _checked_lock(dir_fd, fd, created=True)
        problem = _entry_problem(st)
        if problem is not None:
            raise KeyRefused("key_file_not_owner_only", problem)
        try:
            fd = os.open(_LOCK_NAME, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise KeyRefused("key_file_not_owner_only", _errno_name(exc)) from None
        return _checked_lock(dir_fd, fd, created=False)
    raise KeyRefused("key_file_not_owner_only", "lock")


def _checked_lock(dir_fd: int, fd: int, created: bool) -> int:
    try:
        if created:
            os.fchmod(fd, 0o600)
        problem = _entry_problem(os.fstat(fd))
        if problem is not None:
            raise KeyRefused("key_file_not_owner_only", problem)
        return fd
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        if created:
            _unlink_quiet(dir_fd, _LOCK_NAME)
        raise


def _refuse_existing_unless_owner_only(dir_fd: int, name: str) -> None:
    try:
        st = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return
    problem = _entry_problem(st)
    if problem is not None:
        raise KeyRefused("key_file_not_owner_only", problem)


def _file_flags(base: int) -> int:
    return base | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)


def _entry_problem(st: os.stat_result) -> str | None:
    """None when ``st`` is a regular ``0600`` file owned by us. Otherwise a short reason.

    Type is decided from ``lstat``/``fstat`` mode bits. A symlink is not followed, and a fifo,
    socket, or device is not reported as merely a mode.
    """

    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISFIFO(st.st_mode):
        return "fifo"
    if stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
        return "device"
    if stat.S_ISSOCK(st.st_mode):
        return "socket"
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if not stat.S_ISREG(st.st_mode):
        return "special"
    if st.st_uid != os.geteuid():
        return "uid"
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        return oct(mode)
    return None


def _problem_state(problem: str) -> str:
    if problem.startswith("0o"):
        return f"file_not_owner_only({problem})"
    return f"file_not_owner_only:{problem}"


def _write_all(fd: int, body: bytes) -> None:
    view = memoryview(body)
    while view:
        wrote = os.write(fd, view)
        if wrote <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[wrote:]


def _wipe_fd(fd: int) -> None:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size <= 0:
        return
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = st.st_size
    zeros = b"\0" * 4096
    while remaining > 0:
        chunk = zeros if remaining >= len(zeros) else zeros[:remaining]
        wrote = os.write(fd, chunk)
        if wrote <= 0:
            raise OSError(errno.EIO, "short wipe")
        remaining -= wrote
    os.fsync(fd)


def _fsync_dir(dir_fd: int) -> None:
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        # Some filesystems cannot fsync a directory. A real I/O error still fails the save.
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", 0)):
            raise


def _discard_temp(dir_fd: int, fd: int, name: str) -> None:
    try:
        _wipe_fd(fd)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass
    try:
        _fsync_dir(dir_fd)
    except OSError:
        pass


def _unlink_quiet(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _entry_name(path: str | None) -> str:
    if path is None:
        raise KeyRefused("key_file_not_configured")
    name = os.path.basename(os.path.abspath(path))
    if name in ("", ".", ".."):
        raise KeyRefused("key_file_not_configured")
    return name


def _errno_name(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, type(exc).__name__)


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
