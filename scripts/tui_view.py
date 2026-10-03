"""Render already-collected board state. No filesystem access or actions."""
from __future__ import annotations

from admission import ownership_action_details

import curses
import json

import jobs as rig_jobs
from ui_snapshot import format_parent_action, scrub_secrets
from tui_chrome import (  # noqa: F401
    SPLIT_MIN_WIDTH as _SPLIT_MIN_WIDTH, TOO_SMALL, board_hints, detail_hints, header_text, help_hints,
    layout, list_heading, message_lines, message_rows_needed, tab_segments,
)
from tui_detail_state import HelpState, Notice
from tui_rows import (  # noqa: F401
    ACTIVE, ATTENTION, FAILED, HISTORY, STATE_LEGEND, VERIFIED, _TABS, _listing_id, _row_state,
    _row_task, _snapshot_workflows, attention_first_jobs, attention_first_workflows, board_listing,
    entry_lines, format_list_row, job_board_rank, job_state, row_state_info, row_title, state_label,
    tab_counts,
)
from tui_text import (  # noqa: F401
    _cell_width, _clip_cells, _elide, _fit_head, _fit_tail, _pad, _sanitize, _text_width, abbrev_id,
    wrap_cells,
)

PRIMARY_FOOTER = "Tab  j/k  Enter  y/n  x  e  l  r  g  ?  q"
HELP_LINES = (
    "Board",
    "Tab / Shift-Tab  Jobs / Queue / Workflows / Settings (counts: n! attention, Queue n pending)",
    "Jobs list attention-first: ASK / attention / active, then history.",
    "j/k or arrows    Move the selection",
    "Enter            Full-screen details for the selected job, queue item, or workflow",
    "e                Open the Unicode queue editor",
    "y / n            Allow or deny the selected ASK",
    "x                Cancel selected job or queue item (y confirms)",
    "l                Jobs: toggle activity (wide pane) or open activity details (narrow)",
    "f                Toggle follow for the activity pane",
    "PgUp / PgDn      Scroll activity when follow is off",
    "r                Refresh the snapshot",
    "g                Read-only recovery guidance for selected job/workflow",
    "o                Show the selected job session path",
    "Esc              Dismiss the current message (errors stay until dismissed or replaced)",
    "Settings e       Edit domain routing and preview tasks",
    "?                Open or close this help",
    "q                Close the board; running jobs keep going",
    "",
    "Details",
    "j/k, arrows, PgUp/PgDn scroll · Enter or Esc closes · Tab/Shift-Tab closes and switches",
    "l toggles details/activity for jobs · f toggles follow · q quits",
    "Details are read-only: mutation keys work only on the board.",
    "",
    "x explains the target and waits for y; Esc or any other key aborts.",
    "Each row shows a state label and task text; color is not the only cue.",
    "",
    "State legend",
)


def help_lines() -> list[str]:
    return list(HELP_LINES) + [f"{label:<13}{explanation}" for label, (_tone, explanation) in STATE_LEGEND.items()]


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
    snippet = _clip_cells(_sanitize(text), room)
    if not snippet:
        return
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


def compact_footer(tab: str, row, *, log_mode: bool = False) -> str:
    return board_hints(tab, row, log_mode=log_mode)


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
    detail.extend(ownership_action_details(row.get("next_parent_action")))
    if row.get("title"):
        detail.append(f"title  {row['title']}")
    return detail


def _detail_lines(job: dict) -> list[str]:
    state = job.get("display_state") or rig_jobs.job_display_state(job)
    detail = [str(job.get("job_id") or "unknown"), f"status {state}"]
    if job.get("display_action"):
        detail.extend(str(job["display_action"]).split(" | "))
        detail.extend(ownership_action_details(job.get("ownership_next_action")))
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


def _cancel_state(row: dict, requested) -> str:
    state = row.get("cancellation_state") or ""
    if f"cancel:Jobs:{row.get('job_id')}" in requested:
        state = state or "stop-requested"
    return state


def job_detail_lines(row: dict, requested=()) -> list[str]:
    label, _tone, explanation = job_state(row, requested)
    lines = [f"Title   {row_title('Jobs', row)}", f"State   {label} — {explanation}",
             f"Job ID  {row.get('job_id') or 'unknown'}"]
    cancel = _cancel_state(row, requested)
    if cancel:
        lines.append(f"cancellation {cancel}")
    lines.extend(_detail_lines(row)[1:])
    lines.append(f"Tokens  {rig_jobs.format_tokens(row.get('token_usage')) or 'unknown'}")
    return lines


