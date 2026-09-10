#!/usr/bin/env python3
"""List Rig jobs, decode child logs, write start/finish files, and render statusline text."""
from __future__ import annotations

import json
import os
import re
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
    activities = decode_log_text(read_log_tail(log_path))
    kind = str(obj.get("kind") or "")
    doing = ""
    if effective == "ask" and pending_ask:
        doing = (
            f"ASK {pending_ask.get('preview') or pending_ask.get('tool_name') or 'tool'}  "
            f"→  rig job allow {job_id}"
        )
    elif effective == "running":
        if activities:
            doing = next((a for a in reversed(activities) if not a.startswith("think")), activities[-1])
        elif kind == "native":
            doing = "native spawn (no stdout.log — finish with rig job finish)"
        else:
            doing = "running (no log yet)"
    elif activities:
        doing = next((a for a in reversed(activities) if not a.startswith("think")), activities[-1])
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
    inferred = False
    if not model:
        try:
            import route as rig_route

            route_kind = rig_route.classify(str(obj.get("role") or "implement"), "")
            model, effort = rig_route.model_for(str(obj.get("worker") or "codex"), route_kind)
            inferred = True
        except Exception:
            model, effort = "", ""
    return {
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
        "open": str(obj.get("open") or ""),
        "watch": str(obj.get("watch") or ""),
        "summary": str(obj.get("summary") or ""),
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_s": elapsed_i,
        "task": task,
        "doing": doing,
        "ask": pending_ask,
        "activities": activities,
        "dir": str(job_path),
        "log": str(log_path),
        "log_pruned": status == "ok" and not log_path.is_file(),
        "mtime": mtime,
    }


def list_jobs(repo: Path, thread: str | None = None) -> list[dict]:
    root = jobs_dir(repo)
    if not root.is_dir():
        return []
    jobs = []
    for path in root.iterdir():
        if path.is_dir():
            job = load_job(path)
            if job:
                jobs.append(job)
    def _rank(job: dict) -> int:
        if job["effective"] == "ask":
            return 0
        if job["effective"] == "running":
            return 1
        return 2

    jobs.sort(key=lambda j: (_rank(j), -j["mtime"]))
    if thread:
        jobs = [j for j in jobs if j.get("thread") == thread]
    return jobs


def resolve_job(repo: Path, job_id: str | None) -> dict:
    jobs = list_jobs(repo)
    if job_id:
        for job in jobs:
            if job["job_id"] == job_id or job_id in job["job_id"]:
                return job
        raise SystemExit(f"rig: no such job {job_id}")
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


def format_wait(job: dict) -> str:
    job_id = job["job_id"]
    eff = job.get("effective")
    if eff == "ask":
        pending = job.get("ask") if isinstance(job.get("ask"), dict) else {}
        preview = str(pending.get("preview") or pending.get("tool_name") or "tool")
        return "\n".join(
            [
                f"ASK {job_id}",
                f"agent   {job.get('worker') or '?'}",
                f"preview {preview}",
                f"answer  rig job allow {job_id}",
                f"        rig job deny {job_id}",
                "You must answer this prompt so the child can continue.",
                "Do not kill this job. Do not spawn another worker for this task.",
            ]
        )
    if eff == "running":
        doing = job.get("doing") or "running"
        return "\n".join(
            [
                f"RUNNING {job_id}",
                f"agent   {job.get('worker') or '?'}",
                f"doing   {doing}",
                f"next    rig job wait {job_id}",
            ]
        )
    return format_show(job)


