#!/usr/bin/env python3
import contextlib
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cancellation
from mcp_runtime import Request

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


class CancelJob(unittest.TestCase):
    def setUp(self):
        # Parent MCP/cancel paths must not inherit a nested worker's job identity.
        self.env = mock.patch.dict(os.environ, {"RIG_JOB_ID": "", "RIG_JOB_DIR": "", "RIG_OWNER_TOKEN": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
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

    def test_cancel_records_intent_without_claiming_termination_and_wait_is_130(self):
        text = jobs.cancel_job(self.repo, "live-job")
        self.assertTrue(text.startswith("cancellation requested"), text)
        self.assertTrue((self.d / "cancel.json").is_file())
        job = jobs.load_job(self.d)
        self.assertEqual(job["effective"], "cancel_requested")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["cancellation_state"], "stop-unconfirmed")
        code, wait_text = jobs.wait_job(self.repo, "live-job")
        self.assertEqual(code, 130)
        self.assertIn("CANCELLATION REQUESTED live-job", wait_text)
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

    def test_repeated_cancel_preserves_first_intent_without_claiming_stop(self):
        jobs.cancel_job(self.repo, "live-job")
        first_marker = (self.d / "cancel.json").read_bytes()
        text = jobs.cancel_job(self.repo, "live-job")
        self.assertTrue(text.startswith("cancellation requested"), text)
        self.assertEqual(jobs.load_job(self.d)["status"], "running")
        self.assertEqual((self.d / "cancel.json").read_bytes(), first_marker)

    def test_cancel_does_not_touch_queue(self):
        obj = work_queue.add_item(self.repo, "later fix sidebar")
        jobs.cancel_job(self.repo, "live-job")
        pending = work_queue.list_items(self.repo, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], obj["id"])

    def test_bin_job_cancel(self):
        proc = _run_rig(self.repo, "job", "cancel", "live-job")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("cancellation requested live-job", proc.stdout)
        self.assertEqual(jobs.load_job(self.d)["effective"], "cancel_requested")

    def test_mcp_cancel_tool(self):
        out = rig_mcp.call_tool(
            "rig_job_cancel", {"repo": str(self.repo), "id": "live-job"}
        )
        self.assertNotIn("isError", out)
        self.assertIn("cancellation requested live-job", out["content"][0]["text"])
        self.assertEqual(jobs.load_job(self.d)["effective"], "cancel_requested")

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
        proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "scripts/rig_mcp.py")],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        def close_transport():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        self.addCleanup(close_transport)
        received = queue.Queue()
        def read():
            for line in proc.stdout:
                received.put(json.loads(line))
        threading.Thread(target=read, daemon=True).start()
        def send(message):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            proc.stdin.flush()
        send({"id": 10, "method": "tools/call", "params": {"name": "rig_job_wait",
              "arguments": {"repo": str(self.repo), "id": "live-job"}}})
        send({"id": 2, "method": "ping"})
        self.assertEqual(received.get(timeout=3)["id"], 2)
        send({"method": "notifications/cancelled", "params": {"requestId": 10}})
        send({"id": 3, "method": "ping"})
        self.assertEqual(received.get(timeout=3)["id"], 3)
        deadline = time.monotonic() + 3
        while not (self.d / "cancel.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.d / "cancel.json").exists())
        # Cancellation suppresses the original JSON-RPC response, while the
        # independent executor still needs authenticated stop confirmation.
        with self.assertRaises(queue.Empty):
            received.get(timeout=0.5)
        self.assertEqual(jobs.load_job(self.d)["effective"], "cancel_requested")
        self.assertEqual(jobs.load_job(self.d)["status"], "running")
        self.assertIsNone(sleeper.poll(), "legacy cancellation must not kill an unproven PID")

    def test_one_missing_target_does_not_skip_remaining_attached_workers(self):
        missing = cancellation.capture(self.d)
        remaining = self.d.parent / "remaining"
        remaining.mkdir()
        (remaining / "meta.json").write_text(json.dumps({
            "job_id": "remaining", "status": "running", "worker": "grok"}))
        attached = cancellation.capture(remaining)
        untouched = self.d.parent / "unattached"
        untouched.mkdir()
        (untouched / "meta.json").write_text(json.dumps({
            "job_id": "unattached", "status": "running", "worker": "grok"}))
        (self.d / "meta.json").unlink()
        request = Request(30, "rig_job_wait", {}, {}, targets=[missing, attached])
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            rig_mcp._abort_request(request)
        self.assertIn("live-job", errors.getvalue())
        self.assertTrue(cancellation.requested(remaining))
        self.assertFalse(cancellation.requested(untouched))
        self.assertEqual(json.loads((remaining / "meta.json").read_text())["status"], "running")

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
        keep = {
            "RIG_HOME", "RIG_PARENT", "RIG_LIVE", "RIG_TIMEOUT",
            "RIG_SKIP_MODEL_CATALOG", "RIG_ROLE", "RIG_SKIP_UPDATE_CHECK",
        }
        for key in list(env):
            if key.startswith("RIG_") and key not in keep:
                env.pop(key, None)
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
        self.assertTrue(text.startswith("cancellation requested"), text)
        t.join(timeout=8)
        self.assertFalse(t.is_alive(), "wrapper did not exit after cancel")
        wrapped = proc_holder["p"]
        self.assertEqual(wrapped.returncode, 130, wrapped.stdout + wrapped.stderr)
        self.assertTrue((self.d / "result.json").is_file(),
                        "wrapper exited without confirmed result: " + wrapped.stdout + wrapped.stderr)
        result = json.loads((self.d / "result.json").read_text())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(jobs.load_job(self.d)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
