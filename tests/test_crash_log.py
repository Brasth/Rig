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

    def test_an_expected_rejection_is_an_error_without_a_logged_traceback(self):
        import admission
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
            def reject(*a, **k):
                raise admission.AdmissionError("overlapping workflow writer scopes")
            with mock.patch.object(rig_mcp.rig_harness, "format_status", side_effect=reject):
                result = rig_mcp.call_tool("rig_status", {"repo": str(repo)})
            self.assertTrue(result.get("isError"))
            self.assertEqual(result["content"][0]["text"], "overlapping workflow writer scopes")
            self.assertFalse((repo / ".rig" / crash_log.LOG_NAME).exists())

    def test_any_rig_defined_rejection_is_not_logged_but_a_plain_value_error_is(self):
        import context_packages
        self.assertTrue(rig_mcp._is_rig_rejection(context_packages.ContextPackageError("context refused: x")))
        self.assertTrue(rig_mcp._is_rig_rejection(__import__("workflow_state").WorkflowError("overlap")))
        self.assertFalse(rig_mcp._is_rig_rejection(ValueError("a bug")))
        self.assertFalse(rig_mcp._is_rig_rejection(_raised()))
        import json
        try:
            json.loads("{")
        except ValueError as error:
            self.assertFalse(rig_mcp._is_rig_rejection(error))

    def _serve(self, raw, handle=None):
        import io
        out = io.BytesIO()
        stdin = mock.Mock(buffer=io.BytesIO(raw))
        stdout = mock.Mock(buffer=out)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(rig_mcp.sys, "stdin", stdin), \
                mock.patch.object(rig_mcp.sys, "stdout", stdout), mock.patch.object(rig_mcp, "_FRAMING", "ndjson"), \
                mock.patch.object(rig_mcp.rig_jobs, "repo_root", return_value=Path(tmp)), \
                mock.patch.object(rig_mcp._runtime, "shutdown"):
            if handle:
                with mock.patch.object(rig_mcp, "handle", side_effect=handle):
                    code = rig_mcp._serve_messages()
            else:
                code = rig_mcp._serve_messages()
            log = Path(tmp, ".rig", crash_log.LOG_NAME)
            return code, out.getvalue().decode(), log.read_text() if log.exists() else ""

    def test_a_malformed_message_gets_a_parse_error_and_the_server_keeps_serving(self):
        code, out, log = self._serve(b'{not json\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
        self.assertEqual(code, 0)
        self.assertIn('"code": -32700', out)
        self.assertIn('"id": 7', out)
        self.assertIn("transport parse error", log)

    def test_a_handler_failure_answers_that_request_and_keeps_serving(self):
        def boom(msg):
            if msg.get("id") == 1:
                raise RuntimeError("handler bug")
            return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}
        code, out, log = self._serve(b'{"id":1,"method":"x"}\n{"id":2,"method":"x"}\n', handle=boom)
        self.assertEqual(code, 0)
        self.assertIn('"code": -32603', out)
        self.assertIn('"id": 2, "result"', out)
        self.assertIn("transport handle failed", log)

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
