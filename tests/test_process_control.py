"""Cancellation terminates stubborn descendants without targeting reused PIDs."""
import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import admission
import process_control


class ProcessControlTests(unittest.TestCase):
    def test_local_lock_deadline_and_reentrancy(self):
        with tempfile.TemporaryDirectory() as directory:
            ready, release = threading.Event(), threading.Event()
            def holder():
                with admission.transaction(directory):
                    ready.set()
                    release.wait(3)
            thread = threading.Thread(target=holder)
            thread.start()
            self.assertTrue(ready.wait(2))
            start = time.monotonic()
            try:
                with self.assertRaises(admission.AdmissionBusy):
                    with admission.transaction(directory, timeout=0.05):
                        self.fail("contended lock acquired")
                self.assertLess(time.monotonic() - start, 0.5)
            finally:
                release.set()
                thread.join(2)
            with admission.transaction(directory):
                with admission.transaction(directory, timeout=0):
                    pass

    def test_file_lock_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / ".rig" / "queue" / ".lock"
            lock.parent.mkdir(parents=True)
            proc = subprocess.Popen([sys.executable, "-c",
                "import fcntl,sys,time; f=open(sys.argv[1],'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(5)",
                str(lock)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                start = time.monotonic()
                with self.assertRaises(admission.AdmissionBusy):
                    with admission.transaction(directory, timeout=0.05):
                        pass
                self.assertLess(time.monotonic() - start, 0.5)
            finally:
                proc.terminate()
                proc.wait(timeout=2)
                proc.stdout.close()

    def test_wrapper_confirms_cancellation(self):
        self._wrapper_cancellation(False)

    def test_wrapper_unknown_stop_never_publishes_terminal_metadata(self):
        self._wrapper_cancellation(True)

    def _wrapper_cancellation(self, unknown):
        import jobs
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            folder = repo / ".rig" / "jobs" / "worker"
            folder.mkdir(parents=True)
            (repo / ".rig" / "harness.toml").write_text('parent = "codex"\n[workers]\ngrok = true\n')
            brief = folder / "brief.md"
            brief.write_text("do the thing\n")
            bins = repo / "bins"
            bins.mkdir()
            worker = bins / "grok"
            worker.write_text("#!/bin/sh\nexec sleep 30\n")
            worker.chmod(0o755)
            env = {**os.environ, "PATH": str(bins) + ":/usr/bin:/bin", "RIG_HOME": str(root),
                   "RIG_PARENT": "codex", "RIG_LIVE": "1", "RIG_TIMEOUT": "20",
                   "RIG_SKIP_MODEL_CATALOG": "1", "RIG_ROLE": "worker"}
            if unknown:
                fixture = repo / "inspection-fixture"
                fixture.mkdir()
                (fixture / "sitecustomize.py").write_text(
                    "import admission\nadmission._external_stopped = lambda record: (False, 'injected inspection unavailable')\n")
                env["PYTHONPATH"] = str(fixture) + os.pathsep + str(root / "scripts")
            proc = subprocess.Popen([str(root / "scripts" / "run-worker.sh"), "grok", "worker", str(brief)],
                                    cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    meta = jobs._read_meta_dict(folder)
                    if meta.get("pid") and (folder / ("launch-" + meta.get("attempt_id", "") + ".json")).exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(meta.get("pid"), proc.poll())
                jobs.cancel_job(repo, "worker")
                out, err = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 130, out + err)
                self.assertTrue((folder / "result.json").exists(), out + err)
                result = json.loads((folder / "result.json").read_text())
                record = admission.get_reservation(repo, meta["reservation_id"])
                self.assertEqual(admission._process_state(record["process"]), "dead")
                if unknown:
                    self.assertEqual(record["execution_status"], "cancelled")
                    self.assertFalse(record["stopped"])
                    self.assertTrue(record["slot_held"])
                    for name in ("meta.json", "result.json"):
                        published = json.loads((folder / name).read_text())
                        self.assertEqual(published["status"], "running")
                        self.assertEqual(published["exit_code"], 0)
                        self.assertEqual(published["ended_at"], "")
                        self.assertNotIn("elapsed_s", published)
                else:
                    self.assertEqual(result["status"], "cancelled")
                    self.assertTrue(record["stopped"])
                    self.assertFalse(record["slot_held"])
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate(timeout=3)

    def test_zombie_is_stopped(self):
        with mock.patch.object(admission.os, "kill"), mock.patch.object(
                admission, "process_identity", return_value={"pid": 123, "start_id": "same", "state": "Z"}):
            self.assertEqual(admission._process_state({"pid": 123, "start_id": "same"}), "dead")

    def test_reused_identity_never_signalled(self):
        with mock.patch.object(admission, "_process_state", return_value="dead"), \
             mock.patch.object(process_control.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "321 1 321\n")), \
             mock.patch.object(process_control.os, "kill") as kill:
            result = process_control.terminate({"pid": 321, "start_id": "old", "pgid": 321}, grace=0)
        kill.assert_not_called()
        self.assertTrue(result["stopped"])

    def test_term_ignoring_descendant_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            code = """
import os, pathlib, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid = os.fork()
if pid == 0:
    pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
while True: time.sleep(0.1)
"""
            proc = subprocess.Popen([sys.executable, "-c", code, str(ready)], start_new_session=True)
            child = None
            try:
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                child = int(ready.read_text())
                identity = admission.process_identity(proc.pid)
                descendant = admission.process_identity(child)
                start = time.monotonic()
                result = process_control.terminate(identity, timeout=3, grace=0.1)
                self.assertLess(time.monotonic() - start, 3.5)
                self.assertTrue(result["stopped"], result)
                self.assertEqual(admission._process_state(descendant), "dead")
                proc.wait(timeout=1)
            finally:
                for pid in [child, proc.pid]:
                    if pid:
                        try: os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                proc.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
