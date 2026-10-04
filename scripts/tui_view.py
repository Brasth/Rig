"""Render already-collected board state. No filesystem access or actions."""
from __future__ import annotations

from admission import ownership_action_details

import curses
import json

import jobs as rig_jobs
from ui_snapshot import format_parent_action, scrub_secrets
from tui_chrome import (  # noqa: F401
    SPLIT_MIN_WIDTH as _SPLIT_MIN_WIDTH, MIN_WIDTH, MIN_HEIGHT, TOO_SMALL, board_hints, detail_hints, header_text, help_hints,
    layout, list_heading, message_lines, message_rows_needed, tab_segments,
)
from tui_detail_state import HelpState, Notice
from tui_style import pair, selection, state_glyph, terminal_text
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
    snippet = _clip_cells(terminal_text(_sanitize(text)), room)
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


def job_detail_lines(row: dict, requested=(), *, read_only: bool = False, width: int = 200) -> list[str]:
    row = scrub_secrets(row)
    label, _tone, explanation = job_state(row, requested)
    compact_ask = width < 60 and row.get("effective") == "ask"
    lines = [f"{state_glyph(label)} {label} · permission" if compact_ask else
             f"{state_glyph(label)} {label} — {explanation}"]
    if not compact_ask and row.get("display_reason") and row["display_reason"] != explanation:
        lines.append(str(row["display_reason"]))
    if row.get("effective") == "ask":
        ask = row.get("ask") or {}
        if not compact_ask:
            lines += ["", f"Worker {row.get('worker') or 'unknown'} asks permission:"]
        if ask.get("tool_name") and not compact_ask:
            lines.append(f"tool     {ask['tool_name']}")
        tool_input = ask.get("input") or ask.get("tool_input") or {}
        if isinstance(tool_input, dict):
            keys = [key for key in ("command", "cwd") if key in tool_input]
            keys += [key for key in tool_input if key not in keys]
            for key in keys:
                value = tool_input[key]
                rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                name = "run" if compact_ask and key == "command" else key
                lines.append(f"{name:<4} {rendered}" if compact_ask else f"{name:<9}{rendered}")
        elif tool_input:
            lines.append(f"input    {tool_input}")
        if ask.get("preview") and not tool_input:
            lines.append(f"request  {ask['preview']}")
        if not tool_input and not ask.get("preview"):
            lines.append("request  not recorded")
        if compact_ask:
            lines.append("Esc, then y allow or n deny" if read_only else "y allow · n deny")
        else:
            lines += ["", "answer   Esc to the board, then y allow or n deny" if read_only else
                      "answer   y allow · n deny · worker stays paused until answered"]
        if ask.get("asked_at"):
            lines.append(f"asked    {ask['asked_at']}")
        if compact_ask and row.get("display_reason"):
            lines.append("reason   " + str(row["display_reason"]))
    if row.get("display_action"):
        lines += ["", "next     " + str(row["display_action"])]
        lines.extend(ownership_action_details(row.get("ownership_next_action")))
    cancel = _cancel_state(row, requested)
    if cancel:
        lines.append(f"stop     {cancel}")
    model = row.get("model") if row.get("model_source") in {"selected", "observed"} and not row.get("model_inferred") else ""
    assessment = row.get("verification_summary") or {}
    verification = f"{assessment.get('state') or 'unknown'} / {assessment.get('method') or 'not accepted'}"
    if label == "Verified":
        verification = "accepted · current snapshot"
    elif assessment.get("state") == "verified":
        verification = "not accepted for current snapshot"
    lines += ["", f"worker   {row.get('worker') or 'unknown'}",
              f"model    {model or 'unknown (not recorded)'}",
              f"verify   {verification}",
              f"review   {row.get('independence') or 'unknown'}; " +
              ("completed" if row.get("review_completed") else "not completed")]
    reservation = row.get("reservation") or {}
    if reservation and reservation.get("stage") != "released":
        files = reservation.get("files") or []
        lines.append("holds    " + ("exclusive repository scope" if reservation.get("scope_unknown") else
                                     f"{len(files)} files ({reservation.get('access') or 'unknown'})"))
        lines.extend("         " + str(path) for path in files)
        lines.append("slot     " + ("held" if reservation.get("slot_held") else "free"))
    lines += ["", f"task     {row.get('task') or row_title('Jobs', row)}",
              f"Job ID  {row.get('job_id') or 'unknown'}", f"role     {row.get('role') or '-'}"]
    for field in ("effort", "thread", "doing", "open"):
        if row.get(field):
            lines.append(f"{field:<9}{row[field]}")
    if row.get("pid"):
        lines.append(f"pid      {row['pid']} ({'alive' if row.get('alive') else 'dead or unknown'})")
    if assessment.get("snapshot_id"):
        lines.append(f"snapshot {assessment['snapshot_id']}")
    lines.append(f"Tokens  {rig_jobs.format_tokens(row.get('token_usage')) or 'unknown'}")
    lines.extend(str(value) for value in row.get("artifacts") or [])
    return lines


