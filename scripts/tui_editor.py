"""Unicode queue drafts and bracketed-paste decoding, independent of curses I/O."""
from __future__ import annotations

import curses
import time
import uuid
from dataclasses import dataclass, field

TEXT_CAP = 2000


@dataclass
class Draft:
    text: str = ""
    cursor: int = 0
    active: bool = False
    submitting: bool = False
    message: str = ""
    submission_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def insert(self, text):
        text = "".join(" " if char.isspace() else char for char in text if char.isprintable() or char.isspace())
        room = TEXT_CAP - len(self.text)
        if len(text) > room:
            self.message = f"Limit {TEXT_CAP} characters; extra input ignored"
        text = text[:room]
        self.text = self.text[:self.cursor] + text + self.text[self.cursor:]
        self.cursor += len(text)
        if text:
            self.submission_id = uuid.uuid4().hex

    def key(self, key, *, pasted=False):
        if key == "\x1b" and not pasted:
            self.active = False
            if not self.submitting:
                self.text, self.cursor = "", 0
                self.submission_id = uuid.uuid4().hex
            self.message = "Enqueue still pending" if self.submitting else "Enqueue cancelled"
            return "cancel"
        if self.submitting:
            return None
        before = self.text
        if pasted:
            self.insert(str(key))
        elif key in ("\n", "\r", curses.KEY_ENTER):
            return "submit" if self.text.strip() else "cancel"
        elif key in ("\b", "\x7f", curses.KEY_BACKSPACE):
            if self.cursor:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
        elif key == curses.KEY_DC:
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
        elif key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key in (curses.KEY_HOME, "\x01"):
            self.cursor = 0
        elif key in (curses.KEY_END, "\x05"):
            self.cursor = len(self.text)
        elif key == "\x15":
            self.text, self.cursor = self.text[self.cursor:], 0
        elif isinstance(key, str):
            self.insert(key)
        if self.text != before:
            self.submission_id = uuid.uuid4().hex
        return None


class InputDecoder:
    """Recognize paste boundaries without blocking to read an escape sequence."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.escape = ""
        self.started = 0.0
        self.pasting = False

    def feed(self, key):
        if self.escape:
            if isinstance(key, str):
                self.escape += key
                sequence = "\x1b[201~" if self.pasting else "\x1b[200~"
                if self.escape == sequence:
                    self.pasting = not self.pasting
                    self.escape = ""
                    return []
                if sequence.startswith(self.escape):
                    return []
            pending, self.escape = self.escape, ""
            output = [(char, self.pasting) for char in pending]
            return output if isinstance(key, str) else output + [(key, self.pasting)]
        if key == "\x1b":
            self.escape, self.started = key, self.clock()
            return []
        return [(key, self.pasting)]

    def flush(self):
        if self.escape and self.clock() - self.started >= 0.03:
            pending, self.escape = self.escape, ""
            return [(char, self.pasting) for char in pending]
        return []
