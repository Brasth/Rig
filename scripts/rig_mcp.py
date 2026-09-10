#!/usr/bin/env python3
"""stdio MCP server: pick/status/jobs/wait/allow/memory for the parent agent."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness as rig_harness  # noqa: E402
import jobs as rig_jobs  # noqa: E402
import memory as rig_memory  # noqa: E402
import route as rig_route  # noqa: E402

PICK_ROLES = ("explore", "mini", "bulk", "implement", "hard", "review", "stay")
JOB_WORKERS = ("grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy", "parent")
JOB_FINISH_STATUSES = ("ok", "fail", "timeout")

TOOLS = [
    {
        "name": "rig_jobs",
        "description": (
            "List Rig worker jobs in this project: which agent is running, "
            "the task, status, and what it is doing now. Status ask means the "
            "Claude child is waiting: you MUST call rig_job_allow or rig_job_deny. "
            "Do not kill that job. Do not spawn another worker for the same task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Project root. Default cwd."},
                "status": {
                    "type": "string",
                    "description": "Filter: running, ok, fail, timeout, stale",
                },
                "thread": {
                    "type": "string",
                    "description": "Filter by parent thread id. Omit to list every job in this repo (new threads still see running work).",
                },
            },
        },
    },
    {
        "name": "rig_job_show",
        "description": "Show one Rig job: agent, task, status, session, and recent log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id. Default: running, else latest."},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_log",
        "description": "Decoded child log so you can see what the worker is doing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "repo": {"type": "string"},
                "lines": {"type": "integer", "default": 40},
            },
        },
    },
    {
        "name": "rig_job_wait",
        "description": (
            "Block until a Rig job asks for permission or finishes. "
            "Do not pass timeout unless you must cap the wait. Do not poll. "
            "If the text starts with ASK, call rig_job_allow or rig_job_deny next "
            "so the child can continue. Do not kill the job. Do not spawn another worker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id. Default: asking, else running."},
                "repo": {"type": "string"},
                "timeout": {
                    "type": "number",
                    "description": (
                        "Optional cap in seconds. Omit to block until ASK or result. "
                        "0 snapshots once. 124 only if still running when the cap hits."
                    ),
                },
            },
        },
    },
    {
        "name": "rig_job_allow",
        "description": (
            "Allow the Claude child's pending permission prompt. "
            "Call this when rig_jobs or rig_job_wait shows status ask and the command is safe worker work "
            "(read, edit, test, ssh gather, git status/diff/add/commit). "
            "This is how the child continues. Do not close the job instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id. Default: the asking job."},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_deny",
        "description": (
            "Deny the Claude child's pending permission prompt. "
            "Use for destructive, prod, or secrets commands. Optional reason is shown to the child."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "repo": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_memory",
        "description": (
            "Show standing project facts in .rig/MEMORY.md. "
            "Call this at the start of a new thread."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_memory_add",
        "description": (
            "Save one standing project fact to .rig/MEMORY.md. "
            "One short bullet. No transcripts. Duplicates and the 120-line cap are handled."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "One durable fact, not a transcript or job log.",
                },
                "repo": {"type": "string"},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "rig_pick",
        "description": (
            "Pick worker, spawn kind, model, and effort for a task. "
            "Same JSON as rig pick --json. Live parent is this MCP process "
            "(PPID walk / RIG_PARENT), so a Grok parent does not pick a Grok "
            "run-worker child. Does not launch a worker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {"type": "string", "description": "The task text."},
                "role": {
                    "type": "string",
                    "enum": list(PICK_ROLES),
                    "description": "Pick role. Default implement.",
                },
                "repo": {"type": "string", "description": "Project root. Default cwd."},
            },
            "required": ["case"],
        },
    },
    {
        "name": "rig_status",
        "description": (
            "Show live parent (this process / RIG_PARENT, not the toml parent key), "
            "preferred parent, effective workers, and job count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Project root. Default cwd."},
            },
        },
    },
    {
        "name": "rig_job_start",
        "description": (
            "Record a running job in .rig/jobs (meta.json + STATE). "
            "Files only. Does not launch a worker. Returns the job id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "worker": {
                    "type": "string",
                    "enum": list(JOB_WORKERS),
                    "description": "Worker name. Default live parent, else preferred.",
                },
                "role": {"type": "string", "description": "Default worker."},
                "id": {"type": "string", "description": "Job id. Allocated if omitted."},
                "summary": {"type": "string"},
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_finish",
        "description": (
            "Finish a recorded job (ok|fail|timeout). Writes result.json. "
            "Does not kill a process."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id."},
                "status": {
                    "type": "string",
                    "enum": list(JOB_FINISH_STATUSES),
                    "description": "Default ok.",
                },
                "summary": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "rig_job_record",
        "description": (
            "One-shot start+finish for a cheap same-CLI worker. Files only. "
            "Same text as rig job record."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "worker": {
                    "type": "string",
                    "enum": list(JOB_WORKERS),
                },
                "role": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": list(JOB_FINISH_STATUSES),
                    "description": "Default ok.",
                },
                "summary": {"type": "string"},
                "id": {"type": "string"},
                "repo": {"type": "string"},
            },
        },
    },
]


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _repo(args: dict) -> Path:
    return rig_jobs.repo_root(args.get("repo") if isinstance(args, dict) else None)


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


def call_tool(name: str, args: dict, on_tick=None) -> dict:
    args = args or {}
    try:
        repo = _repo(args)
        if name == "rig_jobs":
            want_thread = str(args.get("thread") or "").strip() or None
            listing = rig_jobs.list_jobs(repo, thread=want_thread)
            want = str(args.get("status") or "").strip()
            if want:
                listing = [j for j in listing if j["effective"] == want or j["status"] == want]
            return _ok(rig_jobs.format_table(listing))
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
            code, text = rig_jobs.wait_job(repo, args.get("id"), timeout_s, on_tick=on_tick)
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
        if name == "rig_memory":
            return _ok(rig_memory.show_memory(repo))
        if name == "rig_memory_add":
            fact = str(args.get("fact") or "")
            if not rig_memory.normalize_fact(fact):
                return _err("rig_memory_add needs fact")
            return _ok(rig_memory.add_memory(repo, fact))
        if name == "rig_pick":
            role = str(args.get("role") or "implement").strip() or "implement"
            if role not in PICK_ROLES:
                return _err(
                    "rig_pick: role must be explore|mini|bulk|implement|hard|review|stay"
                )
            case = str(args.get("case") or "")
            live = rig_harness.live_parent()
            effective = rig_harness.effective_workers(repo, live)
            choice = rig_route.pick(live, effective, role, case)
            return _ok(json.dumps(choice, indent=2))
        if name == "rig_status":
            return _ok(rig_harness.format_status(repo, live=rig_harness.live_parent()))
        if name in {"rig_job_start", "rig_job_finish", "rig_job_record"}:
            live = rig_harness.live_parent()
            preferred = rig_harness.preferred_parent(repo)
            worker = str(args.get("worker") or "")
            role = str(args.get("role") or "")
            summary = str(args.get("summary") or "")
            job_id = str(args.get("id") or "")
            if name == "rig_job_start":
                return _ok(
                    rig_jobs.start_job(
                        repo,
                        worker=worker,
                        role=role or "worker",
                        job_id=job_id,
                        summary=summary,
                        live=live,
                        preferred=preferred,
                    )
                )
            status = str(args.get("status") or "ok")
            if name == "rig_job_finish":
                return _ok(
                    rig_jobs.finish_job(
                        repo,
                        job_id,
                        status=status,
                        summary=summary,
                        worker=worker,
                        role=role,
                        live=live,
                        preferred=preferred,
                    )
                )
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
                )
            )
        return _err(f"unknown tool {name}")
    except SystemExit as exc:
        return _err(str(exc) or "rig error")
    except Exception as exc:  # noqa: BLE001 — MCP must not crash the parent
        return _err(str(exc))


_FRAMING = "lsp"


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
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        on_tick = None
        if name == "rig_job_wait":
            token = _progress_token(params)
            if token is not None:
                on_tick = _progress_on_tick(token)
        result = call_tool(name, args, on_tick=on_tick)
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def main() -> int:
    while True:
        try:
            msg = read_message()
        except (OSError, json.JSONDecodeError, ValueError):
            return 1
        if msg is None:
            return 0
        reply = handle(msg)
        if reply is not None:
            write_message(reply)


if __name__ == "__main__":
    raise SystemExit(main())
