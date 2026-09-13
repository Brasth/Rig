"""Observed milestones and a quiet, literal terminal status row."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from ui_store import clean_text, read, write


def subject(job):
    identity = job.get("identity") or job
    return ":".join(str(identity.get(k) or "") for k in ("job_id", "reservation_id", "attempt_id"))


def transition(job):
    ask = job.get("ask") or {}
    return (job.get("display_state"), job.get("effective"), job.get("cancellation_state"),
            ask.get("ask_id") or ask.get("tool_use_id"),
            (job.get("verification_summary") or {}).get("accepted_at"))


def milestone(job):
    title = clean_text(job.get("task") or job.get("job_id"), 65)
    state = job.get("display_state")
    cancel = job.get("cancellation_state")
    if job.get("effective") == "ask":
        return f"{title} needs approval", True
    if cancel == "stopped" or (job.get("effective") == "cancelled" and (job.get("reservation") or {}).get("stopped")):
        return f"Stopped: {title}", False
    if cancel in {"stop-unconfirmed", "native-cancel-required"}:
        return f"{title}: " + ("owning host must interrupt" if cancel == "native-cancel-required" else "stop requested; unconfirmed"), True
    if state == "needs-input":
        return f"{title}: {clean_text(job.get('display_reason') or 'needs attention', 70)}", True
    if state == "failed":
        return f"{title}: {clean_text(job.get('display_reason') or 'execution failed', 70)}", True
    if state == "verified":
        return f"{title} verified", False
    if state == "completed-unverified":
        return f"{title} finished · verification pending", False
    if state == "verifying":
        return f"Verifying: {title}", False
    if state == "working":
        return f"Started: {title} · {clean_text(job.get('worker') or 'worker', 25)}", False
    return None


class Notices:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        saved = read(path, {})
        self.items = saved.get("items", [])[-500:]
        self.seen = {k: tuple(v) for k, v in saved.get("seen", {}).items()}
        self.queued = set(saved.get("queued", []))
        # A fresh collector has not revalidated historical verification evidence.
        # Establish a quiet baseline on every observer start; retain notice history.
        self.baseline = False
        self.read_by = saved.get("read_by", {})

    def save(self):
        write(self.path, {"items": self.items[-500:], "seen": self.seen,
                         "queued": list(self.queued), "baseline": self.baseline, "read_by": self.read_by})

    def add(self, text, attention=False, job_id="", dedup=""):
        if dedup and any(item.get("dedup") == dedup for item in self.items):
            return
        self.items.append({"id": uuid.uuid4().hex, "text": clean_text(text, 200),
                           "attention": attention, "job_id": job_id, "at": self.clock(), "dedup": dedup})
        self.items = self.items[-500:]

    def update(self, snapshot):
        if snapshot.get("error"):
            return
        changed = False
        current = {}
        for job in snapshot.get("jobs", []):
            key, state = subject(job), transition(job)
            current[key] = state
            if self.baseline and self.seen.get(key) != state:
                value = milestone(job)
                if value:
                    self.add(*value, job_id=job.get("job_id", ""))
                    changed = True
        queued = {row["id"] for row in snapshot.get("pending", [])}
        if self.baseline:
            for row in snapshot.get("pending", []):
                if row["id"] not in self.queued:
                    self.add("Queued: " + clean_text(row.get("text"), 100), dedup="queue:" + row["id"])
                    changed = True
        dirty = current != self.seen or queued != self.queued or not self.baseline
        self.seen, self.queued, self.baseline = current, queued, True
        if dirty or changed:
            self.save()

    def list(self, session=""):
        seen = set(self.read_by.get(session, []))
        return [{**item, "unread": item["id"] not in seen} for item in reversed(self.items)]

    def ack(self, session, notice_id="", all=False):
        ids = {item["id"] for item in self.items}
        selected = ids if all else {notice_id} & ids
        self.read_by[session] = list((set(self.read_by.get(session, [])) | selected) & ids)
        self.save()


def age_seconds(stamp, now):
    try:
        return max(0, now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def status_line(snapshot, notices=(), *, now=None, width=160, manager_key="F8", add_key="F9"):
    now = time.time() if now is None else now
    rows = snapshot.get("jobs", [])
    unread_failures = {item.get("job_id") for item in notices if item.get("attention") and item.get("unread", True)}
    attention = [row for row in rows if row.get("display_state") == "needs-input" or
                 row.get("cancellation_state") in {"stop-unconfirmed", "native-cancel-required"} or
                 (row.get("display_state") == "failed" and
                  (row.get("job_id") in unread_failures or
                   ((row.get("reservation") or {}).get("stage") not in {None, "released"})))]
    active = [row for row in rows if row.get("display_state") in {"working", "verifying", "reserved"}]
    reserved = sum(row.get("display_state") == "reserved" for row in active)
    prefix = f"Rig · {len(active) - reserved} working"
    if reserved:
        prefix += f" · {reserved} reserved"
    prefix += f" · {len(snapshot.get('pending', []))} queued · !{len(attention)}"
    detail = "Idle"
    if snapshot.get("error") or snapshot.get("snapshot_age", 0) > 5:
        detail = "status stale · " + clean_text(snapshot.get("error") or "refresh delayed", 55)
    else:
        recent = [item for item in notices if now - item["at"] < (8 if item["attention"] else 4)]
        if recent:
            detail = recent[0]["text"] if len(recent) == 1 else f"{len(recent)} updates · {recent[0]['text']}"
        elif attention or active:
            row = attention[0] if attention else active[int(now // 6) % len(active)]
            detail = clean_text(row.get("task") or row.get("job_id"), 45) + ": " + clean_text(row.get("display_reason") or row.get("doing"), 65)
            stamp = row.get("last_activity_at") or row.get("activity_updated_at") or row.get("doing_updated_at")
            age = age_seconds(stamp, now)
            detail += f" · {int(age)}s ago" if age is not None else " · update age unknown"
    hint = f"{clean_text(manager_key, 15)} Manage · {clean_text(add_key, 15)} Add"
    room = max(0, width - len(prefix) - len(hint) - 6)
    return clean_text(f"{prefix} | {clean_text(detail, room)} | {hint}", width)
