#!/usr/bin/env python3
"""Readiness, admission, routing, and launch decisions for adaptive workflows."""
from __future__ import annotations

import copy
import inspect
import json

import admission
import harness
import jobs as rig_jobs
import route as rig_route
import workflow_state as wf

SchedulerError = wf.WorkflowError


def _repo_has_ask(root):
    listing = rig_jobs.list_jobs(root)
    return any(job.get("effective") == "ask" for job in listing)


def _accepted_overlap_reservations(spec, state, node):
    allowed = []
    deps = wf._transitive(spec["nodes"], node["id"])
    for dep in deps:
        row = (state.get("nodes") or {}).get(dep) or {}
        if row.get("accepted") and row.get("reservation_id"):
            allowed.append(row["reservation_id"])
    return allowed


def _node_access(node):
    return "write" if node.get("role") in wf.WRITE_ROLES else "read"


def _supported_kwargs(fn, extra):
    if not extra:
        return {}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return dict(extra)
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return dict(extra)
    return {key: value for key, value in extra.items() if key in params}


def _actual_writer_provider(root, job_id, row=None):
    """Known actual provider from accepted writer job/routing metadata. Never invent one."""
    meta = wf._job_row(root, job_id) or {}
    model = str(meta.get("model") or "").strip()
    inferred = bool(meta.get("model_inferred"))
    model_source = str(meta.get("model_source") or "")
    recorded = str(meta.get("provider") or "").strip().lower()
    recorded_source = str(meta.get("provider_source") or "")
    derived = ""
    if model and not inferred and model_source in {"selected", "observed"}:
        derived = rig_route.provider_for(model)
    if recorded in rig_route.PROVIDERS and recorded_source in {"explicit", "model", "selected", "observed"}:
        if derived and derived != recorded:
            return ""
        derived = derived or recorded
    if derived:
        return derived
    routing = row.get("routing") if isinstance(row, dict) and isinstance(row.get("routing"), dict) else None
    if routing is None:
        try:
            import routing_evidence
            sidecar = routing_evidence.read_sidecar(root / ".rig" / "jobs" / job_id)
            if sidecar and isinstance(sidecar.get("routing"), dict):
                routing = sidecar["routing"]
        except (OSError, ValueError, TypeError):
            routing = None
    profile = routing.get("selected_profile") if isinstance(routing, dict) else None
    if not isinstance(profile, dict):
        return ""
    profile_model = str(profile.get("model") or "").strip()
    profile_provider = str(profile.get("provider") or "").strip().lower()
    from_model = rig_route.provider_for(profile_model) if profile_model else ""
    if profile_provider in rig_route.PROVIDERS:
        if from_model and from_model != profile_provider:
            return ""
        return profile_provider
    return from_model


def _writer_handoff(root, spec, state, node):
    writers, snapshots, providers = [], [], []
    deps = wf._transitive(spec["nodes"], node["id"])
    for dep_node in spec.get("nodes") or []:
        if dep_node["id"] not in deps or dep_node.get("role") not in wf.WRITE_ROLES:
            continue
        dep_row = (state.get("nodes") or {}).get(dep_node["id"]) or {}
        job_id = dep_row.get("job_id") or ""
        if not job_id:
            continue
        writers.append(job_id)
        snap = dep_row.get("acceptance_snapshot") or ""
        if snap and snap not in snapshots:
            snapshots.append(snap)
        provider = _actual_writer_provider(root, job_id, dep_row)
        if provider and provider not in providers:
            providers.append(provider)
    return writers, snapshots, providers


def _review_handoff_fields(root, spec, state, node):
    writers, snapshots, providers = _writer_handoff(root, spec, state, node)
    fields = {
        "writer_job_id": writers[0] if writers else "",
        "writer_snapshot_id": snapshots[0] if snapshots else "",
    }
    if node.get("role") == "review" and writers:
        fields.update(
            writer_job_ids=list(writers),
            writer_snapshot_ids=list(snapshots),
            writer_providers=list(providers),
            review_mode="independent",
        )
    return fields, writers, snapshots, providers


