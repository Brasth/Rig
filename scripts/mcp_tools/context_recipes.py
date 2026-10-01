"""Context package and workflow recipe MCP handlers.

Facade names are resolved through the call context. Do not import rig_mcp.
"""
from __future__ import annotations

import json
import task_preparation

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
    if name == "rig_task_prepare":
        if set(args) - {"repo", "selection"}:
            return _err("unsupported task preparation arguments")
        result = task_preparation.prepare(repo, args.get("selection"))
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}
    rig_recipes = ctx.rig_recipes
    context_packages = ctx.context_packages
    if name in {"rig_context_preview", "rig_context_build"}:
        operation = context_packages.preview if name == "rig_context_preview" else context_packages.build
        result = operation(repo, args.get("selection"))
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}

    if name in {"rig_workflow_recipe_list", "rig_workflow_recipe_show", "rig_workflow_recipe_preview"}:
        allowed = {"repo"} if name.endswith("_list") else {"repo", "name", "version"}
        if name.endswith("_preview"):
            allowed.add("parameters")
        if set(args) - allowed:
            return _err("workflow recipe request has unsupported fields")
        if name.endswith("_list"):
            result = {"recipes": rig_recipes.listing()}
        elif name.endswith("_show"):
            result = rig_recipes.show(args.get("name"), args.get("version", 1))
        else:
            result = rig_recipes.preview(repo, args.get("name"), args.get("parameters"), args.get("version", 1))
        return {**_ok(json.dumps(result, indent=2)), "structuredContent": result}
    return _err(f"unknown tool {name}")

HANDLERS = {
    "rig_task_prepare": dispatch,
    "rig_context_preview": dispatch,
    "rig_context_build": dispatch,
    "rig_workflow_recipe_list": dispatch,
    "rig_workflow_recipe_show": dispatch,
    "rig_workflow_recipe_preview": dispatch,
}
