#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import jobs  # noqa: E402


STREAM = """
{"type":"thought","data":"Looking at the OpenCode client"}
{"type":"tool_call","toolCallId":"c1","title":"Read","kind":"read","status":"in_progress","toolName":"read_file","rawInput":{"target_file":"apps/ai-agent/src/providers/opencode-go-client.ts"}}
{"type":"tool_call_update","toolCallId":"c1","status":"completed"}
{"type":"text","data":"I will add a session header"}
{"type":"end","stopReason":"end_turn"}
""".strip()

JSON_BLOB = json.dumps(
    {
        "text": "I'll inspect the specs.\nOpenCode Go 400 is fixed by sending a session header.",
        "stopReason": "end_turn",
        "sessionId": "abc",
    }
)


class TaskAndLog(unittest.TestCase):
    def test_task_skips_preamble(self):
        brief = (
            "You are a worker, not the orchestrator. Do not spawn codex, grok, or claude.\n"
            "Fix OpenCode Go 400 missing x-opencode-session.\n"
        )
        self.assertIn("OpenCode Go 400", jobs.task_from_brief(brief, "260908-opencode-session-fix"))

    def test_task_falls_back_to_slug(self):
        self.assertEqual(
            jobs.task_from_brief("", "260908-opencode-session-fix"),
            "opencode session fix",
        )

    def test_decode_streaming_json_doing(self):
        acts = jobs.decode_log_text(STREAM)
        self.assertTrue(any("read_file" in a and "opencode-go-client.ts" in a for a in acts))
        self.assertEqual(acts[-1], "I will add a session header")

    def test_decode_json_blob(self):
        acts = jobs.decode_log_text(JSON_BLOB)
        self.assertTrue(any("session header" in a for a in acts))

    def test_incomplete_json_blob(self):
        acts = jobs.decode_log_text('{"text": "still going')
        self.assertEqual(acts, ["waiting for child json (buffered until exit)"])


class JobBoard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.id().replace(".", "_"))
        # use isolated tmp via tempfile in each test
        import tempfile

        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        jobs_dir = self.repo / ".rig" / "jobs" / "260908-opencode-session-fix"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "brief.md").write_text(
            "You are a worker, not the orchestrator.\n"
            "Fix OpenCode Go 400 missing session header.\n"
        )
        (jobs_dir / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "260908-opencode-session-fix",
                    "worker": "grok",
                    "role": "implement",
                    "status": "running",
                    "session_id": "sess-1",
                    "open": "grok -r sess-1",
                }
            )
        )
        (jobs_dir / "stdout.log").write_text(STREAM + "\n")

    def tearDown(self):
        self.td.cleanup()

    def test_list_running_agent_task_doing(self):
        listing = jobs.list_jobs(self.repo)
        self.assertEqual(len(listing), 1)
        job = listing[0]
        self.assertEqual(job["worker"], "grok")
        self.assertEqual(job["effective"], "running")
        self.assertIn("OpenCode Go 400", job["task"])
        self.assertIn("session header", job["doing"] or job["activities"][-1])

    def test_list_prefers_running_then_recent(self):
        older = self.repo / ".rig" / "jobs" / "older-fail"
        older.mkdir()
        (older / "meta.json").write_text(
            json.dumps({"job_id": "older-fail", "worker": "grok", "role": "worker", "status": "fail"})
        )
        listing = jobs.list_jobs(self.repo)
        self.assertEqual(listing[0]["job_id"], "260908-opencode-session-fix")

    def test_dead_pid_is_stale(self):
        meta = self.repo / ".rig" / "jobs" / "260908-opencode-session-fix" / "meta.json"
        obj = json.loads(meta.read_text())
        obj["pid"] = 99999991
        meta.write_text(json.dumps(obj))
        job = jobs.list_jobs(self.repo)[0]
        self.assertEqual(job["effective"], "stale")

    def test_table_and_show(self):
        listing = jobs.list_jobs(self.repo)
        table = jobs.format_table(listing)
        self.assertIn("grok", table)
        self.assertIn("260908-opencode-session-fix", table)
        shown = jobs.format_show(listing[0])
        self.assertIn("agent   grok", shown)
        self.assertIn("read_file", shown)

    def test_statusline(self):
        text = jobs.format_statusline(
            {
                "cwd": str(self.repo),
                "workspace": {"current_dir": str(self.repo), "repo_root": str(self.repo)},
                "model": {"display_name": "Grok 4.6"},
                "context_window": {"used_percentage": 12},
            }
        )
        self.assertIn("Grok 4.6", text)
        self.assertIn("rig ·", text)
        self.assertIn("grok", text)
        self.assertIn("running", text)


class McpTools(unittest.TestCase):
    def test_list_and_show(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import rig_mcp

        td = __import__("tempfile").TemporaryDirectory()
        repo = Path(td.name)
        d = repo / ".rig" / "jobs" / "j1"
        d.mkdir(parents=True)
        (d / "brief.md").write_text("Add session headers to OpenCode Go.\n")
        (d / "meta.json").write_text(
            json.dumps({"job_id": "j1", "worker": "grok", "role": "implement", "status": "ok"})
        )
        (d / "stdout.log").write_text(
            '{"type":"tool_call","toolName":"read_file","rawInput":{"path":"README.md"}}\n'
        )
        listed = rig_mcp.call_tool("rig_jobs", {"repo": str(repo)})
        self.assertIn("j1", listed["content"][0]["text"])
        shown = rig_mcp.call_tool("rig_job_show", {"repo": str(repo), "id": "j1"})
        self.assertIn("agent   grok", shown["content"][0]["text"])
        log = rig_mcp.call_tool("rig_job_log", {"repo": str(repo), "id": "j1"})
        self.assertIn("read_file", log["content"][0]["text"])
        td.cleanup()

    def test_ndjson_initialize_replies(self):
        import select
        import subprocess
        import time

        script = ROOT / "scripts" / "rig_mcp.py"
        init = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            }
        )
        p = subprocess.Popen(
            ["python3", "-u", str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert p.stdin and p.stdout
        p.stdin.write(init.encode() + b"\n")
        p.stdin.flush()
        buf = b""
        deadline = time.time() + 2
        while time.time() < deadline and b"\n" not in buf:
            ready, _, _ = select.select([p.stdout], [], [], 0.2)
            if ready:
                buf += p.stdout.read1(4096)
            if p.poll() is not None:
                break
        p.kill()
        p.wait(timeout=2)
        self.assertIn(b'"protocolVersion": "2025-03-26"', buf)
        self.assertIn(b'"name": "rig"', buf)


if __name__ == "__main__":
    unittest.main()
