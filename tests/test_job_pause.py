#!/usr/bin/env python3
"""Pause and resume a running helper: SIGSTOP and SIGCONT to its process group, a mark the board shows."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import job_pause  # noqa: E402
import jobs  # noqa: E402
import local_api  # noqa: E402
import tui_rows  # noqa: E402


def _state(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0]
    except OSError:
        return "?"


class Pause(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        self.folder = self.repo / ".rig" / "jobs" / "j1"
        self.folder.mkdir(parents=True)
        self.proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        (self.folder / "meta.json").write_text(json.dumps({"job_id": "j1", "worker": "codex", "task": "Sleep", "status": "running",
                                                           "pid": self.proc.pid, "started_at": "2026-10-21T07:00:00Z"}))

    def tearDown(self):
        try:
            os.killpg(self.proc.pid, 9)
        except OSError:
            pass
        self.proc.wait()
        self.td.cleanup()

    @unittest.skipUnless(Path("/proc").is_dir(), "needs /proc to read the process state")
    def test_pause_stops_the_worker_and_resume_lets_it_go_on(self):
        self.assertEqual(job_pause.pause(self.repo, "j1"), {"job_id": "j1", "paused": True})
        time.sleep(0.1)
        self.assertEqual(_state(self.proc.pid), "T", "stopped")
        job = jobs.resolve_job(self.repo, "j1")
        self.assertTrue(job["paused"])
        self.assertEqual(tui_rows.job_state(job)[0], "Paused")
        self.assertEqual(job_pause.pause(self.repo, "j1")["already"], True)
        self.assertEqual(job_pause.resume(self.repo, "j1"), {"job_id": "j1", "paused": False})
        time.sleep(0.1)
        self.assertEqual(_state(self.proc.pid), "S", "sleeping again")
        self.assertFalse(jobs.resolve_job(self.repo, "j1")["paused"])

    def test_a_stop_wakes_a_paused_worker_first(self):
        job_pause.pause(self.repo, "j1")
        job_pause.resume_before_stop(self.folder)
        time.sleep(0.1)
        self.assertFalse(job_pause.is_paused(self.folder))
        if Path("/proc").is_dir():
            self.assertEqual(_state(self.proc.pid), "S")

    def test_over_the_api(self):
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda params: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": "job.pause", "params": params, "token": "t" * 64})
        self.assertTrue(call({"id": "j1"})["result"]["paused"])
        self.assertFalse(call({"id": "j1", "on": False})["result"]["paused"])
        self.assertEqual(call({"id": "j1", "on": "yes"})["error"]["code"], local_api.INVALID_PARAMS)
        self.assertIn("error", call({"id": "nope"}))


if __name__ == "__main__":
    unittest.main()
