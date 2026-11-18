"""Owner-only key storage for the app (item 5; #319, CSL-59).

Lucas, Nov 11, 12:52am PST: "Lift the #318 hold and have new app/ code wire in #318 and #319".
His Nov 10 8:49pm PST unfreeze covers app/ "only for the paper panel and owner-only key
integration"; this module is the owner-only-key half and touches no other app/ code. The
app's existing ``core.secrets`` (its own keyring service) is unchanged.

Where a key lives is decided by ``engine/cockpit/keys`` (#319; PRODUCT.md A44, 3.8 and 5.4):
an OS keychain when the host has one; otherwise model-provider keys only in one owner-only
file (``0600`` file, ``0700`` directory, owned by the running user, no symlinks), and broker
and venue keys refused (``keychain_required``). This module only adapts that store to the app:

* values come back as ``core.secrets.Redacted`` so they cannot leak through repr or logging;
* every refusal (``KeyRefused``: not owner-only, wrong owner, loose lock, malformed or
  mislabelled file, no keychain for a broker or venue key) propagates. Nothing falls back to
  an environment variable, ``core.secrets`` or a default, so a key file that is not the
  owner's fails closed;
* ``doctor_line`` names where keys are kept and never prints a key.

No order path: paper only remains PRODUCT.md 5.2 / CP0-v2 (pin d84cdd26, CSLR-D-012).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from core.engine_path import import_engine
from core.secrets import Redacted

__all__ = ["OwnerKeys", "KeyRefused"]


def _keys() -> Any:
    return import_engine("cockpit.keys")


KeyRefused = _keys().KeyRefused


class OwnerKeys:
    """The host's Cockpit ``KeyStore`` behind the app's ``Redacted`` type."""

    def __init__(self, store: Any, status: Any, key_file: str | os.PathLike[str] | None) -> None:
        keys = _keys()
        if not isinstance(store, keys.KeyStore) or not isinstance(status, keys.KeychainStatus):
            raise TypeError("OwnerKeys needs a cockpit.keys KeyStore and KeychainStatus")
        self._store, self._status = store, status
        self._key_file = os.fspath(key_file) if key_file is not None else None

    @classmethod
    def for_host(cls, key_file: str | os.PathLike[str] | None = None,
                 load: Callable[[], Any] | None = None) -> OwnerKeys:
        """Detect the host's keychain (read-only) and build the store it should use."""
        keys = _keys()
        path = os.fspath(key_file) if key_file is not None else keys.detect.default_key_file()
        store, status = (keys.store_for_host(path) if load is None else keys.store_for_host(path, load))
        return cls(store, status, path)

    @property
    def keychain(self) -> bool:
        return bool(self._status.available)

    def backend_for(self, kind: str) -> str:
        """``keyring`` or ``owner_only_file``; raises ``KeyRefused`` where a kind may not be kept."""
        return self._store.backend_for(kind)

    def get(self, kind: str, name: str) -> Redacted | None:
        """The key, or ``None`` if it was never saved. A refusal raises; it is never ``None``."""
        value = self._store.get(kind, name)
        return None if value is None else Redacted(value)

    def model_key(self, name: str) -> Redacted | None:
        return self.get("model_provider", name)

    def put(self, kind: str, name: str, secret: str | Redacted) -> str:
        value = secret.reveal() if isinstance(secret, Redacted) else secret
        return self._store.put(kind, name, value)

    def delete(self, kind: str, name: str) -> None:
        self._store.delete(kind, name)

    def doctor(self) -> dict[str, str]:
        return _keys().doctor(self._status, self._key_file)

    def doctor_line(self) -> str:
        return _keys().doctor_line(self.doctor())
