#!/usr/bin/env python3
"""An unexpected error says where it came from and leaves its traceback in .rig/mcp-errors.log."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import crash_log  # noqa: E402
import jobs  # noqa: E402
import rig_mcp  # noqa: E402


def _raised():
    try:
        [][0]
    except IndexError as error:
        return error


def _raised_now():
    [][0]


class CrashLog(unittest.TestCase):
    def test_the_message_names_the_exception_the_file_and_the_function(self):
        text = crash_log.describe(_raised())
        self.assertTrue(text.startswith("list index out of range (IndexError at test_crash_log.py:"), text)
        self.assertTrue(text.endswith("in _raised)"), text)
        self.assertEqual(crash_log.describe(ValueError()), "ValueError")

    def test_the_innermost_frame_in_rigs_scripts_is_the_one_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "thread").write_text("x\n")
            with mock.patch.object(Path, "read_text", side_effect=IndexError("list index out of range")):
                try:
                    jobs.current_thread(repo)
                except IndexError as error:
                    self.assertIn("IndexError at jobs.py:", crash_log.describe(error))

    def test_a_tool_that_crashes_reports_where_and_keeps_the_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
            with mock.patch.object(rig_mcp.rig_harness, "format_status", side_effect=lambda *a, **k: _raised_now()):
                result = rig_mcp.call_tool("rig_status", {"repo": str(repo)})
            self.assertTrue(result.get("isError"))
            self.assertIn("list index out of range (IndexError at routing.py:", result["content"][0]["text"])  # the innermost frame in Rig's scripts
            log = (repo / ".rig" / crash_log.LOG_NAME).read_text()
            self.assertIn("rig_status", log)
            self.assertIn("Traceback (most recent call last)", log)

    def test_the_log_keeps_only_the_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(400):
                crash_log.record(tmp, f"tool-{i}", ValueError("x" * 400))
            text = (Path(tmp) / ".rig" / crash_log.LOG_NAME).read_text()
            self.assertLessEqual(len(text), crash_log.LOG_LIMIT)
            self.assertIn("tool-399", text)
            self.assertNotIn("tool-0 ", text)


if __name__ == "__main__":
    unittest.main()
