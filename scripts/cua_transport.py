"""Small persistent stdio client for the Cua Driver MCP server.

The Driver owns sessions per MCP connection.  A short-lived ``cua-driver
call`` process therefore cannot safely perform capture followed by act and
confirm.  This client owns one MCP child for the parent process and never
retries a request after its connection becomes uncertain.
"""
from __future__ import annotations

import atexit
import json
import os
import select
import subprocess
import threading
import time


class TransportError(RuntimeError):
    """A request could not be delivered or its result could not be read."""


class DriverTransport:
    def __init__(self, binary: str, session: str):
        self.binary = binary
        self.session = session
        self._lock = threading.RLock()
        self._next_id = 0
        self._proc: subprocess.Popen | None = None

    def _deadline(self, timeout: int) -> float:
        return time.monotonic() + max(1, int(timeout))

    def _start(self, deadline: float) -> None:
        if self._proc and self._proc.poll() is None:
            return
        self.close()
        try:
            # stderr is deliberately discarded: an unread pipe can deadlock a
            # long-lived child and Driver diagnostics are returned over MCP.
            self._proc = subprocess.Popen(
                [self.binary, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
            self._request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "rig-cua", "version": "1"},
            }, deadline)
            self._notify("notifications/initialized", {}, deadline)
            self._request("tools/call", {
                "name": "start_session", "arguments": {"session": self.session},
            }, deadline)
        except Exception:
            self.close()
            raise

    def _write(self, payload: dict, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("cua-driver transport timed out")
        if not self._proc or not self._proc.stdin:
            raise TransportError("cua-driver MCP stdin is unavailable")
        try:
            self._proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise TransportError(f"cua-driver MCP write failed: {error}") from error

    def _read_response(self, request_id: int, deadline: float) -> dict:
        if not self._proc or not self._proc.stdout:
            raise TransportError("cua-driver MCP stdout is unavailable")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("cua-driver transport timed out")
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                raise TimeoutError("cua-driver transport timed out")
            line = self._proc.stdout.readline()
            if not line:
                raise TransportError("cua-driver MCP connection ended")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != request_id:
                continue
            if "error" in response:
                message = response.get("error") or {}
                raise TransportError(str(message.get("message") if isinstance(message, dict) else message))
            result = response.get("result")
            if not isinstance(result, dict):
                raise TransportError("cua-driver MCP returned a malformed tool result")
            return result

    def _request(self, method: str, params: dict, deadline: float) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, deadline)
        return self._read_response(request_id, deadline)

    def _notify(self, method: str, params: dict, deadline: float) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params}, deadline)

    def call(self, tool: str, arguments: dict, timeout: int) -> dict:
        with self._lock:
            deadline = self._deadline(timeout)
            try:
                self._start(deadline)
                return self._request("tools/call", {"name": tool, "arguments": arguments}, deadline)
            except Exception:
                # The outcome of the current request is unknown.  Destroy the
                # channel and let the caller require a fresh capture; never
                # replay an action on a replacement connection.
                self.close()
                raise

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if not proc:
            return
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)


_CLIENTS: dict[tuple[str, str], DriverTransport] = {}
_CLIENTS_LOCK = threading.Lock()


def call(binary: str, session: str, tool: str, arguments: dict, timeout: int) -> dict:
    key = (os.path.realpath(binary), session)
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = _CLIENTS[key] = DriverTransport(binary, session)
    return client.call(tool, arguments, timeout)


def close_all() -> None:
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        client.close()


atexit.register(close_all)
