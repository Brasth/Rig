#!/usr/bin/env python3
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

import board_view  # noqa: E402
import local_api  # noqa: E402
import tui_rows  # noqa: E402

JOBS = [
    {"job_id": "job-81c", "worker": "codex", "effective": "running", "display_state": "working",
     "task": "Rate limit /login", "doing": "editing limit.ts"},
    {"job_id": "job-7f2", "worker": "claude", "effective": "ask", "display_state": "needs-input",
     "task": "Add supertest dev dep", "ask": {"preview": "npm i -D supertest"}},
    {"job_id": "job-5aa", "worker": "pi", "effective": "fail", "display_state": "failed",
     "task": "Bump eslint", "display_reason": "check npm run lint"},
]
PENDING = [{"id": "q-1", "text": "Add /logout audit", "priority": 2, "waiting_reason": "Awaiting parent claim"}]
FLOWS = [{"workflow_id": "wf-12", "title": "Auth and rate limit", "status": "attention", "accepted": 1, "required": 3}]


class BuildView(unittest.TestCase):
    def setUp(self):
        self.view = board_view.build(JOBS, PENDING, FLOWS, 2, 3, "/x/my-repo")

    def test_header_and_tabs_use_the_curses_board_counts(self):
        self.assertEqual(self.view["header"], {"project": "my-repo", "attention": 1, "running": 1, "slots": 2, "cap": 3})
        self.assertEqual({t["name"]: (t["count"], t["attention"]) for t in self.view["tabs"]},
                         {"Jobs": (3, 1), "Queue": (1, 0), "Workflows": (1, 1)})

    def test_jobs_are_attention_first_with_curses_labels(self):
        self.assertEqual([r["id"] for r in self.view["jobs"]], ["job-7f2", "job-81c", "job-5aa"])
        first = self.view["jobs"][0]
        self.assertEqual((first["label"], first["tone"], first["glyph"], first["ask"]), ("Needs input", "attention", "!", True))
        self.assertEqual(first["second"], "permission: npm i -D supertest")
        for row in self.view["jobs"]:
            self.assertEqual(row["label"], tui_rows.job_state(next(j for j in JOBS if j["job_id"] == row["id"]))[0])

    def test_queue_and_workflow_rows(self):
        queue = self.view["queue"][0]
        self.assertEqual((queue["label"], queue["summary"]), ("Queued", "p2"))
        self.assertIn("waiting: Awaiting parent claim", queue["second"])
        flow = self.view["workflows"][0]
        self.assertEqual((flow["label"], flow["summary"], flow["attention"]), ("Needs input", "accepted 1/3", True))
        self.assertTrue(any("accepted/required 1/3" in line for line in flow["detail"]))

    def test_every_row_carries_detail_and_nothing_unserialisable(self):
        for key in ("jobs", "queue", "workflows"):
            for row in self.view[key]:
                self.assertTrue(row["detail"])
        json.dumps(self.view)

    def test_empty_inputs(self):
        view = board_view.build(None, None, None, 0, 3)
        self.assertEqual((view["jobs"], view["queue"], view["workflows"]), ([], [], []))
        self.assertEqual(view["header"]["project"], "")


class ServedView(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n\n[workers]\ngrok = true\n")
        self.env = {k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}}
        self.script = str(ROOT / "scripts" / "local_api.py")

    def tearDown(self):
        for pid in self.procs():
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        self.td.cleanup()

    def procs(self):
        return [int(p) for p in subprocess.run(["pgrep", "-f", f"local_api.py serve --repo {self.repo}"],
                                               capture_output=True, text=True).stdout.split()]

    def test_ensure_starts_a_server_and_snapshot_carries_the_view(self):
        done = subprocess.run([sys.executable, self.script, "ensure", "--repo", str(self.repo), "--idle", "3"],
                              env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(done.stdout.strip(), ("started", "running"))
        again = subprocess.run([sys.executable, self.script, "ensure", "--repo", str(self.repo), "--idle", "3"],
                               env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(again.stdout.strip(), "running")
        reply = local_api.call(self.repo, "board.snapshot", {"rows": 5})
        view = reply["result"]["view"]
        self.assertEqual(view["version"], board_view.VIEW_VERSION)
        self.assertEqual(view["header"]["project"], self.repo.name)
        self.assertEqual([t["name"] for t in view["tabs"]], ["Jobs", "Queue", "Workflows"])


class GoShellFixture(unittest.TestCase):
    """shell/testdata/snapshot.json is what the Go shell renders; keep it equal to
    what this server code produces. Refresh with RIG_UPDATE_FIXTURES=1."""

    def test_snapshot_fixture_matches_the_view_builder(self):
        data = ROOT / "shell" / "testdata"
        sample = json.loads((data / "sample-board.json").read_text())
        view = board_view.build(sample["jobs"], sample["pending"], sample["workflows"],
                                sample["slots"], sample["cap"], sample["repo"])
        fresh = {"view": view, "captured_wall": 1760000000.0}
        path = data / "snapshot.json"
        if os.environ.get("RIG_UPDATE_FIXTURES") == "1":
            path.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n")
        self.assertEqual(json.loads(path.read_text()), fresh,
                         "run: RIG_UPDATE_FIXTURES=1 python3 tests/test_board_view.py GoShellFixture")


if __name__ == "__main__":
    unittest.main()
