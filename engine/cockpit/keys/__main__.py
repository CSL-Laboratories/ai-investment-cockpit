"""``python -m cockpit.keys doctor [--key-file PATH] [--check-keychain]``: print the keys doctor line (CSL-59,
candidate).

Exit 0 for ok or warn, 1 for fail. Cockpit asks for no key and writes or prints none (what the keychain
library itself does under the opt-in read is said below). The plain doctor reads no keychain item and calls
no password method: it looks at the key file's folder and the keychain record file, so on a keychain host
with no record it says so and names the way out. Finding out which keychain is selected asks
the keychain service whether it is there, as detection always has (see ``detect``). ``--check-keychain``
(B37, opt-in) also asks the keychain for Cockpit's marker when the record is absent. That goes through the
keychain library's ordinary read, the same as any broker key read: it can show the keychain's unlock prompt
or wait for it, and in keyring 25.7.0, on KWallet, the call that opens the wallet first moves old-format
entries out of its old "Python" folder, and on Secret Service it creates the default collection if there is
none. If that read fails with an ordinary error, the line shows only the error's type (an ok line becomes
warn); a refusal (``KeyRefused``) is shown as a refusal with its own text and exits 1. Lucas, Nov 13, 2026,
told this: "State what is true (Recommended)".

``python -m cockpit.keys accept-keychain NAME [--key-file PATH]``: after a real change of OS keychain,
record the keychain this run selected as the one broker and venue keys are kept in (B24). ``NAME`` must be
exactly Cockpit's name for that keychain, as the doctor line and the refusal show it, and the command runs
only at an interactive terminal. It writes Cockpit's marker in the keychain and then the keychain record
beside the key file; it asks for no key and writes or prints none (the marker write is the keychain
library's ordinary write, which can do on each keychain what any key write does). Exit 0 when recorded, 1
when refused or when it did not finish.
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
    d.add_argument("--check-keychain", action="store_true",
                   help="when the keychain record is missing, also ask the keychain for Cockpit's marker; this is "
                        "the keychain library's ordinary read, as for any broker key: it can show the keychain's "
                        "unlock prompt or wait for it, on KWallet it can move old-format entries out of the "
                        "wallet's old Python folder, and on Secret Service it can create the default collection "
                        "if there is none")
    a = sub.add_parser("accept-keychain", help="after a real change of keychain, keep broker and venue keys "
                                                "in the keychain this run selected")
    a.add_argument("name", help="Cockpit's name for the keychain this run selected, as the doctor shows it")
    a.add_argument("--key-file", default=None)
    args = ap.parse_args(argv)
    key_file = args.key_file or default_key_file()
    if args.cmd == "accept-keychain":
        return _accept(args.name, key_file)
    if args.check_keychain:
        result = _doctor_with_keychain_check(key_file)
    else:
        # doctor() gets no keychain object, so it reads no keychain item; detect_keychain() asks the keychain
        # service whether it is there (the admitted class's priority), as detection always has.
        result = doctor(detect_keychain(), key_file)
    print(doctor_line(result))
    return 1 if result["level"] == "fail" else 0


def _doctor_with_keychain_check(key_file: str) -> dict[str, str]:
    store, status = store_for_host(key_file)
    result = doctor(status, key_file)
    if result.get("keychain_record_note") != "none":
        return result  # the record file answered (or there is no record to check): no keychain read is needed
    try:
        # The record is absent: ask for the marker, through the library's ordinary read (see the module docstring).
        checked = store.check_keychain_record()
    except KeyRefused as exc:
        result.pop("keychain_record_note", None)
        result.update(level="fail", broker=f"refused:{exc.cause}", venue=f"refused:{exc.cause}",
                      keychain_record=exc.detail)
    except Exception as exc:  # noqa: BLE001 - a locked keychain, a dismissed prompt: the type only, never the message
        result["keychain_check"] = f"did not finish ({_exception_name(exc)})"
        if result["level"] == "ok":
            result["level"] = "warn"
    else:
        if checked == "ok":  # "unsupported": this store keeps no record and read nothing, so nothing was checked
            result["keychain_record_note"] = "checked"
    return result


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
        # The marker is written first and kept; the record may or may not have been replaced (an error can
        # come after it was published), so nothing is claimed. Only the exception's type is shown.
        print(f"keys: failed - accept-keychain did not finish ({_exception_name(exc)}); nothing is confirmed: "
              f"run it again, then check python -m cockpit.keys doctor")
        return 1
    note = f" (switched from {previous})" if previous else ""
    print(f"keys: ok - broker and venue keys are kept in {status.backend}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
