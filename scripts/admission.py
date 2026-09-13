#!/usr/bin/env python3
"""Atomic execution admission and retained file ownership for this repository.

The token authenticates local orchestration, not an operating-system boundary.
Execution slots and file protection have deliberately separate lifetimes.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import change_evidence as evidence
import harness


class AdmissionError(ValueError):
    pass


_process = os.getpid()
_locks: dict[str, threading.RLock] = {}
_lock_map_guard = threading.Lock()
_held = threading.local()
_ID = re.compile(r"[A-Za-z0-9._-]+")
_ACTIVE = {"reserved", "running", "verifying", "reviewing"}


def _root(repo):
    return evidence.repository(repo)


def _id(value, label="id", optional=False):
    if optional and not value:
        return ""
    if not isinstance(value, str) or value in {"", ".", ".."} or not _ID.fullmatch(value):
        raise AdmissionError(f"invalid {label}")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def transaction(repo):
    """Reentrant per thread, serialized locally and across processes; fail closed."""
    global _process, _locks, _lock_map_guard, _held
    pid = os.getpid()
    if pid != _process:
        # Never LOCK_UN an inherited descriptor: that unlocks the parent's flock.
        for value in getattr(_held, "entries", {}).values():
            value[0].close()
        _process, _locks, _lock_map_guard, _held = pid, {}, threading.Lock(), threading.local()
    root = _root(repo)
    key = str(root)
    with _lock_map_guard:
        local = _locks.setdefault(key, threading.RLock())
    with local:
        entries = getattr(_held, "entries", None)
        if entries is None:
            entries = _held.entries = {}
        if key in entries:
            yield root
            return
        stream = None
        try:
            folder = root / ".rig" / "queue"
            folder.mkdir(parents=True, exist_ok=True)
            stream = (folder / ".lock").open("a+")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except (OSError, RuntimeError) as error:
            if stream is not None:
                stream.close()
            raise AdmissionError(f"admission lock unavailable: {error}") from error
        entries[key] = (stream, pid)
        try:
            yield root
        finally:
            if os.getpid() == pid:
                entries.pop(key, None)
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path, required=False):
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except FileNotFoundError:
        if not required:
            return None
        raise AdmissionError("admission record is missing") from None
    except (OSError, ValueError) as error:
        raise AdmissionError(f"unreadable coordination record: {path.name}") from error


def _reservation_path(root, rid):
    return root / ".rig" / "reservations" / (_id(rid, "reservation id") + ".json")


def _save(root, record):
    record["updated_at"] = _now()
    _write(_reservation_path(root, record["reservation_id"]), record)


def credentials(record):
    return {key: record.get(key, "") for key in ("reservation_id", "attempt_id", "owner_token")}


def _public(record):
    result = copy.deepcopy(record)
    result.pop("owner_token", None)
    for item in result.get("history", []):
        if isinstance(item, dict):
            item.pop("owner_token", None)
    return result


def list_reservations(repo, *, include_released=False):
    root = _root(repo)
    values = []
    for path in sorted((root / ".rig" / "reservations").glob("*.json")):
        record = _read(path, required=True)
        if include_released or record.get("stage") != "released":
            values.append(_public(record))
    return values


def get_reservation(repo, reservation_id):
    record = _read(_reservation_path(_root(repo), reservation_id))
    return _public(record) if record else None


def write_credentials(repo, record):
    root = _root(repo)
    job = _id(record.get("job_id"), "job id")
    path = root / ".rig" / "jobs" / job / "owner-credentials.json"
    _write(path, {**credentials(record), "job_id": job, "owner": record.get("owner", {})})
    return path


def process_identity(pid):
    """Capture a PID incarnation. Missing observability remains unknown."""
    try:
        pid = int(pid)
        if pid <= 0:
            return {}
    except (TypeError, ValueError):
        return {}
    result = {"pid": pid, "start_id": "", "pgid": None}
    try:
        result["pgid"] = os.getpgid(pid)
        stat = Path(f"/proc/{pid}/stat")
        if stat.is_file():
            fields = stat.read_text().rsplit(")", 1)[1].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            result["start_id"] = boot + ":" + fields[19]
        else:
            proc = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                                  text=True, check=False, env={**os.environ, "LC_ALL": "C"})
            if proc.returncode == 0:
                result["start_id"] = proc.stdout.strip()
    except (OSError, ValueError, IndexError):
        pass
    return result


def _process_state(identity):
    pid = (identity or {}).get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except (PermissionError, OSError):
        return "unknown"
    current = process_identity(pid)
    before, after = identity.get("start_id"), current.get("start_id")
    if before and after and before != after:
        return "dead"
    return "alive" if before and after else "unknown"


def _legacy_process_state(identity):
    state = _process_state(identity)
    if state == "unknown" and isinstance(identity.get("pid"), int) and identity["pid"] > 0:
        try:
            os.kill(identity["pid"], 0)
            return "alive"
        except OSError:
            pass
    return state


def caller_owner(executor_kind, *, owner_session="", owner_pid=None, native_agent_id=""):
    if executor_kind not in {"parent", "native_child", "wrapper"}:
        raise AdmissionError("executor kind must be parent|native_child|wrapper")
    session = owner_session or next((os.environ.get(key, "") for key in (
        "RIG_OWNER_SESSION", "RIG_THREAD", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "GROK_SESSION_ID",
    ) if os.environ.get(key)), "")
    parent_pid = os.getppid()
    probe = parent_pid
    for _ in range(10):
        if probe <= 1:
            break
        if harness._comm_parent(harness._ps_comm(probe), probe):
            parent_pid = probe
            break
        try:
            probe = int(harness._ps_ppid(probe))
        except ValueError:
            break
    parent = process_identity(parent_pid)
    executor = process_identity(owner_pid if owner_pid is not None else os.getpid())
    return {"kind": executor_kind, "session_id": session,
            "parent_cli": harness.live_parent(), "parent_pid": parent.get("pid"),
            "parent_start_id": parent.get("start_id", ""), **executor,
            "native_agent_id": native_agent_id}


def _owner(owner, owner_session="", kind="parent"):
    result = copy.deepcopy(owner) if isinstance(owner, dict) else caller_owner(kind, owner_session=owner_session)
    if owner_session:
        if result.get("session_id") and result["session_id"] != owner_session:
            raise AdmissionError("owner session mismatch")
        result["session_id"] = owner_session
    if result.get("kind") not in {"parent", "native_child", "wrapper"}:
        raise AdmissionError("owner kind is required")
    return result


def _same_actor(left, right):
    if left.get("session_id") or right.get("session_id"):
        return bool(left.get("session_id")) and left.get("session_id") == right.get("session_id")
    return bool(left.get("parent_pid") and left.get("parent_start_id")) and (
        left.get("parent_pid"), left.get("parent_start_id")
    ) == (right.get("parent_pid"), right.get("parent_start_id"))


def _auth(root, reservation_id, attempt_id, owner_token, owner=None, owner_session=""):
    _id(attempt_id, "attempt id")
    if not isinstance(owner_token, str) or not owner_token:
        raise AdmissionError("owner credentials required")
    record = _read(_reservation_path(root, reservation_id), required=True)
    if record.get("attempt_id") != attempt_id or not hmac.compare_digest(record.get("owner_token", ""), owner_token):
        raise AdmissionError("reservation attempt or owner credentials mismatch")
    actor = _owner(owner, owner_session, record["owner"]["kind"])
    if not _same_actor(record["owner"], actor):
        raise AdmissionError("initiating owner session mismatch")
    return record, actor


def assert_owned(repo, *, reservation_id, attempt_id, owner_token, owner=None, owner_session=""):
    with transaction(repo) as root:
        record, _ = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
        return _public(record)


def canonical_files(repo, files):
    declared = evidence.normalize_files(repo, files or [], allow_empty=True)
    root, canonical = _root(repo), set()
    for name in declared:
        path = root / name
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(root)
        except (OSError, ValueError, RuntimeError) as error:
            raise AdmissionError("file scope escapes repository or has an invalid alias") from error
        if not relative.parts or relative.parts[0] == ".git" or resolved.is_dir():
            raise AdmissionError("file scope must contain concrete repository files")
        canonical.add(relative.as_posix())
        if path.is_symlink():
            canonical.add(name)  # Protect both link replacement and target writes.
    return declared, sorted(canonical)


def _records(root):
    return [_read(path, required=True) for path in sorted((root / ".rig" / "reservations").glob("*.json"))]


def _queue_path(root, queue_id):
    return root / ".rig" / "queue" / (_id(queue_id, "queue id") + ".json")


def _queue_update(root, record, status):
    if not record.get("queue_id"):
        return
    path = _queue_path(root, record["queue_id"])
    item = _read(path, required=True)
    if item.get("reservation_id") not in {None, "", record["reservation_id"]}:
        raise AdmissionError("queue belongs to another reservation")
    if item.get("attempt_id") not in {None, "", record["attempt_id"]}:
        raise AdmissionError("queue belongs to another attempt")
    # A cancelled job must never turn into an implicit queue retry, including
    # cancellation before its launch barrier was opened.
    if record.get("job_id") and (root / ".rig" / "jobs" / record["job_id"] / "cancel.json").exists():
        item["status"] = "cancelled"
    # Cancellation preserves intent even when a late executor completes.
    if item.get("status") != "cancelled" and not (item.get("status") == "done" and status in {"claimed", "spawned"}):
        item["status"] = status
    item.update(reservation_id=record["reservation_id"], attempt_id=record["attempt_id"],
                files=record["declared_files"], access=record["access"], worker=record["worker"])
    if record.get("job_id"):
        item["job_id"] = record["job_id"]
    if status == "claimed":
        item["claimed_at"] = item.get("claimed_at") or _now()
    if status == "pending":
        item.update(claimed_at="", job_id="", reservation_id="", attempt_id="")
    item.pop("owner_token", None)
    _write(path, item)


def _accounting(root, skip_reservation="", skip_queue=""):
    records = _records(root)
    held = [record for record in records if record.get("stage") != "released"]
    attempts = {(record.get("reservation_id"), record.get("attempt_id")): record for record in records}
    reconciled_legacy = {record.get("job_id") for record in records if record.get("legacy") and record.get("stopped")}
    bound_jobs = {record.get("job_id") for record in held if record.get("job_id")}
    bound_queues = {record.get("queue_id") for record in held if record.get("queue_id")}
    rows = [record for record in held if record.get("reservation_id") != skip_reservation]
    for path in (root / ".rig" / "jobs").glob("*/meta.json"):
        job = _read(path, required=True)
        jid = path.parent.name
        if jid in reconciled_legacy:
            continue
        bound = attempts.get((job.get("reservation_id"), job.get("attempt_id")))
        if bound and bound.get("job_id") == jid:
            continue  # The ledger, including confirmed release, is authoritative.
        cancelled_owner = job.get("legacy_cancel_requested") and _legacy_process_state(
            {"pid": job.get("pid"), "start_id": job.get("process_start_id", "")}) != "dead"
        if jid in bound_jobs or job.get("status") not in {"running", "ask"} and not cancelled_owner:
            continue
        _, files = canonical_files(root, job.get("files") or [])
        access = job.get("access") or ("read" if job.get("role") in {"explore", "explorer", "review", "reviewer"} else "write")
        rows.append({"job_id": jid, "worker": job.get("worker", ""), "access": access,
                     "files": files, "scope_unknown": not files, "slot_held": True, "legacy": True})
    for path in (root / ".rig" / "queue").glob("*.json"):
        item = _read(path, required=True)
        qid = path.stem
        if qid in bound_queues or qid == skip_queue or item.get("status") not in {"claimed", "spawned"}:
            continue
        if item.get("job_id") in bound_jobs:
            continue
        _, files = canonical_files(root, item.get("files") or [])
        rows.append({"queue_id": qid, "job_id": item.get("job_id", ""), "worker": item.get("worker", ""),
                     "access": item.get("access") or "write", "files": files,
                     "scope_unknown": not files, "slot_held": True, "legacy": True})
    # Legacy spawned queue plus its live metadata is also one execution.
    seen, result = set(), []
    for row in rows:
        jid = row.get("job_id")
        if jid and jid in seen:
            continue
        if jid:
            seen.add(jid)
        result.append(row)
    return result


def _validate(root, worker, role, model, access, owner, *, allow_unknown_worker=False):
    if access not in {"read", "write"}:
        raise AdmissionError("access must be read|write")
    if not worker and allow_unknown_worker:
        return
    if worker not in set(harness.WORKERS) | {"parent"}:
        raise AdmissionError("valid selected worker required")
    try:
        harness.assert_spawn_allowed(root, worker, owner.get("parent_cli", ""))
    except SystemExit as error:
        raise AdmissionError(str(error)) from error
    if owner.get("kind") != "parent":
        import route
        problem = route.assert_child_model(model)
        if problem:
            raise AdmissionError(problem)


def _capacity(root, rows, worker, access, files, *, reserve_slot=True):
    cfg = harness.parse_harness(harness.harness_path(root))["queue"]
    slots = [row for row in rows if row.get("slot_held")]
    cap = max(1, int(cfg.get("max_running", 3)))
    if reserve_slot and len(slots) >= cap:
        raise AdmissionError(f"live+reserved {len(slots)}/{cap}; wait for an execution slot")
    workers = [worker] if worker else list(harness.WORKERS)
    for name in workers:
        limit = int(cfg.get("per_worker", {}).get(name, cfg.get("max_per_worker", 0)))
        count = sum(1 for row in slots if row.get("worker") in {"", name})
        if reserve_slot and limit and count >= limit:
            raise AdmissionError(f"{name} live {count}/{limit} (max_per_worker)")
    for row in rows:
        if access == "read" and row.get("access") == "read":
            continue
        unknown = not files or row.get("scope_unknown") or not row.get("files")
        overlap = set(files) & set(row.get("files", []))
        if unknown or overlap:
            identity = row.get("job_id") or row.get("queue_id") or row.get("reservation_id")
            reason = "no listed files; exclusive scope" if unknown else "files overlap: " + ", ".join(sorted(overlap))
            raise AdmissionError(f"{reason} ({identity})")


def _ensure_idle(record):
    if record.get("operation"):
        raise AdmissionError("an active or interrupted verification operation still holds this reservation")


def _review_gate(root, record, model, snapshot_id):
    import route
    if not record.get("stopped") or record.get("legacy"):
        raise AdmissionError("independent review requires protected, stopped writer execution")
    writer_id = record.get("writer_job_id") if record.get("review_launch_failed") else record["job_id"]
    folder = root / ".rig" / "jobs" / writer_id
    accepted = _read(folder / "verification.json", required=True)
    if accepted.get("next") != "review":
        raise AdmissionError("writer acceptance must retain protection for review")
    context, problem = route._writer_context(writer_job_id=writer_id, review_mode="independent", repo=root)
    if problem or not snapshot_id or context["writer_snapshot_id"] != snapshot_id:
        raise AdmissionError(problem or "writer snapshot changed before review")
    _, problem = route._review_model(model, context)
    if problem:
        raise AdmissionError(problem)


def _reconcile_dead(root):
    for record in _records(root):
        pending = record.get("pending_operation")
        if (record.get("stage") == "released" and pending == "return_pending") or (record.get("stopped") and pending == "queue_done"):
            _queue_update(root, record, "pending" if pending == "return_pending" else "done")
            record.pop("pending_operation", None)
            _save(root, record)
            continue
        if record.get("stage") != "reserved" or record.get("launch_started") or record.get("process"):
            continue
        if _process_state(record.get("owner")) == "dead":
            if record.get("writer_job_id"):
                _retain_failed_review(root, record, "review owner stopped before launch")
                continue
            record.update(stage="released", slot_held=False, stopped=True,
                          release_reason="unlaunched owner process stopped", pending_operation="return_pending")
            _save(root, record)
            _queue_update(root, record, "pending")
            record.pop("pending_operation", None)
            _save(root, record)


def _retain_failed_review(root, record, reason):
    record.update(stage="verifying", slot_held=False, stopped=True, review_launch_failed=True,
                  execution_status="fail", needs_reconciliation=True,
                  reconciliation_reason="review never launched; original writer scope remains protected",
                  release_reason=reason, pending_operation="queue_done")
    _save(root, record)
    _queue_update(root, record, "done")
    record.pop("pending_operation", None)
    _save(root, record)
    return _public(record)


def _new_record(*, actor, job_id, queue_id, worker, role, model, access, canonical, declared,
                source=None, writer_job_id="", writer_snapshot_id=""):
    return {"version": 1, "reservation_id": source["reservation_id"] if source else uuid.uuid4().hex,
            "attempt_id": uuid.uuid4().hex, "owner_token": secrets.token_hex(16), "owner": actor,
            "job_id": job_id, "queue_id": queue_id, "writer_job_id": writer_job_id,
            "writer_snapshot_id": writer_snapshot_id, "worker": worker, "role": role, "model": model,
            "access": access, "files": canonical, "declared_files": declared,
            "scope_unknown": not canonical, "stage": "reserved", "slot_held": True,
            "created_at": _now(), "release_reason": "", "launch_started": False,
            "claim_consumed": bool(job_id) and not bool(queue_id), "stopped": False, "needs_reconciliation": False,
            "history": list(source.get("history", [])) + [_public(source)] if source else []}


def reserve(repo, *, job_id="", worker="", role="worker", model="", files=None,
            access="write", owner=None, owner_session="", queue_id="", reservation_id="",
            attempt_id="", owner_token="", writer_job_id="", writer_snapshot_id="", dry_run=False):
    root = _root(repo)
    _id(job_id, "job id", optional=bool(queue_id))
    _id(queue_id, "queue id", optional=True)
    actor = _owner(owner, owner_session)
    declared, canonical = canonical_files(root, files)
    with transaction(root):
        if not dry_run:
            _reconcile_dead(root)
        _validate(root, worker, role, model, access, actor, allow_unknown_worker=bool(queue_id))
        source = None
        supplied = any((reservation_id, attempt_id, owner_token))
        if supplied:
            source, actor = _auth(root, reservation_id, attempt_id, owner_token, actor, owner_session)
            _ensure_idle(source)
            if source["stage"] == "released":
                raise AdmissionError("released attempt cannot be launched again")
        if writer_job_id:
            source_writer = source.get("writer_job_id") if source and source.get("review_launch_failed") else (source or {}).get("job_id")
            if source is None or source_writer != writer_job_id or access != "read":
                raise AdmissionError("review handoff needs the writer reservation credentials and read access")
            _review_gate(root, source, model, writer_snapshot_id)
            canonical = sorted(set(canonical) | set(source["files"]))
        elif source:
            if source.get("queue_id", "") != queue_id or source.get("access") != access or source.get("files") != canonical:
                raise AdmissionError("reservation queue, access or files mismatch")
            if source.get("job_id") not in {"", job_id} or source.get("worker") not in {"", worker}:
                raise AdmissionError("reservation job or worker mismatch")
            if source.get("claim_consumed") and (source.get("role"), source.get("model")) != (role, model):
                raise AdmissionError("admitted role and model are immutable")
            previous = source["owner"]
            if source.get("claim_consumed") and (previous.get("pid"), previous.get("start_id")) != (actor.get("pid"), actor.get("start_id")):
                raise AdmissionError("claim already consumed by another executor")
            if source.get("launch_started") or source.get("process") or source.get("stage") != "reserved":
                raise AdmissionError("attempt already launched; credentials cannot authorize another launch")
        if job_id:
            for record in _records(root):
                if record.get("job_id") == job_id and (source is None or record["reservation_id"] != source["reservation_id"]):
                    raise AdmissionError("job id already belongs to an attempt; use a fresh job id")
            meta = root / ".rig" / "jobs" / job_id / "meta.json"
            if meta.exists() and (source is None or source.get("job_id") != job_id):
                raise AdmissionError("existing job id is not launch authorization")
        queue = _read(_queue_path(root, queue_id), required=True) if queue_id else None
        if queue and source is None and queue.get("status") != "pending":
            raise AdmissionError("queue claim requires matching current attempt credentials; reconcile legacy claims")
        rows = _accounting(root, source["reservation_id"] if source else "", queue_id if source else "")
        _capacity(root, rows, worker, access, canonical)
        if dry_run:
            return {"job_id": job_id, "stage": "dry_run", "slot_held": False, "access": access,
                    "files": canonical, "declared_files": declared, "owner": actor, "dry_run": True}
        if source and not writer_job_id:
            record = source
            record.update(job_id=job_id, worker=worker, role=role, model=model,
                          owner=actor, claim_consumed=bool(job_id))
        else:
            record = _new_record(actor=actor, job_id=job_id, queue_id=queue_id, worker=worker, role=role,
                                 model=model, access=access, canonical=canonical, declared=declared,
                                 source=source, writer_job_id=writer_job_id, writer_snapshot_id=writer_snapshot_id)
        record["pending_operation"] = "bind_queue" if queue_id else "register_job"
        _save(root, record)
        if queue_id:
            _queue_update(root, record, "claimed")
        record.pop("pending_operation", None)
        _save(root, record)
        return record


def activate(repo, *, reservation_id, attempt_id, owner_token, job_id, worker, files,
             access, owner=None, owner_session="", process=None, native_agent_id=""):
    with transaction(repo) as root:
        record, actor = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
        _, canonical = canonical_files(root, files)
        if (record.get("job_id"), record.get("worker"), record.get("access")) != (job_id, worker, access):
            raise AdmissionError("activation job, worker or access mismatch")
        if not set(canonical) <= set(record["files"]) or canonical != canonical_files(root, record["declared_files"])[1]:
            raise AdmissionError("activation scope changed")
        if record["stage"] == "released" or record.get("stopped"):
            raise AdmissionError("finished attempt cannot activate")
        _refuse_cancelled(root, record)
        previous = record["owner"]
        if (previous.get("pid"), previous.get("start_id")) != (actor.get("pid"), actor.get("start_id")):
            raise AdmissionError("activation belongs to another executor")
        if record.get("process") and process and any(record["process"].get(key) != process.get(key) for key in ("pid", "start_id", "pgid", "gated", "gate_path")):
            raise AdmissionError("execution process identity is immutable")
        old_agent = previous.get("native_agent_id")
        if old_agent and native_agent_id and old_agent != native_agent_id:
            raise AdmissionError("native agent identity is immutable")
        _validate(root, worker, record["role"], record["model"], access, actor)
        if record.get("writer_job_id"):
            import route
            context, problem = route._writer_context(writer_job_id=record["writer_job_id"], review_mode="independent", repo=root)
            if problem or context.get("writer_snapshot_id") != record.get("writer_snapshot_id"):
                raise AdmissionError(problem or "writer snapshot changed before review launch")
            _, problem = route._review_model(record["model"], context)
            if problem:
                raise AdmissionError(problem)
        _ensure_idle(record)
        if record.get("launch_started"):
            return record
        if process:
            if process.get("gated"):
                gate = Path(process.get("gate_path", "")).resolve()
                folder = root / ".rig" / "jobs" / job_id
                if gate.parent != folder.resolve() or gate.exists():
                    raise AdmissionError("launch gate must be a fresh file in this job directory")
            record["process"] = copy.deepcopy(process)
        if native_agent_id:
            record["owner"]["native_agent_id"] = native_agent_id
        if actor["kind"] == "wrapper" and not record.get("process"):
            raise AdmissionError("wrapper activation requires a gated child process identity")
        record.update(stage="reviewing" if record.get("writer_job_id") else "running",
                      launch_started=not bool(record.get("process", {}).get("gated")), claim_consumed=True)
        _save(root, record)
        _queue_update(root, record, "spawned")
        return record


def commit_launch(repo, *, reservation_id, attempt_id, owner_token, owner=None, owner_session=""):
    """Release a recorded child barrier only after registration has committed."""
    with transaction(repo) as root:
        record, actor = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
        process = record.get("process") or {}
        if record.get("stopped") or record["stage"] == "released" or not process.get("gated"):
            raise AdmissionError("attempt has no active gated launch")
        if (record["owner"].get("pid"), record["owner"].get("start_id")) != (actor.get("pid"), actor.get("start_id")):
            raise AdmissionError("launch belongs to another executor")
        _ensure_idle(record)
        _refuse_cancelled(root, record)
        _validate(root, record["worker"], record["role"], record["model"], record["access"], actor)
        if record.get("writer_job_id"):
            import route
            context, problem = route._writer_context(writer_job_id=record["writer_job_id"], review_mode="independent", repo=root)
            if problem or context.get("writer_snapshot_id") != record.get("writer_snapshot_id"):
                raise AdmissionError(problem or "writer snapshot changed before review launch")
            _, problem = route._review_model(record["model"], context)
            if problem:
                raise AdmissionError(problem)
        gate = Path(process["gate_path"])
        if gate.exists():
            raise AdmissionError("launch barrier already committed")
        record["launch_started"] = True
        _save(root, record)
        _write(gate, {"attempt_id": attempt_id, "committed_at": _now()})
        return _public(record)


def _refuse_cancelled(root, record):
    if record.get("job_id") and (root / ".rig" / "jobs" / record["job_id"] / "cancel.json").exists():
        raise AdmissionError("job cancellation was requested; launch refused")
    if record.get("queue_id"):
        item = _read(_queue_path(root, record["queue_id"]), required=True)
        if item.get("status") == "cancelled":
            raise AdmissionError("queue cancellation was requested; launch refused")


def observe_process(repo, *, reservation_id, attempt_id, owner_token, owner=None, owner_session=""):
    """Remember observable descendants before they can outlive/reparent away."""
    root = _root(repo)
    record, _ = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
    process = record.get("process") or {}
    descendants, complete = [], False
    try:
        table = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid="], capture_output=True, text=True, check=False)
        if table.returncode == 0:
            rows = [tuple(map(int, row.split())) for row in table.stdout.splitlines() if row.strip()]
            selected = {process.get("pid")}
            selected.update(item.get("pid") for item in process.get("descendants", []))
            changed = True
            while changed:
                before = len(selected)
                selected.update(pid for pid, ppid, pgid in rows if ppid in selected or pgid == process.get("pgid"))
                changed = before != len(selected)
            descendants = [process_identity(pid) for pid in selected if pid and pid != process.get("pid")]
            complete = True
    except (OSError, ValueError):
        pass
    with transaction(root):
        current, _ = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
        if current.get("process", {}).get("pid") != process.get("pid"):
            raise AdmissionError("primary process changed during observation")
        known = {(item.get("pid"), item.get("start_id")): item for item in current.get("process", {}).get("descendants", [])}
        known.update({(item.get("pid"), item.get("start_id")): item for item in descendants})
        current.setdefault("process", {}).update(descendants=list(known.values()), observation_complete=complete)
        _save(root, current)
        return _public(current)


def _external_stopped(record):
    process = record.get("process")
    if not process:
        return False, "child identity unavailable"
    if process.get("observation_complete") is False:
        return False, "descendant observation is unavailable"
    for identity in [process, *process.get("descendants", [])]:
        if _process_state(identity) != "dead":
            return False, "child or observed descendant is alive or unknown"
    pgid = process.get("pgid")
    # Only an isolated child's group is meaningful; never treat the parent's
    # shared group as a complete descendant inventory.
    if pgid != process.get("pid"):
        return False, "child process group is not isolated"
    try:
        rows = subprocess.run(["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True, check=False)
        if rows.returncode:
            return False, "process group liveness unavailable"
        for row in rows.stdout.splitlines():
            fields = row.split()
            if len(fields) >= 3 and int(fields[1]) == pgid and not fields[2].startswith("Z"):
                return False, "child process group remains alive"
    except (OSError, ValueError):
        return False, "process group liveness unavailable"
    return True, ""


def _stopped(record, completion=None):
    if record.get("stopped"):
        return True, ""
    if record["owner"]["kind"] == "wrapper":
        return _external_stopped(record)
    completion = completion or {}
    if record["owner"]["kind"] == "parent" and completion.get("kind") == "parent_task" and completion.get("completed") is True:
        return True, ""
    if record["owner"]["kind"] == "native_child" and completion.get("kind") == "native_child":
        agent = completion.get("agent_id")
        known = record["owner"].get("native_agent_id")
        if agent and (not known or agent == known) and completion.get("terminal") is True and completion.get("outcome") in {"ok", "fail", "timeout", "cancelled"}:
            return True, ""
    return False, "authenticated task completion is unavailable"


def finish(repo, *, reservation_id, attempt_id, owner_token, status, owner=None,
           owner_session="", completion=None):
    if status not in {"ok", "fail", "timeout", "cancelled"}:
        raise AdmissionError("finish requires a terminal execution status")
    with transaction(repo) as root:
        record, _ = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
        if record.get("stopped") and record.get("execution_status") not in {None, "", status}:
            raise AdmissionError("confirmed execution status is immutable")
        if record.get("stage") == "released":
            return _public(record)
        _ensure_idle(record)
        stopped, reason = _stopped(record, completion)
        if completion and completion.get("kind") == "native_child" and stopped:
            if completion.get("outcome") != status:
                raise AdmissionError("native child outcome differs from recorded result")
            record["owner"]["native_agent_id"] = completion["agent_id"]
        record.update(stopped=stopped, needs_reconciliation=not stopped, reconciliation_reason=reason,
                      execution_status=status, completion=completion or record.get("completion", {}))
        if stopped:
            record.update(stage="verifying", slot_held=False)
        _save(root, record)
        if stopped:
            _queue_update(root, record, "done")
        return _public(record)


def _accepted_complete(root, record, snapshot_id):
    folder = root / ".rig" / "jobs" / record["job_id"]
    accepted = _read(folder / "verification.json", required=True)
    current = evidence.snapshot(root, record["declared_files"])["snapshot_id"]
    if not snapshot_id or snapshot_id != current or accepted.get("snapshot_id") != current or accepted.get("acceptance") != "accepted" or accepted.get("next") != "complete":
        raise AdmissionError("release requires current parent acceptance of this snapshot")


def release(repo, *, reservation_id, attempt_id, owner_token, rationale, owner=None,
            owner_session="", mode="close", snapshot_id=""):
    if not isinstance(rationale, str) or not rationale.strip():
        raise AdmissionError("release rationale required")
    if mode not in {"close", "launch_failed", "accepted_complete"}:
        raise AdmissionError("invalid release mode")
    with transaction(repo) as root:
        record, _ = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
        if record["stage"] == "released":
            return _public(record)
        _ensure_idle(record)
        if mode == "launch_failed":
            process = record.get("process")
            gated_stopped = bool(process and process.get("gated") and not Path(process["gate_path"]).exists()
                                 and _external_stopped(record)[0])
            if (record.get("launch_started") or process) and not gated_stopped:
                raise AdmissionError("launched execution cannot be returned to pending")
            if record.get("writer_job_id"):
                return _retain_failed_review(root, record, rationale.strip())
        else:
            stopped, reason = (True, "") if not record.get("launch_started") and not record.get("process") else _stopped(record, record.get("completion"))
            if not stopped:
                raise AdmissionError("cannot release execution: " + reason)
            if mode == "accepted_complete":
                _accepted_complete(root, record, snapshot_id)
        record.update(stage="released", slot_held=False, stopped=True, release_reason=rationale.strip(),
                      needs_reconciliation=False, reconciliation_reason="")
        record["pending_operation"] = "return_pending" if mode == "launch_failed" else "queue_done"
        _save(root, record)
        _queue_update(root, record, "pending" if mode == "launch_failed" else "done")
        record.pop("pending_operation", None)
        _save(root, record)
        return _public(record)


@contextmanager
def mutation_guard(repo, job_dir, operation, *, reservation_id="", attempt_id="",
                   owner_token="", owner=None, owner_session=""):
    root = _root(repo)
    folder = evidence.job_directory(root, job_dir)
    with transaction(root):
        record, actor = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
        if record["job_id"] != folder.name:
            raise AdmissionError("verification job does not own this reservation")
        meta = _read(folder / "meta.json", required=True)
        if (meta.get("reservation_id"), meta.get("attempt_id")) != (reservation_id, attempt_id) or not meta.get("ownership_established"):
            raise AdmissionError("execution metadata is not bound to this protected attempt")
        if record["stage"] == "released":
            if operation != "accept":
                raise AdmissionError("released attempt cannot run new checks")
            accepted = _read(folder / "verification.json", required=True)
            _accepted_complete(root, record, accepted.get("snapshot_id", ""))
            # Repeated complete acceptance may revalidate immutable old evidence.
            yield_record = _public(record)
            marker = None
        else:
            _ensure_idle(record)
            if operation != "requirements" and not record.get("stopped"):
                raise AdmissionError("execution must be confirmed stopped before verification")
            marker = {"id": uuid.uuid4().hex, "operation": operation, **process_identity(os.getpid())}
            record["operation"] = marker
            _save(root, record)
            yield_record = _public(record)
    succeeded = False
    try:
        yield yield_record
        succeeded = True
    finally:
        if marker:
            with transaction(root):
                current, _ = _auth(root, reservation_id, attempt_id, owner_token, actor, owner_session)
                if current.get("operation", {}).get("id") != marker["id"]:
                    raise AdmissionError("verification operation ownership changed")
                current.pop("operation", None)
                _save(root, current)
                if succeeded and operation == "accept":
                    accepted = _read(folder / "verification.json") or {}
                    if accepted.get("acceptance") == "accepted" and accepted.get("next") == "complete":
                        release(root, reservation_id=reservation_id, attempt_id=attempt_id, owner_token=owner_token,
                                owner=actor, mode="accepted_complete", snapshot_id=accepted.get("snapshot_id", ""),
                                rationale=accepted.get("rationale") or "parent accepted completion")


def reconcile(repo, *, job_id="", queue_id="", apply=False, action="report", owner=None,
              owner_session="", worker="", access="write", files=None, rationale="", completion=None,
              reservation_id="", attempt_id="", owner_token=""):
    root = _root(repo)
    if action not in {"report", "adopt", "release"}:
        raise AdmissionError("reconcile action must be report|adopt|release")
    if not apply:
        rows = [row for row in list_reservations(root, include_released=True)
                if (not job_id or row.get("job_id") == job_id) and (not queue_id or row.get("queue_id") == queue_id)]
        if queue_id and not rows:
            item = _read(_queue_path(root, queue_id), required=True)
            rows = [{"queue_id": queue_id, "legacy": True, "stage": item.get("status"),
                     "needs_reconciliation": True, "reconciliation_reason": "legacy claim has no owner credentials"}]
        elif job_id and not rows:
            meta = _read(root / ".rig" / "jobs" / _id(job_id) / "meta.json", required=True)
            rows = [{"job_id": job_id, "legacy": True, "stage": meta.get("status", "unknown"),
                     "needs_reconciliation": True, "reconciliation_reason": "legacy execution has no protected ownership"}]
        for record in rows:
            if record.get("stage") == "released" or record.get("stopped") or record.get("legacy"):
                continue
            if record.get("operation"):
                record.update(needs_reconciliation=True,
                              reconciliation_reason="verification operation remains held until its executor is confirmed stopped")
            elif record.get("launch_started"):
                if record.get("owner", {}).get("kind") == "wrapper":
                    stopped, reason = _external_stopped(record)
                    record.update(observed_stopped=stopped, needs_reconciliation=True,
                                  reconciliation_reason=reason or "execution stopped; parent assessment is required")
                else:
                    record.update(needs_reconciliation=True,
                                  reconciliation_reason="authenticated native task completion is unavailable")
            elif _process_state(record.get("owner")) == "unknown":
                record.update(needs_reconciliation=True,
                              reconciliation_reason="unlaunched owner liveness is unknown; protection remains held")
        return {"items": rows, "applied": 0}
    with transaction(root):
        if action == "report":
            if (completion or {}).get("checks_stopped") is True:
                if not job_id or not rationale.strip():
                    raise AdmissionError("interrupted check recovery requires job id and rationale")
                record, actor = _auth(root, reservation_id, attempt_id, owner_token, owner, owner_session)
                if record.get("job_id") != job_id:
                    raise AdmissionError("check recovery credentials belong to another job")
                recovered = _recover_check(root, record, actor, rationale)
                return {"items": [_public(record)], "applied": int(recovered)}
            before = sum(row.get("stage") != "released" for row in _records(root))
            _reconcile_dead(root)
            for record in _records(root):
                operation = record.get("operation") or {}
                if operation.get("operation") in {"requirements", "accept"} and _process_state(operation) == "dead":
                    record["interrupted_operation"] = record.pop("operation")
                    record.update(needs_reconciliation=True,
                                  reconciliation_reason="stopped verification operation cleared; parent assessment required")
                    _save(root, record)
            result = reconcile(root, job_id=job_id, queue_id=queue_id)
            result["applied"] = before - sum(row.get("stage") != "released" for row in _records(root))
            return result
        if not (queue_id or job_id) or not rationale.strip():
            raise AdmissionError("legacy adoption/release requires job or queue id and rationale")
        actor = _owner(owner, owner_session)
        if job_id and not queue_id:
            return _reconcile_legacy_job(root, job_id, action, actor, worker, access, files, rationale, completion)
        path = _queue_path(root, queue_id)
        item = _read(path, required=True)
        previous = item.get("reconciliation") or {}
        if previous.get("action") == action and _same_actor(previous.get("owner", {}), actor):
            return {"items": [item], "applied": 0}
        if item.get("reservation_id"):
            raise AdmissionError("owned queue attempt requires its credentials")
        if any(row.get("queue_id") == queue_id and row.get("stage") != "released" for row in _records(root)):
            raise AdmissionError("queue adoption already has a reserved attempt; reconcile its recorded owner")
        if item.get("status") not in {"claimed", "spawned"}:
            raise AdmissionError("legacy reconciliation requires a claimed queue item")
        associated = item.get("job_id")
        if associated:
            meta = _read(root / ".rig" / "jobs" / _id(associated) / "meta.json") or {}
            process = {"pid": meta.get("pid"), "start_id": meta.get("process_start_id", "")}
            state = _legacy_process_state(process)
            if state == "alive" or state == "unknown" and (completion or {}).get("confirmed_stopped") is not True:
                raise AdmissionError("associated legacy execution is alive or unknown")
        if action == "release" and (completion or {}).get("confirmed_stopped") is not True:
            raise AdmissionError("legacy release requires explicit absence/completion attestation")
        if action == "adopt":
            declared, canonical = canonical_files(root, files)
            _validate(root, worker, "worker", model="", access=access, owner=actor)
            _capacity(root, _accounting(root, skip_queue=queue_id), worker, access, canonical)
            # Write the conservative reservation before changing legacy state.
            # A crash between writes retains its scope and consumes no work.
            audit = {"action": action, "owner": actor, "rationale": rationale, "at": _now(),
                     "previous_status": item["status"], "previous_job_id": associated or ""}
            record = _new_record(actor=actor, job_id="", queue_id=queue_id, worker=worker,
                                 role="worker", model="", access=access, canonical=canonical, declared=declared)
            record.update(reconciliation=audit, pending_operation="bind_queue")
            _save(root, record)
            item.update(job_id="", reconciliation=audit)
            _write(path, item)
            _queue_update(root, record, "claimed")
            record.pop("pending_operation", None)
            _save(root, record)
            result = _read(path, required=True)
            return {"items": [result], "applied": 1, **credentials(record)}
        item.update(status="done" if associated or item.get("status") == "spawned" else "pending", claimed_at="",
                    reconciliation={"action": action, "owner": actor, "rationale": rationale, "at": _now(),
                                    "previous_status": item["status"], "previous_job_id": associated or ""})
        _write(path, item)
        return {"items": [item], "applied": 1}


def _recover_check(root, record, actor, rationale):
    operation = record.get("operation") or {}
    if not operation:
        return False
    if operation.get("operation") != "check":
        raise AdmissionError("this reservation is not held by an interrupted check")
    folder = root / ".rig" / "jobs" / record["job_id"]
    active = _read(folder / "check-running.json") or {}
    identities = [operation]
    if active.get("pid") and active["pid"] != operation.get("pid"):
        identities.append({"pid": active["pid"], "start_id": active.get("start_id", "")})
    if active.get("process_pid"):
        identities.append({"pid": active["process_pid"], "start_id": active.get("process_start_id", "")})
    process = operation.get("process") or {}
    if process:
        identities.extend([process, *process.get("descendants", [])])
    if any(_legacy_process_state(identity) == "alive" for identity in identities):
        raise AdmissionError("check executor or observed subprocess is still alive")
    # Exact current owner attests to stopped unknown descendants; this never
    # overrides an observed live checker, and never accepts or releases files.
    audit = {"operation": operation, "check": active, "owner": actor,
             "rationale": rationale.strip(), "at": _now(), "checks_stopped": True}
    _write(folder / ("check-interrupted-" + operation["id"] + ".json"), audit)
    (folder / "check-running.json").unlink(missing_ok=True)
    record.pop("operation", None)
    record.update(interrupted_operation=audit, needs_reconciliation=True,
                  reconciliation_reason="parent confirmed interrupted checks stopped; assessment required")
    _save(root, record)
    return True


def _reconcile_legacy_job(root, job_id, action, actor, worker, access, files, rationale, completion):
    job_id = _id(job_id, "job id")
    existing = [record for record in _records(root) if record.get("job_id") == job_id]
    if existing:
        if existing[0].get("legacy") and _same_actor(existing[0]["owner"], actor):
            return {"items": [_public(existing[0])], "applied": 0}
        raise AdmissionError("owned execution requires its current attempt credentials")
    if action != "adopt":
        raise AdmissionError("attach a stopped legacy owner with adopt, then close using returned credentials")
    meta = _read(root / ".rig" / "jobs" / job_id / "meta.json", required=True)
    if meta.get("reservation_id"):
        raise AdmissionError("execution references an existing admission attempt")
    state = _legacy_process_state({"pid": meta.get("pid"), "start_id": meta.get("process_start_id", "")})
    if state == "alive" or (completion or {}).get("confirmed_stopped") is not True:
        raise AdmissionError("legacy recovery requires confirmed stopped work; live processes cannot be overridden")
    declared, canonical = canonical_files(root, files)
    _validate(root, worker, meta.get("role", "worker"), "", access, actor)
    rows = [row for row in _accounting(root) if row.get("job_id") != job_id]
    _capacity(root, rows, worker, access, canonical, reserve_slot=False)
    record = _new_record(actor=actor, job_id=job_id, queue_id="", worker=worker,
                         role=meta.get("role", "worker"), model="", access=access,
                         canonical=canonical, declared=declared)
    record.update(legacy=True, ownership_established=False, stage="verifying", slot_held=False, stopped=True,
                  completion=completion, reconciliation={"action": action, "owner": actor,
                                                         "rationale": rationale, "at": _now()})
    _save(root, record)
    return {"items": [_public(record)], "applied": 1, **credentials(record)}


def main():
    parser = argparse.ArgumentParser(prog="admission.py")
    parser.add_argument("command", choices=["reserve", "activate", "finish", "release", "reconcile", "list"])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--input-json", default="{}", help="Explicit operation arguments; tokens may instead use RIG_OWNER_TOKEN.")
    args = parser.parse_args()
    try:
        values = json.loads(args.input_json)
        if not isinstance(values, dict):
            raise AdmissionError("input must be a JSON object")
        if args.command in {"reserve", "activate", "finish", "release"}:
            for key, env in (("reservation_id", "RIG_RESERVATION_ID"), ("attempt_id", "RIG_ATTEMPT_ID"), ("owner_token", "RIG_OWNER_TOKEN")):
                if key not in values and os.environ.get(env):
                    values[key] = os.environ[env]
        function = list_reservations if args.command == "list" else globals()[args.command]
        print(json.dumps(function(Path(args.repo), **values), indent=2))
        return 0
    except (AdmissionError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
