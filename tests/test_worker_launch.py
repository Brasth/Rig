#!/usr/bin/env python3
"""Atomic wrapper launch handoff: admit, write, detach, never clobber a finished child."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import admission  # noqa: E402
import child_mcp  # noqa: E402
import jobs  # noqa: E402
import mcp_test_support  # noqa: E402
import rig_mcp  # noqa: E402
import worker_launch  # noqa: E402
import work_queue  # noqa: E402

SLEEP_WRAPPER = """#!/usr/bin/env python3
import json, os, time
from pathlib import Path
job = Path(os.environ["RIG_JOB_DIR"])
(job / "wrapper-env.json").write_text(json.dumps(dict(os.environ)))
(job / "wrapper-alive").write_text(str(os.getpid()))
time.sleep(20)
"""

RACE_WRAPPER = """#!/usr/bin/env python3
import json, os, time
from pathlib import Path
job = Path(os.environ["RIG_JOB_DIR"])
meta_path = job / "meta.json"
for _ in range(400):
    if meta_path.is_file():
        break
    time.sleep(0.01)
obj = json.loads(meta_path.read_text())
obj["status"] = "ok"
obj["pid"] = 7
obj["exit_code"] = 0
meta_path.write_text(json.dumps(obj, indent=2) + "\\n")
(job / "result.json").write_text(json.dumps(obj, indent=2) + "\\n")
time.sleep(15)
"""


class WorkerLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.rig_home = self.root / "rig-home"
        self.bins = self.root / "bins"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / "a.py").write_text("a\n")
        (self.repo / "b.py").write_text("b\n")
        (self.repo / ".rig").mkdir()
        self.home.mkdir()
        (self.rig_home / "scripts").mkdir(parents=True)
        mcp_test_support.fake_bin(self.bins, "grok")
        mcp_test_support.fake_bin(self.bins, "claude")
        mcp_test_support.fake_bin(self.bins, "opencode")
        mcp_test_support.seed_installed_mcp(self.home)
        self._write_wrapper(SLEEP_WRAPPER)
        self.configure()
        self.pids = []
        self._env = mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "PATH": mcp_test_support.stub_path(self.bins),
            "RIG_HOME": str(self.rig_home),
            "RIG_PARENT": "codex",
            "RIG_OWNER_SESSION": "launch-tests",
            "RIG_SKIP_MODEL_CATALOG": "1",
        }, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)
        os.environ.pop("OPENCODE_CONFIG", None)

    def tearDown(self):
        for pid in self.pids:
            self._stop(pid)

    def configure(self, cap=3):
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\nclaude = true\ncodex = true\n'
            "opencode = true\nomp = true\npi = true\nagy = true\ncursor = false\n"
            f"[queue]\nmax_running = {cap}\nmax_per_worker = 0\n"
        )

    def _write_wrapper(self, source: str) -> Path:
        path = self.rig_home / "scripts" / "run-worker.sh"
        path.write_text(source)
        path.chmod(0o755)
        return path

    def _stop(self, pid: int) -> None:
        if not pid:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                return
        for _ in range(20):
            try:
                os.waitpid(pid, os.WNOHANG)
            except (OSError, ChildProcessError):
                pass
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                return
            time.sleep(0.05)

    def _launch(self, job="job-a", files=None, **kwargs):
        result = worker_launch.launch(
            self.repo,
            id=job,
            brief=kwargs.pop("brief", "do the listed files"),
            worker=kwargs.pop("worker", "grok"),
            role=kwargs.pop("role", "implement"),
            model=kwargs.pop("model", "grok-4.6"),
            effort=kwargs.pop("effort", "high"),
            files=["a.py"] if files is None else files,
            owner_session=kwargs.pop("owner_session", "launch-tests"),
            **kwargs,
        )
        self.pids.append(result["wrapper_pid"])
        return result

    def _held(self):
        return [row for row in admission.list_reservations(self.repo) if row.get("slot_held")]

    def test_unknown_and_typed_args_rejected(self):
        with self.assertRaisesRegex(worker_launch.LaunchError, "unknown launch argument"):
            worker_launch.launch(self.repo, brief="x", command="/bin/true")
        with self.assertRaisesRegex(worker_launch.LaunchError, "unknown launch argument"):
            worker_launch.launch(self.repo, brief="x", env={"FOO": "1"})
        with self.assertRaisesRegex(worker_launch.LaunchError, "unknown launch argument"):
            worker_launch.launch(self.repo, brief="x", executable="/bin/sh")
        with self.assertRaisesRegex(worker_launch.LaunchError, "files must be a JSON array"):
            worker_launch.launch(self.repo, brief="x", files="a.py")
        with self.assertRaisesRegex(worker_launch.LaunchError, "brief must be a string"):
            worker_launch.launch(self.repo, brief=1)
        with self.assertRaisesRegex(worker_launch.LaunchError, "live must be a string"):
            worker_launch.launch(self.repo, brief="x", live=1)

    def test_auto_and_explicit_worker_selection_keep_smart_sidecar(self):
        for worker in ("", "grok"):
            with self.subTest(worker=worker):
                result = self._launch("auto-" + (worker or "all"), files=["a.py" if worker else "b.py"], worker=worker,
                                      model="", effort="", assessment={
                                          "complexity": "low", "risk": "low", "uncertainty": "low"})
                folder = self.repo / ".rig" / "jobs" / result["job_id"]
                evidence = json.loads((folder / "routing.json").read_text())
                self.assertEqual(evidence["attempt_id"], result["attempt_id"])
                routing = evidence["routing"]
                self.assertEqual(routing["policy_mode"], "smart")
                self.assertEqual(routing["required_tier"], "fast")
                self.assertEqual(routing["selected_profile"]["model"], "grok-4.5")
                self.assertEqual(routing["selected_profile"]["effort"], "low")

    def test_invalid_routing_does_not_consume_queue_claim(self):
        import route
        item = work_queue.add_item(self.repo, "scoped work")
        claim = work_queue.claim_next(self.repo, item_id=item["id"], worker="grok",
                                      access="write", files=["a.py"], owner_session="launch-tests")
        choice = route.pick("codex", ["grok"], "implement", "scoped work", repo=self.repo)
        routing = dict(choice["routing"], config_fingerprint="stale")
        with self.assertRaisesRegex(worker_launch.LaunchError, "fingerprint"):
            self._launch("bad-routing", routing=routing, queue_id=item["id"],
                         reservation_id=claim["reservation_id"], attempt_id=claim["attempt_id"],
                         owner_token=claim["owner_token"])
        held = admission.get_reservation(self.repo, claim["reservation_id"])
        self.assertFalse(held.get("claim_consumed"))
        self.assertFalse((self.repo / ".rig" / "jobs" / "bad-routing").exists())

    def test_parent_only_schema_and_dispatch_reject_injection(self):
        tool = next(item for item in rig_mcp.TOOLS if item["name"] == "rig_job_launch")
        schema = tool["inputSchema"]
        self.assertFalse(schema.get("additionalProperties", True))
        self.assertIn("brief", schema["required"])
        self.assertNotIn("command", schema["properties"])
        self.assertNotIn("env", schema["properties"])
        self.assertNotIn("executable", schema["properties"])
        self.assertIn("rig_job_launch", rig_mcp.PARENT_TOOL_NAMES)
        self.assertNotIn("rig_job_launch", rig_mcp.CHILD_TOOL_NAMES)
        blocked = rig_mcp.call_tool("rig_job_launch", {
            "repo": str(self.repo), "brief": "x", "command": "/bin/true",
        })
        self.assertTrue(blocked.get("isError"))
        self.assertIn("unknown launch argument", blocked["content"][0]["text"])
        typed = rig_mcp.call_tool("rig_job_launch", {
            "repo": str(self.repo), "brief": "x", "files": "a.py",
        })
        self.assertTrue(typed.get("isError"))
        job_dir = self.repo / ".rig" / "jobs" / "child-one"
        job_dir.mkdir(parents=True)
        with mock.patch.dict(os.environ, {
            "RIG_JOB_ID": "child-one",
            "RIG_JOB_DIR": str(job_dir),
            "RIG_REPO": str(self.repo),
        }):
            child = rig_mcp.call_tool("rig_job_launch", {"brief": "x", "repo": str(self.repo)})
        self.assertTrue(child.get("isError"))
        self.assertIn("not a child tool", child["content"][0]["text"])

    def test_detached_wrapper_returns_immediately_and_survives_caller_exit(self):
        started = time.monotonic()
        result = self._launch("detach-live")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["wrapper_pid"])
        os.kill(result["wrapper_pid"], 0)
        marker = self.repo / ".rig" / "jobs" / "detach-live" / "wrapper-alive"
        for _ in range(50):
            if marker.is_file():
                break
            time.sleep(0.05)
        self.assertTrue(marker.is_file())
        code = (
            "import json,os,sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import worker_launch\n"
            f"os.environ['HOME'] = {str(self.home)!r}\n"
            f"os.environ['PATH'] = {mcp_test_support.stub_path(self.bins)!r}\n"
            f"os.environ['RIG_HOME'] = {str(self.rig_home)!r}\n"
            "os.environ['RIG_PARENT'] = 'codex'\n"
            "os.environ['RIG_OWNER_SESSION'] = 'caller-exit'\n"
            "os.environ['RIG_SKIP_MODEL_CATALOG'] = '1'\n"
            "result = worker_launch.launch("
            f"{str(self.repo)!r}, id='caller-exit', brief='go', worker='grok', "
            "role='implement', model='grok-4.6', effort='high', files=['b.py'], "
            "owner_session='caller-exit')\n"
            "print(result['wrapper_pid'])\n"
        )
        caller = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            timeout=8,
            env=os.environ.copy(),
        )
        self.assertEqual(caller.returncode, 0, caller.stderr)
        pid = int(caller.stdout.strip().splitlines()[-1])
        self.pids.append(pid)
        time.sleep(0.3)
        os.kill(pid, 0)
        meta = json.loads((self.repo / ".rig" / "jobs" / "caller-exit" / "meta.json").read_text())
        self.assertIn(meta.get("status"), {"running", "ok"})

    def test_fast_child_status_is_not_overwritten(self):
        self._write_wrapper(RACE_WRAPPER)

        def racing_popen(argv, *, cwd, env, log):
            job_dir = Path(env["RIG_JOB_DIR"])
            meta_path = job_dir / "meta.json"
            obj = json.loads(meta_path.read_text())
            obj["status"] = "ok"
            obj["pid"] = 4242
            obj["exit_code"] = 0
            meta_path.write_text(json.dumps(obj, indent=2) + "\n")
            (job_dir / "result.json").write_text(json.dumps(obj, indent=2) + "\n")
            return subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(12)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )

        with mock.patch.object(worker_launch, "_spawn_wrapper", side_effect=racing_popen):
            result = self._launch("race-ok")
        self.assertEqual(result["status"], "ok")
        meta = json.loads((self.repo / ".rig" / "jobs" / "race-ok" / "meta.json").read_text())
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(meta["pid"], 4242)
        self.assertEqual(json.loads((self.repo / ".rig" / "jobs" / "race-ok" / "result.json").read_text())["status"], "ok")

        self._write_wrapper(RACE_WRAPPER)
        real = self._launch("race-real", files=["b.py"])
        folder = self.repo / ".rig" / "jobs" / "race-real"
        deadline = time.monotonic() + 6
        meta = {}
        while time.monotonic() < deadline:
            meta = json.loads((folder / "meta.json").read_text())
            if meta.get("status") == "ok":
                break
            time.sleep(0.02)
        time.sleep(0.15)
        meta = json.loads((folder / "meta.json").read_text())
        self.assertEqual(meta["status"], "ok")
        self.assertIn(meta.get("pid"), {7, real["wrapper_pid"]})
        os.kill(real["wrapper_pid"], 0)

    def test_popen_failure_releases_reservation(self):
        with mock.patch.object(worker_launch, "_spawn_wrapper", side_effect=OSError("boom")):
            with self.assertRaisesRegex(worker_launch.LaunchError, "could not start wrapper"):
                worker_launch.launch(
                    self.repo, id="fail-pop", brief="go", worker="grok", role="implement",
                    model="grok-4.6", effort="high", files=["a.py"], owner_session="launch-tests",
                )
        self.assertEqual(self._held(), [])
        folder = self.repo / ".rig" / "jobs" / "fail-pop"
        self.assertIn("could not start wrapper", (folder / "launcher.log").read_text())
        meta = json.loads((folder / "meta.json").read_text())
        self.assertEqual(meta["status"], "fail")
        self.assertEqual(json.loads((folder / "result.json").read_text())["status"], "fail")
        released = admission.list_reservations(self.repo, include_released=True)
        self.assertTrue(released)
        self.assertEqual(released[0]["stage"], "released")
        self.assertFalse(released[0].get("slot_held"))

    def test_duplicate_conflict_capacity_and_stale_token_rejected(self):
        first = self._launch("keep-slot")
        with mock.patch.object(worker_launch, "_spawn_wrapper") as spawn:
            with self.assertRaisesRegex(worker_launch.LaunchError, "job id already belongs|existing job id"):
                worker_launch.launch(
                    self.repo, id="keep-slot", brief="again", worker="grok", role="implement",
                    model="grok-4.6", effort="high", files=["b.py"], owner_session="launch-tests",
                )
            spawn.assert_not_called()
            with self.assertRaisesRegex(worker_launch.LaunchError, "overlap"):
                worker_launch.launch(
                    self.repo, id="overlap", brief="no", worker="grok", role="implement",
                    model="grok-4.6", effort="high", files=["a.py"], owner_session="launch-tests",
                )
            spawn.assert_not_called()
        item = work_queue.add_item(self.repo, "queued work")
        claim = work_queue.claim_next(
            self.repo, item_id=item["id"], files=["b.py"], worker="grok",
            owner_session="launch-tests",
        )
        with mock.patch.object(worker_launch, "_spawn_wrapper") as spawn:
            with self.assertRaisesRegex(worker_launch.LaunchError, "credentials mismatch|owner"):
                worker_launch.launch(
                    self.repo, id="stale-tok", brief="no", worker="grok", role="implement",
                    model="grok-4.6", effort="high", files=["b.py"], owner_session="launch-tests",
                    queue_id=item["id"], reservation_id=claim["reservation_id"],
                    attempt_id=claim["attempt_id"], owner_token="deadbeef",
                )
            spawn.assert_not_called()
        self.configure(cap=1)
        with mock.patch.object(worker_launch, "_spawn_wrapper") as spawn:
            with self.assertRaisesRegex(worker_launch.LaunchError, r"live\+reserved \d+/1"):
                worker_launch.launch(
                    self.repo, id="cap-two", brief="no", worker="grok", role="implement",
                    model="grok-4.6", effort="high", files=["b.py"], owner_session="launch-tests",
                )
            spawn.assert_not_called()
        os.kill(first["wrapper_pid"], 0)

    def test_queue_claim_launch_adopts_same_credentials(self):
        item = work_queue.add_item(self.repo, "queued launch")
        claim = work_queue.claim_next(
            self.repo, item_id=item["id"], files=["a.py"], worker="grok",
            owner_session="launch-tests",
        )
        result = self._launch(
            "from-queue",
            queue_id=item["id"],
            reservation_id=claim["reservation_id"],
            attempt_id=claim["attempt_id"],
            owner_token=claim["owner_token"],
        )
        self.assertEqual(result["reservation_id"], claim["reservation_id"])
        self.assertEqual(result["attempt_id"], claim["attempt_id"])
        held = admission.get_reservation(self.repo, claim["reservation_id"])
        self.assertEqual(held["job_id"], "from-queue")
        self.assertEqual(held["stage"], "reserved")
        creds = json.loads((self.repo / ".rig" / "jobs" / "from-queue" / "owner-credentials.json").read_text())
        self.assertEqual(creds["owner_token"], claim["owner_token"])

    def test_stale_independent_review_fails_before_popen(self):
        with mock.patch.object(worker_launch, "_spawn_wrapper") as spawn:
            with self.assertRaisesRegex(worker_launch.LaunchError, "no such job|independent review"):
                worker_launch.launch(
                    self.repo, id="stale-review", brief="review", worker="claude",
                    role="review", model="claude-sonnet-4-6", effort="high", files=["a.py"],
                    access="read", review_mode="independent", writer_job_id="missing-writer",
                    writer_snapshot_id="snap-old", owner_session="launch-tests",
                )
            spawn.assert_not_called()
        self.assertEqual(self._held(), [])
        self.assertFalse((self.repo / ".rig" / "jobs" / "stale-review").exists())

    def test_readiness_override_env_propagates_to_wrapper(self):
        custom = self.root / "opencode.json"
        launcher = self.home / "rig-mcp.sh"
        custom.write_text(json.dumps({
            "mcp": {"rig": {"type": "local", "command": [str(launcher)], "enabled": True}},
        }))
        os.environ["OPENCODE_CONFIG"] = str(custom)
        self.assertTrue(child_mcp.worker_mcp_ready("opencode")[0])
        result = self._launch("oc-env", worker="opencode", model="openai/gpt-5.6-luna", effort="high", files=["b.py"])
        env_path = self.repo / ".rig" / "jobs" / "oc-env" / "wrapper-env.json"
        for _ in range(50):
            if env_path.is_file():
                break
            time.sleep(0.05)
        dumped = json.loads(env_path.read_text())
        self.assertEqual(dumped.get("OPENCODE_CONFIG"), str(custom))
        self.assertEqual(dumped.get("RIG_JOB_ID"), "oc-env")
        self.assertNotIn("LD_PRELOAD", dumped)
        self.assertEqual(result["worker"], "opencode")


if __name__ == "__main__":
    unittest.main()