def queue_detail_lines(row: dict, requested=()) -> list[str]:
    row = scrub_secrets(row)
    label, _tone, explanation = row_state_info("Queue", row, requested)
    lines = [f"{state_glyph(label)} {label} — {explanation}",
             "next     Parent claims pending items on a free turn.",
             f"priority {row.get('priority') or 0}", f"worker   {row.get('worker') or 'parent chooses'}"]
    if row.get("created_at"):
        lines.append(f"created  {row['created_at']}")
    return lines + ["", "Text", str(row.get("text") or ""), "", f"Queue ID {row.get('id') or 'unknown'}"]


def workflow_detail_lines(row: dict) -> list[str]:
    clean = scrub_secrets(row if isinstance(row, dict) else {})
    label, _tone, explanation = row_state_info("Workflows", clean)
    action = clean.get("next_parent_action")
    lines = [f"{state_glyph(label)} {label} — {explanation}",
             f"blocker {clean.get('blocker') or 'none'}",
             f"next parent action {format_parent_action(action)}"]
    lines.extend(ownership_action_details(action))
    return lines + ["", f"accepted/required {clean.get('accepted', 0)}/{clean.get('required', 0)}",
                    f"running {clean.get('running', 0)}  ask {clean.get('ask', 0)}",
                    "", f"Title   {row_title('Workflows', clean)}",
                    f"Workflow ID {clean.get('workflow_id') or 'unknown'}"]


def detail_document(tab: str, row, requested=(), *, read_only: bool = False, width: int = 200) -> list[str]:
    if row is None:
        return ["Item no longer available",
                f"It left the {tab} list (finished, cancelled, or removed).", "Esc returns to the board."]
    if tab == "Jobs":
        return job_detail_lines(row, requested, read_only=read_only, width=width)
    if tab == "Queue":
        return queue_detail_lines(row, requested)
    if tab == "Workflows":
        return workflow_detail_lines(row)
    return [str(row.get("id") or ""), str(row.get("text") or "")]


def find_item(tab: str, item_id, snapshot, workflows=None):
    return next((row for row in board_listing(tab, snapshot, workflows) if _listing_id(tab, row) == item_id), None)


def _tone_attr(tone: str) -> int:
    if tone == ATTENTION:
        return pair(3)
    if tone == ACTIVE:
        return pair(4)
    if tone == VERIFIED:
        return pair(1) | curses.A_BOLD
    if tone == FAILED:
        return pair(2) | curses.A_BOLD
    return curses.A_NORMAL


def _wrap_all(lines, width: int) -> list[str]:
    return [piece for line in lines for piece in wrap_cells(line, width)]


def _rule(stdscr, y: int, x: int, width: int, heading: str = "", *, focused: bool = False) -> None:
    glyph = "━" if focused else "─"
    text = f"{glyph} {heading} " if heading else ""
    text = _fit_head(text, width)
    text += glyph * max(0, width - _text_width(text))
    _add(stdscr, y, x, text, pair(4 if focused else 6) | (curses.A_BOLD if focused else 0), width=width)


def _draw_tabs(stdscr, y: int, active: str, counts: dict, w: int) -> None:
    x = 0
    for text, on in tab_segments(active, counts, w):
        _add(stdscr, y, x, text, selection() if on else pair(5), width=w - x)
        x += _text_width(text)
    legend = "!n attention · Queue n pending"
    if w >= 100 and w - x >= _text_width(legend) + 2:
        _add(stdscr, y, w - _text_width(legend) - 1, legend, pair(5))


