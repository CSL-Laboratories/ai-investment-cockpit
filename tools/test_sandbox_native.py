#!/usr/bin/env python3
"""Compile-and-run tests for the D10 static launcher/relay/filter (CSL-474).

Run: python3 -X utf8 -B tools/test_sandbox_native.py
"""
from __future__ import annotations

import hashlib
import os
import re
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


C_PIN_FILES = (
    "sandbox_common.h",
    "sandbox_common.c",
    "sandbox_filter.h",
    "sandbox_filter.c",
    "sandbox_argv.h",
    "sandbox_argv.c",
    "sandbox_launch.c",
    "sandbox_capture.c",
    "sandbox_relay.c",
)
def _unindent_heredoc(body: str) -> str:
    lines = body.split("\n")
    out = []
    for line in lines:
        if line.startswith("          "):
            out.append(line[10:])
        else:
            out.append(line)
    text = "\n".join(out)
    if body.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    if not text.endswith("\n"):
        text += "\n"
    return text


def extract_heredoc(yml: str, tag: str) -> str:
    start = f"<<'{tag}'\n"
    idx = yml.find(start)
    if idx < 0:
        raise AssertionError(f"missing heredoc {tag}")
    idx += len(start)
    match = re.search(rf"\n[ \t]*{re.escape(tag)}\n", yml[idx:])
    if not match:
        raise AssertionError(f"unterminated heredoc {tag}")
    return _unindent_heredoc(yml[idx : idx + match.start()] + "\n")


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

    def test_argv_requires_setting_sources_and_tools(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in {
            "SRT_DEBUG", "DEBUG_CLAUDE_AGENT_SDK", "ACTIONS_STEP_DEBUG", "LD_PRELOAD", "LD_AUDIT",
        }}
        env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
        proc = subprocess.run(
            [str(self.bins["launch"]), "--max-turns", "30", "--allowedTools", "Read"],
            env=env,
            capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"launcher refused", proc.stderr)

    def test_n1_does_not_stat_cwd_mcp_json(self) -> None:
        src = (SRC / "sandbox_launch.c").read_text(encoding="utf-8")
        self.assertNotIn('mcp_exists("/.mcp.json")', src)
        self.assertNotIn('mcp_exists("/.claude/settings.json")', src)
        self.assertIn("settings.local.json", src)
        self.assertIn("strict-mcp-config", src)
        self.assertIn("CSL_MCP_EMPTY_PATH", src)


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
            state.mkdir(parents=True, exist_ok=True)
            (state / "worker.pid").write_text(f"{os.getpid()}\n", encoding="ascii")
            (state / "worker.pid").chmod(0o600)
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
                    self.assertEqual(reply, b"yes\n")
                finally:
                    match.close()
            finally:
                daemon.send_signal(15)
                try:
                    daemon.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait(timeout=2)


class EmbedPinTests(unittest.TestCase):
    def test_each_embedded_c_file_hashes_to_its_pin(self) -> None:
        pin_text = (SRC / "sandbox_c.pin").read_text(encoding="utf-8")
        want = {}
        for line in pin_text.splitlines():
            digest, name = line.split()
            want[name] = digest
        self.assertEqual(set(want), set(C_PIN_FILES))
        for name in C_PIN_FILES:
            data = (SRC / name).read_bytes()
            got = hashlib.sha256(data).hexdigest()
            self.assertEqual(got, want[name], name)
        yml = (ROOT / ".github" / "workflows" / "claude.yml").read_text(encoding="utf-8")
        names = {
            "CSL_C_COMMON_H": "sandbox_common.h",
            "CSL_C_COMMON_C": "sandbox_common.c",
            "CSL_C_FILTER_H": "sandbox_filter.h",
            "CSL_C_FILTER_C": "sandbox_filter.c",
            "CSL_C_ARGV_H": "sandbox_argv.h",
            "CSL_C_ARGV_C": "sandbox_argv.c",
            "CSL_C_LAUNCH": "sandbox_launch.c",
            "CSL_C_CAPTURE": "sandbox_capture.c",
            "CSL_C_RELAY": "sandbox_relay.c",
            "CSL_C_PIN": "sandbox_c.pin",
        }
        for tag, name in names.items():
            extracted = extract_heredoc(yml, tag)
            repo = (SRC / name).read_text(encoding="utf-8")
            if not repo.endswith("\n"):
                repo += "\n"
            self.assertEqual(extracted, repo, tag)
            digest = hashlib.sha256(extracted.encode("utf-8")).hexdigest()
            if name in want:
                self.assertEqual(digest, want[name], f"embed {name}")
            self.assertGreaterEqual(yml.count(f"<<'{tag}'"), 2)


if __name__ == "__main__":
    unittest.main()