def _pick_node(repo, node, spec, state, *, exclude=""):
    live = harness.live_parent()
    effective = harness.effective_workers(repo, live)
    assessment = node.get("assessment") if isinstance(node.get("assessment"), dict) else None
    extra, writers, snapshots, providers = _review_handoff_fields(repo, spec, state, node)
    if node.get("role") != "review" or not writers:
        extra = {}
    else:
        extra = {
            "writer_job_id": extra.get("writer_job_id") or (writers[0] if writers else ""),
            "writer_job_ids": writers,
            "writer_snapshot_ids": snapshots,
            "writer_providers": providers,
            "review_mode": "independent",
        }
    case = node.get("brief") or spec.get("case") or spec.get("title") or node["id"]
    return rig_route.pick(
        live, effective, node["role"], case, exclude=exclude, repo=repo, assessment=assessment, **extra,
    )


def _approval_blocker(spec, state, node):
    if node.get("effects") not in wf.GATED_EFFECTS:
        return ""
    row = (state.get("nodes") or {}).get(node["id"]) or {}
    approval = row.get("approval") or {}
    if approval.get("granted") and approval.get("spec_hash") == spec.get("spec_hash"):
        return ""
    return f"node {node['id']} requires approval for {node.get('effects')} effects"


def _conflict_reason(root, node, *, skip_reservation="", allow_read=None):
    try:
        _, files = admission.canonical_files(root, node.get("files") or [])
        resources = wf.canonical_resources(node.get("resources"))
        admission._capacity(
            root, admission._accounting(root, skip_reservation), "", _node_access(node), files,
            reserve_slot=True, resources=resources, allow_read_overlap_reservations=allow_read,
        )
    except admission.AdmissionError as error:
        return str(error)
    return ""


def _default_launch(repo, *, node, spec, state, choice, owner, owner_session, resources, allow_read):
    """Launch a wrapper or register a parent_writes action. Tests may replace this."""
    import worker_launch

    access = _node_access(node)
    files = list(node.get("files") or [])
    brief = node.get("brief") or spec.get("case") or spec.get("title") or node["id"]
    shared = state.get("shared_context") if state.get("shared_context_frozen") else spec.get("shared_context") or ""
    if shared:
        brief = brief + "\n\nShared context:\n" + shared
    identity = {
        "workflow_id": spec["workflow_id"],
        "workflow_node_id": node["id"],
        "workflow_spec_hash": spec.get("spec_hash") or "",
        "workflow_attempt": int(((state.get("nodes") or {}).get(node["id"]) or {}).get("workflow_attempt") or 0) + 1,
    }
    handoff, _writers, _snapshots, _providers = _review_handoff_fields(repo, spec, state, node)
    if _is_parent_choice(choice) or choice.get("spawn") == "native":
        details = rig_jobs.start_job(
            repo, worker="parent", role=node["role"], executor_kind="parent",
            model=choice.get("model") or "", effort=choice.get("effort") or "",
            summary=brief, files=files, access=access, owner_session=owner_session,
            routing=choice.get("routing"), assessment=node.get("assessment") or None,
            return_details=True,
            resources=resources, allow_read_overlap_reservations=allow_read, **identity,
            **_supported_kwargs(rig_jobs.start_job, handoff),
        )
        return {"kind": "parent_writes", "choice": choice, "job": details, **identity}
    if choice.get("spawn") != "run-worker" or not choice.get("worker"):
        raise SchedulerError(choice.get("reason") or "no eligible worker for workflow node")
    result = worker_launch.launch(
        repo, brief=brief, case=brief, role=node["role"], worker=choice.get("worker") or "",
        model=choice.get("model") or "", effort=choice.get("effort") or "",
        access=access, files=files, owner_session=owner_session, routing=choice.get("routing"),
        assessment=node.get("assessment") or None,
        resources=resources, allow_read_overlap_reservations=allow_read, **identity,
        **handoff,
    )
    return {"kind": "wrapper", "choice": choice, "job": result, **identity}


