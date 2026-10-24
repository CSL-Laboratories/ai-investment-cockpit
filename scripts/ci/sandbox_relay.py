#!/usr/bin/python3 -I
"""E1 trusted print: stop-commands wrapper + constant-alphabet relay (CSL-450 D10).

Ignores argv (D10 §3.8). Reads the compiled-in capture path, runs the E2 filter
in-process, and prints one public block inside ``::stop-commands::<token>``.
``first_bash=bound`` is never treated as §1.7 clause 3 (E4 parked; origin stays
``unattributed``). A catch-all prints ``[csl-relay] filter error`` and nothing
else from the capture. Filter stderr is discarded by the caller.

D10's production binary is statically linked under ``/opt/csl-relay/bin/relay``.
This file is the reviewed grammar and print alphabet.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

# python3 -I implies -P (3.11+): the script directory is not on sys.path.
# Production D10 compiles this grammar into the relay binary; the insert is
# only so the reviewed Python modules resolve next to this file.
_DIR = str(Path(__file__).resolve().parent)
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from sandbox_debug_filter import (
    CAP_BYTES,
    FilterResult,
    STATUS_CONFLICT,
    STATUS_NOT_OBSERVED,
    STATUS_OBSERVED,
    filter_capture,
)
from sandbox_e3_record import E3RecordError, reconstruct_e3_record
from sandbox_e1_hooks import accept_parent_shape, live_parent_shape, write_records

# Compiled-in paths (D10). Tests patch these attributes; argv is ignored.
CAPTURE_PATH = "/var/lib/csl-relay/capture"
E3_RECORD_PATH = "/var/lib/csl-relay/e3.record"
END_PATH = "/var/lib/csl-relay/end"

CHANNEL = "S"
ORIGIN = "unattributed"
FIRST_BASH_VALUES = frozenset({"missing", "ambiguous", "bound"})

LINE_OBSERVED_PREFIX = "[Sandbox Linux] Wrapped command with bwrap ("
LINE_NOT_OBSERVED = "[csl-relay] sandbox literal NOT observed"
LINE_CONFLICT = "[csl-relay] sandbox literal CONFLICT"
LINE_FILTER_ERROR = "[csl-relay] filter error"
LINE_NOT_RELAYED_PREFIX = "[csl-relay] NOT RELAYED reason="
NOT_RELAYED_REASONS = frozenset(
    {"no_end", "pair_open", "writer_held", "already_ran", "bad_parent"}
)

# D10 §2.3 step 7. Tests pass min_wall_seconds=0.
MIN_WALL_SECONDS = 5.0

PUBLIC_LITERALS = (
    LINE_NOT_OBSERVED,
    LINE_CONFLICT,
    LINE_FILTER_ERROR,
    "channel=S",
    "origin=unattributed",
    "first_bash=missing",
    "first_bash=ambiguous",
    "first_bash=bound",
)


def stop_commands_token() -> str:
    """Kernel CSPRNG. Never derived from github.token (D10 §2.6)."""
    return os.urandom(16).hex()


def wrap_stop_commands(body: str, token: Optional[str] = None) -> str:
    """G3: the only ``::`` lines are the wrapper. Body never starts with ``::``."""
    if token is None:
        token = stop_commands_token()
    if not token or any(ch not in "0123456789abcdef" for ch in token):
        raise ValueError("token")
    if "::" in token or "\n" in token:
        raise ValueError("token")
    lines = []
    for line in body.split("\n"):
        if line.startswith("::"):
            raise ValueError("body")
    lines.append(f"::stop-commands::{token}")
    lines.append(body.rstrip("\n"))
    lines.append(f"::{token}::")
    return "\n".join(lines) + "\n"


def public_literal_line(result: FilterResult) -> str:
    if result.status == STATUS_OBSERVED:
        assert result.reconstructed is not None
        assert result.reconstructed.startswith(LINE_OBSERVED_PREFIX)
        assert not result.reconstructed.startswith("::")
        return result.reconstructed
    if result.status == STATUS_CONFLICT:
        return LINE_CONFLICT
    return LINE_NOT_OBSERVED


def not_relayed_line(reason: str) -> str:
    if reason not in NOT_RELAYED_REASONS:
        raise ValueError("reason")
    return LINE_NOT_RELAYED_PREFIX + reason


def format_public_body(
    result: FilterResult,
    e3_text: Optional[str],
    *,
    first_bash: str = "missing",
    origin: str = ORIGIN,
    channel: str = CHANNEL,
) -> str:
    """Constant-alphabet body. ``first_bash=bound`` is not clause 3."""
    if first_bash not in FIRST_BASH_VALUES:
        raise ValueError("first_bash")
    if origin != ORIGIN:
        # E4 parked: do not emit any other origin.
        raise ValueError("origin")
    if channel != CHANNEL:
        raise ValueError("channel")
    parts = [public_literal_line(result)]
    if e3_text:
        parts.append(e3_text.rstrip("\n"))
    parts.append(f"channel={channel}")
    parts.append(f"first_bash={first_bash}")
    parts.append(f"origin={origin}")
    return "\n".join(parts)


def relay_print(
    capture: bytes,
    e3_fields: Optional[Mapping[str, str]] = None,
    *,
    first_bash: str = "missing",
    min_wall_seconds: float = 0.0,
    token: Optional[str] = None,
) -> str:
    """Filter + wrap. On any unexpected error, the catch-all string only."""
    started = time.monotonic()
    try:
        result = filter_capture(capture)
        e3_text = None
        if e3_fields is not None:
            e3_text = reconstruct_e3_record(e3_fields)
        body = format_public_body(result, e3_text, first_bash=first_bash)
        text = wrap_stop_commands(body, token=token)
    except Exception:
        text = wrap_stop_commands(LINE_FILTER_ERROR, token=token)
    elapsed = time.monotonic() - started
    remain = min_wall_seconds - elapsed
    if remain > 0:
        time.sleep(remain)
    return text


def shred_capture(path: str) -> None:
    """Overwrite-then-unlink the capture. Best-effort; never raises."""
    try:
        with open(path, "r+b") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(0)
            remaining = min(size, CAP_BYTES)
            zeros = b"\0" * 65536
            while remaining:
                chunk = zeros if remaining >= 65536 else b"\0" * remaining
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError:
        try:
            os.remove(path)
        except OSError:
            return


def _read_exactly(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(CAP_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return data[:CAP_BYTES]


def main(argv: Sequence[str]) -> int:
    # D10: ignore argv. Do not open, stat, map, or exec {0}.
    del argv
    sys.tracebacklimit = 0
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        pass
    try:
        if not os.path.exists(END_PATH):
            sys.stdout.write(
                wrap_stop_commands(not_relayed_line("no_end"))
            )
            return 0
        shape = live_parent_shape()
        write_records(os.path.dirname(END_PATH), shape=shape)
        if not accept_parent_shape(shape):
            sys.stdout.write(
                wrap_stop_commands(not_relayed_line("bad_parent"))
            )
            return 0
        capture = _read_exactly(CAPTURE_PATH)
        if capture is None:
            sys.stdout.write(
                wrap_stop_commands(not_relayed_line("no_end"))
            )
            return 0
        e3_fields = None
        if os.path.exists(E3_RECORD_PATH):
            with open(E3_RECORD_PATH, "r", encoding="ascii") as handle:
                from sandbox_e3_record import parse_e3_text

                e3_fields = parse_e3_text(handle.read())
        sys.stdout.write(
            relay_print(
                capture,
                e3_fields,
                first_bash="missing",
                min_wall_seconds=MIN_WALL_SECONDS,
            )
        )
        shred_capture(CAPTURE_PATH)
        return 0
    except E3RecordError:
        sys.stdout.write(wrap_stop_commands(LINE_FILTER_ERROR))
        shred_capture(CAPTURE_PATH)
        return 0
    except Exception:
        sys.stdout.write(wrap_stop_commands(LINE_FILTER_ERROR))
        shred_capture(CAPTURE_PATH)
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
