"""Job, launch, verification, and coordination MCP handlers.

Facade names are resolved through the call context. Do not import rig_mcp.
"""
from __future__ import annotations

import json
from pathlib import Path

def dispatch(ctx, state):
    """Handle this module's tools. Bindings are read from the facade on each call."""
    name = state.name
    args = state.args
    repo = state.repo
    on_tick = state.on_tick
    wait_paths = state.wait_paths
    cancel_event = state.cancel_event
    wait_targets = state.wait_targets
    _ok = ctx._ok
    _err = ctx._err
    _optional_string = ctx._optional_string
    _ownership_args = ctx._ownership_args
    _job_ownership_args = ctx._job_ownership_args
    _workflow_owner_args = ctx._workflow_owner_args
    _workflow_response = ctx._workflow_response
    _execution_args = ctx._execution_args
    _domain_args = ctx._domain_args
    _child_ask = ctx._child_ask
    child_job_dir = ctx.child_job_dir
    child_job_id = ctx.child_job_id
    rig_jobs = ctx.rig_jobs
    rig_inbox = ctx.rig_inbox
    rig_harness = ctx.rig_harness
    rig_verification = ctx.rig_verification
    rig_coordination = ctx.rig_coordination
    rig_launch = ctx.rig_launch
    JOB_FINISH_STATUSES = ctx.JOB_FINISH_STATUSES
    JOB_RECORD_STATUSES = ctx.JOB_RECORD_STATUSES
    LAUNCH_ARG_NAMES = ctx.LAUNCH_ARG_NAMES
    _LAUNCH_PUBLIC_KEYS = ctx._LAUNCH_PUBLIC_KEYS
    rig_child_mcp = ctx.rig_child_mcp
    if name == "rig_job_doing":
        text = str(args.get("text") or "").strip()
        if not text:
            return _err("rig_job_doing needs text")
        return _ok(rig_jobs.set_doing(child_job_dir(repo), text))
    if name == "rig_job_note":
        text = str(args.get("text") or "").strip()
        if not text:
            return _err("rig_job_note needs text")
        return _ok(rig_jobs.add_note(child_job_dir(repo), text))
    if name == "permission_prompt":
        return _child_ask(child_job_dir(repo), args, permission=True)
    if name == "rig_job_ask":
        preview = str(args.get("preview") or args.get("text") or "").strip()
        if not preview and not args.get("tool_name") and not args.get("input"):
            return _err("rig_job_ask needs preview or text")
        return _child_ask(child_job_dir(repo), args, permission=False)
    if name == "rig_job_inbox":
        rig_child_mcp.record_handshake(child_job_dir(repo))
        obj = rig_inbox.consume_inbox(child_job_dir(repo))
        if not obj:
            return _ok("(empty)")
        return _ok(str(obj.get("text") or ""))
    if name == "rig_job_message":
        text = str(args.get("text") or "").strip()
        if not text:
            return _err("rig_job_message needs text")
        job = rig_jobs.resolve_job(repo, args.get("id"))
        obj = rig_inbox.write_inbox(Path(job["dir"]), text)
        return _ok(f"message {job['job_id']} inbox pending\n{obj['text']}")

    if name == "rig_jobs":
        want_thread = str(args.get("thread") or "").strip() or None
        listing = rig_jobs.list_jobs(repo, thread=want_thread)
        want = str(args.get("status") or "").strip()
        if want:
            listing = [j for j in listing if j["effective"] == want or j["status"] == want]
        return _ok(rig_jobs.format_table(listing, repo))
    if name == "rig_job_show":
        job = rig_jobs.resolve_job(repo, args.get("id"))
        return _ok(rig_jobs.format_show(job))
    if name == "rig_job_log":
        job = rig_jobs.resolve_job(repo, args.get("id"))
        n = args.get("lines") or 40
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 40
        return _ok(rig_jobs.format_log(job, n))
    if name == "rig_job_wait":
        timeout = args.get("timeout")
        if timeout is None:
            timeout_s = None
        else:
            try:
                timeout_s = float(timeout)
            except (TypeError, ValueError):
                timeout_s = None
        code, text = rig_jobs.wait_job(
            repo,
            args.get("id"),
            timeout_s,
            on_tick=on_tick,
            resolved_paths=wait_paths,
            ids=args.get("ids"), cancel_event=cancel_event, targets=wait_targets,
        )
        if code == 1:
            return _err(text)
        return _ok(text)
    if name == "rig_job_allow":
        job = rig_jobs.resolve_job(repo, args.get("id"))
        text = rig_jobs.answer_pending(job, "allow")
        return _ok(text) if text.startswith("allow") else _err(text)
    if name == "rig_job_deny":
        job = rig_jobs.resolve_job(repo, args.get("id"))
        text = rig_jobs.answer_pending(job, "deny", str(args.get("reason") or ""))
        return _ok(text) if text.startswith("deny") else _err(text)
    if name == "rig_job_cancel":
        reason = str(args.get("reason") or "parent").strip() or "parent"
        names = rig_jobs._normalize_wait_ids(args.get("id"), args.get("ids")) or [None]
        results = []
        failed = False
        for jid in names:
            try:
                results.append(rig_jobs.cancel_job(repo, jid, reason, return_details=True))
            except (SystemExit, Exception) as exc:
                failed = True
                results.append({"job_id": jid, "state": "error", "text": str(exc) or "rig error"})
        result = _ok("\n".join(item["text"] for item in results))
        if failed:
            result["isError"] = True
        result["structuredContent"] = {"jobs": results}
        return result

    if name == "rig_job_break_glass_close":
        job_id = _optional_string(args, "id").strip()
        if not job_id:
            return _err("rig_job_break_glass_close needs id")
        if args.get("confirmed_stopped") is not True:
            return _err("break-glass close requires confirmed_stopped=true")
        for banned in ("owner_token", "reservation_id", "attempt_id"):
            if args.get(banned) not in (None, ""):
                return _err("break-glass close does not accept raw owner tokens")
        result = rig_jobs.break_glass_close_job(
            repo, job_id,
            credentials_path=_optional_string(args, "credentials_path"),
            confirmed_stopped=True,
            rationale=_optional_string(args, "rationale"),
            owner_session=_optional_string(args, "owner_session"),
        )
        dumped = json.dumps(result, indent=2)
        if "owner_token" in dumped:
            dumped = json.dumps({key: value for key, value in result.items() if key != "owner_token"}, indent=2)
        return {**_ok(dumped), "structuredContent": result}
    if name == "rig_job_close":
        result = rig_jobs.close_job(repo, _optional_string(args, "id"),
                                    rationale=_optional_string(args, "rationale"), **_job_ownership_args(args, repo))
        return _ok(json.dumps(result, indent=2))
    if name == "rig_job_reconcile":
        if not isinstance(args.get("apply", False), bool):
            raise ValueError("apply must be a boolean")
        result = rig_jobs.reconcile_jobs(
            repo, _optional_string(args, "id"), queue_id=_optional_string(args, "queue_id"),
            apply=args.get("apply", False), action=args.get("action", "report"),
            worker=_optional_string(args, "worker"), **_job_ownership_args(args, repo),
            access=args.get("access", "write"), files=args.get("files"),
            rationale=_optional_string(args, "rationale"), completion=args.get("completion"),
        )
        return _ok(json.dumps(result, indent=2))
    if name == "rig_job_recover_cancelled":
        if not isinstance(args.get("apply", False), bool):
            raise ValueError("apply must be a boolean")
        job_id = _optional_string(args, "id").strip()
        if not job_id:
            return _err("rig_job_recover_cancelled needs id")
        result = rig_jobs.recover_cancelled_job(
            repo, job_id, rationale=_optional_string(args, "rationale"),
            apply=args.get("apply", False),
            **_ownership_args(args, repo, job_id=job_id, restore_owner_session=False),
        )
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}
    if name == "rig_job_recover_parent_write":
        job_id = _optional_string(args, "id").strip()
        if not job_id:
            return _err("rig_job_recover_parent_write needs id")
        if args.get("confirmed_stopped") is not True:
            return _err(
                "parent write recovery requires confirmed_stopped=true; stop/cancel first. "
                "Allow/approve is not completion"
            )
        result = rig_jobs.recover_parent_write_job(
            repo, job_id, rationale=_optional_string(args, "rationale"),
            confirmed_stopped=True, owner_session=_optional_string(args, "owner_session"),
        )
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}
    if name == "rig_job_recover_wrapper_receipt":
        job_id = _optional_string(args, "id").strip()
        if not job_id:
            return _err("rig_job_recover_wrapper_receipt needs id")
        result = rig_jobs.recover_wrapper_receipt_job(repo, job_id)
        if "owner_token" in result:
            result = {key: value for key, value in result.items() if key != "owner_token"}
        return {**_ok(json.dumps(result, indent=2, default=str)), "structuredContent": result}
    if name == "rig_job_ui_evidence":
        import ui_evidence
        job_id = _optional_string(args, "id").strip()
        if not job_id:
            return _err("rig_job_ui_evidence needs id")
        job_dir = Path(rig_jobs.resolve_job(repo, job_id)["dir"])
        result = ui_evidence.record_pack(repo, job_dir, args.get("pack"), **_job_ownership_args(args, repo))
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}
    if name in {"rig_job_requirements", "rig_job_check", "rig_job_criterion", "rig_job_accept"}:
        job_id = _optional_string(args, "id").strip()
        if not job_id:
            return _err(f"{name} needs id")
        job_dir = Path(rig_jobs.resolve_job(repo, job_id)["dir"])
        if name == "rig_job_requirements":
            result = rig_verification.record_requirements(
                repo, job_dir, args.get("requirements", []), args.get("manual_criteria", []),
                **_job_ownership_args(args, repo),
            )
        elif name == "rig_job_criterion":
            result = rig_verification.record_criterion(
                repo, job_dir, _optional_string(args, "criterion_id"),
                _optional_string(args, "contract_fingerprint"), _optional_string(args, "snapshot_id"),
                _optional_string(args, "result"), _optional_string(args, "rationale"), args.get("evidence_refs"),
                **_job_ownership_args(args, repo),
            )
        elif name == "rig_job_check":
            result = rig_verification.run_check(
                repo, job_dir, _optional_string(args, "name"), args.get("argv"),
                cwd=args.get("cwd"), on_tick=on_tick,
                **_job_ownership_args(args, repo),
            )
        else:
            result = rig_verification.accept(
                repo, job_dir, _optional_string(args, "decision"),
                _optional_string(args, "snapshot_id"), check_ids=args.get("check_ids", []),
                rationale=_optional_string(args, "rationale"), next=args.get("next", "complete"),
                **_job_ownership_args(args, repo),
            )
        return _ok(json.dumps(result, indent=2))
    if name in {"rig_job_start", "rig_job_finish", "rig_job_record"}:
        live = rig_harness.live_parent()
        preferred = rig_harness.preferred_parent(repo)
        worker = str(args.get("worker") or "")
        role = str(args.get("role") or "")
        summary = str(args.get("summary") or "")
        job_id = str(args.get("id") or "")
        if name == "rig_job_start":
            result = rig_jobs.start_job(
                    repo,
                    worker=worker,
                    role=role or "worker",
                    job_id=job_id,
                    summary=summary,
                    live=live,
                    preferred=preferred,
                    **_execution_args(args),
                    files=args.get("files"), acceptance_contract=args.get("acceptance_contract"),
                    context_package=args.get("context_package"),
                    writer_job_id=_optional_string(args, "writer_job_id"),
                    continues_job_id=_optional_string(args, "continues_job_id"),
                    writer_snapshot_id=_optional_string(args, "writer_snapshot_id"),
                    access=_optional_string(args, "access"), queue_id=_optional_string(args, "queue_id"),
                    native_agent_id=_optional_string(args, "native_agent_id"), return_details=True,
                    routing=args.get("routing"), assessment=args.get("assessment"), **_domain_args(args),
                    **_job_ownership_args(args, repo),
            )
            return {**_ok(result["job_id"]), "structuredContent": result}
        status = str(args.get("status") or "ok")
        if name == "rig_job_finish":
            if status not in JOB_FINISH_STATUSES:
                return _err("status must be ok|fail|timeout|cancelled")
            usage = args.get("token_usage")
            if usage not in (None, "") and not isinstance(usage, dict):
                return _err("token_usage must be an object")
            result = rig_jobs.finish_job(
                    repo,
                    job_id,
                    status=status,
                    summary=summary,
                    worker=worker,
                    role=role,
                    live=live,
                    preferred=preferred,
                    completion=args.get("completion"), return_details=True,
                    token_usage=usage if usage not in (None, "") else None,
                    **_job_ownership_args(args, repo),
            )
            return {**_ok(result["text"]), "structuredContent": result}
        if status not in JOB_RECORD_STATUSES:
            return _err("record status must be ok|fail|timeout")
        usage = args.get("token_usage")
        if usage not in (None, "") and not isinstance(usage, dict):
            return _err("token_usage must be an object")
        return _ok(
            rig_jobs.record_job(
                repo,
                worker=worker,
                role=role or "worker",
                status=status,
                summary=summary,
                job_id=job_id,
                live=live,
                preferred=preferred,
                token_usage=usage if usage not in (None, "") else None,
                **_execution_args(args),
            )
        )

    if name == "rig_job_coordination_reply":
        wid = _optional_string(args, "id").strip()
        request_id = _optional_string(args, "request_id").strip()
        if not wid or not request_id:
            return _err("rig_job_coordination_reply needs id and request_id")
        decision = args.get("decision") or "reply"
        if decision not in {"reply", "stop"}:
            return _err("decision must be reply|stop")
        return _workflow_response(rig_coordination.reply(
            repo, wid, request_id, decision=decision,
            text=_optional_string(args, "text"),
            **_workflow_owner_args(args),
        ))
    if name == "rig_job_coordination_request":
        kind = _optional_string(args, "kind").strip()
        text = _optional_string(args, "text").strip()
        if kind not in rig_coordination.KINDS:
            return _err("kind must be dependency|contract|scope")
        if not text:
            return _err("rig_job_coordination_request needs text")
        payload = args.get("payload")
        if payload is None:
            payload = {}
        elif not isinstance(payload, dict):
            return _err("payload must be an object")
        result = rig_coordination.request(repo, child_job_id(), kind, text, payload)
        return _workflow_response(result)

    if name == "rig_job_launch":
        extra = sorted(set(args) - LAUNCH_ARG_NAMES)
        if extra:
            return _err(f"unknown launch argument: {extra[0]}")
        files = args.get("files")
        if files is not None and not isinstance(files, list):
            return _err("files must be a JSON array of nonempty paths")
        review_mode = args.get("review_mode", "standalone")
        if review_mode is not None and not isinstance(review_mode, str):
            return _err("review_mode must be a string")
        try:
            result = rig_launch.launch(
                repo,
                id=_optional_string(args, "id"),
                case=_optional_string(args, "case"),
                role=_optional_string(args, "role"),
                worker=_optional_string(args, "worker"),
                model=_optional_string(args, "model"),
                effort=_optional_string(args, "effort"),
                access=_optional_string(args, "access"),
                files=files, acceptance_contract=args.get("acceptance_contract"),
                brief=_optional_string(args, "brief"), context_package=args.get("context_package"),
                queue_id=_optional_string(args, "queue_id"),
                writer_job_id=_optional_string(args, "writer_job_id"),
                continues_job_id=_optional_string(args, "continues_job_id"),
                writer_snapshot_id=_optional_string(args, "writer_snapshot_id"),
                writer_cli=_optional_string(args, "writer_cli"),
                writer_model=_optional_string(args, "writer_model"),
                writer_provider=_optional_string(args, "writer_provider"),
                review_mode=review_mode if review_mode is not None else "standalone",
                routing=args.get("routing"),
                assessment=args.get("assessment"), **_domain_args(args),
                **_job_ownership_args(args, repo),
            )
        except rig_launch.LaunchError as exc:
            return _err(str(exc))
        public = {key: result[key] for key in _LAUNCH_PUBLIC_KEYS if key in result}
        return {**_ok(json.dumps(public, indent=2)), "structuredContent": dict(public)}
    return _err(f"unknown tool {name}")