def _rollback_launch(state, node_id, **fields):
    row = state["nodes"][node_id]
    row.update(
        launched=False, status="pending", job_id="", reservation_id="", attempt_id="",
        started_at="", parent_action=None, **fields,
    )
    return row


def _fail_unlaunched(state, node_id, reason, **fields):
    row = _rollback_launch(state, node_id, blocker=reason, **fields)
    row["status"] = "failed"
    state["failure"] = {"node_id": node_id, "reason": reason}
    return row


def _never_started(message):
    text = str(message).lower()
    return any(token in text for token in ("never started", "not_started", "could not start", "no eligible"))


def _is_parent_choice(choice):
    """Parent executes this node: parent_writes, or stay when no wrapper exists."""
    choice = choice or {}
    spawn = str(choice.get("spawn") or "")
    routing = choice.get("routing") if isinstance(choice.get("routing"), dict) else {}
    strategy = str(routing.get("execution_strategy") or choice.get("execution_strategy") or "")
    return bool(choice.get("parent_writes") or spawn == "stay" or strategy == "stay")


def advance(repo, workflow_id, *, owner=None, owner_session="", owner_token="",
            launch_fn=None, pick_fn=None):
    root = admission._root(repo)
    launcher = launch_fn or _default_launch
    picker = pick_fn or (lambda node, spec, state, exclude="": _pick_node(root, node, spec, state, exclude=exclude))
    launched, skipped = [], []
    parent_action = None
    generation_bumped = False

    while True:
        claim = None
        with admission.transaction(root):
            wf.require_adaptive_orchestration(root)
            if owner_token:
                wf.match_workflow_credentials(root, workflow_id, owner_token)
            spec, state = wf.load_pair(root, workflow_id, required=True)
            state = wf.refresh_locked(root, spec, state)
            if not generation_bumped:
                state["advance_generation"] = int(state.get("advance_generation") or 0) + 1
                generation_bumped = True
            blocked = None
            if state.get("status") in wf.TERMINAL:
                blocked = "terminal"
            elif state.get("cancel_requested"):
                blocked = "cancel-requested"
            elif (state.get("failure") or {}).get("node_id"):
                blocked = "unresolved failure"
            elif any(item.get("status") == "pending" for item in (state.get("coordination") or [])):
                state["status"] = "blocked"
                blocked = "coordination"
            elif _repo_has_ask(root):
                state["status"] = "attention"
                blocked = "repo ASK"
            if blocked is not None:
                wf.save_state(root, state)
                result = wf.public_record(spec, state)
                result.update(launched=launched, skipped=skipped, partial=bool(launched) and bool(skipped),
                              parent_action=parent_action or result.get("parent_action"), reason=blocked)
                return result
            if parent_action:
                wf.save_state(root, state)
                break
            for node in wf.ready_nodes(spec, state):
                row = state["nodes"][node["id"]]
                if row.get("status") == "launching" or (row.get("launched") and row.get("job_id")):
                    skipped.append({"node_id": node["id"], "reason": "already launched"})
                    continue
                if row.get("ran"):
                    skipped.append({"node_id": node["id"], "reason": "ran child is never silently replaced"})
                    continue
                blocker = _approval_blocker(spec, state, node)
                if blocker:
                    row["blocker"] = blocker
                    skipped.append({"node_id": node["id"], "reason": blocker})
                    continue
                allow_read = _accepted_overlap_reservations(spec, state, node)
                conflict = _conflict_reason(root, node, allow_read=allow_read)
                if conflict:
                    row["blocker"] = conflict
                    skipped.append({"node_id": node["id"], "reason": conflict})
                    continue
                exclude = ",".join(row.get("exclude") or [])
                wf.mark_launched(state, node["id"])
                if state.get("queue_id"):
                    wf._bind_queue(root, spec, state, "spawned")
                wf.save_state(root, state)
                claim = {
                    "node": copy.deepcopy(node),
                    "allow_read": list(allow_read),
                    "exclude": exclude,
                    "spec": copy.deepcopy(spec),
                    "state": copy.deepcopy(state),
                }
                break
            else:
                wf.save_state(root, state)
        if claim is None:
            break

        node = claim["node"]
        try:
            choice = picker(node, claim["spec"], claim["state"], claim["exclude"])
        except (ValueError, TypeError) as error:
            message = str(error)
            with admission.transaction(root):
                spec, state = wf.load_pair(root, workflow_id, required=True)
                row = state["nodes"][node["id"]]
                if row.get("status") == "launching" and not row.get("job_id"):
                    _fail_unlaunched(state, node["id"], message)
                    wf.save_state(root, state)
                skipped.append({"node_id": node["id"], "reason": message})
            continue

        is_parent = _is_parent_choice(choice)
        if is_parent and launched:
            with admission.transaction(root):
                spec, state = wf.load_pair(root, workflow_id, required=True)
                row = state["nodes"][node["id"]]
                if row.get("status") == "launching" and not row.get("job_id"):
                    _rollback_launch(state, node["id"], blocker="parent_writes occupies this turn")
                    wf.save_state(root, state)
            skipped.append({"node_id": node["id"], "reason": "parent_writes occupies this turn"})
            break

        claim["choice"] = copy.deepcopy(choice)
        claim["parent_writes"] = is_parent
        with admission.transaction(root):
            spec, state = wf.load_pair(root, workflow_id, required=True)
            row = state["nodes"][node["id"]]
            if row.get("status") == "launching":
                row["routing"] = copy.deepcopy(choice.get("routing"))
                wf.save_state(root, state)
            claim["spec"] = copy.deepcopy(spec)
            claim["state"] = copy.deepcopy(state)

        errors = (admission.AdmissionError, SchedulerError, OSError, TypeError, ValueError, SystemExit)
        if not is_parent:
            errors = Exception
        try:
            result = launcher(
                root, node=node, spec=claim["spec"], state=claim["state"], choice=choice, owner=owner,
                owner_session=owner_session, resources=node.get("resources") or [],
                allow_read=claim["allow_read"],
            )
        except errors as error:
            message = str(error)
            with admission.transaction(root):
                spec, state = wf.load_pair(root, workflow_id, required=True)
                row = state["nodes"][node["id"]]
                if row.get("status") == "launching" and not row.get("job_id"):
                    dead = str((choice or {}).get("worker") or "")
                    if (_never_started(message) and not row.get("ran")
                            and len(row.get("exclude") or []) < 1):
                        if dead and dead not in (row.get("exclude") or []):
                            row.setdefault("exclude", []).append(dead)
                        _rollback_launch(state, node["id"],
                                         blocker="spawn never started; one exclude/repick")
                        skipped.append({"node_id": node["id"], "reason": "spawn never started",
                                        "exclude": dead})
                    else:
                        _fail_unlaunched(state, node["id"], message)
                        skipped.append({"node_id": node["id"], "reason": message})
                    wf.save_state(root, state)
                else:
                    skipped.append({"node_id": node["id"], "reason": message})
            if is_parent:
                break
            continue

        with admission.transaction(root):
            spec, state = wf.load_pair(root, workflow_id, required=True)
            row = state["nodes"][node["id"]]
            job = result.get("job") or {}
            row.update(
                job_id=job.get("job_id") or "",
                reservation_id=job.get("reservation_id") or "",
                attempt_id=job.get("attempt_id") or "",
                workflow_attempt=result.get("workflow_attempt") or int(row.get("workflow_attempt") or 0) + 1,
                routing=copy.deepcopy(choice.get("routing")),
                started_at=wf._now(),
                launched=True,
                ran=False,
            )
            cancelled = bool(state.get("cancel_requested") or row.get("status") == "cancelled")
            if cancelled:
                job_id = job.get("job_id") or ""
                # A live job must remain visible so the workflow stays cancel-requested
                # until confirmed stop. Do not claim termination here.
                row["status"] = "running" if job_id else "cancelled"
                if job_id:
                    try:
                        rig_jobs.cancel_job(root, job_id, "workflow cancelled", return_details=True)
                    except (SystemExit, OSError, ValueError):
                        pass
                wf.commit_launch(state, spec)
                wf.save_state(root, state)
                skipped.append({"node_id": node["id"], "reason": "cancelled during launch"})
                continue
            row["status"] = "running"
            if is_parent:
                row["parent_action"] = result
                parent_action = {
                    "kind": "parent_writes",
                    "node_id": node["id"],
                    "job_id": job.get("job_id") or "",
                    "reservation_id": job.get("reservation_id") or "",
                    "attempt_id": job.get("attempt_id") or "",
                    "credentials_path": job.get("credentials_path") or "",
                    "role": node["role"],
                    "files": node.get("files") or [],
                }
                state["parent_action"] = parent_action
            wf.commit_launch(state, spec)
            if state.get("queue_id"):
                wf._bind_queue(root, spec, state, "spawned")
            wf.save_state(root, state)
            payload = {
                "node_id": node["id"], "job_id": job.get("job_id") or "",
                "worker": choice.get("worker") or "", "routing": choice.get("routing"),
            }
            if is_parent:
                payload["parent_writes"] = True
                wf.append_event(root, workflow_id, "advanced", payload)
                launched.append({"node_id": node["id"], "job_id": job.get("job_id") or "",
                                 "parent_writes": True})
                break
            wf.append_event(root, workflow_id, "launched", payload)
            launched.append({"node_id": node["id"], "job_id": job.get("job_id") or "",
                             "worker": choice.get("worker")})

    with admission.transaction(root):
        spec, state = wf.load_pair(root, workflow_id, required=True)
        state = wf.refresh_locked(root, spec, state)
        wf.save_state(root, state)
        result = wf.public_record(spec, state)
        result.update(launched=launched, skipped=skipped, partial=bool(launched) and bool(skipped),
                      parent_action=parent_action or result.get("parent_action"))
        return result


