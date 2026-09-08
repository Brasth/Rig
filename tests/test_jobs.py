#!/usr/bin/env python3
import json
import os
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

    def test_timestamp_pid_id_is_not_a_task(self):
        self.assertEqual(
            jobs.task_from_brief("", "20260908T071315Z-4994"),
            "20260908T071315Z-4994",
        )

    def test_decode_streaming_json_doing(self):
        acts = jobs.decode_log_text(STREAM)
        self.assertTrue(any("read_file" in a and "opencode-go-client.ts" in a for a in acts))
        self.assertEqual(acts[-1], "I will add a session header")

    def test_thought_tokens_coalesce(self):
        raw = "\n".join(
            json.dumps({"type": "thought", "data": chunk})
            for chunk in ("The", " user", " wants", " a", " review")
        )
        raw += "\n" + json.dumps(
            {
                "type": "tool_call",
                "toolName": "read_file",
                "rawInput": {"target_file": "apps/ai-agent/src/providers/opencode-go-client.ts"},
            }
        )
        acts = jobs.decode_log_text(raw)
        think_lines = [a for a in acts if a.startswith("think")]
        self.assertEqual(len(think_lines), 1)
        self.assertIn("The user wants a review", think_lines[0])
        self.assertTrue(any(a.startswith("read_file") for a in acts))
        self.assertNotIn("think The", "\n".join(acts))

    def test_decode_json_blob(self):
        acts = jobs.decode_log_text(JSON_BLOB)
        self.assertTrue(any("session header" in a for a in acts))

    def test_incomplete_json_blob(self):
        acts = jobs.decode_log_text('{"text": "still going')
        self.assertEqual(acts, ["waiting for child json (buffered until exit)"])

    def test_claude_managed_settings_noise_hidden(self):
        raw = "\n".join(
            [
                'remote managed settings (permissions.deny): Invalid permission rule "Bash(eval $(wget*))" was skipped: Mismatched parentheses. Ensure all opening parentheses have matching closing parentheses.',
                "Managed settings contain invalid entries (remaining valid policies are still enforced):",
                'remote managed settings (permissions.deny): Invalid permission rule "Bash(bash <(curl*))" was skipped: Mismatched parentheses.',
                'remote managed settings (permissions.deny): Invalid permission rule "Bash(eval $(curl*))" was skipped: Mismatched parentheses.',
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Read",
                                    "input": {"file_path": "apps/next/src/container.ts"},
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        acts = jobs.decode_log_text(raw)
        self.assertEqual(acts, ["Read src/container.ts"])
        self.assertFalse(any("wget" in a.lower() or "mismatched" in a.lower() for a in acts))

    def test_claude_settings_noise_only_is_empty(self):
        raw = (
            'remote managed settings (permissions.deny): Invalid permission rule "Bash(eval $(wget*))" '
            "was skipped: Mismatched parentheses.\n"
            "Managed settings contain invalid entries (remaining valid policies are still enforced):\n"
        )
        self.assertEqual(jobs.decode_log_text(raw), [])

    def test_claude_result_event(self):
        acts = jobs.decode_log_text(
            json.dumps({"type": "result", "result": "Updated the shared container utility."})
        )
        self.assertEqual(acts, ["Updated the shared container utility."])

    def test_running_job_hides_claude_settings_noise(self):
        import tempfile

        td = tempfile.TemporaryDirectory()
        repo = Path(td.name)
        d = repo / ".rig" / "jobs" / "claude-noise"
        d.mkdir(parents=True)
        (d / "brief.md").write_text("Fix the shared container utility.\n")
        (d / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "claude-noise",
                    "worker": "claude",
                    "role": "implement",
                    "status": "running",
                    "pid": os.getpid(),
                    "model": "claude-sonnet-5",
                    "effort": "medium",
                }
            )
        )
        (d / "stdout.log").write_text(
            'remote managed settings (permissions.deny): Invalid permission rule "Bash(eval $(wget*))" '
            "was skipped: Mismatched parentheses.\n"
        )
        job = jobs.load_job(d)
        self.assertEqual(job["doing"], "running (no log yet)")
        self.assertEqual(job["activities"], [])
        td.cleanup()


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
        self.assertIn("model", shown)
        self.assertIn("reasoning", shown)
        table = jobs.format_table(listing)
        self.assertIn("model", table)
        self.assertIn("reasoning", table)

    def test_native_job_uses_summary_as_task(self):
        d = self.repo / ".rig" / "jobs" / "20260908T071315Z-4994"
        d.mkdir()
        (d / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "20260908T071315Z-4994",
                    "worker": "codex",
                    "role": "worker",
                    "status": "ok",
                    "kind": "native",
                    "summary": "Implemented bounded enrichment deadlines",
                    "model": "gpt-5.6-luna",
                    "effort": "low",
                }
            )
        )
        job = jobs.load_job(d)
        self.assertIn("enrichment deadlines", job["task"])
        self.assertNotEqual(job["task"], "4994")

    def test_ok_job_without_log_is_pruned(self):
        d = self.repo / ".rig" / "jobs" / "done-ok"
        d.mkdir()
        (d / "meta.json").write_text(
            json.dumps({"job_id": "done-ok", "worker": "grok", "role": "implement", "status": "ok"})
        )
        job = jobs.load_job(d)
        self.assertTrue(job["log_pruned"])
        self.assertIn("pruned after success", jobs.format_log(job))

    def test_thread_tag_and_filter(self):
        meta = self.repo / ".rig" / "jobs" / "260908-opencode-session-fix" / "meta.json"
        obj = json.loads(meta.read_text())
        obj["thread"] = "parent-thread-a"
        meta.write_text(json.dumps(obj))
        other = self.repo / ".rig" / "jobs" / "other-thread"
        other.mkdir()
        (other / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "other-thread",
                    "worker": "codex",
                    "role": "worker",
                    "status": "ok",
                    "thread": "parent-thread-b",
                    "summary": "done in another thread",
                }
            )
        )
        all_jobs = jobs.list_jobs(self.repo)
        self.assertEqual({j["job_id"] for j in all_jobs}, {"260908-opencode-session-fix", "other-thread"})
        only_a = jobs.list_jobs(self.repo, thread="parent-thread-a")
        self.assertEqual([j["job_id"] for j in only_a], ["260908-opencode-session-fix"])
        table = jobs.format_table(all_jobs)
        self.assertIn("parent-thread-a", table)
        shown = jobs.format_show(only_a[0])
        self.assertIn("thread  parent-thread-a", shown)

    def test_remember_thread_from_statusline(self):
        saved = {k: os.environ.pop(k, None) for k in jobs.THREAD_ENV}
        try:
            text = jobs.format_statusline(
                {
                    "cwd": str(self.repo),
                    "session_id": "sess-parent-9",
                    "workspace": {"current_dir": str(self.repo), "repo_root": str(self.repo)},
                    "model": {"display_name": "Grok 4.6"},
                }
            )
            self.assertIn("grok", text)
            self.assertEqual(jobs.current_thread(self.repo), "sess-parent-9")
            self.assertEqual((self.repo / ".rig" / "thread").read_text().strip(), "sess-parent-9")
            os.environ["RIG_THREAD"] = "env-wins"
            self.assertEqual(jobs.current_thread(self.repo), "env-wins")
        finally:
            os.environ.pop("RIG_THREAD", None)
            for key, val in saved.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

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
        mem = rig_mcp.call_tool("rig_memory", {"repo": str(repo)})
        self.assertIn("no memory yet", mem["content"][0]["text"])
        added = rig_mcp.call_tool(
            "rig_memory_add", {"repo": str(repo), "fact": "Jobs survive a new parent thread"}
        )
        self.assertEqual(added["content"][0]["text"], "added")
        shown = rig_mcp.call_tool("rig_memory", {"repo": str(repo)})
        self.assertIn("Jobs survive a new parent thread", shown["content"][0]["text"])
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
