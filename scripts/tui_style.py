"""Terminal-safe design tokens; retain the user's default background."""
from __future__ import annotations

import curses
import locale
import os

_ASCII = {"●": "*", "○": "o", "✓": "+", "✗": "x", "–": "-", "·": ".",
          "━": "=", "─": "-", "│": "|", "┬": "+", "┴": "+", "…": "~",
          "▸": ">", "↳": ">", "↓": "v", "↑": "^", "›": ">", "—": "-"}
_enabled = False


def terminal_text(text: str) -> str:
    encoding = locale.getpreferredencoding(False)
    ascii_only = os.environ.get("RIG_TUI_ASCII") == "1" or "utf" not in encoding.lower()
    ambiguous = os.environ.get("RIG_TUI_CJK") == "1"
    if ascii_only or ambiguous:
        return "".join(_ASCII.get(c, "?" if ascii_only and ord(c) > 127 else c) for c in text)
    return text


def pair(index: int) -> int:
    if not _enabled or "NO_COLOR" in os.environ:
        return 0
    try:
        return curses.color_pair(index)
    except curses.error:
        return 0


def initialize() -> None:
    global _enabled
    _enabled = False
    if "NO_COLOR" in os.environ:
        return
    try:
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        colors = (114, 210, 179, 111, 249, 60) if curses.COLORS >= 256 else (
            curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_YELLOW,
            curses.COLOR_CYAN, curses.COLOR_WHITE, curses.COLOR_BLUE)
        for index, color in enumerate(colors, 1):
            curses.init_pair(index, color, background)
        if curses.COLORS >= 256:
            curses.init_pair(7, 255, 235)
        else:
            curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)
        _enabled = True
    except curses.error:
        _enabled = False


def selection() -> int:
    return pair(7) | curses.A_BOLD if _enabled and "NO_COLOR" not in os.environ else curses.A_REVERSE | curses.A_BOLD


def state_glyph(label: str) -> str:
    if label in {"Needs input", "Stopping", "Stop unclear", "Host stop"}:
        return "!"
    if label in {"Running", "Reserved", "Checking", "Planned"}:
        return "●"
    return {"Verified": "✓", "Failed": "✗", "Cancelled": "–", "Unverified": "○"}.get(label, "·")
