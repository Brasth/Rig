#!/usr/bin/env python3
"""Child coordination requests and parent replies. Never expands frozen contracts."""
from __future__ import annotations

import uuid

import admission
import jobs as rig_jobs
import workflow_state as wf

CoordinationError = wf.WorkflowError
KINDS = frozenset({"dependency", "contract", "scope"})
# Coordination may describe a need; it cannot expand the frozen node contract.
EXPAND_KEYS = frozenset({
    "files", "resources", "effects", "depends_on", "dependencies",
    "role", "nodes", "contracts", "frozen_contracts", "contract",
})


def _reject_expansion(payload):
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise CoordinationError("coordination payload must be an object")

    def walk(value):
        if isinstance(value, dict):
            if set(value) & EXPAND_KEYS:
                raise CoordinationError(
                    "coordination never expands files/resources/effects/dependencies/frozen contracts"
                )
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return payload


def _job_workflow(root, job_id):
    meta = admission._read(root / ".rig" / "jobs" / admission._id(job_id, "job id") / "meta.json", required=True)
    wid = str(meta.get("workflow_id") or "").strip()
    nid = str(meta.get("workflow_node_id") or "").strip()
    if not wid:
        raise CoordinationError("job is not bound to a workflow")
    return meta, wid, nid


def request(repo, job_id, kind, text, payload=None):
    kind = str(kind or "").strip()
    if kind not in KINDS:
        raise CoordinationError("coordination kind must be dependency|contract|scope")
    if not isinstance(text, str) or not text.strip():
        raise CoordinationError("coordination request needs text")
    extra = _reject_expansion(payload)
    root = admission._root(repo)
    with admission.transaction(root):
        meta, wid, nid = _job_workflow(root, job_id)
        spec, state = wf.load_pair(root, wid, required=True)
        item = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "status": "pending",
            "job_id": job_id,
            "node_id": nid,
            "text": text.strip(),
            "payload": extra,
            "created_at": wf._now(),
            "reply": None,
        }
        state.setdefault("coordination", []).append(item)
        state["status"] = "blocked"
        wf.save_state(root, state)
        wf.append_event(root, wid, "coordination", {"request_id": item["id"], "kind": kind, "node_id": nid})
        wake = root / ".rig" / "workflows" / wid / "coordination-wake.json"
        admission._write(wake, {"request_id": item["id"], "at": item["created_at"]})
        public = {key: value for key, value in item.items() if key != "owner_token"}
        return {"workflow_id": wid, "request": public}


def reply(repo, workflow_id, request_id, *, decision="reply", text="", owner_token="", owner=None, owner_session=""):
    if decision not in {"reply", "stop"}:
        raise CoordinationError("parent may reply or stop")
    if decision == "reply" and (not isinstance(text, str) or not text.strip()):
        raise CoordinationError("coordination reply needs text")
    root = admission._root(repo)
    with admission.transaction(root):
        wf.match_workflow_credentials(root, workflow_id, owner_token)
        spec, state = wf.load_pair(root, workflow_id, required=True)
        items = state.setdefault("coordination", [])
        item = next((row for row in items if row.get("id") == request_id), None)
        if item is None:
            raise CoordinationError("unknown coordination request")
        if item.get("status") != "pending":
            return wf.public_record(spec, state)
        item["status"] = "stopped" if decision == "stop" else "replied"
        item["reply"] = {"decision": decision, "text": str(text or "").strip(), "at": wf._now()}
        if decision == "stop":
            nid = item.get("node_id") or ""
            if nid and nid in state.get("nodes", {}):
                state["nodes"][nid]["blocker"] = "parent stopped coordination"
                job_id = state["nodes"][nid].get("job_id")
                if job_id:
                    try:
                        rig_jobs.cancel_job(root, job_id, "coordination-stop")
                    except (SystemExit, OSError, ValueError):
                        pass
        state = wf.refresh_locked(root, spec, state)
        wf.save_state(root, state)
        wf.append_event(root, workflow_id, "coordination_reply", {
            "request_id": request_id, "decision": decision,
        })
        return wf.public_record(spec, state)


def pending(repo, workflow_id=""):
    rows = []
    ids = [workflow_id] if workflow_id else wf.list_ids(repo)
    for wid in ids:
        spec, state = wf.load_pair(repo, wid)
        if state is None:
            continue
        for item in state.get("coordination") or []:
            if item.get("status") == "pending":
                rows.append({"workflow_id": wid, **{k: v for k, v in item.items() if k != "owner_token"}})
    return rows
