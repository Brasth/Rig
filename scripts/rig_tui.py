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


HELP = "j/k select   l log   o open   r refresh   q quit"


def _paint(stdscr, repo: Path) -> None:
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    stdscr.nodelay(True)
    stdscr.timeout(400)
    selected = 0
    log_off = 0
    follow = True
    footer = HELP
    last = 0.0
    listing: list[dict] = []

    def color_for(status: str) -> int:
        if status == "running":
            return curses.color_pair(1) | curses.A_BOLD
        if status in {"fail", "stale"}:
            return curses.color_pair(2)
        if status == "timeout":
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
        title = f" Rig  {running} running / {len(listing)} jobs   {repo} "
        stdscr.addnstr(0, 0, title[:w], w, curses.A_REVERSE)
        if h < 8 or w < 40:
            stdscr.addnstr(1, 0, "terminal too small", w)
            stdscr.refresh()
        else:
            left_w = min(36, max(22, w // 3))
            job = listing[selected] if listing else None
            for i, item in enumerate(listing[: h - 3]):
                mark = "●" if item["effective"] == "running" else "○"
                spec = "/".join(x for x in [item.get("model"), item.get("effort")] if x)
                label = f"{mark} {item['worker']:<6} {item['effective']:<8} {spec or item['job_id']}"
                attr = color_for(item["effective"])
                if i == selected:
                    attr |= curses.A_REVERSE
                stdscr.addnstr(i + 2, 0, label[:left_w], left_w, attr)
            rx = left_w + 1
            rw = max(0, w - rx)
            if rw > 10 and h > 4:
                for y in range(2, h - 1):
                    if rx - 1 < w:
                        stdscr.addnstr(y, rx - 1, "│", 1, curses.color_pair(4))
                if job:
                    detail = [
                        job["job_id"],
                        f"agent  {job['worker']}   role {job['role'] or '-'}",
                        f"status {job['effective']}",
                        f"model  {job.get('model') or '-'}",
                        f"reasoning  {job.get('effort') or '-'}",
                        f"task   {job['task']}",
                    ]
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
                        stdscr.addnstr(y, rx, line[:rw], rw)
                else:
                    stdscr.addnstr(2, rx, "No jobs in .rig/jobs", rw)
            stdscr.addnstr(h - 1, 0, footer[:w], w, curses.A_REVERSE)
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
        print(rig_jobs.format_table(rig_jobs.list_jobs(repo)))
        return 0
    try:
        curses.wrapper(lambda stdscr: _paint(stdscr, repo))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
