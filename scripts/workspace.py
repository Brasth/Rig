#!/usr/bin/env python3
"""Workspace panel data: container, ports and dc-cli's own diagnosis.

All dc-cli JSON is read here (see dc_client for the assumed contract) and turned
into ready-to-render sections, so clients such as the Go shell render text and
never parse dc output. Reads are cached briefly so a polling client cannot make
Rig run dc more than once every few seconds.
"""
from __future__ import annotations

import re

import threading
import time
from pathlib import Path

import dc_client
import sandbox_env

VIEW_VERSION = 1
CACHE_SECONDS = 5.0
_BAD_PORT_STATES = frozenset({"held", "conflict", "down", "error", "blocked", "failed"})
_cache: dict = {}
_lock = threading.Lock()


def containers_view(rows) -> list[dict]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "name": str(dc_client.pick(row, "name", "container", "id", default="container")),
            "id": str(dc_client.pick(row, "id", "container_id", default="") or ""),
            "state": str(dc_client.pick(row, "state", "status", default="unknown")),
            "image": str(dc_client.pick(row, "image", "image_name", default="") or ""),
            "uptime": str(dc_client.pick(row, "uptime", "running_for", "age", default="") or ""),
        })
    return out


def parse_forward_status(text: str) -> list[dict]:
    """Rows from the text ``dc forward --status`` prints (it has no --json): a ``desired:`` list of
    host:container pairs, then ``actual:`` sidecar lines ``sidecar  NAME<TAB>STATUS<TAB>PORTS``.
    A wanted port with no running sidecar is a problem row."""
    section, desired, running = "", [], {}
    for raw in str(text).splitlines():
        line = raw.strip()
        if line in ("desired:", "actual:"):
            section = line[:-1]
        elif section == "desired" and re.match(r"\d+:\d+\b", line):
            desired.append(line.split()[0].split(":")[0])
        elif section == "actual" and raw.startswith("sidecar"):
            parts = raw.split("\t")
            name = parts[0].split(None, 1)[1].strip() if len(parts[0].split(None, 1)) > 1 else ""
            state = parts[1].strip() if len(parts) > 1 else ""
            bound = re.findall(r"(\d+)->\d+/", parts[2]) if len(parts) > 2 else []
            tail = re.search(r"-(\d+)$", name)
            for port in bound or ([tail.group(1)] if tail else []):
                running[port] = (name, state)
    rows = []
    for port in sorted(set(desired) | set(running), key=int):
        name, state = running.get(port, ("", "not running"))
        rows.append({"port": port, "name": name, "state": state, "held_by": "",
                     "problem": not state.lower().startswith("up")})
    return rows


def ports_view(data) -> list[dict]:
    rows = data if isinstance(data, list) else dc_client.pick(data, "ports", "forwards", "items", default=[])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        port = dc_client.pick(row, "port", "host_port", "local_port", default="")
        held = dc_client.pick(row, "held_by", "process", default="") or ""
        state = str(dc_client.pick(row, "state", "status", default="") or "")
        out.append({"port": str(port), "name": str(dc_client.pick(row, "name", "service", "label", default="") or ""),
                    "state": state, "held_by": str(held),
                    "problem": bool(held) or state.lower() in _BAD_PORT_STATES})
    return out


def _doctor_summary(repo: Path) -> dict:
    import doctor
    check = doctor.engine_check(repo)
    return {"ok": check["status"] == "ready", "detail": check["detail"], "problems": check.get("problems", [])}


def status(repo: Path, *, deep: bool = False, now=time.monotonic) -> dict:
    """Facts about the workspace. ``deep`` adds ports and dc's own diagnosis."""
    repo = Path(repo).resolve()
    key = (str(repo), bool(deep))
    with _lock:
        hit = _cache.get(key)
        if hit and now() - hit[0] < CACHE_SECONDS:
            return hit[1]
    base = sandbox_env.env_status(repo)
    result = {"kind": base["kind"], "override": base["override"], "dc": base["dc"],
              "containers": containers_view(base["containers"]), "ports": [], "doctor": None, "recovery": None, "error": base["error"],
              "worktrees": _worktree_rows(repo), "memory": _memory_stats(repo)}
    if deep and base["dc"]["found"]:
        try:
            result["ports"] = parse_forward_status(dc_client.run_text(["forward", "--status"], repo, timeout=10))
        except dc_client.DcError as error:
            result["error"] = result["error"] or str(error)
        result["doctor"] = _doctor_summary(repo)
        try:
            import env_recovery
            result["recovery"] = env_recovery.plan(repo, timeout=15)
        except (dc_client.DcError, ValueError) as error:
            result["recovery"] = {"ready": True, "error": str(error)}
    with _lock:
        _cache[key] = (now(), result)
    return result


