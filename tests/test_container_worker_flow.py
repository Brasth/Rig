#!/usr/bin/env python3
"""run-worker.sh in container mode, against a fake dc whose exec runs locally."""
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
sys.path.insert(0, str(ROOT / "tests"))

import jobs  # noqa: E402
import mcp_test_support  # noqa: E402
from container_test_support import fake_agent, install_fake_dc  # noqa: E402

RUN = ROOT / "scripts" / "run-worker.sh"
CLEAR = ("RIG_JOB_ID", "RIG_JOB_DIR", "RIG_OWNER_TOKEN", "RIG_LIVE", "RIG_REPO", "RIG_ROLE", "RIG_CHILD_PYTHON",
         "RIG_PARENT", "RIG_OWNER_SESSION", "RIG_TIMEOUT")


class Flow(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = self.base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        self.write_harness()
        self.job = self.repo / ".rig" / "jobs" / "cj"
        self.job.mkdir(parents=True)
        self.brief = self.job / "brief.md"
        self.brief.write_text("Fix the thing.\n")
        self.bin = self.base / "bin"
        self.dc = install_fake_dc(self.bin)
        self.home = self.base / "home"
        mcp_test_support.seed_installed_mcp(self.home)
        (self.home / ".claude").mkdir(exist_ok=True)
        self.log = self.base / "dc.log"

    def tearDown(self):
        self.td.cleanup()

    def write_harness(self, provider="devcontainer", creds='claude = "mount-ro"\ncodex = "none"\n', workers="claude = true\ncodex = true\ngrok = true\n"):
        (self.repo / ".rig" / "harness.toml").write_text(
            f'parent = "grok"\n\n[project]\nenabled = true\n\n[workers]\n{workers}\n'
            f'[sandbox]\nprovider = "{provider}"\n\n[sandbox.workers]\n{creds}')

    def env(self, **extra):
        env = os.environ.copy()
        for key in CLEAR:
            env.pop(key, None)
        env.update({"PATH": mcp_test_support.stub_path(self.bin), "HOME": str(self.home), "RIG_HOME": str(ROOT),
                    "RIG_PARENT": "grok", "RIG_SKIP_MODEL_CATALOG": "1", "RIG_SKIP_UPDATE_CHECK": "1",
                    "RIG_DC_BIN": str(self.dc), "FAKE_DC_LOG": str(self.log), "RIG_ROLE": "worker",
                    "RIG_OWNER_SESSION": "container-flow-parent"})
        env.update(extra)
        return env

    def run_worker(self, worker, **extra):
        return subprocess.run([str(RUN), worker, "cj", str(self.brief)], cwd=self.repo, env=self.env(**extra),
                              text=True, capture_output=True, timeout=90)

    def dc_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class DryRun(Flow):
    def test_claude_dry_run_shows_the_supervised_container_command(self):
        proc = self.run_worker("claude")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        would = out.split("would run:", 1)[1]
        for fragment in ("-- sh -c", "rig-supervisor", f"HOME={self.home}", "RIG_JOB_ID=cj",
                         "env HOME=", "claude -p", "--mcp-config", "--strict-mcp-config"):
            self.assertIn(fragment, would)
        self.assertNotIn("--dangerously-skip-permissions", would)
        self.assertEqual([c for c in self.dc_calls() if c.startswith("exec")], [], "dry-run must not touch the container")

    def test_dry_run_mcp_config_names_python_by_name_not_the_host_path(self):
        self.run_worker("claude")
        server = json.loads((self.job / "mcp.json").read_text())["mcpServers"]["rig"]
        self.assertEqual(server["command"], "python3")

    def test_host_mode_is_unchanged_and_keeps_the_host_python(self):
        self.write_harness(provider="host")
        self.run_worker("claude")
        server = json.loads((self.job / "mcp.json").read_text())["mcpServers"]["rig"]
        self.assertTrue(os.path.isabs(server["command"]))

    def test_codex_runs_without_its_own_sandbox_inside_the_container(self):
        out = self.run_worker("codex")
        would = (out.stdout + out.stderr).split("would run:", 1)[1]
        self.assertIn("danger-full-access", would)
        self.assertNotIn("workspace-write", would)

    def test_unsupported_worker_is_refused_with_the_reason(self):
        out = self.run_worker("grok", RIG_PARENT="codex")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("cannot run in a container", out.stdout + out.stderr)
        self.assertIn("global host configuration", out.stdout + out.stderr)

    def test_a_worker_without_a_credential_choice_is_refused(self):
        self.write_harness(creds='codex = "none"\n')
        out = self.run_worker("claude")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no credential choice", out.stdout + out.stderr)
        self.assertIn('claude = "mount-ro"', out.stdout + out.stderr)

    def test_missing_dc_is_refused_never_run_on_the_host(self):
        empty = self.base / "empty"
        empty.mkdir()
        out = self.run_worker("claude", RIG_DC_BIN="", PATH=f"{empty}:/usr/bin:/bin")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("needs dc-cli", out.stdout + out.stderr)
        self.assertNotIn("would run", out.stdout)


HANDSHAKE_AGENT = (mcp_test_support.inbox_handshake_prelude(ROOT)
                   + "import os, sys\n_rig_inbox_handshake()\n")


def live_env(self, **extra):
    return Flow.env(self, RIG_LIVE="1", RIG_TIMEOUT="40", **extra)


class Live(Flow):
    """Live launches. The 'container' is the local machine, so the supervisor really runs."""

    def run_live(self, worker="claude", **extra):
        return subprocess.run([str(RUN), worker, "cj", str(self.brief)], cwd=self.repo, env=live_env(self, **extra),
                              text=True, capture_output=True, timeout=90)

    def test_a_job_completes_in_the_container_and_records_where_it_ran(self):
        fake_agent(self.bin, "claude", HANDSHAKE_AGENT + "print('{\"result\": \"done in container\"}')\n"
                   "open(os.path.join(os.environ['RIG_JOB_DIR'], 'ran-as.txt'), 'w').write(os.environ['HOME'] + ' ' + os.getcwd())\n")
        proc = self.run_live()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        result = json.loads((self.job / "result.json").read_text())
        self.assertEqual(result["status"], "ok", out)
        self.assertEqual((self.job / "ran-as.txt").read_text(), f"{self.home} {self.repo.resolve()}")
        evidence = json.loads((self.job / "sandbox.json").read_text())
        self.assertEqual((evidence["provider"], evidence["worker"], evidence["probe"]), ("devcontainer", "claude", "ok"))
        execs = [c for c in self.dc_calls() if c.startswith("exec")]
        self.assertGreaterEqual(len(execs), 2, "one probe and one supervised run")
        self.assertTrue((self.job / "container-heartbeat").exists())

    def test_unreachable_container_refuses_before_anything_runs(self):
        marker = self.base / "agent-ran"
        fake_agent(self.bin, "claude", f"open({str(marker)!r}, 'w').write('x')\n")
        proc = self.run_live(FAKE_DC_EXEC_FAIL="1")
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("rig env up", out)
        self.assertFalse(marker.exists(), "the agent must never run on the host as a fallback")
        self.assertFalse((self.job / "sandbox.json").exists())

    def test_cancel_stops_the_whole_agent_tree_inside_the_container(self):
        pids = self.base / "pids"
        fake_agent(self.bin, "claude", HANDSHAKE_AGENT + "import subprocess, time\nchild = subprocess.Popen(['sleep', '300'])\n"
                   f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\ntime.sleep(300)\n")
        holder = {}
        thread = threading.Thread(target=lambda: holder.update(p=self.run_live()))
        thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not pids.exists():
            time.sleep(0.1)
        self.assertTrue(pids.exists(), f"agent never started: {holder.get('p')}")
        agent, child = (int(x) for x in pids.read_text().split())
        text = jobs.cancel_job(self.repo, "cj")
        self.assertTrue(text.startswith("cancellation requested"), text)
        thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "wrapper did not exit after cancel")
        proc = holder["p"]
        self.assertEqual(proc.returncode, 130, proc.stdout + proc.stderr)
        time.sleep(0.5)
        for pid, label in ((agent, "agent"), (child, "its child")):
            try:
                os.kill(pid, 0)
                state = Path(f"/proc/{pid}/stat").read_text().split()[2] if Path(f"/proc/{pid}/stat").exists() else "gone"
                self.assertEqual(state, "Z", f"{label} survived the stop")
            except ProcessLookupError:
                pass
        self.assertTrue((self.job / "container-stop").exists())
        self.assertEqual(json.loads((self.job / "result.json").read_text())["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
