"""Render already-collected board state. No filesystem access or actions."""
from __future__ import annotations

import curses
import json
import textwrap
import unicodedata

import jobs as rig_jobs
from ui_snapshot import _ACTIVE_STATUSES, _ATTENTION_STATUSES, format_parent_action, scrub_secrets

_TABS = ("Jobs", "Queue", "Workflows")


def _cell_width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _text_width(text):
    return sum(_cell_width(char) for char in text)


def _clip_cells(text, width):
    used = 0
    for index, char in enumerate(text):
        used += _cell_width(char)
        if used > width:
            return text[:index]
    return text


def _elide(text: str, width: int) -> str:
    if width <= 1 or len(text) <= width:
        return text
    return "…" + text[-(width - 1) :]


def _room(y: int, x: int, h: int, w: int) -> int:
    """Cells we can write without hitting the bottom-right scroll cell."""
    if y < 0 or y >= h or x < 0 or x >= w:
        return 0
    room = w - x
    if y == h - 1:
        room -= 1
    return max(0, room)


def _add(stdscr, y: int, x: int, text: str, attr: int = 0, width: int | None = None) -> None:
    h, w = stdscr.getmaxyx()
    room = _room(y, x, h, w)
    if width is not None:
        room = min(room, max(0, width))
    if room <= 0:
        return
    snippet = _clip_cells(text, room)
    try:
        stdscr.addnstr(y, x, snippet, len(snippet), attr)
    except curses.error:
        pass


def _viewport(selected: int, count: int, rows: int, offset: int) -> tuple[int, int]:
    rows = max(1, rows)
    offset = min(max(0, offset), max(0, count - rows))
    if selected < offset:
        offset = selected
    elif selected >= offset + rows:
        offset = selected - rows + 1
    return max(0, offset), min(count, max(0, offset) + rows)


def _next_tab(tab: str) -> str:
    try:
        return _TABS[(_TABS.index(tab) + 1) % len(_TABS)]
    except ValueError:
        return "Queue"


def _listing_id(tab: str, row: dict):
    if tab == "Jobs":
        return row.get("job_id")
    if tab == "Queue":
        return row.get("id")
    return row.get("workflow_id")


def _workflow_detail_lines(row: dict) -> list[str]:
    row = scrub_secrets(row if isinstance(row, dict) else {})
    action = row.get("next_parent_action")
    detail = [
        str(row.get("workflow_id") or "unknown"),
        f"status {row.get('status') or 'unknown'}",
        f"accepted/required {row.get('accepted', 0)}/{row.get('required', 0)}",
        f"running {row.get('running', 0)}  ask {row.get('ask', 0)}",
        f"blocker {row.get('blocker') or 'none'}",
        f"next parent action {format_parent_action(action)}",
    ]
    if row.get("title"):
        detail.append(f"title  {row['title']}")
    return detail


def _detail_lines(job: dict) -> list[str]:
    state = job.get("display_state") or rig_jobs.job_display_state(job)
    detail = [str(job.get("job_id") or "unknown"), f"status {state}"]
    if job.get("display_action"):
        detail.extend(str(job["display_action"]).split(" | "))
    if job.get("display_reason"):
        detail.append(str(job["display_reason"]))
    model = job.get("model") if job.get("model_source") in {"selected", "observed"} and not job.get("model_inferred") else ""
    detail.append(f"agent  {job.get('worker') or 'unknown'}   model {model or 'unknown'}")
    reservation = job.get("reservation") or {}
    if reservation and reservation.get("stage") != "released":
        scope = "exclusive repository scope" if reservation.get("scope_unknown") else json.dumps(reservation.get("files") or [], ensure_ascii=False)
        detail.append(f"files held ({reservation.get('access') or 'unknown'}): {scope}")
        detail.append(f"execution slot {'held' if reservation.get('slot_held') else 'free'}")
    assessment = job.get("verification_summary") or {}
    detail.append(f"verification {assessment.get('state') or 'unknown'} / {assessment.get('method') or 'not accepted'}")
    independence = job.get("independence") or "unknown"
    detail.append(f"independent review {independence}; {'completed' if job.get('review_completed') else 'not completed'}")
    detail.extend([f"role   {job.get('role') or '-'}", f"task   {job.get('task') or '-'}"])
    for field in ("effort", "thread", "doing", "open"):
        if job.get(field):
            detail.append(f"{field:<6} {job[field]}")
    if job.get("pid"):
        detail.append(f"pid    {job['pid']} ({'alive' if job.get('alive') else 'dead or unknown'})")
    if assessment.get("snapshot_id"):
        detail.append(f"snapshot {assessment['snapshot_id']}")
    detail.extend(job.get("artifacts") or [])
    return detail


def _color(status):
    if status in {"working", "verified", "running"}:
        return curses.color_pair(1) | curses.A_BOLD
    if status == "failed":
        return curses.color_pair(2)
    if status in {"needs-input", "reserved", "verifying", "cancelled", "stop-requested", "stop-unconfirmed",
                  "native-cancel-required", "attention", "blocked", "cancel-requested"}:
        return curses.color_pair(3)
    return curses.A_NORMAL


def _snapshot_workflows(snapshot, workflows=None):
    if workflows is not None:
        return workflows
    return list(getattr(snapshot, "workflows", None) or [])


