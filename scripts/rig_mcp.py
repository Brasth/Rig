#!/usr/bin/env python3
"""stdio MCP server: pick/status/jobs/wait/allow/memory/workflow for the parent agent.

When RIG_JOB_ID (or RIG_JOB_DIR) is set, this is the child surface: inbox/doing/note/ask
plus own show, project memory, and coordination request. Parent workflow tools stay hidden.
First Rig operation is inbox.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import cancellation
from mcp_runtime import Runtime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ask as rig_ask  # noqa: E402
import harness as rig_harness  # noqa: E402
import inbox as rig_inbox  # noqa: E402
import jobs as rig_jobs  # noqa: E402
import work_queue as rig_queue  # noqa: E402
import memory as rig_memory  # noqa: E402
import route as rig_route  # noqa: E402
import verification as rig_verification  # noqa: E402
import worker_launch as rig_launch  # noqa: E402
import child_mcp as rig_child_mcp  # noqa: E402
import workflow as rig_workflow  # noqa: E402
import workflow_recipes as rig_recipes  # noqa: E402
import coordination as rig_coordination  # noqa: E402
import routing_domains  # noqa: E402
import doctor as rig_doctor  # noqa: E402
import context_packages  # noqa: E402

_DOCTOR_HOST_SNAPSHOT = rig_doctor.runtime_snapshot()

from mcp_tools.schemas import *  # noqa: F403
from mcp_tools.context import CallState, ToolContext
from mcp_tools import load_registry

def child_job_id() -> str:
    jid = (os.environ.get("RIG_JOB_ID") or "").strip()
    if jid:
        return jid
    raw = (os.environ.get("RIG_JOB_DIR") or "").strip()
    return Path(raw).name if raw else ""


def is_child() -> bool:
    return bool(child_job_id() or (os.environ.get("RIG_JOB_DIR") or "").strip())


CU_TOOL_NAMES = frozenset({"rig_cu_serve", "rig_cu_capture", "rig_cu_act", "rig_cu_confirm", "rig_cu_record"})
BSK_TOOL_NAMES = frozenset({"rig_bsk_session", "rig_bsk_observe", "rig_bsk_act", "rig_bsk_confirm", "rig_bsk_navigate", "rig_bsk_tab"})


def listed_tools() -> list[dict]:
    if is_child():
        return list(CHILD_TOOLS)
    tools = list(TOOLS)
    try:
        import computer_use as cu
        raw = (os.environ.get("RIG_REPO") or "").strip()
        repo = _repo({"repo": raw} if raw else {})
        if not rig_harness.project_state(repo)["enabled"] or not cu.tools_listed(repo, child=False):
            tools = [item for item in tools if item["name"] not in CU_TOOL_NAMES]
    except Exception:
        # Mixed ~/.rig copies or a CU helper crash must not kill tools/list.
        tools = [item for item in tools if item["name"] not in CU_TOOL_NAMES]
    try:
        import browser_skill as bsk
        raw = (os.environ.get("RIG_REPO") or "").strip()
        repo = _repo({"repo": raw} if raw else {})
        if not rig_harness.project_state(repo)["enabled"] or not bsk.tools_listed(repo, child=False):
            tools = [item for item in tools if item["name"] not in BSK_TOOL_NAMES]
    except Exception:
        tools = [item for item in tools if item["name"] not in BSK_TOOL_NAMES]
    return tools


def child_job_dir(repo: Path) -> Path:
    raw = (os.environ.get("RIG_JOB_DIR") or "").strip()
    if raw:
        return Path(raw)
    jid = child_job_id()
    if not jid:
        raise SystemExit("child MCP needs RIG_JOB_ID")
    return rig_jobs.jobs_dir(repo) / jid


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _repo(args: dict) -> Path:
    return rig_jobs.repo_root(args.get("repo") if isinstance(args, dict) else None)


def _bound_child_repo(args: dict) -> Path:
    env_repo = (os.environ.get("RIG_REPO") or "").strip()
    if env_repo:
        bound = rig_jobs.repo_root(env_repo)
    else:
        raw = (os.environ.get("RIG_JOB_DIR") or "").strip()
        if not raw:
            raise ValueError("child MCP needs RIG_REPO or RIG_JOB_DIR")
        bound = rig_jobs.repo_root(Path(raw).resolve().parent.parent.parent)
    requested = args.get("repo") if isinstance(args, dict) else None
    if requested not in (None, ""):
        got = rig_jobs.repo_root(requested)
        if got != bound:
            raise ValueError("child cannot bind a different repo")
    return bound


LAUNCH_ARG_NAMES = frozenset({
    "repo", "id", "case", "role", "worker", "model", "effort", "access", "files", "brief",
    "queue_id", "reservation_id", "attempt_id", "owner_token", "owner_session",
    "credentials_path",
    "writer_job_id", "writer_snapshot_id", "writer_cli", "writer_model",
    "writer_provider", "review_mode", "routing", "assessment", "task_domain", "research_sources",
    "continues_job_id", "acceptance_contract", "context_package",
})
_LAUNCH_PUBLIC_KEYS = (
    "job_id", "worker", "role", "wrapper_pid", "status",
    "reservation_id", "attempt_id", "credentials_path", "contract_fingerprint", "context_package", "context_evidence",
)


def _optional_string(args: dict, name: str) -> str:
    value = args.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_number(args: dict, name: str):
    if name not in args or args[name] is None or args[name] == "":
        return None
    value = args[name]
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as error:
            raise ValueError(f"{name} must be a number") from error
    raise ValueError(f"{name} must be a number")


def _workflow_owner_args(args: dict) -> dict:
    return {
        "owner_token": _optional_string(args, "owner_token"),
        "owner_session": _optional_string(args, "owner_session"),
    }


def _workflow_response(obj, *, include_secrets=False) -> dict:
    safe = rig_workflow.public_payload(obj)
    return {**_ok(json.dumps(safe, indent=2, default=str)), "structuredContent": obj if include_secrets else safe}


def _ownership_args(args: dict, repo: Path, *, job_id: str = "", restore_owner_session: bool = True) -> dict:
    import admission as rig_admission

    return rig_admission.resolve_ownership(
        repo,
        reservation_id=_optional_string(args, "reservation_id"),
        attempt_id=_optional_string(args, "attempt_id"),
        owner_token=_optional_string(args, "owner_token"),
        owner_session=_optional_string(args, "owner_session"),
        credentials_path=_optional_string(args, "credentials_path"),
        job_id=job_id,
        restore_owner_session=restore_owner_session,
    )


def _job_ownership_args(args: dict, repo: Path) -> dict:
    return _ownership_args(args, repo, job_id=_optional_string(args, "id"))


def _execution_args(args: dict) -> dict:
    values = {name: _optional_string(args, name) for name in JOB_EXECUTION_PROPERTIES}
    if values["executor_kind"] not in {"", "parent", "native_child"}:
        raise ValueError("executor_kind must be parent|native_child")
    return values


def _review_args(args: dict) -> dict:
    skip = {"review_mode", "writer_job_ids", "writer_snapshot_ids", "writer_providers"}
    values = {name: _optional_string(args, name) for name in REVIEW_PROPERTIES if name not in skip}
    values["review_mode"] = args.get("review_mode", "standalone")
    if values["review_mode"] not in {"standalone", "independent"}:
        raise ValueError("review_mode must be standalone|independent")
    for name in ("writer_job_ids", "writer_snapshot_ids", "writer_providers"):
        raw = args.get(name)
        if raw in (None, ""):
            continue
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ValueError(f"{name} must be an array of strings")
        values[name] = raw
    return values


def _domain_args(args: dict) -> dict:
    domain = routing_domains.normalize_domain(_optional_string(args, "task_domain"))
    sources = args.get("research_sources")
    if sources is not None and (
        not isinstance(sources, list)
        or any(not isinstance(item, str) or not item.strip() for item in sources)
    ):
        raise ValueError("research_sources must be an array of nonempty file paths")
    return {"task_domain": domain, "research_sources": sources}


def _assessment_args(args: dict) -> dict:
    import routing_policy

    assessment = args.get("assessment")
    if assessment not in (None, "") and not isinstance(assessment, dict):
        raise ValueError("assessment must be an object")
    if isinstance(assessment, dict):
        extra = set(assessment) - (routing_policy.ASSESSMENT_FIELDS | routing_policy.ASSESSMENT_META)
        if extra:
            raise ValueError(f"unknown assessment key {sorted(extra)[0]}")
        for key in ("complexity", "risk", "uncertainty", "reason"):
            if key in assessment:
                value = assessment[key]
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, str):
                    raise ValueError(f"assessment.{key} must be a string")
        if "supplied" in assessment and type(assessment["supplied"]) is not bool:
            raise ValueError("assessment.supplied must be a boolean")
        if "defaulted" in assessment and (
            not isinstance(assessment["defaulted"], list)
            or any(not isinstance(item, str) for item in assessment["defaulted"])
        ):
            raise ValueError("assessment.defaulted must be a list of strings")
    policy_mode = args.get("policy_mode", "")
    if policy_mode not in (None, "", "smart", "legacy"):
        raise ValueError("policy_mode must be smart|legacy")
    explain = args.get("explain", False)
    if type(explain) is not bool:
        raise ValueError("explain must be a boolean")
    out = {
        "assessment": assessment if isinstance(assessment, dict) else None,
        "complexity": _optional_string(args, "complexity") if "complexity" in args else "",
        "risk": _optional_string(args, "risk") if "risk" in args else "",
        "uncertainty": _optional_string(args, "uncertainty") if "uncertainty" in args else "",
        "assessment_reason": _optional_string(args, "assessment_reason") if "assessment_reason" in args else "",
        "policy_mode": policy_mode or None,
        "explain": explain,
    }
    return out


def _compact_rows(listing: list[dict], terminal_limit: int, *, repo: Path, cache=None) -> list[dict]:
    active_states = {"running", "ask", "reserved"}
    def is_active(job):
        return job.get("effective") in active_states or bool(job.get("reservation") and job["reservation"].get("stage") != "released")
    active = [job for job in listing if is_active(job)]
    terminal = [job for job in listing if not is_active(job)]
    terminal.sort(key=lambda job: job.get("mtime", 0), reverse=True)
    keys = (
        "job_id", "worker", "role", "task", "status", "effective", "doing",
        "model", "effort", "model_source", "model_inferred", "executor_kind",
        "files", "reservation_id", "attempt_id", "execution_mode",
        "writer_job_id", "writer_provider", "ownership_established",
    )
    rows = []
    for job in active + terminal[:terminal_limit]:
        row = {key: job.get(key, False if key == "model_inferred" else "") for key in keys}
        projection = rig_jobs.project_job(job, repo, refresh=True, cache=cache)
        for key in ("verification_summary", "display_state", "display_reason", "display_action", "display_model", "independence", "review_completed", "ownership_next_action"):
            row[key] = projection[key]
        if job.get("reservation"):
            row["reservation"] = {key: job["reservation"].get(key) for key in
                                  ("stage", "slot_held", "access", "stopped", "needs_reconciliation", "reconciliation_reason")}
        rows.append(row)
    return rows


def format_session(
    repo: Path,
    case: str,
    role: str = "",
    exclude: str = "",
    *,
    as_json: bool = False,
    compact: bool = False,
    terminal_limit: int = 10,
    parent_model: str = "",
    parent_effort: str = "",
    writer_job_id: str = "",
    writer_cli: str = "",
    writer_model: str = "",
    writer_provider: str = "",
    review_mode: str = "standalone",
    assessment: dict | None = None,
    complexity: str = "",
    risk: str = "",
    uncertainty: str = "",
    assessment_reason: str = "",
    policy_mode: str | None = None,
    explain: bool = False,
    task_domain: str = "",
    research_sources: list[str] | None = None,
) -> str:
    if type(compact) is not bool:
        raise ValueError("rig_session: compact must be a boolean")
    if type(terminal_limit) is not int or not 0 <= terminal_limit <= 100:
        raise ValueError("rig_session: terminal_limit must be an integer from 0 to 100")
    live = rig_harness.live_parent()
    effective = rig_harness.effective_workers(repo, live)
    mem = rig_memory.show_memory(repo)
    listing = rig_jobs.list_jobs(repo)
    hash_cache = {}
    status = "" if compact and as_json else rig_harness.format_status(
        repo, live=live, jobs_snapshot=listing, include_jobs=not compact, effective=effective,
    )
    role_n = (role or "").strip()
    pick_err = ""
    choice: dict = {}
    if role_n and role_n not in PICK_ROLES:
        pick_err = "rig_pick: role must be " + "|".join(PICK_ROLES)
    else:
        choice = rig_route.pick(
            live, effective, role_n, case or "", exclude=exclude,
            parent_model=parent_model, parent_effort=parent_effort,
            writer_job_id=writer_job_id, writer_cli=writer_cli, writer_model=writer_model,
            writer_provider=writer_provider, review_mode=review_mode, repo=repo, jobs_snapshot=listing,
            hash_cache=hash_cache, assessment=assessment, complexity=complexity, risk=risk,
            uncertainty=uncertainty, assessment_reason=assessment_reason, policy_mode=policy_mode,
            explain=explain, task_domain=task_domain, research_sources=research_sources,
        )
        choice = {key: value for key, value in choice.items() if not str(key).startswith("_")}
    shown = _compact_rows(listing, terminal_limit, repo=repo, cache=hash_cache) if compact else listing
    import workflow_state as rig_wf
    workflow_rows = rig_wf.list_workflows(repo, include_terminal=True)
    if compact:
        active_wf = [row for row in workflow_rows if row.get("status") in rig_wf.ACTIVE]
        terminal_wf = [row for row in workflow_rows if row.get("status") not in rig_wf.ACTIVE]
        workflow_rows = active_wf + terminal_wf[:terminal_limit]
    history = {
        "total": len(listing),
        "shown": len(shown),
        "omitted": len(listing) - len(shown),
        "invalid_directories": getattr(listing, "directory_count", len(listing)) - getattr(listing, "real_job_count", len(listing)),
    }
    if as_json:
        payload = {
            "memory": mem,
            "jobs": shown,
            "workflows": workflow_rows,
            "status": status,
            "pick": choice if not pick_err else {"error": pick_err},
        }
        if compact:
            payload.update(schema_version=2, mode="compact", history=history)
            payload["status"] = {
                "live": live or "",
                "preferred": rig_harness.preferred_parent(repo),
                "effective": effective,
                "jobs": getattr(listing, "directory_count", len(listing)),
                "thread": rig_jobs.current_thread(repo),
                "memory_facts": len(rig_memory.parse_bullets(mem)),
                "reserved": sum(1 for reservation in getattr(listing, "reservations", None) or [] if reservation.get("stage") == "reserved"),
            }
        return json.dumps(payload, indent=2, default=str)
    jobs_text = rig_jobs.format_table(shown, repo, jobs_snapshot=listing)
    wf_text = rig_workflow.format_list(workflow_rows)
    if compact:
        jobs_text += (
            f"\nhistory: {history['shown']} shown / {history['total']} valid jobs; "
            f"{history['omitted']} omitted; {history['invalid_directories']} invalid directories"
        )
    parts = [
        "# memory",
        mem.strip() or "(empty)",
        "# jobs",
        jobs_text,
        "# workflows",
        wf_text,
        "# status",
        status.strip() or "(empty)",
        "# pick",
        pick_err or json.dumps(choice, indent=2),
    ]
    if explain and choice and not pick_err:
        import routing_policy
        parts.extend(["# routing explanation", *routing_policy.explain_lines(choice)])
    return "\n".join(parts)


def _progress_token(params: dict):
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict) or "progressToken" not in meta:
        return None
    token = meta.get("progressToken")
    if token is None or token == "":
        return None
    return token


def _progress_on_tick(token):
    n = 0

    def on_tick(job: dict) -> None:
        nonlocal n
        n += 1
        status = str((job or {}).get("effective") or "").strip()
        job_id = str((job or {}).get("job_id") or "").strip()
        doing = str((job or {}).get("doing") or "").strip()
        write_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": token,
                    "progress": n,
                    "message": " ".join(p for p in (status, job_id, doing) if p),
                },
            }
        )

    return on_tick


def _child_ask(job_dir: Path, args: dict, *, permission: bool) -> dict:
    preview_text = str(args.get("preview") or args.get("text") or "").strip()
    tool, inp, uid = rig_ask.parse_prompt_args(args)
    if not permission:
        if preview_text and (tool == "tool" or not args.get("tool_name")):
            tool = str(args.get("tool_name") or "ask")
            if not inp:
                inp = {"text": preview_text}
        rig_ask.write_ask(job_dir, tool, inp, uid, preview_text=preview_text)
    else:
        rig_ask.write_ask(job_dir, tool, inp, uid)
    reply = rig_ask.wait_reply(job_dir)
    decision = rig_ask.decision_from_reply(reply, inp)
    rig_ask.consume_ask(job_dir)
    return _ok(json.dumps(decision))


_CTX = ToolContext(sys.modules[__name__])
_REGISTRY = load_registry()

def call_tool(name: str, args: dict, on_tick=None, *, wait_paths: list[Path] | None = None, cancel_event=None, wait_targets=None) -> dict:
    args = args or {}
    try:
        child = is_child()
        allowed = CHILD_TOOL_NAMES if child else PARENT_TOOL_NAMES
        if name not in allowed:
            who = "child" if child else "parent"
            return _err(f"{name} is not a {who} tool")
        repo = _bound_child_repo(args) if child else _repo(args)
        if not child:
            state = rig_harness.project_state(repo)
            readonly = {"rig_recovery_guide", "rig_task_prepare", "rig_doctor", "rig_status", "rig_jobs", "rig_job_show", "rig_job_log", "rig_memory",
                        "rig_queue_list", "rig_workflows", "rig_workflow_show", "rig_workflow_report", "rig_task_timeline",
                        "rig_workflow_recipe_list", "rig_workflow_recipe_show", "rig_workflow_recipe_preview",
                        "rig_routing_report", "rig_billing_report"}
            if state["state"] == "uninitialized" and name not in readonly:
                return _err("Rig is uninitialized for this project; run rig init")
            if not state["enabled"] and name not in readonly:
                return _err(rig_harness.DISABLED_MESSAGE)
        if child:
            job_dir = child_job_dir(repo)
            blocked = rig_child_mcp.require_inbox(job_dir, name)
            if blocked:
                return _err(blocked)
        if child and name == "rig_job_show":
            jid = child_job_id()
            want = str(args.get("id") or jid).strip()
            if want and jid and want != jid:
                return _err("child can only show its own job")
            args = {**args, "id": jid}
        state = CallState(
            name, args, repo, on_tick=on_tick, wait_paths=wait_paths,
            cancel_event=cancel_event, wait_targets=wait_targets,
        )
        handler = _REGISTRY.get(name)
        if handler is None:
            return _err(f"unknown tool {name}")
        return handler(_CTX, state)
    except SystemExit as exc:
        return _err(str(exc) or "rig error")
    except Exception as exc:  # noqa: BLE001 — MCP must not crash the parent
        return _err(str(exc))

_FRAMING = "lsp"
_out_lock = threading.Lock()
def _abort_request(request):
    for target in request.targets:
        try:
            if request.name == "rig_workflow_wait":
                import workflow_cancellation
                workflow_cancellation.publish(target, "wait-cancelled")
            else:
                rig_jobs.cancel_target(target, "wait-cancelled")
        except (SystemExit, Exception) as error:
            print(f"rig cancellation {target.get('job_id') or target.get('workflow_id')}: {error}", file=sys.stderr)


def _execute_request(request):
    on_tick = None
    token = _progress_token(request.params)
    if token is not None:
        progress = _progress_on_tick(token)
        def on_tick(job):
            if _runtime.progress_allowed(request):
                progress(job)
    paths = None
    if request.name == "rig_job_wait" and not is_child():
        repo = _repo(request.args)
        names = rig_jobs._normalize_wait_ids(request.args.get("id"), request.args.get("ids"))
        paths = rig_jobs.resolve_job_paths(repo, names)
        request.targets = [cancellation.capture(path) for path in paths]
        request.bound.set()
        if request.cancelled:
            _abort_request(request)
    elif request.name == "rig_workflow_wait" and not is_child() and request.args.get("owner_token"):
        import workflow_cancellation
        request.targets = [workflow_cancellation.capture(
            _repo(request.args), _optional_string(request.args, "id").strip(),
            **_workflow_owner_args(request.args),
        )]
        request.bound.set()
        if request.cancelled:
            _abort_request(request)
    else:
        request.bound.set()
    try:
        return call_tool(request.name, request.args, on_tick=on_tick, wait_paths=paths,
                         cancel_event=request.stop, wait_targets=request.targets or None)
    finally:
        if request.cancelled and request.name in {"rig_job_wait", "rig_workflow_wait"}:
            _abort_request(request)


_runtime = Runtime(_execute_request, lambda message: write_message(message), _abort_request)


def read_message() -> dict | None:
    global _FRAMING
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    stripped = line.lstrip()
    if stripped.startswith(b"{"):
        _FRAMING = "ndjson"
        return json.loads(stripped)
    headers: dict[str, str] = {}
    while True:
        if line in (b"\r\n", b"\n"):
            break
        key, _, val = line.decode("utf-8", errors="replace").partition(":")
        headers[key.strip().lower()] = val.strip()
        line = sys.stdin.buffer.readline()
        if not line:
            return None
    n = int(headers.get("content-length") or "0")
    if n <= 0:
        return None
    _FRAMING = "lsp"
    body = sys.stdin.buffer.read(n)
    return json.loads(body.decode("utf-8"))


def write_message(msg: dict) -> None:
    raw = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    with _out_lock:
        if _FRAMING == "ndjson":
            sys.stdout.buffer.write(raw + b"\n")
        else:
            sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
        sys.stdout.buffer.flush()


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        proto = str(params.get("protocolVersion") or "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "rig", "version": "1"},
            },
        }
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "notifications/cancelled" or method == "cancelled":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        _runtime.cancel(params.get("requestId"))
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": listed_tools()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        allowed = CHILD_TOOL_NAMES if is_child() else PARENT_TOOL_NAMES
        if name not in allowed:
            who = "child" if is_child() else "parent"
            return {"jsonrpc": "2.0", "id": mid, "result": _err(f"{name} is not a {who} tool")}
        return _runtime.start(mid, name, args, params)
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def run_session_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="rig_mcp")
    parser.add_argument("--session", action="store_true")
    parser.add_argument("--case", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--task-domain", default="")
    parser.add_argument("--research-source", action="append", dest="research_sources")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--parent-model", default="")
    parser.add_argument("--parent-effort", default="")
    parser.add_argument("--complexity", default="")
    parser.add_argument("--risk", default="")
    parser.add_argument("--uncertainty", default="")
    parser.add_argument("--assessment-reason", default="")
    parser.add_argument("--policy-mode", choices=["smart", "legacy"], default="")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--terminal-limit", type=int, default=10)
    parser.add_argument("--repo", default="")
    parser.add_argument("--json", action="store_true")
    for name in ("writer-job-id", "writer-cli", "writer-model", "writer-provider"):
        parser.add_argument("--" + name, default="")
    parser.add_argument("--review-mode", choices=["standalone", "independent"], default="standalone")
    args = parser.parse_args(argv)
    if not 0 <= args.terminal_limit <= 100:
        parser.error("--terminal-limit must be an integer from 0 to 100")
    repo = rig_jobs.repo_root(args.repo or None)
    print(
        format_session(
            repo,
            args.case,
            args.role,
            args.exclude,
            as_json=args.json,
            compact=args.compact,
            terminal_limit=args.terminal_limit,
            parent_model=args.parent_model,
            parent_effort=args.parent_effort,
            writer_job_id=args.writer_job_id, writer_cli=args.writer_cli,
            writer_model=args.writer_model, writer_provider=args.writer_provider,
            review_mode=args.review_mode,
            complexity=args.complexity, risk=args.risk, uncertainty=args.uncertainty,
            assessment_reason=args.assessment_reason, policy_mode=args.policy_mode or None,
            explain=args.explain, task_domain=args.task_domain, research_sources=args.research_sources,
        )
    )
    return 0


def main() -> int:
    if "--session" in sys.argv:
        return run_session_cli(sys.argv[1:])
    from ui_runtime_lease import lease
    with lease("mcp"):
        try:
            return _serve_messages()
        finally:
            _runtime.shutdown()


def _serve_messages() -> int:
    while True:
        try:
            msg = read_message()
        except (OSError, json.JSONDecodeError, ValueError):
            return 1
        if msg is None:
            _runtime.shutdown()
            return 0
        reply = handle(msg)
        if reply is not None:
            write_message(reply)


if __name__ == "__main__":
    raise SystemExit(main())
