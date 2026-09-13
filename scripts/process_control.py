"""Bounded, incarnation-checked termination of an isolated worker tree."""
from __future__ import annotations

import os
import signal
import subprocess
import time

import admission


def _inventory(known, group, deadline):
    """Expand only from live incarnations; never adopt a reused historical PID."""
    live = {}
    for identity in known.values():
        if time.monotonic() >= deadline:
            return False
        if admission._process_state(identity, timeout=min(1.0, max(0.001, deadline - time.monotonic()))) == "alive":
            current = admission.process_identity(identity["pid"], timeout=min(1.0, max(0.001, deadline - time.monotonic())))
            if current.get("start_id") == identity.get("start_id"):
                live[identity["pid"]] = current
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    try:
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid="], capture_output=True,
                                text=True, timeout=min(1.0, remaining), check=False)
        if result.returncode:
            return False
        rows = [tuple(map(int, line.split())) for line in result.stdout.splitlines() if line.strip()]
        selected = set(live)
        anchored = any(item.get("pgid") == group for item in live.values())
        while True:
            expanded = selected | {pid for pid, parent, pgid in rows
                                   if parent in selected or (anchored and pgid == group)}
            if expanded == selected:
                break
            selected = expanded
        for pid in selected:
            if time.monotonic() >= deadline:
                return False
            identity = admission.process_identity(pid, timeout=min(1.0, max(0.001, deadline - time.monotonic())))
            if identity.get("start_id"):
                known[(pid, identity["start_id"])] = identity
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def terminate(process, timeout=5.0, grace=1.0):
    """Return by deadline; unknown ownership is retained, never guessed."""
    deadline = time.monotonic() + max(0.0, timeout)
    if not process.get("pid") or not process.get("start_id"):
        return {"stopped": False, "survivors": [process], "descendants": process.get("descendants", []),
                "reason": "child identity unavailable"}
    known = {(item.get("pid"), item.get("start_id")): dict(item)
             for item in [process, *process.get("descendants", [])] if item.get("pid")}
    group = process.get("pgid") if process.get("pgid") == process.get("pid") else None
    complete = _inventory(known, group, deadline)

    def send(sig):
        # The root goes last so descendants have a chance to exit and be reaped.
        ordered = sorted(known.values(), key=lambda item: item["pid"] == process.get("pid"))
        for identity in ordered:
            if time.monotonic() >= deadline:
                return
            if admission._process_state(identity, timeout=min(1.0, max(0.001, deadline - time.monotonic()))) == "alive":
                try:
                    os.kill(identity["pid"], sig)
                except (ProcessLookupError, PermissionError):
                    pass

    send(signal.SIGTERM)
    grace_end = min(deadline, time.monotonic() + max(0.0, grace))
    while time.monotonic() < grace_end:
        time.sleep(min(0.05, grace_end - time.monotonic()))
    complete = _inventory(known, group, deadline) and complete
    send(signal.SIGKILL)
    survivors = list(known.values())
    while time.monotonic() < deadline:
        survivors = []
        for item in known.values():
            if time.monotonic() >= deadline or admission._process_state(
                    item, timeout=min(1.0, max(0.001, deadline - time.monotonic()))) != "dead":
                survivors.append(item)
        if not survivors:
            break
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return {"stopped": complete and not survivors, "survivors": survivors,
            "descendants": [item for item in known.values() if item["pid"] != process.get("pid")],
            "reason": "" if complete and not survivors else "termination deadline or unknown process ownership"}
