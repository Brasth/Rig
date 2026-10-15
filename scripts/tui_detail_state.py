"""Input-thread UI state: notifications, details overlay, and help scrolling.

Nothing here performs I/O. The clock is injectable so expiry is testable.
"""
from __future__ import annotations

import curses
import time
from dataclasses import dataclass

NOTICE_TTL = 4.0
_ENTER = ("\n", "\r", curses.KEY_ENTER)


@dataclass(frozen=True)
class Notice:
    kind: str  # info | success | error
    text: str
    created: float


class Notifications:
    """Info/success expire after four seconds; errors persist until Esc or a newer notice."""

    def __init__(self, clock=time.monotonic, ttl: float = NOTICE_TTL):
        self.clock, self.ttl = clock, ttl
        self._notice: Notice | None = None

    def push(self, kind: str, text) -> None:
        self._notice = Notice(kind, str(text), self.clock())

    def info(self, text) -> None:
        self.push("info", text)

    def success(self, text) -> None:
        self.push("success", text)

    def error(self, text) -> None:
        self.push("error", text)

    def current(self) -> Notice | None:
        notice = self._notice
        if notice is not None and notice.kind != "error" and self.clock() - notice.created >= self.ttl:
            self._notice = notice = None
        return notice

    def dismiss(self) -> bool:
        """Esc clears the current notice; returns whether anything was visible."""
        visible = self.current() is not None
        self._notice = None
        return visible


class Scroll:
    """Line offset clamped against the latest rendered content."""

    def __init__(self):
        self.offset = 0
        self.follow = False

    def move(self, delta: int) -> None:
        self.offset = max(0, self.offset + delta)
        self.follow = False

    def clamp(self, total: int, page: int) -> int:
        bottom = max(0, total - max(1, page))
        self.offset = bottom if self.follow else min(max(0, self.offset), bottom)
        return self.offset


def _scroll_delta(key, page: int):
    if key in ("j", curses.KEY_DOWN):
        return 1
    if key in ("k", curses.KEY_UP):
        return -1
    if key == curses.KEY_NPAGE:
        return max(1, page - 1)
    if key == curses.KEY_PPAGE:
        return -max(1, page - 1)
    if key == curses.KEY_HOME:
        return -10 ** 9
    if key == curses.KEY_END:
        return 10 ** 9
    return None


class DetailState:
    """Full-screen details bound to a tab and item ID, never to a list position."""

    def __init__(self, tab: str, item_id, *, mode: str = "details"):
        self.tab, self.item_id = tab, item_id
        self.mode = mode if tab == "Jobs" else "details"
        self.details = Scroll()
        self.activity = Scroll()
        self.activity.follow = True

    @property
    def scroll(self) -> Scroll:
        return self.activity if self.mode == "activity" else self.details

    def key(self, key, page: int) -> str | None:
        """Returns close/quit/next-tab/prev-tab/help/refresh/blocked, or None when handled."""
        delta = _scroll_delta(key, page)
        if delta is not None:
            self.scroll.move(delta)
            return None
        if key in _ENTER or key == "\x1b":
            return "close"
        if key == "\t":
            return "next-tab"
        if key == curses.KEY_BTAB:
            return "prev-tab"
        if key == "q":
            return "quit"
        if key == "?":
            return "help"
        if key == "r":
            return "refresh"
        if key == curses.KEY_RESIZE:
            return None
        if key == "l" and self.tab == "Jobs":
            self.mode = "details" if self.mode == "activity" else "activity"
            return None
        if key == "f" and self.mode == "activity":
            self.activity.follow = not self.activity.follow
            return None
        # Every other text key, including x/y/n/e/c/d/t/o/g, is a board-only action.
        return "blocked" if isinstance(key, str) else None


class HelpState:
    def __init__(self):
        self.scroll = Scroll()

    def key(self, key, page: int) -> str | None:
        delta = _scroll_delta(key, page)
        if delta is not None:
            self.scroll.move(delta)
            return None
        if key in ("\x1b", "?"):
            return "close"
        if key == "q":
            return "quit"
        return None
