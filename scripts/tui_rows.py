"""Presentation-only board rows: readable state labels, ordering, and row text.

Labels never change persisted state, CLI output, or MCP payloads. They read the
already-projected row fields and nothing else (no filesystem access).
"""
from __future__ import annotations

import jobs as rig_jobs
from tui_text import _fit_head, _pad, _text_width, abbrev_id, first_line
from ui_snapshot import _ACTIVE_STATUSES, _ATTENTION_STATUSES

_TABS = ("Jobs", "Queue", "Workflows", "Settings")
_ASK_STATES = frozenset({"needs-input"})
_JOB_ATTENTION_STATES = frozenset({
    "attention", "blocked", "stop-requested", "stop-unconfirmed",
    "native-cancel-required", "cancel-requested", "unconfirmed", "needs-input",
})
_JOB_ACTIVE_STATES = frozenset({"working", "running", "reserved", "verifying"})
_JOB_ACTIVE_EFFECTIVE = frozenset({"ask", "running", "reserved", "cancel_requested"})
_TERMINAL_EFFECTIVE = frozenset({"ok", "fail", "timeout", "cancelled", "stale"})

# Tone drives colour only; every row also carries the label text.
ATTENTION, ACTIVE, VERIFIED, FAILED, HISTORY = "attention", "active", "verified", "failed", "history"
LABEL_WIDTH = 12

# Label -> (tone, legend explanation). Order is the help legend order.
STATE_LEGEND = {
    "Needs input": (ATTENTION, "Waiting on a person or parent: an ASK answer, reconciliation, or a decision."),
    "Running": (ACTIVE, "Worker process is executing."),
    "Reserved": (ACTIVE, "Scope is reserved; waiting for an execution slot."),
    "Checking": (ACTIVE, "A verification check or reviewer is running."),
    "Unverified": (HISTORY, "Finished, but the parent has not accepted the current content."),
    "Verified": (VERIFIED, "Parent accepted the current, unchanged content snapshot."),
    "Failed": (FAILED, "Execution failed, timed out, went stale, or acceptance was rejected."),
    "Cancelled": (HISTORY, "Stopped by cancellation; the worker is no longer running."),
    "Stopping": (ATTENTION, "Stop was requested; waiting for the worker to exit and release its scope."),
    "Stop unclear": (ATTENTION, "Stop was requested but termination is unconfirmed; files stay held until reconciled."),
    "Host stop": (ATTENTION, "Native agent: the owning host must interrupt it and confirm completion."),
    "Queued": (HISTORY, "Pending queue item; the parent claims it on a free turn."),
    "Planned": (ACTIVE, "Workflow is planned; no node has launched yet."),
}
SETTING_LABELS = {"configured": ("Set", HISTORY), "missing": ("Missing", ATTENTION), "blocked": ("Error", FAILED)}

# Raw machine states -> readable labels, for callers that only have a state string.
_RAW_LABELS = {
    "needs-input": "Needs input", "attention": "Needs input", "blocked": "Needs input",
    "unconfirmed": "Needs input", "working": "Running", "running": "Running",
    "reserved": "Reserved", "verifying": "Checking", "completed-unverified": "Unverified",
    "verified": "Verified", "failed": "Failed", "cancelled": "Cancelled", "stopped": "Cancelled",
    "stop-requested": "Stopping", "cancel-requested": "Stopping",
    "stop-unconfirmed": "Stop unclear", "native-cancel-required": "Host stop",
    "queued": "Queued", "pending": "Queued", "planned": "Planned",
}


def _listing_id(tab: str, row: dict):
    if tab == "Jobs":
        return row.get("job_id")
    if tab == "Queue":
        return row.get("id")
    return row.get("workflow_id") if tab == "Workflows" else row.get("id")


def _row_task(tab: str, row: dict) -> str:
    if tab == "Jobs":
        return str(row.get("task") or row.get("doing") or "")
    if tab == "Queue":
        return str(row.get("text") or "")
    return str(row.get("title") or row.get("blocker") or row.get("text") or "")


def row_title(tab: str, row: dict) -> str:
    """Stable first non-empty task line; the full text stays in details."""
    title = first_line(_row_task(tab, row))
    if title:
        return title
    if tab == "Workflows":
        return str(row.get("workflow_id") or "(untitled workflow)")
    return "(no task recorded)"


