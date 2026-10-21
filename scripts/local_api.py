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
import subprocess
import sys
import threading
import time
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
    "recovery.guide": ("rig_recovery_guide", False, "Recorded recovery evidence for a job or workflow"),
    "workflows.list": ("rig_workflows", False, "List workflows"),
    "workflows.show": ("rig_workflow_show", False, "Show one workflow"),
    "job.allow": ("rig_job_allow", True, "Allow a worker's pending command"),
    "job.deny": ("rig_job_deny", True, "Deny a worker's pending command"),
    "job.cancel": ("rig_job_cancel", True, "Request a job stop; not confirmation"),
    "job.message": ("rig_job_message", True, "Send a message to a running worker"),
}
# method -> (mutates state, one-line summary); handled by the server itself.
LOCAL_METHODS = {
    "api.ping": (False, "Check the server and protocol version"),
    "api.methods": (False, "List the methods this server offers"),
    "board.snapshot": (False, "Board data (jobs window, pending queue, slots, settings, workflows) plus a ready-to-render `view`"),
    "workspace.status": (False, "Workspace panel: container, ports and dc-cli diagnosis as ready-to-render sections (params: deep)"),
    "chat.status": (False, "EXPERIMENT: whether the headless-parent chat is on, which parents can run, and recent sessions"),
    "chat.transcript": (False, "EXPERIMENT: a chat session's transcript (params: session, tail)"),
    "chat.send": (True, "EXPERIMENT: send one message to the headless parent; the answer arrives in the transcript (params: message, session, parent)"),
    "chat.cancel": (True, "EXPERIMENT: stop a chat session's running turn (params: session)"),
    "chat.parent": (True, "Name the project's parent CLI in .rig/harness.toml, as rig use does (params: name)"),
    "chat.answer": (True, "Allow or deny the parent's pending permission request in a chat session (params: session, ask_id, decision)"),
    "log.follow": (False, "Tail of a job's log as plain text; without an id, the job worth watching (params: id, lines)"),
    "shell.parent": (False, "The command that runs the parent CLI in a board pane, or why there is none (params: parent)"),
    "env.shell": (False, "The dc exec --no-start command for an interactive shell in a job's container, or why there is none (params: id)"),
    "job.evidence": (False, "Evidence drawer for one job: where it ran, change, contract, checks, ports (params: id)"),
    "fleet.status": (False, "Fleet panel: workspace containers on the engine with observed CPU and memory, ready to render"),
    "settings.view": (False, "Settings tab: what this repository's .rig/harness.toml makes effective, ready to render and read-only"),
    "env.recovery": (False, "The one next step dc-cli proposes for an environment fault, and whether Rig would apply it"),
    "env.recover": (True, "Apply the confirmed environment recovery step once, if it is safe (params: confirm_id)"),
    "events.list": (False, "Notices for a companion session: approvals, finished work, verification, stop progress"),
    "events.ack": (True, "Acknowledge one notice, or all, for a companion session"),
    "ui.request": (True, "Companion operations (snapshot, enqueue, stop, reply, details, sessions) on the hosted UI service"),
}

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
        self._service = None
        self._service_lock = threading.Lock()

    @property
    def service(self):
        """The companion UI service, created on first use so plain API clients
        never create ``.rig/ui`` or start an observer."""
        with self._service_lock:
            if self._service is None:
                import ui_service
                service = ui_service.Service(self.repo)
                threading.Thread(target=service.collect, daemon=True, name="rig-ui-observer").start()

                def maintain():
                    while not service.closed.wait(5):
                        service.prune_sessions()

                threading.Thread(target=maintain, daemon=True, name="rig-ui-session-check").start()
                self._service = service
            return self._service

    def busy(self) -> bool:
        """True while companion sessions or accepted actions need the server alive."""
        service = self._service
        return bool(service and (service.sessions or service.actions.running))

    def close(self):
        service = self._service
        if service is not None:
            service.closed.set()
            service.refresh.set()

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
            rows += [{"method": name, "mutates": mutates, "summary": summary}
                     for name, (mutates, summary) in sorted(LOCAL_METHODS.items())]
            return _reply(request_id, result={"version": API_VERSION, "methods": rows})
        if method == "board.snapshot":
            return self._board_snapshot(request_id, params)
        if method == "workspace.status":
            return self._workspace(request_id, params)
        if method in ("job.evidence", "fleet.status", "settings.view", "env.recovery", "env.recover", "log.follow", "env.shell", "shell.parent",
                      "chat.status", "chat.transcript", "chat.send", "chat.cancel", "chat.answer", "chat.parent"):
            return self._environment(request_id, method, params)
        if method in ("ui.request", "events.list", "events.ack"):
            return self._companion(request_id, method, params)
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
        structured = outcome.get("structuredContent")
        if isinstance(structured, (dict, list)):
            result["structured"] = scrub(structured)
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            result["json"] = scrub(parsed)
        return _reply(request_id, result=result)


