#!/usr/bin/env python3
"""Compile-and-run tests for the D10 static launcher/relay/filter (CSL-474).

Run: python3 -X utf8 -B tools/test_sandbox_native.py
"""
from __future__ import annotations

import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "ci"
WRAP = (
    b"[SandboxDebug] [Sandbox Linux] Wrapped command with bwrap "
    b"(network, filesystem, env, seccomp(unix-block) restrictions)\n"
)


def compile_prefix(prefix: Path) -> dict[str, Path]:
    out = prefix / "bin"
    out.mkdir(parents=True, exist_ok=True)
    (prefix / "state").mkdir(parents=True, exist_ok=True)
    define = f'-DCSL_PREFIX="{prefix}"'
    common = [str(SRC / "sandbox_common.c"), f"-I{SRC}", "-static", "-O2", "-Wall", "-Werror"]
    launch = out / "launch"
    capture = out / "capture-start"
    relay = out / "relay"
    subprocess.run(
        ["gcc", define, "-o", str(launch), str(SRC / "sandbox_launch.c"),
         str(SRC / "sandbox_argv.c"), *common],
        check=True,
        cwd=SRC,
    )
    subprocess.run(
        ["gcc", define, "-o", str(capture), str(SRC / "sandbox_capture.c"), *common],
        check=True,
        cwd=SRC,
    )
    subprocess.run(
        ["gcc", define, "-o", str(relay), str(SRC / "sandbox_relay.c"),
         str(SRC / "sandbox_filter.c"), *common],
        check=True,
        cwd=SRC,
    )
    return {"launch": launch, "capture": capture, "relay": relay}


class NativeArgvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.prefix = Path(cls.tmp.name)
        cls.bins = compile_prefix(cls.prefix)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_argv_rejects_skip_permissions(self) -> None:
        env = {**os.environ, "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}
        proc = subprocess.run(
            [str(self.bins["launch"]), "--dangerously-skip-permissions"],
            env=env,
            capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"launcher refused", proc.stderr)

    def test_u15_presence_refuses(self) -> None:
        env = {**os.environ, "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "SRT_DEBUG": "0"}
        proc = subprocess.run([str(self.bins["launch"])], env=env, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_ld_preload_refuses(self) -> None:
        env = {**os.environ, "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "LD_PRELOAD": "x"}
        proc = subprocess.run([str(self.bins["launch"])], env=env, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)


class NativeRelayOneShotTests(unittest.TestCase):
    def test_no_end_then_success_then_already_ran(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            bins = compile_prefix(prefix)
            state = prefix / "state"
            relay = bins["relay"]
            first = subprocess.run([str(relay)], capture_output=True, text=True)
            self.assertEqual(first.returncode, 0)
            self.assertIn("NOT RELAYED reason=no_end", first.stdout)
            (state / "end").write_text("ok\n", encoding="ascii")
            (state / "pair.closed").write_text("1\n", encoding="ascii")
            (state / "capture").write_bytes(WRAP)
            second = subprocess.run([str(relay)], capture_output=True, text=True)
            self.assertEqual(second.returncode, 0)
            self.assertNotIn("NOT RELAYED", second.stdout)
            self.assertIn("::stop-commands::", second.stdout)
            self.assertIn("[Sandbox Linux] Wrapped command with bwrap (", second.stdout)
            self.assertIn("channel=S", second.stdout)
            self.assertIn("origin=unattributed", second.stdout)
            self.assertTrue((state / "already_ran").is_file())
            self.assertFalse((state / "capture").exists())
            third = subprocess.run([str(relay)], capture_output=True, text=True)
            self.assertIn("NOT RELAYED reason=already_ran", third.stdout)

    def test_filter_error_does_not_require_shred_of_missing_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            bins = compile_prefix(prefix)
            first = subprocess.run([str(bins["relay"])], capture_output=True, text=True)
            self.assertIn("no_end", first.stdout)
            self.assertNotIn("filter error", first.stdout)


class NativeSocketTests(unittest.TestCase):
    def test_capture_daemon_binds_fifo_and_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            bins = compile_prefix(prefix)
            state = prefix / "state"
            daemon = subprocess.Popen(
                [str(bins["capture"])],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                sock_path = state / "handoff.sock"
                fifo_path = state / "stderr.fifo"
                for _ in range(50):
                    if sock_path.exists() and fifo_path.exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(sock_path.exists(), "handoff.sock")
                self.assertTrue(fifo_path.exists(), "stderr.fifo")
                mode = stat.S_IMODE(fifo_path.stat().st_mode)
                self.assertEqual(mode, 0o600)
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.settimeout(1)
                    client.connect(str(sock_path))
                finally:
                    client.close()
                control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    control.settimeout(1)
                    control.connect(str(state / "control.sock"))
                    control.sendall(b"STATUS\n")
                    reply = control.recv(256)
                    self.assertIn(b"end=", reply)
                    self.assertIn(b"worker=", reply)
                finally:
                    control.close()
                match = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    match.settimeout(1)
                    match.connect(str(state / "control.sock"))
                    match.sendall(f"WORKER_MATCH {os.getpid()}\n".encode("ascii"))
                    reply = match.recv(32)
                    self.assertIn(reply, (b"yes\n", b"no\n"))
                finally:
                    match.close()
            finally:
                daemon.send_signal(15)
                try:
                    daemon.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
