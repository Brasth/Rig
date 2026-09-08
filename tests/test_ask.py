#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ask  # noqa: E402
import jobs  # noqa: E402
import rig_mcp  # noqa: E402


class AskProtocol(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.job = Path(self.td.name) / "job"
        self.job.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_preview_and_parse(self):
        tool, inp, uid = ask.parse_prompt_args(
            {
                "tool_name": "Bash",
                "input": {"command": "ssh vm -- ls /var/log"},
                "tool_use_id": "toolu_1",
            }
        )
        self.assertEqual(tool, "Bash")
        self.assertEqual(inp["command"], "ssh vm -- ls /var/log")
        self.assertEqual(uid, "toolu_1")
        self.assertIn("ssh vm", ask.preview(tool, inp))

    def test_allow_keeps_original_input(self):
        inp = {"command": "ssh vm -- df -h"}
        reply = {"behavior": "allow"}
        decision = ask.decision_from_reply(reply, inp)
        self.assertEqual(decision["behavior"], "allow")
        self.assertEqual(decision["updatedInput"], inp)

    def test_deny_has_message(self):
        decision = ask.decision_from_reply({"behavior": "deny", "message": "prod"}, {})
        self.assertEqual(decision, {"behavior": "deny", "message": "prod"})

    def test_wait_reply_from_parent(self):
        ask.write_ask(self.job, "Bash", {"command": "git status"}, "t1")

        def later():
            time.sleep(0.15)
            ask.write_reply(self.job, "allow", "", "t1")

        threading.Thread(target=later, daemon=True).start()
        reply = ask.wait_reply(self.job, timeout=2)
        self.assertEqual(reply["behavior"], "allow")
        decision = ask.decision_from_reply(reply, {"command": "git status"})
        self.assertEqual(decision["updatedInput"]["command"], "git status")

    def test_wait_timeout_denies(self):
        ask.write_ask(self.job, "Bash", {"command": "rm -rf /"}, "t2")
        reply = ask.wait_reply(self.job, timeout=0.4)
        self.assertEqual(reply["behavior"], "deny")
        self.assertIn("did not answer", reply["message"])


class AskJobBoard(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        self.d = self.repo / ".rig" / "jobs" / "claude-ask"
        self.d.mkdir(parents=True)
        (self.d / "brief.md").write_text("SSH to the VM and gather nginx logs.\n")
        (self.d / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "claude-ask",
                    "worker": "claude",
                    "role": "implement",
                    "status": "running",
                    "pid": os.getpid(),
                    "model": "claude-sonnet-5",
                    "effort": "medium",
                }
            )
        )
        ask.write_ask(self.d, "Bash", {"command": "ssh vm -- tail /var/log/nginx/error.log"}, "t9")

    def tearDown(self):
        self.td.cleanup()

    def test_load_job_is_ask(self):
        job = jobs.load_job(self.d)
        self.assertEqual(job["effective"], "ask")
        self.assertIn("ASK", job["doing"])
        self.assertIn("rig job allow claude-ask", job["doing"])
        table = jobs.format_table(jobs.list_jobs(self.repo))
        self.assertIn("ask", table.lower())
        self.assertIn("rig job allow claude-ask", table)

    def test_parent_allow(self):
        job = jobs.load_job(self.d)
        text = jobs.answer_pending(job, "allow")
        self.assertTrue(text.startswith("allow"), text)
        reply = ask._read_json(ask.reply_path(self.d))
        self.assertEqual(reply["behavior"], "allow")
        job2 = jobs.load_job(self.d)
        self.assertEqual(job2["effective"], "running")

    def test_mcp_allow(self):
        out = rig_mcp.call_tool("rig_job_allow", {"repo": str(self.repo), "id": "claude-ask"})
        self.assertIn("allow", out["content"][0]["text"])
        self.assertNotIn("isError", out)

    def test_allow_when_not_asking(self):
        ask.consume_ask(self.d)
        job = jobs.load_job(self.d)
        text = jobs.answer_pending(job, "allow")
        self.assertIn("not waiting", text)

    def test_wait_exits_ask(self):
        code, text = jobs.wait_job(self.repo, "claude-ask", timeout=0)
        self.assertEqual(code, 2)
        self.assertIn("ASK claude-ask", text)
        self.assertIn("rig job allow claude-ask", text)
        self.assertIn("Do not kill this job", text)
        self.assertIn("Do not spawn another worker", text)

    def test_wait_exits_ok_when_finished(self):
        ask.consume_ask(self.d)
        meta = json.loads((self.d / "meta.json").read_text())
        meta["status"] = "ok"
        meta["summary"] = "done"
        (self.d / "meta.json").write_text(json.dumps(meta))
        (self.d / "result.json").write_text(json.dumps({"status": "ok"}))
        code, text = jobs.wait_job(self.repo, "claude-ask", timeout=0)
        self.assertEqual(code, 0)
        self.assertIn("claude-ask", text)

    def test_wait_running_times_out(self):
        ask.consume_ask(self.d)
        code, text = jobs.wait_job(self.repo, "claude-ask", timeout=0)
        self.assertEqual(code, 124)
        self.assertIn("RUNNING claude-ask", text)

    def test_show_tells_parent_not_to_replace(self):
        job = jobs.load_job(self.d)
        shown = jobs.format_show(job)
        self.assertIn("do not kill this job", shown)
        self.assertIn("do not spawn a replacement", shown)

    def test_mcp_wait_ask(self):
        out = rig_mcp.call_tool(
            "rig_job_wait",
            {"repo": str(self.repo), "id": "claude-ask", "timeout": 0},
        )
        self.assertIn("ASK", out["content"][0]["text"])
        self.assertNotIn("isError", out)


if __name__ == "__main__":
    unittest.main()
