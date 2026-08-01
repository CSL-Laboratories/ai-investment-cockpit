"""WP0 — the keyring secret wrapper (Invariant 4; docs/setup/accounts-and-keys.md §5).

Secrets live ONLY in the OS credential store (Windows Credential Manager at runtime, an
in-memory backend under test — see ``app/tests/conftest.py``). They are NEVER read from env,
config, the repo, a prompt, or a log.

The read API returns a ``Redacted`` rather than a bare ``str`` so that the DEFAULT thing a
caller can do with a secret is safe: ``repr``/``str``/f-string/``logging`` interpolation all
render ``Redacted('***')``. Getting the value requires typing ``.reveal()`` at the point of
use, which is also the one token the secrets-grep gate (``tests/test_invariants.py``) can be
pointed at when auditing where plaintext is materialized.

NOTE FOR THE NEXT SESSION — THIS FILE IS EXPLICITLY UN-IGNORED IN ``.gitignore``.
The security rule ``secrets.*`` (which blocks plaintext secret FILES like ``secrets.yaml``)
also matched THIS legitimate source module, so it was silently untracked from WP0 until
RW-01: ``git status`` never surfaced it, and a clean checkout failed to collect four test
modules with ``ModuleNotFoundError: No module named 'core.secrets'`` while the working tree
looked healthy. The ``!**/secrets.py`` exception below the rule is what keeps it tracked. Do
not remove it, and do not "tidy" the rule back to a bare ``secrets.*``.
"""
from __future__ import annotations

import keyring

# The credential-store service name every secret is filed under (accounts-and-keys.md §5:
# ``keyring set ai-investment-cockpit <name>``). One service, so the operator's provisioning
# commands and the app's reads cannot drift apart.
SERVICE = "ai-investment-cockpit"
_SERVICE = SERVICE          # historical private alias; both names resolve to the one constant


class Redacted:
    """A secret value that cannot leak through ``repr``/``str``/logging.

    ``__slots__`` keeps the instance dict-free, so the value is not reachable via ``__dict__``
    in a generic object dump (a traceback renderer, a JSON encoder with ``default=vars``, a
    debug ``pprint``). Read it with ``.reveal()``, deliberately and at the point of use.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Materialize the plaintext. The ONE call that does — keep its scope tight."""
        return self._value

    def __repr__(self) -> str:
        return "Redacted('***')"

    def __str__(self) -> str:
        return "Redacted('***')"


def get_secret(name: str) -> Redacted | None:
    """Look up a secret by name; ``None`` when it is not provisioned.

    ``None`` is the NOT-PROVISIONED signal, and every caller must fail CLOSED on it (Inv 7) —
    never substitute a default, an empty string, or an env fallback. ``orchestrator/auth.py``
    is the reference posture: an absent API key rejects every request rather than admitting
    all of them.
    """
    value = keyring.get_password(SERVICE, name)
    return Redacted(value) if value is not None else None


def require_secret(name: str) -> Redacted:
    """Look up a secret by name, raising ``KeyError`` when it is not provisioned.

    Use this at a STARTUP/composition seam that genuinely cannot proceed without the secret,
    so the failure is loud and immediate rather than surfacing later as a mystery 401 or an
    empty feed. Use ``get_secret`` instead wherever "not provisioned" is a legitimate,
    degraded-but-running state (RW-01's ingest feeds are all of that shape).

    The message names the secret and the service so the operator can act on it directly, and
    deliberately carries no value.
    """
    secret = get_secret(name)
    if secret is None:
        raise KeyError(
            f"secret {name!r} is not provisioned in keyring service {SERVICE!r}; "
            f"run: keyring set {SERVICE} {name}"
        )
    return secret


def set_secret(name: str, value: str) -> None:
    """Write a secret into the credential store.

    Provisioning is normally the operator's ``keyring set`` (which prompts, so the value never
    lands in shell history). This exists for the ONE machine-minted secret the app owns: the
    audit-chain HMAC key, get-or-created by ``main._hmac_key``.
    """
    keyring.set_password(SERVICE, name, value)
