"""Board chrome: layout budget, header, tab strip, message row, and key hints."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tui_rows import _TABS
from tui_text import _fit_head, _text_width, wrap_cells

MIN_WIDTH, MIN_HEIGHT = 40, 8
SPLIT_MIN_WIDTH = 100
TWO_LINE_MIN_HEIGHT = 12
TOO_SMALL = f"Terminal too small: resize to at least {MIN_WIDTH}x{MIN_HEIGHT} · q quits"


@dataclass(frozen=True)
class Layout:
    """Rows: 0 header, 1 tabs, 2 list heading, list body, message row(s), hints."""
    h: int
    w: int
    tiny: bool
    split: bool
    left_w: int
    list_top: int
    list_rows: int
    message_y: int
    message_rows: int
    entry_lines: int

    @property
    def capacity(self) -> int:
        """Whole list entries that fit; two-line job entries count once."""
        return max(1, self.list_rows // max(1, self.entry_lines)) if not self.tiny else 0


def layout(h: int, w: int, *, tab: str = "Jobs", message_rows: int = 1) -> Layout:
    if h < MIN_HEIGHT or w < MIN_WIDTH:
        return Layout(h, w, True, False, w, 0, 0, max(0, h - 2), 0, 1)
    message_rows = 2 if message_rows > 1 and h >= TWO_LINE_MIN_HEIGHT else 1
    split = w >= SPLIT_MIN_WIDTH
    left_w = max(48, min(72, w * 11 // 20)) if split else w
    list_rows = h - 4 - message_rows
    entry = 2 if tab == "Jobs" and h >= TWO_LINE_MIN_HEIGHT else 1
    return Layout(h, w, False, split, left_w, 3, list_rows, h - 1 - message_rows, message_rows, entry)


def project_name(repo) -> str:
    try:
        return Path(repo).name or str(repo)
    except TypeError:
        return str(repo)


def header_text(snapshot, repo) -> str:
    """Project and health only; snapshot age lives in the message row."""
    jobs = list(getattr(snapshot, "jobs", None) or [])
    asking = sum(row.get("effective") == "ask" for row in jobs)
    running = sum(row.get("effective") == "running" for row in jobs)
    reserved = sum(row.get("effective") == "reserved" for row in jobs)
    health = (f"{asking} need input · {running} running · {reserved} reserved · "
              f"slots {getattr(snapshot, 'slots', 0)}/{getattr(snapshot, 'cap', 0)}")
    if not getattr(snapshot, "captured_at", 0):
        health = "loading…"
    return f" Rig · {project_name(repo)}  {health}"


_FULL = {"Jobs": "Jobs", "Queue": "Queue", "Workflows": "Workflows", "Settings": "Settings"}
_COMPACT = {"Jobs": "Jobs", "Queue": "Queue", "Workflows": "Flows", "Settings": "Set"}
_TINY = {"Jobs": "J", "Queue": "Q", "Workflows": "W", "Settings": "S"}


def _tab_label(name: str, names: dict, counts: dict) -> str:
    count = int(counts.get(name) or 0)
    if not count:
        return names[name]
    # "!" marks attention counts; Queue shows its pending count.
    return f"{names[name]} {count}" + ("" if name == "Queue" else "!")


def tab_segments(active: str, counts: dict, width: int) -> list[tuple[str, bool]]:
    """Every tab and count, shortening names before anything is dropped."""
    for names, gap in ((_FULL, " "), (_COMPACT, " "), (_TINY, "")):
        segments = [(f" {_tab_label(name, names, counts)} ", name == active) for name in _TABS]
        total = sum(_text_width(text) for text, _ in segments) + len(gap) * (len(segments) - 1)
        if total <= width:
            return [(text, on) if index == 0 else (gap + text, on) for index, (text, on) in enumerate(segments)]
    return segments


def list_heading(tab: str, start: int, stop: int, count: int, width: int) -> str:
    text = f"{tab} {start + 1 if count else 0}-{stop}/{count}"
    if tab == "Jobs":
        text += " · attention first"
    return _fit_head(text, width)


def message_lines(notice, snapshot_status: str, width: int, rows: int = 1) -> list[tuple[str, str]]:
    """(text, kind) rows. Notices win; refresh status fills the remaining space."""
    status = str(snapshot_status or "")
    if notice is None:
        return [(_fit_head(status, width), "status")]
    text, kind = str(notice.text), notice.kind
    wrapped = wrap_cells(text, width)
    if len(wrapped) <= 1:
        line = wrapped[0] if wrapped else ""
        spare = width - _text_width(line) - 3
        if status and spare >= min(12, _text_width(status)):
            tail = _fit_head(status, spare)
            line += " " * (width - _text_width(line) - _text_width(tail)) + tail
        return [(line, kind)]
    shown = wrapped[:max(1, rows)]
    if len(wrapped) > len(shown):
        shown[-1] = _fit_head(shown[-1] + " " + " ".join(wrapped[len(shown):]), width)
    return [(line, kind) for line in shown]


def message_rows_needed(notice, width: int) -> int:
    if notice is None or notice.kind != "error":
        return 1
    return 2 if len(wrap_cells(notice.text, width)) > 1 else 1


def _fit_hints(parts: list[str], keep: list[str], width: int) -> str:
    """Drop optional hints from the end until the persistent ones fit."""
    parts = list(parts)
    while parts and _text_width("  ".join(parts + keep)) > width:
        parts.pop()
    return "  ".join(parts + keep)


def board_hints(tab: str, row, *, log_mode: bool = False, split: bool = False, width: int = 200) -> str:
    row = row or {}
    parts = []
    if tab in {"Jobs", "Queue", "Workflows"} and row:
        parts.append("Enter details")
    if tab == "Jobs" and row.get("effective") == "ask":
        parts.append("y/n answer")
    if tab == "Jobs" and row:
        parts.append("x stop")
    elif tab == "Queue" and row:
        parts.append("x cancel")
    if tab == "Settings":
        parts.append("e routing/preview" if row.get("id") == "domains" else "c/d key · t/o picker")
    else:
        parts.append("e enqueue")
    parts.append("j/k move")
    parts.append("Tab view")
    if tab == "Jobs" and row:
        parts.append(("l details" if log_mode else "l activity") if split else "l activity")
        if split and log_mode:
            parts.append("f follow")
    if tab in {"Jobs", "Workflows"} and row:
        parts.append("g recovery")
    parts.append("r refresh")
    return _fit_hints(parts, ["? help", "q quit"], width)


def detail_hints(tab: str, mode: str, follow: bool, width: int) -> str:
    parts = ["j/k scroll", "Enter/Esc close", "PgUp/PgDn page", "Tab switch"]
    if tab == "Jobs":
        parts.insert(2, "l details" if mode == "activity" else "l activity")
        if mode == "activity":
            parts.insert(3, "f follow off" if follow else "f follow on")
    return _fit_hints(parts, ["q quit"], width)


def help_hints(width: int) -> str:
    return _fit_hints(["j/k scroll", "PgUp/PgDn page"], ["Esc/? close", "q quit"], width)