def approve_node(repo, workflow_id, node_id, *, owner_token="", owner=None, owner_session="", rationale=""):
    if not isinstance(rationale, str) or not rationale.strip():
        raise SchedulerError("approval rationale required")
    root = admission._root(repo)
    with admission.transaction(root):
        wf.require_adaptive_orchestration(root)
        creds = wf.match_workflow_credentials(root, workflow_id, owner_token)
        spec, state = wf.load_pair(root, workflow_id, required=True)
        node = next((item for item in spec["nodes"] if item["id"] == node_id), None)
        if node is None:
            raise SchedulerError("unknown workflow node")
        if node.get("effects") not in wf.GATED_EFFECTS:
            raise SchedulerError("approval is only required for external|production|destructive effects")
        session = owner_session or (creds.get("session_id") or "")
        actor = admission._owner(owner, session)
        if creds.get("session_id") and actor.get("session_id") and creds.get("session_id") != actor.get("session_id"):
            raise SchedulerError("approval must be bound to the owner session")
        row = state["nodes"][node_id]
        row["approval"] = {
            "granted": True,
            "spec_hash": spec.get("spec_hash"),
            "node_id": node_id,
            "session_id": session,
            "rationale": rationale.strip(),
            "at": wf._now(),
        }
        row["blocker"] = ""
        wf.save_state(root, state)
        wf.append_event(root, workflow_id, "approved", {"node_id": node_id, "spec_hash": spec.get("spec_hash")})
        return wf.public_record(spec, state)


