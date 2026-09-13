"""Repository-scoped UI observation and local actions, independent of host turns."""
from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from ui_actions import Actions
from ui_notices import Notices, status_line
from ui_store import private_directory, read, runtime_directory, write

MAX_REQUEST = 65536
MAX_RESPONSE = 8 * 1024 * 1024


def receive(stream, limit):
    data = bytearray()
    while b"\n" not in data:
        part = stream.recv(min(65536, limit + 1 - len(data)))
        if not part:
            raise ValueError("UI connection closed before response")
        data.extend(part)
        if len(data) > limit:
            raise ValueError("UI message exceeds size limit")
    value = json.loads(bytes(data).split(b"\n", 1)[0])
    if not isinstance(value, dict):
        raise ValueError("UI message must be an object")
    return value


def request(endpoint, payload, timeout=2.0):
    encoded = json.dumps(payload, ensure_ascii=False).encode() + b"\n"
    if len(encoded) > MAX_REQUEST:
        raise ValueError("UI request too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(timeout)
        stream.connect(str(endpoint))
        stream.sendall(encoded)
        return receive(stream, MAX_RESPONSE)


def ensure_service(repo):
    repo = Path(repo).resolve()
    if not (repo / ".rig" / "harness.toml").is_file():
        raise ValueError("Repository is not initialized for Rig")
    folder = runtime_directory(repo)
    endpoint = str(folder / "control.sock")
    with (folder / "start.lock").open("a") as lock:
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Rig observer startup busy")
                time.sleep(0.02)
        try:
            if request(endpoint, {"op": "ping"}, timeout=0.3).get("ok"):
                return endpoint
        except (OSError, ValueError):
            pass
        # The serving process holds this for its entire lifetime, including
        # initialization. Never unlink a socket belonging to a slow live server.
        with (folder / "run.lock").open("a") as running:
            try:
                fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    try:
                        if request(endpoint, {"op": "ping"}, timeout=0.2).get("ok"):
                            return endpoint
                    except (OSError, ValueError):
                        time.sleep(0.025)
                raise RuntimeError("Existing Rig observer is busy; no replacement was started")
        prior = read(folder / "owner.json", {})
        if prior.get("pid"):
            import admission
            identity = admission.process_identity(prior["pid"])
            if identity.get("start_id") == prior.get("start_id") and identity.get("start_id"):
                raise RuntimeError("Rig observer is not responding; existing process retained")
            if not identity.get("start_id"):
                try:
                    os.kill(int(prior["pid"]), 0)
                except ProcessLookupError:
                    pass
                else:
                    raise RuntimeError("Rig observer identity is unknown; restart it explicitly")
        Path(endpoint).unlink(missing_ok=True)
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("rig_ui.py")),
                                    "serve", "--repo", str(repo)], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Rig observer failed to start")
            try:
                if request(endpoint, {"op": "ping"}, timeout=0.2).get("ok"):
                    return endpoint
            except (OSError, ValueError):
                time.sleep(0.025)
        raise TimeoutError("Rig observer startup timed out; no second observer was started")


