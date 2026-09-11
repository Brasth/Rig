#!/usr/bin/env python3
"""Grok UserPromptSubmit: park /queue text and block it from the model turn."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jobs as rig_jobs  # noqa: E402
import work_queue as rig_queue  # noqa: E402


def handle(payload: dict) -> dict | None:
    prompt = rig_queue.prompt_from_hook_payload(payload)
    parsed = rig_queue.parse_slash(prompt)
    if not parsed or parsed.get("action") == "list":
        return None
    cwd = (
        payload.get("workspaceRoot")
        or payload.get("workspace_root")
        or payload.get("cwd")
        or os.getcwd()
    )
    repo = rig_jobs.repo_root(str(cwd))
    result = rig_queue.apply_slash(repo, prompt)
    if not result or result.get("action") == "list":
        return None
    item = result.get("item") or {}
    action = result.get("action")
    label = "queued" if action == "add" else "cancelled"
    reason = (
        f"rig {label} {item.get('id') or ''} — {item.get('text') or ''}\n"
        f"{rig_queue.format_block(repo)}"
    )
    return {"decision": "block", "reason": reason[:2000]}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        out = handle(payload)
    except Exception as exc:
        print(json.dumps({"decision": "block", "reason": f"rig queue hook: {exc}"}))
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
