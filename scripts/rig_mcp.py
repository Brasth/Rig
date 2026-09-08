#!/usr/bin/env python3
"""stdio MCP server: list/show/log Rig jobs for the parent agent."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jobs as rig_jobs  # noqa: E402
import memory as rig_memory  # noqa: E402

TOOLS = [
    {
        "name": "rig_jobs",
        "description": (
            "List Rig worker jobs in this project: which agent is running, "
            "the task, status, and what it is doing now."
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
]


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _repo(args: dict) -> Path:
    return rig_jobs.repo_root(args.get("repo") if isinstance(args, dict) else None)


def call_tool(name: str, args: dict) -> dict:
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
        if name == "rig_memory":
            return _ok(rig_memory.show_memory(repo))
        if name == "rig_memory_add":
            fact = str(args.get("fact") or "")
            if not rig_memory.normalize_fact(fact):
                return _err("rig_memory_add needs fact")
            return _ok(rig_memory.add_memory(repo, fact))
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
        result = call_tool(str(params.get("name") or ""), params.get("arguments") or {})
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
