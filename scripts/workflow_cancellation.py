"""Authenticated workflow Stop intent, independent of the admission lock.

A wait binds one workflow incarnation, not whichever workflow later uses its ID.
The marker prevents further admissions; only execution evidence confirms stop.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

import admission
import cancellation
import workflow_state as wf


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def capture(repo, workflow_id, *, owner_token, owner_session=""):
    root = admission._root(repo)
    folder = wf.workflow_dir(root, workflow_id).resolve()
    if folder.parent != root / ".rig" / "workflows":
        raise wf.WorkflowError("workflow belongs to another repository")
    identity = folder.stat()
    credentials = wf.match_workflow_credentials(root, workflow_id, owner_token)
    if owner_session and owner_session != credentials.get("session_id"):
        raise wf.WorkflowError("workflow owner session mismatch")
    spec, _ = wf.load_pair(root, workflow_id, required=True)
    return {"repo": root, "path": folder, "workflow_id": workflow_id,
            "directory_identity": (identity.st_dev, identity.st_ino),
            "created_at": spec["created_at"], "owner_token": owner_token,
            "owner_session": owner_session}


def current(target):
    folder = wf.workflow_dir(target["repo"], target["workflow_id"]).resolve()
    identity = folder.stat()
    if (folder.parent != target["repo"] / ".rig" / "workflows" or folder != target["path"] or
            (identity.st_dev, identity.st_ino) != target["directory_identity"]):
        raise wf.WorkflowError("workflow identity changed; cancellation target was not replaced")
    credentials = wf.match_workflow_credentials(target["repo"], target["workflow_id"], target["owner_token"])
    if target["owner_session"] and target["owner_session"] != credentials.get("session_id"):
        raise wf.WorkflowError("workflow owner session mismatch")
    spec, state = wf.load_pair(target["repo"], target["workflow_id"], required=True)
    if spec.get("created_at") != target["created_at"]:
        raise wf.WorkflowError("workflow identity changed; cancellation target was not replaced")
    return spec, state


def requested(repo, spec):
    folder = wf.workflow_dir(repo, spec["workflow_id"])
    marker = admission._read(folder / "cancel.json") or {}
    if not marker:
        return False
    credentials = admission._read(folder / "owner-credentials.json") or {}
    token = credentials.get("owner_token") or ""
    return bool(token and marker.get("workflow_id") == spec["workflow_id"]
                and marker.get("created_at") == spec.get("created_at")
                and marker.get("credentials_digest") == _digest(token))


def prepare_admission(repo, record):
    """Pin a real workflow launch before any worker can start. Caller holds admission."""
    wid = record.get("workflow_id")
    if not wid:
        return None
    spec, state = wf.load_pair(repo, wid)
    if spec is None or state is None:
        # Historical callers may carry workflow metadata without a saved DAG.
        return None
    if state.get("cancel_requested") or requested(repo, spec):
        raise wf.WorkflowError("workflow cancellation was requested; launch refused")
    if not admission._same_initiating_owner(state.get("owner") or {}, record.get("owner") or {}):
        raise wf.WorkflowError("workflow initiating owner mismatch")
    row = (state.get("nodes") or {}).get(record.get("workflow_node_id"))
    if not row:
        raise wf.WorkflowError("workflow launch identity changed")
    creds = admission._read(wf.workflow_dir(repo, wid) / "owner-credentials.json", required=True)
    binding = {"created_at": spec["created_at"], "credentials_digest": _digest(creds["owner_token"])}
    if record.get("workflow_binding"):
        if record["workflow_binding"] != binding:
            raise wf.WorkflowError("workflow incarnation changed")
    elif (row.get("status") != "launching"
          or record.get("workflow_spec_hash") != spec.get("spec_hash")):
        raise wf.WorkflowError("workflow launch identity changed")
    if row.get("job_id"):
        if any(row.get(key) != record.get(key) for key in ("job_id", "reservation_id", "attempt_id", "workflow_attempt")):
            raise wf.WorkflowError("workflow node already belongs to another attempt")
    elif record.get("workflow_attempt") != int(row.get("workflow_attempt") or 0) + 1:
        raise wf.WorkflowError("workflow launch attempt changed")
    record["workflow_binding"] = binding
    row.update({key: record[key] for key in ("job_id", "reservation_id", "attempt_id", "workflow_attempt")})
    return state


def requested_for_record(repo, record):
    """Detached executors consume Stop even if the scheduler never returned."""
    binding = record.get("workflow_binding") or {}
    wid = record.get("workflow_id")
    if not wid or not binding:
        return False
    spec, _ = wf.load_pair(repo, wid)
    if not spec or spec.get("created_at") != binding.get("created_at"):
        return False
    marker = admission._read(wf.workflow_dir(repo, wid) / "cancel.json") or {}
    return (marker.get("credentials_digest") == binding.get("credentials_digest")
            and requested(repo, spec))


def orphaned_scopes(repo, records):
    """Cancelled workflow scopes stay held when their attempt ledger is missing."""
    known = {(item.get("job_id"), item.get("reservation_id"), item.get("attempt_id")) for item in records}
    scopes = []
    for wid in wf.list_ids(repo):
        spec, state = wf.load_pair(repo, wid)
        if not spec or not state or not (state.get("cancel_requested") or requested(repo, spec)):
            continue
        for node in spec["nodes"]:
            row = (state.get("nodes") or {}).get(node["id"]) or {}
            identity = tuple(row.get(key) for key in ("job_id", "reservation_id", "attempt_id"))
            if not row.get("job_id") or identity in known or not wf._node_stop_unconfirmed(repo, row):
                continue
            _, files = admission.canonical_files(repo, node.get("files") or [])
            scopes.append({"job_id": row["job_id"], "reservation_id": row.get("reservation_id") or "",
                           "attempt_id": row.get("attempt_id") or "", "orphaned_workflow_scope": True,
                           "worker": "", "access": "write" if node["role"] in wf.WRITE_ROLES else "read",
                           "files": files, "scope_unknown": not files, "slot_held": True,
                           "resources": node.get("resources") or [], "needs_reconciliation": True})
    return scopes


def cancel_jobs(repo, state, reason="workflow cancelled"):
    """Stop only the exact attempts recorded by this workflow, never replacements."""
    import jobs

    results = []
    for row in (state.get("nodes") or {}).values():
        job_id = row.get("job_id")
        if not job_id:
            continue
        try:
            jobs_dir = admission._root(repo) / ".rig" / "jobs"
            target = cancellation.capture(jobs_dir / admission._id(job_id, "job id"))
            if target["path"].parent != jobs_dir:
                raise wf.WorkflowError("workflow job belongs to another repository")
            if any(target[key] != (row.get(key) or "") for key in ("reservation_id", "attempt_id")):
                raise wf.WorkflowError("workflow job attempt changed; cancellation target was not replaced")
            results.append(jobs.cancel_target(target, reason, return_details=True))
        except (SystemExit, OSError, ValueError) as error:
            results.append({"job_id": job_id, "state": "stop-unconfirmed", "error": str(error)})
    return results


def publish(target, reason="parent"):
    spec, state = current(target)
    if state.get("status") == "verified":
        return []
    folder = target["path"]
    marker = {"workflow_id": target["workflow_id"], "created_at": target["created_at"],
              "credentials_digest": _digest(target["owner_token"]),
              "reason": str(reason), "at": wf._now()}
    fd, temporary = tempfile.mkstemp(prefix=".cancel-", dir=folder)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(marker, stream)
            stream.flush()
            os.fsync(stream.fileno())
        current(target)
        try:
            os.link(temporary, folder / "cancel.json")
        except FileExistsError:
            if not requested(target["repo"], spec):
                raise wf.WorkflowError("workflow cancellation marker identity changed")
        directory = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    _, state = current(target)
    return cancel_jobs(target["repo"], state, reason)
