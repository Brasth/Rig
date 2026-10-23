#!/usr/bin/env python3
"""Plan limits: how much of each provider's plan is used, and who should be the parent while one is out.

Rig never guesses a plan's size. A meter is shown only when a provider reported it:

* codex writes its own rate-limit windows into its session files (``~/.codex/sessions``); the newest one is read.
* any provider's status line or hook can report with ``limits.report`` (or ``rig api limits.report``), which keeps
  ``$RIG_HOME/limits.json``; claude's status line carries its plan windows that way.

A meter whose window has reset reads as reset, not as its old number. A report older than ``MAX_AGE`` with no reset
time is dropped. Handing the parent role over is a decision this module makes and the caller applies: it is never done
silently, and the role goes back when the provider that ran out has reset."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROVIDERS = ("claude", "codex", "cursor", "opencode")
WARN = 80.0   # a provider this full is steered away from for new work, and named in routing notes
OUT = 95.0    # a parent this full should hand over
MAX_AGE = 6 * 3600


def _home() -> Path:
    return Path(os.environ.get("RIG_HOME") or Path.home() / ".rig")


def _file() -> Path:
    return _home() / "limits.json"


def _state_file() -> Path:
    return _home() / "limits-handover.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(temporary, path)


def _pct(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= 100 else None


def report(provider: str, used_pct, resets_at=None, window: str = "", source: str = "reported", now=None) -> dict:
    """Keep what a provider said about its plan: percent used, when that window resets (epoch seconds), its name."""
    if provider not in PROVIDERS:
        raise ValueError(f"provider is one of {', '.join(PROVIDERS)}")
    pct = _pct(used_pct)
    if pct is None:
        raise ValueError("used_pct is a number from 0 to 100")
    if resets_at is not None and (isinstance(resets_at, bool) or not isinstance(resets_at, (int, float)) or resets_at < 0):
        raise ValueError("resets_at is epoch seconds")
    now = time.time() if now is None else now
    data = _load(_file())
    windows = [w for w in data.get(provider, {}).get("windows", []) if w.get("label") != (window or "plan")]
    windows.append({"label": window or "plan", "used_pct": pct, "resets_at": resets_at})
    data[provider] = {"windows": windows, "source": source, "at": now}
    _save(_file(), data)
    return data[provider]


def _codex_windows(home: Path | None = None) -> dict | None:
    """The newest rate-limit windows codex wrote into its session files, or None."""
    root = (home or Path.home()) / ".codex" / "sessions"
    try:
        files = sorted(root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    except OSError:
        return None
    for path in files:
        try:
            lines = path.read_text(errors="replace").splitlines()[-400:]
            at = path.stat().st_mtime
        except OSError:
            continue
        for line in reversed(lines):
            if "rate_limits" not in line:
                continue
            try:
                payload = json.loads(line).get("payload") or {}
            except ValueError:
                continue
            limits = payload.get("rate_limits")
            if not isinstance(limits, dict):
                continue
            windows = []
            for key in ("primary", "secondary"):
                w = limits.get(key)
                pct = _pct(w.get("used_percent")) if isinstance(w, dict) else None
                if pct is None:
                    continue
                minutes = w.get("window_minutes")
                label = "5 hours" if minutes == 300 else "week" if minutes == 10080 else f"{minutes} min" if minutes else key
                resets = w.get("resets_at")
                if resets is None and isinstance(w.get("resets_in_seconds"), (int, float)):
                    resets = at + w["resets_in_seconds"]
                windows.append({"label": label, "used_pct": pct, "resets_at": resets})
            if windows:
                return {"windows": windows, "source": "codex session files", "at": at}
    return None


def _live(entry: dict, now: float) -> dict | None:
    """A meter as it is now: windows already reset drop to 0 and say so; an old report with no reset time is dropped."""
    windows = []
    for w in entry.get("windows", []):
        pct, resets = _pct(w.get("used_pct")), w.get("resets_at")
        if pct is None:
            continue
        reset = isinstance(resets, (int, float)) and resets <= now
        if resets is None and now - float(entry.get("at", 0)) > MAX_AGE:
            continue
        windows.append({"label": w.get("label", "plan"), "used_pct": 0.0 if reset else pct,
                        "resets_at": None if reset else resets, "reset": reset})
    if not windows:
        return None
    worst = max(windows, key=lambda w: w["used_pct"])
    return {"windows": windows, "used_pct": worst["used_pct"], "resets_at": worst["resets_at"],
            "source": entry.get("source", ""), "at": entry.get("at")}


def meters(now=None, codex_home: Path | None = None) -> dict:
    """Every provider's plan meter that anyone reported, newest word winning between a report and codex's own files."""
    now = time.time() if now is None else now
    found = {name: entry for name, entry in _load(_file()).items() if name in PROVIDERS and isinstance(entry, dict)}
    codex = _codex_windows(codex_home)
    if codex and codex["at"] >= float(found.get("codex", {}).get("at", 0)):
        found["codex"] = codex
    out = {}
    for name, entry in found.items():
        live = _live(entry, now)
        if live:
            out[name] = live
    return out


def decide(original: str, acting: str, candidates: list[str], current: dict, threshold: float = OUT) -> dict:
    """Who should be the parent. ``original`` is the project's own parent, ``acting`` the one now in the role (they differ
    after a hand-over), ``candidates`` the other parents that are installed. Returns {"action": stay|handover|back, ...}."""
    def used(name):
        return current.get(name, {}).get("used_pct")
    if acting != original:
        back = used(original)
        if back is None or back < WARN:
            if current.get(original, {}).get("windows") and all(w.get("reset") or w["used_pct"] < WARN for w in current[original]["windows"]):
                return {"action": "back", "to": original, "from": acting, "reason": f"{original} has reset"}
        return {"action": "stay", "reason": f"{original} is not back yet"}
    pct = used(acting)
    if pct is None or pct < threshold:
        return {"action": "stay", "reason": ""}
    roomy = sorted((c for c in candidates if c != acting and (used(c) is None or used(c) < WARN)), key=lambda c: (used(c) is None, used(c) or 0))
    if not roomy:
        return {"action": "stay", "reason": f"{acting} is at {pct:.0f}% and no other parent has room"}
    return {"action": "handover", "to": roomy[0], "from": acting, "reason": f"{acting} is at {pct:.0f}%"}


def note(current: dict, tasks: dict | None = None) -> str:
    """The routing note: a provider nearly full, and where its work goes meanwhile, as "tests → codex, claude at 92%"."""
    full = sorted(((n, m["used_pct"]) for n, m in current.items() if m["used_pct"] >= WARN), key=lambda p: -p[1])
    if not full:
        return ""
    name, pct = full[0]
    others = [n for n in current if n != name and current[n]["used_pct"] < WARN]
    target = others[0] if others else ""
    return f"{'tests → ' + target + ', ' if target else ''}{name} at {pct:.0f}%"


def state(repo) -> dict:
    return _load(_state_file()).get(str(Path(repo).resolve()), {})


def remember_handover(repo, original: str, acting: str) -> None:
    data = _load(_state_file())
    key = str(Path(repo).resolve())
    if acting == original:
        data.pop(key, None)
    else:
        data[key] = {"original": original, "acting": acting, "since": time.time()}
    _save(_state_file(), data)


def view(repo, parent: str, installed: list[str], now=None, codex_home: Path | None = None) -> dict:
    """What the limits screen shows: each provider's meters, the routing note, and what to do about the parent."""
    current = meters(now, codex_home)
    saved = state(repo)
    original = saved.get("original") or parent
    acting = parent
    suggestion = decide(original, acting, [p for p in PROVIDERS if p in installed], current)
    return {"meters": current, "note": note(current), "parent": acting, "original": original, "suggestion": suggestion,
            "providers": [{"name": p, "installed": p in installed} for p in PROVIDERS]}