HANDLERS = {
    "rig_job_doing": dispatch,
    "rig_job_note": dispatch,
    "permission_prompt": dispatch,
    "rig_job_ask": dispatch,
    "rig_job_inbox": dispatch,
    "rig_job_message": dispatch,
    "rig_jobs": dispatch,
    "rig_job_show": dispatch,
    "rig_job_log": dispatch,
    "rig_job_wait": dispatch,
    "rig_job_allow": dispatch,
    "rig_job_deny": dispatch,
    "rig_job_cancel": dispatch,
    "rig_job_break_glass_close": dispatch,
    "rig_job_close": dispatch,
    "rig_job_reconcile": dispatch,
    "rig_job_recover_cancelled": dispatch,
    "rig_job_recover_parent_write": dispatch,
    "rig_job_recover_wrapper_receipt": dispatch,
    "rig_job_ui_evidence": dispatch,
    "rig_job_requirements": dispatch,
    "rig_job_criterion": dispatch,
    "rig_job_check": dispatch,
    "rig_job_start": dispatch,
    "rig_job_finish": dispatch,
    "rig_job_coordination_reply": dispatch,
    "rig_job_coordination_request": dispatch,
    "rig_job_launch": dispatch,
    "rig_job_accept": dispatch,
    "rig_job_record": dispatch,
}
