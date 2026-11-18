"""``python -m cockpit.keys doctor [--key-file PATH]``: print the keys doctor line (CSL-59, candidate).

Exit 0 for ok or warn, 1 for fail. Read-only: never reads, writes or prints a key.
"""

from __future__ import annotations

import argparse
import sys

from cockpit.keys.detect import default_key_file, detect_keychain, doctor, doctor_line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cockpit.keys")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="say where Cockpit keeps keys on this host")
    d.add_argument("--key-file", default=None)
    args = ap.parse_args(argv)
    result = doctor(detect_keychain(), args.key_file or default_key_file())
    print(doctor_line(result))
    return 1 if result["level"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
