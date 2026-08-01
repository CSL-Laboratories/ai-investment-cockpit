"""RW-01 — verify the operator's keyring provisioning, and heal a mis-named entry.

    python tools/verify_keys.py              # report
    python tools/verify_keys.py --migrate    # copy near-miss names onto the canonical ones

THE NAMES COME FROM THE CODE, NEVER FROM AN ARGUMENT. That is this tool's entire reason to
exist. The obvious way to check provisioning is to type the five names into a one-liner — but
then the check and the app can disagree, and the check will happily confirm a set of secrets
the cockpit cannot see. That happened for real during RW-01 bring-up: the operator provisioned
``alpaca-key`` (hyphens), "corrected" the checker to match, and got five green lines for
credentials ``main.build_ingest_fetchers`` would never find. Same defect class as the
``discord-bot-token`` vs ``discord_bot_token`` mismatch RW-01 fixed inside the app.

So the constants are IMPORTED from the modules that do the lookups. If someone renames a
secret in the code, this tool renames with it, automatically and by construction.

NO SECRET VALUE IS EVER PRINTED. Only presence, length, and a whitespace verdict — enough to
catch the two failure modes a hidden paste actually has (nothing landed; a trailing newline
came along from a web dashboard) and nothing more. Safe to run over someone's shoulder and
safe to paste the output into a chat.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))            # so this runs from the repo root

import keyring                                                          # noqa: E402

from core.secrets import SERVICE, get_secret, set_secret                # noqa: E402
from main import (                                                      # noqa: E402
    _ALPACA_KEY_NAME,
    _ALPACA_SECRET_NAME,
    _DISCORD_TOKEN_NAME,
    _FINNHUB_KEY_NAME,
    _HMAC_SECRET_NAME,
)
from orchestrator.auth import API_KEY_SECRET_NAME                       # noqa: E402

# (canonical name, what it is, what breaks without it). Imported constants only — never
# literals, so this list cannot drift from what the app actually reads.
REQUIRED = [
    (_ALPACA_KEY_NAME, "Alpaca API key id", "no Benzinga news WebSocket"),
    (_ALPACA_SECRET_NAME, "Alpaca API secret", "no Benzinga news WebSocket"),
    (_FINNHUB_KEY_NAME, "Finnhub free-tier key", "no Finnhub news, no earnings calendar"),
    (_DISCORD_TOKEN_NAME, "Discord bot token", "the brief is stored but DELIVERED NOWHERE"),
    (API_KEY_SECRET_NAME, "orchestrator X-API-Key", "every API route 401s, incl. /status"),
]

# App-minted, never operator-provisioned. Reported for completeness so its absence before the
# first compose() does not read as a provisioning gap.
APP_MINTED = [(_HMAC_SECRET_NAME, "audit-chain HMAC key (app mints this on first compose)")]


def variants(name: str) -> list[str]:
    """Plausible mis-spellings of ``name`` to look for in the store.

    Hyphen/underscore is the one that actually bites: the docs, the code and muscle memory
    disagree about separators, and a hyphenated entry is INVISIBLE to the app while looking
    perfectly healthy in Credential Manager.
    """
    candidates = {
        name.replace("_", "-"),
        name.replace("-", "_"),
        name.upper(),
        name.lower(),
        name.replace("_", ""),
        name.replace("_", ".") if "_" in name else name,
    }
    candidates.discard(name)
    return sorted(candidates)


def describe(name: str) -> str | None:
    """``len=.. clean=..`` for a stored secret, or None when absent. Never the value."""
    secret = get_secret(name)
    if secret is None:
        return None
    value = secret.reveal()
    return f"len={len(value):<4} clean={value == value.strip()}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/verify_keys.py",
        description="Verify cockpit keyring provisioning. Never prints a secret value.")
    parser.add_argument("--migrate", action="store_true",
                        help="copy a near-miss entry (e.g. alpaca-key) onto its canonical name "
                             "(alpaca_key). Non-destructive: the old entry is left in place.")
    args = parser.parse_args(argv)

    print(f"keyring service: {SERVICE}")
    print(f"backend:         {type(keyring.get_keyring()).__name__}\n")

    missing, healed, dirty = [], [], []
    for name, what, breaks in REQUIRED:
        state = describe(name)
        if state is not None:
            flag = "" if "clean=True" in state else "   <- STRIP WHITESPACE: re-paste this one"
            if flag:
                dirty.append(name)
            print(f"  OK       {name:<20} {state}{flag}")
            continue

        # Absent under the canonical name. Before calling it missing, look for the mis-spelling
        # that makes it invisible to the app while looking fine in Credential Manager.
        found = next((v for v in variants(name) if get_secret(v) is not None), None)
        if found is None:
            missing.append((name, what, breaks))
            print(f"  MISSING  {name:<20} ({what}) -> {breaks}")
        elif args.migrate:
            set_secret(name, get_secret(found).reveal())
            healed.append((found, name))
            print(f"  HEALED   {name:<20} {describe(name)}   (copied from {found!r})")
        else:
            missing.append((name, what, breaks))
            print(f"  WRONG    {name:<20} stored as {found!r} -- the app reads {name!r}, so it "
                  f"is INVISIBLE. Run with --migrate.")

    for name, what in APP_MINTED:
        state = describe(name)
        print(f"  {'note   ' if state else 'pending'}  {name:<20} "
              f"{state or 'not yet minted (created on first compose) - this is normal'}"
              f"   [{what}]")

    print()
    if healed:
        print(f"Migrated {len(healed)} entr(ies). The old names were LEFT IN PLACE (deleting a "
              "credential is not something a check tool should do unasked). Remove them with:")
        for old, _new in healed:
            print(f"    keyring del {SERVICE} {old}")
        print()
    if dirty:
        print(f"{len(dirty)} entr(ies) carry leading/trailing whitespace and will fail auth with "
              "a generic 401/403. Re-paste: " + ", ".join(f"keyring set {SERVICE} {n}"
                                                          for n in dirty))
        return 1
    if missing:
        print(f"{len(missing)} secret(s) not provisioned. Fix with:")
        for name, _what, _breaks in missing:
            print(f"    keyring set {SERVICE} {name}")
        print("\nSee docs/runbooks/rw01-live-smoke.md section 1.")
        return 1

    print("All required secrets are provisioned under the names the app reads.")
    print("NOTE: this proves they are STORED and well-formed, not that they are VALID -- only "
          "the vendor can say that. The live smoke is what settles validity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
