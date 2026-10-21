#!/usr/bin/env python3
"""Every model a parent CLI can be told to use, for the shell's /model: what the CLI itself lists, else what its own
files keep, and the models Rig routes it to. Slow listings are cached under ~/.rig for an hour."""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path

import catalog
import route
import ui_theme

TTL_SECONDS = 3600
PROBE_TIMEOUT = 8.0
_LOCK = threading.Lock()

# claude's own aliases: they follow the newest model of each family
CLAUDE_ALIASES = (("opus", "the newest Opus"), ("sonnet", "the newest Sonnet"), ("haiku", "the newest Haiku"),
                  ("opusplan", "Opus plans, Sonnet does the work"), ("sonnet[1m]", "Sonnet with a million-token context"))
PREFIXES = {"claude": ("claude-",), "codex": ("gpt-", "o3", "o4", "codex-"), "grok": ("grok-",)}


def _cache_path() -> Path:
    return ui_theme.global_path().parent / "cache" / "parent-models.json"


def _cached(parent: str) -> list[dict] | None:
    try:
        entry = json.loads(_cache_path().read_text()).get(parent) or {}
    except (OSError, ValueError, AttributeError):
        return None
    if time.time() - float(entry.get("at", 0)) > TTL_SECONDS or not isinstance(entry.get("models"), list):
        return None
    return entry["models"]


def _keep(parent: str, models: list[dict]) -> None:
    path = _cache_path()
    with _LOCK:
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data[parent] = {"at": time.time(), "models": models}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except OSError:
            pass


def _row(entry, *names) -> dict | None:
    """One listed model as {name, detail}, from the keys a CLI's listing uses for its id and its description."""
    if isinstance(entry, str):
        return {"name": entry.strip(), "detail": ""} if entry.strip() else None
    if not isinstance(entry, dict) or str(entry.get("visibility") or "").lower() in ("hide", "hidden", "none"):
        return None
    name = next((entry[k].strip() for k in names if isinstance(entry.get(k), str) and entry[k].strip()), "")
    if not name:
        return None
    detail = next((entry[k].strip() for k in ("description", "display_name", "displayName", "name")
                   if isinstance(entry.get(k), str) and entry[k].strip() and entry[k].strip() != name), "")
    return {"name": name, "detail": detail.splitlines()[0][:80]}


def _listed(data, *names) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("models") or data.get("data") or []
    rows = [_row(e, *names) for e in data] if isinstance(data, list) else []
    return [r for r in rows if r]


def codex_app_server(timeout: float = PROBE_TIMEOUT) -> list[dict]:
    """codex's own model list: `codex app-server` answers model/list, as its /model menu does."""
    binary = shutil.which("codex")
    if not binary:
        return []
    try:
        proc = subprocess.Popen([binary, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return []
    rows: list[dict] = []
    try:
        for message in ({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "rig", "title": "Rig", "version": "1"}}},
                        {"method": "initialized"}, {"id": 1, "method": "model/list", "params": {}}):
            proc.stdin.write((json.dumps(message) + "\n").encode())
        proc.stdin.flush()
        deadline, pending, answered = time.monotonic() + timeout, b"", False
        while not answered and time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], max(0.0, deadline - time.monotonic()))
            chunk = os.read(proc.stdout.fileno(), 65536) if ready else b""
            if not chunk:
                break
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if isinstance(reply, dict) and reply.get("id") == 1:
                    rows, answered = _listed(reply.get("result") or {}, "model", "id", "slug"), True
                    break
    except (OSError, ValueError):
        pass
    finally:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return rows


def _codex_files() -> list[dict]:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    rows = []
    try:
        rows += _listed(json.loads((home / "models_cache.json").read_text()), "slug", "model", "id")
    except (OSError, ValueError):
        pass
    try:
        import child_mcp
        config = child_mcp._load_toml(home / "config.toml")
        named = [config.get("model")] + [p.get("model") for p in (config.get("profiles") or {}).values() if isinstance(p, dict)]
        rows += [{"name": n.strip(), "detail": "in codex's config"} for n in named if isinstance(n, str) and n.strip()]
    except (OSError, ValueError, AttributeError, UnicodeError):
        pass
    return rows


def _grok_files() -> list[dict]:
    try:
        settings = json.loads((Path.home() / ".grok" / "user-settings.json").read_text())
    except (OSError, ValueError):
        return []
    rows = _listed(settings.get("models") if isinstance(settings, dict) else [], "model", "id", "name")
    if isinstance(settings, dict) and isinstance(settings.get("defaultModel"), str):
        rows.insert(0, {"name": settings["defaultModel"].strip(), "detail": "grok's default"})
    return rows


def _routed(parent: str) -> list[dict]:
    """The models Rig routes this CLI to, and the parent-only ones of its family."""
    names = [model for (worker, _kind), (model, _effort) in route.MODELS.items() if worker == parent and model]
    names += sorted(m for m in route.PARENT_ONLY if m.startswith(PREFIXES.get(parent, ("\0",))))
    return [{"name": n, "detail": ""} for n in names]


def live(parent: str, timeout: float = PROBE_TIMEOUT) -> list[dict]:
    """What the CLI itself lists, asked at most once an hour."""
    cached = _cached(parent)
    if cached is not None:
        return cached
    rows: list[dict] = []
    if parent == "codex":
        rows = codex_app_server(timeout)
    elif parent in catalog.CATALOG_WORKERS:
        ids = catalog.load_catalog_info(parent, timeout).get("ids") or []
        rows = [{"name": i, "detail": ""} for i in ids]
    if rows:
        _keep(parent, rows)
    return rows


def models(parent: str, timeout: float = PROBE_TIMEOUT) -> list[dict]:
    """Every model to offer for a parent, each once, the CLI's own listing first."""
    rows = live(parent, timeout)
    if parent == "claude":
        rows = [{"name": n, "detail": d} for n, d in CLAUDE_ALIASES] + rows
    elif parent == "codex":
        rows = rows + _codex_files()
    elif parent == "grok":
        rows = rows + _grok_files()
    rows = rows + _routed(parent)
    seen, out = set(), []
    for r in rows:
        key = r["name"].lower()
        if key in seen:
            for kept in out:  # a later source may know what the model is
                if kept["name"].lower() == key and not kept["detail"]:
                    kept["detail"] = r["detail"]
            continue
        seen.add(key)
        out.append({"name": r["name"], "detail": r["detail"]})
    return out
