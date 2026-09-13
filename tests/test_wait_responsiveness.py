"""Real stdio transport regressions: keep stdin open while observing responses."""
import fcntl
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cancellation


class Transport(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.folder = self.repo / ".rig" / "jobs" / "working"
        self.folder.mkdir(parents=True)
        (self.folder / "meta.json").write_text(json.dumps({
            "job_id": "working", "worker": "grok", "status": "running",
            "pid": os.getpid(), "model": "known", "model_source": "selected"}))
        self.proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "scripts/rig_mcp.py")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.close)
        self.messages = queue.Queue()
        threading.Thread(target=self.read, daemon=True).start()
        self.send({"id": 1, "method": "initialize"})
        self.response(1)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=3)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()

    def read(self):
        for line in self.proc.stdout:
            self.messages.put(json.loads(line))

    def send(self, message):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.proc.stdin.flush()

    def response(self, rid, timeout=2):
        deadline = time.monotonic() + timeout
        while True:
            value = self.messages.get(timeout=max(0.001, deadline - time.monotonic()))
            if type(value.get("id")) is type(rid) and value.get("id") == rid:
                return value
            if time.monotonic() >= deadline:
                self.fail(f"no response for {rid!r}")

    def wait(self, rid=10):
        self.send({"id": rid, "method": "tools/call", "params": {"name": "rig_job_wait",
                   "arguments": {"repo": str(self.repo), "id": "working"}}})

    def test_cancel_does_not_block_ping_behind_admission(self):
        self.wait()
        self.send({"id": 2, "method": "ping"})
        self.response(2)
        folder = self.repo / ".rig" / "queue"
        folder.mkdir()
        with (folder / ".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.send({"method": "notifications/cancelled", "params": {"requestId": 10}})
            start = time.monotonic()
            self.send({"id": 3, "method": "ping"})
            self.response(3, timeout=0.25)
            self.assertLess(time.monotonic() - start, 0.25)
            deadline = time.monotonic() + 1
            while not cancellation.requested(self.folder) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(cancellation.requested(self.folder))
        self.assertEqual(json.loads((self.folder / "meta.json").read_text())["status"], "running")
        time.sleep(0.1)
        self.assertFalse(any(item.get("id") == 10 for item in list(self.messages.queue)))

    def test_eof_does_not_join_indefinite_wait(self):
        self.wait()
        self.send({"id": 2, "method": "ping"})
        self.response(2)
        start = time.monotonic()
        self.proc.stdin.close()
        self.proc.wait(timeout=1.5)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertFalse(cancellation.requested(self.folder))


class MarkerIdentity(unittest.TestCase):
    def test_interrupt_during_observation_also_persists_stop(self):
        import jobs

        for stage in ("load", "progress"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary) / ".rig/jobs/j"
                folder.mkdir(parents=True)
                (folder / "meta.json").write_text(json.dumps({"job_id": "j", "status": "running",
                    "pid": os.getpid(), "worker": "grok", "model": "known"}))
                def interrupt(*_args, **_kwargs):
                    raise KeyboardInterrupt()
                context = mock.patch.object(jobs, "_load_selected_job", side_effect=interrupt) if stage == "load" else mock.patch.object(jobs, "pid_alive", return_value=True)
                with context:
                    code, text = jobs.wait_job(Path(temporary), "j", on_tick=interrupt if stage == "progress" else None)
                self.assertEqual(code, 130)
                self.assertIn("CANCELLATION REQUESTED", text)
                self.assertTrue(cancellation.requested(folder))

    def test_cancelled_attempt_cannot_affect_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            meta = {"job_id": "j", "attempt_id": "a", "reservation_id": "r", "status": "running"}
            (folder / "meta.json").write_text(json.dumps(meta))
            target = cancellation.capture(folder)
            cancellation.publish(target)
            self.assertTrue(cancellation.requested(folder, "a", "r"))
            meta["attempt_id"] = "b"
            (folder / "meta.json").write_text(json.dumps(meta))
            self.assertFalse(cancellation.requested(folder))
            with self.assertRaises(ValueError):
                cancellation.publish(target)

    def test_completed_result_is_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "meta.json").write_text(json.dumps({"job_id": "j", "status": "ok"}))
            result = cancellation.publish(cancellation.capture(folder))
            self.assertEqual(result["state"], "already-terminal")
            self.assertFalse(cancellation.requested(folder))


if __name__ == "__main__":
    unittest.main()
