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
from datetime import datetime, timezone
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
        mcp_test_support.fake_bin(self.bins, "devin")
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
            "opencode = true\nomp = true\npi = true\nagy = true\ndevin = true\ncursor = false\n"
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

    def test_devin_rejects_wrong_selector_and_allows_its_own_reservation(self):
        with self.assertRaisesRegex(worker_launch.LaunchError, "expected swe-2-high"):
            self._launch("devin-bad", worker="devin", model="swe-2-medium", effort="medium")
        launched = self._launch("devin-good", worker="devin", model="swe-2-high", effort="high")
        self.assertEqual(launched["worker"], "devin")
        with self.assertRaisesRegex(worker_launch.LaunchError, "one Devin job at a time"):
            self._launch("devin-second", files=["b.py"], worker="devin", model="swe-2-high", effort="high")

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

    def test_direct_parent_opt_in_is_not_wrapper_launch(self):
        (self.repo / ".rig" / "routing.json").write_text(json.dumps({
            "schema_version": 2,
            "execution": {"direct_parent_low_risk": True},
        }))
        with self.assertRaisesRegex(worker_launch.LaunchError, "direct-parent|parent writes"):
            worker_launch.launch(
                self.repo, id="direct-parent", brief="tiny label", worker="",
                role="implement", model="", effort="", files=["a.py"],
                assessment={"complexity": "low", "risk": "low", "uncertainty": "low"},
                owner_session="launch-tests",
            )
        self.assertFalse((self.repo / ".rig" / "jobs" / "direct-parent").exists())
        self.assertEqual(self._held(), [])

    def _wrapper_env(self, job_id):
        env_path = self.repo / ".rig" / "jobs" / job_id / "wrapper-env.json"
        for _ in range(50):
            if env_path.is_file():
                break
            time.sleep(0.05)
        return json.loads(env_path.read_text())

    def test_resources_json_is_canonical_and_legacy_empty(self):
        empty = self._launch("res-empty", files=["a.py"])
        empty_env = self._wrapper_env("res-empty")
        self.assertEqual(json.loads(empty_env["RIG_JOB_RESOURCES_JSON"]), [])
        self.assertEqual(empty["job_id"], "res-empty")
        claimed = self._launch(
            "res-claim", files=["b.py"],
            resources=[{"name": "db.primary", "access": "write"}],
            workflow_id="wf1", workflow_node_id="n1",
            workflow_spec_hash="abc", workflow_attempt=2,
        )
        claimed_env = self._wrapper_env("res-claim")
        self.assertEqual(
            json.loads(claimed_env["RIG_JOB_RESOURCES_JSON"]),
            [{"name": "db.primary", "access": "write"}],
        )
        self.assertEqual(claimed_env.get("RIG_WORKFLOW_ID"), "wf1")
        self.assertEqual(claimed_env.get("RIG_WORKFLOW_NODE_ID"), "n1")
        self.assertEqual(claimed_env.get("RIG_WORKFLOW_SPEC_HASH"), "abc")
        self.assertEqual(claimed_env.get("RIG_WORKFLOW_ATTEMPT"), "2")
        meta = json.loads((self.repo / ".rig" / "jobs" / "res-claim" / "meta.json").read_text())
        self.assertEqual(meta["resources"], [{"name": "db.primary", "access": "write"}])
        self.assertEqual(meta["workflow_id"], "wf1")
        self.assertEqual(claimed["job_id"], "res-claim")

    def test_verify_launch_keeps_writer_provenance_without_review_gate(self):
        result = self._launch(
            "verify-node", role="verify", access="read", files=["a.py"],
            writer_job_id="writer-a", writer_job_ids=["writer-b"],
            writer_providers=["xai"],
        )
        meta = json.loads((self.repo / ".rig" / "jobs" / "verify-node" / "meta.json").read_text())
        self.assertEqual(meta["role"], "verify")
        self.assertEqual(meta["access"], "read")
        self.assertEqual(meta["writer_job_id"], "writer-a")
        self.assertIn("writer-b", meta["writer_job_ids"])
        lease = admission.get_reservation(self.repo, result["reservation_id"])
        self.assertFalse(lease.get("writer_job_id"))
        self.assertIn("writer-a", lease.get("writer_job_ids") or [])

    def test_omitted_empty_ids_admit_three_disjoint_nodes_same_frozen_second(self):
        (self.repo / "c.py").write_text("c\n")
        (self.repo / "d.py").write_text("d\n")
        frozen = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
        pid = os.getpid()
        prefix = frozen.strftime("%Y%m%dT%H%M%SZ") + f"-{pid}-"

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz is None else frozen.astimezone(tz)

        def admit(files, **kwargs):
            result = worker_launch.launch(
                self.repo,
                brief="do the listed files",
                worker="grok",
                role="implement",
                model="grok-4.6",
                effort="high",
                files=files,
                owner_session="launch-tests",
                **kwargs,
            )
            self.pids.append(result["wrapper_pid"])
            return result

        with mock.patch("jobs.datetime", FrozenDateTime):
            omitted = admit(["a.py"])
            empty = admit(["b.py"], id="")
            spaced = admit(["c.py"], id="  ")
            with self.assertRaisesRegex(
                worker_launch.LaunchError, "job id already belongs|existing job id"
            ):
                worker_launch.launch(
                    self.repo, id=omitted["job_id"], brief="again", worker="grok",
                    role="implement", model="grok-4.6", effort="high", files=["d.py"],
                    owner_session="launch-tests",
                )
            with self.assertRaisesRegex(worker_launch.LaunchError, r"live\+reserved 3/3"):
                admit(["d.py"], id="")

        ids = [omitted["job_id"], empty["job_id"], spaced["job_id"]]
        reservations = [
            omitted["reservation_id"], empty["reservation_id"], spaced["reservation_id"],
        ]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(len(set(reservations)), 3)
        for job_id in ids:
            self.assertTrue(job_id.startswith(prefix), job_id)
            self.assertTrue(jobs.JOB_ID_RE.fullmatch(job_id))
            self.assertTrue((self.repo / ".rig" / "jobs" / job_id / "meta.json").is_file())
        self.assertEqual(len(self._held()), 3)
        job_dirs = {path.name for path in (self.repo / ".rig" / "jobs").iterdir() if path.is_dir()}
        self.assertEqual(job_dirs, set(ids))

    def _finish_wrapper(self, launched):
        creds = json.loads(Path(launched["credentials_path"]).read_text())
        admission.finish(
            self.repo, status="ok", completion={"kind": "parent_task", "completed": True},
            reservation_id=creds["reservation_id"], attempt_id=creds["attempt_id"],
            owner_token=creds["owner_token"], owner_session="launch-tests",
        )
        return self.repo / ".rig" / "reservations" / f"{launched['reservation_id']}.json"

    def test_mcp_launch_public_text_includes_credentials_path_not_token(self):
        out = rig_mcp.call_tool("rig_job_launch", {
            "repo": str(self.repo), "id": "pub-creds", "brief": "go",
            "worker": "grok", "role": "implement", "model": "grok-4.6",
            "effort": "high", "files": ["a.py"],
        })
        self.assertNotIn("isError", out, out)
        result = out["structuredContent"]
        self.pids.append(result["wrapper_pid"])
        payload = json.loads(out["content"][0]["text"])
        creds = json.loads(Path(payload["credentials_path"]).read_text())
        token = creds["owner_token"]
        public_keys = {
            "job_id", "worker", "role", "wrapper_pid", "status",
            "reservation_id", "attempt_id", "credentials_path",
        }
        self.assertEqual(payload["job_id"], "pub-creds")
        self.assertEqual(payload["credentials_path"], result["credentials_path"])
        self.assertEqual(payload["reservation_id"], result["reservation_id"])
        self.assertEqual(payload["attempt_id"], result["attempt_id"])
        self.assertIn("wrapper_pid", payload)
        self.assertLessEqual(set(payload), public_keys)
        self.assertLessEqual(set(result), public_keys)
        self.assertNotIn("owner_token", payload)
        self.assertNotIn("owner_token", result)
        self.assertNotIn(token, out["content"][0]["text"])
        self.assertNotIn(token, json.dumps(result))
        self.assertEqual(Path(payload["credentials_path"]).stat().st_mode & 0o777, 0o600)

    def test_stopped_wrapper_receipt_and_credentials_path_close(self):
        launched = self._launch("wrap-receipt")
        creds = json.loads(Path(launched["credentials_path"]).read_text())
        token = creds["owner_token"]
        rec_path = self._finish_wrapper(launched)
        before = rec_path.read_bytes()
        recovered = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-receipt"},
        )
        self.assertNotIn("isError", recovered, recovered)
        payload = json.loads(recovered["content"][0]["text"])
        self.assertEqual(payload["credentials_path"], launched["credentials_path"])
        self.assertNotIn("owner_token", payload)
        self.assertNotIn(token, recovered["content"][0]["text"])
        self.assertEqual(rec_path.read_bytes(), before)
        again = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-receipt"},
        )
        self.assertNotIn("isError", again)
        self.assertEqual(rec_path.read_bytes(), before)
        closed = rig_mcp.call_tool(
            "rig_job_close",
            {"repo": str(self.repo), "id": "wrap-receipt", "rationale": "wrapper handoff",
             "credentials_path": payload["credentials_path"], "owner_session": "launch-tests"},
        )
        self.assertFalse(closed.get("isError"), closed)
        self.assertNotIn(token, closed["content"][0]["text"])
        released = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-receipt"},
        )
        self.assertTrue(released.get("isError"))
        self.assertIn("released", released["content"][0]["text"])
        self.assertNotIn(token, released["content"][0]["text"])

    def test_wrapper_receipt_rejects_live_launch(self):
        launched = self._launch("wrap-live")
        token = json.loads(Path(launched["credentials_path"]).read_text())["owner_token"]
        denied = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-live"},
        )
        self.assertTrue(denied.get("isError"))
        self.assertIn("active work", denied["content"][0]["text"])
        self.assertNotIn(token, denied["content"][0]["text"])

    def _seed_terminal(self, job_id, status="ok", files=None, worker="grok",
                       owner_session="launch-tests", **job_fields):
        files = ["a.py"] if files is None else files
        folder = self.repo / ".rig" / "jobs" / job_id
        ended = "" if status == "running" else "2026-09-10T07:01:00Z"
        if status != "running":
            owner = admission.caller_owner("parent", owner_session=owner_session)
            lease = admission.reserve(
                self.repo, job_id=job_id, worker=worker, role="implement",
                model="grok-4.6", files=files, owner=owner,
            )
            record = admission._read(admission._reservation_path(self.repo, lease["reservation_id"]), required=True)
            record.update(stopped=True, execution_status=status, stage="verifying", slot_held=False)
            for key in ("continues_job_id", "continuation_root_id", "continuation_depth"):
                if key in job_fields:
                    record[key] = job_fields[key]
            admission._save(self.repo, record)
        jobs.write_job_files(
            folder, job_id, worker, "implement", status,
            0 if status in {"ok", "running"} else 1,
            "2026-09-10T07:00:00Z", ended, "done" if status != "running" else "",
            kind="wrapper", executor_kind="wrapper", files=files,
            **{key: value for key, value in job_fields.items() if key in {
                "continues_job_id", "continuation_root_id", "continuation_depth", "continuation_mode",
            }},
        )
        return folder

    def test_continues_job_id_requires_existing_terminal_job(self):
        with self.assertRaisesRegex(worker_launch.LaunchError, "does not exist"):
            worker_launch.launch(
                self.repo, id="next-missing", brief="delta", worker="grok",
                role="implement", model="grok-4.6", effort="high", files=["b.py"],
                continues_job_id="missing-job", owner_session="launch-tests",
            )
        self._seed_terminal("prior-live", status="running")
        with self.assertRaisesRegex(worker_launch.LaunchError, "not terminal"):
            worker_launch.launch(
                self.repo, id="next-live", brief="delta", worker="grok",
                role="implement", model="grok-4.6", effort="high", files=["b.py"],
                continues_job_id="prior-live", owner_session="launch-tests",
            )
        self._seed_terminal("prior-ok", status="ok", files=["b.py"])
        launched = self._launch("next-ok", files=["b.py"], continues_job_id="prior-ok")
        env = self._wrapper_env("next-ok")
        self.assertEqual(env.get("RIG_CONTINUES_JOB_ID"), "prior-ok")
        self.assertEqual(env.get("RIG_CONTINUATION_ROOT_ID"), "prior-ok")
        self.assertEqual(env.get("RIG_CONTINUATION_DEPTH"), "1")
        meta = json.loads((self.repo / ".rig" / "jobs" / "next-ok" / "meta.json").read_text())
        self.assertEqual(meta.get("continues_job_id"), "prior-ok")
        self.assertEqual(meta.get("continuation_root_id"), "prior-ok")
        self.assertEqual(meta.get("continuation_depth"), 1)
        self.assertEqual(launched["job_id"], "next-ok")
        prior = next(row for row in admission.list_reservations(self.repo) if row.get("job_id") == "prior-ok")
        self.assertTrue(prior.get("superseded"))
        self.assertEqual(prior.get("continued_by"), "next-ok")
        self.assertEqual(prior.get("stage"), "verifying")

    def test_continuation_rejects_cancelled_worker_owner_and_wider_files(self):
        (self.repo / "c.py").write_text("c\n")
        (self.repo / "d.py").write_text("d\n")
        self._seed_terminal("prior-cancel", status="cancelled", files=["a.py"])
        with self.assertRaisesRegex(worker_launch.LaunchError, "cancelled predecessor"):
            self._launch("next-cancel", files=["a.py"], continues_job_id="prior-cancel")
        self._seed_terminal("prior-grok", status="ok", files=["b.py"])
        with self.assertRaisesRegex(worker_launch.LaunchError, "worker mismatch"):
            self._launch("next-claude", worker="claude", model="claude-sonnet-5", effort="medium",
                         files=["b.py"], continues_job_id="prior-grok")
        self._seed_terminal("prior-owner", status="ok", files=["c.py"])
        with self.assertRaisesRegex(worker_launch.LaunchError, "owner/session"):
            self._launch("next-owner", files=["c.py"], continues_job_id="prior-owner",
                         owner_session="other-session")
        self._seed_terminal("prior-narrow", status="ok", files=["d.py"])
        with self.assertRaisesRegex(worker_launch.LaunchError, "equal or narrower"):
            self._launch("next-wide", files=["d.py", "a.py"], continues_job_id="prior-narrow")

    def test_continuation_cap_requires_a_fresh_job(self):
        self._seed_terminal("root", files=["root.py"])
        self._seed_terminal(
            "prior-deep", status="ok", continues_job_id="root",
            continuation_root_id="root", continuation_depth=3,
        )
        with self.assertRaisesRegex(worker_launch.LaunchError, "inconsistent continuation depth"):
            self._launch("next-deep", continues_job_id="prior-deep")


