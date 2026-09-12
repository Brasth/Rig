#!/usr/bin/env python3
"""Interactive Rig jobs board: agent, task, status, live log."""
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
    log_off = 0
    follow = True
    footer = HELP
    last = 0.0
    listing: list[dict] = []

    def color_for(status: str) -> int:
        if status == "running":
            return curses.color_pair(1) | curses.A_BOLD
        if status == "ask":
            return curses.color_pair(3) | curses.A_BOLD
        if status in {"fail", "stale"}:
            return curses.color_pair(2)
        if status in {"timeout", "cancelled"}:
            return curses.color_pair(3)
        return curses.A_NORMAL

    while True:
        now = time.time()
        if now - last > 0.8 or not listing:
            listing = rig_jobs.list_jobs(repo)
            last = now
            if selected >= len(listing):
                selected = max(0, len(listing) - 1)
            if follow:
                log_off = 0
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        running = sum(1 for j in listing if j["effective"] == "running")
        asking = sum(1 for j in listing if j["effective"] == "ask")
        pending = len(rig_queue.list_items(repo, status="pending"))
        cap = rig_queue.max_running(repo)
        title = (
            f" Rig  {asking} ask / {running} running / {len(listing)} jobs  "
            f"queue {pending}  live {running + asking}/{cap}   {repo} "
        )
        _add(stdscr, 0, 0, title, curses.A_REVERSE, width=w)
        if h < 8 or w < 40:
            _add(stdscr, 1, 0, "terminal too small", width=w)
            stdscr.refresh()
        else:
            left_w = min(36, max(22, w // 3))
            job = listing[selected] if listing else None
            for i, item in enumerate(listing[: h - 3]):
                mark = "●" if item["effective"] == "running" else "○"
                prefix = f"{mark} {item['worker']:<6} {item['effective']:<8} "
                rest = left_w - len(prefix)
                label = prefix + _elide(item["job_id"], max(rest, 4))
                attr = color_for(item["effective"])
                if i == selected:
                    attr |= curses.A_REVERSE
                _add(stdscr, i + 2, 0, label, attr, width=left_w)
            rx = left_w + 1
            rw = max(0, w - rx)
            if rw > 10 and h > 4:
                for y in range(2, h - 1):
                    if rx - 1 < w:
                        _add(stdscr, y, rx - 1, "│", curses.color_pair(4), width=1)
                if job:
                    detail = [
                        job["job_id"],
                        f"agent  {job['worker']}   role {job['role'] or '-'}",
                        f"status {job['effective']}",
                        f"model  {job.get('model') or '-'}",
                        f"reasoning  {job.get('effort') or '-'}",
                        f"task   {job['task']}",
                    ]
                    if job.get("thread"):
                        detail.append(f"thread {job['thread']}")
                    if job.get("doing"):
                        detail.append(f"doing  {job['doing']}")
                    if job.get("pid"):
                        detail.append(
                            f"pid    {job['pid']} ({'alive' if job['alive'] else 'dead'})"
                        )
                    if job.get("open"):
                        detail.append(f"open   {job['open']}")
                    detail.append("")
                    acts = job.get("activities") or []
                    if follow:
                        view = acts[-(h - 3 - len(detail)) :]
                    else:
                        start = max(0, len(acts) - (h - 3 - len(detail)) - log_off)
                        view = acts[start : start + (h - 3 - len(detail))]
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