def _worktree_rows(repo: Path) -> list[dict]:
    """The job worktrees still in play (local reads only; a broken record is shown, not fatal)."""
    try:
        import worktrees
        return [{"job_id": r["job_id"], "kind": r.get("kind", "")} for r in worktrees.job_rows(repo)
                if r.get("kind") in ("writer", "missing") or r.get("state") == "unreadable"]
    except Exception:  # the side panel must never fail because a record is odd
        return []


def _memory_stats(repo: Path) -> dict:
    try:
        import memory
        return memory.stats(repo)
    except Exception:
        return {"lines": 0, "max": 0, "updated": 0.0}


def _ago(seconds: float) -> str:
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago" if hours < 48 else f"{hours // 24}d ago"


def _context(status: dict) -> str:
    """The short container identity the board's header shows: 'devcontainer a3f9c1'."""
    for box in status.get("containers") or []:
        if str(box.get("state", "")).lower().startswith(("up", "run")):
            return f"{status['kind']} {(box.get('id') or box.get('name') or '')[:6]}".strip()
    return ""


def _line(text: str, tone: str = "") -> dict:
    return {"text": text, "tone": tone}


def view(status: dict) -> dict:
    """Sections of {text, tone} lines. Tone is attention, ok or empty."""
    dc, sections, attention = status["dc"], [], False
    work = [_line(f"kind     {status['kind']}"),
            _line("override " + ("present" if status["override"]["exists"] else "none"))]
    if not dc["found"]:
        work.append(_line("! dc-cli is not installed", "attention"))
        attention = True
    else:
        pin = f" (pin {dc['pin']})" if dc["pin"] else " (unpinned)"
        mismatch = dc["pin_state"] == "mismatch"
        work.append(_line(("! " if mismatch else "  ") + f"dc-cli   {dc['version'] or 'unknown'}{pin}",
                          "attention" if mismatch else ""))
        attention = attention or mismatch
    for box in status["containers"]:
        up = box["state"].lower().startswith(("up", "run"))
        ident = box["id"][:12] or box["name"]
        work.append(_line(f"{'●' if up else '○'} {box['state']}  {ident}" + (f" · {box['uptime']}" if box["uptime"] else ""),
                          "ok" if up else ""))
        if box["image"]:
            work.append(_line(f"image    {box['image']}"))
    if not status["containers"] and dc["found"] and not status["error"]:
        work.append(_line("no container for this repository"))
    if status["error"]:
        text = "! " + status["error"]
        if text not in [line["text"] for line in work]:  # "dc-cli is not installed" is already said above
            work.append(_line(text, "attention"))
        attention = True
    sections.append({"title": "Workspace", "lines": work})
    if status["ports"]:
        lines = []
        for port in status["ports"]:
            text = f"{port['port']}  {port['name']}".rstrip()
            if port["held_by"]:
                text += f"  held by {port['held_by']}"
            elif port["problem"] and port["state"]:
                text += f"  {port['state']}"
            lines.append(_line(("! " if port["problem"] else "  ") + text, "attention" if port["problem"] else ""))
            attention = attention or port["problem"]
        sections.append({"title": "Ports", "lines": lines})
    rows = status.get("worktrees") or []
    if rows:
        lines = [_line("  main")]
        for row in rows[:6]:
            if row.get("kind") == "writer":
                lines.append(_line(f"  {row['job_id']}  ● writer", "ok"))
            else:
                lines.append(_line(f"! {row['job_id']}  worktree {row.get('kind') or 'unreadable'}", "attention"))
                attention = True
        if len(rows) > 6:
            lines.append(_line(f"  +{len(rows) - 6} more"))
        sections.append({"title": "Worktrees", "lines": lines})
    if status["doctor"] is not None:
        from doctor import fix_text
        doctor = status["doctor"]
        if doctor["ok"]:
            lines = [_line("● dc doctor reports no problems", "ok")]
        else:
            lines = [_line("! " + p["name"] + (f"  (fix: {fix_text(p['recover'])})" if p["recover"] else ""), "attention")
                     for p in doctor["problems"]] or [_line("! " + doctor["detail"], "attention")]
            attention = True
        sections.append({"title": "Doctor", "lines": lines})
    recovery = status.get("recovery") or {"ready": True}
    if not recovery.get("ready"):
        lines = [_line("! " + (recovery.get("summary") or recovery.get("error") or "dc recover reports a problem"), "attention")]
        if recovery.get("command"):
            lines.append(_line("  run: " + recovery["command"]))
        if recovery.get("safe"):
            lines.append(_line(f"  R applies {recovery['apply']} (confirm)", "attention"))
        sections.append({"title": "Recovery", "lines": lines})
        attention = True
    memory = status.get("memory")
    if memory and memory.get("max"):
        lines = [_line(f"{memory['lines']}/{memory['max']} lines")]
        if memory.get("updated"):
            lines.append(_line(f"updated {_ago(time.time() - memory['updated'])}"))
        sections.append({"title": "Memory", "lines": lines})
    return {"version": VIEW_VERSION, "attention": attention, "context": _context(status), "sections": sections}
