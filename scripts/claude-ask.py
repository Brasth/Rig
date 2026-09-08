#!/usr/bin/env python3
"""MCP server Claude calls via --permission-prompt-tool. Blocks until the parent answers."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ask as rig_ask  # noqa: E402

JOB_DIR = Path(os.environ.get("RIG_JOB_DIR") or "")

TOOL = {
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
}

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


def handle_prompt(args: dict) -> dict:
    if not JOB_DIR:
        decision = rig_ask.decision_from_reply(None, {})
        decision["message"] = "RIG_JOB_DIR missing"
        text = json.dumps(decision)
        return {"content": [{"type": "text", "text": text}]}
    tool, inp, uid = rig_ask.parse_prompt_args(args)
    rig_ask.write_ask(JOB_DIR, tool, inp, uid)
    reply = rig_ask.wait_reply(JOB_DIR)
    decision = rig_ask.decision_from_reply(reply, inp)
    rig_ask.consume_ask(JOB_DIR)
    text = json.dumps(decision)
    return {"content": [{"type": "text", "text": text}]}


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
                "serverInfo": {"name": "rig-ask", "version": "1"},
            },
        }
    if method in {"notifications/initialized", "initialized"}:
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name != "permission_prompt":
            result = {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        else:
            result = handle_prompt(args)
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
