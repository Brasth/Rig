#!/usr/bin/env python3
"""A helper's thread with the parent, for the shell: the brief the parent gave it, what it did, a message the parent
left it, a question it asks, what it reported, and how it ended. Read-only; everything comes from the job's folder."""
from __future__ import annotations

from pathlib import Path

import jobs as rig_jobs

MAX_BRIEF = 6000
MAX_ACTIVITY = 400


def _parent(repo: Path) -> str:
    try:
        import rig_chat
        return rig_chat.default_parent(repo) or "parent"
    except Exception:
        return "parent"


def _ending(job: dict) -> str:
    status, code = job.get("effective") or job.get("status"), job.get("exit_code")
    if status in ("running", "ask", "reserved"):
        return ""
    if status == "ok":
        return "finished"
    if status == "cancelled":
        return "stopped"
    if status == "timeout":
        return "timed out"
    if status == "stale":
        return "stopped answering"
    if status in ("fail", "failed"):
        return f"failed (exit {code})" if code not in (None, 0) else "failed"
    return str(status or "")


def thread(repo, job_id: str) -> dict:
    repo = Path(repo).resolve()
    if not job_id or "/" in job_id or job_id.startswith(".") or not (repo / ".rig" / "jobs" / job_id).is_dir():
        raise ValueError(f"no such job: {job_id}")
    job = rig_jobs.resolve_job(repo, job_id)
    parent, worker = _parent(repo), str(job.get("worker") or "helper")
    entries = []
    brief = ""
    try:
        brief = (Path(job["dir"]) / "brief.md").read_text(errors="replace").strip()
    except OSError:
        pass
    brief = brief or str(job.get("task") or "").strip()
    if brief:
        if len(brief) > MAX_BRIEF:
            brief = brief[:MAX_BRIEF].rstrip() + "\n…"
        entries.append({"who": parent, "kind": "brief", "text": brief, "at": job.get("started_at") or ""})
    for line in (job.get("activities") or [])[-MAX_ACTIVITY:]:
        if str(line).strip():
            entries.append({"who": worker, "kind": "activity", "text": str(line)})
    import inbox as rig_inbox
    sent = rig_inbox.messages(Path(job["dir"]))
    pending = job.get("inbox") if isinstance(job.get("inbox"), dict) else None
    if not sent and pending and pending.get("text"):
        sent = [pending]  # a job from before messages were kept: only the one still waiting
    for msg in sent:
        waiting = bool(pending) and pending.get("sent_at") == msg.get("sent_at") and pending.get("text") == msg.get("text")
        entries.append({"who": parent, "kind": "message", "text": str(msg["text"]), "at": msg.get("sent_at") or "",
                        "unread": waiting})
    ask = job.get("ask")
    if isinstance(ask, dict):
        entries.append({"who": worker, "kind": "ask", "text": str(ask.get("preview") or ask.get("tool_name") or "a permission")})
    ending = _ending(job)
    if ending and str(job.get("summary") or "").strip():
        entries.append({"who": worker, "kind": "result", "text": str(job["summary"]).strip(), "at": job.get("ended_at") or ""})
    return {"job_id": job_id, "worker": worker, "parent": parent, "title": str(job.get("task") or job_id),
            "running": not ending, "ending": ending, "entries": entries}
