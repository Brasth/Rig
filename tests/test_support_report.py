#!/usr/bin/env python3
"""rig support: one report to send, with the failures in it and no secrets."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import shell_doctor  # noqa: E402
import support_report  # noqa: E402


class SupportReport(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = Path(self.td.name).resolve() / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig" / "chat").mkdir(parents=True)
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
        for patch in (mock.patch.object(support_report, "run", return_value="(stubbed)"),
                      mock.patch.object(shell_doctor, "from_doctor", return_value=[])):
            patch.start()
            self.addCleanup(patch.stop)

    def chat(self, rows):
        path = self.repo / ".rig" / "chat" / "c-aaaa1111.jsonl"
        path.write_text("".join(json.dumps({"at": "2026-10-22T00:00:00Z", "role": r, "text": t}) + "\n" for r, t in rows))
        return path

    def test_the_report_names_the_failed_tool_and_its_answer(self):
        self.chat([("user", "get the ticket"), ("tool", "codex_apps.asana.get_user"), ("result", "huy@x.com"),
                   ("tool", "codex_apps.asana.get_task"), ("result", "Not Authorized.")])
        text = support_report.build(self.repo)
        self.assertIn("tool: codex_apps.asana.get_task", text)
        self.assertIn("result: Not Authorized.", text)
        self.assertNotIn("get_user", text.split("tool errors")[1].split("==")[0])  # the working call is not a failure

    def test_what_the_parent_printed_and_how_rig_runs_it_are_in_the_report(self):
        self.chat([("user", "x")])
        (self.repo / ".rig" / "chat" / "c-aaaa1111.parent.log").write_text("--- t\nWARN mcp asana: 401\n")
        text = support_report.build(self.repo)
        self.assertIn("WARN mcp asana: 401", text)
        self.assertIn("codex exec --json", text)
        self.assertNotIn("<your message>" * 2, text)

    def test_secrets_are_left_out(self):
        self.chat([("tool", "x"), ("result", "error: Authorization: Bearer abcdefghijklmnop1234 and token=supersecret1 and sk-abcdefghij12345")])
        text = support_report.build(self.repo)
        for leaked in ("abcdefghijklmnop1234", "supersecret1", "sk-abcdefghij12345"):
            self.assertNotIn(leaked, text)
        self.assertIn("«hidden»", text)

    def test_environment_variables_are_named_never_shown(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-realvalue1234567"}):
            text = support_report.build(self.repo)
        self.assertIn("OPENAI_API_KEY: set", text)
        self.assertNotIn("realvalue", text)

    def test_write_puts_the_report_in_the_project(self):
        path = support_report.write(self.repo)
        self.assertEqual(path, self.repo / ".rig" / "support.txt")
        self.assertIn("== machine ==", path.read_text())


class ParentStderrIsKept(unittest.TestCase):
    def test_parent_log_keeps_the_newest_text(self):
        import rig_chat
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".rig" / "chat").mkdir(parents=True)
            rig_chat.parent_log(td, "c-aaaa1111", "boom")
            rig_chat.parent_log(td, "c-aaaa1111", "   ")
            text = (Path(td) / ".rig" / "chat" / "c-aaaa1111.parent.log").read_text()
        self.assertIn("boom", text)
        self.assertEqual(text.count("---"), 1)


if __name__ == "__main__":
    unittest.main()
