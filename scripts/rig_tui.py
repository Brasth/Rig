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
from tui_editor import Draft, InputDecoder  # noqa: E402
from tui_runtime import BoardRuntime, visible_jobs as _visible_jobs  # noqa: E402
from tui_view import _add, _detail_lines, _elide, _room, _viewport, render  # noqa: E402,F401

HELP = "Tab Jobs/Queue  j/k select  e enqueue  y/n answer  x cancel  l log  r refresh  q quit"


def _snapshot_status(runtime):
    snapshot = runtime.snapshot
    age = f"snapshot {max(0, time.monotonic() - snapshot.captured_at):.1f}s old" if snapshot.captured_at else "loading snapshot"
    if runtime.snapshot_error:
        return f"refresh failed: {runtime.snapshot_error} ({age})"
    return age + (" · refreshing" if runtime.scanning else "")


def _paint(stdscr, repo: Path, *, runtime=None) -> None:
    runtime = runtime or BoardRuntime(repo)
    curses.curs_set(0)
    curses.use_default_colors()
    curses.noecho()
    for index, color in enumerate((curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_CYAN), 1):
        curses.init_pair(index, color, -1)
    # Curses otherwise waits up to a second to disambiguate an Escape key.
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    stdscr.keypad(True)
    stdscr.timeout(25)
    stdscr.scrollok(False)
    tab = "Jobs"
    selected = {"Jobs": 0, "Queue": 0}
    offsets = {"Jobs": 0, "Queue": 0}
    follow, log_off, log_mode = True, 0, False
    footer = HELP
    draft, decoder = Draft(), InputDecoder()
    requested = set()
    pasted_outside_editor = False
    bracketed = sys.stdout.isatty()
    if bracketed:
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()

    def listing(name):
        return runtime.snapshot.jobs if name == "Jobs" else runtime.snapshot.pending

    try:
        while True:
            identities = {name: ((listing(name)[selected[name]].get("job_id") if name == "Jobs" else
                                 listing(name)[selected[name]].get("id")) if listing(name) else None)
                          for name in selected}
            revision = runtime.revision
            for result in runtime.poll():
                if result.key == "enqueue":
                    draft.submitting = False
                    if result.error:
                        draft.active = True
                        draft.message = f"Enqueue failed: {result.error}; draft retained"
                        footer = draft.message
                    else:
                        item = result.value
                        footer = f"queued {item['id']}  {item['text']}"
                        draft = Draft()
                elif result.error:
                    requested.discard(result.key)
                    footer = f"Action failed: {result.error}"
                elif isinstance(result.value, dict):
                    footer = f"{result.value.get('status') or 'updated'} {result.value.get('id') or ''}"
                    if result.value.get("held_reason"):
                        footer += f" — {result.value['held_reason']}"
                else:
                    footer = str(result.value or "Action completed")
            if runtime.revision != revision:
                for name in selected:
                    key = "job_id" if name == "Jobs" else "id"
                    selected[name] = next((i for i, row in enumerate(listing(name)) if row.get(key) == identities[name]),
                                          min(selected[name], max(0, len(listing(name)) - 1)))
                for row in listing("Jobs"):
                    if (row.get("reservation") or {}).get("stopped") or row.get("cancellation_state") == "stopped":
                        requested.discard(f"cancel:Jobs:{row['job_id']}")
            h, w = stdscr.getmaxyx()
            runtime.request_snapshot(start=offsets["Jobs"], rows=max(0, h - 3) if h >= 8 and w >= 40 else 0,
                                     selected_id=identities["Jobs"])
            curses.curs_set(1 if draft.active else 0)
            offsets[tab] = render(stdscr, repo, runtime.snapshot, tab=tab, selected=selected[tab], offset=offsets[tab],
                                  follow=follow, log_off=log_off, footer=footer, snapshot_status=_snapshot_status(runtime),
                                  requested=requested, draft=draft, log_mode=log_mode)
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
                # Pasting into navigation must never execute x/y/n/q commands.
                if pasted and not draft.active:
                    pasted_outside_editor = True
                    footer = "Press e before pasting queue text"
                    continue
                if not pasted:
                    pasted_outside_editor = False
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
                        footer = draft.message or "Enqueue cancelled"
                    continue
                if key == "q":
                    return
                if key == "\x1b" or pasted_outside_editor:
                    continue
                rows = listing(tab)
                row = rows[selected[tab]] if rows else None
                if key in ("j", curses.KEY_DOWN):
                    selected[tab] = min(selected[tab] + 1, max(0, len(rows) - 1))
                    follow = True
                    runtime.refresh()
                elif key in ("k", curses.KEY_UP):
                    selected[tab] = max(0, selected[tab] - 1)
                    follow = True
                    runtime.refresh()
                elif key in ("\t", curses.KEY_BTAB):
                    tab = "Queue" if tab == "Jobs" else "Jobs"
                elif key == "r":
                    runtime.refresh()
                elif key == "e":
                    draft.active, draft.message = True, ""
                elif key == "o" and row and tab == "Jobs":
                    footer = row.get("open") or f"no session for {row['job_id']}"
                elif key in ("y", "n") and row and tab == "Jobs":
                    behavior = "allow" if key == "y" else "deny"
                    action_key = f"answer:{row['job_id']}"
                    accepted = runtime.submit(action_key, lambda row=row, behavior=behavior: rig_jobs.answer_pending(row, behavior))
                    footer = f"{behavior} requested {row['job_id']}" if accepted else "Action already pending or busy"
                elif key == "x" and row:
                    jid = str(row.get("job_id") if tab == "Jobs" else row.get("id"))
                    if tab == "Jobs" and ((row.get("reservation") or {}).get("stopped") or
                                           (row.get("effective") in {"ok", "fail", "timeout"} and not row.get("cancellation_state"))):
                        footer = f"{jid} already finished"
                        continue
                    action_key = f"cancel:{tab}:{jid}"
                    if action_key in requested:
                        footer = f"stop already requested {jid}"
                        continue
                    action = (lambda jid=jid: rig_jobs.cancel_job(repo, jid, "tui")) if tab == "Jobs" else (lambda jid=jid: rig_queue.cancel_item(repo, jid))
                    if runtime.submit(action_key, action, cancellation=True):
                        requested.add(action_key)
                        footer = f"stop requested {jid}" if tab == "Jobs" else f"queue cancellation requested {jid}"
                    else:
                        footer = "Cancellation lane busy; x retries"
                elif key == "l" and tab == "Jobs":
                    log_mode = not log_mode
                elif key == "f":
                    follow = not follow
                    footer = "follow on" if follow else "follow off (pgup/pgdn)"
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
