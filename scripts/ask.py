#!/usr/bin/env python3
"""Claude child permission prompts: ask.json in the job dir, parent answers."""
from __future__ import annotations

import json
import fcntl
import hashlib
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ASK_NAME = "ask.json"
REPLY_NAME = "ask-reply.json"
_guard = threading.RLock()
_requests = threading.local()


def _owned_requests():
    if not hasattr(_requests, "pending"):
        _requests.pending = {}
    return _requests.pending


@contextmanager
def _locked(job_dir: Path):
    """Serialize prompt replacement and reply comparison across threads/processes."""
    Path(job_dir).mkdir(parents=True, exist_ok=True)
    with _guard, (Path(job_dir) / ".ask.lock").open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _pending(job_dir: Path) -> dict | None:
    path = ask_path(job_dir)
    obj = _read_json(path)
    if obj and not obj.get("ask_id"):
        # Legacy requests get a stable identity for this exact file incarnation.
        try:
            stamp = path.stat()
        except OSError:
            return None
        value = json.dumps(obj, sort_keys=True) + f"/{stamp.st_ino}/{stamp.st_mtime_ns}"
        obj["ask_id"] = "legacy-" + hashlib.sha256(value.encode()).hexdigest()
    return obj


def _matches(reply: dict | None, pending: dict | None) -> bool:
    return bool(reply and pending and reply.get("ask_id") == pending.get("ask_id")
                and all((reply.get(key) or "") == (pending.get(key) or "")
                        for key in ("attempt_id", "reservation_id", "tool_use_id")))


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


def write_ask(
    job_dir: Path,
    tool_name: str,
    inp: dict,
    tool_use_id: str = "",
    preview_text: str = "",
) -> dict:
    shown = " ".join(str(preview_text).split())
    if shown:
        shown = shown if len(shown) <= 140 else shown[:139] + "…"
    else:
        shown = preview(tool_name, inp if isinstance(inp, dict) else {})
    obj = {
        "ask_id": uuid.uuid4().hex,
        "tool_name": tool_name,
        "input": inp if isinstance(inp, dict) else {},
        "tool_use_id": tool_use_id,
        "preview": shown,
        "asked_at": iso_now(),
    }
    with _locked(job_dir):
        meta = _read_json(Path(job_dir) / "meta.json") or {}
        obj.update({key: str(meta.get(key) or "") for key in ("attempt_id", "reservation_id")})
        reply_path(job_dir).unlink(missing_ok=True)
        _write_json(ask_path(job_dir), obj)
    _owned_requests()[str(Path(job_dir).resolve())] = obj
    return obj


def load_ask(job_dir: Path) -> dict | None:
    obj = _pending(job_dir)
    if not obj:
        return None
    if _matches(_read_json(reply_path(job_dir)), obj):
        return None
    return obj


def write_reply(job_dir: Path, behavior: str, message: str = "", tool_use_id: str = "",
                *, expected: dict | None = None) -> dict:
    if behavior not in {"allow", "deny"}:
        raise ValueError("behavior must be allow or deny")
    with _locked(job_dir):
        pending = _pending(job_dir)
        if not pending:
            raise ValueError("permission request is no longer pending")
        meta = _read_json(Path(job_dir) / "meta.json") or {}
        if any((pending.get(key) or "") != (meta.get(key) or "")
               for key in ("attempt_id", "reservation_id")):
            raise ValueError("permission request belongs to an earlier job attempt")
        if expected is not None and not _matches(expected, pending):
            raise ValueError("permission request changed; refresh before answering")
        if tool_use_id and tool_use_id != str(pending.get("tool_use_id") or ""):
            raise ValueError("permission tool request changed")
        previous = _read_json(reply_path(job_dir))
        if _matches(previous, pending):
            if previous.get("behavior") == behavior:
                return previous
            raise ValueError("permission request already answered differently")
        if meta and meta.get("status") not in {None, "running", "ask"}:
            raise ValueError("job is no longer waiting for permission")
        obj = {key: pending.get(key) or "" for key in
               ("ask_id", "attempt_id", "reservation_id", "tool_use_id")}
        obj.update(behavior=behavior, message=message, answered_at=iso_now())
        _write_json(reply_path(job_dir), obj)
        return obj


def wait_reply(job_dir: Path, timeout: float | None = None, *, expected: dict | None = None) -> dict:
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
    pending = expected or _owned_requests().get(str(Path(job_dir).resolve())) or _pending(job_dir)
    want = str((pending or {}).get("tool_use_id") or "")
    while deadline is None or time.time() < deadline:
        if not _matches(pending, _pending(job_dir)):
            break
        reply = _read_json(reply_path(job_dir))
        if _matches(reply, pending):
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
    with _locked(job_dir):
        expected = _owned_requests().pop(str(Path(job_dir).resolve()), None)
        if expected and not _matches(expected, _pending(job_dir)):
            return
        ask_path(job_dir).unlink(missing_ok=True)
        reply_path(job_dir).unlink(missing_ok=True)
