#!/usr/bin/env python3
"""Repository-local workflow spec, state, events, and schema validation."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import admission
import harness

WorkflowError = admission.AdmissionError

STATUSES = (
    "planned", "running", "attention", "blocked", "completed-unverified",
    "verified", "failed", "cancel-requested", "cancelled",
)
TERMINAL = frozenset({"verified", "cancelled"})
ACTIVE = frozenset({"planned", "running", "attention", "blocked", "completed-unverified",
                    "failed", "cancel-requested"})
NODE_ROLES = frozenset({"mini", "bulk", "implement", "hard", "verify", "review"})
WRITE_ROLES = frozenset({"mini", "bulk", "implement", "hard"})
READ_ROLES = frozenset({"verify", "review"})
EFFECTS = frozenset({"none", "local", "external", "production", "destructive"})
GATED_EFFECTS = frozenset({"external", "production", "destructive"})
NODE_STATES = frozenset({
    "pending", "ready", "launching", "running", "ask", "unconfirmed",
    "completed-unverified", "accepted", "failed", "skipped", "cancelled", "blocked",
})
_ID = admission._ID
DEFAULT_MAX_NODES = 12
LAUNCHED_CONTRACT = (
    "id", "role", "effects", "files", "resources", "depends_on", "required",
    "brief", "kind", "final", "priority", "assessment", "shared_context",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _root(repo):
    return admission._root(repo)


def workflow_dir(repo, workflow_id):
    return _root(repo) / ".rig" / "workflows" / admission._id(workflow_id, "workflow id")


def _spec_path(folder):
    return folder / "spec.json"


def _state_path(folder):
    return folder / "state.json"


def _events_dir(folder):
    return folder / "events"


def _credentials_path(folder):
    return folder / "owner-credentials.json"


def canonical_resources(raw):
    """Validate opaque resource names with read|write access. Never store secrets."""
    try:
        return admission.canonical_resources(raw)
    except admission.AdmissionError as error:
        raise WorkflowError(str(error)) from error


def resources_conflict(left, right):
    return admission.resources_conflict(left, right)


def spec_hash(spec):
    payload = {key: value for key, value in spec.items() if key != "spec_hash"}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def orchestration_config(repo):
    data = harness.parse_harness(harness.harness_path(_root(repo))).get("orchestration") or {}
    mode = str(data.get("mode") or "adaptive").strip().lower()
    if mode not in {"adaptive", "single"}:
        mode = "adaptive"
    try:
        max_nodes = int(data.get("max_nodes") or DEFAULT_MAX_NODES)
    except (TypeError, ValueError):
        max_nodes = DEFAULT_MAX_NODES
    return {"mode": mode, "max_nodes": max(1, max_nodes)}


def require_adaptive_orchestration(repo):
    """Refuse mutating workflow ops when rollback mode restores one-job behavior."""
    cfg = orchestration_config(repo)
    if cfg["mode"] == "single":
        raise WorkflowError(
            "orchestration mode is single; workflow create, advance, extend, approve, "
            "and resolve are refused"
        )
    return cfg


def _node_files(node):
    files = node.get("files") or []
    if not isinstance(files, list) or any(not isinstance(item, str) or not item.strip() for item in files):
        raise WorkflowError(f"node {node.get('id')}: write nodes list concrete files")
    return [item.strip() for item in files]


def _is_writer(node):
    return node.get("role") in WRITE_ROLES


def _writer_files(node):
    return set(_node_files(node)) if _is_writer(node) else set()


def _writer_resources(node):
    return [item for item in canonical_resources(node.get("resources")) if item["access"] == "write"]


def _depends_set(node):
    deps = node.get("depends_on") or []
    if not isinstance(deps, list) or any(not isinstance(item, str) for item in deps):
        raise WorkflowError(f"node {node.get('id')}: depends_on must be an array of node ids")
    return list(dict.fromkeys(deps))


def _cycle(nodes):
    index = {node["id"]: node for node in nodes}
    visiting, seen = set(), set()

    def walk(nid):
        if nid in seen:
            return False
        if nid in visiting:
            return True
        visiting.add(nid)
        for dep in index[nid]["depends_on"]:
            if dep not in index or walk(dep):
                return True
        visiting.remove(nid)
        seen.add(nid)
        return False

    return any(walk(node["id"]) for node in nodes)


def _downstream_depth(nodes):
    index = {node["id"]: node for node in nodes}
    cache = {}

    def depth(nid):
        if nid in cache:
            return cache[nid]
        kids = [other["id"] for other in nodes if nid in other["depends_on"]]
        cache[nid] = 1 + max((depth(kid) for kid in kids), default=0)
        return cache[nid]

    return {node["id"]: depth(node["id"]) for node in nodes}


def _transitive(nodes, nid):
    index = {node["id"]: node for node in nodes}
    out, stack = set(), list(index[nid]["depends_on"])
    while stack:
        cur = stack.pop()
        if cur in out or cur not in index:
            continue
        out.add(cur)
        stack.extend(index[cur]["depends_on"])
    return out


def _normalize_node(raw, index, *, max_nodes):
    if not isinstance(raw, dict):
        raise WorkflowError("each node must be an object")
    nid = str(raw.get("id") or "").strip()
    if not nid or not _ID.fullmatch(nid) or nid in {".", ".."}:
        raise WorkflowError("node id must be a stable identifier")
    role = str(raw.get("role") or "").strip()
    if role not in NODE_ROLES:
        raise WorkflowError(f"node {nid}: role must be mini|bulk|implement|hard|verify|review")
    effects = str(raw.get("effects") or "none").strip() or "none"
    if effects not in EFFECTS:
        raise WorkflowError(f"node {nid}: effects must be none|local|external|production|destructive")
    files = _node_files({"id": nid, **raw})
    if role in WRITE_ROLES and not files:
        raise WorkflowError(f"node {nid}: write nodes list concrete files")
    resources = canonical_resources(raw.get("resources"))
    depends_on = _depends_set({"id": nid, **raw})
    required = raw.get("required")
    if required is None:
        required = True
    if type(required) is not bool:
        raise WorkflowError(f"node {nid}: required must be a boolean")
    try:
        priority = int(raw.get("priority") or 0)
    except (TypeError, ValueError) as error:
        raise WorkflowError(f"node {nid}: priority must be an integer") from error
    node = {
        "id": nid,
        "role": role,
        "effects": effects,
        "files": files,
        "resources": resources,
        "depends_on": depends_on,
        "required": required,
        "priority": priority,
        "brief": str(raw.get("brief") or raw.get("case") or "").strip(),
        "kind": str(raw.get("kind") or ("final-verify" if raw.get("final") else role)).strip() or role,
        "final": bool(raw.get("final")),
        "assessment": raw.get("assessment") if isinstance(raw.get("assessment"), dict) else {},
    }
    if raw.get("shared_context") not in (None, ""):
        node["shared_context"] = str(raw.get("shared_context"))
    return node


def _validate_graph(nodes):
    ids = [node["id"] for node in nodes]
    if len(ids) != len(set(ids)):
        raise WorkflowError("node ids must be unique")
    index = {node["id"]: node for node in nodes}
    for node in nodes:
        for dep in node["depends_on"]:
            if dep not in index:
                raise WorkflowError(f"node {node['id']}: unknown dependency {dep}")
            if dep == node["id"]:
                raise WorkflowError(f"node {node['id']}: dependency cycle")
    if _cycle(nodes):
        raise WorkflowError("workflow DAG must not contain cycles")
    writers = [node for node in nodes if _is_writer(node)]
    for i, left in enumerate(writers):
        left_files, left_res = _writer_files(left), _writer_resources(left)
        for right in writers[i + 1:]:
            overlap = left_files & _writer_files(right)
            if overlap:
                raise WorkflowError(
                    "overlapping workflow writer scopes are rejected, not sequenced: "
                    + ", ".join(sorted(overlap)[:8])
                )
            conflict = resources_conflict(left_res, _writer_resources(right))
            if conflict:
                raise WorkflowError(f"overlapping workflow writer resource {conflict}")
        if not left_files:
            raise WorkflowError(f"node {left['id']}: unknown write scope is exclusive; list concrete files")
    for node in nodes:
        if _is_writer(node):
            continue
        files = set(node["files"])
        deps = _transitive(nodes, node["id"])
        for writer in writers:
            overlap = files & _writer_files(writer)
            if overlap and writer["id"] not in deps:
                raise WorkflowError(
                    f"node {node['id']}: read overlap with writer {writer['id']} "
                    "only after accepted dependency"
                )
            read_res = [item for item in node["resources"] if item["access"] == "read"]
            conflict = resources_conflict(read_res, _writer_resources(writer))
            if conflict and writer["id"] not in deps:
                raise WorkflowError(
                    f"node {node['id']}: resource {conflict} overlaps writer {writer['id']} "
                    "only after accepted dependency"
                )


def _needs_final_verify(nodes):
    writers = [node for node in nodes if _is_writer(node)]
    side = [node for node in nodes if node.get("effects", "none") != "none"]
    return len(writers) > 1 or bool(side)


def _is_final_verify_node(node):
    return bool(node.get("final") or node.get("kind") == "final-verify")


def _required_predecessor_ids(nodes, exclude_id=""):
    """Required execution, review, and seed nodes that final verify must wait on."""
    return [
        node["id"] for node in nodes
        if node["id"] != exclude_id
        and node.get("required", True)
        and node["role"] in (WRITE_ROLES | {"review"})
    ]


def _union_required_scope(nodes, required_ids):
    files, resources = [], []
    seen_files, seen_res = set(), set()
    required_set = set(required_ids)
    for node in nodes:
        if node["id"] not in required_set:
            continue
        for name in node.get("files") or []:
            if name not in seen_files:
                seen_files.add(name)
                files.append(name)
        for item in node.get("resources") or []:
            key = (item["name"], "read")
            if key not in seen_res:
                seen_res.add(key)
                resources.append({"name": item["name"], "access": "read"})
    return files, resources


def _apply_final_verify_policy(node, nodes):
    """Normalize a final-verify node onto policy, or reject an invalid role."""
    if node.get("role") != "verify":
        raise WorkflowError("final integration node must use role verify")
    required_ids = _required_predecessor_ids(nodes, exclude_id=node["id"])
    files, resources = _union_required_scope(nodes, required_ids)
    deps = [item for item in list(node.get("depends_on") or []) if item != node["id"]]
    for nid in required_ids:
        if nid not in deps:
            deps.append(nid)
    seen_files = set()
    merged_files = []
    for name in list(node.get("files") or []) + files:
        if name not in seen_files:
            seen_files.add(name)
            merged_files.append(name)
    seen_res = set()
    merged_res = []
    for item in list(node.get("resources") or []) + resources:
        name = item.get("name") if isinstance(item, dict) else ""
        if not name:
            continue
        key = (name, "read")
        if key in seen_res:
            continue
        seen_res.add(key)
        merged_res.append({"name": name, "access": "read"})
    node["role"] = "verify"
    node["required"] = True
    node["final"] = True
    node["kind"] = "final-verify"
    node["depends_on"] = deps
    node["files"] = merged_files
    node["resources"] = merged_res
    return node


def _ensure_final_verify(nodes, max_nodes):
    existing = [node for node in nodes if _is_final_verify_node(node)]
    if existing:
        for node in existing:
            _apply_final_verify_policy(node, nodes)
        return nodes
    if not _needs_final_verify(nodes):
        return nodes
    if len(nodes) >= max_nodes:
        raise WorkflowError("final verify required but max_nodes would be exceeded")
    required = _required_predecessor_ids(nodes)
    files, resources = _union_required_scope(nodes, required)
    nodes.append({
        "id": "final-verify",
        "role": "verify",
        "effects": "none",
        "files": files,
        "resources": resources,
        "depends_on": required,
        "required": True,
        "priority": 0,
        "brief": "Final integration verification of required workflow results",
        "kind": "final-verify",
        "final": True,
        "assessment": {},
    })
    return nodes


def normalize_spec(raw, *, max_nodes=DEFAULT_MAX_NODES, workflow_id=""):
    if not isinstance(raw, dict):
        raise WorkflowError("workflow spec must be an object")
    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise WorkflowError("workflow spec needs a nonempty nodes array")
    if len(nodes_raw) > max_nodes:
        raise WorkflowError(f"workflow exceeds max_nodes {max_nodes}")
    index = {}
    nodes = []
    for item in nodes_raw:
        node = _normalize_node(item, index, max_nodes=max_nodes)
        if node["id"] in index:
            raise WorkflowError(f"node ids must be unique: duplicate {node['id']}")
        index[node["id"]] = node
        nodes.append(node)
    nodes = _ensure_final_verify(nodes, max_nodes)
    if len(nodes) > max_nodes:
        raise WorkflowError(f"workflow exceeds max_nodes {max_nodes}")
    _validate_graph(nodes)
    wid = str(raw.get("workflow_id") or workflow_id or "").strip()
    spec = {
        "version": 1,
        "workflow_id": wid,
        "title": str(raw.get("title") or raw.get("case") or "").strip(),
        "case": str(raw.get("case") or raw.get("title") or "").strip(),
        "shared_context": str(raw.get("shared_context") or "").strip(),
        "queue_id": str(raw.get("queue_id") or "").strip(),
        "nodes": nodes,
        "created_at": str(raw.get("created_at") or ""),
    }
    spec["spec_hash"] = spec_hash(spec)
    return spec


def _empty_node_state(node):
    return {
        "status": "pending",
        "job_id": "",
        "reservation_id": "",
        "attempt_id": "",
        "workflow_attempt": 0,
        "routing": None,
        "launched": False,
        "ran": False,
        "accepted": False,
        "acceptance_snapshot": "",
        "blocker": "",
        "approval": None,
        "skip_rationale": "",
        "started_at": "",
        "ended_at": "",
        "exclude": [],
        "parent_action": None,
    }


def _new_state(spec, *, owner):
    return {
        "version": 1,
        "workflow_id": spec["workflow_id"],
        "status": "planned",
        "spec_hash": spec["spec_hash"],
        "shared_context": spec.get("shared_context") or "",
        "shared_context_frozen": False,
        "queue_id": spec.get("queue_id") or "",
        "created_at": spec.get("created_at") or _now(),
        "updated_at": _now(),
        "nodes": {node["id"]: _empty_node_state(node) for node in spec["nodes"]},
        "failure": None,
        "coordination": [],
        "cancel_requested": False,
        "parent_action": None,
        "advance_generation": 0,
        "metrics": {"started_at": "", "ended_at": "", "max_concurrency": 0, "launches": 0},
        "owner": admission._public_owner(owner) if hasattr(admission, "_public_owner") else {
            key: value for key, value in (owner or {}).items() if key != "owner_token"
        },
    }


def _public_owner(owner):
    result = copy.deepcopy(owner) if isinstance(owner, dict) else {}
    result.pop("owner_token", None)
    return result


def public_record(spec, state, *, include_events=False):
    payload = {
        "workflow_id": spec.get("workflow_id") or state.get("workflow_id"),
        "status": state.get("status") or "planned",
        "spec_hash": spec.get("spec_hash") or state.get("spec_hash"),
        "title": spec.get("title") or "",
        "case": spec.get("case") or "",
        "shared_context": state.get("shared_context") if state.get("shared_context_frozen") else spec.get("shared_context") or "",
        "shared_context_frozen": bool(state.get("shared_context_frozen")),
        "queue_id": state.get("queue_id") or spec.get("queue_id") or "",
        "nodes": spec.get("nodes") or [],
        "node_state": copy.deepcopy(state.get("nodes") or {}),
        "failure": copy.deepcopy(state.get("failure")),
        "coordination": [
            {key: value for key, value in item.items() if key != "owner_token"}
            for item in (state.get("coordination") or []) if isinstance(item, dict)
        ],
        "cancel_requested": bool(state.get("cancel_requested")),
        "parent_action": copy.deepcopy(state.get("parent_action")),
        "metrics": copy.deepcopy(state.get("metrics") or {}),
        "created_at": spec.get("created_at") or state.get("created_at") or "",
        "updated_at": state.get("updated_at") or "",
        "blocker": summary_blocker(spec, state),
        "counts": summary_counts(spec, state),
        "next_parent_action": next_parent_action(spec, state),
    }
    payload.pop("owner_token", None)
    if include_events:
        payload["events"] = []
    return payload


def summary_counts(spec, state):
    nodes = spec.get("nodes") or []
    rows = state.get("nodes") or {}
    required = [node for node in nodes if node.get("required", True)]
    accepted = sum(1 for node in required if (rows.get(node["id"]) or {}).get("accepted"))
    running = sum(1 for row in rows.values() if row.get("status") in {"running", "launching"})
    asking = sum(1 for row in rows.values() if row.get("status") == "ask")
    return {
        "required": len(required),
        "accepted": accepted,
        "running": running,
        "ask": asking,
        "nodes": len(nodes),
    }


def summary_blocker(spec, state):
    if state.get("cancel_requested") and state.get("status") != "cancelled":
        return "cancellation requested"
    failure = state.get("failure") or {}
    if failure.get("node_id"):
        return f"unresolved failure on {failure['node_id']}"
    pending = [item for item in (state.get("coordination") or []) if item.get("status") == "pending"]
    if pending:
        return f"coordination {pending[0].get('kind') or 'request'}"
    for node in spec.get("nodes") or []:
        row = (state.get("nodes") or {}).get(node["id"]) or {}
        if row.get("status") == "unconfirmed":
            return str(row.get("blocker") or "execution stop unconfirmed")
    action = state.get("parent_action") or {}
    if action.get("kind"):
        return str(action.get("reason") or action.get("kind"))
    for node in spec.get("nodes") or []:
        row = (state.get("nodes") or {}).get(node["id"]) or {}
        if row.get("blocker"):
            return str(row["blocker"])
    return ""


def _approval_ok(spec, row):
    approval = (row or {}).get("approval") or {}
    return bool(approval.get("granted") and approval.get("spec_hash") == spec.get("spec_hash"))


def _deps_accepted(spec, state, node):
    rows = state.get("nodes") or {}
    for dep in node.get("depends_on") or []:
        if not (rows.get(dep) or {}).get("accepted"):
            return False
    return True


def next_parent_action(spec, state):
    status = state.get("status") or "planned"
    action = state.get("parent_action")
    if isinstance(action, dict) and action.get("kind"):
        return action
    if state.get("cancel_requested") and status != "cancelled":
        return {"kind": "confirm_stop"}
    failure = state.get("failure") or {}
    if failure.get("node_id"):
        return {"kind": "resolve", "node_id": failure["node_id"]}
    pending = [item for item in (state.get("coordination") or []) if item.get("status") == "pending"]
    if pending:
        return {"kind": "coordination_reply", "request_id": pending[0].get("id")}
    asking = [nid for nid, row in (state.get("nodes") or {}).items() if row.get("status") == "ask"]
    if asking:
        return {"kind": "allow_or_deny", "node_id": asking[0], "job_id": (state["nodes"][asking[0]] or {}).get("job_id")}
    unconfirmed = [nid for nid, row in (state.get("nodes") or {}).items() if row.get("status") == "unconfirmed"]
    if unconfirmed:
        row = state["nodes"][unconfirmed[0]] or {}
        return {"kind": "reconcile", "node_id": unconfirmed[0], "job_id": row.get("job_id")}
    gated = [
        node["id"] for node in spec.get("nodes") or []
        if node.get("effects") in GATED_EFFECTS
        and _deps_accepted(spec, state, node)
        and ((state.get("nodes") or {}).get(node["id"]) or {}).get("status") in {"pending", "ready", "blocked"}
        and not _approval_ok(spec, (state.get("nodes") or {}).get(node["id"]))
        and not ((state.get("nodes") or {}).get(node["id"]) or {}).get("launched")
    ]
    if gated:
        return {"kind": "approve", "node_id": gated[0]}
    if status in {"planned", "running"}:
        return {"kind": "advance"}
    if status == "completed-unverified":
        return {"kind": "accept", "reason": "final parent acceptance required"}
    if status == "attention":
        return {"kind": "accept", "reason": "parent acceptance required"}
    if status == "cancel-requested":
        return {"kind": "confirm_stop"}
    return None


def summary_row(spec, state):
    counts = summary_counts(spec, state)
    return {
        "workflow_id": spec.get("workflow_id") or state.get("workflow_id"),
        "status": state.get("status") or "planned",
        "accepted": counts["accepted"],
        "required": counts["required"],
        "running": counts["running"],
        "ask": counts["ask"],
        "blocker": summary_blocker(spec, state),
        "next_parent_action": next_parent_action(spec, state),
        "spec_hash": spec.get("spec_hash") or state.get("spec_hash") or "",
        "updated_at": state.get("updated_at") or "",
    }


def _read_legacy(path):
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def load_spec(repo, workflow_id, *, required=False):
    path = _spec_path(workflow_dir(repo, workflow_id))
    spec = _read_legacy(path)
    if spec is None:
        if required:
            raise WorkflowError("workflow spec is missing")
        return None
    spec.setdefault("version", 1)
    spec.setdefault("nodes", [])
    spec.setdefault("spec_hash", spec_hash(spec) if spec.get("nodes") else "")
    return spec


def load_state(repo, workflow_id, *, required=False):
    path = _state_path(workflow_dir(repo, workflow_id))
    state = _read_legacy(path)
    if state is None:
        if required:
            raise WorkflowError("workflow state is missing")
        return None
    state.setdefault("version", 1)
    state.setdefault("nodes", {})
    state.setdefault("coordination", [])
    state.setdefault("metrics", {})
    state.pop("owner_token", None)
    return state


def load_pair(repo, workflow_id, *, required=False):
    spec, state = load_spec(repo, workflow_id, required=required), load_state(repo, workflow_id, required=required)
    if spec is None or state is None:
        return None, None
    return spec, state


def save_spec(repo, spec):
    folder = workflow_dir(repo, spec["workflow_id"])
    spec = copy.deepcopy(spec)
    spec["spec_hash"] = spec_hash(spec)
    admission._write(_spec_path(folder), spec)
    return spec


def save_state(repo, state):
    state = copy.deepcopy(state)
    state["updated_at"] = _now()
    state.pop("owner_token", None)
    admission._write(_state_path(workflow_dir(repo, state["workflow_id"])), state)
    return state


def append_event(repo, workflow_id, kind, payload=None):
    folder = _events_dir(workflow_dir(repo, workflow_id))
    folder.mkdir(parents=True, exist_ok=True)
    seq = 1
    for path in folder.glob("*.json"):
        prefix = path.name.split("-", 1)[0]
        if prefix.isdigit():
            seq = max(seq, int(prefix) + 1)
    extra = {key: value for key, value in (payload or {}).items() if key not in {"version", "seq", "kind", "at", "workflow_id"}}
    extra.pop("owner_token", None)
    event = {
        "version": 1,
        "seq": seq,
        "kind": kind,
        "at": _now(),
        "workflow_id": workflow_id,
        **extra,
    }
    admission._write(folder / f"{seq:06d}-{kind}.json", event)
    return event


def list_events(repo, workflow_id):
    folder = _events_dir(workflow_dir(repo, workflow_id))
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json")):
        item = _read_legacy(path)
        if item:
            item.pop("owner_token", None)
            rows.append(item)
    return rows


def list_ids(repo):
    folder = _root(repo) / ".rig" / "workflows"
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir()):
        if path.is_dir() and _ID.fullmatch(path.name) and (_spec_path(path).is_file() or _state_path(path).is_file()):
            out.append(path.name)
    return out


def list_workflows(repo, *, include_terminal=True):
    rows = []
    for wid in list_ids(repo):
        spec, state = load_pair(repo, wid)
        if spec is None or state is None:
            continue
        if not include_terminal and (state.get("status") in TERMINAL):
            continue
        rows.append(summary_row(spec, state))
    rows.sort(key=lambda row: (0 if row["status"] in ACTIVE else 1, row.get("updated_at") or "", row["workflow_id"]))
    return rows


def active_writer_scopes(repo, *, skip=""):
    scopes = []
    for wid in list_ids(repo):
        if wid == skip:
            continue
        spec, state = load_pair(repo, wid)
        if spec is None or state is None or state.get("status") in TERMINAL:
            continue
        for node in spec.get("nodes") or []:
            if not _is_writer(node):
                continue
            row = (state.get("nodes") or {}).get(node["id"]) or {}
            if row.get("status") in {"skipped", "cancelled"}:
                continue
            scopes.append({
                "workflow_id": wid,
                "node_id": node["id"],
                "files": list(_writer_files(node)),
                "resources": _writer_resources(node),
            })
    return scopes


def _reject_writer_overlap(repo, spec, *, skip=""):
    for node in spec["nodes"]:
        if not _is_writer(node):
            continue
        files, resources = _writer_files(node), _writer_resources(node)
        for other in active_writer_scopes(repo, skip=skip):
            overlap = files & set(other["files"])
            if overlap:
                raise WorkflowError(
                    f"overlapping workflow writer scopes with {other['workflow_id']}/{other['node_id']}: "
                    + ", ".join(sorted(overlap)[:8])
                )
            conflict = resources_conflict(resources, other["resources"])
            if conflict:
                raise WorkflowError(
                    f"overlapping workflow writer resource {conflict} with {other['workflow_id']}"
                )


def write_credentials(repo, workflow_id, record):
    path = _credentials_path(workflow_dir(repo, workflow_id))
    admission._write(path, record)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def match_workflow_credentials(repo, workflow_id, owner_token):
    if not isinstance(owner_token, str) or not owner_token:
        raise WorkflowError("owner credentials required")
    path = _credentials_path(workflow_dir(repo, workflow_id))
    record = admission._read(path, required=True)
    if not hmac.compare_digest(str(record.get("owner_token") or ""), owner_token):
        raise WorkflowError("workflow owner credentials mismatch")
    return record


def _bind_queue(root, spec, state, status):
    queue_id = state.get("queue_id") or spec.get("queue_id") or ""
    if not queue_id:
        return
    path = admission._queue_path(root, queue_id)
    item = admission._read(path, required=True)
    if item.get("workflow_id") not in {None, "", spec["workflow_id"]}:
        raise WorkflowError("queue belongs to another workflow")
    if status == "pending":
        raise WorkflowError("workflow-bound queue is never pending")
    if item.get("status") == "cancelled" and status != "cancelled":
        return
    if item.get("status") == "done" and status in {"claimed", "spawned"}:
        return
    item["workflow_id"] = spec["workflow_id"]
    item["status"] = status
    item.pop("owner_token", None)
    admission._write(path, item)


def create_workflow(repo, raw, *, owner=None, owner_session="", queue_id=""):
    root = _root(repo)
    actor = admission._owner(owner, owner_session)
    with admission.transaction(root):
        cfg = require_adaptive_orchestration(root)
        spec = normalize_spec(raw, max_nodes=cfg["max_nodes"])
        wid = spec["workflow_id"] or uuid.uuid4().hex[:16]
        admission._id(wid, "workflow id")
        spec["workflow_id"] = wid
        spec["created_at"] = spec.get("created_at") or _now()
        if queue_id:
            spec["queue_id"] = admission._id(queue_id, "queue id")
        spec["spec_hash"] = spec_hash(spec)
        folder = workflow_dir(root, wid)
        if folder.exists() and (_spec_path(folder).exists() or _state_path(folder).exists()):
            raise WorkflowError("workflow id already exists")
        _reject_writer_overlap(root, spec, skip=wid)
        if spec.get("queue_id"):
            item = admission._read(admission._queue_path(root, spec["queue_id"]), required=True)
            if item.get("status") != "pending":
                raise WorkflowError("workflow create binds a parked pending queue item")
        token = secrets.token_hex(16)
        owner_record = {**actor, "owner_token": token, "session_id": actor.get("session_id") or owner_session}
        state = _new_state(spec, owner=owner_record)
        folder.mkdir(parents=True, exist_ok=True)
        _events_dir(folder).mkdir(parents=True, exist_ok=True)
        admission._write(_spec_path(folder), spec)
        write_credentials(root, wid, {
            "workflow_id": wid, "owner_token": token, "owner": _public_owner(owner_record),
            "session_id": owner_record.get("session_id") or "",
        })
        if spec.get("queue_id"):
            state["queue_id"] = spec["queue_id"]
            _bind_queue(root, spec, state, "claimed")
        admission._write(_state_path(folder), state)
        append_event(root, wid, "created", {"spec_hash": spec["spec_hash"], "nodes": [n["id"] for n in spec["nodes"]]})
        public = public_record(spec, state)
        return {
            **public,
            "owner_token": token,
            "credentials_path": str(_credentials_path(folder)),
            "owner": _public_owner(owner_record),
        }


def extend_workflow(repo, workflow_id, added_nodes, *, owner_token="", owner=None, owner_session=""):
    root = _root(repo)
    with admission.transaction(root):
        cfg = require_adaptive_orchestration(root)
        match_workflow_credentials(root, workflow_id, owner_token)
        spec, state = load_pair(root, workflow_id, required=True)
        if state.get("status") in TERMINAL:
            raise WorkflowError("cannot extend a terminal workflow")
        if any((state.get("nodes") or {}).get(node["id"], {}).get("launched") and (node.get("final") or node.get("kind") == "final-verify")
               for node in spec["nodes"]):
            raise WorkflowError("no extension after final verify launches")
        launched = {
            node["id"]: copy.deepcopy(node)
            for node in spec["nodes"]
            if (state.get("nodes") or {}).get(node["id"], {}).get("launched")
        }
        previous_ids = {node["id"] for node in spec["nodes"]}
        extra = added_nodes if isinstance(added_nodes, list) else (added_nodes or {}).get("nodes")
        if not extra:
            raise WorkflowError("extension needs additional nodes")
        for item in extra:
            nid = str((item or {}).get("id") or "").strip() if isinstance(item, dict) else ""
            if nid in launched:
                raise WorkflowError("extension cannot alter launched nodes or contracts")
        merged_raw = {
            **{key: spec.get(key) for key in ("title", "case", "shared_context", "queue_id", "created_at")},
            "workflow_id": spec["workflow_id"],
            "nodes": spec["nodes"] + list(extra),
        }
        if isinstance(added_nodes, dict) and "shared_context" in added_nodes:
            merged_raw["shared_context"] = added_nodes.get("shared_context") or ""
        merged = normalize_spec(merged_raw, max_nodes=cfg["max_nodes"], workflow_id=workflow_id)
        by_id = {node["id"]: node for node in merged["nodes"]}
        for nid, original in launched.items():
            current = by_id.get(nid)
            if current is None or {k: current.get(k) for k in LAUNCHED_CONTRACT} != {
                k: original.get(k) for k in LAUNCHED_CONTRACT
            }:
                raise WorkflowError("extension cannot alter launched nodes or contracts")
        if state.get("shared_context_frozen") and merged.get("shared_context") != spec.get("shared_context"):
            raise WorkflowError("shared context is frozen after first launch")
        _reject_writer_overlap(root, merged, skip=workflow_id)
        previous = spec["spec_hash"]
        spec = save_spec(root, merged)
        for node in spec["nodes"]:
            state.setdefault("nodes", {}).setdefault(node["id"], _empty_node_state(node))
        for nid, row in list(state["nodes"].items()):
            if isinstance(row.get("approval"), dict) and row["approval"].get("spec_hash") != spec["spec_hash"]:
                row["approval"] = None
                row["blocker"] = "approval invalidated by spec change"
        state["spec_hash"] = spec["spec_hash"]
        save_state(root, state)
        append_event(root, workflow_id, "extended", {
            "previous_spec_hash": previous, "spec_hash": spec["spec_hash"],
            "added": [node["id"] for node in spec["nodes"] if node["id"] not in previous_ids],
        })
        return public_record(spec, state)


def _job_verification(root, job_id):
    if not job_id:
        return None
    try:
        return admission._read(root / ".rig" / "jobs" / job_id / "verification.json")
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        return None


def _job_acceptance(root, job_id, files):
    stored = _job_verification(root, job_id)
    if not stored or stored.get("acceptance") != "accepted":
        return False, ""
    try:
        import change_evidence as evidence
        current = evidence.snapshot(root, files or [])["snapshot_id"]
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        return False, ""
    if not stored.get("snapshot_id") or stored.get("snapshot_id") != current:
        return False, stored.get("snapshot_id") or ""
    return True, current


def _job_rejection_reason(root, job_id):
    stored = _job_verification(root, job_id)
    if not stored or stored.get("acceptance") != "rejected":
        return ""
    reason = str(stored.get("reason") or "").strip() or "parent_rejected"
    rationale = str(stored.get("rationale") or "").strip()
    if rationale and rationale not in reason:
        return f"{reason}: {rationale}"
    return reason


def _job_row(root, job_id):
    if not job_id:
        return None
    path = root / ".rig" / "jobs" / job_id / "meta.json"
    return admission._read(path)


_TERMINAL_EXECUTION = frozenset({"ok", "fail", "timeout", "cancelled"})


def _job_execution_status(root, meta):
    """Admission stop/outcome is authoritative over stale running metadata."""
    status = str((meta or {}).get("status") or "")
    rid = str((meta or {}).get("reservation_id") or "")
    reservation = admission.get_reservation(root, rid) if rid else None
    if reservation and reservation.get("job_id") == (meta or {}).get("job_id"):
        execution = reservation.get("execution_status")
        if reservation.get("stopped") and execution in _TERMINAL_EXECUTION:
            return execution, reservation
        process = reservation.get("process") or {}
        if status == "unconfirmed":
            return "unconfirmed", reservation
        if (not reservation.get("stopped") and process
                and admission._process_state(process) == "dead"):
            return "unconfirmed", reservation
        if not reservation.get("stopped") and reservation.get("needs_reconciliation") and status == "running":
            if process and admission._process_state(process) != "alive":
                return "unconfirmed", reservation
    if status == "unconfirmed":
        return "unconfirmed", reservation
    return status, reservation


def refresh_locked(root, spec, state):
    """Update node/workflow status from jobs, reservations, verification, queue."""
    asking = False
    running = 0
    live = 0
    for node in spec.get("nodes") or []:
        row = state.setdefault("nodes", {}).setdefault(node["id"], _empty_node_state(node))
        job_id = row.get("job_id") or ""
        meta = _job_row(root, job_id)
        if meta:
            if row.get("status") == "skipped":
                continue
            status, _reservation = _job_execution_status(root, meta)
            effective = status
            already_accepted = bool(row.get("accepted"))
            if status == "running":
                import ask as rig_ask
                if rig_ask.load_ask(root / ".rig" / "jobs" / job_id):
                    effective = "ask"
            if already_accepted:
                accepted, snap = _job_acceptance(root, job_id, node.get("files") or [])
                if accepted:
                    row["accepted"] = True
                    row["status"] = "accepted"
                    if snap:
                        row["acceptance_snapshot"] = snap
                    row["ran"] = True
                    if status == "running":
                        live += 1
                        running += 1
                    if meta.get("reservation_id"):
                        row["reservation_id"] = meta.get("reservation_id")
                    if meta.get("attempt_id"):
                        row["attempt_id"] = meta.get("attempt_id")
                    continue
                row["accepted"] = False
                row["acceptance_snapshot"] = ""
            if effective == "ask":
                row["status"] = "ask"
                asking = True
            elif status == "running":
                row["status"] = "running"
                row["ran"] = True
                running += 1
                live += 1
            elif status == "unconfirmed":
                row["status"] = "unconfirmed"
                row["ran"] = True
                row["blocker"] = str((_reservation or {}).get("reconciliation_reason") or "").strip() or (
                    "execution stop unconfirmed"
                )
            elif status == "ok":
                accepted, snap = _job_acceptance(root, job_id, node.get("files") or [])
                reject_reason = "" if accepted else _job_rejection_reason(root, job_id)
                row["accepted"] = accepted
                row["acceptance_snapshot"] = snap
                if accepted:
                    row["status"] = "accepted"
                elif reject_reason:
                    row["status"] = "failed"
                    row["blocker"] = reject_reason
                else:
                    row["status"] = "completed-unverified"
                row["ran"] = True
                row["ended_at"] = row.get("ended_at") or meta.get("ended_at") or _now()
            elif status in {"fail", "timeout"}:
                row["status"] = "failed"
                row["ran"] = True
                row["ended_at"] = row.get("ended_at") or meta.get("ended_at") or _now()
            elif status == "cancelled":
                row["status"] = "cancelled"
                row["ended_at"] = row.get("ended_at") or meta.get("ended_at") or _now()
            if meta.get("reservation_id"):
                row["reservation_id"] = meta.get("reservation_id")
            if meta.get("attempt_id"):
                row["attempt_id"] = meta.get("attempt_id")
        elif row.get("status") == "launching":
            live += 1
    metrics = state.setdefault("metrics", {})
    metrics["max_concurrency"] = max(int(metrics.get("max_concurrency") or 0), max(running, live))
    pending_coord = [item for item in (state.get("coordination") or []) if item.get("status") == "pending"]
    unresolved = state.get("failure") if isinstance(state.get("failure"), dict) and state["failure"].get("node_id") else None
    required = [node for node in spec.get("nodes") or [] if node.get("required", True)]
    final_nodes = [node for node in spec.get("nodes") or [] if node.get("final") or node.get("kind") == "final-verify"]
    all_required_accepted = bool(required) and all((state["nodes"].get(node["id"]) or {}).get("accepted") for node in required)
    finals_ok = (not final_nodes) or all((state["nodes"].get(node["id"]) or {}).get("accepted") for node in final_nodes)
    any_failed = any((state["nodes"].get(node["id"]) or {}).get("status") == "failed" for node in required)
    all_done = all((state["nodes"].get(node["id"]) or {}).get("status") in {
        "accepted", "completed-unverified", "skipped", "cancelled", "failed", "unconfirmed",
    } for node in spec.get("nodes") or [])
    live_busy = running > 0 or live > 0 or asking
    if any_failed and not (unresolved or {}).get("node_id"):
        failed_id = next(
            (node["id"] for node in required if (state["nodes"].get(node["id"]) or {}).get("status") == "failed"),
            "",
        )
        failed_row = state["nodes"].get(failed_id) or {}
        unresolved = {
            "node_id": failed_id,
            "reason": str(failed_row.get("blocker") or "").strip() or "node failed",
        }
        state["failure"] = unresolved

    if state.get("cancel_requested"):
        remaining = [
            node for node in spec.get("nodes") or []
            if (state["nodes"].get(node["id"]) or {}).get("status") in {"running", "ask", "launching"}
        ]
        state["status"] = "cancelled" if not remaining else "cancel-requested"
    elif (unresolved or {}).get("final"):
        state["status"] = "failed"
    elif unresolved or any_failed:
        state["status"] = "blocked"
    elif pending_coord:
        state["status"] = "blocked"
    elif asking or state.get("parent_action") or any(
            (state["nodes"].get(node["id"]) or {}).get("status") == "unconfirmed"
            for node in spec.get("nodes") or []):
        state["status"] = "attention"
    elif all_required_accepted and finals_ok and not live_busy:
        state["status"] = "verified"
        metrics["ended_at"] = metrics.get("ended_at") or _now()
        if state.get("queue_id"):
            _bind_queue(root, spec, state, "done")
    elif all_done and not live_busy:
        state["status"] = "completed-unverified"
    elif live_busy or any((state["nodes"].get(node["id"]) or {}).get("launched") for node in spec.get("nodes") or []):
        state["status"] = "running"
    else:
        state["status"] = "planned"
    return state


def refresh(repo, workflow_id):
    root = _root(repo)
    with admission.transaction(root):
        spec, state = load_pair(root, workflow_id, required=True)
        state = refresh_locked(root, spec, state)
        save_state(root, state)
        return public_record(spec, state)


def node_ready(spec, state, node):
    row = (state.get("nodes") or {}).get(node["id"]) or {}
    if row.get("status") in {
        "accepted", "skipped", "cancelled", "launching", "running", "ask",
        "completed-unverified", "failed", "unconfirmed",
    }:
        return False
    if row.get("launched") and row.get("ran"):
        return False
    if state.get("cancel_requested"):
        return False
    if (state.get("failure") or {}).get("node_id"):
        return False
    if any(item.get("status") == "pending" for item in (state.get("coordination") or [])):
        return False
    if not _deps_accepted(spec, state, node):
        return False
    if node.get("effects") in GATED_EFFECTS and not _approval_ok(spec, row):
        return False
    return row.get("status") in {"pending", "ready", "blocked"} and not row.get("ran")


def ready_nodes(spec, state):
    depths = _downstream_depth(spec["nodes"])
    ready = [node for node in spec["nodes"] if node_ready(spec, state, node)]
    ready.sort(key=lambda node: (
        -int(node.get("priority") or 0),
        -depths.get(node["id"], 0),
        [item["id"] for item in spec["nodes"]].index(node["id"]),
    ))
    return ready


def mark_launched(state, node_id, **fields):
    row = state.setdefault("nodes", {}).setdefault(node_id, _empty_node_state({"id": node_id}))
    row.update(launched=True, status="launching", **fields)
    if not state.get("shared_context_frozen"):
        state["shared_context_frozen"] = True
        state.setdefault("metrics", {})["started_at"] = state.get("metrics", {}).get("started_at") or _now()
    return row


def commit_launch(state, spec=None):
    """Freeze shared context and count a successful launch."""
    if spec and not state.get("shared_context"):
        state["shared_context"] = spec.get("shared_context") or ""
    if not state.get("shared_context_frozen"):
        state["shared_context_frozen"] = True
        state.setdefault("metrics", {})["started_at"] = state.get("metrics", {}).get("started_at") or _now()
    state.setdefault("metrics", {})
    state["metrics"]["launches"] = int(state.get("metrics", {}).get("launches") or 0) + 1
    return state
