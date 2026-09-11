#!/usr/bin/env python3
"""stdio MCP server: pick/status/jobs/wait/allow/memory for the parent agent.

When RIG_JOB_ID (or RIG_JOB_DIR) is set, this is the child surface: doing/note/ask
only. Parent tools stay hidden.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ask as rig_ask  # noqa: E402
import harness as rig_harness  # noqa: E402
import inbox as rig_inbox  # noqa: E402
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
            "the task, status, and what it is doing now. Status ask or running: "
            "you MUST call rig_job_allow or rig_job_deny for ask. "
            "Do not kill that job. Never spawn another worker because a child asked. "
            "After implement+verify ok, you MAY start seed (disjoint listed files) "
            "in parallel with a read-only review; wait those ids together."
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
            "Block until the job (or jobs) asks for permission or finishes. "
            "Do not pass timeout unless you must cap the wait. Do not poll. "
            "If the text starts with ASK, call rig_job_allow or rig_job_deny next "
            "so that child can continue; then wait the same ids again. "
            "Do not kill an ask or running job. Never spawn a replacement because "
            "a child asked. After implement+verify ok, pass ids for read-only review "
            "and disjoint seed together (barrier; wakes on first ASK). "
            "Spawn-infra fail may re-pick with exclude once."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Job id. Default: asking, else running. Comma-separated is wait-all.",
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Wait these jobs together. Wakes on first ASK. "
                        "Exit 0 only if every id is ok."
                    ),
                },
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
            "Allow a pending permission prompt (Claude ask). "
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
            "Deny a pending permission prompt (Claude ask). "
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
                    "description": (
                        "Optional pick kind. Omit to classify from case. "
                        "Pass stay|explore|mini|bulk|implement|hard|review "
                        "when the parent already knows the kind."
                    ),
                },
                "repo": {"type": "string", "description": "Project root. Default cwd."},
                "exclude": {
                    "type": "string",
                    "description": (
                        "Comma worker names to skip after a dead spawn "
                        "(example: grok). Skips native if live is excluded."
                    ),
                },
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
    {
        "name": "rig_session",
        "description": (
            "One call: memory, jobs, status, and pick JSON. "
            "Same as rig memory + rig jobs + rig status + rig pick. "
            "Call this first when present. Does not launch a worker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {
                    "type": "string",
                    "description": "Task text for pick. Required to include pick JSON.",
                },
                "role": {
                    "type": "string",
                    "enum": list(PICK_ROLES),
                    "description": "Optional pick kind. Omit to classify from case.",
                },
                "exclude": {
                    "type": "string",
                    "description": "Comma worker names to skip. Same as rig_pick exclude.",
                },
                "repo": {"type": "string", "description": "Project root. Default cwd."},
            },
            "required": ["case"],
        },
    },
    {
        "name": "rig_job_doing",
        "description": (
            "Child only. Set what this job is doing now. Writes job files. "
            "Do not pick, wait, or spawn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Short doing line."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rig_job_note",
        "description": "Child only. Append a short activity note. Does not change ask state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rig_job_ask",
        "description": (
            "Child only. Ask the parent a question and block until allow/deny. "
            "Same file protocol as Claude permission prompts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "preview": {"type": "string"},
                "text": {"type": "string"},
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            },
        },
    },
    {
        "name": "permission_prompt",
        "description": "Answer a Claude Code permission prompt for this Rig job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_input": {"type": "object"},
                "tool_use_id": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
    {
        "name": "rig_job_inbox",
        "description": (
            "Child only. Pull the parent inbox message once and ack it. "
            "Empty if the parent has not sent mail. Not ASK."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rig_job_message",
        "description": (
            "Parent only. Leave one message for a running child. "
            "The child pulls it with rig_job_inbox. Does not change ASK/wait."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job id. Default: running."},
                "text": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["text"],
        },
    },
]

TOOL_ORDER = (
    "rig_session",
    "rig_job_wait",
    "rig_job_allow",
    "rig_job_deny",
    "rig_jobs",
    "rig_pick",
    "rig_status",
    "rig_job_start",
    "rig_job_show",
    "rig_job_log",
    "rig_job_finish",
    "rig_job_record",
    "rig_memory",
    "rig_memory_add",
    "rig_job_message",
)
CHILD_TOOL_ORDER = (
    "rig_job_doing",
    "rig_job_note",
    "rig_job_ask",
    "permission_prompt",
    "rig_job_inbox",
    "rig_job_show",
    "rig_memory",
)
PARENT_TOOL_NAMES = frozenset(TOOL_ORDER)
CHILD_TOOL_NAMES = frozenset(CHILD_TOOL_ORDER)
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
TOOLS = [_TOOLS_BY_NAME[n] for n in TOOL_ORDER if n in _TOOLS_BY_NAME] + [
    t for t in TOOLS if t["name"] not in TOOL_ORDER and t["name"] in PARENT_TOOL_NAMES
]
CHILD_TOOLS = [_TOOLS_BY_NAME[n] for n in CHILD_TOOL_ORDER if n in _TOOLS_BY_NAME]


def child_job_id() -> str:
    jid = (os.environ.get("RIG_JOB_ID") or "").strip()
    if jid:
        return jid
    raw = (os.environ.get("RIG_JOB_DIR") or "").strip()
    return Path(raw).name if raw else ""


def is_child() -> bool:
    return bool(child_job_id() or (os.environ.get("RIG_JOB_DIR") or "").strip())


def listed_tools() -> list[dict]:
    return list(CHILD_TOOLS) if is_child() else list(TOOLS)


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


def format_session(
    repo: Path,
    case: str,
    role: str = "",
    exclude: str = "",
    *,
    as_json: bool = False,
) -> str:
    live = rig_harness.live_parent()
    effective = rig_harness.effective_workers(repo, live)
    mem = rig_memory.show_memory(repo)
    listing = rig_jobs.list_jobs(repo)
    status = rig_harness.format_status(repo, live=live)
    role_n = (role or "").strip()
    pick_err = ""
    choice: dict = {}
    if role_n and role_n not in PICK_ROLES:
        pick_err = "rig_pick: role must be explore|mini|bulk|implement|hard|review|stay"
    else:
        choice = rig_route.pick(live, effective, role_n, case or "", exclude=exclude)
    if as_json:
        return json.dumps(
            {
                "memory": mem,
                "jobs": listing,
                "status": status,
                "pick": choice if not pick_err else {"error": pick_err},
            },
            indent=2,
            default=str,
        )
    parts = [
        "# memory",
        mem.strip() or "(empty)",
        "# jobs",
        rig_jobs.format_table(listing),
        "# status",
        status.strip() or "(empty)",
        "# pick",
        pick_err or json.dumps(choice, indent=2),
    ]
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


def call_tool(name: str, args: dict, on_tick=None) -> dict:
    args = args or {}
    try:
        child = is_child()
        allowed = CHILD_TOOL_NAMES if child else PARENT_TOOL_NAMES
        if name not in allowed:
            who = "child" if child else "parent"
            return _err(f"{name} is not a {who} tool")
        repo = _repo(args)
        if child and name == "rig_job_show":
            jid = child_job_id()
            want = str(args.get("id") or jid).strip()
            if want and jid and want != jid:
                return _err("child can only show its own job")
            args = {**args, "id": jid}
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
            code, text = rig_jobs.wait_job(
                repo,
                args.get("id"),
                timeout_s,
                on_tick=on_tick,
                ids=args.get("ids"),
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
        if name == "rig_memory":
            return _ok(rig_memory.show_memory(repo))
        if name == "rig_memory_add":
            fact = str(args.get("fact") or "")
            if not rig_memory.normalize_fact(fact):
                return _err("rig_memory_add needs fact")
            return _ok(rig_memory.add_memory(repo, fact))
        if name == "rig_pick":
            role = str(args.get("role") or "").strip()
            if role and role not in PICK_ROLES:
                return _err(
                    "rig_pick: role must be explore|mini|bulk|implement|hard|review|stay"
                )
            case = str(args.get("case") or "")
            live = rig_harness.live_parent()
            effective = rig_harness.effective_workers(repo, live)
            exclude = str(args.get("exclude") or "")
            choice = rig_route.pick(live, effective, role, case, exclude=exclude)
            return _ok(json.dumps(choice, indent=2))
        if name == "rig_session":
            role = str(args.get("role") or "").strip()
            if role and role not in PICK_ROLES:
                return _err(
                    "rig_session: role must be explore|mini|bulk|implement|hard|review|stay"
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
                )
            )
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
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": listed_tools()}}
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


def run_session_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="rig_mcp")
    parser.add_argument("--session", action="store_true")
    parser.add_argument("--case", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--repo", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    repo = rig_jobs.repo_root(args.repo or None)
    print(
        format_session(
            repo,
            args.case,
            args.role,
            args.exclude,
            as_json=args.json,
        )
    )
    return 0


def main() -> int:
    if "--session" in sys.argv:
        return run_session_cli(sys.argv[1:])
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
