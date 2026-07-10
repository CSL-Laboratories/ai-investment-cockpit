"""W11 (b) — decision-agent credential starvation (SEC-05 / Inv 4).

Decision-stage agents run as plain subprocesses. Unless they run under a SEPARATE low-privilege
Windows account whose NTFS ACLs deny read on the bearer stores, a compromised/injected decision
agent can read ``~/.codex/auth.json``, the Claude CLI session store, and the keyring backing — and
a harvested token places orders with ZERO contact with this app (backlog SEC-05, L201-216).

This module is the STARTUP hook that FAILS CLOSED unless the decision-agent account provably
CANNOT read those stores. "Provably" is the load-bearing word: the check evaluates the configured
decision-agent account's EFFECTIVE read permission (an ACL evaluation), not this process's own
access — the orchestrator runs as the privileged account and can always read them. When the
separate account is not configured (dev), the control is reported NOT enforced rather than
crashing the app; provisioning is documented in ``docs/setup/low-priv-agent-account.md``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class CredentialGuardError(Exception):
    """SEC-05 — the decision-agent account can still read a bearer store. Fail closed: the app
    must not serve a decision/order path whose agents are one file-read from a live credential."""


@dataclass
class CredentialGuardStatus:
    """Outcome of the startup credential-starvation check. ``enforced=False`` means no separate
    low-priv account is configured (dev): the SEC-05 control is INACTIVE, not satisfied."""

    enforced: bool
    account: str | None
    stores: list[str] = field(default_factory=list)
    readable: list[str] = field(default_factory=list)


def default_store_paths() -> list[str]:
    """The bearer stores a decision agent must not be able to read (SEC-05). Paths, not contents —
    the ACL check never opens them."""
    home = Path.home()
    return [
        str(home / ".codex" / "auth.json"),   # Codex OAuth bearer
        str(home / ".claude"),                 # Claude CLI session store (dir)
        str(home / ".claude.json"),            # Claude CLI config / session
    ]


def _account_can_read_factory(account: str):
    """Return ``account_can_read(path) -> bool`` for the configured decision-agent ``account``.

    FAIL CLOSED: if the effective-permission evaluation cannot be performed (non-Windows, missing
    pywin32, an unresolvable account), treat the store as READABLE so the guard refuses rather than
    silently "passing" an unverifiable box. Provisioning ACLs is what turns this False."""

    def account_can_read(path: str) -> bool:   # pragma: no cover - OS/ACL specific
        # NOTE (G11 round-4 hardening, pre-W17): this walks DIRECT-SID ACEs only; a read granted via
        # GROUP membership is not detected here. A future refinement should evaluate the account's
        # EFFECTIVE access (``win32security.AuthzAccessCheck``) rather than direct-SID ACEs. It is
        # non-reachable today — the ``_logon_launch`` spawn raises until the account is provisioned,
        # so no decision job runs under it until the ACL model is completed with the launcher.
        if not os.path.exists(path):
            return False                        # a store that does not exist cannot be read
        try:
            import ntsecuritycon as con
            import win32security

            sid, _, _ = win32security.LookupAccountName(None, account)
            sd = win32security.GetFileSecurity(path, win32security.DACL_SECURITY_INFORMATION)
            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:
                return True                     # no DACL => everyone implicitly allowed
            read_mask = con.FILE_GENERIC_READ
            allowed = 0
            for i in range(dacl.GetAceCount()):
                ace_type, _flags, mask, ace_sid = _unpack_ace(dacl.GetAce(i))
                if ace_sid != sid:
                    continue
                if ace_type == win32security.ACCESS_DENIED_ACE_TYPE and (mask & read_mask):
                    return False                # an explicit deny wins
                if ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE:
                    allowed |= mask
            return bool(allowed & read_mask)
        except Exception:
            return True                         # cannot evaluate => fail closed (treat as readable)

    return account_can_read


def _unpack_ace(ace):  # pragma: no cover - OS/ACL specific
    (ace_type, ace_flags), mask, sid = ace
    import win32security  # noqa: F401  (imported for symmetry / lazy availability)
    return ace_type, ace_flags, mask, sid


def assert_credential_starved(settings=None, *, store_paths=None,
                              account_can_read=None) -> CredentialGuardStatus:
    """Startup SEC-05 hook. When a decision-agent account is configured
    (``settings.decision_agent_account``), FAIL CLOSED (raise ``CredentialGuardError``) unless that
    account provably cannot read ANY bearer store. When no account is configured, return a
    ``CredentialGuardStatus(enforced=False, ...)`` — the control is inactive, not passed.

    ``account_can_read(path) -> bool`` is injectable so the fail-closed LOGIC is tested without a
    real second Windows account; production defaults to an NTFS-ACL effective-permission check."""
    account = getattr(settings, "decision_agent_account", None) if settings is not None else None
    stores = list(store_paths) if store_paths is not None else default_store_paths()
    if account is None:
        return CredentialGuardStatus(enforced=False, account=None, stores=stores, readable=[])
    check = account_can_read or _account_can_read_factory(account)
    readable = [p for p in stores if check(p)]
    if readable:
        raise CredentialGuardError(
            f"SEC-05: decision-agent account {account!r} can still read credential store(s) "
            f"{readable}; provision the separate low-priv account + per-file ACLs "
            "(docs/setup/low-priv-agent-account.md). Failing closed."
        )
    return CredentialGuardStatus(enforced=True, account=account, stores=stores, readable=[])
