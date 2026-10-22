#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "media"))

import frame  # noqa: E402


class SGRToHTML(unittest.TestCase):
    def test_true_colour_bold_and_reset(self):
        line = "\x1b[38;2;24;24;27;48;2;250;250;250m a \x1b[1;38;2;109;73;255mb\x1b[0m c"
        html = frame.line_html(line, frame.frame_defaults([line]))
        self.assertTrue(html.startswith(" a <span"), html)
        self.assertIn('<span style="color:#6d49ff;font-weight:700">b</span>', html)
        self.assertTrue(html.endswith(" c"), html)

    def test_256_colour_and_reverse(self):
        defaults = ("#eeeeee", "#121212")
        html = frame.line_html("\x1b[38;5;234mx\x1b[7my\x1b[27mz", defaults)
        self.assertIn('<span style="color:#1c1c1c">x</span>', html)
        self.assertIn('<span style="color:#121212;background:#1c1c1c">y</span>', html)
        self.assertTrue(html.endswith('<span style="color:#1c1c1c">z</span>'), html)

    def test_other_escapes_are_dropped_and_text_is_escaped(self):
        line = "\x1b]0;title\x07<a> & \x1b[2Kb"
        self.assertEqual(frame.line_html(line, (None, None)), "&lt;a&gt; &amp; b")
        self.assertEqual(frame.plain(["\x1b[1mbold\x1b[0m"]), ["bold"])

    def test_page_fits_the_frame_to_the_pixel_size(self):
        lines = ["\x1b[38;2;24;24;27;48;2;250;250;250mhello\x1b[0m"]
        page = frame.page_html(lines, 120, 36, (1440, 900))
        self.assertIn("width:1440px;height:900px", page)
        self.assertIn("background:#fafafa", page)
        self.assertIn("font:19.0px/1.3", page)  # 900 / (36 * 1.3) = 19.2, in half pixels


if __name__ == "__main__":
    unittest.main()