def resolve_node(repo, workflow_id, node_id, *, action="retry", rationale="", owner_token="", owner=None, owner_session=""):
    if action not in {"retry", "skip", "fail"}:
        raise SchedulerError("resolve action must be retry|skip|fail")
    if action == "skip" and (not isinstance(rationale, str) or not rationale.strip()):
        raise SchedulerError("skip requires a rationale")
    root = admission._root(repo)
    with admission.transaction(root):
        wf.require_adaptive_orchestration(root)
        wf.match_workflow_credentials(root, workflow_id, owner_token)
        spec, state = wf.load_pair(root, workflow_id, required=True)
        node = next((item for item in spec["nodes"] if item["id"] == node_id), None)
        if node is None:
            raise SchedulerError("unknown workflow node")
        row = state["nodes"][node_id]
        if row.get("accepted"):
            raise SchedulerError("accepted nodes cannot retry")
        if action == "skip" and node.get("required", True):
            raise SchedulerError("required nodes cannot be silently waived")
        failure = state.get("failure") or {}
        if failure.get("node_id") not in {None, "", node_id} and action != "fail":
            raise SchedulerError("resolve the failed node first")
        if action == "retry":
            if row.get("ran") and row.get("status") not in {"failed", "cancelled"}:
                raise SchedulerError("identical retry requires a stopped/released attempt")
            rid = row.get("reservation_id") or ""
            reservation = admission.get_reservation(root, rid) if rid else None
            if reservation and reservation.get("stage") not in {None, "released"} and not reservation.get("stopped"):
                raise SchedulerError("identical retry requires a stopped/released attempt")
            if row.get("job_id"):
                meta = wf._job_row(root, row["job_id"])
                if meta and str(meta.get("status") or "") in {"running", "ask"}:
                    raise SchedulerError("identical retry requires a stopped/released attempt")
            row.update(
                status="pending", launched=False, ran=False, job_id="", reservation_id="", attempt_id="",
                blocker="", skip_rationale="", parent_action=None,
            )
            state["failure"] = None
        elif action == "skip":
            row.update(status="skipped", skip_rationale=rationale.strip(), blocker="")
            state["failure"] = None
        else:
            row.update(status="failed")
            state["failure"] = {"node_id": node_id, "reason": rationale.strip() or "final failure", "final": True}
            state["status"] = "failed"
        wf.save_state(root, state)
        wf.append_event(root, workflow_id, "resolved", {"node_id": node_id, "action": action, "rationale": rationale})
        state = wf.refresh_locked(root, spec, state)
        wf.save_state(root, state)
        return wf.public_record(spec, state)