def _view_int(params, name, default, low, high):
    value = params.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def _board_snapshot(self, request_id, params):
    """Read-only board data, built by the same loader the in-process board uses."""
    try:
        start = _view_int(params, "start", 0, 0, 1_000_000)
        rows = _view_int(params, "rows", 20, 0, 500)
        selected = params.get("selected_id")
        if selected is not None and (not isinstance(selected, str) or len(selected) > 200):
            raise ValueError("selected_id must be a short string")
    except ValueError as error:
        return _reply(request_id, error=_error(INVALID_PARAMS, str(error)))
    import tui_runtime
    import ui_snapshot
    try:
        snap = tui_runtime.load_snapshot(self.repo, start=start, rows=rows, selected_id=selected or None)
        workflows = ui_snapshot.collect_workflows(self.repo)
    except (Exception, SystemExit) as error:
        return _reply(request_id, error=_error(SERVER_ERROR, str(error) or "board snapshot failed"))
    import board_view
    result = scrub({"jobs": snap.jobs, "pending": snap.pending, "slots": snap.slots, "cap": snap.cap,
                    "settings": snap.settings, "workflows": workflows, "captured_wall": time.time(),
                    "view": board_view.build(snap.jobs, snap.pending, workflows, snap.slots, snap.cap, self.repo)})
    return _reply(request_id, result=json.loads(json.dumps(result, default=str)))


Api._board_snapshot = _board_snapshot


def _workspace(self, request_id, params):
    deep = params.get("deep", False)
    if deep not in (True, False):
        return _reply(request_id, error=_error(INVALID_PARAMS, "deep must be a boolean"))
    import workspace
    try:
        facts = workspace.status(self.repo, deep=bool(deep))
        result = {"status": facts, "view": workspace.view(facts)}
    except (Exception, SystemExit) as error:
        return _reply(request_id, error=_error(SERVER_ERROR, str(error) or "workspace status failed"))
    return _reply(request_id, result=json.loads(json.dumps(scrub(result), default=str)))


Api._workspace = _workspace