def _bottom_rule(stdscr, lay, *, split: bool = True) -> None:
    if lay.h >= 12:
        _rule(stdscr, lay.message_y - 1, 0, lay.w)
        if split and lay.split:
            _add(stdscr, lay.message_y - 1, lay.left_w, "┴", pair(6), width=1)


def _draw_scroller(stdscr, lay, heading: str, lines, scroll, hints: str, notice, snapshot_status,
                   breadcrumb: str = "Read-only view") -> None:
    w = lay.w
    readonly = "read-only"
    left = _fit_head(breadcrumb, max(1, w - len(readonly) - 3))
    _add(stdscr, 1, 1, left, pair(5), width=w - len(readonly) - 3)
    _add(stdscr, 1, w - len(readonly) - 1, readonly, curses.A_BOLD)
    wrapped = _wrap_all(lines, max(1, w - 2))
    rows = max(1, lay.list_rows - 1)
    offset = scroll.clamp(len(wrapped), rows)
    position = f"rows {offset + 1 if wrapped else 0}-{min(len(wrapped), offset + rows)} of {len(wrapped)}"
    _rule(stdscr, 2, 0, w, heading + " · " + position, focused=True)
    for index, line in enumerate(wrapped[offset:offset + rows]):
        _add(stdscr, lay.list_top + index, 1, line, width=w - 2)
    remaining = len(wrapped) - offset - rows
    if remaining > 0:
        _add(stdscr, lay.list_top + rows, 1, f"↓ {remaining} more rows · PgDn", pair(5), width=w - 2)
    _bottom_rule(stdscr, lay, split=False)
    _draw_message(stdscr, lay, notice, snapshot_status)
    _add(stdscr, lay.h - 1, 0, hints, pair(5), width=w)


def _draw_message(stdscr, lay, notice, snapshot_status) -> None:
    attrs = {"error": pair(2) | curses.A_BOLD, "success": pair(1)}
    for index, (text, kind) in enumerate(message_lines(notice, snapshot_status, lay.w - 2, lay.message_rows)):
        _add(stdscr, lay.message_y + index, 1, text, attrs.get(kind, pair(5)), width=lay.w - 2)


def _activity_tail(acts, rows: int, follow: bool, log_off: int) -> list[str]:
    start = max(0, len(acts) - rows - (0 if follow else log_off))
    return list(acts[start:start + rows])


def _job_group(row, requested=()) -> str:
    rank = 1 if job_state(row, requested)[1] == ATTENTION else job_board_rank(row)
    return "needs attention" if rank <= 1 else "active" if rank == 2 else "finished"


def board_window(listing, selected: int, offset: int, budget: int, entry: int, *, grouped: bool, requested=()):
    """Budget headings/overflow indicators without splitting a selected entry."""
    count = len(listing)
    if not count:
        return 0, 0, []
    selected = min(max(0, selected), count - 1)
    start = min(max(0, offset), selected)
    # Indicators and headings yield to a complete entry on very short screens.
    while True:
        available = budget
        rendered = []
        if start and available >= entry + 2:
            rendered.append(("above", start))
            available -= 1
        previous = None
        stop = start
        while stop < count:
            group = _job_group(listing[stop], requested) if grouped else None
            heading = group is not None and group != previous
            needed = entry + int(heading)
            reserve = int(stop + 1 < count)
            if needed + reserve > available:
                if heading and entry + reserve <= available:
                    heading = False
                else:
                    break
            if heading:
                rendered.append(("group", group))
                available -= 1
            rendered.append(("entry", stop))
            available -= entry
            previous = group
            stop += 1
        if stop < count and available:
            rendered.append(("below", count - stop))
        if selected < stop:
            return start, stop, rendered
        start += 1
        if start > selected:
            return selected, selected + 1, [("entry", selected)]


