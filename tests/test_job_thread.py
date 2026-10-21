#!/usr/bin/env python3
"""A helper's thread with the parent, for the shell."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import inbox  # noqa: E402
import job_thread  # noqa: E402
import jobs  # noqa: E402
import local_api  # noqa: E402


class Thread(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        self.folder = self.repo / ".rig" / "jobs" / "j1"
        self.folder.mkdir(parents=True)
        (self.folder / "brief.md").write_text("# Rate limit login\n\nAdd a limiter to POST /login.\n")
        jobs.write_activity(self.folder, ["read src/routes/login.ts", "edit src/routes/login.ts"], job_id="j1")

    def tearDown(self):
        self.td.cleanup()

    def meta(self, **extra):
        (self.folder / "meta.json").write_text(json.dumps({"job_id": "j1", "worker": "codex", "task": "Rate limit login",
                                                           "started_at": "2026-10-21T07:00:00Z", **extra}))

    def test_a_finished_helper_reads_as_a_conversation(self):
        self.meta(status="ok", exit_code=0, summary="Added a sliding-window limiter; tests pass.", ended_at="2026-10-21T07:05:00Z")
        shown = job_thread.thread(self.repo, "j1")
        self.assertEqual((shown["parent"], shown["worker"], shown["running"], shown["ending"]), ("claude", "codex", False, "finished"))
        self.assertEqual([(e["who"], e["kind"]) for e in shown["entries"]],
                         [("claude", "brief"), ("codex", "activity"), ("codex", "activity"), ("codex", "result")])
        self.assertIn("Add a limiter to POST /login.", shown["entries"][0]["text"])
        self.assertEqual(shown["entries"][-1]["text"], "Added a sliding-window limiter; tests pass.")

    def test_a_running_helper_with_a_message_waiting(self):
        self.meta(status="running")
        inbox.write_inbox(self.folder, "use 429 with Retry-After")
        shown = job_thread.thread(self.repo, "j1")
        self.assertIn(("claude", "message", "use 429 with Retry-After"), [(e["who"], e["kind"], e["text"]) for e in shown["entries"]])
        self.assertNotIn("result", [e["kind"] for e in shown["entries"]])

    def test_over_the_api(self):
        self.meta(status="fail", exit_code=2)
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda params: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": "job.thread", "params": params, "token": "t" * 64})
        self.assertEqual(call({"id": "j1"})["result"]["ending"], "failed (exit 2)")
        self.assertIn("error", call({"id": "../j1"}))
        self.assertEqual(call({})["error"]["code"], local_api.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
