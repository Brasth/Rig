#!/usr/bin/env python3
import json
import os
import signal
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
sys.path.insert(0, str(ROOT / "tests"))

import container_modes  # noqa: E402
import sandbox_container as sc  # noqa: E402
import sandbox_provider  # noqa: E402
from container_test_support import fake_agent, install_fake_dc  # noqa: E402


def make_repo(root: Path, harness: str = "") -> Path:
    (root / ".git").mkdir(exist_ok=True)
    (root / ".rig" / "jobs" / "j1").mkdir(parents=True, exist_ok=True)
    (root / ".rig" / "harness.toml").write_text(
        '[project]\nenabled = true\n\n[sandbox]\nprovider = "devcontainer"\n\n[sandbox.workers]\n'
        'claude = "mount-ro"\ncodex = "none"\n' + harness)
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = make_repo(self.base / "repo") if (self.base / "repo").mkdir() is None else None
        self.job = self.repo / ".rig" / "jobs" / "j1"
        self.bin = self.base / "bin"
        self.dc = install_fake_dc(self.bin)
        self.home = self.base / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.log = self.base / "dc.log"
        self.env = mock.patch.dict(os.environ, {
            "RIG_DC_BIN": str(self.dc), "FAKE_DC_LOG": str(self.log), "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ['PATH']}"})
        self.env.start()
        for key in ("FAKE_DC_EXEC_FAIL", "FAKE_DC_LS"):
            os.environ.pop(key, None)
        self.provider = sc.DevcontainerProvider(self.repo, {"claude": "mount-ro", "codex": "none"})
        self.job_env = {"RIG_JOB_ID": "j1", "RIG_JOB_DIR": str(self.job), "RIG_REPO": str(self.repo),
                        "RIG_ROLE": "worker", "RIG_OWNER_TOKEN": "SECRET", "PATH": "/x"}

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()


class Modes(unittest.TestCase):
    def test_only_job_scoped_workers_are_supported(self):
        self.assertEqual(sorted(container_modes.supported_workers()), ["claude", "codex"])
        for name in ("grok", "cursor", "opencode", "omp", "pi", "agy", "devin", "mimo"):
            self.assertIn("cannot run in a container", container_modes.credential_problem(name, {name: "none"}))

    def test_permission_replacement_is_exact_and_repeatable(self):
        argv = ["codex", "exec", "--json", "-s", "workspace-write", "-C", "/r"]
        self.assertEqual(container_modes.apply_permissions("codex", argv)[3:5], ["-s", "danger-full-access"])
        self.assertEqual(container_modes.apply_permissions("codex", ["codex", "exec", "resume"]), ["codex", "exec", "resume"])
        twice = container_modes.apply_permissions("codex", ["-s", "workspace-write", "x", "-s", "workspace-write"])
        self.assertEqual(twice.count("danger-full-access"), 2)
        self.assertEqual(container_modes.apply_permissions("claude", ["claude", "-s", "workspace-write"]), ["claude", "-s", "workspace-write"])

    def test_credential_choice_is_explicit(self):
        self.assertIn("no credential choice", container_modes.credential_problem("claude", {}))
        self.assertIn("no credential choice", container_modes.credential_problem("claude", {"claude": "bogus"}))
        self.assertEqual(container_modes.credential_problem("claude", {"claude": "mount-ro"}), "")
        self.assertEqual(container_modes.credential_problem("codex", {"codex": "none"}), "")