class Service:
    def __init__(self, repo, collector=None):
        from ui_snapshot import Collector
        self.repo = Path(repo).resolve()
        self.folder = private_directory(self.repo / ".rig" / "ui")
        self.collector = collector or Collector(self.repo)
        self.lock = threading.RLock()
        self.snapshot = {"version": 1, "repo": str(self.repo), "jobs": [], "pending": [],
                         "slots": 0, "cap": 3, "error": "Loading Rig status", "captured_at": None}
        self.observed_at = 0.0
        self.notices = Notices(self.folder / "notices.json")
        self.sessions = read(self.folder / "sessions.json", {})
        self.actions = Actions(self.repo, self.completed)
        self.closed = threading.Event()
        self.refresh = threading.Event()
        self.last_client = time.monotonic()

    def completed(self, action, result):
        with self.lock:
            if result.get("status") == "done" and action.get("op") == "enqueue":
                item = result["result"]
                self.notices.add("Queued: " + item.get("text", ""), dedup="queue:" + item["id"])
                self.notices.save()
            elif result.get("status") == "error":
                self.notices.add("Action failed: " + result.get("error", ""), attention=True)
                self.notices.save()
        self.refresh.set()

    def collect(self):
        while not self.closed.is_set():
            try:
                value = self.collector.collect()
                with self.lock:
                    if value.get("error"):
                        self.snapshot["error"] = value["error"]
                    else:
                        self.snapshot = value
                        self.observed_at = time.time()
                        self.notices.update(value)
            except (Exception, SystemExit) as error:
                with self.lock:
                    self.snapshot["error"] = str(error) or "Status observation failed"
            self.refresh.wait(1)
            self.refresh.clear()

    def handle(self, payload):
        op = payload.get("op")
        self.last_client = time.monotonic()
        if op == "ping":
            return {"ok": True, "repo": str(self.repo)}
        if op in {"enqueue", "cancel_queue", "stop", "reply"}:
            return self.actions.submit(payload)
        if op == "action":
            return self.actions.get(payload.get("action_id"))
        if op == "details":
            return {"job": self.collector.details(payload.get("job_id") or "")}
        with self.lock:
            session = str(payload.get("session") or "")[:160]
            if op == "snapshot":
                notices = self.notices.list(session)
                snapshot = {**self.snapshot, "snapshot_age": time.time() - self.observed_at if self.observed_at else 999,
                            "notices": notices}
                snapshot["status_line"] = status_line(snapshot, notices,
                    width=max(30, min(1000, int(payload.get("width") or 160))),
                    manager_key=payload.get("manager_key") or "F8", add_key=payload.get("add_key") or "F9")
                return snapshot
            if op == "ack":
                self.notices.ack(session, payload.get("notice_id", ""), all=bool(payload.get("all")))
                return {"ok": True}
            if op in {"register", "unregister"}:
                if not session:
                    raise ValueError("Session ID required")
                if op == "register":
                    self.sessions[session] = {k: v for k, v in payload.items() if k != "op"}
                else:
                    self.sessions.pop(session, None)
                write(self.folder / "sessions.json", self.sessions)
                return {"ok": True}
        raise ValueError("Unknown UI operation")

    def prune_sessions(self):
        import admission
        with self.lock:
            sessions = dict(self.sessions)
        dead = []
        for name, value in sessions.items():
            if not value.get("pid"):
                continue
            identity = admission.process_identity(value["pid"])
            if identity.get("start_id") and value.get("start_id"):
                if identity["start_id"] != value["start_id"]:
                    dead.append(name)
            else:
                try:
                    os.kill(int(value["pid"]), 0)
                except ProcessLookupError:
                    dead.append(name)
                except (OSError, ValueError):
                    pass
        for name in dead:
            self.handle({"op": "unregister", "session": name})
            try:
                from ui_install import remove_lease
                remove_lease(name)
            except (ImportError, OSError, ValueError):
                pass


def serve(repo):
    folder = runtime_directory(repo)
    with (folder / "run.lock").open("a") as running:
        try:
            fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 2
        return _serve(repo, folder)


def _serve(repo, folder):
    import admission
    endpoint = folder / "control.sock"
    slots = threading.BoundedSemaphore(8)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        endpoint.chmod(0o600)
        try:
            service = Service(repo)
        except BaseException:
            endpoint.unlink(missing_ok=True)
            raise
        identity = admission.process_identity(os.getpid())
        write(folder / "owner.json", {"pid": os.getpid(), "start_id": identity.get("start_id"), "repo": str(service.repo)})
        listener.listen(8)
        listener.settimeout(1)
        collector = threading.Thread(target=service.collect, daemon=True, name="rig-ui-observer")
        collector.start()

        def maintain():
            while not service.closed.wait(5):
                service.prune_sessions()

        threading.Thread(target=maintain, daemon=True, name="rig-ui-session-check").start()

        def handle(connection):
            with connection:
                connection.settimeout(2)
                try:
                    result = service.handle(receive(connection, MAX_REQUEST))
                except (Exception, SystemExit) as error:
                    result = {"error": str(error) or "UI request failed", "status": "error"}
                try:
                    connection.sendall(json.dumps(result, ensure_ascii=False, default=str).encode() + b"\n")
                except OSError:
                    pass
                finally:
                    slots.release()

        try:
            while not service.closed.is_set():
                try:
                    connection, _ = listener.accept()
                    if slots.acquire(blocking=False):
                        threading.Thread(target=handle, args=(connection,), daemon=True).start()
                    else:
                        connection.close()
                except socket.timeout:
                    pass
                if not service.sessions and not service.actions.running and time.monotonic() - service.last_client > 15:
                    break
        finally:
            service.closed.set()
            service.refresh.set()
            endpoint.unlink(missing_ok=True)
            (folder / "owner.json").unlink(missing_ok=True)
    return 0
