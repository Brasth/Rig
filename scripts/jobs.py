#!/usr/bin/env python3
"""List Rig jobs, decode child logs, write start/finish files, and render statusline text."""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ask as rig_ask  # noqa: E402
import harness as rig_harness  # noqa: E402
import inbox as rig_inbox  # noqa: E402

PREAMBLE_MARKERS = (
    "you are a worker, not the orchestrator",
    "do not spawn codex, grok, or claude",
    "do not spawn codex, grok, claude, or cursor",
    "do not spawn codex, grok, claude, cursor, opencode, omp, or pi",
    "do not spawn codex, grok, claude, cursor, opencode, omp, pi, or agy",
)
INPUT_KEYS = (
    "path",
    "target_file",
    "file_path",
    "command",
    "query",
    "pattern",
    "url",
    "prompt",
    "cwd",
)


def _kit_dir() -> Path:
    raw = (os.environ.get("RIG_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".rig").resolve()


def repo_root(start: str | None = None) -> Path:
    d = Path(start or os.getcwd()).resolve()
    try:
        kit = _kit_dir()
    except OSError:
        kit = None
    for p in [d, *d.parents]:
        if kit is not None and p == kit:
            continue
        harness = p / ".rig" / "harness.toml"
        if harness.is_file():
            try:
                marker = (p / ".rig").resolve()
            except OSError:
                marker = None
            if kit is None or marker != kit:
                return p
        if (p / ".git").is_dir():
            return p
    return d


def jobs_dir(repo: Path) -> Path:
    return repo / ".rig" / "jobs"


THREAD_ENV = (
    "RIG_THREAD",
    "GROK_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


def thread_file(repo: Path) -> Path:
    return repo / ".rig" / "thread"


def remember_thread(repo: Path, session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    path = thread_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(sid + "\n")
    tmp.replace(path)


def current_thread(repo: Path | None = None) -> str:
    for key in THREAD_ENV:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    if repo is None:
        repo = repo_root()
    path = thread_file(repo)
    if path.is_file():
        try:
            return path.read_text(errors="replace").strip().splitlines()[0].strip()
        except OSError:
            return ""
    return ""


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _first_line(text: str, limit: int = 88) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def task_from_brief(text: str, job_id: str) -> str:
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        if any(m in low for m in PREAMBLE_MARKERS):
            continue
        lines.append(s)
    if lines:
        return _first_line(" ".join(lines[:2]))
    slug = job_id.split("-", 1)
    # 20260908T071315Z-4994 is a timestamp-pid id, not a task slug.
    if len(slug) == 2 and slug[0][:6].isdigit() and not slug[1].isdigit():
        return slug[1].replace("-", " ")
    return job_id


def _short_path(val: str) -> str:
    s = val.strip()
    if "/" in s or s.startswith("."):
        parts = [p for p in s.split("/") if p]
        if len(parts) >= 2:
            return "/".join(parts[-2:])
        return parts[-1] if parts else s
    return s


def _input_detail(inp: object) -> str:
    if isinstance(inp, str) and inp.strip():
        return _first_line(_short_path(inp), 120)
    if not isinstance(inp, dict):
        return ""
    for key in INPUT_KEYS:
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return _first_line(_short_path(val), 120)
        if isinstance(val, list) and val:
            return _first_line(" ".join(str(x) for x in val[:4]), 120)
    for val in inp.values():
        if isinstance(val, str) and 1 < len(val) < 200:
            return _first_line(_short_path(val), 120)
    return ""


def _wrap_words(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    for word in words:
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


SETTINGS_NOISE = (
    "invalid permission rule",
    "mismatched parentheses",
    "managed settings contain invalid",
    "remaining valid policies are still enforced",
    "remote managed settings",
    "ensure all opening parentheses",
)


def _is_settings_noise(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in SETTINGS_NOISE)


def _cursor_tool_line(obj: dict) -> str | None:
    tc = obj.get("tool_call")
    if not isinstance(tc, dict):
        return None
    if obj.get("subtype") == "completed":
        return None
    for key, val in tc.items():
        if not isinstance(val, dict):
            continue
        name = str(key).replace("ToolCall", "").replace("toolCall", "")
        name = name[:1].lower() + name[1:] if name else "tool"
        args = val.get("args") if isinstance(val.get("args"), dict) else {}
        detail = _input_detail(args)
        return f"{name} {detail}".strip()
    return None


def _assistant_text(obj: dict) -> str | None:
    msg = obj.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        bits = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("text", "thinking") and block.get("text"):
                bits.append(str(block["text"]))
        if bits:
            return "".join(bits)
    if isinstance(content, str) and content.strip():
        return content
    return None


def _assistant_tools(obj: dict) -> list[str]:
    msg = obj.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    out: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                out.append(
                    f"{block.get('name', 'tool')} {_input_detail(block.get('input') or {})}".strip()
                )
    return out


def activity_from_event(obj: dict) -> str | None:
    kind = obj.get("type")
    if kind == "tool_call":
        cursor = _cursor_tool_line(obj)
        if cursor:
            return cursor
        name = str(obj.get("toolName") or obj.get("title") or obj.get("kind") or "tool")
        detail = _input_detail(obj.get("rawInput") or obj.get("input") or {})
        status = obj.get("status") or ""
        line = f"{name} {detail}".strip()
        if status and status != "completed":
            line += f" ({status})"
        return line
    if kind in ("text", "thought"):
        data = obj.get("data") or obj.get("text") or ""
        if isinstance(data, str) and data.strip():
            prefix = "think " if kind == "thought" else ""
            return prefix + _first_line(data, 140)
    if kind == "error":
        return "error: " + _first_line(str(obj.get("message") or obj), 140)
    if kind == "result":
        blob = obj.get("result") or obj.get("error") or ""
        if isinstance(blob, str) and blob.strip():
            return _first_line(blob, 140)
        if obj.get("is_error"):
            return "error: " + _first_line(str(obj.get("subtype") or "result"), 140)
        return None
    if kind == "assistant":
        tools = _assistant_tools(obj)
        if tools:
            return tools[-1]
        text = _assistant_text(obj)
        if text and text.strip():
            return _first_line(text, 140)
    return None


def _flush_stream(buf_kind: str | None, buf: list[str], out: list[str]) -> None:
    if not buf or not buf_kind:
        return
    text = "".join(buf).strip()
    buf.clear()
    if not text:
        return
    if buf_kind == "thought":
        wrapped = _wrap_words(" ".join(text.split()), 120)
        if not wrapped:
            return
        out.append("think  " + wrapped[0])
        for extra in wrapped[1:]:
            out.append("       " + extra)
        return
    for para in text.split("\n"):
        s = para.strip()
        if s:
            out.append(_first_line(s, 160))


def _decode_json_lines(json_lines: list[str]) -> list[str]:
    lines: list[str] = []
    buf_kind: str | None = None
    buf: list[str] = []
    for line in json_lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        if kind in ("system", "user", "stream_event", "tool_call_update", "end"):
            continue
        if kind in ("thought", "text"):
            data = obj.get("data") or obj.get("text") or ""
            if not isinstance(data, str) or not data:
                continue
            if buf_kind not in (None, kind):
                _flush_stream(buf_kind, buf, lines)
            buf_kind = kind
            buf.append(data)
            continue
        if kind == "assistant" and obj.get("timestamp_ms") is not None and not obj.get("model_call_id"):
            text = _assistant_text(obj) or ""
            if text:
                if buf_kind not in (None, "text"):
                    _flush_stream(buf_kind, buf, lines)
                buf_kind = "text"
                buf.append(text)
                continue
        _flush_stream(buf_kind, buf, lines)
        buf_kind = None
        if kind in ("tool_call", "error", "assistant", "result"):
            act = activity_from_event(obj)
            if act and (not lines or lines[-1] != act):
                lines.append(act)
            continue
        blob = obj.get("text") or obj.get("result") or obj.get("response") or ""
        if isinstance(blob, str) and blob.strip():
            lines.append(_first_line(blob, 160))
    _flush_stream(buf_kind, buf, lines)
    return lines


def decode_log_text(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    json_lines: list[str] = []
    text_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _is_settings_noise(line):
            continue
        if line.startswith("{"):
            json_lines.append(line)
        else:
            text_lines.append(line)
    if json_lines:
        decoded = _decode_json_lines(json_lines)
        if decoded:
            return decoded[-120:]
        last = json_lines[-1]
        if last.startswith("{") and not last.endswith("}"):
            return ["waiting for child json (buffered until exit)"]
        if len(json_lines) == 1:
            try:
                obj = json.loads(json_lines[0])
            except json.JSONDecodeError:
                return ["waiting for child json (buffered until exit)"]
            if isinstance(obj, dict):
                if obj.get("type") == "error":
                    return [activity_from_event(obj) or str(obj)]
                blob = obj.get("text") or obj.get("result") or obj.get("response") or ""
                if isinstance(blob, str) and blob.strip():
                    out = [
                        _first_line(para, 160)
                        for para in blob.split("\n")
                        if para.strip()
                    ]
                    return out[-80:] or [_first_line(blob, 160)]
                act = activity_from_event(obj)
                return [act] if act else []
    if text_lines:
        return [_first_line(line, 160) for line in text_lines][-80:]
    return []


def read_log_tail(path: Path, nbytes: int = 120_000) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("r", errors="replace") as fh:
        if size > nbytes:
            fh.seek(size - nbytes)
            fh.readline()
        return fh.read()


ACTIVITY_NAME = "activity.json"
ACTIVITY_CAP = 120


def activity_path(job_dir: Path) -> Path:
    return Path(job_dir) / ACTIVITY_NAME


def read_activity(job_dir: Path) -> dict | None:
    path = activity_path(job_dir)
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def activity_lines(job_dir: Path) -> list[str]:
    obj = read_activity(job_dir)
    if not obj:
        return []
    raw = obj.get("lines")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][-ACTIVITY_CAP:]


def write_activity(
    job_dir: Path,
    lines: list[str],
    source: str = "stdout",
    job_id: str = "",
) -> None:
    job_dir = Path(job_dir)
    cleaned = [str(item).strip() for item in lines if str(item).strip()][-ACTIVITY_CAP:]
    if not cleaned:
        return
    job_dir.mkdir(parents=True, exist_ok=True)
    obj = {
        "job_id": job_id or job_dir.name,
        "updated_at": iso_now(),
        "source": source or "stdout",
        "lines": cleaned,
    }
    path = activity_path(job_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def persist_activity(job_dir: Path, source: str = "stdout") -> list[str]:
    """Decode stdout.log into activity.json. Keep prior (child) lines if the log is empty."""
    job_dir = Path(job_dir)
    log_path = job_dir / "stdout.log"
    decoded = decode_log_text(read_log_tail(log_path)) if log_path.is_file() else []
    prior = activity_lines(job_dir)
    if decoded:
        extra = [line for line in prior if line not in decoded]
        write_activity(job_dir, extra + decoded, source=source)
        return activity_lines(job_dir)
    return prior


def patch_meta(job_dir: Path, **fields) -> dict:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    obj = _read_meta_dict(job_dir)
    if not obj.get("job_id"):
        obj["job_id"] = job_dir.name
    for key, val in fields.items():
        if val is None:
            continue
        obj[key] = val
    path = job_dir / "meta.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)
    return obj


def set_doing(job_dir: Path, text: str) -> str:
    line = _first_line(text, 160)
    if not line:
        raise ValueError("doing text required")
    patch_meta(job_dir, doing=line)
    prior = activity_lines(job_dir)
    if not prior or prior[-1] != line:
        write_activity(job_dir, prior + [line], source="child")
    return line


def add_note(job_dir: Path, text: str) -> str:
    line = _first_line(text, 160)
    if not line:
        raise ValueError("note text required")
    prior = activity_lines(job_dir)
    if not prior or prior[-1] != line:
        write_activity(job_dir, prior + [line], source="child")
    return line


def _prune_stdout_log(job_dir: Path) -> None:
    """Drop the raw log only when decoded activity was saved. Never delete the only copy."""
    job_dir = Path(job_dir)
    log = job_dir / "stdout.log"
    if not log.is_file():
        return
    if not activity_path(job_dir).is_file():
        return
    try:
        log.unlink()
    except OSError:
        pass


def _doing_from_activities(activities: list[str]) -> str:
    if not activities:
        return ""
    return next((a for a in reversed(activities) if not a.startswith("think")), activities[-1])


def job_display_state(job: dict, reservation: dict | None = None, verification: dict | None = None) -> str:
    """Project factual execution and assessment state without changing machine statuses."""
    reservation = reservation if reservation is not None else job.get("reservation") or {}
    verification = verification if verification is not None else job.get("verification_summary") or job.get("verification") or {}
    effective = job.get("effective") or job.get("status")
    if effective == "ask":
        return "needs-input"
    if effective == "cancelled":
        return "cancelled"
    if effective in {"fail", "timeout", "stale"} or verification.get("state") == "failed" or verification.get("acceptance") == "rejected":
        return "failed"
    if reservation.get("needs_reconciliation"):
        return "needs-input"
    if verification.get("state") == "verifying" and verification.get("active_check"):
        return "verifying"
    if effective == "running":
        return "verifying" if job.get("role") in {"review", "reviewer"} else "working"
    if effective == "reserved":
        return "reserved"
    if effective == "pending":
        return "queued"
    if (effective == "ok" and verification.get("state") == "verified"
            and verification.get("acceptance") == "accepted" and verification.get("freshness") == "current"):
        return "verified"
    return "completed-unverified"


def project_job(job: dict, repo: Path | None = None, *, refresh: bool = False, cache: dict | None = None) -> dict:
    """Validate only chosen subjects; callers share one hash cache per request."""
    import verification

    row = dict(job)
    assessment = row.get("verification_summary") or row.get("verification") or {}
    if refresh and row.get("dir"):
        root = repo if repo is not None else Path(row["dir"]).parents[2]
        assessment = verification.assessment(root, row, refresh=True, cache=cache)
    row["verification_summary"] = assessment
    row["verification"] = assessment
    state = job_display_state(row, verification=assessment)
    row["display_state"] = state
    observed_model = row.get("model_source") in {"selected", "observed"} and not row.get("model_inferred")
    row["display_model"] = (row.get("model") or "unknown") if observed_model else "unknown"
    reservation = row.get("reservation") or {}
    held = bool(reservation and reservation.get("stage") != "released")
    jid = row.get("job_id") or ""
    action = ""
    if row.get("effective") == "ask":
        pending = row.get("ask") or {}
        reason = pending.get("preview") or pending.get("tool_name") or "Permission required"
        action = f"rig job allow {jid} | rig job deny {jid}"
    elif reservation.get("needs_reconciliation"):
        reason = reservation.get("reconciliation_reason") or "Owner or process state needs reconciliation"
        if row.get("effective") == "cancelled" and held and not reservation.get("stopped"):
            reason = "stopping; files held — " + str(reason)
        action = f"rig job reconcile {jid}"
    elif state == "cancelled":
        reason = "stopping; files held" if held and not reservation.get("stopped") else "cancelled; files held" if held else "User cancelled execution"
    elif state == "failed":
        reason = str(assessment.get("reason") or "") if assessment.get("state") == "failed" else str(row.get("effective") or "Execution failed")
    elif state == "verifying":
        active = assessment.get("active_check")
        reason = (active.get("name") or active.get("check_id")) if isinstance(active, dict) else "Reviewer executing"
    elif state == "reserved":
        reason = "Waiting for execution; scope reserved"
    elif state == "working":
        reason = row.get("doing") or "Execution running"
    elif state == "verified":
        reason = f"Parent accepted current content ({assessment.get('method') or 'unknown method'})"
    else:
        reason = assessment.get("reason") or "Parent acceptance required"
        if assessment.get("state") == "verified" and assessment.get("freshness") != "current":
            reason = "Accepted content has not been checked in this view"
    if held and reservation.get("stopped") and not action:
        reason = str(reason) + "; files held"
    row["display_reason"] = str(reason or "")
    row["display_action"] = action
    # Provider provenance describes independence, never whether findings passed.
    independence = "unknown"
    if row.get("writer_job_id"):
        import route

        actual = row.get("model_source") in {"selected", "observed"} and not row.get("model_inferred")
        provider = route.provider_for(row.get("model") or "") if actual else ""
        writer_provider = row.get("writer_provider") or ""
        if provider and writer_provider:
            independence = "confirmed" if provider != writer_provider else "unavailable"
        elif row.get("independence") == "unavailable":
            independence = "unavailable"
    row["independence"] = independence
    row["review_completed"] = bool(row.get("writer_job_id") and row.get("effective") == "ok"
                                   and row.get("ownership_established") and row.get("execution_mode") in {"live", "native"})
    return row


def load_job(job_path: Path) -> dict | None:
    meta_path = job_path / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        obj = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    job_id = str(obj.get("job_id") or job_path.name)
    brief = ""
    brief_path = job_path / "brief.md"
    if brief_path.is_file():
        try:
            brief = brief_path.read_text(errors="replace")
        except OSError:
            brief = ""
    task = str(obj.get("task") or "").strip() or task_from_brief(brief, job_id)
    summary = str(obj.get("summary") or "").strip()
    if (not brief.strip()) and summary:
        task = _first_line(summary)
    pid = obj.get("pid")
    try:
        pid_i = int(pid) if pid not in (None, "") else None
    except (TypeError, ValueError):
        pid_i = None
    status = str(obj.get("status") or "unknown")
    alive = pid_alive(pid_i)
    effective = status
    pending_ask = rig_ask.load_ask(job_path) if status == "running" else None
    if status == "running" and pid_i and not alive:
        effective = "stale"
    elif pending_ask and (alive or not pid_i):
        effective = "ask"
    log_path = job_path / "stdout.log"
    activities = decode_log_text(read_log_tail(log_path)) if log_path.is_file() else []
    if not activities:
        activities = activity_lines(job_path)
    kind = str(obj.get("kind") or "")
    child_doing = str(obj.get("doing") or "").strip()
    pending_inbox = rig_inbox.load_inbox(job_path)
    doing = ""
    if effective == "ask" and pending_ask:
        doing = (
            f"ASK {pending_ask.get('preview') or pending_ask.get('tool_name') or 'tool'}  "
            f"→  rig job allow {job_id}"
        )
    elif effective == "running":
        if child_doing:
            doing = child_doing
        elif activities:
            doing = _doing_from_activities(activities)
        elif kind == "native":
            doing = "native spawn (no stdout.log — finish with rig job finish)"
        else:
            doing = "running (no log yet)"
    elif activities:
        doing = _doing_from_activities(activities)
    elif summary:
        doing = _first_line(summary)
    mtime = 0.0
    try:
        mtime = job_path.stat().st_mtime
    except OSError:
        pass
    started_at = str(obj.get("started_at") or "")
    ended_at = str(obj.get("ended_at") or "")
    stored_elapsed = obj.get("elapsed_s")
    try:
        stored_i = int(stored_elapsed) if stored_elapsed not in (None, "") else None
    except (TypeError, ValueError):
        stored_i = None
    computed = elapsed_seconds(started_at, ended_at, live=effective in {"running", "ask"})
    elapsed_i = computed if computed is not None else stored_i
    model = str(obj.get("model") or "").strip()
    effort = str(obj.get("effort") or "").strip()
    executor = str(obj.get("executor_kind") or "")
    if str(obj.get("role") or "") == "parent" or obj.get("worker") == "parent":
        executor = "parent"
    elif not executor:
        executor = "native_child" if kind == "native" else "wrapper"
    model_source = str(obj.get("model_source") or "unknown")
    inferred = bool(obj.get("model_inferred", False))
    if not model and executor != "parent":
        try:
            import route as rig_route

            route_kind = rig_route.classify(str(obj.get("role") or "implement"), "")
            model, effort = rig_route.model_for(str(obj.get("worker") or "codex"), route_kind)
            inferred = True
        except Exception:
            model, effort = "", ""
    job = {
        "job_id": job_id,
        "worker": str(obj.get("worker") or "?"),
        "role": str(obj.get("role") or ""),
        "kind": kind,
        "status": status,
        "effective": effective,
        "pid": pid_i,
        "alive": alive,
        "session_id": str(obj.get("session_id") or ""),
        "thread": str(obj.get("thread") or ""),
        "model": model,
        "effort": effort,
        "model_inferred": inferred,
        "model_source": model_source,
        "executor_kind": executor,
        "execution_mode": str(obj.get("execution_mode") or "unknown"),
        "writer_job_id": str(obj.get("writer_job_id") or ""),
        "writer_snapshot_id": str(obj.get("writer_snapshot_id") or ""),
        "writer_provider": str(obj.get("writer_provider") or ""),
        "independence": str(obj.get("independence") or "unknown"),
        "provider": str(obj.get("provider") or ""),
        "provider_source": str(obj.get("provider_source") or "unknown"),
        "reservation_id": str(obj.get("reservation_id") or ""),
        "attempt_id": str(obj.get("attempt_id") or ""),
        "ownership_established": obj.get("ownership_established") is True,
        "access": str(obj.get("access") or ""),
        "queue_id": str(obj.get("queue_id") or ""),
        "native_agent_id": str(obj.get("native_agent_id") or ""),
        "open": str(obj.get("open") or ""),
        "watch": str(obj.get("watch") or ""),
        "summary": str(obj.get("summary") or ""),
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_s": elapsed_i,
        "task": task,
        "doing": doing,
        "ask": pending_ask,
        "inbox": pending_inbox,
        "activities": activities,
        "dir": str(job_path),
        "log": str(log_path),
        "log_pruned": status == "ok" and not log_path.is_file(),
        "mtime": mtime,
        "files": [
            x
            for x in (obj.get("files") or [])
            if isinstance(x, str) and x
        ]
        if isinstance(obj.get("files"), list)
        else [],
    }
    import verification

    job["verification"] = verification.assessment(job_path.parents[2], job)
    job["verification_summary"] = job["verification"]
    return project_job(job)


class JobsSnapshot(list):
    """One enumeration, including directory counts for legacy status output."""

    def __init__(self, rows=(), *, directory_count: int = 0, all_jobs=None, real_job_count=None, reservations=None):
        super().__init__(rows)
        self.directory_count = directory_count
        self.all_jobs = self if all_jobs is None else all_jobs
        self.real_job_count = len(self) if real_job_count is None else real_job_count
        self.reservations = reservations


def _reserved_job(repo: Path, reservation: dict) -> dict:
    jid = reservation.get("job_id") or "reservation-" + reservation["reservation_id"]
    folder = jobs_dir(repo) / jid
    stamp = parse_job_ts(reservation.get("updated_at") or reservation.get("created_at") or "")
    return {
        "job_id": jid, "worker": reservation.get("worker") or "unknown", "role": reservation.get("role") or "worker",
        "status": "reserved", "effective": "reserved", "task": "Reserved queue item " + reservation["queue_id"] if reservation.get("queue_id") else "Waiting for execution registration",
        "doing": reservation.get("reconciliation_reason") or "", "model": reservation.get("model") or "", "effort": "",
        "model_source": "selected" if reservation.get("model") else "unknown", "model_inferred": False,
        "executor_kind": (reservation.get("owner") or {}).get("kind", "unknown"), "execution_mode": "unknown",
        "dir": str(folder), "log": str(folder / "stdout.log"), "mtime": stamp.timestamp() if stamp else 0,
        "files": reservation.get("declared_files") or [], "reservation": reservation, "reservation_only": True,
        "reservation_id": reservation["reservation_id"], "attempt_id": reservation["attempt_id"],
        "queue_id": reservation.get("queue_id") or "", "access": reservation.get("access") or "",
        "thread": (reservation.get("owner") or {}).get("session_id") or "", "session_id": "", "open": "",
        "pid": None, "alive": False, "started_at": "", "ended_at": "", "summary": "", "activities": [],
        "verification_summary": {"state": "unknown", "reason": "execution_not_started", "freshness": "not_checked"},
    }


def list_jobs(repo: Path, thread: str | None = None) -> list[dict]:
    root = jobs_dir(repo)
    jobs = JobsSnapshot()
    for path in root.iterdir() if root.is_dir() else []:
        if path.is_dir():
            jobs.directory_count += 1
            job = load_job(path)
            if job:
                jobs.append(job)
    jobs.real_job_count = len(jobs)
    import admission

    jobs.reservations = admission.list_reservations(repo)
    by_id = {job["job_id"]: job for job in jobs}
    for reservation in jobs.reservations:
        job = by_id.get(reservation.get("job_id"))
        if job is not None:
            if job.get("attempt_id") == reservation.get("attempt_id"):
                job["reservation"] = reservation
        else:
            jobs.append(_reserved_job(repo, reservation))
    jobs[:] = [project_job(job) for job in jobs]
    def _rank(job: dict) -> int:
        if job["effective"] == "ask":
            return 0
        if job["effective"] in {"running", "reserved"} or job.get("reservation"):
            return 1
        return 2

    jobs.sort(key=lambda j: (_rank(j), -j["mtime"]))
    if thread:
        jobs = JobsSnapshot(
            [j for j in jobs if j.get("thread") == thread],
            directory_count=jobs.directory_count, all_jobs=jobs,
            real_job_count=jobs.real_job_count, reservations=jobs.reservations,
        )
    return jobs


def _job_path(repo: Path, name: str) -> Path:
    if not name or name in {".", ".."} or not JOB_ID_RE.fullmatch(name):
        raise SystemExit(f"rig: invalid job id '{name}'")
    root = jobs_dir(repo).resolve()
    path = root / name
    if path.resolve().parent != root:
        raise SystemExit(f"rig: invalid job path '{name}'")
    return path


def _load_selected_job(path: Path) -> dict:
    job = load_job(path)
    if job is not None:
        if job.get("reservation_id"):
            import admission

            reservation = admission.get_reservation(path.parents[2], job["reservation_id"])
            if reservation and reservation.get("job_id") == job["job_id"] and reservation.get("attempt_id") == job.get("attempt_id"):
                job["reservation"] = reservation
        return project_job(job)
    if path.is_dir() and not (path / "meta.json").is_file():
        reason = "never started (brief.md only, no meta.json)" if (path / "brief.md").is_file() else "has a folder but no meta.json"
        raise SystemExit(f"rig: job {path.name} {reason}. Launch with run-worker.sh, then wait.")
    if path.is_dir():
        raise SystemExit(f"rig: job {path.name} has invalid meta.json")
    raise SystemExit(f"rig: no such job {path.name}")


def resolve_job_paths(repo: Path, names: list[str]) -> list[Path]:
    """Pin exact paths once; partial names share one directory-name snapshot."""
    if not names:
        return [Path(resolve_job(repo, None)["dir"])]
    paths = []
    candidates = None
    for raw in names:
        name = str(raw).strip()
        path = _job_path(repo, name)
        if not path.is_dir():
            if candidates is None:
                root = jobs_dir(repo)
                candidates = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
            matches = [p for p in candidates if name in p.name]
            if len(matches) > 1:
                raise SystemExit(f"rig: ambiguous job id '{name}': " + ", ".join(sorted(p.name for p in matches)))
            if not matches:
                raise SystemExit(f"rig: no such job {name}")
            path = _job_path(repo, matches[0].name)
        if path not in paths:
            paths.append(path)
    return paths


def resolve_job(repo: Path, job_id: str | None) -> dict:
    if job_id:
        return _load_selected_job(resolve_job_paths(repo, [job_id])[0])
    jobs = list_jobs(repo)
    for job in jobs:
        if job["effective"] == "ask":
            return job
    for job in jobs:
        if job["effective"] == "running":
            return job
    if jobs:
        return jobs[0]
    raise SystemExit("rig: no jobs")


def answer_pending(job: dict, behavior: str, message: str = "") -> str:
    if behavior not in {"allow", "deny"}:
        return "rig: behavior must be allow or deny"
    if job.get("effective") != "ask":
        return f"rig: job {job['job_id']} is not waiting (status {job.get('effective')})"
    pending = job.get("ask") if isinstance(job.get("ask"), dict) else {}
    rig_ask.write_reply(
        Path(job["dir"]),
        behavior,
        message,
        str(pending.get("tool_use_id") or ""),
    )
    preview = str(pending.get("preview") or pending.get("tool_name") or "tool")
    return f"{behavior} {job['job_id']}  {preview}"


def write_cancel_flag(job_dir: Path, reason: str) -> None:
    path = job_dir / "cancel.json"
    job_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"reason": reason, "at": iso_now()}, indent=2) + "\n")
    tmp.replace(path)


def cancel_job(repo: Path, job_id: str | None, reason: str = "parent", *, job_path: Path | None = None) -> str:
    """Abort a live job. Wrapper kill_tree + status cancelled. Does not touch the queue."""
    import admission

    with admission.transaction(repo):
        return _cancel_job_locked(repo, job_id, reason, job_path=job_path)


def _cancel_job_locked(repo: Path, job_id: str | None, reason: str, *, job_path: Path | None = None) -> str:
    job = _load_selected_job(job_path) if job_path is not None else resolve_job(repo, job_id)
    jid = str(job["job_id"])
    eff = str(job.get("effective") or "")
    if eff == "cancelled":
        return f"cancelled {jid} (already)"
    if eff in {"ok", "fail", "timeout"}:
        return f"rig: job {jid} already {eff}"
    job_dir = Path(job["dir"])
    write_cancel_flag(job_dir, reason)
    meta = _read_meta_dict(job_dir)
    reservation = job.get("reservation") or {}
    owner = reservation.get("owner") or {}
    # Signal the wrapper so its trap terminates the child tree and restores
    # launcher state. meta.pid may identify the child, never the wrapper.
    pid = owner.get("pid")
    matching_wrapper = (
        owner.get("kind") == "wrapper" and isinstance(pid, int) and pid > 0
        and reservation.get("attempt_id") == meta.get("attempt_id")
        and owner.get("start_id")
    )
    if matching_wrapper:
        import admission

        current = admission.process_identity(pid)
        matching_wrapper = current.get("start_id") == owner["start_id"]
    if matching_wrapper and pid_alive(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError, TypeError):
            pass
    worker = str(meta.get("worker") or job.get("worker") or "parent")
    role = str(meta.get("role") or job.get("role") or "worker")
    started = str(meta.get("started_at") or job.get("started_at") or iso_now())
    summary = f"cancelled ({reason})"
    write_job_files(
        job_dir,
        jid,
        worker,
        role,
        "cancelled",
        130,
        started,
        iso_now(),
        summary,
        kind=str(meta.get("kind") or ""),
        thread=str(meta.get("thread") or ""),
        model_source=str(meta.get("model_source") or "unknown"),
        capture_evidence=False,
        legacy_cancel_requested=not bool(meta.get("reservation_id")),
    )
    write_state(repo, jid, worker, "cancelled", summary)
    return f"cancelled {jid}"


def format_wait(job: dict, others: list[dict] | None = None) -> str:
    job = project_job(job)
    job_id = job["job_id"]
    eff = job.get("effective")
    if eff == "ask":
        pending = job.get("ask") if isinstance(job.get("ask"), dict) else {}
        preview = str(pending.get("preview") or pending.get("tool_name") or "tool")
        lines = [
            f"ASK {job_id}",
            f"agent   {job.get('worker') or '?'}",
            f"preview {preview}",
            f"answer  rig job allow {job_id}",
            f"        rig job deny {job_id}",
            "You must answer this prompt so the child can continue.",
            "Do not kill this job. Do not spawn another worker for this task.",
        ]
    elif eff == "running":
        doing = job.get("doing") or "running"
        lines = [
            f"RUNNING {job_id}",
            f"display {job['display_state']} — {job['display_reason']}",
            f"agent   {job.get('worker') or '?'}",
            f"doing   {doing}",
            f"next    rig job wait {job_id}",
        ]
    elif eff == "cancelled":
        lines = [
            f"CANCELLED {job_id}",
            f"display cancelled — {job['display_reason']}",
            f"agent   {job.get('worker') or '?'}",
            "Do not re-pick. User aborted this wait.",
        ]
    else:
        lines = [format_show(job)]
    if others:
        for other in others:
            oid = other.get("job_id")
            if not oid or oid == job_id:
                continue
            lines.append(f"also    {str(other.get('effective') or '').upper()} {oid}")
    return "\n".join(lines)


def format_wait_many(jobs: list[dict]) -> str:
    if len(jobs) == 1:
        return format_wait(jobs[0])
    rows = [f"WAIT {len(jobs)} jobs"]
    cache = {}
    for job in jobs:
        job = project_job(job, refresh=job.get("effective") == "ok", cache=cache)
        rows.append(
            f"{job['display_state']:<20} {job.get('job_id')}  "
            f"{job.get('worker') or '?'}"
        )
        rows.append(f"          {job['display_reason']}")
        if job.get("doing") and job.get("effective") == "running":
            rows.append(f"          doing  {job['doing']}")
    return "\n".join(rows)


def _normalize_wait_ids(
    job_id: str | None,
    ids: str | list | tuple | set | None = None,
) -> list[str]:
    out: list[str] = []

    def add(raw) -> None:
        if raw is None or raw is False:
            return
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                add(item)
            return
        text = str(raw).strip()
        if not text:
            return
        for part in text.split(","):
            name = part.strip()
            if name and name not in out:
                out.append(name)

    add(ids)
    add(job_id)
    return out


def wait_job(
    repo: Path,
    job_id: str | None,
    timeout: float | None = None,
    on_tick: Callable | None = None,
    ids: str | list | tuple | set | None = None,
    resolved_paths: list[Path] | None = None,
) -> tuple[int, str]:
    timeout_s: float | None
    if timeout is None:
        timeout_s = None
    else:
        try:
            timeout_s = float(timeout)
        except (TypeError, ValueError):
            timeout_s = None
    deadline = None if timeout_s is None else time.time() + max(0.0, timeout_s)
    last_tick: dict[str, tuple[str, str]] = {}
    names = _normalize_wait_ids(job_id, ids)
    paths = resolve_job_paths(repo, names) if resolved_paths is None else resolved_paths
    if not paths:
        raise SystemExit("rig: no jobs")
    while True:
        jobs = [_load_selected_job(path) for path in paths]
        if on_tick is not None:
            for job in jobs:
                key = (str(job.get("effective") or ""), str(job.get("doing") or ""))
                jid = str(job.get("job_id") or "")
                prev = last_tick.get(jid)
                if prev is None:
                    if job.get("effective") == "running":
                        on_tick(job)
                        last_tick[jid] = key
                elif key != prev:
                    on_tick(job)
                    last_tick[jid] = key
        asks = [job for job in jobs if job.get("effective") == "ask"]
        if asks:
            return 2, format_wait(asks[0], others=jobs)
        live = [job for job in jobs if job.get("effective") == "running"]
        if not live:
            cancelled = [job for job in jobs if job.get("effective") == "cancelled"]
            fails = [
                job
                for job in jobs
                if job.get("effective") in {"fail", "timeout", "stale"}
            ]
            text = format_wait_many(jobs)
            if cancelled and not fails:
                if len(jobs) == 1:
                    return 130, format_wait(jobs[0])
                return 130, text
            return (1 if fails else 0), text
        if deadline is not None and time.time() >= deadline:
            running = live[0]
            return 124, format_wait(running, others=jobs)
        time.sleep(0.4)


def format_table(jobs: list[dict], repo: Path | None = None, *, jobs_snapshot=None) -> str:
    if not jobs:
        rows = ["no jobs  (cross-CLI children and recorded cheap workers show up here)"]
    else:
        rows = ["STATE                 AGENT    ROLE       JOB                              TASK"]
        for job in jobs:
            job = project_job(job)
            rows.append(
                f"{job['display_state']:<21} {job['worker']:<8} {job['role']:<10} {job['job_id']:<32} {job['task']}"
            )
            extras = [f"          {job['display_reason']}"]
            if job.get("display_action"):
                extras.append(f"          action {job['display_action']}")
            assessment = job.get("verification") or job.get("verification_summary") or {}
            if assessment:
                state = assessment.get("state", "unknown")
                if state == "verified" and assessment.get("freshness") == "not_checked":
                    state = "unverified (content not checked)"
                extras.append(f"          verification  {state}")
            if job.get("model") or job.get("effort") or job.get("effective") in {"running", "reserved"}:
                extras.append(
                    f"          model  {job['display_model']}   reasoning {job.get('effort') or '-'}"
                )
            if job.get("elapsed_s") is not None:
                extras.append(f"          elapsed  {format_elapsed(int(job['elapsed_s']))}")
            if job.get("thread"):
                extras.append(f"          thread {job['thread']}")
            if job["doing"]:
                extras.append(f"          doing  {job['doing']}")
            if job.get("inbox") and job["effective"] in {"running", "ask"}:
                extras.append("          inbox  pending")
            if job["effective"] == "ask":
                extras.append(
                    f"          answer rig job allow {job['job_id']}  |  rig job deny {job['job_id']}"
                )
            if job["effective"] == "running" and job.get("open"):
                extras.append(f"          open   {job['open']}")
            if job["effective"] in {"running", "ask"}:
                extras.append(f"          log    rig job log {job['job_id']} -f")
            rows.extend(extras)
        running = sum(1 for j in jobs if j["effective"] == "running")
        rows.append(f"\n{running} running / {len(jobs)} jobs    rig tui    rig job log [id] -f")
    if repo is not None:
        import work_queue as rig_queue

        rows.append("")
        snapshot = jobs_snapshot if jobs_snapshot is not None else getattr(jobs, "all_jobs", jobs)
        live = rig_queue.slot_count(repo, jobs_snapshot=snapshot)
        rows.append(rig_queue.format_block(repo, live=live, jobs_snapshot=snapshot))
    return "\n".join(rows)


def format_show(job: dict, log_lines: int = 24) -> str:
    import change_evidence
    import verification

    job_dir = Path(job["dir"])
    repo = job_dir.parents[2]
    hash_cache = {}
    job = project_job(job, repo, refresh=True, cache=hash_cache)
    assessment = job["verification_summary"]
    lines = [
        f"job     {job['job_id']}",
        f"agent   {job['worker']}",
        f"role    {job['role'] or '-'}",
        f"status  {job['effective']}",
        f"display {job['display_state']} — {job['display_reason']}",
    ]
    if job.get("display_action"):
        lines.append(f"action  {job['display_action']}")
    lines.append(f"independence  {job['independence']} (review {'completed' if job['review_completed'] else 'not completed'})")
    lines.append(f"model      {job['display_model']}")
    lines.append(f"reasoning  {job.get('effort') or '-'}")
    lines.append(f"execution  {job.get('execution_mode') or 'unknown'}")
    lines.append(f"verification  {assessment.get('state', 'unknown')} ({assessment.get('reason') or assessment.get('acceptance', 'pending')})")
    try:
        snapshot = change_evidence.snapshot(repo, job.get("files") or [], cache=hash_cache)
        lines.append(f"snapshot_id  {snapshot['snapshot_id']}")
    except (OSError, ValueError):
        lines.append("snapshot_id  unavailable (declare a concrete file scope)")
    for name in ("change-evidence.json", "requirements.json", "verification.json", "checks"):
        if (job_dir / name).exists():
            lines.append(f"evidence  {job_dir / name}")
    claims = verification.worker_claims(job_dir)
    if claims.get("available"):
        lines.append("worker claims (not parent verification)  " + json.dumps(claims.get("claims", {}), ensure_ascii=True))
    lines += [
        f"task    {job['task']}",
    ]
    if job["doing"]:
        lines.append(f"doing   {job['doing']}")
    if job.get("inbox"):
        preview = str((job["inbox"] or {}).get("text") or "").strip()
        if len(preview) > 120:
            preview = preview[:119] + "…"
        lines.append(f"inbox   {preview or 'pending'}")
        lines.append("        child pulls with rig_job_inbox (not ASK)")
    if job.get("effective") == "ask":
        lines.append(f"answer  rig job allow {job['job_id']}")
        lines.append(f"        rig job deny {job['job_id']}")
        lines.append("keep    do not kill this job; do not spawn a replacement")
    if job["pid"]:
        lines.append(f"pid     {job['pid']} ({'alive' if job['alive'] else 'dead'})")
    if job.get("thread"):
        lines.append(f"thread  {job['thread']}")
    if job["session_id"]:
        lines.append(f"session {job['session_id']}")
    if job["open"]:
        lines.append(f"open    {job['open']}")
    if job["started_at"]:
        lines.append(f"start   {job['started_at']}")
    if job["ended_at"]:
        lines.append(f"end     {job['ended_at']}")
    if job.get("elapsed_s") is not None:
        lines.append(f"elapsed  {format_elapsed(int(job['elapsed_s']))}")
    if job["summary"] and job["effective"] != "running":
        lines.append(f"summary {_first_line(job['summary'], 200)}")
    lines.append(f"dir     {job['dir']}")
    acts = job.get("activities") or []
    if acts:
        lines.append("")
        lines.append("log")
        for act in acts[-log_lines:]:
            lines.append(f"  {act}")
    else:
        lines.append("")
        if job.get("log_pruned"):
            lines.append("log     pruned after success (summary kept in result.json)")
        else:
            lines.append("log     (empty — json children buffer until exit; streaming-json writes live)")
    lines.append("")
    lines.append(f"follow  rig job log {job['job_id']} -f")
    lines.append("board   rig tui")
    return "\n".join(lines)


def format_log(job: dict, n: int = 40) -> str:
    acts = job.get("activities") or decode_log_text(read_log_tail(Path(job["log"])))
    if not acts:
        if job["effective"] == "ask":
            return f"ASK — parent must answer: rig job allow {job['job_id']}  |  rig job deny {job['job_id']}"
        if job["effective"] == "running":
            return "log empty (child still running; json is buffered until exit)"
        if job.get("log_pruned"):
            return "log pruned after success"
        return "log empty"
    return "\n".join(acts[-n:])


JOB_WORKERS = frozenset(
    {"grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy", "parent"}
)
JOB_STATUSES = frozenset({"ok", "fail", "timeout", "running", "cancelled"})
JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


JOB_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime(JOB_TS_FMT)


def parse_job_ts(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except ValueError:
        return None


def elapsed_seconds(started_at: str, ended_at: str = "", *, live: bool = False) -> int | None:
    start = parse_job_ts(started_at)
    if start is None:
        return None
    end = parse_job_ts(ended_at)
    if end is None:
        if not live:
            return None
        end = datetime.now(timezone.utc)
    return max(0, int((end - start).total_seconds()))


def format_elapsed(seconds: int) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    return f"{minutes}m{secs}s"


def new_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{os.getpid()}"


def _read_meta_dict(job_dir: Path) -> dict:
    path = job_dir / "meta.json"
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def write_state(repo: Path, job: str, worker: str, status: str, summary: str) -> None:
    path = repo / ".rig" / "STATE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# STATE\n\n"
        "Overwritten each run.\n\n"
        f"- last_job: {job}\n"
        f"- worker: {worker}\n"
        f"- status: {status}\n"
        f"- summary: {summary}\n"
    )


def write_job_files(
    job_dir: Path,
    job_id: str,
    worker: str,
    role: str,
    status: str,
    exit_code: int,
    started_at: str,
    ended_at: str,
    summary: str,
    kind: str = "native",
    thread: str = "",
    model: str = "",
    effort: str = "",
    files: list | None = None,
    executor_kind: str = "",
    model_source: str = "",
    execution_mode: str = "",
    writer_job_id: str = "",
    writer_snapshot_id: str = "",
    reservation: dict | None = None,
    capture_evidence: bool = True,
    legacy_cancel_requested: bool | None = None,
) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    old = _read_meta_dict(job_dir)
    executor_kind = executor_kind or str(old.get("executor_kind") or "")
    if worker == "parent" or role == "parent":
        executor_kind = "parent"
    if not executor_kind:
        executor_kind = "native_child" if kind == "native" else "wrapper"
    if executor_kind not in {"parent", "native_child", "wrapper"}:
        raise SystemExit("rig job: executor_kind must be parent|native_child|wrapper")
    supplied_model = bool(model.strip())
    inferred = bool(old.get("model_inferred", False)) and not supplied_model
    model = model.strip() or str(old.get("model") or "")
    effort = effort.strip() or str(old.get("effort") or "")
    if not model and executor_kind != "parent":
        try:
            import route as rig_route

            route_kind = rig_route.classify(role or "implement", "")
            if old:
                model, effort = rig_route.model_for(worker or "codex", route_kind)
            else:
                model, effort = rig_route.resolved_model_for(worker or "codex", route_kind)
            inferred = bool(old)
        except Exception:
            model, effort = model or "", effort or ""
    if not model_source:
        if old and not supplied_model:
            model_source = str(old.get("model_source") or "unknown")
        elif executor_kind == "parent":
            model_source = "observed" if supplied_model else str(old.get("model_source") or "unknown")
        else:
            model_source = "selected" if model else "unknown"
    if not model:
        effort = ""
    if model and executor_kind != "parent" and status == "running" and (supplied_model or not old):
        import route as rig_route

        model_error = rig_route.assert_child_model(model)
        if model_error:
            raise SystemExit(model_error)
    if not execution_mode:
        execution_mode = str(old.get("execution_mode") or "unknown") if old else ("parent" if executor_kind == "parent" else "native")
    obj = {
        "job_id": job_id,
        "worker": worker,
        "role": role,
        "status": status,
        "exit_code": int(exit_code),
        "started_at": started_at,
        "ended_at": ended_at,
        "summary": summary,
        "files_changed": [],
        "next": "",
        "kind": kind or "native",
        "model": model,
        "effort": effort,
        "executor_kind": executor_kind,
        "model_source": model_source,
        "model_inferred": inferred,
        "execution_mode": execution_mode,
        "writer_job_id": writer_job_id or str(old.get("writer_job_id") or ""),
        "writer_snapshot_id": writer_snapshot_id or str(old.get("writer_snapshot_id") or ""),
    }
    for key in ("thread", "session_id", "pid", "open", "watch", "kind", "model", "effort", "doing", "writer_provider", "independence"):
        if not obj.get(key) and old.get(key) not in (None, ""):
            obj[key] = old[key]
    listed = files
    if listed is None:
        raw = old.get("files")
        listed = raw if isinstance(raw, list) else []
        if not old and status == "running":
            listed = job_files_input()
    obj["files"] = [x for x in listed if isinstance(x, str) and x]
    if legacy_cancel_requested is not None or old.get("legacy_cancel_requested"):
        obj["legacy_cancel_requested"] = bool(legacy_cancel_requested if legacy_cancel_requested is not None else old["legacy_cancel_requested"])
    for key in ("reservation_id", "attempt_id", "access", "owner", "ownership_established", "queue_id", "native_agent_id"):
        if key in old:
            obj[key] = old[key]
    if reservation:
        for key in ("reservation_id", "attempt_id", "access", "owner", "queue_id"):
            if key in reservation:
                obj[key] = reservation[key]
        obj["ownership_established"] = True
        obj["native_agent_id"] = (reservation.get("owner") or {}).get("native_agent_id", "")
    if model and not inferred and model_source in {"selected", "observed"}:
        import route as rig_route

        obj["provider"] = rig_route.provider_for(model)
        obj["provider_source"] = model_source if obj["provider"] else "unknown"
    else:
        obj["provider"] = ""
        obj["provider_source"] = "unknown"
    if obj.get("writer_job_id") and not obj.get("writer_provider"):
        import route as rig_route

        writer = _read_meta_dict(_job_path(job_dir.parents[2], obj["writer_job_id"]))
        if writer.get("model_source") in {"selected", "observed"} and not writer.get("model_inferred"):
            obj["writer_provider"] = rig_route.provider_for(writer.get("model") or "")
        elif writer.get("provider_source") == "explicit":
            obj["writer_provider"] = writer.get("provider") if writer.get("provider") in rig_route.PROVIDERS else ""
    if capture_evidence and executor_kind in {"native_child", "parent"}:
        import change_evidence

        repo = job_dir.parents[2]
        evidence_path = job_dir / "change-before.json"
        if status == "running" and not evidence_path.exists():
            change_evidence.begin(repo, job_dir, obj["files"])
        elif status in {"ok", "fail", "timeout", "cancelled"} and evidence_path.exists():
            evidence = change_evidence.finish(repo, job_dir)
            obj["files_changed"] = evidence.get("scoped_changed_paths", [])
    if thread:
        obj["thread"] = thread
    live = (not (ended_at or "").strip()) and status in {"running", "ask"}
    elapsed = elapsed_seconds(started_at, ended_at, live=live)
    if elapsed is not None:
        obj["elapsed_s"] = elapsed
    blob = json.dumps(obj, indent=2) + "\n"
    for name in ("meta.json", "result.json"):
        path = job_dir / name
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(blob)
        tmp.replace(path)


def _require_harness(repo: Path) -> None:
    harness = repo / ".rig" / "harness.toml"
    if not harness.is_file():
        raise SystemExit(f"rig job: missing {harness} — run: rig init")


def _allocate_job_id(job_id: str) -> str:
    job_id = (job_id or "").strip()
    if not job_id:
        job_id = new_job_id()
    if job_id in {".", ".."} or not JOB_ID_RE.fullmatch(job_id):
        raise SystemExit(f"rig job: invalid job id '{job_id}'")
    return job_id


def _validate_worker(worker: str) -> str:
    if worker not in JOB_WORKERS:
        raise SystemExit(
            "rig job: worker must be grok|codex|claude|cursor|opencode|omp|pi|agy|parent"
        )
    return worker


def _validate_status(status: str) -> str:
    if status not in JOB_STATUSES:
        raise SystemExit("rig job: status must be ok|fail|timeout|running|cancelled")
    return status


def _resolve_worker(worker: str, live: str, preferred: str, meta: dict) -> str:
    worker = (worker or "").strip()
    if not worker:
        worker = str(meta.get("worker") or "").strip()
    if not worker:
        worker = (live or preferred or "").strip()
    return _validate_worker(worker)


def _job_thread(repo: Path) -> str:
    return (os.environ.get("RIG_THREAD") or "").strip() or current_thread(repo)


def _started_at(job_dir: Path, meta: dict, now: str) -> str:
    stamp = job_dir / "started_at"
    if stamp.is_file():
        try:
            text = stamp.read_text(errors="replace").strip().splitlines()
            if text and text[0].strip():
                return text[0].strip()
        except OSError:
            pass
    started = str(meta.get("started_at") or "").strip()
    return started or now


def _exit_code(status: str) -> int:
    if status == "ok":
        return 0
    if status == "timeout":
        return 124
    if status == "cancelled":
        return 130
    return 1


def _finish_text(job_id: str, worker: str, role: str, status: str, job_dir: Path) -> str:
    return f"job {job_id} worker={worker} role={role} status={status}\n{job_dir / 'result.json'}"


def job_files_input(files: list | None = None) -> list[str]:
    if files is None:
        raw = os.environ.get("RIG_JOB_FILES_JSON", "")
        if raw:
            try:
                files = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SystemExit("rig job: RIG_JOB_FILES_JSON must be a JSON string array") from exc
        else:
            files = (os.environ.get("RIG_JOB_FILES") or "").replace(",", " ").split()
    if not isinstance(files, list) or any(not isinstance(path, str) or not path.strip() for path in files):
        raise SystemExit("rig job: files must be a JSON array of nonempty paths")
    return list(dict.fromkeys(files))


def _native_parent_only() -> None:
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise SystemExit("rig job: native lifecycle and acceptance are parent-only")


def start_job(
    repo: Path,
    worker: str = "",
    role: str = "worker",
    job_id: str = "",
    summary: str = "",
    live: str = "",
    preferred: str = "",
    model: str = "",
    effort: str = "",
    executor_kind: str = "",
    files: list | None = None,
    writer_job_id: str = "",
    writer_snapshot_id: str = "",
    access: str = "",
    reservation_id: str = "",
    attempt_id: str = "",
    owner_token: str = "",
    owner_session: str = "",
    queue_id: str = "",
    native_agent_id: str = "",
    return_details: bool = False,
) -> str | dict:
    """Write a running job. Does not launch a worker. Returns the job id."""
    _require_harness(repo)
    _native_parent_only()
    role = (role or "worker").strip() or "worker"
    live = (live or "").strip() or rig_harness.live_parent()
    preferred = (preferred or "").strip() or rig_harness.preferred_parent(repo)
    raw_id = (job_id or "").strip()
    job_id = _allocate_job_id(raw_id)
    job_dir = _job_path(repo, job_id)
    worker = _resolve_worker(worker, live, preferred, {})
    rig_harness.assert_spawn_allowed(repo, worker, live)
    listed = job_files_input(files)
    kind = "parent" if worker == "parent" or role == "parent" else executor_kind or "native_child"
    if kind not in {"parent", "native_child"}:
        raise SystemExit("rig job: executor_kind must be parent|native_child")
    access = access or ("read" if role in {"review", "explore", "research"} else "write")
    import admission
    import route
    # Resolve models before taking the admission lock. Parent models are observed,
    # while child selections still honor model bans and effective worker policy.
    if not model and kind != "parent":
        model, effort = route.resolved_model_for(worker, route.classify(role, ""))
    if model and kind != "parent":
        error = route.assert_child_model(model)
        if error:
            raise SystemExit(error)
    owner = admission.caller_owner(kind, owner_session=owner_session, native_agent_id=native_agent_id)
    owner["parent_cli"] = live
    now = iso_now()
    with admission.transaction(repo):
        lease = admission.reserve(
            repo, job_id=job_id, worker=worker, role=role, model=model, files=listed, access=access,
            owner=owner, owner_session=owner_session, queue_id=queue_id,
            reservation_id=reservation_id, attempt_id=attempt_id, owner_token=owner_token,
            writer_job_id=writer_job_id, writer_snapshot_id=writer_snapshot_id,
        )
        credentials = admission.credentials(lease)
        listed = lease.get("declared_files", listed)
        activated = False
        try:
            write_job_files(job_dir, job_id, worker, role, "running", 0, now, "", summary or "", "native",
                            thread=_job_thread(repo), files=listed, model=model, effort=effort,
                            executor_kind=kind, execution_mode="parent" if kind == "parent" else "native",
                            writer_job_id=writer_job_id, writer_snapshot_id=writer_snapshot_id,
                            reservation=lease)
            lease = admission.activate(repo, **credentials, job_id=job_id, worker=worker, files=listed,
                                       access=access, owner=owner, owner_session=owner_session,
                                       native_agent_id=native_agent_id)
            activated = True
            (job_dir / "started_at").write_text(now + "\n")
            write_state(repo, job_id, worker, "running", summary or "")
            artifact = admission.write_credentials(repo, {**lease, **credentials})
        except BaseException:
            if not activated:
                current = admission.get_reservation(repo, credentials["reservation_id"])
                if current and not current.get("launch_started") and not current.get("process"):
                    # A partial running row must not reappear as an ownerless live
                    # writer after its unlaunched reservation is compensated.
                    partial = _read_meta_dict(job_dir)
                    if partial.get("attempt_id") == credentials["attempt_id"]:
                        import change_evidence

                        partial.update(status="fail", exit_code=1, ended_at=iso_now(),
                                       execution_mode="not_started", ownership_established=False,
                                       summary="Native registration failed before execution started")
                        for name in ("meta.json", "result.json"):
                            change_evidence.write_json(job_dir / name, partial)
                    admission.release(repo, **credentials, rationale="native registration failed before activation",
                                      owner=owner, owner_session=owner_session, mode="launch_failed")
            raise
    details = {**lease, **credentials, "job_id": job_id, "credentials_path": str(artifact)}
    return details if return_details else job_id


def finish_job(
    repo: Path,
    job_id: str,
    status: str = "ok",
    summary: str = "",
    worker: str = "",
    role: str = "",
    live: str = "",
    preferred: str = "",
    require_id: bool = True,
    model: str = "",
    effort: str = "",
    executor_kind: str = "",
    reservation_id: str = "",
    attempt_id: str = "",
    owner_token: str = "",
    owner_session: str = "",
    completion: dict | None = None,
    execution_mode: str = "",
    return_details: bool = False,
) -> str | dict:
    """Write result.json for a job. Does not kill a process."""
    _require_harness(repo)
    _native_parent_only()
    raw_id = (job_id or "").strip()
    if require_id and not raw_id:
        raise SystemExit("usage: rig job finish <id> [--status ok|fail] [--summary TEXT]")
    status = _validate_status((status or "ok").strip() or "ok")
    job_id = _allocate_job_id(raw_id)
    job_dir = _job_path(repo, job_id)
    import admission

    transition = None
    with admission.transaction(repo):
        meta = _read_meta_dict(job_dir)
        if meta.get("reservation_id") and (job_dir / "cancel.json").exists() and status != "cancelled":
            raise ValueError("rig job: cancellation was requested; preserve the cancelled outcome")
        role = (role or "").strip() or str(meta.get("role") or "").strip() or "worker"
        worker = _resolve_worker(worker, live, preferred, meta)
        credentials = dict(reservation_id=reservation_id, attempt_id=attempt_id, owner_token=owner_token)
        if meta.get("reservation_id"):
            if (reservation_id, attempt_id) != (meta.get("reservation_id"), meta.get("attempt_id")):
                raise ValueError("rig job: finish requires this job's reservation and attempt credentials")
            for key, value in (("worker", worker), ("role", role), ("model", model), ("effort", effort), ("executor_kind", executor_kind)):
                if value and value != meta.get(key):
                    raise ValueError(f"rig job: finish cannot change admitted {key}")
            if status == "running":
                raise ValueError("rig job: admitted execution must finish with a terminal status")
            owner = admission.caller_owner(str(meta.get("executor_kind") or "native_child"), owner_session=owner_session)
            transition = admission.finish(repo, **credentials, status=status, owner=owner,
                                          owner_session=owner_session, completion=completion)
        elif any(credentials.values()):
            raise ValueError("rig job: supplied credentials do not belong to this legacy job")
        now = iso_now()
        started = _started_at(job_dir, meta, now)
        write_job_files(job_dir, job_id, worker, role, status, _exit_code(status), started, now,
                        summary or "", str(meta.get("kind") or "native"), thread=_job_thread(repo),
                        model=model, effort=effort, executor_kind=executor_kind,
                        execution_mode=execution_mode or str(meta.get("execution_mode") or "unknown"),
                        capture_evidence=transition is None or transition.get("stopped") is True)
        write_state(repo, job_id, worker, status, summary or "")
    persist_activity(job_dir)
    if status == "ok":
        _prune_stdout_log(job_dir)
    text = _finish_text(job_id, worker, role, status, job_dir)
    if transition and transition.get("needs_reconciliation"):
        text += "\nneeds_reconciliation: completion is unconfirmed; slot and files remain held"
    return {"job_id": job_id, "text": text, "reservation": transition} if return_details else text


def close_job(repo: Path, job_id: str, *, reservation_id: str = "", attempt_id: str = "",
              owner_token: str = "", owner_session: str = "", rationale: str = "") -> dict:
    _require_harness(repo)
    _native_parent_only()
    import admission

    if not job_id:
        raise ValueError("rig job: close requires a job ID")
    credentials = dict(reservation_id=reservation_id, attempt_id=attempt_id, owner_token=owner_token)
    with admission.transaction(repo):
        lease = admission.assert_owned(repo, **credentials, owner_session=owner_session)
        # A retained review handoff can fail before meta.json is created. Its
        # authenticated reservation is enough to identify an explicit close.
        bound_id = str(lease.get("job_id") or "")
        requested = job_id if job_id == bound_id else resolve_job(repo, job_id)["job_id"]
        if bound_id != requested:
            raise ValueError("rig job: credentials belong to another job")
        return admission.release(repo, **credentials, owner_session=owner_session,
                                 rationale=rationale, mode="close")


def reconcile_jobs(repo: Path, job_id: str = "", **options) -> dict:
    _require_harness(repo)
    _native_parent_only()
    import admission

    return admission.reconcile(repo, job_id=job_id, **options)


def record_job(
    repo: Path,
    worker: str = "",
    role: str = "worker",
    status: str = "ok",
    summary: str = "",
    job_id: str = "",
    live: str = "",
    preferred: str = "",
    model: str = "",
    effort: str = "",
    executor_kind: str = "",
    files: list | None = None,
) -> str:
    """One-shot start+finish like `rig job record`. Files only."""
    _require_harness(repo)
    _native_parent_only()
    live = (live or "").strip() or rig_harness.live_parent()
    preferred = (preferred or "").strip() or rig_harness.preferred_parent(repo)
    raw_id = (job_id or "").strip()
    meta = _read_meta_dict(jobs_dir(repo) / raw_id) if raw_id else {}
    worker = _resolve_worker(worker, live, preferred, meta)
    rig_harness.assert_spawn_allowed(repo, worker, live)
    if files is not None:
        raise SystemExit("rig job record is retrospective; start scoped writes before editing")
    if meta or status == "running":
        raise SystemExit("rig job record requires a fresh ID and a terminal read-only result")
    return finish_job(
        repo,
        job_id,
        status=status,
        summary=summary,
        worker=worker,
        role=role,
        live=live,
        preferred=preferred,
        require_id=False,
        model=model,
        effort=effort,
        executor_kind=executor_kind,
        execution_mode="retrospective",
    )


def follow_log(job: dict) -> None:
    print(
        f"# {job['job_id']}  {job['worker']}  {job.get('model') or '-'}  reasoning={job.get('effort') or '-'}  {job['effective']}  {job['task']}",
        flush=True,
    )
    seen = 0
    job_dir = Path(job["dir"])
    while True:
        fresh = load_job(job_dir) or job
        acts = fresh.get("activities") or []
        for act in acts[seen:]:
            print(act, flush=True)
        seen = len(acts)
        time.sleep(0.5)


def _hud_job(job: dict) -> dict:
    return {
        "id": job.get("job_id"),
        "worker": job.get("worker"),
        "role": job.get("role") or "",
        "task": job.get("task") or "",
        "doing": job.get("doing") or "",
        "model": job.get("model") or "",
        "effort": job.get("effort") or "",
        "effective": job.get("effective"),
        "display_state": job.get("display_state") or job_display_state(job),
        "display_reason": job.get("display_reason") or "",
        "display_action": job.get("display_action") or "",
        "independence": job.get("independence") or "unknown",
    }


def _hud_priority(job: dict) -> int:
    reservation = job.get("reservation") or {}
    state = job_display_state(job)
    if job.get("effective") == "ask":
        return 0
    if reservation.get("needs_reconciliation") or (state == "cancelled" and reservation.get("stage") != "released" and reservation and not reservation.get("stopped")):
        return 1
    if state in {"working", "reserved", "verifying"}:
        return 2
    return 3


def _terminal_time(job: dict) -> float:
    accepted = (job.get("verification_summary") or {}).get("accepted_at") or ""
    times = [parse_job_ts(value) for value in (job.get("ended_at") or "", accepted)]
    return max((stamp.timestamp() for stamp in times if stamp is not None), default=0)


def hud_snapshot(payload: dict | None = None, *, repo: Path | None = None) -> dict:
    """Read-only jobs + queue snapshot for parent TUI HUDs. Does not spawn."""
    payload = payload if isinstance(payload, dict) else {}
    ws = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    ctx = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    cwd = ws.get("current_dir") or payload.get("cwd") or ""
    start = repo if repo is not None else Path(ws.get("repo_root") or cwd or os.getcwd())
    root = repo_root(str(start))
    sid = str(payload.get("session_id") or "").strip()
    if sid:
        try:
            remember_thread(root, sid)
        except OSError:
            pass
    name = Path(str(cwd)).name if cwd else root.name
    model_name = str(model.get("display_name") or "")
    pct = ctx.get("used_percentage")
    bits = [x for x in [name, model_name, f"{pct}% ctx" if pct is not None else ""] if x]
    line1 = " · ".join(bits) or "rig"
    try:
        listing = list_jobs(root)
    except OSError:
        listing = []
    asking = [j for j in listing if j.get("effective") == "ask"]
    running = [j for j in listing if j.get("effective") == "running"]
    reserved = [j for j in listing if j.get("effective") == "reserved"]
    pending: list[dict] = []
    cap = 3
    n_live = len(asking) + len(running)
    try:
        import work_queue as rig_queue  # noqa: PLC0415 — avoid import cycle

        pending = rig_queue.list_items(root, status="pending")
        cap = rig_queue.max_running(root)
        n_live = rig_queue.live_count(root, jobs_snapshot=listing)
    except (OSError, ImportError, ValueError):
        pass
    first_pending = str(pending[0].get("text") or "")[:80] if pending else ""
    queue_bit = f"QUEUE {len(pending)} · live {n_live}/{cap}"
    active = [job for job in listing if _hud_priority(job) < 3]
    active.sort(key=lambda job: (_hud_priority(job), -job.get("mtime", 0)))
    recent = [job for job in listing if _hud_priority(job) == 3 and 0 <= time.time() - _terminal_time(job) < 60]
    recent.sort(key=_terminal_time, reverse=True)
    selected = active[0] if active else recent[0] if recent else None
    # Selection precedes hashing. Historical accepted jobs are metadata-only.
    if selected:
        selected = project_job(selected, root, refresh=not bool(active), cache={})
    status = "ask" if asking else "running" if active else "idle"
    display = selected["display_state"] if selected else "queued" if pending else "idle"
    remaining = max(0, len(active) - 1)
    extra = f" +{remaining} · rig tui" if remaining else " · rig tui"
    line2 = f"rig · {display} · {queue_bit}{extra}"
    detail = first_pending
    if selected:
        jid = selected["job_id"]
        detail = selected["display_reason"]
        if selected.get("effective") == "ask":
            line1 = f"rig · {selected['worker']} ASK {jid}{extra}"
            line2 = selected["display_action"]
        else:
            spec = selected["display_model"] if selected["display_model"] != "unknown" else "model unknown"
            line2 = f"rig · {display} {jid} · {selected['worker']} {spec}{extra}"
            if selected.get("display_action"):
                line1 = line2
                line2 = selected["display_action"]
    lines = [line1, line2]
    if selected:
        lines.append(queue_bit)
    if detail:
        lines.append(str(detail)[:160])
    return {
        "idle": status == "idle",
        "status": status,
        "display_state": display,
        "selected": _hud_job(selected) if selected else None,
        "verification_summary": selected.get("verification_summary") if selected else None,
        "independence": selected.get("independence", "unknown") if selected else "unknown",
        "remaining": remaining,
        "cwd": str(cwd or root),
        "repo": str(root),
        "queue_pending": len(pending),
        "live": n_live,
        "cap": cap,
        "asking": [_hud_job(j) for j in asking],
        "running": [_hud_job(j) for j in running],
        "reserved": [_hud_job(j) for j in reserved],
        "pending_text": first_pending,
        "lines": lines,
        "text": "\n".join(lines),
    }


def format_statusline(payload: dict) -> str:
    snap = hud_snapshot(payload)
    lines = list(snap.get("lines") or [])
    if not lines:
        return "rig · idle · QUEUE 0"
    status = snap.get("status")
    green, yellow, reset = "\033[32m", "\033[33m", "\033[0m"
    if status == "ask" and len(lines) > 1:
        lines[1] = f"{yellow}{lines[1]}{reset}"
    elif status == "running" and len(lines) > 1:
        lines[1] = f"{green}{lines[1]}{reset}"
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="jobs.py")
    parser.add_argument(
        "cmd",
        nargs="?",
        default="list",
        choices=[
            "list",
            "show",
            "log",
            "statusline",
            "hud",
            "thread",
            "allow",
            "deny",
            "cancel",
            "wait",
            "persist",
            "message",
            "start", "finish", "record", "close", "reconcile",
        ],
    )
    parser.add_argument("job_id", nargs="*")
    parser.add_argument("--repo")
    parser.add_argument("--dir")
    parser.add_argument("--text", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-f", "--follow", action="store_true")
    parser.add_argument("-n", "--lines", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument(
        "--thread",
        nargs="?",
        const="this",
        help="Filter by parent thread id. Bare --thread uses the current parent thread.",
    )
    parser.add_argument("--reason", default="")
    parser.add_argument("--worker", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--status", default="ok")
    parser.add_argument("--summary", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="")
    parser.add_argument("--executor-kind", choices=["parent", "native_child"], default="")
    parser.add_argument("--files-json")
    parser.add_argument("--access", choices=["read", "write"], default="")
    parser.add_argument("--reservation-id", default=os.environ.get("RIG_RESERVATION_ID", ""))
    parser.add_argument("--attempt-id", default=os.environ.get("RIG_ATTEMPT_ID", ""))
    parser.add_argument("--owner-session", default=os.environ.get("RIG_OWNER_SESSION", ""))
    parser.add_argument("--queue-id", default="")
    parser.add_argument("--native-agent-id", default="")
    parser.add_argument("--completion-json")
    parser.add_argument("--writer-job-id", default="")
    parser.add_argument("--writer-snapshot-id", default="")
    parser.add_argument("--rationale", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--action", choices=["report", "adopt", "release"], default="report")
    args = parser.parse_intermixed_args()
    repo = repo_root(args.repo)
    wait_ids = [str(x).strip() for x in (args.job_id or []) if str(x).strip()]
    job_id = wait_ids[0] if wait_ids else None
    if args.cmd in {"start", "finish", "record", "close", "reconcile"}:
        if len(wait_ids) > 1:
            parser.error("this command accepts one job ID")
        ownership = {"reservation_id": args.reservation_id, "attempt_id": args.attempt_id,
                     "owner_token": os.environ.get("RIG_OWNER_TOKEN", ""), "owner_session": args.owner_session}
        common = {"worker": args.worker, "role": args.role, "summary": args.summary,
                  "model": args.model, "effort": args.effort, "executor_kind": args.executor_kind}
        try:
            files = json.loads(args.files_json) if args.files_json is not None else None
            completion = json.loads(args.completion_json) if args.completion_json is not None else None
            if args.cmd == "start":
                result = start_job(repo, job_id=job_id or "", files=files, access=args.access,
                                   queue_id=args.queue_id, native_agent_id=args.native_agent_id,
                                   writer_job_id=args.writer_job_id, writer_snapshot_id=args.writer_snapshot_id,
                                   return_details=True, **common, **ownership)
                print(json.dumps(result) if args.json else result["job_id"])
                print(f"job {result['job_id']} status=running\ncredentials {result['credentials_path']}", file=sys.stderr)
            elif args.cmd == "finish":
                result = finish_job(repo, job_id or "", status=args.status, completion=completion,
                                    return_details=True, **common, **ownership)
                print(json.dumps(result) if args.json else result["text"])
            elif args.cmd == "record":
                result = record_job(repo, job_id=job_id or "", status=args.status, files=files, **common)
                print(result)
            elif args.cmd == "close":
                print(json.dumps(close_job(repo, job_id or "", rationale=args.rationale, **ownership)))
            else:
                print(json.dumps(reconcile_jobs(repo, job_id or "", queue_id=args.queue_id,
                    apply=args.apply, action=args.action, **ownership,
                    worker=args.worker, access=args.access or "write", files=files,
                    rationale=args.rationale, completion=completion)))
        except (ValueError, OSError) as error:
            parser.exit(2, f"rig job: {error}\n")
        return 0
    if args.cmd == "message":
        job = resolve_job(repo, job_id)
        text = (args.text or "").strip()
        if not text:
            raise SystemExit("usage: jobs.py message <id> --text TEXT")
        obj = rig_inbox.write_inbox(Path(job["dir"]), text)
        print(f"message {job['job_id']} inbox pending")
        print(obj["text"])
        return 0
    if args.cmd == "persist":
        target = Path(args.dir) if args.dir else None
        if target is None and job_id:
            target = jobs_dir(repo) / job_id
        if target is None:
            raise SystemExit("usage: jobs.py persist --dir DIR")
        persist_activity(target)
        return 0
    if args.cmd == "thread":
        print(current_thread(repo))
        return 0
    if args.cmd == "list":
        want_thread = args.thread
        if want_thread == "this":
            want_thread = current_thread(repo) or None
        listing = list_jobs(repo, thread=want_thread)
        if args.json:
            dump = [{k: v for k, v in j.items() if k != "activities"} for j in listing]
            print(json.dumps(dump, indent=2))
        else:
            print(format_table(listing, repo))
        return 0
    if args.cmd == "show":
        print(format_show(resolve_job(repo, job_id)))
        return 0
    if args.cmd == "log":
        job = resolve_job(repo, job_id)
        if args.follow:
            follow_log(job)
            return 0
        print(format_log(job, args.lines))
        return 0
    if args.cmd in {"allow", "deny"}:
        job = resolve_job(repo, job_id)
        text = answer_pending(job, args.cmd, args.reason)
        print(text)
        return 0 if text.startswith(args.cmd) else 1
    if args.cmd == "cancel":
        text = cancel_job(repo, job_id, args.reason or "parent")
        print(text)
        return 0 if text.startswith("cancelled") else 1
    if args.cmd == "wait":
        code, text = wait_job(repo, job_id, args.timeout, ids=wait_ids or None)
        print(text)
        return code
    raw = sys.stdin.read() if not sys.stdin.isatty() else "{}"
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if args.repo:
        payload.setdefault("cwd", str(repo))
        ws = payload.get("workspace")
        if not isinstance(ws, dict):
            payload["workspace"] = {"current_dir": str(repo), "repo_root": str(repo)}
    snap = hud_snapshot(payload, repo=repo)
    if args.json:
        print(json.dumps(snap))
    else:
        print(format_statusline(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
