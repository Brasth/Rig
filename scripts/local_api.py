#!/usr/bin/env python3
"""Rig local API v1: JSON-RPC 2.0 over a private Unix socket.

Every method is a thin mapping onto an existing parent MCP tool, so the shell,
the CLI and agents share one implementation. The API adds no authority: the
same project-enabled and ownership checks run inside the tool handlers, and
owner tokens are never returned.

Wire format: one JSON object per line. Every request carries ``token`` (the
contents of ``api.token`` in the 0700 runtime directory). Every response carries
``"rig_api": 1``.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import signal
import socket
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

API_VERSION = 1
MAX_REQUEST = 65536
MAX_RESPONSE = 8 * 1024 * 1024
SERVER_ERROR = -32000
INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, PARSE_ERROR, UNAUTHORIZED = -32600, -32601, -32602, -32700, -32001

# method -> (parent MCP tool, mutates state, one-line summary)
METHODS = {
    "session": ("rig_session", False, "Routing and session view for a case"),
    "doctor": ("rig_doctor", False, "Readiness report"),
    "status": ("rig_status", False, "Parent and worker availability"),
    "jobs.list": ("rig_jobs", False, "List running and recent jobs"),
    "jobs.show": ("rig_job_show", False, "Show one job"),
    "jobs.log": ("rig_job_log", False, "Read a job's log"),
    "queue.list": ("rig_queue_list", False, "List queue items"),
    "queue.add": ("rig_queue_add", True, "Park a task in the queue"),
    "queue.cancel": ("rig_queue_cancel", True, "Cancel a pending queue item"),
    "workflows.list": ("rig_workflows", False, "List workflows"),
    "workflows.show": ("rig_workflow_show", False, "Show one workflow"),
    "job.allow": ("rig_job_allow", True, "Allow a worker's pending command"),
    "job.deny": ("rig_job_deny", True, "Deny a worker's pending command"),
    "job.cancel": ("rig_job_cancel", True, "Request a job stop; not confirmation"),
    "job.message": ("rig_job_message", True, "Send a message to a running worker"),
}
LOCAL_METHODS = {"api.ping": "Check the server and protocol version",
                 "api.methods": "List the methods this server offers"}

_SECRET = re.compile(r'("owner_token"\s*:\s*)"[^"]*"')


def scrub(value):
    """Defence in depth: owner tokens never leave through the API."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if k == "owner_token" else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _SECRET.sub(r'\1"[redacted]"', value)
    return value


def runtime_paths(repo):
    from ui_store import runtime_directory
    folder = runtime_directory(repo)
    return folder / "api.sock", folder / "api.token"


def _reply(request_id, *, result=None, error=None):
    body = {"jsonrpc": "2.0", "id": request_id, "rig_api": API_VERSION}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return body


def _error(code, message):
    return {"code": code, "message": message}


class Api:
    def __init__(self, repo, token):
        self.repo = Path(repo).resolve()
        self.token = token

    def dispatch(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _reply(None, error=_error(INVALID_REQUEST, "expected a JSON-RPC 2.0 request"))
        request_id = message.get("id")
        supplied = message.get("token")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), self.token.encode()):
            return _reply(request_id, error=_error(UNAUTHORIZED, "missing or invalid API token"))
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _reply(request_id, error=_error(INVALID_PARAMS, "params must be an object"))
        method = message["method"]
        if method == "api.ping":
            return _reply(request_id, result={"ok": True, "version": API_VERSION, "repo": str(self.repo)})
        if method == "api.methods":
            rows = [{"method": name, "mutates": mutates, "summary": summary}
                    for name, (_, mutates, summary) in sorted(METHODS.items())]
            rows += [{"method": name, "mutates": False, "summary": summary} for name, summary in sorted(LOCAL_METHODS.items())]
            return _reply(request_id, result={"version": API_VERSION, "methods": rows})
        entry = METHODS.get(method)
        if entry is None:
            return _reply(request_id, error=_error(METHOD_NOT_FOUND, f"unknown method {method}"))
        # The server is bound to one repository; a client cannot redirect it.
        params = {key: value for key, value in params.items() if key != "repo"}
        params["repo"] = str(self.repo)
        import rig_mcp
        outcome = rig_mcp.call_tool(entry[0], params)
        text = "\n".join(part.get("text", "") for part in outcome.get("content", []) if isinstance(part, dict))
        text = scrub(text)
        if outcome.get("isError"):
            return _reply(request_id, error=_error(SERVER_ERROR, text or "request failed"))
        result = {"text": text}
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            result["json"] = scrub(parsed)
        return _reply(request_id, result=result)


def _receive(connection):
    data = bytearray()
    while b"\n" not in data:
        part = connection.recv(min(65536, MAX_REQUEST + 1 - len(data)))
        if not part:
            raise ValueError("connection closed")
        data.extend(part)
        if len(data) > MAX_REQUEST:
            raise ValueError("request exceeds size limit")
    return bytes(data).split(b"\n", 1)[0]


