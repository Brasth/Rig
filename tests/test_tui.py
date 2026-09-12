#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rig_tui  # noqa: E402


class FakeScr:
    def __init__(self, h: int, w: int) -> None:
        self.h = h
        self.w = w
        self.calls: list[tuple] = []

    def getmaxyx(self) -> tuple[int, int]:
        return self.h, self.w

    def addnstr(self, y: int, x: int, text: str, n: int, attr: int = 0) -> None:
        written = min(n, len(text))
        if y == self.h - 1 and x + written >= self.w:
            raise rig_tui.curses.error("addnwstr() returned ERR")
        self.calls.append((y, x, text, n, attr))


class LastCellClip(unittest.TestCase):
    def test_room_last_row_leaves_one_cell(self):
        self.assertEqual(rig_tui._room(23, 0, 24, 80), 79)
        self.assertEqual(rig_tui._room(23, 10, 24, 80), 69)
        self.assertEqual(rig_tui._room(23, 79, 24, 80), 0)

    def test_room_other_rows_full_width(self):
        self.assertEqual(rig_tui._room(0, 0, 24, 80), 80)
        self.assertEqual(rig_tui._room(10, 5, 24, 80), 75)

    def test_enqueue_prompt_does_not_write_bottom_right(self):
        scr = FakeScr(24, 80)
        prompt = "enqueue: "
        rig_tui._add(scr, 23, 0, prompt + " " * 80, rig_tui.curses.A_REVERSE)
        self.assertEqual(len(scr.calls), 1)
        y, x, text, n, _attr = scr.calls[0]
        self.assertEqual((y, x), (23, 0))
        self.assertEqual(n, 79)
        self.assertEqual(len(text), 79)
        self.assertLess(x + n, 80)

    def test_footer_as_wide_as_screen_does_not_raise(self):
        scr = FakeScr(24, 80)
        rig_tui._add(scr, 23, 0, "x" * 200, rig_tui.curses.A_REVERSE, width=80)
        self.assertEqual(scr.calls[0][3], 79)

    def test_title_row_can_use_full_width(self):
        scr = FakeScr(24, 80)
        rig_tui._add(scr, 0, 0, "t" * 80, rig_tui.curses.A_REVERSE, width=80)
        self.assertEqual(scr.calls[0][3], 80)


if __name__ == "__main__":
    unittest.main()