def _environment(self, request_id, method, params):
    try:
        if method == "job.evidence":
            job_id = params.get("id")
            if not isinstance(job_id, str) or not job_id:
                return _reply(request_id, error=_error(INVALID_PARAMS, "id must be a job id"))
            import job_evidence_view
            result = job_evidence_view.build(self.repo, job_id)
        elif method == "log.follow":
            lines = params.get("lines", 60)
            job_id = params.get("id", "")
            if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= 500 or not isinstance(job_id, str):
                return _reply(request_id, error=_error(INVALID_PARAMS, "lines must be 1 to 500 and id a string"))
            import rig_shell
            result = rig_shell.follow_frame(self.repo, job_id, lines)
        elif method == "shell.parent":
            parent = params.get("parent", "")
            if not isinstance(parent, str):
                return _reply(request_id, error=_error(INVALID_PARAMS, "parent must be a parent CLI name"))
            import rig_shell
            result = rig_shell.parent_pane_plan(self.repo, parent)
        elif method == "env.shell":
            job_id = params.get("id", "")
            if not isinstance(job_id, str):
                return _reply(request_id, error=_error(INVALID_PARAMS, "id must be a string"))
            import env_shell
            result = env_shell.plan(self.repo, job_id)
        elif method.startswith("chat."):
            import rig_chat
            session, tail = params.get("session", ""), params.get("tail", 200)
            if not isinstance(session, str) or not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= 5000:
                return _reply(request_id, error=_error(INVALID_PARAMS, "session must be a string and tail 1 to 5000"))
            if method == "chat.status":
                result = rig_chat.status(self.repo)
            elif method == "chat.transcript":
                result = rig_chat.transcript(self.repo, session, tail)
            elif method == "chat.cancel":
                result = rig_chat.cancel(self.repo, session)
            elif method == "chat.parent":
                name = params.get("name")
                if not isinstance(name, str):
                    return _reply(request_id, error=_error(INVALID_PARAMS, "name must be a string"))
                rig_chat.require_enabled(self.repo)
                result = rig_chat.set_parent(self.repo, name)
            elif method == "chat.answer":
                ask_id, decision = params.get("ask_id"), params.get("decision")
                if not isinstance(ask_id, str) or decision not in ("allow", "deny"):
                    return _reply(request_id, error=_error(INVALID_PARAMS, "ask_id must be a string and decision allow or deny"))
                result = rig_chat.answer(self.repo, session, ask_id, decision)
            else:
                message, parent = params.get("message"), params.get("parent", "")
                if not isinstance(message, str) or not isinstance(parent, str):
                    return _reply(request_id, error=_error(INVALID_PARAMS, "message and parent must be strings"))
                rig_chat.require_enabled(self.repo)
                result = rig_chat.send(self.repo, message, session, parent)
        elif method == "fleet.status":
            import fleet
            data = fleet.facts(self.repo)
            result = {"facts": data, "view": fleet.view(data)}
        elif method == "settings.view":
            import settings_view
            result = {"view": settings_view.view(self.repo)}
        elif method == "env.recovery":
            import env_recovery
            result = env_recovery.plan(self.repo)
        else:
            confirm = params.get("confirm_id")
            if not isinstance(confirm, str) or not confirm:
                return _reply(request_id, error=_error(INVALID_PARAMS, "confirm_id must be the plan id the owner confirmed"))
            import env_recovery
            result = env_recovery.apply(self.repo, confirm, actor="local-api")
    except ValueError as error:  # includes ChatError
        return _reply(request_id, error=_error(INVALID_PARAMS, str(error)))
    except (Exception, SystemExit) as error:
        return _reply(request_id, error=_error(SERVER_ERROR, str(error) or f"{method} failed"))
    return _reply(request_id, result=json.loads(json.dumps(scrub(result), default=str)))


Api._environment = _environment


def _companion(self, request_id, method, params):
    if method == "ui.request":
        payload = params.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("op"), str):
            return _reply(request_id, error=_error(INVALID_PARAMS, "payload must be an object with an op"))
        if payload["op"] == "ping":
            return _reply(request_id, result={"ok": True, "repo": str(self.repo)})
        try:
            result = self.service.handle(payload)
        except (Exception, SystemExit) as error:
            # Same shape the companion socket always returned for a failed operation.
            result = {"error": str(error) or "UI request failed", "status": "error"}
        return _reply(request_id, result=json.loads(json.dumps(scrub(result), default=str)))
    session = params.get("session", "")
    if not isinstance(session, str) or len(session) > 160:
        return _reply(request_id, error=_error(INVALID_PARAMS, "session must be a short string"))
    service = self.service
    with service.lock:
        if method == "events.list":
            return _reply(request_id, result={"notices": json.loads(json.dumps(service.notices.list(session), default=str))})
        notice_id = params.get("notice_id", "")
        if not isinstance(notice_id, str) or params.get("all", False) not in (True, False):
            return _reply(request_id, error=_error(INVALID_PARAMS, "notice_id must be a string and all a boolean"))
        service.notices.ack(session, notice_id, all=bool(params.get("all", False)))
        return _reply(request_id, result={"ok": True})


Api._companion = _companion


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


