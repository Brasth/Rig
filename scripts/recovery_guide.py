"""Bounded read-only recovery projection. Never refreshes lifecycle or probes processes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import context_packages
import recovery_view
from task_timeline import Reader, _id

STATES = frozenset({"running", "ask", "ok", "fail", "failed", "timeout", "cancelled",
    "cancel_requested", "cancel-requested", "unconfirmed", "stop-unconfirmed", "stop-requested",
    "native-cancel-required", "planned", "pending", "ready", "launching", "attention", "blocked",
    "completed-unverified", "verified", "accepted", "skipped"})


def _bound(value, meta, *, verification=False):
    keys = ("attempt_id", "reservation_id") if verification else ("job_id", "attempt_id", "reservation_id")
    return bool(value and meta.get("attempt_id") and meta.get("reservation_id") and
                (not verification or value.get("job_id", meta.get("job_id")) == meta.get("job_id")) and
                all(value.get(k) == meta.get(k) for k in keys))


def _held(reader, reservation):
    if not reservation or reservation.get("stage") == "released":
        return []
    files, resources = [], []
    raw = reservation.get("files")
    if isinstance(raw, list) and len(raw) <= 256:
        for value in raw:
            try:
                path = context_packages._relative(value)
                context_packages._scan(path, "held file")
                files.append(path)
            except (ValueError, TypeError):
                reader.issues["scope_redacted"] += 1
    else:
        reader.issues["scope_unavailable"] += 1
    raw = reservation.get("resources", [])
    if isinstance(raw, list) and len(raw) <= 256:
        for value in raw:
            if isinstance(value, dict) and _id(value.get("name")) and value.get("access") in {"read", "write"}:
                resources.append(value["name"] + " (" + value["access"] + ")")
            else:
                reader.issues["resource_redacted"] += 1
    else:
        reader.issues["resource_unavailable"] += 1
    slot = reservation.get("slot_held")
    return [{"files": files, "resources": resources,
             "slot": "held" if slot is True else "free" if slot is False else "unknown"}]


def _intent(reader, base, meta, reservation):
    """Read persisted control records without consuming prompts or refreshing state."""
    marker, source = reader.read(base + "/cancellation/" + meta["attempt_id"] + ".json")
    stopped = bool(source and _bound(marker, meta))
    if source and not stopped:
        reader.issues["cancellation_binding_unavailable"] += 1
    _, legacy = reader.read(base + "/cancel.json")
    stopped = stopped or bool(legacy)
    wid, binding = reservation.get("workflow_id"), reservation.get("workflow_binding")
    if _id(wid) and isinstance(binding, dict):
        workflow_marker, source = reader.read(".rig/workflows/" + wid + "/cancel.json")
        if source:
            matches = bool(workflow_marker and workflow_marker.get("workflow_id") == wid and
                           all(binding.get(k) and workflow_marker.get(k) == binding[k]
                               for k in ("created_at", "credentials_digest")))
            stopped = stopped or matches
            if not matches:
                reader.issues["workflow_cancellation_binding_unavailable"] += 1
    ask, _ = reader.read(base + "/ask.json")
    reply, _ = reader.read(base + "/ask-reply.json") if ask else (None, None)
    pending = False
    if ask:
        if _bound(ask, meta, verification=True) and _id(ask.get("ask_id")):
            pending = not (reply and all(reply.get(k) == ask.get(k)
                             for k in ("ask_id", "attempt_id", "reservation_id", "tool_use_id")))
        else:
            reader.issues["ask_binding_unavailable"] += 1
    return stopped, pending


def _job(reader, job_id, expected_attempt=None, workflow_stopping=False):
    base = ".rig/jobs/" + job_id
    meta, _ = reader.read(base + "/meta.json", required=True)
    meta = meta or {}
    valid = (meta.get("job_id") == job_id and _id(meta.get("attempt_id")) and
             _id(meta.get("reservation_id")) and
             (expected_attempt is None or meta.get("attempt_id") == expected_attempt))
    if not valid:
        reader.issues["attempt_binding_unavailable"] += 1
        return {"state": "unknown", "blockers": ["incomplete-evidence"], "held": [],
                "steps": [recovery_view.step("rig_job_show", "Inspect the exact attempt and its ownership.")]}
    reservation, _ = reader.read(".rig/reservations/" + meta["reservation_id"] + ".json", required=True)
    verification, _ = reader.read(base + "/verification.json")
    complete = _bound(reservation, meta)
    if not complete:
        reader.issues["reservation_binding_unavailable"] += 1
        reservation = {}
    elif not isinstance(reservation.get("stopped"), bool) or not isinstance(reservation.get("slot_held"), bool):
        reader.issues["completion_evidence_unavailable"] += 1
        complete = False
    # Bind verification to the exact attempt; reject contradictory job IDs while
    # permitting older records whose job identity comes from their directory.
    if verification and not _bound(verification, meta, verification=True):
        reader.issues["verification_binding_unavailable"] += 1
        verification = {}
    verification = verification or {}
    raw_state = reservation.get("execution_status") or meta.get("cancellation_state") or meta.get("status")
    state = raw_state if isinstance(raw_state, str) and raw_state in STATES else "unknown"
    before = sum(reader.issues.values())
    stopping, pending = _intent(reader, base, meta, reservation)
    if sum(reader.issues.values()) != before:
        complete = False
    if not reservation.get("stopped"):
        if stopping or workflow_stopping:
            state = "stop-requested"
        elif pending:
            state = "ask"
    if state == "unknown":
        complete = False
        reader.issues["state_unavailable"] += 1
    blockers, steps = recovery_view.explain(state, reservation, verification, evidence_complete=complete)
    return {"state": state, "blockers": blockers, "held": _held(reader, reservation), "steps": steps}


def build(repo, *, job_id=None, workflow_id=None):
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise ValueError("recovery guidance is parent-only")
    if (job_id is not None) + (workflow_id is not None) != 1 or not _id(job_id or workflow_id):
        raise ValueError("select exactly one valid job_id or workflow_id")
    reader = Reader(Path(repo))
    if job_id:
        result = _job(reader, job_id)
    else:
        saved, _ = reader.read(".rig/workflows/" + workflow_id + "/state.json", required=True)
        saved = saved or {}
        if saved.get("workflow_id") != workflow_id:
            reader.issues["workflow_binding_unavailable"] += 1
            saved = {}
        marker, marker_source = reader.read(".rig/workflows/" + workflow_id + "/cancel.json")
        workflow_stopping = saved.get("cancel_requested") is True or bool(marker_source)
        if marker_source and (not marker or marker.get("workflow_id") != workflow_id or
                              not saved.get("created_at") or marker.get("created_at") != saved["created_at"]):
            reader.issues["workflow_cancellation_binding_unavailable"] += 1
        raw_state = saved.get("status")
        result = {"state": raw_state if isinstance(raw_state, str) and raw_state in STATES else "unknown",
                  "blockers": [], "held": [], "steps": []}
        nodes = saved.get("nodes")
        if not isinstance(nodes, dict) or len(nodes) > 64:
            reader.issues["workflow_nodes_unavailable"] += 1
        else:
            for node in nodes.values():
                if not isinstance(node, dict):
                    reader.issues["workflow_node_unavailable"] += 1
                    continue
                if _id(node.get("job_id")) and _id(node.get("attempt_id")):
                    job = _job(reader, node["job_id"], node["attempt_id"],
                               workflow_stopping=workflow_stopping)
                    result["blockers"].extend(job["blockers"])
                    result["held"].extend(job["held"])
                    result["steps"].extend(job["steps"])
                elif node.get("job_id") or node.get("attempt_id"):
                    reader.issues["workflow_attempt_unavailable"] += 1
        if result["state"] in {"blocked", "failed", "attention"}:
            result["blockers"].append("workflow-blocked")
        if workflow_stopping:
            result["blockers"].append("recorded-workflow-stop-intent")
        # Recorded graph state never grants retry, skip or approval authority.
        result["steps"].append(recovery_view.step("rig_workflow_report",
            "Inspect recorded dependency blockers, then use supported resolve/advance operations deliberately.",
            "Workflow owner credentials for mutations", "Confirmed predecessor termination and current acceptance"))
    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    result["steps"] = [value for index, value in enumerate(result["steps"]) if value not in result["steps"][:index]]
    return {"schema_version": 1, "advisory_only": True, "scope": {"job_id": job_id} if job_id else
            {"workflow_id": workflow_id}, **result,
            "coverage": "unknown" if reader.files == 0 else "partial" if reader.issues else "recorded",
            "evidence_gaps": sorted(reader.issues),
            "freshness": "recorded evidence only; current content and process state are not revalidated"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["guide"])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job", dest="job_id")
    group.add_argument("--workflow", dest="workflow_id")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build(args.repo, job_id=args.job_id, workflow_id=args.workflow_id)
        print(json.dumps(result, indent=2) if args.json else "\n".join(recovery_view.format_lines(result)))
        return 0
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
