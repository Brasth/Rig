#!/usr/bin/env python3
"""Parent → child inbox. Pull-only. Not ASK; wait does not wake on this file."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

INBOX_NAME = "inbox.json"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inbox_path(job_dir: Path) -> Path:
    return Path(job_dir) / INBOX_NAME


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


def write_inbox(job_dir: Path, text: str) -> dict:
    line = " ".join(str(text).split())
    if not line:
        raise ValueError("inbox text required")
    if len(line) > 2000:
        line = line[:1999] + "…"
    obj = {"text": line, "sent_at": iso_now()}
    _write_json(inbox_path(job_dir), obj)
    return obj


def load_inbox(job_dir: Path) -> dict | None:
    return _read_json(inbox_path(job_dir))


def consume_inbox(job_dir: Path) -> dict | None:
    path = inbox_path(job_dir)
    obj = _read_json(path)
    if path.is_file():
        path.unlink(missing_ok=True)
    return obj
