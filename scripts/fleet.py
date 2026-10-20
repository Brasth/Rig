"""Fleet panel: the workspace containers dc-cli knows about, with CPU and memory as observed now.

`dc ls --all --json` lists every labelled container on the engine, this repository's or not; `dc stats
--json WORKSPACE` adds observed CPU and memory for the running ones. Values are shown as dc-cli reports
them and are never summed into a cost or an estimate (docs/cost-accounting.md).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import dc_client

CACHE_SECONDS = 5.0
MAX_STATS = 12
STATS_BUDGET = 12.0  # seconds for all stats calls together; a slow engine must not stall the panel
_lock = threading.Lock()
_cache: dict = {}


def _mb(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(number) < 1024 or unit == "T":
            return f"{number:.0f}{unit}" if unit in ("B", "K") else f"{number:.1f}{unit}".replace(".0", "")
        number /= 1024
    return "?"


def facts(repo, *, now=time.monotonic) -> dict:
    repo = Path(repo).resolve()
    with _lock:
        hit = _cache.get(str(repo))
        if hit and now() - hit[0] < CACHE_SECONDS:
            return hit[1]
    result = {"containers": [], "guest": None, "error": ""}
    if not dc_client.dc_binary():
        result["error"] = "dc-cli is not installed"
    else:
        try:
            rows = dc_client.run_json(["ls", "--all", "--json"], repo)
            worktrees_root = str(repo / ".rig" / "worktrees")
            running = {}
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                folder = str(dc_client.pick(row, "local_folder", "folder", default="") or "")
                entry = {"id": str(row.get("id") or ""), "name": str(row.get("name") or ""), "folder": folder,
                         "status": str(row.get("status") or ""), "kind": str(row.get("kind") or ""),
                         "mine": folder == str(repo) or folder.startswith(worktrees_root + "/"),
                         "job": Path(folder).name if folder.startswith(worktrees_root + "/") else "",
                         "cpu_pct": None, "mem_used": None, "mem_limit": None, "image": ""}
                result["containers"].append(entry)
                if entry["status"].lower().startswith("running") and folder and len(running) < MAX_STATS:
                    running.setdefault(folder, []).append(entry)
            deadline = time.monotonic() + STATS_BUDGET
            for folder, entries in running.items():
                left = deadline - time.monotonic()
                if left <= 1:
                    break
                try:
                    stats = dc_client.run_json(["stats", "--json", folder], repo, timeout=min(5.0, left))
                except dc_client.DcError:
                    continue
                if result["guest"] is None and isinstance(stats.get("guest"), dict):
                    result["guest"] = stats["guest"]
                by_id = {str(item.get("id")): item for item in stats.get("containers", []) if isinstance(item, dict)}
                for entry in entries:
                    item = by_id.get(entry["id"])
                    if item:
                        entry.update(cpu_pct=item.get("cpuPct"), mem_used=item.get("memUsedBytes"),
                                     mem_limit=item.get("memLimitBytes"), image=str(item.get("service") or ""))
            result["containers"].sort(key=lambda row: (not row["mine"], not row["status"].lower().startswith("running"), row["folder"]))
        except dc_client.DcError as error:
            result["error"] = str(error)
    with _lock:
        _cache[str(repo)] = (now(), result)
    return result


def _line(text, tone=""):
    return {"text": text, "tone": tone}


def view(data: dict) -> dict:
    sections = []
    for title, mine in (("This repository", True), ("Other workspaces", False)):
        lines = []
        for box in (row for row in data["containers"] if row["mine"] == mine):
            up = box["status"].lower().startswith("running")
            stats = ""
            if box["cpu_pct"] is not None:
                stats = f"  cpu {box['cpu_pct']:.1f}%  mem {_mb(box['mem_used'])}/{_mb(box['mem_limit'])}"
            label = f"job {box['job']}" if box["job"] else Path(box["folder"]).name or box["name"]
            lines.append(_line(f"{'●' if up else '○'} {label}  {box['status']}{stats}", "ok" if up else ""))
            if not mine:
                lines.append(_line(f"    {box['folder']}"))
        if lines:
            sections.append({"title": title, "lines": lines})
    guest = data.get("guest")
    if guest:
        text = f"{guest.get('label', 'engine')}  {guest.get('cpus', '?')} cpus  {_mb(guest.get('memoryBytes'))} memory"
        if guest.get("live"):
            text += f"  in use cpu {guest.get('cpuPct', 0):.1f}% mem {_mb(guest.get('memoryUsedBytes'))}"
        sections.append({"title": "Engine", "lines": [_line(text)]})
    if data["error"]:
        sections.append({"title": "Note", "lines": [_line("! " + data["error"], "attention")]})
    if not sections:
        sections.append({"title": "Fleet", "lines": [_line("no workspace containers")]})
    return {"sections": sections, "attention": bool(data["error"])}
