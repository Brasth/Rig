"""Session, pick, and status MCP handlers.

Facade names are resolved through the call context. Do not import rig_mcp.
"""
from __future__ import annotations

import json

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
    _review_args = ctx._review_args
    _assessment_args = ctx._assessment_args
    _domain_args = ctx._domain_args
    format_session = ctx.format_session
    rig_harness = ctx.rig_harness
    rig_route = ctx.rig_route
    PICK_ROLES = ctx.PICK_ROLES
    if name == "rig_pick":
        role = str(args.get("role") or "").strip()
        if role and role not in PICK_ROLES:
            return _err(
                "rig_pick: role must be explore|mini|bulk|implement|hard|review|verify|stay"
            )
        case = str(args.get("case") or "")
        live = rig_harness.live_parent()
        effective = rig_harness.effective_workers(repo, live)
        exclude = str(args.get("exclude") or "")
        choice = rig_route.pick(
            live, effective, role, case, exclude=exclude,
            parent_model=_optional_string(args, "parent_model"),
            parent_effort=_optional_string(args, "parent_effort"),
            repo=repo, **_review_args(args), **_assessment_args(args), **_domain_args(args),
        )
        choice = {key: value for key, value in choice.items() if not str(key).startswith("_")}
        return _ok(json.dumps(choice, indent=2))
    if name == "rig_session":
        role = str(args.get("role") or "").strip()
        if role and role not in PICK_ROLES:
            return _err(
                "rig_session: role must be explore|mini|bulk|implement|hard|review|verify|stay"
            )
        case = str(args.get("case") or "")
        if not case.strip():
            return _err("rig_session needs case")
        return _ok(
            format_session(
                repo,
                case,
                role,
                str(args.get("exclude") or ""),
                as_json=args.get("compact", False) is True,
                compact=args.get("compact", False),
                terminal_limit=args.get("terminal_limit", 10),
                parent_model=_optional_string(args, "parent_model"),
                parent_effort=_optional_string(args, "parent_effort"),
                **_review_args(args), **_assessment_args(args), **_domain_args(args),
            )
        )

    if name == "rig_status":
        return _ok(rig_harness.format_status(repo, live=rig_harness.live_parent()))
    return _err(f"unknown tool {name}")

HANDLERS = {
    "rig_pick": dispatch,
    "rig_session": dispatch,
    "rig_status": dispatch,
}
