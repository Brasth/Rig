#!/usr/bin/env python3
"""Interactive Rig board. Input and curses never wait for job or queue I/O."""
from __future__ import annotations

import curses
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jobs as rig_jobs  # noqa: E402
import work_queue as rig_queue  # noqa: E402
import jev_provider  # noqa: E402
import jev_settings  # noqa: E402
import routing_settings  # noqa: E402
from tui_routing import RoutingPanel, render_panel  # noqa: E402
from tui_editor import Draft, InputDecoder  # noqa: E402
from tui_runtime import BoardRuntime, visible_jobs as _visible_jobs  # noqa: E402
from tui_chrome import layout, message_rows_needed
from tui_style import initialize  # noqa: E402
from tui_detail_state import DetailState, HelpState, Notifications  # noqa: E402
from tui_view import (  # noqa: E402,F401
    PRIMARY_FOOTER, _TABS, _add, _detail_lines, _elide, _listing_id, _next_tab,
    _room, _row_task, _viewport, _workflow_detail_lines, attention_first_jobs,
    board_listing, format_list_row, render,
)
from ui_snapshot import collect_workflows  # noqa: E402
import recovery_guide  # noqa: E402
import recovery_view  # noqa: E402

HELP = PRIMARY_FOOTER
_ENTER = ("\n", "\r", curses.KEY_ENTER)
_DETAIL_TABS = ("Jobs", "Queue", "Workflows")


def _snapshot_status(runtime):
    snapshot = runtime.snapshot
    age = f"snapshot {max(0, time.monotonic() - snapshot.captured_at):.1f}s old" if snapshot.captured_at else "loading snapshot"
    if runtime.snapshot_error:
        return f"refresh failed: {runtime.snapshot_error} ({age})"
    return age + (" · refreshing" if runtime.scanning else "")


def _report_result(notes, result) -> None:
    """Every action result replaces the previous notice, including a persistent error."""
    if result.error:
        notes.error(f"Action failed: {result.error}")
    elif isinstance(result.value, dict):
        text = f"{result.value.get('status') or 'updated'} {result.value.get('id') or ''}".rstrip()
        if result.value.get("held_reason"):
            text += f" — {result.value['held_reason']}"
        notes.success(text)
    else:
        notes.info(str(result.value or "Action completed"))