class Wrap(Base):
    def test_exact_argv_shape(self):
        argv, env = self.provider.wrap_worker_cmd(["/usr/local/bin/claude", "-p", "do it"], self.job_env, worker="claude")
        self.assertEqual(argv[:7], [str(self.dc), "exec", "--no-start", str(self.repo.resolve()), "--", "sh", "-c"])
        self.assertEqual(argv[7], sc.SUPERVISOR)
        self.assertEqual(argv[8:13], ["rig-supervisor", str(self.repo.resolve()), str(self.job / "container-stop"),
                                      str(self.job / "container-heartbeat"), "15"])
        self.assertEqual(argv[13:], ["env", f"HOME={self.home}", "RIG_JOB_ID=j1", f"RIG_JOB_DIR={self.job}",
                                     f"RIG_REPO={self.repo}", "RIG_ROLE=worker", "claude", "-p", "do it"])
        self.assertEqual(env, self.job_env)

    def test_secrets_and_host_environment_are_not_forwarded(self):
        argv, _ = self.provider.wrap_worker_cmd(["claude"], self.job_env, worker="claude")
        joined = "\n".join(argv)
        self.assertNotIn("SECRET", joined)
        self.assertNotIn("RIG_OWNER_TOKEN", joined)
        self.assertNotIn("PATH=", joined)

    def test_codex_gets_the_container_sandbox_setting_and_no_dc_flags_leak(self):
        argv, _ = self.provider.wrap_worker_cmd(["codex", "exec", "-s", "workspace-write", "-C", str(self.repo)],
                                                self.job_env, worker="codex")
        tail = argv[argv.index("codex"):]
        self.assertEqual(tail[:5], ["codex", "exec", "-s", "danger-full-access", "-C"])

    def test_refusals_name_the_reason(self):
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "cannot run in a container"):
            self.provider.wrap_worker_cmd(["grok"], self.job_env, worker="grok")
        bare = sc.DevcontainerProvider(self.repo, {})
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "no credential choice"):
            bare.wrap_worker_cmd(["claude"], self.job_env, worker="claude")
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "job directory"):
            self.provider.wrap_worker_cmd(["claude"], {}, worker="claude")

    def test_provider_for_requires_dc_and_carries_credentials(self):
        provider = sandbox_provider.provider_for(self.repo)
        self.assertEqual((provider.name, provider.credentials), ("devcontainer", {"claude": "mount-ro", "codex": "none"}))
        with mock.patch.dict(os.environ, {"RIG_DC_BIN": "", "PATH": str(self.base)}):
            with self.assertRaisesRegex(sandbox_provider.SandboxError, "needs dc-cli"):
                sandbox_provider.provider_for(self.repo)


def run_supervised(argv, env=None, timeout=60):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env or os.environ.copy())


