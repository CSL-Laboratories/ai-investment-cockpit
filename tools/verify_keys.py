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


# Format expectations, used ONLY to warn — never to reject. A vendor may change its key shape
# at any time, and a tool that refuses a valid new format is worse than one that says "this
# looks unusual". ``prefix`` is a public format marker, not secret material.
_SHAPE = {
    _ALPACA_KEY_NAME: ("PK", "an Alpaca PAPER key id normally starts 'PK' ('AK' = live)"),
    _DISCORD_TOKEN_NAME: ("", "a Discord bot token has two dots in it"),
}


def set_one(name: str) -> int:
    """Prompt for ONE secret and store it under a CANONICAL name.

    THREE THINGS THIS DOES THAT A BARE ``keyring set`` DOES NOT, each earned during RW-01
    bring-up:

    1. IT REFUSES AN UNKNOWN NAME. ``keyring set ai-investment-cockpit alpaca-key`` succeeds
       happily and the app never sees it — the entry looks perfectly healthy in Credential
       Manager while the cockpit reports the feed unprovisioned. The valid names come from the
       code (see the module docstring), so a typo is caught here instead of at 08:35.
    2. IT STRIPS WHITESPACE. A value copied from a web dashboard often carries a trailing
       newline; auth then fails with a generic 401 and nothing points at the cause.
    3. IT ECHOES THE STORED LENGTH. The prompt is hidden, so a paste that silently did not
       land is otherwise indistinguishable from one that did.
    """
    import getpass

    canonical = {n for n, _w, _b in REQUIRED}
    if name not in canonical:
        near = next((c for c in canonical if c.replace("_", "-") == name
                     or c == name.replace("-", "_")), None)
        print(f"refusing to store {name!r}: it is not a name this app reads.")
        if near:
            print(f"  did you mean {near!r}?  (separators matter: a hyphenated entry is "
                  "INVISIBLE to the cockpit)")
        print("  valid names: " + ", ".join(sorted(canonical)))
        return 2

    raw = getpass.getpass(f"paste the value for {name} (input hidden): ")
    value = raw.strip()
    if not value:
        print("nothing was entered: NOT stored. If your terminal swallows Ctrl+V at a hidden "
              "prompt, try right-click paste (Windows Terminal: Ctrl+Shift+V).")
        return 1
    if value != raw:
        print("  note: stripped surrounding whitespace from the pasted value")

    set_secret(name, value)
    stored = get_secret(name).reveal()
    print(f"  stored {name}: len={len(stored)}  clean={stored == stored.strip()}")

    prefix, hint = _SHAPE.get(name, ("", ""))
    if prefix and not stored.startswith(prefix):
        print(f"  WARNING: {hint}. Stored anyway. Check you copied the KEY ID, not the secret.")
    elif name == _DISCORD_TOKEN_NAME and stored.count(".") != 2:
        print(f"  WARNING: {hint}. Stored anyway. Check you copied the whole token.")
    print("\nRe-run `python tools/verify_keys.py` to confirm the full set, then "
          "`python tools/probe_credentials.py` to prove it works against the vendor.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/verify_keys.py",
        description="Verify cockpit keyring provisioning. Never prints a secret value.")
    parser.add_argument("--migrate", action="store_true",
                        help="copy a near-miss entry (e.g. alpaca-key) onto its canonical name "
                             "(alpaca_key). Non-destructive: the old entry is left in place.")
    parser.add_argument("--force", action="store_true",
                        help="with --migrate, OVERWRITE a canonical entry that already exists. "
                             "Repair path for a canonical name holding the wrong value while "
                             "the good value survives under a near-miss name.")
    parser.add_argument("--set", metavar="NAME", default=None,
                        help="prompt for ONE secret and store it under a CANONICAL name. "
                             "Refuses an unknown name (a mis-typed name is invisible to the "
                             "app), strips stray whitespace, and echoes the stored length so a "
                             "hidden paste is confirmable.")
    args = parser.parse_args(argv)
    if args.force and not args.migrate:
        parser.error("--force only means something with --migrate")
    if args.set is not None:
        return set_one(args.set)

    print(f"keyring service: {SERVICE}")
    print(f"backend:         {type(keyring.get_keyring()).__name__}\n")

    missing, healed, dirty = [], [], []
    for name, what, breaks in REQUIRED:
        state = describe(name)
        if state is not None:
            # --force: the canonical name EXISTS but may hold the wrong value, while the good
            # value survives under a near-miss name. That is not hypothetical -- it happened
            # during RW-01 bring-up when an ad-hoc script wrote a placeholder over a real
            # credential. Plain --migrate cannot fix it (it only fills a MISSING name), so the
            # repair is explicit and opt-in rather than silently clobbering on every run.
            source = next((v for v in variants(name) if get_secret(v) is not None), None)
            if args.force and args.migrate and source is not None:
                set_secret(name, get_secret(source).reveal())
                healed.append((source, name))
                print(f"  RESTORED {name:<20} {describe(name)}   (overwritten from {source!r})")
                continue
            flag = "" if "clean=True" in state else "   <- STRIP WHITESPACE: re-paste this one"
            if flag:
                dirty.append(name)
            note = ""
            if source is not None and not args.migrate:
                note = f"   (note: {source!r} also exists; --migrate --force to restore from it)"
            print(f"  OK       {name:<20} {state}{flag}{note}")
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
