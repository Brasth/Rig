"""Queue and project memory MCP handlers.

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
    _ownership_args = ctx._ownership_args
    rig_queue = ctx.rig_queue
    rig_memory = ctx.rig_memory
    if name == "rig_queue_add":
        text = str(args.get("text") or "").strip()
        if not text:
            return _err("rig_queue_add needs text")
        obj = rig_queue.add_item(repo, text, idempotency_key=args.get("idempotency_key", ""))
        return {**_ok(f"queued {obj['id']}\n{obj['text']}"), "structuredContent": obj}
    if name == "rig_queue_list":
        return _ok(rig_queue.format_list(repo))
    if name == "rig_queue_cancel":
        qid = str(args.get("id") or "").strip()
        if not qid:
            return _err("rig_queue_cancel needs id")
        try:
            obj = rig_queue.cancel_item(repo, qid, **_ownership_args(args, repo))
        except FileNotFoundError:
            return _err(f"queue item not found: {qid}")
        except ValueError as exc:
            return _err(str(exc))
        return {**_ok(f"cancelled {obj['id']}\n{obj.get('held_reason') or ''}"), "structuredContent": obj}
    if name == "rig_queue_claim":
        files = args.get("files")
        try:
            obj = rig_queue.claim_next(
                repo,
                files=files,
                item_id=str(args.get("id") or "").strip(),
                worker=_optional_string(args, "worker"), access=args.get("access", "write"),
                owner_session=_optional_string(args, "owner_session"), job_id=_optional_string(args, "job_id"),
            )
        except rig_queue.QueueError as exc:
            return _err(str(exc))
        except FileNotFoundError as exc:
            return _err(f"queue item not found: {exc}")
        return {**_ok(f"claimed {obj['id']}\n{obj.get('text') or ''}\n{rig_queue.format_block(repo)}"), "structuredContent": obj}
    if name == "rig_queue_unclaim":
        qid = str(args.get("id") or "").strip()
        if not qid:
            return _err("rig_queue_unclaim needs id")
        try:
            obj = rig_queue.unclaim(repo, qid, **_ownership_args(args, repo))
        except FileNotFoundError:
            return _err(f"queue item not found: {qid}")
        except rig_queue.QueueError as exc:
            return _err(str(exc))
        return _ok(f"unclaimed {obj['id']}\n{rig_queue.format_block(repo)}")
    if name == "rig_queue_spawned":
        qid = str(args.get("id") or "").strip()
        jid = str(args.get("job_id") or "").strip()
        try:
            obj = rig_queue.mark_spawned(
                repo, qid, jid, files=args.get("files"), worker=_optional_string(args, "worker"),
                access=_optional_string(args, "access"), **_ownership_args(args, repo),
            )
        except FileNotFoundError:
            return _err(f"queue item not found: {qid}")
        except rig_queue.QueueError as exc:
            return _err(str(exc))
        return _ok(f"{obj.get('status') or 'spawned'} {obj['id']} job {obj.get('job_id')}\n{rig_queue.format_block(repo)}")

    if name == "rig_memory":
        view = rig_memory.memory_view(repo)
        return {**_ok(view["text"]), "structuredContent": {key: view[key] for key in ("sha256", "facts")}}
    if name in {"rig_memory_replace", "rig_memory_remove"}:
        selected = args.get("old") if name == "rig_memory_replace" else args.get("fact")
        if not isinstance(selected, str) or (name == "rig_memory_replace" and not isinstance(args.get("new"), str)):
            return _err("Specify the exact fact and replacement")
        return _ok(rig_memory.edit_memory(repo, selected, args.get("expected_sha256"),
                   args.get("new") if name == "rig_memory_replace" else None))
    if name == "rig_memory_add":
        fact = str(args.get("fact") or "")
        if not rig_memory.normalize_fact(fact):
            return _err("rig_memory_add needs fact")
        return _ok(rig_memory.add_memory(repo, fact))
    return _err(f"unknown tool {name}")

HANDLERS = {
    "rig_queue_add": dispatch,
    "rig_queue_list": dispatch,
    "rig_queue_cancel": dispatch,
    "rig_queue_claim": dispatch,
    "rig_queue_unclaim": dispatch,
    "rig_queue_spawned": dispatch,
    "rig_memory": dispatch,
    "rig_memory_replace": dispatch,
    "rig_memory_add": dispatch,
    "rig_memory_remove": dispatch,
}