class SupervisorBehaviour(Base):
    """The real supervisor script running through the fake dc."""

    def agent(self, body):
        return fake_agent(self.bin, "claude", body)

    def start(self, command, lease=15):
        argv = sc.supervised(str(self.dc), self.repo, self.repo, self.job / sc.STOP_FILE, self.job / sc.HEARTBEAT_FILE,
                             command, [f"HOME={self.home}", "RIG_JOB_ID=j1"], lease=lease)
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_exit_status_and_streams_pass_through_and_env_and_cwd_are_set(self):
        self.agent("import os,sys\nprint(os.getcwd());print(os.environ['HOME'], os.environ['RIG_JOB_ID'])\n"
                   "print('to-stderr', file=sys.stderr)\nsys.exit(7)\n")
        (self.job / sc.HEARTBEAT_FILE).write_text("1")
        done = self.start(["claude"])
        out, err = done.communicate(timeout=30)
        self.assertEqual(done.returncode, 7)
        self.assertEqual(out.split("\n")[:2], [str(self.repo.resolve()), f"{self.home} j1"])
        self.assertIn("to-stderr", err)

    def pids(self, name):
        deadline = time.monotonic() + 10
        path = self.base / name
        while time.monotonic() < deadline and not path.exists():
            time.sleep(0.05)
        agent, child = path.read_text().split()
        return int(agent), int(child)

    def alive(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        stat = Path(f"/proc/{pid}/stat")
        return not (stat.exists() and stat.read_text().split()[2] == "Z")

    def tree_agent(self, name):
        self.agent("import os,subprocess,sys,time\nchild = subprocess.Popen(['sleep','300'])\n"
                   f"open({str(self.base / name)!r},'w').write(f'{{os.getpid()}} {{child.pid}}')\ntime.sleep(300)\n")

    def test_stop_file_ends_the_whole_process_tree(self):
        self.tree_agent("pids1")
        (self.job / sc.HEARTBEAT_FILE).write_text("1")
        proc = self.start(["claude"])
        agent, child = self.pids("pids1")
        self.assertTrue(self.alive(agent) and self.alive(child))
        (self.job / sc.STOP_FILE).write_text("stop\n")
        proc.communicate(timeout=30)
        time.sleep(0.3)
        self.assertFalse(self.alive(agent), "agent must be gone")
        self.assertFalse(self.alive(child), "the agent's own child must be gone too")
        self.assertNotEqual(proc.returncode, 0)

    def test_a_stopped_heartbeat_ends_the_tree_after_the_lease(self):
        self.tree_agent("pids2")
        (self.job / sc.HEARTBEAT_FILE).write_text("1")
        proc = self.start(["claude"], lease=3)
        agent, child = self.pids("pids2")
        started = time.monotonic()
        for n in range(2, 6):  # a live host keeps beating, and the agent must survive that
            (self.job / sc.HEARTBEAT_FILE).write_text(str(n))
            time.sleep(1)
        self.assertIsNone(proc.poll(), "a beating heartbeat must keep the agent alive")
        proc.communicate(timeout=30)  # beats stop here
        self.assertGreater(time.monotonic() - started, 5)
        time.sleep(0.3)
        self.assertFalse(self.alive(agent) or self.alive(child))

    def test_a_missing_heartbeat_file_also_expires(self):
        self.agent("import time\ntime.sleep(300)\n")
        proc = self.start(["claude"], lease=2)
        proc.communicate(timeout=30)
        self.assertNotEqual(proc.returncode, 0)

    def test_unreachable_container_is_a_failure_not_a_host_run(self):
        self.agent("open(%r,'w').write('ran on the host')\n" % str(self.base / "ran"))
        os.environ["FAKE_DC_EXEC_FAIL"] = "1"
        proc = self.start(["claude"])
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertFalse((self.base / "ran").exists())


class Probe(Base):
    def setUp(self):
        super().setUp()
        fake_agent(self.bin, "claude", "")
        fake_agent(self.bin, "codex", "")

    def test_ready_container_passes_for_both_credential_modes(self):
        self.provider.probe("claude")
        self.provider.probe("codex")

    def test_each_named_failure_has_its_own_message(self):
        cases = {11: "same path", 12: "Rig runtime", 13: "python3", 14: "setsid", 15: "claude CLI", 16: "no credentials for claude"}
        for code, text in cases.items():
            result = subprocess.CompletedProcess([], code, "", "")
            with mock.patch.object(self.provider, "_probe_run", return_value=result):
                with self.assertRaisesRegex(sandbox_provider.SandboxError, text):
                    self.provider.probe("claude")

    def test_real_failures_through_the_actual_probe_script(self):
        (self.repo / ".rig" / "harness.toml").rename(self.base / "h.toml")
        os.rename(self.repo / ".rig", self.base / "rig-moved")
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "same path"):
            self.provider.probe("claude")
        os.rename(self.base / "rig-moved", self.repo / ".rig")
        (self.bin / "claude").unlink()
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "claude CLI"):
            self.provider.probe("claude")
        fake_agent(self.bin, "claude", "")
        (self.home / ".claude").rmdir()
        with self.assertRaisesRegex(sandbox_provider.SandboxError, r"mount one of ~/\.claude, ~/\.claude\.json"):
            self.provider.probe("claude")
        self.provider.probe("codex")  # "none" needs no credential mount

    def test_unreachable_container_says_how_to_start_it(self):
        os.environ["FAKE_DC_EXEC_FAIL"] = "1"
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "rig env up"):
            self.provider.probe("claude")

    def test_a_hung_container_times_out_with_a_clear_message(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("dc", 1)):
            with self.assertRaisesRegex(sandbox_provider.SandboxError, "did not answer"):
                self.provider.probe("claude")


class Evidence(Base):
    def setUp(self):
        super().setUp()
        fake_agent(self.bin, "claude", "")

    def test_prepare_records_container_facts_from_dc(self):
        os.environ["FAKE_DC_LS"] = json.dumps([
            {"name": "other", "state": "running", "workspace": "/else"},
            {"name": "my-dev", "id": "a3f9c1d2e4b5", "state": "running", "image": "node:22",
             "image_digest": "sha256:abc", "workspace": str(self.repo)}])
        evidence = self.provider.prepare("claude", self.job, "j1")
        self.assertEqual(evidence["container"], {"found": True, "name": "my-dev", "id": "a3f9c1d2e4b5", "image": "node:22",
                                                  "image_digest": "sha256:abc", "state": "running", "matched": 1})
        saved = json.loads((self.job / "sandbox.json").read_text())
        self.assertEqual((saved["provider"], saved["worker"], saved["probe"], saved["lease_seconds"]), ("devcontainer", "claude", "ok", 15))
        self.assertEqual([p.name for p in self.job.glob("sandbox*")], ["sandbox.json"])

    def test_unknown_container_facts_are_not_invented(self):
        evidence = self.provider.prepare("claude", self.job, "j1")
        self.assertFalse(evidence["container"]["found"])
        self.assertIn("no container", evidence["container"]["note"])
        self.assertNotIn("image_digest", evidence["container"])

    def test_prepare_refuses_before_writing_when_the_probe_fails(self):
        os.environ["FAKE_DC_EXEC_FAIL"] = "1"
        with self.assertRaises(sandbox_provider.SandboxError):
            self.provider.prepare("claude", self.job, "j1")
        self.assertFalse((self.job / "sandbox.json").exists())


