"""``python -m cockpit.keys doctor [--key-file PATH] [--check-keychain]``: print the keys doctor line (CSL-59,
candidate).

Exit 0 for ok or warn, 1 for fail. Never reads, writes or prints a key. The plain doctor never contacts the
keychain: it looks at the key file's folder and the keychain record only, so on a keychain host with no record
it says so and names the way out. ``--check-keychain`` (B37, opt-in) also looks in the keychain for
Cockpit's marker when the record is absent: that is a keychain read, so it can show the keychain's unlock
prompt or wait for it. If that read fails or is refused, only the error's type is shown.

``python -m cockpit.keys accept-keychain NAME [--key-file PATH]``: after a real change of OS keychain,
record the keychain this run selected as the one broker and venue keys are kept in (B24). ``NAME`` must be
exactly Cockpit's name for that keychain, as the doctor line and the refusal show it, and the command runs
only at an interactive terminal. It writes Cockpit's marker in the keychain and then the keychain record
beside the key file; it never reads, writes or prints a key. Exit 0 when recorded, 1 when refused or when
it did not finish.
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
                   help="when the keychain record is missing, also look in the keychain for Cockpit's marker; "
                        "this reads the keychain, so it can show the keychain's unlock prompt or wait for it")
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
        result = doctor(detect_keychain(), key_file)  # no keychain object: the plain doctor cannot contact it
    print(doctor_line(result))
    return 1 if result["level"] == "fail" else 0


def _doctor_with_keychain_check(key_file: str) -> dict[str, str]:
    store, status = store_for_host(key_file)
    result = doctor(status, key_file)
    if result.get("keychain_record_note") != "none":
        return result  # the record file answered (or there is no record to check): no keychain read is needed
    try:
        store.check_keychain_record()  # reads the marker, since the record is absent
    except KeyRefused as exc:
        result.pop("keychain_record_note", None)
        result.update(level="fail", broker=f"refused:{exc.cause}", venue=f"refused:{exc.cause}",
                      keychain_record=exc.detail)
    except Exception as exc:  # noqa: BLE001 - a locked keychain, a dismissed prompt: the type only, never the message
        result["keychain_check"] = f"did not finish ({_exception_name(exc)})"
        if result["level"] == "ok":
            result["level"] = "warn"
    else:
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