def cancel_workflow(repo, workflow_id, *, owner_token="", owner=None, owner_session="", rationale="parent"):
    root = admission._root(repo)
    with admission.transaction(root):
        wf.match_workflow_credentials(root, workflow_id, owner_token)
        spec, state = wf.load_pair(root, workflow_id, required=True)
        state["cancel_requested"] = True
        state["status"] = "cancel-requested"
        cancelled_jobs = []
        for node in spec["nodes"]:
            row = state["nodes"][node["id"]]
            if not row.get("job_id") and row.get("status") in {"pending", "ready", "blocked", "launching"}:
                row["status"] = "cancelled"
            job_id = row.get("job_id")
            if job_id and row.get("status") in {"running", "ask", "launching"}:
                try:
                    cancelled_jobs.append(rig_jobs.cancel_job(root, job_id, rationale or "parent", return_details=True))
                except (SystemExit, OSError, ValueError):
                    cancelled_jobs.append({"job_id": job_id, "state": "error"})
        if state.get("queue_id"):
            wf._bind_queue(root, spec, state, "cancelled")
        wf.save_state(root, state)
        wf.append_event(root, workflow_id, "cancelled", {"rationale": rationale})
        state = wf.refresh_locked(root, spec, state)
        wf.save_state(root, state)
        result = wf.public_record(spec, state)
        result["cancelled_jobs"] = [
            {key: value for key, value in item.items() if key != "owner_token"} if isinstance(item, dict) else item
            for item in cancelled_jobs
        ]
        return result


