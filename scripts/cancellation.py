"""Durable cancellation intent, scoped to an immutable execution attempt.

Publishing intent never acquires admission: a busy writer must not prevent Stop.
Only the admission lifecycle may confirm termination or release protection.
"""
from __future__ import annotations

import json
import os
import re
import signal
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"[A-Za-z0-9._-]+")
TERMINAL = {"ok", "fail", "timeout", "cancelled"}
_active = set()
_guard = threading.Lock()


def _read(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def capture(path):
    """Pin directory and attempt identities before waiting or cancelling."""
    path = Path(path).resolve()
    stat = path.stat()
    meta = _read(path / "meta.json")
    if not meta:
        raise ValueError(f"job {path.name} has missing or invalid metadata")
    return {"path": path, "directory_identity": (stat.st_dev, stat.st_ino),
            "job_id": meta.get("job_id") or path.name,
            "reservation_id": meta.get("reservation_id") or "",
            "attempt_id": meta.get("attempt_id") or ""}


def current(target):
    path = target["path"]
    stat = path.stat()
    meta = _read(path / "meta.json")
    if ((stat.st_dev, stat.st_ino) != target["directory_identity"] or not meta
            or (meta.get("job_id") or path.name) != target["job_id"]
            or any((meta.get(key) or "") != target[key] for key in ("reservation_id", "attempt_id"))):
        raise ValueError("job identity changed; cancellation target was not replaced")
    return meta


def _marker(folder, attempt_id):
    if attempt_id:
        if not _ID.fullmatch(attempt_id) or attempt_id in {".", ".."}:
            raise ValueError("invalid cancellation attempt")
        return Path(folder) / "cancellation" / f"{attempt_id}.json"
    return Path(folder) / "cancel.json"


def requested(job_dir, attempt_id="", reservation_id=""):
    folder = Path(job_dir)
    # Pre-upgrade writers used a job-scoped sentinel (sometimes an empty JSON
    # object). Preserve that stop intent; new admitted writers never create it.
    if (folder / "cancel.json").is_file():
        return True
    # Callers without credentials still bind to the currently recorded attempt.
    if not attempt_id:
        meta = _read(folder / "meta.json")
        attempt_id = meta.get("attempt_id") or ""
        reservation_id = reservation_id or meta.get("reservation_id") or ""
    record = _read(_marker(folder, attempt_id))
    if not record:
        return False
    return (not attempt_id or (record.get("attempt_id") == attempt_id
            and (not reservation_id or record.get("reservation_id") == reservation_id)))


def publish(target, reason="parent"):
    """Commit a stop request without rewriting execution metadata."""
    meta = current(target)
    if meta.get("status") in TERMINAL:
        return {"state": "already-terminal", "status": meta["status"], "job_id": target["job_id"]}
    path = _marker(target["path"], target["attempt_id"])
    if path.parent != target["path"]:
        path.parent.mkdir(exist_ok=True)
    current(target)
    value = {key: target[key] for key in ("job_id", "reservation_id", "attempt_id")}
    value.update(reason=str(reason), at=datetime.now(timezone.utc).isoformat())
    fd, temporary = tempfile.mkstemp(prefix=".cancel-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        current(target)
        # Immutable first publication: concurrent repeated Stop is idempotent.
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"state": "stop-requested", "job_id": target["job_id"]}


def signal_wrapper(target):
    """Wake only the matching wrapper. Native agents belong to their host."""
    import admission

    meta = current(target)
    if not target["reservation_id"]:
        return "stop-unconfirmed"
    record = admission.get_reservation(target["path"].parents[2], target["reservation_id"])
    if not record or record.get("attempt_id") != target["attempt_id"]:
        return "stop-unconfirmed"
    if record.get("stopped"):
        return "stopped"
    owner = record.get("owner") or {}
    if owner.get("kind") != "wrapper":
        return "native-cancel-required" if owner.get("kind") == "native_child" else "stop-unconfirmed"
    pid = owner.get("pid")
    if not pid or not owner.get("start_id"):
        return "stop-unconfirmed"
    identity = admission.process_identity(pid)
    if identity.get("start_id") != owner["start_id"]:
        return "stop-unconfirmed"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return "stop-unconfirmed"
    return "stop-requested"


def dispatch(target):
    """Bounded background stop attempts; the durable marker survives saturation."""
    key = (str(target["path"]), target["attempt_id"])
    with _guard:
        if key in _active or len(_active) >= 16:
            return
        _active.add(key)

    def stop():
        deadline = time.monotonic() + 5.0
        try:
            signal_wrapper(target)
            import admission
            import process_control

            if target["reservation_id"]:
                current(target)
                record = admission.get_reservation(target["path"].parents[2], target["reservation_id"])
                if (record and record.get("attempt_id") == target["attempt_id"]
                        and not record.get("stopped") and (record.get("owner") or {}).get("kind") == "wrapper"
                        and record.get("process")):
                    process_control.terminate(record["process"], timeout=max(0, deadline - time.monotonic()))
        except (OSError, ValueError, ImportError):
            # The worker also consumes intent. Missing identity remains unconfirmed.
            pass
        finally:
            with _guard:
                _active.discard(key)

    threading.Thread(target=stop, daemon=True, name="rig-terminate").start()


def state(job):
    folder = job.get("dir")
    if not folder or not requested(folder, job.get("attempt_id") or "", job.get("reservation_id") or ""):
        return ""
    reservation = job.get("reservation") or {}
    if job.get("status") in {"ok", "fail", "timeout"} and (not reservation or reservation.get("stopped")):
        return ""
    if reservation.get("stopped"):
        return "stopped"
    if job.get("executor_kind") == "native_child":
        return "native-cancel-required"
    return "stop-unconfirmed"