def wait_job(
    repo: Path,
    job_id: str | None,
    timeout: float | None = None,
    on_tick: Callable | None = None,
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
    last_tick: tuple[str, str] | None = None
    while True:
        job = resolve_job(repo, job_id)
        eff = job.get("effective")
        if on_tick is not None:
            key = (str(eff or ""), str(job.get("doing") or ""))
            if last_tick is None:
                if eff == "running":
                    on_tick(job)
                    last_tick = key
            elif key != last_tick:
                on_tick(job)
                last_tick = key
        if eff == "ask":
            return 2, format_wait(job)
        if eff in {"ok", "fail", "timeout", "stale"}:
            return (0 if eff == "ok" else 1), format_wait(job)
        if deadline is not None and time.time() >= deadline:
            return 124, format_wait(job)
        time.sleep(0.4)


def format_table(jobs: list[dict]) -> str:
    if not jobs:
        return "no jobs  (cross-CLI children and recorded cheap workers show up here)"
    rows = ["STATUS    AGENT    ROLE       JOB                              TASK"]
    for job in jobs:
        rows.append(
            f"{job['effective']:<9} {job['worker']:<8} {job['role']:<10} {job['job_id']:<32} {job['task']}"
        )
        extras = []
        if job.get("model") or job.get("effort"):
            extras.append(
                f"          model  {job.get('model') or '-'}   reasoning {job.get('effort') or '-'}"
            )
        if job.get("elapsed_s") is not None:
            extras.append(f"          elapsed  {format_elapsed(int(job['elapsed_s']))}")
        if job.get("thread"):
            extras.append(f"          thread {job['thread']}")
        if job["doing"]:
            extras.append(f"          doing  {job['doing']}")
        if job["effective"] == "ask":
            extras.append(f"          answer rig job allow {job['job_id']}  |  rig job deny {job['job_id']}")
        if job["effective"] == "running" and job.get("open"):
            extras.append(f"          open   {job['open']}")
        if job["effective"] in {"running", "ask"}:
            extras.append(f"          log    rig job log {job['job_id']} -f")
        rows.extend(extras)
    running = sum(1 for j in jobs if j["effective"] == "running")
    rows.append(f"\n{running} running / {len(jobs)} jobs    rig tui    rig job log [id] -f")
    return "\n".join(rows)


def format_show(job: dict, log_lines: int = 24) -> str:
    lines = [
        f"job     {job['job_id']}",
        f"agent   {job['worker']}",
        f"role    {job['role'] or '-'}",
        f"status  {job['effective']}",
    ]
    lines.append(f"model      {job.get('model') or '-'}")
    lines.append(f"reasoning  {job.get('effort') or '-'}")
    lines += [
        f"task    {job['task']}",
    ]
    if job["doing"]:
        lines.append(f"doing   {job['doing']}")
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
JOB_STATUSES = frozenset({"ok", "fail", "timeout", "running"})
JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


JOB_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime(JOB_TS_FMT)


def parse_job_ts(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, JOB_TS_FMT).replace(tzinfo=timezone.utc)
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
) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    if not model:
        try:
            import route as rig_route

            route_kind = rig_route.classify(role or "implement", "")
            model, effort = rig_route.model_for(worker or "codex", route_kind)
        except Exception:
            model, effort = model or "", effort or ""
    old = _read_meta_dict(job_dir)
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
    }
    for key in ("thread", "session_id", "pid", "open", "watch", "kind", "model", "effort"):
        if not obj.get(key) and old.get(key) not in (None, ""):
            obj[key] = old[key]
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
    if not JOB_ID_RE.match(job_id):
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
        raise SystemExit("rig job: status must be ok|fail|timeout|running")
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
    return 1


def _finish_text(job_id: str, worker: str, role: str, status: str, job_dir: Path) -> str:
    return f"job {job_id} worker={worker} role={role} status={status}\n{job_dir / 'result.json'}"


def start_job(
    repo: Path,
    worker: str = "",
    role: str = "worker",
    job_id: str = "",
    summary: str = "",
    live: str = "",
    preferred: str = "",
) -> str:
    """Write a running job. Does not launch a worker. Returns the job id."""
    _require_harness(repo)
    role = (role or "worker").strip() or "worker"
    live = (live or "").strip() or rig_harness.live_parent()
    preferred = (preferred or "").strip() or rig_harness.preferred_parent(repo)
    raw_id = (job_id or "").strip()
    meta = _read_meta_dict(jobs_dir(repo) / raw_id) if raw_id else {}
    job_id = _allocate_job_id(raw_id)
    worker = _resolve_worker(worker, live, preferred, meta)
    rig_harness.assert_spawn_allowed(repo, worker, live)
    now = iso_now()
    job_dir = jobs_dir(repo) / job_id
    write_job_files(
        job_dir,
        job_id,
        worker,
        role,
        "running",
        0,
        now,
        "",
        summary or "",
        "native",
        thread=_job_thread(repo),
    )
    (job_dir / "started_at").write_text(now + "\n")
    write_state(repo, job_id, worker, "running", summary or "")
    return job_id


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
) -> str:
    """Write result.json for a job. Does not kill a process."""
    _require_harness(repo)
    raw_id = (job_id or "").strip()
    if require_id and not raw_id:
        raise SystemExit("usage: rig job finish <id> [--status ok|fail] [--summary TEXT]")
    status = _validate_status((status or "ok").strip() or "ok")
    meta = _read_meta_dict(jobs_dir(repo) / raw_id) if raw_id else {}
    role = (role or "").strip() or str(meta.get("role") or "").strip() or "worker"
    worker = _resolve_worker(worker, live, preferred, meta)
    job_id = _allocate_job_id(raw_id)
    now = iso_now()
    job_dir = jobs_dir(repo) / job_id
    started = _started_at(job_dir, meta, now)
    write_job_files(
        job_dir,
        job_id,
        worker,
        role,
        status,
        _exit_code(status),
        started,
        now,
        summary or "",
        "native",
        thread=_job_thread(repo),
    )
    write_state(repo, job_id, worker, status, summary or "")
    if status == "ok":
        log = job_dir / "stdout.log"
        try:
            log.unlink()
        except OSError:
            pass
    return _finish_text(job_id, worker, role, status, job_dir)