def wait_workflow(repo, workflow_id, timeout=None, *, on_tick=None, cancel_event=None):
    import time
    import threading
    import coordination

    timeout_s = None if timeout is None else max(0.0, float(timeout))
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    stop = cancel_event if cancel_event is not None else threading.Event()
    last = None
    while True:
        if stop.is_set():
            return 130, "CANCELLED WAIT: observer stopped; execution stop confirmation is separate. Do not re-wait."
        public = wf.refresh(repo, workflow_id)
        last = public
        siblings = []
        for node in public.get("nodes") or []:
            row = (public.get("node_state") or {}).get(node["id"]) or {}
            siblings.append({
                "node_id": node["id"], "role": node.get("role"), "status": row.get("status"),
                "job_id": row.get("job_id") or "", "accepted": bool(row.get("accepted")),
            })
        pending = [item for item in (public.get("coordination") or []) if item.get("status") == "pending"]
        if pending:
            text = "COORDINATION\n" + json.dumps({"workflow": public["workflow_id"], "pending": pending, "siblings": siblings}, indent=2)
            return 2, text
        asking = [row for row in siblings if row.get("status") == "ask"]
        if asking:
            job_id = asking[0].get("job_id")
            if job_id:
                code, text = rig_jobs.wait_job(repo, job_id, 0)
                return 2, "ASK\n" + text + "\n" + json.dumps({"siblings": siblings}, indent=2)
            return 2, "ASK\n" + json.dumps({"siblings": siblings}, indent=2)
        live_ids = [row.get("job_id") for row in siblings if row.get("status") in {"running", "launching"} and row.get("job_id")]
        if public.get("status") in wf.TERMINAL:
            return (0 if public.get("status") == "verified" else 130 if public.get("status") == "cancelled" else 1), json.dumps(
                {"workflow": public, "siblings": siblings}, indent=2, default=str,
            )
        if public.get("status") in {"blocked", "attention", "completed-unverified"}:
            return 0, json.dumps({"workflow": public, "siblings": siblings}, indent=2, default=str)
        if not live_ids:
            return 0, json.dumps({"workflow": public, "siblings": siblings}, indent=2, default=str)
        if deadline is not None and time.monotonic() >= deadline:
            return 124, json.dumps({"workflow": public, "siblings": siblings}, indent=2, default=str)
        if on_tick:
            on_tick(public)
        remaining = 0.4 if deadline is None else min(0.4, max(0, deadline - time.monotonic()))
        stop.wait(remaining)


def report(repo, workflow_id):
    spec, state = wf.load_pair(repo, workflow_id, required=True)
    state = wf.refresh(repo, workflow_id)
    spec, current = wf.load_pair(repo, workflow_id, required=True)
    metrics = current.get("metrics") or {}
    node_times = []
    outcomes = []
    for node in spec.get("nodes") or []:
        row = (current.get("nodes") or {}).get(node["id"]) or {}
        started, ended = row.get("started_at") or "", row.get("ended_at") or ""
        elapsed = None
        if started:
            elapsed = rig_jobs.elapsed_seconds(started, ended, live=row.get("status") in {"running", "ask"})
        node_times.append({"node_id": node["id"], "elapsed_s": elapsed, "status": row.get("status")})
        outcomes.append({
            "node_id": node["id"], "role": node.get("role"), "status": row.get("status"),
            "accepted": bool(row.get("accepted")), "job_id": row.get("job_id") or "",
        })
    wall = rig_jobs.elapsed_seconds(metrics.get("started_at") or current.get("created_at") or "",
                                    metrics.get("ended_at") or "", live=current.get("status") in wf.ACTIVE)
    return {
        "workflow_id": workflow_id,
        "status": current.get("status"),
        "spec_hash": spec.get("spec_hash"),
        "wall_time_s": wall,
        "node_times": node_times,
        "max_concurrency": int(metrics.get("max_concurrency") or 0),
        "outcomes": outcomes,
        "accepted": wf.summary_counts(spec, current)["accepted"],
        "required": wf.summary_counts(spec, current)["required"],
        "events": wf.list_events(repo, workflow_id),
    }
