"""Stdio MCP client for the separately installed ``vibe-trading-mcp`` binary.

One short-lived process per tool call. The child environment is a scrubbed
copy of PATH and locale — cockpit settings and credential-shaped variables are
not passed through. Trading tools are refused before the binary is resolved,
so a missing install and a denied tool are different failures.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from research.vibe_trading.policy import assert_callable, prepare_arguments

_MAX_BODY = 8_000_000
# HOME is not inherited: a shared home is how file-backed keyrings and the sidecar
# config dir would see cockpit credentials. A scratch HOME is set per spawn.
# VIBE_TRADING_ALLOWED_RUN_ROOTS is the sidecar's own run-root allowlist.
_KEEP_ENV = (
    "PATH", "LANG", "LC_ALL", "SYSTEMROOT",
    "VIBE_TRADING_ALLOWED_RUN_ROOTS",
)


class SidecarUnavailable(Exception):
    """The research binary is not installed, or the cockpit refused to start it."""


class SidecarProtocolError(Exception):
    """The process spoke something other than a single JSON-RPC object."""


class SidecarTimeout(Exception):
    """The process did not answer before the deadline."""


def _child_env() -> dict[str, str]:
    return {key: os.environ[key] for key in _KEEP_ENV if key in os.environ}


def _write_message(stdin, payload: dict) -> None:
    # MCP stdio is newline-delimited JSON-RPC (not LSP Content-Length). FastMCP
    # rejects a Content-Length header as invalid JSON and then waits forever.
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > _MAX_BODY:
        raise SidecarProtocolError("sidecar request exceeded max body")
    stdin.write(body + b"\n")
    stdin.flush()


def _read_message(stdout) -> dict:
    line = stdout.readline(_MAX_BODY + 1)
    if not line:
        raise SidecarProtocolError("sidecar closed stdout")
    if len(line) > _MAX_BODY:
        raise SidecarProtocolError("sidecar line exceeded max body")
    try:
        parsed = json.loads(line)
    except ValueError as exc:
        raise SidecarProtocolError("sidecar line was not JSON") from exc
    if type(parsed) is not dict:
        raise SidecarProtocolError("sidecar body was not an object")
    return parsed


def _payload_from_result(message: dict) -> dict:
    if "error" in message:
        raise SidecarProtocolError("sidecar returned a JSON-RPC error")
    result = message.get("result")
    if type(result) is not dict:
        raise SidecarProtocolError("sidecar result was not an object")
    if result.get("isError") is True:
        raise SidecarProtocolError("sidecar tool returned isError")
    content = result.get("content")
    if type(content) is not list:
        raise SidecarProtocolError("sidecar result had no content list")
    texts = [
        item.get("text", "")
        for item in content
        if type(item) is dict and item.get("type") == "text" and type(item.get("text")) is str
    ]
    raw = "\n".join(texts).strip()
    if not raw:
        raise SidecarProtocolError("sidecar tool returned no text")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise SidecarProtocolError("sidecar tool text was not JSON") from exc
    if type(parsed) is not dict:
        raise SidecarProtocolError("sidecar tool JSON was not an object")
    return parsed


class StdioMcpClient:
    """Speak MCP stdio to ``command``. ``command`` is a PATH basename, never a shell line."""

    def __init__(self, command: str = "vibe-trading-mcp", *, timeout_s: float = 60):
        if type(command) is not str or not command or "/" in command or "\\" in command:
            raise SidecarUnavailable("sidecar command rejected")
        self._command = command
        self._timeout_s = float(timeout_s)

    def call(self, tool: str, arguments: dict) -> dict:
        # Denial wins over "binary missing": a trading name never resolves or spawns.
        cleaned = prepare_arguments(tool, arguments)
        assert_callable(tool)
        resolved = shutil.which(self._command)
        if resolved is None:
            raise SidecarUnavailable(f"{self._command} is not installed")
        return self._exchange(resolved, tool, cleaned)

    def _exchange(self, resolved: str, tool: str, arguments: dict) -> dict:
        scratch = tempfile.mkdtemp(prefix="vibe-trading-home-")
        try:
            return self._exchange_in(resolved, tool, arguments, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _exchange_in(self, resolved: str, tool: str, arguments: dict, scratch: str) -> dict:
        env = _child_env()
        env["HOME"] = scratch
        env["TMPDIR"] = scratch
        env["TEMP"] = scratch
        env["TMP"] = scratch
        proc = subprocess.Popen(
            [resolved],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            shell=False,
        )
        messages: queue.Queue = queue.Queue()

        def _pump() -> None:
            try:
                while True:
                    messages.put(_read_message(proc.stdout))
            except Exception as exc:  # noqa: BLE001 - the reader thread must not die silent
                messages.put(exc)

        thread = threading.Thread(target=_pump, name="vibe-trading-mcp", daemon=True)
        thread.start()
        deadline = time.monotonic() + self._timeout_s
        try:
            _write_message(proc.stdin, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cockpit-vibe-research", "version": "1"},
                },
            })
            self._next(messages, deadline, expect_id=1)
            _write_message(proc.stdin, {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })
            _write_message(proc.stdin, {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            })
            reply = self._next(messages, deadline, expect_id=2)
            return _payload_from_result(reply)
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _next(self, messages: queue.Queue, deadline: float, *, expect_id: int) -> dict:
        # The server may emit notifications. Bound how many we skip.
        for _ in range(8):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SidecarTimeout("sidecar timed out")
            try:
                item = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise SidecarTimeout("sidecar timed out") from exc
            if isinstance(item, Exception):
                raise item
            if item.get("id") == expect_id:
                return item
        raise SidecarProtocolError("sidecar did not answer the request")