def main(argv=None) -> int:
    """rig limits [show] | report PROVIDER PERCENT [--window NAME] [--resets EPOCH]: the terminal's view of the same meters,
    and the way a status line or hook tells Rig what a plan has left."""
    import argparse
    import shutil
    parser = argparse.ArgumentParser(prog="rig limits")
    parser.add_argument("command", nargs="?", default="show", choices=("show", "report"))
    parser.add_argument("provider", nargs="?")
    parser.add_argument("percent", nargs="?", type=float)
    parser.add_argument("--window", default="")
    parser.add_argument("--resets", type=float)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    if args.command == "report":
        if args.provider is None or args.percent is None:
            parser.error("report needs PROVIDER and PERCENT")
        try:
            report(args.provider, args.percent, args.resets, args.window)
        except ValueError as error:
            print(f"rig limits: {error}", file=sys.stderr)
            return 2
    import harness
    parent = harness.parse_harness(harness.harness_path(Path(args.repo).resolve()))["parent"] or ""
    shown = view(args.repo, parent, [p for p in PROVIDERS if shutil.which(p)])
    if not shown["meters"]:
        print("No plan has reported its usage yet. A status line or hook can: rig limits report claude 42 --resets EPOCH")
        return 0
    now = time.time()
    for name, m in sorted(shown["meters"].items(), key=lambda item: -item[1]["used_pct"]):
        when = "no reset time" if m["resets_at"] is None else f"resets in {max(0, round((m['resets_at'] - now) / 60))}m"
        print(f"{name:10} {m['used_pct']:5.0f}%  {when}")
    if shown["note"]:
        print(shown["note"])
    s = shown["suggestion"]
    if s["action"] != "stay":
        print(f"{s['reason']}: parent {'to' if s['action'] == 'handover' else 'back to'} {s['to']} (rig api call limits.handover '{{\"to\":\"{s['to']}\"}}')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
