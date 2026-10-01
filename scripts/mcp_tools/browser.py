"""Parent computer-use and BrowserSkill MCP handlers.

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
    _optional_number = ctx._optional_number
    BSK_TOOL_NAMES = ctx.BSK_TOOL_NAMES
    CU_TOOL_NAMES = ctx.CU_TOOL_NAMES
    if name == "rig_cu_status":
        import computer_use as cu
        status = cu.cu_status(repo)
        return {**_ok(json.dumps(status, indent=2)), "structuredContent": status}
    if name == "rig_cu_serve":
        import computer_use as cu
        served = cu.cu_serve(repo)
        return {**_ok(json.dumps(served, indent=2)), "structuredContent": served}
    if name == "rig_bsk_status":
        import browser_skill as bsk
        status = bsk.bsk_status(repo)
        return {**_ok(json.dumps(status, indent=2)), "structuredContent": status}
    if name in BSK_TOOL_NAMES:
        import browser_skill as bsk
        if name == "rig_bsk_session":
            ev = bsk.bsk_session(
                repo,
                action=str(args.get("action") or "start"),
                no_focus=args.get("no_focus", True) is not False,
            )
        elif name == "rig_bsk_observe":
            ev = bsk.bsk_observe(repo)
        elif name == "rig_bsk_act":
            ev = bsk.bsk_act(
                repo,
                snapshot_id=str(args.get("snapshot_id") or ""),
                ref=str(args.get("ref") or ""),
                action=str(args.get("action") or "click"),
                text=str(args.get("text") or ""),
                key=str(args.get("key") or ""),
            )
        elif name == "rig_bsk_navigate":
            ev = bsk.bsk_navigate(repo, url=str(args.get("url") or ""))
        elif name == "rig_bsk_tab":
            ev = bsk.bsk_tab(
                repo,
                action=str(args.get("action") or "list"),
                tab_id=str(args.get("tab_id") or ""),
            )
        else:
            ev = bsk.bsk_confirm(repo, snapshot_id=str(args.get("snapshot_id") or ""))
        return bsk.present_mcp(ev)
    if name in CU_TOOL_NAMES:
        import computer_use as cu
        import cu_receipt
        if name == "rig_cu_capture":
            ev = cu.cu_capture(
                repo,
                pid=int(args.get("pid") or 0),
                window_id=int(args.get("window_id") or 0),
                bundle_id=str(args.get("bundle_id") or ""),
                app_name=str(args.get("app_name") or ""),
                profile_key=str(args.get("profile_key") or ""),
                url=str(args.get("url") or ""),
            )
        elif name == "rig_cu_act":
            ev = cu.cu_act(
                repo,
                snapshot_id=str(args.get("snapshot_id") or ""),
                element_token=str(args.get("element_token") or ""),
                ref=str(args.get("ref") or ""),
                action=str(args.get("action") or "click"),
                text=str(args.get("text") or ""),
                key=str(args.get("key") or ""),
                x=_optional_number(args, "x"),
                y=_optional_number(args, "y"),
            )
        elif name == "rig_cu_record":
            video = args.get("record_video")
            if video is None or video == "":
                video_flag = None
            elif isinstance(video, bool):
                video_flag = video
            else:
                return _err("record_video must be a boolean")
            ev = cu.cu_record(
                repo,
                action=str(args.get("action") or ""),
                output_dir=str(args.get("output_dir") or ""),
                record_video=video_flag,
            )
        else:
            ev = cu.cu_confirm(repo, snapshot_id=str(args.get("snapshot_id") or ""))
        return cu_receipt.present_mcp(ev)
    return _err(f"unknown tool {name}")

HANDLERS = {
    "rig_cu_status": dispatch,
    "rig_cu_serve": dispatch,
    "rig_bsk_status": dispatch,
    "rig_bsk_session": dispatch,
    "rig_bsk_observe": dispatch,
    "rig_bsk_act": dispatch,
    "rig_bsk_navigate": dispatch,
    "rig_bsk_tab": dispatch,
    "rig_cu_capture": dispatch,
    "rig_cu_act": dispatch,
    "rig_cu_record": dispatch,
    "rig_cu_confirm": dispatch,
    "rig_bsk_confirm": dispatch,
}
