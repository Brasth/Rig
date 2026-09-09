#!/usr/bin/env python3
"""Claude child permission prompts: ask.json in the job dir, parent answers."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ASK_NAME = "ask.json"
REPLY_NAME = "ask-reply.json"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ask_path(job_dir: Path) -> Path:
    return Path(job_dir) / ASK_NAME


def reply_path(job_dir: Path) -> Path:
    return Path(job_dir) / REPLY_NAME


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def preview(tool_name: str, inp: dict) -> str:
    detail = ""
    if isinstance(inp, dict):
        for key in ("command", "file_path", "path", "target_file", "query", "url"):
            val = inp.get(key)
            if isinstance(val, str) and val.strip():
                detail = " ".join(val.split())
                break
    text = f"{tool_name} {detail}".strip()
    return text if len(text) <= 140 else text[:139] + "…"


def parse_prompt_args(args: dict | None) -> tuple[str, dict, str]:
    args = args if isinstance(args, dict) else {}
    tool = str(args.get("tool_name") or args.get("tool") or "tool")
    inp = args.get("input")
    if not isinstance(inp, dict):
        inp = args.get("tool_input")
    if not isinstance(inp, dict):
        inp = {
            k: v
            for k, v in args.items()
            if k not in {"tool_name", "tool", "tool_use_id", "description"}
        }
    uid = str(args.get("tool_use_id") or args.get("toolUseId") or "")
    return tool, inp if isinstance(inp, dict) else {}, uid


def write_ask(job_dir: Path, tool_name: str, inp: dict, tool_use_id: str = "") -> dict:
    reply_path(job_dir).unlink(missing_ok=True)
    obj = {
        "tool_name": tool_name,
        "input": inp if isinstance(inp, dict) else {},
        "tool_use_id": tool_use_id,
        "preview": preview(tool_name, inp if isinstance(inp, dict) else {}),
        "asked_at": iso_now(),
    }
    _write_json(ask_path(job_dir), obj)
    return obj


def load_ask(job_dir: Path) -> dict | None:
    obj = _read_json(ask_path(job_dir))
    if not obj:
        return None
    if _read_json(reply_path(job_dir)):
        return None
    return obj


def write_reply(job_dir: Path, behavior: str, message: str = "", tool_use_id: str = "") -> dict:
    if behavior not in {"allow", "deny"}:
        raise ValueError("behavior must be allow or deny")
    pending = _read_json(ask_path(job_dir)) or {}
    obj = {
        "behavior": behavior,
        "message": message,
        "tool_use_id": tool_use_id or str(pending.get("tool_use_id") or ""),
        "answered_at": iso_now(),
    }
    _write_json(reply_path(job_dir), obj)
    return obj


def wait_reply(job_dir: Path, timeout: float | None = None) -> dict:
    """Wait for the parent allow/deny. Default is forever.

    Do not share RIG_TIMEOUT (the child's work budget). A slow parent allow
    must not auto-deny. Tests and operators may set timeout or RIG_ASK_TIMEOUT.
    """
    if timeout is None:
        raw = os.environ.get("RIG_ASK_TIMEOUT")
        if raw not in (None, ""):
            try:
                timeout = float(raw)
            except ValueError:
                timeout = None
    deadline = None if timeout is None else time.time() + max(0.0, timeout)
    want = str((_read_json(ask_path(job_dir)) or {}).get("tool_use_id") or "")
    while deadline is None or time.time() < deadline:
        reply = _read_json(reply_path(job_dir))
        if reply:
            got = str(reply.get("tool_use_id") or "")
            if not want or not got or got == want:
                return reply
        time.sleep(0.2)
    return {
        "behavior": "deny",
        "message": "parent did not answer the permission prompt",
        "tool_use_id": want,
        "answered_at": iso_now(),
    }


def decision_from_reply(reply: dict | None, original_input: dict) -> dict:
    inp = original_input if isinstance(original_input, dict) else {}
    if isinstance(reply, dict) and reply.get("behavior") == "allow":
        return {"behavior": "allow", "updatedInput": inp}
    message = ""
    if isinstance(reply, dict):
        message = str(reply.get("message") or "")
    return {
        "behavior": "deny",
        "message": message or "parent denied",
    }


def consume_ask(job_dir: Path) -> None:
    ask_path(job_dir).unlink(missing_ok=True)
    reply_path(job_dir).unlink(missing_ok=True)
