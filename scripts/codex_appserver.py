#!/usr/bin/env python3
"""A client for `codex app-server`: the protocol Codex's own IDE extensions use.

Newline-delimited JSON-RPC over stdio, with no "jsonrpc" field on the wire. A client sends `initialize`, then `initialized`,
starts or resumes a thread, starts a turn, and reads notifications (item started, deltas, item completed, turn completed)
until the turn ends. Approvals come as requests from the server that the client answers. Compared with `codex exec` or
`codex mcp-server` this carries every item with its fields, real interrupts, compaction and token usage, so a chat can
show what the native TUI shows. https://developers.openai.com/codex/app-server

This module is only the transport: spawning, matching a response to its request, a reader thread, a graceful interrupt.
What the messages mean to rig chat is in rig_chat.py.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading

TIMEOUT = object()  # get() found nothing in time


class AppServerError(Exception):
    """The server answered a request with an error, or stopped before it answered."""


class AppServer:
    def __init__(self, argv, cwd, env, errlog=subprocess.DEVNULL):
        self.proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errlog,
                                     text=True, errors="replace", env=env, start_new_session=True)
        # its own group: a cancel's SIGTERM to the runner's group must not kill it before terminate() has interrupted the turn
        self.messages: queue.Queue = queue.Queue()
        self.backlog: list[dict] = []  # what arrived while a request waited for its own answer
        self.next_id = 0
        self.thread_id = ""
        self.turn_id = ""
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                self.messages.put(message)
        self.messages.put(None)

    def send(self, obj: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as error:
            raise AppServerError("the server is not running") from error

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"method": method, "params": params or {}})

    def respond(self, request_id, result: dict | None = None, error: str = "") -> None:
        if error:
            self.send({"id": request_id, "error": {"code": -32601, "message": error}})
        else:
            self.send({"id": request_id, "result": result or {}})

    def request(self, method: str, params: dict, timeout: float = 30):
        """Send a request and wait for its answer; anything else that arrives meanwhile is kept for get()."""
        self.next_id += 1
        want = self.next_id
        self.send({"method": method, "id": want, "params": params})
        while True:
            try:
                message = self.messages.get(timeout=timeout)
            except queue.Empty as error:
                raise AppServerError(f"{method} did not answer in {int(timeout)}s") from error
            if message is None:
                raise AppServerError("the server stopped before it answered " + method)
            if message.get("id") == want and "method" not in message:
                if "error" in message:
                    detail = message["error"]
                    raise AppServerError(str(detail.get("message") if isinstance(detail, dict) else detail))
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            self.backlog.append(message)

    def get(self, timeout: float | None = None):
        """The next message, None when the server has ended, TIMEOUT when nothing came in time."""
        if self.backlog:
            return self.backlog.pop(0)
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return TIMEOUT

    def terminate(self) -> None:
        """What a cancel calls: ask the turn to stop (it ends as interrupted), and end the server if it does not."""
        if self.thread_id and self.turn_id:
            try:
                self.send({"method": "turn/interrupt", "id": 10_000_000, "params": {"threadId": self.thread_id, "turnId": self.turn_id}})
            except AppServerError:
                pass
            timer = threading.Timer(4.0, self._end)
            timer.daemon = True
            timer.start()
        else:
            self._end()

    def _end(self) -> None:
        try:
            self.proc.terminate()
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
