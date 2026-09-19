"""Observed milestones and a quiet, literal terminal status row."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from ui_snapshot import _ATTENTION_STATUSES, _ACTIVE_STATUSES, format_parent_action, scrub_secrets
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
    if state == "needs-input" or job.get("effective") == "unconfirmed":
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


def workflow_subject(row):
    return "wf:" + str(row.get("workflow_id") or "")


def workflow_transition(row):
    action = row.get("next_parent_action") or {}
    kind = action.get("kind") if isinstance(action, dict) else action
    return (row.get("status"), row.get("blocker"), kind)


def workflow_milestone(row):
    wid = clean_text(row.get("workflow_id") or "workflow", 40)
    blocker = clean_text(row.get("blocker"), 70)
    status = row.get("status")
    if status == "attention":
        extra = blocker or format_parent_action(row.get("next_parent_action"))
        return f"Workflow {wid} needs attention" + (f": {extra}" if extra and extra != "none" else ""), True
    if status == "blocked":
        return f"Workflow {wid} blocked" + (f": {blocker}" if blocker else ""), True
    if status == "cancel-requested":
        return f"Workflow {wid}: cancellation requested", True
    if status == "failed":
        return f"Workflow {wid} failed" + (f": {blocker}" if blocker else ""), True
    if status == "verified":
        return f"Workflow {wid} verified", False
    if status == "running":
        return f"Started workflow {wid}", False
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

    def add(self, text, attention=False, job_id="", dedup="", workflow_id=""):
        if dedup and any(item.get("dedup") == dedup for item in self.items):
            return
        item = {"id": uuid.uuid4().hex, "text": clean_text(scrub_secrets(text), 200),
                "attention": attention, "job_id": job_id, "at": self.clock(), "dedup": dedup}
        if workflow_id:
            item["workflow_id"] = str(workflow_id)
        self.items.append(item)
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
        for row in snapshot.get("workflows") or []:
            row = scrub_secrets(row) if isinstance(row, dict) else {}
            key, state = workflow_subject(row), workflow_transition(row)
            current[key] = state
            if self.baseline and self.seen.get(key) != state:
                value = workflow_milestone(row)
                if value:
                    self.add(*value, workflow_id=row.get("workflow_id", ""),
                             dedup="wf:" + str(row.get("workflow_id") or "") + ":" + str(state))
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
    workflows = [row for row in (snapshot.get("workflows") or []) if isinstance(row, dict)]
    wf_active = [row for row in workflows if row.get("status") in _ACTIVE_STATUSES]
    wf_attention = [row for row in workflows if row.get("status") in _ATTENTION_STATUSES]
    if wf_active:
        prefix += f" · {len(wf_active)} wf"
    if wf_attention:
        prefix += f" · wf!{len(wf_attention)}"
    prefix += f" · {len(snapshot.get('pending', []))} queued · !{len(attention) + len(wf_attention)}"
    detail = "Idle"
    if snapshot.get("error") or snapshot.get("snapshot_age", 0) > 5:
        detail = "status stale · " + clean_text(snapshot.get("error") or "refresh delayed", 55)
    else:
        recent = [item for item in notices if now - item["at"] < (8 if item["attention"] else 4)]
        if recent:
            detail = recent[0]["text"] if len(recent) == 1 else f"{len(recent)} updates · {recent[0]['text']}"
        elif attention or wf_attention or active:
            if attention:
                row = attention[0]
                detail = clean_text(row.get("task") or row.get("job_id"), 45) + ": " + clean_text(row.get("display_reason") or row.get("doing"), 65)
                stamp = row.get("last_activity_at") or row.get("activity_updated_at") or row.get("doing_updated_at")
                age = age_seconds(stamp, now)
                detail += f" · {int(age)}s ago" if age is not None else " · update age unknown"
            elif wf_attention:
                row = wf_attention[0]
                detail = clean_text(row.get("workflow_id"), 40) + ": " + clean_text(
                    row.get("blocker") or format_parent_action(row.get("next_parent_action")) or row.get("status"), 70)
            else:
                row = active[int(now // 6) % len(active)]
                detail = clean_text(row.get("task") or row.get("job_id"), 45) + ": " + clean_text(row.get("display_reason") or row.get("doing"), 65)
                stamp = row.get("last_activity_at") or row.get("activity_updated_at") or row.get("doing_updated_at")
                age = age_seconds(stamp, now)
                detail += f" · {int(age)}s ago" if age is not None else " · update age unknown"
    hint = f"{clean_text(manager_key, 15)} Manage · {clean_text(add_key, 15)} Add"
    room = max(0, width - len(prefix) - len(hint) - 6)
    return clean_text(f"{prefix} | {clean_text(detail, room)} | {hint}", width)