def render(stdscr, repo, snapshot, *, tab, selected, offset, follow, log_off,
           footer, snapshot_status, requested, draft, log_mode=False, workflows=None):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    workflows = _snapshot_workflows(snapshot, workflows)
    if tab == "Jobs":
        listing = snapshot.jobs
    elif tab == "Queue":
        listing = snapshot.pending
    else:
        listing = workflows
    running = sum(row.get("effective") == "running" for row in snapshot.jobs)
    asking = sum(row.get("effective") == "ask" for row in snapshot.jobs)
    reserved = sum(row.get("effective") == "reserved" for row in snapshot.jobs)
    wf_active = sum(row.get("status") in _ACTIVE_STATUSES for row in workflows)
    wf_attention = sum(row.get("status") in _ATTENTION_STATUSES for row in workflows)
    title = (f" Rig  {asking} ask / {running} working / {reserved} reserved / {len(snapshot.jobs)} jobs  "
             f"queue {len(snapshot.pending)}  live {snapshot.slots}/{snapshot.cap}")
    if workflows:
        title += f"  wf {wf_active} active / {wf_attention} attention"
    title += f"   {repo}"
    _add(stdscr, 0, 0, title, curses.A_REVERSE, width=w)
    if h < 8 or w < 40:
        _add(stdscr, 1, 0, "terminal too small", width=w)
    else:
        left_w = min(44, max(22, w // 3))
        offset, stop = _viewport(selected, len(listing), h - 3, offset)
        label = f"{tab} {offset + 1 if listing else 0}-{stop}/{len(listing)}  Tab: {_next_tab(tab)}"
        _add(stdscr, 1, 0, label, width=left_w)
        for index in range(offset, stop):
            row = listing[index]
            jid = _listing_id(tab, row)
            state = row.get("cancellation_state") or row.get("display_state") or row.get("status") or "unknown"
            if tab != "Workflows" and f"cancel:{tab}:{jid}" in requested and state != "stopped":
                state = row.get("cancellation_state") or "stop-requested"
            id_width = min(16, max(8, left_w // 3))
            mark = "●" if state in {"working", "running", "attention"} else "○"
            extra = ""
            if tab == "Workflows":
                extra = f" {row.get('accepted', 0)}/{row.get('required', 0)}"
            label = f"{mark} {_elide(str(jid), id_width):<{id_width}} {state}{extra}"
            _add(stdscr, index - offset + 2, 0, label,
                 _color(state) | (curses.A_REVERSE if index == selected else 0), width=left_w)
        rx, rw = left_w + 1, w - left_w - 1
        _add(stdscr, 1, rx, snapshot_status, width=rw)
        for y in range(2, h - 1):
            _add(stdscr, y, rx - 1, "│", curses.color_pair(4), width=1)
        row = listing[selected] if listing else None
        if row and tab == "Workflows":
            detail = _workflow_detail_lines(row)
        elif row and tab == "Jobs":
            detail = [str(row["job_id"]), "Activity log (l returns)"] if log_mode else _detail_lines(row) + [""]
            cancel_state = row.get("cancellation_state")
            if f"cancel:Jobs:{row['job_id']}" in requested:
                cancel_state = cancel_state or "stop-requested"
            if cancel_state:
                detail.insert(2, f"cancellation {cancel_state}")
            acts = row.get("activities") or []
            available = max(0, h - 3 - len(detail))
            start = max(0, len(acts) - available - (0 if follow else log_off))
            detail.extend(acts[start:start + available] or ["(no log yet)"])
        elif row:
            detail = [str(row["id"]), "status pending", row.get("waiting_reason") or "Awaiting parent claim",
                      f"priority {row.get('priority') or 0}", f"worker {row.get('worker') or 'parent chooses'}", ""]
            detail.extend(textwrap.wrap(str(row.get("text") or ""), max(1, rw)))
        else:
            empty = {"Jobs": "No jobs in .rig/jobs", "Queue": "No pending queue items",
                     "Workflows": "No workflows in .rig/workflows"}
            detail = [empty.get(tab, "No items")]
        for index, line in enumerate(detail[:max(0, h - 3)]):
            _add(stdscr, index + 2, rx, str(line), width=rw)
    if draft.active:
        prefix = f"{'saving' if draft.submitting else 'enqueue'} {len(draft.text)}/2000: "
        space = max(1, _room(h - 1, 0, h, w) - len(prefix))
        start, used = draft.cursor, 0
        while start and used + _cell_width(draft.text[start - 1]) < space:
            start -= 1
            used += _cell_width(draft.text[start])
        footer = prefix + _clip_cells(draft.text[start:], space)
        _add(stdscr, h - 1, 0, footer, curses.A_REVERSE, width=w)
        if h >= 3:
            hint = draft.message or "Enter queues · Esc closes draft · arrows edit"
            _add(stdscr, h - 2, 0, hint, curses.A_REVERSE, width=w)
        try:
            stdscr.move(h - 1, min(max(0, w - 2), len(prefix) + _text_width(draft.text[start:draft.cursor])))
        except curses.error:
            pass
    else:
        _add(stdscr, h - 1, 0, footer, curses.A_REVERSE, width=w)
    stdscr.refresh()
    return offset