class ResumeExecution(unittest.TestCase):
    def run_resume(self, folder, diagnostic, *, connected=False, fresh_exit=0):
        if connected:
            (folder / "meta.json").write_text(json.dumps({
                "child_mcp_status": "connected", "child_mcp_protocol": 1,
            }))
        resume = [sys.executable, "-c", f"print({diagnostic!r}); raise SystemExit(2)"]
        fresh = [sys.executable, "-c", f"print('FRESH_EXECUTED'); raise SystemExit({fresh_exit})"]
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import worker_launch; "
            "raise SystemExit(worker_launch.run_resume_command("
            f"{resume!r}, {fresh!r}, sys.argv[2], 'new-session'))"
        )
        return subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts"), str(folder)],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              start_new_session=True, timeout=20)

    def test_real_failed_resume_runs_fresh_once_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            result = self.run_resume(folder, "Error: session not found: missing", fresh_exit=9)
            self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
            self.assertEqual(result.stdout.count("FRESH_EXECUTED"), 1)
            self.assertIn("session not found", (folder / "resume-attempt.log").read_text())
            self.assertEqual(json.loads((folder / "resume-fallback.json").read_text())["session_id"], "new-session")

    def test_activity_or_handshake_or_arbitrary_failure_never_replays(self):
        for diagnostic, connected in [
            ('{"type":"tool_call"}\nError: session not found: missing', False),
            ("Error: session not found: missing", True),
            ("Error: network timeout", False),
        ]:
            with self.subTest(diagnostic=diagnostic, connected=connected), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                result = self.run_resume(folder, diagnostic, connected=connected)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("FRESH_EXECUTED", result.stdout)
                self.assertFalse((folder / "resume-fallback.json").exists())


if __name__ == "__main__":
    unittest.main()
