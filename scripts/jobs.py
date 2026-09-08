#!/usr/bin/env python3
"""List Rig jobs, decode child logs, and render statusline text."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PREAMBLE_MARKERS = (
    "you are a worker, not the orchestrator",
    "do not spawn codex, grok, or claude",
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


def repo_root(start: str | None = None) -> Path:
    d = Path(start or os.getcwd()).resolve()
    for p in [d, *d.parents]:
        if (p / ".rig").is_dir() or (p / ".git").is_dir():
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


def activity_from_event(obj: dict) -> str | None:
    kind = obj.get("type")
    if kind == "tool_call":
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
    if kind == "assistant":
        msg = obj.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            bits = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    bits.append(
                        f"{block.get('name', 'tool')} {_input_detail(block.get('input') or {})}".strip()
                    )
                elif block.get("type") in ("text", "thinking") and block.get("text"):
                    bits.append(_first_line(str(block["text"]), 140))
            if bits:
                return bits[-1]
        if isinstance(content, str) and content.strip():
            return _first_line(content, 140)
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


def decode_log_text(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    if text.startswith("{") and "\n{" in text:
        lines: list[str] = []
        buf_kind: str | None = None
        buf: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            if kind in ("thought", "text"):
                data = obj.get("data") or obj.get("text") or ""
                if not isinstance(data, str) or not data:
                    continue
                if buf_kind not in (None, kind):
                    _flush_stream(buf_kind, buf, lines)
                buf_kind = kind
                buf.append(data)
                continue
            _flush_stream(buf_kind, buf, lines)
            buf_kind = None
            if kind == "tool_call":
                act = activity_from_event(obj)
                if act and (not lines or lines[-1] != act):
                    lines.append(act)
                continue
            if kind == "error":
                act = activity_from_event(obj)
                if act:
                    lines.append(act)
        _flush_stream(buf_kind, buf, lines)
        if lines:
            return lines[-120:]
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # incomplete json blob (buffered until child exits)
            return ["waiting for child json (buffered until exit)"]
        if isinstance(obj, dict):
            if obj.get("type") == "error":
                return [activity_from_event(obj) or str(obj)]
            blob = obj.get("text") or obj.get("result") or obj.get("message") or ""
            if isinstance(blob, str) and blob.strip():
                out = []
                for para in blob.split("\n"):
                    s = para.strip()
                    if s:
                        out.append(_first_line(s, 160))
                return out[-80:] or [_first_line(blob, 160)]
            act = activity_from_event(obj)
            return [act] if act else []
    return [_first_line(line, 160) for line in text.splitlines() if line.strip()][-80:]


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
    if status == "running" and pid_i and not alive:
        effective = "stale"
    log_path = job_path / "stdout.log"
    activities = decode_log_text(read_log_tail(log_path))
    kind = str(obj.get("kind") or "")
    doing = ""
    if effective == "running":
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
        "started_at": str(obj.get("started_at") or ""),
        "ended_at": str(obj.get("ended_at") or ""),
        "task": task,
        "doing": doing,
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
    jobs.sort(key=lambda j: (0 if j["effective"] == "running" else 1, -j["mtime"]))
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
        if job["effective"] == "running":
            return job
    if jobs:
        return jobs[0]
    raise SystemExit("rig: no jobs")


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
        if job.get("thread"):
            extras.append(f"          thread {job['thread']}")
        if job["doing"]:
            extras.append(f"          doing  {job['doing']}")
        if job["effective"] == "running" and job.get("open"):
            extras.append(f"          open   {job['open']}")
        if job["effective"] == "running":
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
        if job["effective"] == "running":
            return "log empty (child still running; json is buffered until exit)"
        if job.get("log_pruned"):
            return "log pruned after success"
        return "log empty"
    return "\n".join(acts[-n:])


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
    running = [j for j in jobs if j["effective"] == "running"]
    green, reset = "\033[32m", "\033[0m"
    if not running:
        return f"{line1}\nrig · idle"
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
        "cmd", nargs="?", default="list", choices=["list", "show", "log", "statusline", "thread"]
    )
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--repo")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-f", "--follow", action="store_true")
    parser.add_argument("-n", "--lines", type=int, default=40)
    parser.add_argument(
        "--thread",
        nargs="?",
        const="this",
        help="Filter by parent thread id. Bare --thread uses the current parent thread.",
    )
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
    raw = sys.stdin.read() if not sys.stdin.isatty() else "{}"
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    print(format_statusline(payload if isinstance(payload, dict) else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
