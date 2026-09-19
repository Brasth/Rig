"""Render already-collected board state. No filesystem access or actions."""
from __future__ import annotations

import curses
import json
import textwrap
import unicodedata

import jobs as rig_jobs
from ui_snapshot import _ACTIVE_STATUSES, _ATTENTION_STATUSES, format_parent_action, scrub_secrets

_TABS = ("Jobs", "Queue", "Workflows")
_SPLIT_MIN_WIDTH = 72
_ASK_STATES = frozenset({"needs-input"})
_JOB_ATTENTION_STATES = frozenset({
    "attention", "blocked", "stop-requested", "stop-unconfirmed",
    "native-cancel-required", "cancel-requested", "unconfirmed", "needs-input",
})
_JOB_ACTIVE_STATES = frozenset({"working", "running", "reserved", "verifying"})
_JOB_ACTIVE_EFFECTIVE = frozenset({"ask", "running", "reserved", "cancel_requested"})
_STATE_LABELS = {
    "needs-input": "ASK",
    "working": "WORK",
    "running": "WORK",
    "reserved": "HOLD",
    "verifying": "CHECK",
    "attention": "ATTN",
    "blocked": "BLOCK",
    "stop-requested": "STOP",
    "stop-unconfirmed": "STOP",
    "native-cancel-required": "HOST",
    "cancel-requested": "STOP",
    "unconfirmed": "ATTN",
    "failed": "FAIL",
    "cancelled": "CANC",
    "verified": "OK",
    "completed-unverified": "DONE",
    "queued": "WAIT",
    "pending": "WAIT",
    "planned": "PLAN",
}
PRIMARY_FOOTER = "Tab  j/k  y/n  x  e  l  r  ?  q"
HELP_LINES = (
    "Tab            Jobs / Queue / Workflows",
    "j/k or arrows  Move the selection",
    "e              Open the Unicode queue editor",
    "y / n          Allow or deny the selected ASK",
    "x              Cancel selected job or queue item (y confirms)",
    "l              Toggle the selected job activity log",
    "r              Refresh the snapshot",
    "f              Toggle follow for the activity pane",
    "PgUp / PgDn    Scroll activity when follow is off",
    "o              Show the selected job session path",
    "?              Close this help overlay",
    "q              Close the board; running jobs keep going",
    "",
    "Jobs list attention-first: ASK / attention / active, then history.",
    "Each row shows a state label and task text; color is not the only cue.",
    "x explains the target and waits for y; Esc or any other key aborts.",
)


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