def serve(repo, idle: float = 0.0) -> int:
    if (os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR") or "").strip():
        print("rig api: a worker child may not serve the API", file=sys.stderr)
        return 1
    import fcntl
    import harness
    repo = Path(repo).resolve()
    if harness.project_state(repo)["state"] == "uninitialized":
        raise ValueError("Rig is uninitialized for this project; run rig init")
    endpoint, token_path = runtime_paths(repo)
    with (endpoint.parent / "api.run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("rig api: already running for this repository", file=sys.stderr)
            return 2
        import admission
        from ui_store import write as write_private
        write_private(endpoint.parent / "owner.json", {"pid": os.getpid(),
                      "start_id": admission.process_identity(os.getpid()).get("start_id"), "repo": str(repo)})
        token = secrets.token_hex(32)
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(token)
        token_path.chmod(0o600)
        endpoint.unlink(missing_ok=True)
        api = Api(repo, token)
        slots = threading.BoundedSemaphore(8)
        last_request = [time.monotonic()]

        def handle(connection):
            last_request[0] = time.monotonic()
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
            listener.settimeout(1.0)
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
            print(f"rig api: listening for {repo}", file=sys.stderr)
            try:
                while True:
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        if idle and time.monotonic() - last_request[0] > idle and not api.busy():
                            break
                        continue
                    if slots.acquire(blocking=False):
                        threading.Thread(target=handle, args=(connection,), daemon=True).start()
                    else:
                        connection.close()
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                api.close()
                endpoint.unlink(missing_ok=True)
                token_path.unlink(missing_ok=True)
                (endpoint.parent / "owner.json").unlink(missing_ok=True)
    return 0


def ensure(repo, idle: float = 60.0) -> bool:
    """Start a detached server if none answers. Returns True when one was started.

    Racing starters are safe: the server holds an exclusive lock and a second
    instance exits at once. The idle timeout means a server never outlives its
    last client for long.
    """
    try:
        call(repo, "api.ping", timeout=0.5)
        return False
    except (ConnectionError, OSError, ValueError):
        pass
    subprocess.Popen([sys.executable, str(HERE / "local_api.py"), "serve", "--repo", str(repo), "--idle", str(idle)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)
    return True


def call(repo, method, params=None, timeout=10.0) -> dict:
    """Client helper: returns the full JSON-RPC response."""
    endpoint, token_path = runtime_paths(Path(repo).resolve())
    return call_at(endpoint, token_path, method, params, timeout)


def call_at(endpoint, token_path, method, params=None, timeout=10.0) -> dict:
    try:
        token = Path(token_path).read_text().strip()
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
             f"`\"rig_api\": {API_VERSION}`. A method returns `{{\"text\": ...}}` plus `json` when the text",
             "parses as a JSON object or array and `structured` when the tool returns a",
             "structured result (queue items, recovery guidance). Tool errors",
             f"are JSON-RPC errors with code {SERVER_ERROR}. `repo` in params is ignored: the server is bound to one",
             "repository. Owner tokens are never returned, and workers may not serve the API.", "",
             "| Method | Mutates | MCP tool | Summary |", "| --- | --- | --- | --- |"]
    for name, (tool, mutates, summary) in sorted(METHODS.items()):
        lines.append(f"| `{name}` | {'yes' if mutates else 'no'} | `{tool}` | {summary} |")
    for name, (mutates, summary) in sorted(LOCAL_METHODS.items()):
        lines.append(f"| `{name}` | {'yes' if mutates else 'no'} | none | {summary} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig api", description="Rig local API")
    parser.add_argument("command", choices=["serve", "call", "methods", "doc", "ensure"])
    parser.add_argument("method", nargs="?")
    parser.add_argument("params", nargs="?", default="{}")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--idle", type=float, default=0.0, help="serve: exit after this many idle seconds (0 = never)")
    args = parser.parse_args(argv)
    if args.command == "doc":
        sys.stdout.write(render_doc())
        return 0
    try:
        import jobs
        args.repo = str(jobs.repo_root(args.repo or None))
        if args.command == "serve":
            return serve(args.repo, max(0.0, args.idle))
    except (ValueError, SystemExit) as error:
        print(f"rig api: {error}", file=sys.stderr)
        return 1
    if args.command == "ensure":
        # Start a detached idle-limited server if needed and wait until it answers.
        local_api_started = ensure(args.repo, idle=args.idle or 60.0)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                call(args.repo, "api.ping", timeout=0.5)
                print("started" if local_api_started else "running")
                return 0
            except (ConnectionError, OSError, ValueError):
                time.sleep(0.05)
        print("rig api: server did not start in time", file=sys.stderr)
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