def queue_detail_lines(row: dict, requested=()) -> list[str]:
    label, _tone, explanation = row_state_info("Queue", row, requested)
    lines = [f"Title   {row_title('Queue', row)}", f"State   {label} — {explanation}",
             f"Queue ID {row.get('id') or 'unknown'}",
             f"waiting {row.get('waiting_reason') or 'Awaiting parent claim'}",
             f"priority {row.get('priority') or 0}", f"worker  {row.get('worker') or 'parent chooses'}"]
    if row.get("created_at"):
        lines.append(f"created {row['created_at']}")
    return lines + ["", "Text", str(row.get("text") or "")]


def workflow_detail_lines(row: dict) -> list[str]:
    clean = scrub_secrets(row if isinstance(row, dict) else {})
    label, _tone, explanation = row_state_info("Workflows", clean)
    return [f"Title   {row_title('Workflows', clean)}", f"State   {label} — {explanation}",
            "Workflow ID " + str(clean.get("workflow_id") or "unknown")] + _workflow_detail_lines(clean)[1:]


def detail_document(tab: str, row, requested=()) -> list[str]:
    if row is None:
        return ["Item no longer available",
                f"It left the {tab} list (finished, cancelled, or removed).", "Esc returns to the board."]
    if tab == "Jobs":
        return job_detail_lines(row, requested)
    if tab == "Queue":
        return queue_detail_lines(row, requested)
    if tab == "Workflows":
        return workflow_detail_lines(row)
    return [str(row.get("id") or ""), str(row.get("text") or "")]


def find_item(tab: str, item_id, snapshot, workflows=None):
    return next((row for row in board_listing(tab, snapshot, workflows) if _listing_id(tab, row) == item_id), None)


def _tone_attr(tone: str) -> int:
    if tone == ATTENTION:
        return curses.color_pair(3)
    if tone == ACTIVE:
        return curses.color_pair(4)
    if tone == VERIFIED:
        return curses.color_pair(1) | curses.A_BOLD
    if tone == FAILED:
        return curses.color_pair(2) | curses.A_BOLD
    return curses.A_NORMAL


def _wrap_all(lines, width: int) -> list[str]:
    return [piece for line in lines for piece in wrap_cells(line, width)]


def _draw_tabs(stdscr, y: int, active: str, counts: dict, w: int) -> None:
    x = 0
    for text, on in tab_segments(active, counts, w):
        _add(stdscr, y, x, text, (curses.A_REVERSE | curses.A_BOLD) if on else curses.A_NORMAL, width=w - x)
        x += _text_width(text)
        if x >= w:
            break


def _draw_scroller(stdscr, lay, heading: str, lines, scroll, hints: str, notice, snapshot_status) -> None:
    w = lay.w
    _add(stdscr, 1, 0, _pad(heading, w), curses.A_REVERSE, width=w)
    top, rows = 2, max(1, lay.message_y - 2)
    wrapped = _wrap_all(lines, max(1, w - 2))
    offset = scroll.clamp(len(wrapped), rows)
    for index, line in enumerate(wrapped[offset:offset + rows]):
        _add(stdscr, top + index, 1, line, width=w - 2)
    if len(wrapped) > rows:
        position = f" {offset + 1}-{min(len(wrapped), offset + rows)}/{len(wrapped)} "
        _add(stdscr, 1, max(0, w - _text_width(position)), position, curses.A_REVERSE, width=w)
    _draw_message(stdscr, lay, notice, snapshot_status)
    _add(stdscr, lay.h - 1, 0, hints, curses.A_REVERSE, width=w)


def _draw_message(stdscr, lay, notice, snapshot_status) -> None:
    attrs = {"error": curses.color_pair(2) | curses.A_BOLD, "success": curses.color_pair(1)}
    for index, (text, kind) in enumerate(message_lines(notice, snapshot_status, lay.w, lay.message_rows)):
        _add(stdscr, lay.message_y + index, 0, text, attrs.get(kind, curses.A_NORMAL), width=lay.w)


def _activity_tail(acts, rows: int, follow: bool, log_off: int) -> list[str]:
    start = max(0, len(acts) - rows - (0 if follow else log_off))
    return list(acts[start:start + rows])


