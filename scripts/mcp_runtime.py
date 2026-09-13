"""Bounded daemon request execution keeps the stdio reader available for Stop."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Request:
    id: object
    name: str
    args: dict
    params: dict
    stop: threading.Event = field(default_factory=threading.Event)
    bound: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    detached: bool = False
    targets: list = field(default_factory=list)
    thread: threading.Thread | None = None


class Runtime:
    def __init__(self, execute, write, abort):
        self.execute, self.write, self.abort = execute, write, abort
        self.lock = threading.RLock()
        self.requests = {}
        self.closed = False
        self.limits = {"wait": threading.BoundedSemaphore(16),
                       "cancel": threading.BoundedSemaphore(8),
                       "tool": threading.BoundedSemaphore(8)}
        self.cancellations = set()
        self.cancel_slots = threading.BoundedSemaphore(16)

    @staticmethod
    def key(rid):
        if isinstance(rid, bool) or not isinstance(rid, (str, int)):
            return None
        return type(rid), rid

    def start(self, rid, name, args, params):
        key = self.key(rid)
        if key is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32600, "message": "request id must be a string or integer"}}
        lane = "wait" if name in {"rig_job_wait", "rig_job_check", "rig_job_ask", "permission_prompt"} else "cancel" if name == "rig_job_cancel" else "tool"
        limit = self.limits[lane]
        with self.lock:
            if self.closed or key in self.requests or not limit.acquire(blocking=False):
                return {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32000, "message": "request busy; retry after active work completes"}}
            request = Request(rid, name, dict(args), params)
            self.requests[key] = request

        def run():
            try:
                result = self.execute(request)
            except (SystemExit, Exception) as error:
                result = {"isError": True, "content": [{"type": "text", "text": str(error) or "rig error"}]}
            finally:
                request.bound.set()
            try:
                # Completion/cancellation choose one outcome under the registry lock.
                with self.lock:
                    send = not (self.closed or request.cancelled or request.detached)
                    self.requests.pop(key, None)
                if send:
                    self.write({"jsonrpc": "2.0", "id": rid, "result": result})
            finally:
                limit.release()

        request.thread = threading.Thread(target=run, daemon=True, name=f"rig-{lane}")
        request.thread.start()
        return None

    def cancel(self, rid):
        key = self.key(rid)
        with self.lock:
            request = self.requests.get(key)
            if request is None or request.cancelled:
                return
            request.cancelled = True
            request.stop.set()
            if request.name != "rig_job_wait":
                return
            # Each wait has at most one cancellation worker; ordinary saturation
            # cannot consume this capacity. The number is bounded by active waits.
            if not self.cancel_slots.acquire(blocking=False):
                # Its own interrupted observer also publishes intent on exit.
                return
            self.cancellations.add(key)

        def cancel_bound():
            try:
                if request.bound.wait(5):
                    self.abort(request)
            except (SystemExit, Exception):
                # Errors are observable on the job; cancellation is fire-and-forget.
                pass
            finally:
                with self.lock:
                    self.cancellations.discard(key)
                self.cancel_slots.release()

        threading.Thread(target=cancel_bound, daemon=True, name="rig-stop").start()

    def progress_allowed(self, request):
        return not (self.closed or request.cancelled or request.detached)

    def shutdown(self, timeout=1.0):
        deadline = time.monotonic() + timeout
        # Drain short calls and already-terminal waits; never join an indefinite
        # observer or use ThreadPoolExecutor's implicit atexit join.
        while time.monotonic() < deadline - 0.1:
            with self.lock:
                if not self.requests and not self.cancellations:
                    break
            time.sleep(0.01)
        with self.lock:
            self.closed = True
            requests = list(self.requests.values())
            for request in requests:
                request.detached = True
                request.stop.set()
        for request in requests:
            if request.thread:
                request.thread.join(max(0, deadline - time.monotonic()))
