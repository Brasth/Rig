#!/usr/bin/env python3
"""/selftest: tiny real turns per installed parent, in a throwaway project, reported as it goes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import selftest  # noqa: E402
from test_chat_experiment import CLAUDE_STREAM, stand_in  # noqa: E402


class SelfTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.base = Path(self.td.name).resolve()
        self.repo = self.base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        self.bin = self.base / "bin"
        self.record = self.base / "record.jsonl"
        env = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
                                           "STANDIN_RECORD": str(self.record), "RIG_EXPERIMENT_CHAT": "1"})
        env.start()
        self.addCleanup(env.stop)

    def checks(self, data, parent="claude"):
        row = next(p for p in data["parents"] if p["name"] == parent)
        return {c["id"]: (c["status"], c["detail"]) for c in row["checks"]}

    def test_it_runs_every_check_for_each_installed_parent_and_writes_the_report(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        fake = {cid: (lambda ctx, cid=cid: ("pass", cid)) for cid, _ in selftest.CHECKS}
        data = selftest.run(self.repo, ["claude", "codex"], functions=fake)  # codex is not installed here
        self.assertEqual([p["name"] for p in data["parents"]], ["claude"], "only installed parents are tried")
        self.assertEqual({v[0] for v in self.checks(data).values()}, {"pass"})
        self.assertEqual(data["state"], "done")
        saved = json.loads((self.repo / ".rig" / "selftest.json").read_text())
        self.assertEqual(saved["state"], "done")
        text = (self.repo / ".rig" / "selftest.txt").read_text()
        self.assertIn("claude\n  ✓ answers a message: pass — basic", text)

    def test_a_check_that_crashes_is_a_result_and_no_answer_skips_the_rest(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        def boom(ctx):
            raise IndexError("list index out of range")
        data = selftest.run(self.repo, ["claude"], functions={**{c: (lambda ctx: ("pass", "")) for c, _ in selftest.CHECKS}, "basic": boom})
        got = self.checks(data)
        self.assertEqual(got["basic"][0], "fail")
        self.assertIn("the check itself crashed: list index out of range (IndexError", got["basic"][1])
        self.assertEqual({got[c][0] for c in ("effort", "model", "plan", "edits", "stop")}, {"skip"})

    def test_the_basic_check_really_talks_to_the_parent_in_a_throwaway_project(self):
        stand_in(self.bin, "claude", [{"type": "assistant", "message": {"content": [{"type": "text", "text": "PONG"}]}},
                                     {"type": "result", "subtype": "success", "result": "PONG", "session_id": "s1"}])
        only_basic = {c: (lambda ctx: ("skip", "")) for c, _ in selftest.CHECKS}
        only_basic["basic"] = selftest.check_basic
        data = selftest.run(self.repo, ["claude"], functions=only_basic)
        self.assertEqual(self.checks(data)["basic"], ("pass", ""))
        cwd = json.loads(self.record.read_text().splitlines()[0])["cwd"]
        self.assertIn("rig-selftest-claude-", cwd)
        self.assertNotEqual(Path(cwd).resolve(), self.repo, "never the user's own project")
        self.assertFalse(Path(cwd).exists(), "the throwaway project is removed")
        # a parent that answers something else fails with a reason
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        data = selftest.run(self.repo, ["claude"], functions=only_basic)
        self.assertEqual(self.checks(data)["basic"], ("fail", "it ended without the answer"))

    def test_a_new_run_shows_as_running_at_once_not_as_the_last_report(self):
        stand_in(self.bin, "claude", CLAUDE_STREAM)
        selftest.report_path(self.repo).write_text(json.dumps({"state": "done", "pid": 1, "parents": [{"name": "claude", "checks": []}]}))
        with mock.patch.object(selftest.subprocess, "Popen") as popen:
            popen.return_value.pid = os.getpid()
            started = selftest.start(self.repo)
        self.assertTrue(started["started"])
        status = selftest.read_report(self.repo)
        self.assertEqual((status["state"], status["running"], status["parents"]), ("running", True, []))

    def test_status_reads_the_report_and_knows_a_dead_run(self):
        self.assertEqual(selftest.read_report(self.repo)["state"], "none")
        report = {"state": "running", "pid": 2_000_000_000, "parents": []}
        selftest.report_path(self.repo).write_text(json.dumps(report))
        status = selftest.read_report(self.repo)
        self.assertEqual((status["state"], status["running"]), ("stopped", False))
        report["pid"] = os.getpid()
        selftest.report_path(self.repo).write_text(json.dumps(report))
        status = selftest.read_report(self.repo)
        self.assertEqual((status["state"], status["running"]), ("running", True))
        again = selftest.start(self.repo)
        self.assertFalse(again["started"])
        self.assertIn("already running", again["reason"])


if __name__ == "__main__":
    unittest.main()
