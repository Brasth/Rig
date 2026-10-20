"""Background I/O for the board. Workers publish data; they never call curses."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import jobs as rig_jobs
import work_queue as rig_queue


@dataclass(frozen=True)
class Snapshot:
    jobs: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    slots: int = 0
    cap: int = 3
    captured_at: float = 0.0
    settings: list[dict] = field(default_factory=list)
    # None means the board collects workflows itself (direct reads).
    workflows: list[dict] | None = None
    # "api": read through the local API. "direct": ApiLoader fell back to
    # in-process reads because the API was unreachable. "local": plain direct mode.
    source: str = "local"


def visible_jobs(listing, repo, start, stop):
    cache = {}
    result = []
    for job in listing[start:stop]:
        row = rig_jobs.project_job(job, repo=repo, refresh=True, cache=cache)
        folder = Path(row["dir"]) if row.get("dir") else None
        row["artifacts"] = [str(folder / name) for name in (
            "change-evidence.json", "verification.json", "checks",
        ) if folder is not None and (folder / name).exists()]
        result.append(row)
    return result


def _pending_reason(item, held, slots, cap, worker_cap):
    """Only report blockers supported by admission accounting, never inferred scope."""
    if slots >= cap:
        return f"Execution capacity full ({slots}/{cap}); awaiting a free slot"
    worker = item.get("worker") or ""
    used = sum(bool(row.get("slot_held")) and row.get("worker") in {"", worker} for row in held)
    if worker and worker_cap and used >= worker_cap:
        return f"{worker} capacity full ({used}/{worker_cap})"
    exclusive = next((row for row in held if row.get("scope_unknown")
                      and row.get("access") == "write"), None)
    if exclusive:
        owner = exclusive.get("job_id") or exclusive.get("queue_id") or "another task"
        return f"Repository write scope held by {owner}"
    return "Awaiting parent scope and claim; file overlap is not yet known"


def load_snapshot(repo, *, start=0, rows=20, selected_id=None):
    import harness as rig_harness
    import jev_provider

    listing = rig_jobs.list_jobs(repo)
    if selected_id:
        selected = next((i for i, row in enumerate(listing) if row.get("job_id") == selected_id), start)
        start = min(start, selected) if selected < start else max(start, selected - max(1, rows) + 1)
    start = min(max(0, start), max(0, len(listing) - max(1, rows)))
    projected = list(listing)
    if rows > 0:
        projected[start:start + rows] = visible_jobs(listing, repo, start, start + rows)
    held = rig_queue._held_rows(repo, jobs_snapshot=listing)
    slots = sum(bool(row.get("slot_held")) for row in held)
    cap = rig_queue.max_running(repo)
    worker_caps = {}
    pending = []
    for item in rig_queue.list_items(repo, status="pending"):
        worker = item.get("worker") or ""
        if worker not in worker_caps:
            worker_caps[worker] = rig_queue.max_per_worker(repo, worker) if worker else 0
        reason = _pending_reason(item, held, slots, cap, worker_caps[worker])
        pending.append({**item, "waiting_reason": reason})
    routing = rig_harness.parse_harness(rig_harness.harness_path(Path(repo))).get("routing") or {}
    jev = jev_provider.status()
    settings = [
        {"id": "jev", "state": "configured" if jev["configured"] else "missing",
         "text": f"Jev global key: {jev['source']} (c connect/replace, d remove)"},
        {"id": "engine", "state": "configured", "text": f"Project picker: {routing.get('engine') or 'local'} (t toggle)"},
        {"id": "objective", "state": "configured", "text": f"Local objective: {routing.get('objective') or 'balanced'} (o cycle)"},
    ]
    import routing_settings
    settings.extend(routing_settings.settings_rows(Path(repo)))
    return Snapshot(projected, pending, slots, cap, time.monotonic(), settings)


@dataclass(frozen=True)
class ActionResult:
    key: str
    value: object = None
    error: str = ""


class BoardRuntime:
    """One snapshot in flight, two ordinary actions, and two cancellation actions.

    State is owned by the input thread. Daemon workers only put completed results
    on the mailbox, so a blocked filesystem operation cannot block board exit.
    """

    def __init__(self, repo, *, loader: Callable = load_snapshot, clock=time.monotonic):
        self.repo, self.loader, self.clock = repo, loader, clock
        self.snapshot = Snapshot()
        self.snapshot_error = ""
        self.scanning = False
        self.closed = False
        self.revision = 0
        self._last_request = float("-inf")
        self._mailbox = queue.SimpleQueue()
        self._actions: dict[str, bool] = {}
        self._dirty = True

    def refresh(self):
        self._dirty = True

    def request_snapshot(self, **view):
        if self.closed or self.scanning or (not self._dirty and self.clock() - self._last_request < 0.8):
            return False
        self.scanning, self._dirty = True, False
        self._last_request = self.clock()

        def collect():
            try:
                value = self.loader(self.repo, **view)
                self._mailbox.put(("snapshot", value, ""))
            except (Exception, SystemExit) as exc:
                self._mailbox.put(("snapshot", None, str(exc) or type(exc).__name__))

        threading.Thread(target=collect, name="rig-tui-snapshot", daemon=True).start()
        return True

    def submit(self, key, action, *, cancellation=False):
        if self.closed or key in self._actions:
            return False
        if sum(lane == cancellation for lane in self._actions.values()) >= 2:
            return False
        self._actions[key] = cancellation

        def execute():
            try:
                result = ActionResult(key, action())
            except (Exception, SystemExit) as exc:
                result = ActionResult(key, error=str(exc) or type(exc).__name__)
            self._mailbox.put(("action", result, ""))

        threading.Thread(target=execute, name="rig-tui-action", daemon=True).start()
        return True

    def poll(self):
        results = []
        while True:
            try:
                kind, value, error = self._mailbox.get_nowait()
            except queue.Empty:
                break
            if kind == "snapshot":
                self.scanning = False
                self.snapshot_error = error
                if value is not None:
                    self.snapshot = value
                    self.revision += 1
            else:
                self._actions.pop(value.key, None)
                self._dirty = True
                results.append(value)
        return results

    def close(self):
        self.closed = True


def api_snapshot(repo, *, start=0, rows=20, selected_id=None, timeout=3.0):
    """One board snapshot through the local API. Raises on any failure."""
    import local_api
    params = {"start": int(start), "rows": int(rows)}
    if selected_id:
        params["selected_id"] = str(selected_id)
    response = local_api.call(repo, "board.snapshot", params, timeout=timeout)
    if "error" in response:
        raise RuntimeError(response["error"].get("message") or "board snapshot failed")
    data = response["result"]
    return Snapshot(list(data["jobs"]), list(data["pending"]), int(data["slots"]), int(data["cap"]),
                    time.monotonic(), list(data["settings"]), list(data["workflows"]), "api")


class ApiLoader:
    """API-first snapshot loader that keeps the board usable when the API is down.

    The first unreachable call starts a detached, idle-limited server (at most
    once per ``respawn`` seconds) and serves that frame from direct reads, so a
    missing server never blanks the board. Direct reads are labelled by
    ``Snapshot.source == "local"``.
    """

    def __init__(self, *, spawn=True, respawn=15.0, clock=time.monotonic,
                 fetch: Callable = None, fallback: Callable = None, ensure: Callable = None):
        self.spawn, self.respawn, self.clock = spawn, respawn, clock
        self.fetch = fetch or api_snapshot
        self.fallback = fallback or load_snapshot
        self.ensure = ensure
        self._last_spawn = float("-inf")

    def __call__(self, repo, **view):
        try:
            return self.fetch(repo, **view)
        except (ConnectionError, OSError, ValueError, RuntimeError, KeyError, TypeError):
            if self.spawn and self.clock() - self._last_spawn >= self.respawn:
                self._last_spawn = self.clock()
                try:
                    (self.ensure or _ensure_api)(repo)
                except (OSError, ValueError):
                    pass
            snapshot = self.fallback(repo, **view)
            return replace(snapshot, source="direct") if isinstance(snapshot, Snapshot) else snapshot


def _ensure_api(repo):
    import local_api
    local_api.ensure(repo)
