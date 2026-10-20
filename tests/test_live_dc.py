#!/usr/bin/env python3
"""Container mode against a REAL dc-cli and Docker engine, with a stand-in `claude` agent.

Skipped unless RIG_TEST_LIVE_DC=1. The environment it needs (see docs/ade-shell.md, "Verifying against a real
engine"): RIG_DC_BIN is dc-cli, RIG_TEST_LIVE_REPO is a running devcontainer workspace whose config mounts the
repository, the Rig checkout (read-only), RIG_TEST_LIVE_HOME (read-only) at their own paths and a stand-in
`claude` at /usr/local/bin/claude. The stand-in does the real MCP handshake; it is not a model."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import jobs  # noqa: E402
import mcp_test_support  # noqa: E402

LIVE = os.environ.get("RIG_TEST_LIVE_DC") == "1"
REPO = Path(os.environ.get("RIG_TEST_LIVE_REPO", "/nonexistent"))
HOME = Path(os.environ.get("RIG_TEST_LIVE_HOME", "/nonexistent"))
RUN = ROOT / "scripts" / "run-worker.sh"
CLEAR = ("RIG_JOB_ID", "RIG_JOB_DIR", "RIG_OWNER_TOKEN", "RIG_LIVE", "RIG_REPO", "RIG_ROLE", "RIG_CHILD_PYTHON",
         "RIG_PARENT", "RIG_OWNER_SESSION", "RIG_TIMEOUT")


@unittest.skipUnless(LIVE, "set RIG_TEST_LIVE_DC=1 with a running devcontainer workspace")
class RealEngine(unittest.TestCase):
    def setUp(self):
        (REPO / ".rig").mkdir(exist_ok=True)
        for name in ("jobs", "queue", "reservations"):  # a finished job keeps its scope until accepted
            shutil.rmtree(REPO / ".rig" / name, ignore_errors=True)
        (REPO / ".rig" / "harness.toml").write_text(
            'parent = "grok"\n\n[project]\nenabled = true\n\n[workers]\nclaude = true\n\n'
            '[sandbox]\nprovider = "devcontainer"\n\n[sandbox.workers]\nclaude = "mount-ro"\n')
        self.id = f"live{int(time.time() * 1000) % 10**9}"
        self.job = REPO / ".rig" / "jobs" / self.id
        self.job.mkdir(parents=True)
        self.brief = self.job / "brief.md"
        self.brief.write_text("Do the thing.\n")

    def env(self, **extra):
        env = os.environ.copy()
        for key in CLEAR:
            env.pop(key, None)
        env.update({"PATH": mcp_test_support.stub_path() + os.pathsep + os.environ.get("PATH", ""), "HOME": str(HOME), "RIG_HOME": str(ROOT), "RIG_PARENT": "grok",
                    "RIG_SKIP_MODEL_CATALOG": "1", "RIG_SKIP_UPDATE_CHECK": "1", "RIG_ROLE": "worker",
                    "RIG_OWNER_SESSION": "live-parent", "RIG_LIVE": "1", "RIG_TIMEOUT": "60"})
        env.update(extra)
        return env

    def run_worker(self, **extra):
        return subprocess.run([str(RUN), "claude", self.id, str(self.brief)], cwd=REPO, env=self.env(**extra),
                              text=True, capture_output=True, timeout=120)

    def test_a_job_runs_inside_the_real_container(self):
        proc = self.run_worker()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(json.loads((self.job / "result.json").read_text())["status"], "ok", out)
        ran = (self.job / "ran-as.txt").read_text()
        self.assertTrue(ran.startswith(f"{HOME} {REPO}"), ran)
        self.assertNotIn(os.uname().nodename, ran, "the agent must run in the container, not on this host")
        evidence = json.loads((self.job / "sandbox.json").read_text())
        self.assertEqual((evidence["provider"], evidence["probe"], evidence["container"]["found"]), ("devcontainer", "ok", True))

    def test_cancel_stops_the_agent_tree_in_the_real_container(self):
        (self.job / "agent-mode").write_text("hang")
        holder = {}
        thread = threading.Thread(target=lambda: holder.update(p=self.run_worker()))
        thread.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (self.job / "pids").exists():
            time.sleep(0.2)
        self.assertTrue((self.job / "pids").exists(), f"agent never started: {holder.get('p')}")
        agent, child = (int(x) for x in (self.job / "pids").read_text().split())
        self.assertTrue(jobs.cancel_job(REPO, self.id).startswith("cancellation requested"))
        thread.join(timeout=90)
        self.assertFalse(thread.is_alive(), "wrapper did not exit after cancel")
        self.assertEqual(holder["p"].returncode, 130, holder["p"].stdout + holder["p"].stderr)
        script = (f"for p in {agent} {child}; do s=$(sed -n 's/^State:[[:space:]]*\\(.\\).*/\\1/p' /proc/$p/status 2>/dev/null); "
                  '[ -n "$s" ] && [ "$s" != Z ] && exit 1; done; exit 0')
        alive = subprocess.run([os.environ["RIG_DC_BIN"], "exec", "--no-start", str(REPO), "--", "sh", "-c", script],
                               cwd=REPO, env=self.env(), capture_output=True)
        self.assertEqual(alive.returncode, 0, "the agent tree survived the stop inside the container")
        self.assertEqual(json.loads((self.job / "result.json").read_text())["status"], "cancelled")

    def provider(self):
        import sandbox_provider
        return sandbox_provider.provider_for(REPO)

    def test_a_check_command_runs_in_the_container_and_passes_its_exit_code_through(self):
        stop, beat = self.job / "check-stop", self.job / "check-heartbeat"
        argv = self.provider().check_command(["sh", "-c", "echo $(hostname) > check-host.txt; exit 7"], REPO, stop, beat)
        done = subprocess.run(argv, cwd=REPO, env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 7, done.stderr)
        self.assertNotEqual((REPO / "check-host.txt").read_text().strip(), os.uname().nodename)

    def test_a_check_directory_the_container_cannot_see_is_refused(self):
        import sandbox_provider
        with self.assertRaises(sandbox_provider.SandboxError):
            self.provider().check_command(["true"], Path("/nonexistent-on-purpose"), self.job / "s", self.job / "h")

    def test_forwarded_ports_come_from_the_real_dc_forward_status(self):
        import sandbox_provider
        provider = self.provider()
        self.assertIn(3000, provider.forwarded_ports())
        provider.check_url("http://localhost:3000/")
        with self.assertRaises(sandbox_provider.SandboxError):
            provider.check_url("http://localhost:65000/")


if __name__ == "__main__":
    unittest.main()
