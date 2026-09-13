#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import jobs  # noqa: E402
import rig_mcp  # noqa: E402
import work_queue  # noqa: E402


def _run_rig(repo: Path, *args: str) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["RIG_HOME"] = str(ROOT)
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    merged["RIG_SKIP_UPDATE_CHECK"] = "1"
    merged["RIG_PARENT"] = "codex"
    return subprocess.run(
        [str(ROOT / "bin" / "rig"), *args],
        cwd=repo,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def _mcp_ndjson(messages: list, timeout: float = 5) -> subprocess.CompletedProcess:
    payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    return subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "rig_mcp.py")],
        input=payload,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


class CancelJob(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\n'
            "codex = true\ngrok = true\nclaude = false\ncursor = false\n"
            "opencode = false\nomp = false\npi = false\nagy = false\n"
        )
        self.d = self.repo / ".rig" / "jobs" / "live-job"
        self.d.mkdir(parents=True)
        (self.d / "brief.md").write_text("do the thing\n")
        (self.d / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "live-job",
                    "worker": "grok",
                    "role": "worker",
                    "status": "running",
                    "started_at": "2026-09-12T00:00:00Z",
                }
            )
            + "\n"
        )

    def tearDown(self):
        self.td.cleanup()

    def test_cancel_stamps_cancelled_and_wait_is_130(self):
        text = jobs.cancel_job(self.repo, "live-job")
        self.assertTrue(text.startswith("cancelled"), text)
        self.assertTrue((self.d / "cancel.json").is_file())
        job = jobs.load_job(self.d)
        self.assertEqual(job["effective"], "cancelled")
        self.assertEqual(job["status"], "cancelled")
        code, wait_text = jobs.wait_job(self.repo, "live-job")
        self.assertEqual(code, 130)
        self.assertIn("CANCELLED live-job", wait_text)
        self.assertIn("Do not re-pick", wait_text)

    def test_cancel_ok_refuses(self):
        jobs.write_job_files(
            self.d,
            "live-job",
            "grok",
            "worker",
            "ok",
            0,
            "2026-09-12T00:00:00Z",
            "2026-09-12T00:01:00Z",
            "done",
        )
        text = jobs.cancel_job(self.repo, "live-job")
        self.assertIn("already ok", text)
        self.assertEqual(jobs.load_job(self.d)["effective"], "ok")

    def test_cancel_already_is_ok(self):
        jobs.cancel_job(self.repo, "live-job")
        text = jobs.cancel_job(self.repo, "live-job")
        self.assertIn("already", text)
        self.assertTrue(text.startswith("cancelled"))

    def test_cancel_does_not_touch_queue(self):
        obj = work_queue.add_item(self.repo, "later fix sidebar")
        jobs.cancel_job(self.repo, "live-job")
        pending = work_queue.list_items(self.repo, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], obj["id"])

    def test_bin_job_cancel(self):
        proc = _run_rig(self.repo, "job", "cancel", "live-job")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("cancelled live-job", proc.stdout)
        self.assertEqual(jobs.load_job(self.d)["effective"], "cancelled")

    def test_mcp_cancel_tool(self):
        out = rig_mcp.call_tool(
            "rig_job_cancel", {"repo": str(self.repo), "id": "live-job"}
        )
        self.assertNotIn("isError", out)
        self.assertIn("cancelled live-job", out["content"][0]["text"])
        self.assertEqual(jobs.load_job(self.d)["effective"], "cancelled")

    def test_mcp_wait_cancelled_notification_aborts(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        def stop_sleeper():
            if sleeper.poll() is None:
                sleeper.kill()
            sleeper.wait(timeout=5)
        self.addCleanup(stop_sleeper)
        meta = json.loads((self.d / "meta.json").read_text())
        meta["pid"] = sleeper.pid
        (self.d / "meta.json").write_text(json.dumps(meta) + "\n")
        proc = _mcp_ndjson(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "rig_job_wait",
                        "arguments": {"repo": str(self.repo), "id": "live-job"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 10},
                },
            ],
            timeout=6,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        wait = next(m for m in lines if m.get("id") == 10)
        text = wait["result"]["content"][0]["text"]
        self.assertNotIn("isError", wait["result"])
        self.assertIn("CANCELLED", text)
        self.assertEqual(jobs.load_job(self.d)["effective"], "cancelled")

    def test_wrapper_honors_cancel_json(self):
        # This test launches a fresh attempt; pre-existing live metadata is no
        # longer authorization to reuse a job ID.
        (self.d / "meta.json").unlink()
        bins = self.repo / "bins"
        bins.mkdir()
        grok = bins / "grok"
        grok.write_text("#!/bin/sh\nexec sleep 30\n")
        grok.chmod(0o755)
        brief = self.d / "brief.md"
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bins}:/usr/bin:/bin",
                "RIG_HOME": str(ROOT),
                "RIG_PARENT": "codex",
                "RIG_LIVE": "1",
                "RIG_TIMEOUT": "20",
                "RIG_SKIP_MODEL_CATALOG": "1",
                "RIG_ROLE": "worker",
            }
        )
        proc_holder = {}

        def run() -> None:
            proc_holder["p"] = subprocess.run(
                [
                    str(ROOT / "scripts" / "run-worker.sh"),
                    "grok",
                    "live-job",
                    str(brief),
                ],
                cwd=self.repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        t = threading.Thread(target=run)
        t.start()
        deadline = time.time() + 8
        pid = None
        while time.time() < deadline:
            meta = jobs._read_meta_dict(self.d)
            pid = meta.get("pid")
            if pid and jobs.pid_alive(pid):
                break
            time.sleep(0.05)
        self.assertTrue(pid and jobs.pid_alive(pid), "wrapper never stamped a live pid")
        text = jobs.cancel_job(self.repo, "live-job")
        self.assertTrue(text.startswith("cancelled"), text)
        t.join(timeout=8)
        self.assertFalse(t.is_alive(), "wrapper did not exit after cancel")
        wrapped = proc_holder["p"]
        self.assertEqual(wrapped.returncode, 130, wrapped.stdout + wrapped.stderr)
        result = json.loads((self.d / "result.json").read_text())
        self.assertEqual(result["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