def _draw_board(stdscr, repo, snapshot, lay, *, tab, listing, selected, offset, follow, log_off,
                requested, log_mode, workflows, stale=False) -> int:
    w = lay.w
    selected = min(max(0, selected), max(0, len(listing) - 1))
    offset, stop, items = board_window(listing, selected, offset, lay.list_rows, lay.entry_lines,
                                     grouped=tab == "Jobs" and lay.h >= 12 and lay.left_w >= 50, requested=requested)
    _rule(stdscr, 2, 0, lay.left_w, list_heading(tab, offset, stop, len(listing), lay.left_w - 4) + (" · stale" if stale else ""), focused=True)
    counts = {name: sum(_job_group(row, requested) == name for row in listing) for name in
              ("needs attention", "active", "finished")} if tab == "Jobs" else {}
    y = lay.list_top
    for kind, value in items:
        if kind != "entry":
            text = f"  {value} ({counts[value]})" if kind == "group" else (
                f"  ↑ {value} more above" if kind == "above" else f"  ↓ {value} more below")
            _add(stdscr, y, 0, text, pair(5), width=lay.left_w)
            y += 1
            continue
        row = listing[value]
        label, tone, explanation = row_state_info(tab, row, requested)
        chosen = value == selected
        lines = entry_lines(tab, row, lay.left_w, selected=chosen, info=(label, tone, explanation),
                            two_line=lay.entry_lines == 2, requested=requested)
        for number, text in enumerate(lines):
            # Selection is independent of semantic color; the label/glyph retain meaning.
            attr = selection() if chosen else (_tone_attr(tone) if number == 0 or tone == ATTENTION else pair(5))
            _add(stdscr, y, 0, _pad(text, lay.left_w) if chosen else text, attr, width=lay.left_w)
            y += 1
    if not listing:
        loading = not getattr(snapshot, "captured_at", 0)
        empty = {"Jobs": ["No jobs yet.", "Press e to enqueue a task for the parent."],
                 "Queue": ["No pending queue items.", "Press e to enqueue a task; the parent claims it on a free turn."],
                 "Workflows": ["No workflows yet.", "Workflows appear after a parent creates one."],
                 "Settings": ["No settings available."]}
        body = ["Loading snapshot…", "Collecting project state; running jobs continue."] if loading else empty.get(tab, ["No items"])
        for index, line in enumerate(_wrap_all(body, max(1, lay.left_w - 2))[:lay.list_rows]):
            _add(stdscr, lay.list_top + index, 1, line, width=lay.left_w - 2)
    if lay.split:
        rx, rw = lay.left_w + 2, w - lay.left_w - 2
        for row_y in range(2, lay.list_top + lay.list_rows):
            _add(stdscr, row_y, lay.left_w, "┬" if row_y == 2 else "│", pair(6), width=1)
        row = listing[selected] if listing else None
        rows = max(0, lay.list_rows - 1)
        if row is not None and tab == "Jobs" and log_mode:
            heading = "Activity · " + ("follow on" if follow else "follow off (PgUp/PgDn)")
            acts = _wrap_all(row.get("activities") or [], max(1, rw - 1))
            body = _activity_tail(acts, rows, follow, log_off) or ["No activity recorded yet."]
        elif row is not None:
            heading = ("Details · " + abbrev_id(_listing_id(tab, row), 12) + " · Enter full screen") if tab != "Settings" else "Setting · e routing/preview"
            body = _wrap_all(detail_document(tab, row, requested, width=rw - 1), max(1, rw - 1))
        else:
            heading, body = "Details", ["Select an item to inspect its recorded state."]
        if stale:
            heading = "Stale · " + heading
        _rule(stdscr, 2, lay.left_w + 1, w - lay.left_w - 1, heading)
        for index, line in enumerate(body[:rows]):
            _add(stdscr, lay.list_top + index, rx, line, width=rw - 1)
        if len(body) > rows:
            _add(stdscr, lay.list_top + rows, rx, "↓ More · Enter full-screen details", pair(5), width=rw - 1)
    return offset


def _editor_lines(text: str, width: int):
    """Soft-wrapped field segments with original offsets for cursor placement."""
    lines, start = [], 0
    width = max(2, width)
    while start < len(text):
        part = _clip_cells(text[start:], width)
        if start + len(part) < len(text) and " " in part:
            cut = part.rfind(" ")
            if cut > 0:
                part = part[:cut + 1]
        lines.append((start, part))
        start += len(part)
    if not lines or _text_width(lines[-1][1]) >= width:
        lines.append((len(text), ""))
    return lines