def _draw_board(stdscr, repo, snapshot, lay, *, tab, listing, selected, offset, follow, log_off,
                requested, log_mode, workflows) -> int:
    w = lay.w
    capacity = lay.capacity
    selected = min(max(0, selected), max(0, len(listing) - 1))
    offset, stop = _viewport(selected, len(listing), capacity, offset)
    _add(stdscr, 2, 0, list_heading(tab, offset, stop, len(listing), lay.left_w), curses.A_BOLD, width=lay.left_w)
    y = lay.list_top
    for index in range(offset, stop):
        row = listing[index]
        label, tone, explanation = row_state_info(tab, row, requested)
        chosen = index == selected
        attr = _tone_attr(tone) | ((curses.A_REVERSE | curses.A_BOLD) if chosen else 0)
        lines = entry_lines(tab, row, lay.left_w, selected=chosen, info=(label, tone, explanation),
                            two_line=lay.entry_lines == 2, requested=requested)
        for number, text in enumerate(lines):
            line_attr = attr if number == 0 else (curses.A_REVERSE if chosen else curses.A_DIM)
            _add(stdscr, y, 0, _pad(text, lay.left_w) if chosen else text, line_attr, width=lay.left_w)
            y += 1
    if not listing:
        empty = {"Jobs": ["No jobs in .rig/jobs yet.", "Press e to enqueue a task for the parent."],
                 "Queue": ["No pending queue items.", "Press e to enqueue a task; the parent claims it on a free turn."],
                 "Workflows": ["No workflows in .rig/workflows.", "Workflows appear after a parent creates one."],
                 "Settings": ["No settings available."]}
        for index, line in enumerate(_wrap_all(empty.get(tab, ["No items"]), max(1, lay.left_w - 2))):
            if lay.list_top + index >= lay.message_y:
                break
            _add(stdscr, lay.list_top + index, 1, line, width=lay.left_w - 1)
    if lay.split:
        rx, rw = lay.left_w + 1, w - lay.left_w - 1
        for row_y in range(2, lay.message_y):
            _add(stdscr, row_y, rx - 1, "│", curses.color_pair(4), width=1)
        row = listing[selected] if listing else None
        rows = max(0, lay.message_y - 3)
        if row is not None and tab == "Jobs" and log_mode:
            heading = "Activity · " + ("follow on" if follow else "follow off (PgUp/PgDn)")
            body = _activity_tail(row.get("activities") or [], rows, follow, log_off) or ["(no activity recorded yet)"]
        elif row is not None and tab == "Jobs":
            heading = "Details · Enter opens full screen"
            body = _wrap_all(job_detail_lines(row, requested), max(1, rw))
            remaining = rows - len(body) - 1
            if remaining > 0:
                body += [""] + (_activity_tail(row.get("activities") or [], remaining, follow, log_off)
                                or ["(no activity recorded yet)"])
        elif row is not None:
            heading = "Details · Enter opens full screen" if tab != "Settings" else "Setting"
            body = _wrap_all(detail_document(tab, row, requested), max(1, rw))
        else:
            heading, body = "Details", []
        _add(stdscr, 2, rx, heading, curses.A_BOLD, width=rw)
        for index, line in enumerate(body[:rows]):
            _add(stdscr, 3 + index, rx, line, width=rw)
    return offset


def _draw_bottom_editor(stdscr, h, w, draft) -> None:
    prefix = f"{'saving' if draft.submitting else 'enqueue'} {len(draft.text)}/2000: "
    space = max(1, _room(h - 1, 0, h, w) - len(prefix))
    start, used = draft.cursor, 0
    while start and used + _cell_width(draft.text[start - 1]) < space:
        start -= 1
        used += _cell_width(draft.text[start])
    _add(stdscr, h - 1, 0, _pad(prefix + _clip_cells(draft.text[start:], space), w), curses.A_REVERSE, width=w)
    if h >= 3:
        hint = draft.message or "Enter queues · Esc closes draft · arrows edit"
        _add(stdscr, h - 2, 0, _pad(hint, w), curses.A_REVERSE, width=w)
    try:
        stdscr.move(h - 1, min(max(0, w - 2), len(prefix) + _text_width(draft.text[start:draft.cursor])))
    except curses.error:
        pass