def _row_state(tab: str, row: dict, requested=()) -> str:
    """Raw machine state used for ranking and the legacy label API."""
    jid = _listing_id(tab, row)
    state = row.get("cancellation_state") or row.get("display_state") or row.get("status") or "unknown"
    if tab in {"Jobs", "Queue"} and jid is not None and f"cancel:{tab}:{jid}" in requested and state != "stopped":
        state = row.get("cancellation_state") or "stop-requested"
    return state


def state_label(state: str) -> str:
    state = str(state or "unknown")
    return _RAW_LABELS.get(state) or (state.replace("-", " ").replace("_", " ").capitalize() or "Unknown")


def _info(label, explanation=None):
    tone, legend = STATE_LEGEND.get(label, (HISTORY, ""))
    return label, tone, explanation or legend


def _accepted_current(row: dict) -> bool:
    summary = row.get("verification_summary") or row.get("verification") or {}
    return (summary.get("state") == "verified" and summary.get("acceptance") == "accepted"
            and summary.get("freshness") == "current")


def job_state(row: dict, requested=()) -> tuple[str, str, str]:
    """(label, tone, explanation) for a job row; presentation only."""
    jid = row.get("job_id")
    cancel = str(row.get("cancellation_state") or "")
    effective = str(row.get("effective") or row.get("status") or "")
    display = str(row.get("display_state") or rig_jobs.job_display_state(row))
    reservation = row.get("reservation") or {}
    held = bool(reservation) and reservation.get("stage") != "released" and not reservation.get("stopped")
    if cancel == "native-cancel-required":
        return _info("Host stop")
    if cancel == "stop-unconfirmed":
        return _info("Stop unclear")
    if cancel == "stopped":
        return _info("Cancelled", "Stop confirmed; the worker is no longer running.")
    locally = jid is not None and f"cancel:Jobs:{jid}" in requested
    if cancel == "stop-requested" or effective == "cancel_requested" or (
            locally and effective not in _TERMINAL_EFFECTIVE):
        return _info("Stopping", "Stop requested from this board; waiting for the worker to exit."
                     if locally else None)
    if display == "needs-input":
        if effective == "ask":
            return _info("Needs input", "Worker is waiting for a permission answer (y allows, n denies).")
        if effective == "unconfirmed" or reservation.get("needs_reconciliation"):
            return _info("Needs input", "Owner or process state is unconfirmed; the parent must reconcile.")
        return _info("Needs input")
    if display == "cancelled":
        if held:
            return _info("Stopping", "Cancelled; waiting for the worker to stop and release held files.")
        return _info("Cancelled")
    if display == "verified":
        # Never show Verified unless the projected assessment is accepted and current.
        return _info("Verified") if _accepted_current(row) else _info("Unverified")
    label = {"working": "Running", "verifying": "Checking", "reserved": "Reserved", "failed": "Failed",
             "queued": "Queued", "completed-unverified": "Unverified"}.get(display)
    return _info(label or state_label(display))


def row_state_info(tab: str, row: dict, requested=()) -> tuple[str, str, str]:
    if tab == "Jobs":
        return job_state(row, requested)
    if tab == "Queue":
        if f"cancel:Queue:{row.get('id')}" in requested:
            return _info("Stopping", "Queue cancellation requested from this board.")
        return _info("Queued", row.get("waiting_reason") or None)
    if tab == "Workflows":
        status = str(row.get("status") or "unknown")
        if status == "blocked":
            return _info("Needs input", f"Blocked: {row.get('blocker') or 'parent resolution required'}")
        if status == "attention":
            return _info("Needs input", "Workflow needs a parent decision; see next parent action.")
        if status == "cancel-requested":
            return _info("Stopping", "Workflow cancellation requested; nodes are stopping.")
        return _info(state_label(status))
    label, tone = SETTING_LABELS.get(str(row.get("state") or ""), ("Set", HISTORY))
    return label, tone, ""


def job_board_rank(job: dict) -> int:
    effective = str(job.get("effective") or "")
    state = str(job.get("cancellation_state") or job.get("display_state") or job.get("status") or "")
    if effective == "ask" or state in _ASK_STATES:
        return 0
    if effective == "cancel_requested" or state in _JOB_ATTENTION_STATES:
        return 1
    if effective in _JOB_ACTIVE_EFFECTIVE or state in _JOB_ACTIVE_STATES:
        return 2
    return 3


