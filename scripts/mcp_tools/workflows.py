"""Adaptive workflow MCP handlers.

Facade names are resolved through the call context. Do not import rig_mcp.
"""
from __future__ import annotations

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
    _workflow_owner_args = ctx._workflow_owner_args
    _workflow_response = ctx._workflow_response
    rig_workflow = ctx.rig_workflow
    context_packages = ctx.context_packages
    if name == "rig_workflow_create":
        spec = args.get("spec")
        if not isinstance(spec, dict):
            return _err("spec must be an object")
        result = rig_workflow.create(
            repo, spec, owner_session=_optional_string(args, "owner_session"),
            queue_id=_optional_string(args, "queue_id"),
        )
        return _workflow_response(result, include_secrets=True)
    if name == "rig_workflows":
        include = args.get("include_terminal")
        if include is None:
            include = True
        elif type(include) is not bool:
            return _err("include_terminal must be a boolean")
        rows = rig_workflow.listing(repo, include_terminal=include)
        safe = rig_workflow.public_payload(rows)
        return {**_ok(rig_workflow.format_list(rows)), "structuredContent": {"workflows": safe}}
    if name == "rig_workflow_show":
        wid = _optional_string(args, "id").strip()
        if not wid:
            return _err("rig_workflow_show needs id")
        return _workflow_response(rig_workflow.show(repo, wid))
    if name == "rig_workflow_advance":
        wid = _optional_string(args, "id").strip()
        if not wid:
            return _err("rig_workflow_advance needs id")
        return _workflow_response(rig_workflow.advance(repo, wid, **_workflow_owner_args(args)))
    if name == "rig_workflow_wait":
        wid = _optional_string(args, "id").strip()
        if not wid:
            return _err("rig_workflow_wait needs id")
        timeout = args.get("timeout")
        if timeout is None:
            timeout_s = None
        else:
            try:
                timeout_s = float(timeout)
            except (TypeError, ValueError):
                return _err("timeout must be a number")
        code, text = rig_workflow.wait(
            repo, wid, timeout_s, on_tick=on_tick, cancel_event=cancel_event,
        )
        if code == 1:
            return _err(text)
        if not args.get("owner_token"):
            text = "Observation-only wait: host Stop ends observation; use rig_workflow_cancel with owner credentials to stop execution.\n" + text
        return _ok(text)
    if name == "rig_workflow_extend":
        wid = _optional_string(args, "id").strip()
        nodes = {"nodes": args.get("nodes", []), "context_packages": args.get("context_packages", {})}
        if not wid:
            return _err("rig_workflow_extend needs id")
        if not isinstance(nodes["nodes"], list) or not isinstance(nodes["context_packages"], dict):
            return _err("nodes must be an array and context_packages a node/reference object")
        return _workflow_response(
            rig_workflow.extend(repo, wid, nodes, **_workflow_owner_args(args)),
        )
    if name == "rig_workflow_resolve":
        wid = _optional_string(args, "id").strip()
        node_id = _optional_string(args, "node_id").strip()
        if not wid or not node_id:
            return _err("rig_workflow_resolve needs id and node_id")
        action = args.get("action", "retry")
        if action not in {"retry", "skip", "fail"}:
            return _err("action must be retry|skip|fail")
        return _workflow_response(rig_workflow.resolve(
            repo, wid, node_id, action=action,
            rationale=_optional_string(args, "rationale"),
            **_workflow_owner_args(args),
        ))
    if name == "rig_workflow_approve":
        wid = _optional_string(args, "id").strip()
        node_id = _optional_string(args, "node_id").strip()
        rationale = _optional_string(args, "rationale")
        if not wid or not node_id:
            return _err("rig_workflow_approve needs id and node_id")
        if not rationale.strip():
            return _err("rig_workflow_approve needs rationale")
        return _workflow_response(rig_workflow.approve(
            repo, wid, node_id, rationale=rationale, **_workflow_owner_args(args),
        ))
    if name == "rig_workflow_cancel":
        wid = _optional_string(args, "id").strip()
        if not wid:
            return _err("rig_workflow_cancel needs id")
        return _workflow_response(rig_workflow.cancel(
            repo, wid, rationale=_optional_string(args, "rationale") or "parent",
            **_workflow_owner_args(args),
        ))

    if name == "rig_workflow_report":
        wid = _optional_string(args, "id").strip()
        if not wid:
            return _err("rig_workflow_report needs id")
        return _workflow_response(rig_workflow.report(repo, wid))
    return _err(f"unknown tool {name}")

HANDLERS = {
    "rig_workflow_create": dispatch,
    "rig_workflows": dispatch,
    "rig_workflow_show": dispatch,
    "rig_workflow_advance": dispatch,
    "rig_workflow_wait": dispatch,
    "rig_workflow_extend": dispatch,
    "rig_workflow_resolve": dispatch,
    "rig_workflow_approve": dispatch,
    "rig_workflow_cancel": dispatch,
    "rig_workflow_report": dispatch,
}
