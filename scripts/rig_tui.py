#!/usr/bin/env python3
"""Interactive Rig jobs board: agent, task, status, live log."""
from __future__ import annotations

import curses
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jobs as rig_jobs  # noqa: E402
import work_queue as rig_queue  # noqa: E402


HELP = "j/k select   e enqueue   y allow   n deny   x cancel   l log   o open   r refresh   q quit"


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
    snippet = text[:room]
    try:
        stdscr.addnstr(y, x, snippet, room, attr)
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


def _visible_jobs(listing: list[dict], repo: Path, start: int, stop: int) -> list[dict]:
    # One refresh hashes only displayed subjects, sharing repeated paths across rows.
    cache: dict = {}
    return [rig_jobs.project_job(job, repo=repo, refresh=True, cache=cache) for job in listing[start:stop]]


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
    if job.get("dir"):
        folder = Path(job["dir"])
        for artifact in ("change-evidence.json", "verification.json", "checks"):
            path = folder / artifact
            if path.exists():
                detail.append(str(path))
    return detail


def _paint(stdscr, repo: Path) -> None:
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    stdscr.nodelay(True)
    stdscr.timeout(400)
    stdscr.scrollok(False)
    selected = 0
    row_offset = 0
    log_off = 0
    follow = True
    footer = HELP
    last = 0.0
    listing: list[dict] = []

    def color_for(status: str) -> int:
        if status in {"working", "verified"}:
            return curses.color_pair(1) | curses.A_BOLD
        if status == "needs-input":
            return curses.color_pair(3) | curses.A_BOLD
        if status == "failed":
            return curses.color_pair(2)
        if status in {"reserved", "verifying", "cancelled"}:
            return curses.color_pair(3)
        return curses.A_NORMAL

    while True:
        now = time.time()
        if now - last > 0.8 or not listing:
            selected_id = listing[selected].get("job_id") if listing else None
            listing = rig_jobs.list_jobs(repo)
            last = now
            selected = next((i for i, job in enumerate(listing) if job.get("job_id") == selected_id),
                            min(selected, max(0, len(listing) - 1)))
            if follow:
                log_off = 0
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        running = sum(1 for j in listing if j.get("effective") == "running")
        asking = sum(1 for j in listing if j.get("effective") == "ask")
        reserved = sum(1 for j in listing if j.get("effective") == "reserved")
        slots = sum(bool((j.get("reservation") or {}).get("slot_held")) if j.get("reservation") else
                    j.get("effective") in {"running", "ask"} for j in listing)
        pending = len(rig_queue.list_items(repo, status="pending"))
        cap = rig_queue.max_running(repo)
        title = (
            f" Rig  {asking} ask / {running} working / {reserved} reserved / {len(listing)} jobs  "
            f"queue {pending}  live {slots}/{cap}   {repo} "
        )
        _add(stdscr, 0, 0, title, curses.A_REVERSE, width=w)
        if h < 8 or w < 40:
            _add(stdscr, 1, 0, "terminal too small", width=w)
            stdscr.refresh()
        else:
            left_w = min(44, max(22, w // 3))
            row_offset, row_end = _viewport(selected, len(listing), h - 3, row_offset)
            visible = _visible_jobs(listing, repo, row_offset, row_end)
            job = visible[selected - row_offset] if visible else None
            _add(stdscr, 1, 0, f"Jobs {row_offset + 1 if listing else 0}-{row_end}/{len(listing)}", width=left_w)
            for i, item in enumerate(visible):
                state = item.get("display_state") or rig_jobs.job_display_state(item)
                mark = "●" if state == "working" else "○"
                id_width = min(16, max(8, left_w // 3))
                label = f"{mark} {_elide(item['job_id'], id_width):<{id_width}} {state}"
                attr = color_for(state)
                if i + row_offset == selected:
                    attr |= curses.A_REVERSE
                _add(stdscr, i + 2, 0, label, attr, width=left_w)
            rx = left_w + 1
            rw = max(0, w - rx)
            if rw > 10 and h > 4:
                for y in range(2, h - 1):
                    if rx - 1 < w:
                        _add(stdscr, y, rx - 1, "│", curses.color_pair(4), width=1)
                if job:
                    detail = _detail_lines(job) + [""]
                    acts = job.get("activities") or []
                    available = max(0, h - 3 - len(detail))
                    if follow:
                        view = acts[-available:] if available else []
                    else:
                        start = max(0, len(acts) - available - log_off)
                        view = acts[start : start + available]
                    detail.extend(view or ["(no log yet)"])
                    for i, line in enumerate(detail):
                        y = 2 + i
                        if y >= h - 1:
                            break
                        _add(stdscr, y, rx, line, width=rw)
                else:
                    _add(stdscr, 2, rx, "No jobs in .rig/jobs", width=rw)
            _add(stdscr, h - 1, 0, footer, curses.A_REVERSE, width=w)
        stdscr.refresh()
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            return
        if ch in (-1, curses.ERR):
            continue
        footer = HELP
        if ch in (ord("q"), 27):
            return
        if ch in (ord("j"), curses.KEY_DOWN):
            selected = min(selected + 1, max(0, len(listing) - 1))
            follow = True
        elif ch in (ord("k"), curses.KEY_UP):
            selected = max(selected - 1, 0)
            follow = True
        elif ch in (ord("r"), curses.KEY_RESIZE):
            last = 0
        elif ch == ord("o"):
            if listing:
                job = listing[selected]
                footer = job.get("open") or f"no session for {job['job_id']}"
        elif ch == ord("e"):
            prompt = "enqueue: "
            stdscr.nodelay(False)
            stdscr.timeout(-1)
            try:
                curses.curs_set(1)
                curses.echo()
                _add(stdscr, h - 1, 0, prompt + " " * max(0, w), curses.A_REVERSE)
                col = min(len(prompt), max(0, w - 2))
                n = _room(h - 1, col, h, w)
                if n < 1:
                    footer = "terminal too small to enqueue"
                else:
                    try:
                        stdscr.move(h - 1, col)
                    except curses.error:
                        pass
                    raw = stdscr.getstr(h - 1, col, n)
                    line = (
                        raw.decode("utf-8", "replace")
                        if isinstance(raw, bytes)
                        else str(raw or "")
                    )
                    line = line.strip()
                    if line:
                        obj = rig_queue.add_item(repo, line)
                        footer = f"queued {obj['id']}  {obj['text']}"
                    else:
                        footer = "enqueue cancelled"
            except KeyboardInterrupt:
                footer = "enqueue cancelled"
            except Exception as exc:
                footer = f"enqueue failed: {exc}"
            finally:
                curses.noecho()
                curses.curs_set(0)
                stdscr.nodelay(True)
                stdscr.timeout(400)
            last = 0
        elif ch in (ord("y"), ord("n")):
            if listing:
                job = listing[selected]
                behavior = "allow" if ch == ord("y") else "deny"
                footer = rig_jobs.answer_pending(job, behavior)
                last = 0
        elif ch == ord("x"):
            if listing:
                job = listing[selected]
                footer = rig_jobs.cancel_job(repo, str(job.get("job_id") or ""), "tui")
                last = 0
        elif ch == ord("l"):
            if not listing:
                continue
            job = listing[selected]
            text = rig_jobs.format_show(job, log_lines=200)
            import subprocess
            import tempfile

            tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".md", encoding="utf-8")
            tmp.write(text)
            tmp.close()
            curses.endwin()
            pager = os.environ.get("PAGER") or "less"
            subprocess.call([pager, tmp.name])
            stdscr.clear()
            last = 0
        elif ch == ord("f"):
            follow = not follow
            footer = "follow on" if follow else "follow off (pgup/pgdn)"
        elif ch == curses.KEY_NPAGE:
            follow = False
            log_off = max(0, log_off - 8)
        elif ch == curses.KEY_PPAGE:
            follow = False
            log_off += 8


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
