#!/usr/bin/env python3
"""Workspace panel data: container, ports and dc-cli's own diagnosis.

All dc-cli JSON is read here (see dc_client for the assumed contract) and turned
into ready-to-render sections, so clients such as the Go shell render text and
never parse dc output. Reads are cached briefly so a polling client cannot make
Rig run dc more than once every few seconds.
"""
from __future__ import annotations

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
              "containers": containers_view(base["containers"]), "ports": [], "doctor": None, "error": base["error"]}
    if deep and base["dc"]["found"]:
        try:
            result["ports"] = ports_view(dc_client.run_json(["forward", "--status", "--json"], repo, timeout=10))
        except dc_client.DcError as error:
            result["error"] = result["error"] or str(error)
        result["doctor"] = _doctor_summary(repo)
    with _lock:
        _cache[key] = (now(), result)
    return result


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
        work.append(_line("! " + status["error"], "attention"))
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
    if status["doctor"] is not None:
        doctor = status["doctor"]
        if doctor["ok"]:
            lines = [_line("● dc doctor reports no problems", "ok")]
        else:
            lines = [_line("! " + p["name"] + (f"  (fix: dc recover {p['recover']})" if p["recover"] else ""), "attention")
                     for p in doctor["problems"]] or [_line("! " + doctor["detail"], "attention")]
            attention = True
        sections.append({"title": "Doctor", "lines": lines})
    return {"version": VIEW_VERSION, "attention": attention, "sections": sections}