def _fit_head(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _text_width(text) <= width:
        return text
    if width == 1:
        return "…"
    return _clip_cells(text, width - 1) + "…"


def _fit_tail(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _text_width(text) <= width:
        return text
    if width == 1:
        return "…"
    keep = []
    used = 1
    for char in reversed(text):
        size = _cell_width(char)
        if used + size > width:
            break
        keep.append(char)
        used += size
    return "…" + "".join(reversed(keep))


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


def _row_task(tab: str, row: dict) -> str:
    if tab == "Jobs":
        return str(row.get("task") or row.get("doing") or "")
    if tab == "Queue":
        return str(row.get("text") or "")
    return str(row.get("title") or row.get("blocker") or "")


def _row_state(tab: str, row: dict, requested=()) -> str:
    jid = _listing_id(tab, row)
    state = row.get("cancellation_state") or row.get("display_state") or row.get("status") or "unknown"
    if tab != "Workflows" and jid is not None and f"cancel:{tab}:{jid}" in requested and state != "stopped":
        state = row.get("cancellation_state") or "stop-requested"
    return state


def state_label(state: str) -> str:
    state = str(state or "unknown")
    if state in _STATE_LABELS:
        return _STATE_LABELS[state]
    compact = "".join(char for char in state.upper() if char.isalnum())
    return (compact[:4] or "STAT")


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


def board_listing(tab: str, snapshot, workflows=None) -> list[dict]:
    if tab == "Jobs":
        return attention_first_jobs(getattr(snapshot, "jobs", None) or [])
    if tab == "Queue":
        return list(getattr(snapshot, "pending", None) or [])
    return attention_first_workflows(_snapshot_workflows(snapshot, workflows))


def format_list_row(tab: str, row: dict, width: int, *, selected: bool = False, state: str | None = None) -> str:
    state = state if state is not None else _row_state(tab, row)
    marker = "▸" if selected else " "
    label = f"{state_label(state):<4}"
    jid = str(_listing_id(tab, row) or "-")
    extra = ""
    if tab == "Workflows":
        extra = f"{row.get('accepted', 0)}/{row.get('required', 0)}"
    task = _row_task(tab, row)
    prefix = f"{marker} {label} "
    used = _text_width(prefix)
    remain = max(0, width - used)
    id_width = min(16, max(8, min(remain, max(8, remain // 3))))
    identity = _fit_tail(jid, id_width)
    identity = identity + " " * max(0, id_width - _text_width(identity))
    body = identity
    remain -= _text_width(body)
    if extra and remain > 1:
        chunk = " " + extra
        if _text_width(chunk) <= remain:
            body += chunk
            remain -= _text_width(chunk)
    if task and remain > 1:
        body += " " + _fit_head(task, remain - 1)
    return _fit_head(prefix + body, width)


def compact_footer(tab: str, row, *, log_mode: bool = False) -> str:
    parts = ["Tab", "j/k"]
    if tab == "Jobs":
        if (row or {}).get("effective") == "ask":
            parts.append("y/n answer")
        parts.append("x stop")
        parts.append("l log" if not log_mode else "l details")
    elif tab == "Queue":
        parts.append("e enqueue")
        parts.append("x cancel")
    else:
        parts.append("read only")
    parts.extend(["? help", "q quit"])
    return "  ".join(parts)


def confirm_lines(confirm: dict) -> tuple[str, str]:
    tab = confirm.get("tab") or "Jobs"
    jid = confirm.get("id") or "unknown"
    task = str(confirm.get("task") or "").strip()
    if tab == "Queue":
        headline = f"Cancel queue item {jid}"
    else:
        headline = f"Stop job {jid}"
    if task:
        headline += f" — {task}"
    return headline, "y confirm · any other key aborts"


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
                  "native-cancel-required", "attention", "blocked", "cancel-requested", "unconfirmed"}:
        return curses.color_pair(3)
    return curses.A_NORMAL


def _snapshot_workflows(snapshot, workflows=None):
    if workflows is not None:
        return workflows
    return list(getattr(snapshot, "workflows", None) or [])


def _layout(h: int, w: int) -> str:
    if h < 8 or w < 40:
        return "tiny"
    if w < _SPLIT_MIN_WIDTH:
        return "stack"
    return "split"


def _health_title(snapshot, workflows, repo) -> str:
    running = sum(row.get("effective") == "running" for row in snapshot.jobs)
    asking = sum(row.get("effective") == "ask" for row in snapshot.jobs)
    reserved = sum(row.get("effective") == "reserved" for row in snapshot.jobs)
    title = (f" Rig  {asking} ask / {running} working / {reserved} reserved / {len(snapshot.jobs)} jobs  "
             f"queue {len(snapshot.pending)}  live {snapshot.slots}/{snapshot.cap}")
    if workflows:
        wf_active = sum(row.get("status") in _ACTIVE_STATUSES for row in workflows)
        wf_attention = sum(row.get("status") in _ATTENTION_STATUSES for row in workflows)
        title += f"  wf {wf_active} active / {wf_attention} attention"
    title += f"   {repo}"
    return title


def _selected_attr(state: str, selected: bool) -> int:
    attr = _color(state)
    if selected:
        attr |= curses.A_REVERSE | curses.A_BOLD
    return attr


def render(stdscr, repo, snapshot, *, tab, selected, offset, follow, log_off,
           footer, snapshot_status, requested, draft, log_mode=False, workflows=None,
           help_mode=False, confirm=None):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    workflows = _snapshot_workflows(snapshot, workflows)
    listing = board_listing(tab, snapshot, workflows)
    layout = _layout(h, w)
    _add(stdscr, 0, 0, _health_title(snapshot, workflows, repo), curses.A_REVERSE, width=w)
    if layout == "tiny":
        _add(stdscr, 1, 0, "terminal too small", width=w)
    elif help_mode:
        _add(stdscr, 1, 0, "Help  ·  Esc or ? closes", curses.A_REVERSE, width=w)
        for index, line in enumerate(HELP_LINES):
            if index + 2 >= h - 1:
                break
            _add(stdscr, index + 2, 1, line, width=max(0, w - 2))
    else:
        split = layout == "split"
        left_w = w if not split else min(56, max(28, w // 2))
        list_rows = h - 4 if not split else h - 3
        offset, stop = _viewport(selected, len(listing), list_rows, offset)
        label = f"{tab} {offset + 1 if listing else 0}-{stop}/{len(listing)}  Tab: {_next_tab(tab)}"
        if not split:
            status_w = max(0, w - _text_width(label) - 2)
            if status_w and snapshot_status:
                label = f"{label}  {_fit_head(snapshot_status, status_w)}"
        _add(stdscr, 1, 0, label, width=left_w)
        for index in range(offset, stop):
            row = listing[index]
            state = _row_state(tab, row, requested)
            text = format_list_row(tab, row, left_w, selected=index == selected, state=state)
            _add(stdscr, index - offset + 2, 0, text, _selected_attr(state, index == selected), width=left_w)
        row = listing[selected] if listing else None
        if split:
            rx, rw = left_w + 1, w - left_w - 1
            _add(stdscr, 1, rx, snapshot_status, width=rw)
            for y in range(2, h - 1):
                _add(stdscr, y, rx - 1, "│", curses.color_pair(4), width=1)
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
        else:
            empty = {"Jobs": "No jobs in .rig/jobs", "Queue": "No pending queue items",
                     "Workflows": "No workflows in .rig/workflows"}
            if row:
                state = _row_state(tab, row, requested)
                summary = f"{state_label(state)} {_listing_id(tab, row)}  {_row_task(tab, row)}"
                if tab == "Jobs" and row.get("display_action"):
                    summary = f"{summary}  ·  {row['display_action']}"
                elif tab == "Queue" and row.get("waiting_reason"):
                    summary = f"{summary}  ·  {row['waiting_reason']}"
            else:
                summary = empty.get(tab, "No items")
            _add(stdscr, h - 2, 0, summary, curses.A_BOLD, width=w)
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
    elif confirm:
        headline, hint = confirm_lines(confirm)
        if h >= 3:
            _add(stdscr, h - 2, 0, headline, curses.A_REVERSE, width=w)
        _add(stdscr, h - 1, 0, hint, curses.A_REVERSE, width=w)
    elif help_mode:
        _add(stdscr, h - 1, 0, "Esc or ? closes help", curses.A_REVERSE, width=w)
    else:
        chrome = footer if footer and footer != PRIMARY_FOOTER else compact_footer(
            tab, listing[selected] if listing else None, log_mode=log_mode)
        _add(stdscr, h - 1, 0, chrome, curses.A_REVERSE, width=w)
    stdscr.refresh()
    return offset
