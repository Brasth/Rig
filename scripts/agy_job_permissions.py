#!/usr/bin/env python3
"""Scoped agy permissions.allow merge/restore for one Rig job."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COMMAND_ALLOW = "command(*)"
MISSING = "missing"
BACKUP_NAME = "agy-settings.bak"


def default_settings_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def backup_path(job_dir: Path) -> Path:
    return Path(job_dir) / BACKUP_NAME


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def load_object(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def dump_object(data: dict) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def is_missing_backup(path: Path) -> bool:
    return path.read_bytes().strip() == MISSING.encode("utf-8")


def merge(settings: Path, job_dir: Path) -> None:
    settings = Path(settings)
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    bak = backup_path(job_dir)
    if not bak.is_file():
        if settings.is_file():
            write_atomic(bak, settings.read_bytes())
        else:
            write_atomic(bak, (MISSING + "\n").encode("utf-8"))
    if settings.is_file():
        data = load_object(settings)
    else:
        data = {}
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
        data["permissions"] = perms
    allow = perms.get("allow")
    if not isinstance(allow, list):
        allow = []
        perms["allow"] = allow
    if COMMAND_ALLOW in allow:
        return
    allow.append(COMMAND_ALLOW)
    write_atomic(settings, dump_object(data))


def restore(settings: Path, job_dir: Path) -> None:
    settings = Path(settings)
    bak = backup_path(Path(job_dir))
    if not bak.is_file():
        return
    if is_missing_backup(bak):
        if settings.is_file():
            settings.unlink()
        return
    write_atomic(settings, bak.read_bytes())


def last_json_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def denied_actions(obj: dict | None) -> list:
    if not isinstance(obj, dict):
        return []
    acts = obj.get("denied_actions")
    return acts if isinstance(acts, list) else []


def response_text(obj: dict | None) -> str:
    if not isinstance(obj, dict):
        return ""
    val = obj.get("response")
    return val.strip() if isinstance(val, str) else ""


def denied_actions_from_log(raw: str) -> list:
    return denied_actions(last_json_object(raw))


def main() -> int:
    parser = argparse.ArgumentParser(prog="agy_job_permissions.py")
    parser.add_argument("action", choices=["merge", "restore", "denied-actions"])
    parser.add_argument("--settings", default="")
    parser.add_argument("--job-dir", default="")
    parser.add_argument("log", nargs="?", default="")
    args = parser.parse_args()

    if args.action in {"merge", "restore"}:
        if not args.job_dir:
            print("agy_job_permissions: --job-dir is required", file=sys.stderr)
            return 2
        settings = Path(args.settings) if args.settings else default_settings_path()
        job_dir = Path(args.job_dir)
        if args.action == "merge":
            merge(settings, job_dir)
        else:
            restore(settings, job_dir)
        return 0

    raw = Path(args.log).read_text(errors="replace") if args.log else sys.stdin.read()
    acts = denied_actions_from_log(raw)
    if acts:
        print(json.dumps(acts))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