def attention_first_jobs(jobs) -> list[dict]:
    return sorted(list(jobs or []), key=job_board_rank)


def attention_first_workflows(rows) -> list[dict]:
    attention, active, rest = [], [], []
    for row in rows or []:
        status = row.get("status")
        if status in _ATTENTION_STATUSES:
            attention.append(row)
        elif status in _ACTIVE_STATUSES:
            active.append(row)
        else:
            rest.append(row)
    return attention + active + rest


def _snapshot_workflows(snapshot, workflows=None):
    if workflows is not None:
        return workflows
    return list(getattr(snapshot, "workflows", None) or [])


def board_listing(tab: str, snapshot, workflows=None) -> list[dict]:
    if tab == "Jobs":
        return attention_first_jobs(getattr(snapshot, "jobs", None) or [])
    if tab == "Queue":
        return list(getattr(snapshot, "pending", None) or [])
    if tab == "Workflows":
        return attention_first_workflows(_snapshot_workflows(snapshot, workflows))
    return list(getattr(snapshot, "settings", None) or [])


def tab_counts(snapshot, workflows=None) -> dict:
    """Attention counts from the existing classifications; Queue counts pending items."""
    return {
        "Jobs": sum(job_board_rank(row) <= 1 for row in getattr(snapshot, "jobs", None) or []),
        "Queue": len(getattr(snapshot, "pending", None) or []),
        "Workflows": sum(row.get("status") in _ATTENTION_STATUSES
                         for row in _snapshot_workflows(snapshot, workflows)),
        "Settings": 0,
    }


def job_activity(row: dict) -> str:
    """Live activity: the recorded doing line, else the recorded state reason."""
    return first_line(row.get("doing")) or first_line(row.get("display_reason"))


def _is_active_job(row: dict) -> bool:
    return job_board_rank(row) <= 2


def _with_id(head: str, ident: str, width: int) -> str:
    """Append the abbreviated ID only when the full head still fits beside it."""
    used = _text_width(head)
    tag = abbrev_id(ident, 12)
    if ident and used + 2 + _text_width(tag) <= width:
        return head + " " * (width - used - _text_width(tag)) + tag
    return _fit_head(head, width)


def entry_lines(tab: str, row: dict, width: int, *, selected: bool = False, info=None,
                two_line: bool = False, requested=()) -> list[str]:
    """Marker, state, then task/activity; the ID only fills space left after the task."""
    label, _tone, _explanation = info or row_state_info(tab, row, requested)
    marker = "▸" if selected else " "
    label_w = LABEL_WIDTH if tab != "Settings" else 8
    prefix = f"{marker} {_pad(label, min(label_w, max(1, width - 3)))} "
    room = max(0, width - _text_width(prefix))
    title = row_title(tab, row)
    if tab == "Jobs" and not two_line and _is_active_job(row):
        title = first_line(row.get("doing")) or title
    if tab == "Workflows":
        title = f"{title}  {row.get('accepted', 0)}/{row.get('required', 0)}"
    if tab == "Settings":
        title = first_line(row.get("text")) or str(row.get("id") or "")
        return [_fit_head(prefix + _fit_head(title, room), width)]
    first = prefix + _with_id(title, str(_listing_id(tab, row) or ""), room)
    lines = [_fit_head(first, width)]
    if two_line:
        indent = " " * min(_text_width(prefix), max(0, width - 1))
        if _is_active_job(row):
            second = "↳ " + (job_activity(row) or _explanation or label)
        else:
            outcome = first_line(row.get("display_reason")) or _explanation
            worker = str(row.get("worker") or "")
            second = f"{worker} · {outcome}" if worker else outcome
        lines.append(_fit_head(indent + second, width))
    return lines


def format_list_row(tab: str, row: dict, width: int, *, selected: bool = False, state: str | None = None) -> str:
    """Single-line row text; ``state`` overrides with a raw machine state string."""
    info = None
    if state is not None:
        label = state_label(state)
        info = (label, STATE_LEGEND.get(label, (HISTORY, ""))[0], "")
    return entry_lines(tab, row, width, selected=selected, info=info)[0]
