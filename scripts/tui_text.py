"""Terminal-cell text helpers for the board. Pure functions; no curses or I/O."""
from __future__ import annotations

import re
import unicodedata


def _cell_width(char):
    if unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me", "Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _text_width(text):
    return sum(_cell_width(char) for char in text)


def _sanitize(text) -> str:
    """Control characters would move the curses cursor; show them as spaces."""
    return "".join(" " if unicodedata.category(char) == "Cc" else char for char in str(text))


def _clip_cells(text, width):
    """Longest prefix within ``width`` cells; combining marks stay with their base."""
    used = 0
    for index, char in enumerate(text):
        used += _cell_width(char)
        if used > width:
            return text[:index]
    return text


def _elide(text: str, width: int) -> str:
    if width <= 1 or len(text) <= width:
        return text
    return "…" + text[-(width - 1):]


def _fit_head(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _text_width(text) <= width:
        return text
    if width == 1:
        return "…"
    return _clip_cells(text, width - 1) + "…"


def _fit_tail(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _text_width(text) <= width:
        return text
    if width == 1:
        return "…"
    keep = []
    used = 1
    for char in reversed(text):
        size = _cell_width(char)
        if used + size > width:
            break
        keep.append(char)
        used += size
    # A combining mark cannot start the kept tail without its base character.
    while keep and _cell_width(keep[-1]) == 0:
        keep.pop()
    return "…" + "".join(reversed(keep))


def _pad(text: str, width: int) -> str:
    text = _fit_head(text, width)
    return text + " " * max(0, width - _text_width(text))


def first_line(text) -> str:
    """Stable title: the first non-empty line of recorded text."""
    for line in str(text or "").splitlines():
        if line.strip():
            return _sanitize(line.strip())
    return ""


def abbrev_id(value, width: int = 12) -> str:
    """Short identity for list rows. Details always show the full ID."""
    return _fit_tail(str(value or ""), width)


def _split_word(word: str, width: int) -> list[str]:
    pieces = []
    while word:
        piece = _clip_cells(word, width)
        if not piece:
            # A single wide character wider than the column still has to advance.
            piece = word[0]
            while len(piece) < len(word) and _cell_width(word[len(piece)]) == 0:
                piece += word[len(piece)]
        pieces.append(piece)
        word = word[len(piece):]
    return pieces


def _hang(raw: str, width: int) -> str:
    """Continuation indent for aligned "Label   value" lines."""
    match = re.match(r"^(\S+(?: \S+){0,2}\s{2,})\S", raw)
    if match and _text_width(match.group(1)) <= min(20, width // 2):
        return " " * _text_width(match.group(1))
    return ""


def wrap_cells(text, width: int) -> list[str]:
    """Wrap by terminal cells, preserving explicit newlines, spacing, and Unicode clusters."""
    width = max(1, width)
    lines: list[str] = []
    for raw in str(text if text is not None else "").split("\n"):
        raw = _sanitize(raw).rstrip()
        if _text_width(raw) <= width:
            lines.append(raw)
            continue
        hang = _hang(raw, width)
        out: list[str] = []
        current = ""
        for token in re.findall(r"\s*\S+", raw):
            if out and not current.strip():
                token = token.lstrip()
            if _text_width(current + token) <= width:
                current += token
                continue
            if current.strip():
                out.append(current.rstrip())
                current, token = hang, token.lstrip()
                if _text_width(current + token) <= width:
                    current += token
                    continue
            token = token.lstrip() if out else token
            while token:
                room = width - _text_width(current)
                piece = _split_word(token, max(1, room))[0]
                if _text_width(piece) > room and current.strip():
                    out.append(current.rstrip())
                    current = ""
                    continue
                current += piece
                token = token[len(piece):]
                if token:
                    out.append(current)
                    current = hang
        if current.strip() or not out:
            out.append(current.rstrip())
        lines.extend(out)
    return lines