def _paint(stdscr, repo: Path, *, runtime=None, clock=time.monotonic) -> None:
    runtime = runtime or BoardRuntime(repo)
    curses.curs_set(0)
    curses.noecho()
    initialize()
    # Curses otherwise waits up to a second to disambiguate an Escape key.
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    stdscr.keypad(True)
    stdscr.timeout(25)
    stdscr.scrollok(False)
    tab = "Jobs"
    selected = {name: 0 for name in _TABS}
    offsets = {name: 0 for name in _TABS}
    follow, log_off, log_mode = True, 0, False
    notes = Notifications(clock)
    draft, decoder = Draft(), InputDecoder()
    requested = set()
    pasted_outside_editor = False
    help_state, detail = None, None
    recovery_lines, recovery_request, recovery_offset = None, "", 0
    recovery_sequence = 0
    confirm = None
    secret_active, secret_text = False, ""
    routing_panel, routing_open = None, ""
    routing_open_count = 0
    bracketed = sys.stdout.isatty()
    if bracketed:
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()

    def listing(name, workflows=()):
        return board_listing(name, runtime.snapshot, workflows)

    try:
        while True:
            board_workflows = getattr(runtime.snapshot, "workflows", None)
            if board_workflows is None:
                board_workflows = collect_workflows(repo)
            identities = {}
            for name in selected:
                rows = listing(name, board_workflows)
                # Workflows are collected outside the snapshot, so clamp before reading.
                identities[name] = _listing_id(name, rows[min(selected[name], len(rows) - 1)]) if rows else None
            revision = runtime.revision
            for result in runtime.poll():
                if result.key.startswith("recovery:"):
                    if result.key == recovery_request:
                        recovery_lines = (["Recovery evidence unavailable; inspect the selected attempt."]
                                          if result.error else recovery_view.format_lines(result.value))
                    continue
                if result.key.startswith("routing-open:"):
                    if result.key == routing_open:
                        routing_open = ""
                        # An asynchronous open must not replace a newer editor,
                        # modal, or navigation choice made while it was loading.
                        if (tab != "Settings" or identities.get("Settings") != "domains" or draft.active
                                or secret_active or help_state is not None or detail is not None
                                or confirm is not None):
                            continue
                        if result.error:
                            notes.error(f"Routing Settings unavailable: {result.error}")
                        else:
                            routing_panel = RoutingPanel(result.value)
                    continue
                if result.key.startswith("routing-form:"):
                    if routing_panel is not None and result.key.startswith(f"routing-form:{routing_panel.token}:"):
                        routing_panel.accept(result.key.rsplit(":", 1)[-1], result)
                    continue
                if result.key == "enqueue":
                    draft.submitting = False
                    if result.error:
                        draft.active = True
                        draft.message = f"Enqueue failed: {result.error}; draft retained"
                        notes.error(draft.message)
                    else:
                        item = result.value
                        notes.success(f"queued {item['id']}  {item['text']}")
                        draft = Draft()
                    continue
                if result.error:
                    requested.discard(result.key)
                _report_result(notes, result)
            if runtime.revision != revision:
                for name in selected:
                    rows = listing(name, board_workflows)
                    selected[name] = next((i for i, row in enumerate(rows)
                                           if _listing_id(name, row) == identities[name]),
                                          min(selected[name], max(0, len(rows) - 1)))
                for row in listing("Jobs", board_workflows):
                    if (row.get("reservation") or {}).get("stopped") or row.get("cancellation_state") == "stopped":
                        requested.discard(f"cancel:Jobs:{row['job_id']}")
            h, w = stdscr.getmaxyx()
            notice = notes.current()
            lay = layout(h, w, tab=detail.tab if detail is not None else tab,
                         message_rows=message_rows_needed(notice, w))
            page = max(1, lay.list_rows)
            jobs_view = layout(h, w, tab="Jobs", message_rows=lay.message_rows)
            focus = detail.item_id if detail is not None and detail.tab == "Jobs" else identities["Jobs"]
            runtime.request_snapshot(start=offsets["Jobs"], rows=jobs_view.capacity, selected_id=focus)
            curses.curs_set(1 if draft.active or secret_active else 0)
            if routing_panel is not None:
                render_panel(stdscr, routing_panel)
            else:
                offsets[tab] = render(stdscr, repo, runtime.snapshot, tab=tab, selected=selected[tab], offset=offsets[tab],
                                      follow=follow, log_off=log_off, footer="", snapshot_status=_snapshot_status(runtime),
                                      requested=requested, draft=draft, log_mode=log_mode, workflows=board_workflows,
                                      confirm=confirm, recovery_lines=recovery_lines, recovery_offset=recovery_offset,
                                      notice=notice, detail=detail, help_state=help_state,
                                      secret_len=len(secret_text) if secret_active else None)
            try:
                key = stdscr.get_wch()
                incoming = decoder.feed(key)
            except curses.error:
                incoming = decoder.flush()
            except KeyboardInterrupt:
                return
            for key, pasted in incoming:
                if key == curses.KEY_RESIZE:
                    runtime.refresh()
                    continue
                if routing_panel is not None:
                    action = routing_panel.key(key, pasted=pasted)
                    if action == "cancel":
                        routing_panel = None
                        notes.info("Routing editor closed; unsaved changes discarded")
                    elif action in {"preview", "save"}:
                        panel = routing_panel
                        candidate = panel.candidate()
                        if action == "preview":
                            args = panel.preview_args()
                            work = lambda candidate=candidate, args=args: routing_settings.preview(repo, candidate, **args)
                        else:
                            expected = panel.document.expected_fingerprint
                            work = lambda candidate=candidate, expected=expected: routing_settings.save_settings(
                                repo, candidate, expected_fingerprint=expected)
                        if runtime.submit(f"routing-form:{panel.token}:{action}", work):
                            panel.pending = action
                        else:
                            panel.message = "Actions busy; retry shortly (draft retained)"
                    continue
                if key == "\x1b" and routing_open:
                    routing_open = ""
                    notes.info("Routing editor opening cancelled")
                    continue
                if routing_open and not pasted and (
                        key in ("\t", curses.KEY_BTAB, "j", "k", curses.KEY_UP, curses.KEY_DOWN, "?")
                        or (key == "e" and (tab != "Settings" or identities.get("Settings") != "domains"))):
                    routing_open = ""
                # Pasting into navigation must never execute x/y/n/q commands.
                if pasted and not draft.active:
                    pasted_outside_editor = True
                    notes.info("Press e before pasting queue text")
                    continue
                if not pasted:
                    pasted_outside_editor = False
                if recovery_lines is not None:
                    if key == "q":
                        return
                    if key in {"g", "\x1b"}:
                        recovery_lines, recovery_request, recovery_offset = None, "", 0
                    elif key == curses.KEY_NPAGE:
                        recovery_offset = min(recovery_offset + page,
                                              sum(max(1, (len(line) + max(1, w - 3)) // max(1, w - 2))
                                                  for line in recovery_lines) - 1)
                    elif key == curses.KEY_PPAGE:
                        recovery_offset = max(0, recovery_offset - page)
                    continue
                if draft.active:
                    action = draft.key(key, pasted=pasted)
                    if action == "submit":
                        text = draft.text
                        submission_id = draft.submission_id
                        if runtime.submit("enqueue", lambda text=text, key=submission_id:
                                          rig_queue.add_item(repo, text, idempotency_key=key)):
                            draft.submitting = True
                            draft.message = "Saving queue item; board remains active"
                        else:
                            draft.message = "Actions busy; draft retained, Enter retries"
                    elif action == "cancel":
                        draft.active = False
                        notes.info(draft.message or "Enqueue cancelled")
                    continue
                if secret_active:
                    if key == "\x1b":
                        secret_active, secret_text = False, ""
                        notes.info("Jev key entry cancelled")
                    elif key in _ENTER:
                        value = secret_text
                        if not value:
                            notes.error("Jev key cannot be empty")
                        elif runtime.submit("jev-key", lambda value=value: jev_provider.store_key(value)):
                            secret_active, secret_text = False, ""
                            notes.info("Saving Jev key")
                    elif key in ("\b", "\x7f", curses.KEY_BACKSPACE):
                        secret_text = secret_text[:-1]
                    elif isinstance(key, str) and key.isprintable():
                        secret_text += key
                    continue
                if confirm is not None:
                    pending = confirm
                    confirm = None
                    if key in ("y", "Y"):
                        jid = pending["id"]
                        target_tab = pending["tab"]
                        action_key = f"cancel:{target_tab}:{jid}"
                        if action_key in requested:
                            notes.info(f"stop already requested {jid}")
                        else:
                            action = ((lambda jid=jid: rig_jobs.cancel_job(repo, jid, "tui"))
                                      if target_tab == "Jobs" else
                                      (lambda jid=jid: rig_queue.cancel_item(repo, jid)))
                            if runtime.submit(action_key, action, cancellation=True):
                                requested.add(action_key)
                                notes.info(f"stop requested {jid}" if target_tab == "Jobs"
                                           else f"queue cancellation requested {jid}")
                            else:
                                notes.error("Cancellation lane busy; x retries")
                    else:
                        notes.info(f"Cancellation aborted {pending['id']}")
                    continue
                if help_state is not None:
                    action = help_state.key(key, page)
                    if action == "quit":
                        return
                    if action == "close":
                        help_state = None
                    continue
                if detail is not None:
                    # Details are read-only: only scrolling, closing, and switching reach here.
                    action = detail.key(key, page)
                    if action == "quit":
                        return
                    if action == "close":
                        detail = None
                    elif action in {"next-tab", "prev-tab"}:
                        step = -1 if action == "prev-tab" else 1
                        tab = _TABS[(_TABS.index(detail.tab) + step) % len(_TABS)]
                        detail = None
                    elif action == "help":
                        help_state = HelpState()
                    elif action == "refresh":
                        runtime.refresh()
                    elif action == "blocked":
                        notes.info("Details are read-only; Esc returns to the board for actions")
                    continue
                if key == "q":
                    return
                if lay.tiny:
                    # Nothing is visible to act on until the terminal is resized.
                    continue
                if key == "?":
                    help_state = HelpState()
                    continue
                if key == "\x1b":
                    notes.dismiss()
                    continue
                if pasted_outside_editor:
                    continue
                rows = listing(tab, board_workflows)
                selected[tab] = min(selected[tab], max(0, len(rows) - 1))
                row = rows[selected[tab]] if rows else None
                if key in _ENTER:
                    if row and tab in _DETAIL_TABS:
                        detail = DetailState(tab, _listing_id(tab, row))
                        runtime.refresh()
                elif key in ("j", curses.KEY_DOWN):
                    selected[tab] = min(selected[tab] + 1, max(0, len(rows) - 1))
                    follow = True
                    runtime.refresh()
                elif key in ("k", curses.KEY_UP):
                    selected[tab] = max(0, selected[tab] - 1)
                    follow = True
                    runtime.refresh()
                elif key in ("\t", curses.KEY_BTAB):
                    step = -1 if key == curses.KEY_BTAB else 1
                    tab = _TABS[(_TABS.index(tab) + step) % len(_TABS)]
                elif key == "r":
                    runtime.refresh()
                elif key == "g" and row and tab in {"Jobs", "Workflows"}:
                    recovery_sequence += 1
                    recovery_request = f"recovery:{recovery_sequence}"
                    recovery_lines, recovery_offset = ["Reading recorded evidence…"], 0
                    selector = {"job_id": row["job_id"]} if tab == "Jobs" else {"workflow_id": row["workflow_id"]}
                    if not runtime.submit(recovery_request, lambda selector=selector: recovery_guide.build(repo, **selector)):
                        recovery_lines = ["Background actions busy; close and reopen recovery guidance."]
                elif tab == "Settings" and row and key == "c" and row.get("id") == "jev":
                    secret_active, secret_text = True, ""
                elif tab == "Settings" and row and key == "d" and row.get("id") == "jev":
                    if runtime.submit("jev-remove", jev_provider.delete_key):
                        notes.info("Removing Jev key")
                elif tab == "Settings" and row and key == "t" and row.get("id") == "engine":
                    engine = "local" if "jev" in str(row.get("text") or "") else "jev"
                    if runtime.submit("routing-engine", lambda engine=engine: jev_settings.update_project(repo, engine=engine)):
                        notes.info(f"Setting project picker to {engine}")
                elif tab == "Settings" and row and key == "o" and row.get("id") == "objective":
                    values = ("quality", "balanced", "speed", "cost")
                    old = next((value for value in values if value in str(row.get("text") or "")), "balanced")
                    objective = values[(values.index(old) + 1) % len(values)]
                    if runtime.submit("routing-objective", lambda objective=objective: jev_settings.update_project(repo, objective=objective)):
                        notes.info(f"Setting local objective to {objective}")
                elif tab == "Settings" and row and key == "e" and row.get("id") == "domains":
                    if not routing_open:
                        routing_open_count += 1
                        action_key = f"routing-open:{routing_open_count}"
                        if runtime.submit(action_key, lambda: routing_settings.open_settings(repo)):
                            routing_open = action_key
                            notes.info("Opening routing settings; Esc cancels")
                elif key == "e":
                    draft.active, draft.message = True, ""
                elif key == "o" and row and tab == "Jobs":
                    notes.info(row.get("open") or f"no session for {row['job_id']}")
                elif key in ("y", "n") and row and tab == "Jobs":
                    behavior = "allow" if key == "y" else "deny"
                    action_key = f"answer:{row['job_id']}"
                    if runtime.submit(action_key, lambda row=row, behavior=behavior: rig_jobs.answer_pending(row, behavior)):
                        notes.info(f"{behavior} requested {row['job_id']}")
                    else:
                        notes.error("Action already pending or busy")
                elif key == "x" and row and tab in {"Jobs", "Queue"}:
                    jid = str(row.get("job_id") if tab == "Jobs" else row.get("id"))
                    if tab == "Jobs" and ((row.get("reservation") or {}).get("stopped") or
                                           (row.get("effective") in {"ok", "fail", "timeout"} and not row.get("cancellation_state"))):
                        notes.info(f"{jid} already finished")
                        continue
                    action_key = f"cancel:{tab}:{jid}"
                    if action_key in requested:
                        notes.info(f"stop already requested {jid}")
                        continue
                    confirm = {"tab": tab, "id": jid, "task": _row_task(tab, row)}
                elif key == "l" and tab == "Jobs":
                    if lay.split:
                        log_mode = not log_mode
                    elif row:
                        detail = DetailState("Jobs", row["job_id"], mode="activity")
                        runtime.refresh()
                elif key == "f":
                    follow = not follow
                    notes.info("follow on" if follow else "follow off (PgUp/PgDn)")
                elif key == curses.KEY_NPAGE:
                    follow, log_off = False, max(0, log_off - 8)
                elif key == curses.KEY_PPAGE:
                    follow, log_off = False, log_off + 8
    finally:
        runtime.close()
        if bracketed:
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.flush()


def main() -> int:
    repo = rig_jobs.repo_root()
    if not sys.stdout.isatty() or os.environ.get("RIG_TUI") == "0":
        print(rig_jobs.format_table(rig_jobs.list_jobs(repo), repo))
        return 0
    try:
        curses.wrapper(lambda stdscr: _paint(stdscr, repo))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