class LeaseAndChecks(Base):
    def test_lease_beats_changes_content_and_cleans_up(self):
        hb, stop = self.job / "x.hb", self.job / "x.stop"
        with sc.Lease(hb, stop, interval=0.05) as lease:
            first = hb.read_text()
            time.sleep(0.3)
            self.assertNotEqual(hb.read_text(), first, "content must change so the supervisor sees life")
            lease.request_stop()
            self.assertTrue(stop.exists())
            frozen = hb.read_text()
            time.sleep(0.3)
            self.assertEqual(hb.read_text(), frozen, "beating stops once a stop is requested")
        self.assertFalse(hb.exists() or stop.exists())

    def test_a_stale_stop_file_does_not_kill_a_new_lease(self):
        hb, stop = self.job / "y.hb", self.job / "y.stop"
        stop.write_text("old")
        with sc.Lease(hb, stop, interval=0.05):
            self.assertFalse(stop.exists())

    def test_check_command_probes_then_returns_a_supervised_argv(self):
        argv = self.provider.check_command(["pytest", "-q"], self.repo, self.job / "c.stop", self.job / "c.hb")
        self.assertEqual(argv[:5], [str(self.dc), "exec", "--no-start", str(self.repo.resolve()), "--"])
        self.assertEqual(argv[-2:], ["pytest", "-q"])
        self.assertEqual(argv[argv.index("env") + 1], "pytest")  # no env pairs for checks
        with self.assertRaisesRegex(sandbox_provider.SandboxError, "not visible"):
            self.provider.check_command(["x"], self.base / "missing", self.job / "c.stop", self.job / "c.hb")


class Cli(Base):
    def run_cli(self, *args, env=None):
        args = list(args)
        split = args.index("--") if "--" in args else len(args)
        args[split:split] = ["--repo", str(self.repo)]  # options must precede the "--" command
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "sandbox_provider.py"), *args],
                              capture_output=True, env=env or os.environ.copy(), timeout=60)

    def test_name_and_wrap_output_is_nul_separated_argv(self):
        self.assertEqual(self.run_cli("name").stdout.decode().strip(), "devcontainer")
        done = self.run_cli("wrap", "--worker", "claude", "--job-id", "j1", "--job-dir", str(self.job), "--role", "worker",
                            "--", "claude", "-p", "a b\nc")
        self.assertEqual(done.returncode, 0, done.stderr)
        parts = done.stdout.decode().split("\0")
        self.assertEqual(parts[-1], "")
        self.assertEqual(parts[-4:-1], ["claude", "-p", "a b\nc"], "arguments with spaces and newlines survive")

    def test_wrap_and_prepare_errors_exit_nonzero_with_the_reason(self):
        bad = self.run_cli("wrap", "--worker", "grok", "--job-dir", str(self.job), "--", "grok")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("cannot run in a container", bad.stderr.decode())
        os.environ["FAKE_DC_EXEC_FAIL"] = "1"
        fake_agent(self.bin, "claude", "")
        prep = self.run_cli("prepare", "--worker", "claude", "--job-dir", str(self.job))
        self.assertEqual(prep.returncode, 1)
        self.assertIn("rig env up", prep.stderr.decode())

    def test_host_provider_wrap_is_identity(self):
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")
        done = self.run_cli("wrap", "--worker", "grok", "--job-dir", str(self.job), "--", "grok", "--x")
        self.assertEqual(done.stdout, b"grok\0--x\0")


if __name__ == "__main__":
    unittest.main()