def _draw_bottom_editor(stdscr, h, w, draft) -> None:
    """Use the detail region for the existing task field; no invented options."""
    lay = layout(h, w)
    x = lay.left_w + 2 if lay.split else 1
    width = max(2, w - x - 1)
    heading_x = lay.left_w + 1 if lay.split else 0
    heading_width = w - heading_x
    for y in range(2, lay.message_y - (1 if h >= 12 else 0)):
        _add(stdscr, y, heading_x, " " * heading_width, width=heading_width)
    _rule(stdscr, 2, heading_x, heading_width, "Enqueue task · saving" if draft.submitting else "Enqueue task", focused=True)
    _add(stdscr, 3, x, f"▸ task · {len(draft.text)}/2000", pair(4) | curses.A_BOLD, width=width)
    lines = _editor_lines(draft.text, width - 1)
    cursor_line = max(i for i, (start, _) in enumerate(lines) if start <= draft.cursor)
    available = max(1, lay.list_rows - 2)
    top = max(0, cursor_line - available + 1)
    for i, (_, line) in enumerate(lines[top:top + available]):
        _add(stdscr, 4 + i, x, _pad(line, width), selection(), width=width)
    hint = draft.message or "Enter queues · Esc closes draft · arrows edit"
    _add(stdscr, h - 2, 0, _pad(hint, w), pair(5), width=w)
    hints = "Enter enqueue  Esc discard draft" if w < 50 else "Enter enqueue  arrows edit  Esc discard draft"
    _add(stdscr, h - 1, 0, _pad(hints, w - 1), pair(5), width=w - 1)
    start = lines[cursor_line][0]
    try:
        stdscr.move(min(h - 3, 4 + cursor_line - top),
                     min(w - 2, x + _text_width(draft.text[start:draft.cursor])))
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
        lines = ["! Terminal too small", f"need {MIN_WIDTH}x{MIN_HEIGHT}, now {w}x{h}",
                 "running jobs continue", "q quit"]
        for index, line in enumerate(lines[:h]):
            y = max(0, (h - min(h, len(lines))) // 2) + index
            fitted = _clip_cells(line, max(0, w - 1))
            _add(stdscr, y, max(0, (w - _text_width(fitted)) // 2), fitted, width=max(0, w - 1))
        stdscr.refresh()
        return offset
    header = header_text(snapshot, repo, w)
    if snapshot_status.startswith("refresh failed"):
        header = _fit_head(header, max(1, w - 9)) + " · stale"
    _add(stdscr, 0, 0, _pad(header, w), pair(5), width=w)
    if recovery_lines is not None:
        from tui_detail_state import Scroll
        scroll = Scroll()
        scroll.offset = recovery_offset
        _draw_scroller(stdscr, lay, "Recovery · g/Esc closes", recovery_lines, scroll,
                       "PgUp/PgDn scroll  g/Esc close  ? help  q quit", notice, snapshot_status,
                       breadcrumb="Recovery guidance")
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
            lines = detail_document(detail.tab, row, requested, read_only=True, width=w - 2)
        _draw_scroller(stdscr, lay, heading, lines, detail.scroll,
                       detail_hints(detail.tab, detail.mode, detail.activity.follow, w - 1), notice, snapshot_status,
                       breadcrumb=f"{detail.tab} › {name} " + (row_title(detail.tab, row) if row else "Item unavailable"))
    else:
        listing = board_listing(tab, snapshot, workflows)
        _draw_tabs(stdscr, 1, tab, tab_counts(snapshot, workflows), w)
        offset = _draw_board(stdscr, repo, snapshot, lay, tab=tab, listing=listing, selected=selected,
                             offset=offset, follow=follow, log_off=log_off, requested=requested,
                             log_mode=log_mode, workflows=workflows, stale=snapshot_status.startswith("refresh failed"))
        _bottom_rule(stdscr, lay)
        _draw_message(stdscr, lay, notice, snapshot_status)
        row = listing[selected] if listing and 0 <= selected < len(listing) else None
        _add(stdscr, h - 1, 0, board_hints(tab, row, log_mode=log_mode, split=lay.split, width=w - 1),
             pair(5), width=w)
    # Editors and confirmation own the bottom rows and their hints.
    if draft is not None and draft.active:
        if lay.split:
            _rule(stdscr, 2, 0, lay.left_w, tab + " · editor open")
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
