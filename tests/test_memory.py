#!/usr/bin/env python3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import memory  # noqa: E402


class MemoryFile(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".rig").mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_add_show_dedupe(self):
        self.assertIn("no memory yet", memory.show_memory(self.repo))
        self.assertEqual(memory.add_memory(self.repo, "Codex sandbox must write ~/.grok"), "added")
        self.assertEqual(
            memory.add_memory(self.repo, "codex sandbox must write ~/.grok"),
            "exists",
        )
        shown = memory.show_memory(self.repo)
        self.assertIn("Codex sandbox must write ~/.grok", shown)
        self.assertEqual(shown.count("sandbox"), 1)
        self.assertEqual(memory.fact_count(self.repo), 1)

    def test_strips_bullet_and_skips_empty(self):
        self.assertEqual(memory.add_memory(self.repo, "  -  pin full Claude ids  "), "added")
        self.assertEqual(memory.add_memory(self.repo, "-"), "skip empty fact")
        self.assertEqual(memory.add_memory(self.repo, "   "), "skip empty fact")
        self.assertIn("- pin full Claude ids", memory.show_memory(self.repo))

    def test_caps_at_120_lines(self):
        for i in range(200):
            memory.add_memory(self.repo, f"fact number {i:03d} stays short")
        text = memory.memory_path(self.repo).read_text()
        self.assertLessEqual(text.count("\n") + (0 if text.endswith("\n") else 1), memory.MAX_LINES)
        self.assertIn("fact number 199", text)
        self.assertNotIn("fact number 000", text)
        self.assertGreater(memory.fact_count(self.repo), 50)


if __name__ == "__main__":
    unittest.main()
