"""Background I/O for the board. Workers publish data; they never call curses."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
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
    return Snapshot(projected, pending, slots, cap, time.monotonic())


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