def render(stdscr, repo, snapshot, *, tab, selected, offset, follow, log_off,
           footer, snapshot_status, requested, draft, log_mode=False, workflows=None,
           help_mode=False, confirm=None, recovery_lines=None, recovery_offset=0,
           notice=None, detail=None, help_state=None, secret_len=None):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    workflows = _snapshot_workflows(snapshot, workflows)
    if notice is None and footer and footer != PRIMARY_FOOTER:
        # Compatibility for direct callers that still pass a footer message.
        notice = Notice("info", footer, 0.0)
    lay = layout(h, w, tab=detail.tab if detail is not None else tab,
                 message_rows=message_rows_needed(notice, w))
    if lay.tiny:
        _add(stdscr, 0, 0, " Rig", curses.A_REVERSE, width=w)
        for index, line in enumerate(wrap_cells(TOO_SMALL, max(1, w - 1))):
            _add(stdscr, 1 + index, 0, line, width=w)
        stdscr.refresh()
        return offset
    _add(stdscr, 0, 0, _pad(header_text(snapshot, repo), w), curses.A_REVERSE, width=w)
    if recovery_lines is not None:
        _add(stdscr, 1, 0, _pad("Recovery  ·  g/Esc closes · PgUp/PgDn scrolls", w), curses.A_REVERSE, width=w)
        wrapped = _wrap_all(recovery_lines, max(1, w - 2))
        rows = max(1, lay.message_y - 2)
        start = min(max(0, recovery_offset), max(0, len(wrapped) - rows))
        for index, line in enumerate(wrapped[start:start + rows]):
            _add(stdscr, index + 2, 1, line, width=max(0, w - 2))
        _draw_message(stdscr, lay, notice, snapshot_status)
        _add(stdscr, h - 1, 0, "PgUp/PgDn scroll  g/Esc close  q quit", curses.A_REVERSE, width=w)
    elif help_state is not None or help_mode:
        state = help_state or HelpState()
        _draw_scroller(stdscr, lay, "Help · keys and state legend", help_lines(), state.scroll,
                       help_hints(w - 1), notice, snapshot_status)
    elif detail is not None:
        row = find_item(detail.tab, detail.item_id, snapshot, workflows)
        name = abbrev_id(detail.item_id, max(8, w // 3))
        if detail.mode == "activity" and row is not None:
            acts = row.get("activities") or []
            heading = f"Activity · {detail.tab} · {name} · follow {'on' if detail.activity.follow else 'off'}"
            lines = list(acts) or ["(no activity recorded yet)"]
        else:
            heading = f"Details · {detail.tab} · {name}"
            lines = detail_document(detail.tab, row, requested)
        _draw_scroller(stdscr, lay, heading, lines, detail.scroll,
                       detail_hints(detail.tab, detail.mode, detail.activity.follow, w - 1), notice, snapshot_status)
    else:
        listing = board_listing(tab, snapshot, workflows)
        _draw_tabs(stdscr, 1, tab, tab_counts(snapshot, workflows), w)
        offset = _draw_board(stdscr, repo, snapshot, lay, tab=tab, listing=listing, selected=selected,
                             offset=offset, follow=follow, log_off=log_off, requested=requested,
                             log_mode=log_mode, workflows=workflows)
        _draw_message(stdscr, lay, notice, snapshot_status)
        row = listing[selected] if listing and 0 <= selected < len(listing) else None
        _add(stdscr, h - 1, 0, board_hints(tab, row, log_mode=log_mode, split=lay.split, width=w - 1),
             curses.A_REVERSE, width=w)
    # Editors and confirmation own the bottom rows and their hints.
    if draft is not None and draft.active:
        _draw_bottom_editor(stdscr, h, w, draft)
    elif confirm:
        headline, hint = confirm_lines(confirm)
        _add(stdscr, h - 2, 0, _pad(headline, w), curses.A_REVERSE | curses.A_BOLD, width=w)
        _add(stdscr, h - 1, 0, _pad(hint, w), curses.A_REVERSE, width=w)
    elif secret_len is not None:
        _add(stdscr, h - 2, 0, _pad("Jev API key: " + "•" * min(secret_len, 24), w), curses.A_REVERSE, width=w)
        _add(stdscr, h - 1, 0, _pad("Enter saves · Esc cancels", w), curses.A_REVERSE, width=w)
    stdscr.refresh()
    return offset
