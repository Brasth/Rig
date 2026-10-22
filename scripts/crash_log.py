#!/usr/bin/env python3
"""Where an unexpected error came from: a short location for the message, the full traceback in .rig/mcp-errors.log.

A bare "list index out of range" says nothing about which file raised it; the location and the log do."""
from __future__ import annotations

import time
import traceback
from pathlib import Path

LOG_NAME = "mcp-errors.log"
LOG_LIMIT = 64_000  # the log keeps the newest of this many bytes


def location(exc: BaseException) -> str:
    """The innermost frame inside Rig's scripts: "IndexError at jobs.py:113 in current_thread"."""
    here = Path(__file__).resolve().parent
    frames = traceback.extract_tb(exc.__traceback__)
    mine = [f for f in frames if Path(f.filename).resolve().parent == here or here in Path(f.filename).resolve().parents]
    frame = (mine or frames or [None])[-1]
    if frame is None:
        return type(exc).__name__
    return f"{type(exc).__name__} at {Path(frame.filename).name}:{frame.lineno} in {frame.name}"


def describe(exc: BaseException) -> str:
    """The error's own text with where it came from: "list index out of range (IndexError at jobs.py:113 in current_thread)"."""
    text = str(exc).strip()
    return f"{text} ({location(exc)})" if text else location(exc)


def record(repo, where: str, exc: BaseException) -> None:
    """Append the traceback to <repo>/.rig/mcp-errors.log, best effort, newest kept."""
    try:
        path = Path(repo) / ".rig" / LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"--- {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {where}\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n"
        old = path.read_text(errors="replace") if path.is_file() else ""
        path.write_text((old + entry)[-LOG_LIMIT:])
    except OSError:
        pass