def record_job(
    repo: Path,
    worker: str = "",
    role: str = "worker",
    status: str = "ok",
    summary: str = "",
    job_id: str = "",
    live: str = "",
    preferred: str = "",
) -> str:
    """One-shot start+finish like `rig job record`. Files only."""
    _require_harness(repo)
    live = (live or "").strip() or rig_harness.live_parent()
    preferred = (preferred or "").strip() or rig_harness.preferred_parent(repo)
    raw_id = (job_id or "").strip()
    meta = _read_meta_dict(jobs_dir(repo) / raw_id) if raw_id else {}
    worker = _resolve_worker(worker, live, preferred, meta)
    rig_harness.assert_spawn_allowed(repo, worker, live)
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


def format_statusline(payload: dict) -> str:
    ws = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    ctx = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    cwd = ws.get("current_dir") or payload.get("cwd") or ""
    repo = Path(ws.get("repo_root") or cwd or os.getcwd())
    sid = str(payload.get("session_id") or "").strip()
    if sid:
        try:
            remember_thread(repo_root(str(repo)), sid)
        except OSError:
            pass
    name = Path(str(cwd)).name if cwd else repo.name
    model_name = str(model.get("display_name") or "")
    pct = ctx.get("used_percentage")
    bits = [x for x in [name, model_name, f"{pct}% ctx" if pct is not None else ""] if x]
    line1 = " · ".join(bits) or "rig"
    try:
        jobs = list_jobs(repo_root(str(repo)))
    except OSError:
        jobs = []
    asking = [j for j in jobs if j["effective"] == "ask"]
    running = [j for j in jobs if j["effective"] == "running"]
    green, yellow, reset = "\033[32m", "\033[33m", "\033[0m"
    if asking:
        job = asking[0]
        extra = f" +{len(asking) - 1}" if len(asking) > 1 else ""
        line2 = f"{yellow}rig · {job['worker']} ASK{extra} · {job['task']}{reset}"
        line3 = job["doing"] if job.get("doing") else f"rig job allow {job['job_id']}"
    elif not running:
        return f"{line1}\nrig · idle"
    else:
        job = running[0]
        extra = f" +{len(running) - 1}" if len(running) > 1 else ""
        spec = " ".join(x for x in [job.get("model"), job.get("effort")] if x)
        line2 = (
            f"{green}rig · {job['worker']} {job['role'] or 'worker'} running{extra}"
            + (f" · {spec}" if spec else "")
            + f" · {job['task']}{reset}"
        )
        line3 = job["doing"] if job.get("doing") else ""
    out = [line1, line2]
    if line3:
        out.append(line3[:160])
    return "\n".join(out)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="jobs.py")
    parser.add_argument(
        "cmd",
        nargs="?",
        default="list",
        choices=["list", "show", "log", "statusline", "thread", "allow", "deny", "wait"],
    )
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--repo")
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
    args = parser.parse_args()
    repo = repo_root(args.repo)
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
            print(format_table(listing))
        return 0
    if args.cmd == "show":
        print(format_show(resolve_job(repo, args.job_id)))
        return 0
    if args.cmd == "log":
        job = resolve_job(repo, args.job_id)
        if args.follow:
            follow_log(job)
            return 0
        print(format_log(job, args.lines))
        return 0
    if args.cmd in {"allow", "deny"}:
        job = resolve_job(repo, args.job_id)
        text = answer_pending(job, args.cmd, args.reason)
        print(text)
        return 0 if text.startswith(args.cmd) else 1
    if args.cmd == "wait":
        code, text = wait_job(repo, args.job_id, args.timeout)
        print(text)
        return code
    raw = sys.stdin.read() if not sys.stdin.isatty() else "{}"
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    print(format_statusline(payload if isinstance(payload, dict) else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
