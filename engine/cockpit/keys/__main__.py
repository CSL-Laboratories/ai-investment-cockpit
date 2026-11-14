"""``python -m cockpit.keys doctor [--key-file PATH]``: print the keys doctor line (CSL-59, candidate).

Exit 0 for ok or warn, 1 for fail. Read-only: never reads, writes or prints a key.

``python -m cockpit.keys accept-keychain NAME [--key-file PATH]``: after a real change of OS keychain,
record the keychain this run selected as the one broker and venue keys are kept in (B24). ``NAME`` must be
exactly Cockpit's name for that keychain, as the doctor line and the refusal show it, and the command runs
only at an interactive terminal. It writes the keychain record beside the key file and Cockpit's marker in
the keychain; it never reads, writes or prints a key. Exit 0 when recorded, 1 when refused.
"""

from __future__ import annotations

import argparse
import sys

from cockpit.keys.detect import _exception_name, default_key_file, detect_keychain, doctor, doctor_line, store_for_host
from cockpit.keys.store import KeyRefused


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cockpit.keys")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="say where Cockpit keeps keys on this host")
    d.add_argument("--key-file", default=None)
    a = sub.add_parser("accept-keychain", help="after a real change of keychain, keep broker and venue keys "
                                                "in the keychain this run selected")
    a.add_argument("name", help="Cockpit's name for the keychain this run selected, as the doctor shows it")
    a.add_argument("--key-file", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "accept-keychain":
        return _accept(args.name, args.key_file or default_key_file())
    result = doctor(detect_keychain(), args.key_file or default_key_file())
    print(doctor_line(result))
    return 1 if result["level"] == "fail" else 0


def _accept(name: str, key_file: str) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("keys: refused - accept-keychain runs only at an interactive terminal")
        return 1
    store, status = store_for_host(key_file)
    if not status.available:
        print(f"keys: refused - no OS keychain ({status.reason})")
        return 1
    try:
        previous = store.accept_keychain(name)
    except KeyRefused as exc:
        print(f"keys: refused - {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - an unfinished acceptance is reported, never as a traceback
        # The record is replaced atomically and only after the marker is written, and a marker this run
        # added is taken back if the record fails; only the exception's type is shown, never its message.
        print(f"keys: failed - accept-keychain did not finish ({_exception_name(exc)}); nothing is confirmed: "
              f"run it again, then check python -m cockpit.keys doctor")
        return 1
    note = f" (switched from {previous})" if previous else ""
    print(f"keys: ok - broker and venue keys are kept in {status.backend}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