def serve(repo) -> int:
    if (os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR") or "").strip():
        print("rig api: a worker child may not serve the API", file=sys.stderr)
        return 1
    import fcntl
    import harness
    repo = Path(repo).resolve()
    harness.assert_project_enabled(repo)
    endpoint, token_path = runtime_paths(repo)
    with (endpoint.parent / "api.run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("rig api: already running for this repository", file=sys.stderr)
            return 2
        token = secrets.token_hex(32)
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(token)
        token_path.chmod(0o600)
        endpoint.unlink(missing_ok=True)
        api = Api(repo, token)
        slots = threading.BoundedSemaphore(8)

        def handle(connection):
            with connection:
                connection.settimeout(5)
                try:
                    try:
                        message = json.loads(_receive(connection))
                    except ValueError:
                        response = _reply(None, error=_error(PARSE_ERROR, "invalid JSON request"))
                    else:
                        response = api.dispatch(message)
                    encoded = json.dumps(response, ensure_ascii=False, default=str).encode() + b"\n"
                    if len(encoded) > MAX_RESPONSE:
                        encoded = json.dumps(_reply(response.get("id"), error=_error(SERVER_ERROR, "response too large"))).encode() + b"\n"
                    connection.sendall(encoded)
                except OSError:
                    pass
                finally:
                    slots.release()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            endpoint.chmod(0o600)
            listener.listen(8)
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
            print(f"rig api: listening for {repo}", file=sys.stderr)
            try:
                while True:
                    connection, _ = listener.accept()
                    if slots.acquire(blocking=False):
                        threading.Thread(target=handle, args=(connection,), daemon=True).start()
                    else:
                        connection.close()
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                endpoint.unlink(missing_ok=True)
                token_path.unlink(missing_ok=True)
    return 0


def call(repo, method, params=None, timeout=10.0) -> dict:
    """Client helper: returns the full JSON-RPC response."""
    endpoint, token_path = runtime_paths(Path(repo).resolve())
    try:
        token = token_path.read_text().strip()
    except OSError as error:
        raise ConnectionError("Rig API is not running; start it with rig api serve") from error
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, "token": token}).encode() + b"\n"
    if len(payload) > MAX_REQUEST:
        raise ValueError("request too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(timeout)
        try:
            stream.connect(str(endpoint))
        except OSError as error:
            raise ConnectionError("Rig API is not running; start it with rig api serve") from error
        stream.sendall(payload)
        data = bytearray()
        while b"\n" not in data:
            part = stream.recv(65536)
            if not part:
                break
            data.extend(part)
            if len(data) > MAX_RESPONSE:
                raise ValueError("response exceeds size limit")
    return json.loads(bytes(data).split(b"\n", 1)[0])


def render_doc() -> str:
    lines = ["# Rig local API v1", "",
             "<!-- Generated by `python3 scripts/local_api.py doc`; do not edit. -->", "",
             "JSON-RPC 2.0, one JSON object per line, over `api.sock` in the private runtime",
             "directory. Start it with `rig api serve`. Each request carries `token` (the",
             "contents of `api.token` beside the socket, mode 0600). Every response carries",
             f"`\"rig_api\": {API_VERSION}`. A method returns `{{\"text\": ..., \"json\": ...}}` where `json`",
             "is present when the tool's text parses as a JSON object or array. Tool errors",
             f"are JSON-RPC errors with code {SERVER_ERROR}. `repo` in params is ignored: the server is bound to one",
             "repository. Owner tokens are never returned, and workers may not serve the API.", "",
             "| Method | Mutates | MCP tool | Summary |", "| --- | --- | --- | --- |"]
    for name, (tool, mutates, summary) in sorted(METHODS.items()):
        lines.append(f"| `{name}` | {'yes' if mutates else 'no'} | `{tool}` | {summary} |")
    for name, summary in sorted(LOCAL_METHODS.items()):
        lines.append(f"| `{name}` | no | none | {summary} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig api", description="Rig local API")
    parser.add_argument("command", choices=["serve", "call", "methods", "doc"])
    parser.add_argument("method", nargs="?")
    parser.add_argument("params", nargs="?", default="{}")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.command == "doc":
        sys.stdout.write(render_doc())
        return 0
    try:
        import jobs
        args.repo = str(jobs.repo_root(args.repo or None))
        if args.command == "serve":
            return serve(args.repo)
    except (ValueError, SystemExit) as error:
        print(f"rig api: {error}", file=sys.stderr)
        return 1
    method, params = args.method, args.params
    if args.command == "methods":
        method, params = "api.methods", "{}"
    if not method:
        parser.error("call needs a METHOD")
    try:
        response = call(args.repo, method, json.loads(params), args.timeout)
    except (ConnectionError, ValueError, OSError) as error:
        print(f"rig api: {error}", file=sys.stderr)
        return 1
    print(json.dumps(response, indent=2, ensure_ascii=False))
    return 1 if "error" in response else 0


if __name__ == "__main__":
    raise SystemExit(main())
