#!/usr/bin/env python3
"""rig dsh-worker: the brief, the MCP patch, the environment and the refusals, with a stand-in `dsh`."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rig_dsh  # noqa: E402

SERVERS = {"rig": {"command": "/usr/bin/python3", "args": ["/r/rig_mcp.py"], "env": {"RIG_JOB_ID": "j1"}},
           "rig-ask": {"command": "/usr/bin/python3", "args": ["/r/rig_mcp.py"], "env": {"RIG_JOB_ID": "j1"}}}


class Pieces(unittest.TestCase):
    def test_the_patch_inserts_one_mcp_row_per_rig_server_and_nothing_else(self):
        rows = json.loads(rig_dsh.render_patch(SERVERS))
        self.assertEqual([r["insert"]["config"]["serverName"] for r in rows], ["rig", "rig-ask"])
        config = rows[0]["insert"]["config"]
        self.assertEqual((rows[0]["insert"]["name"], config["transport"], config["command"], config["args"], config["env"]),
                         ("@deepseek-ai/dsh-mcp-client", "stdio", "/usr/bin/python3", ["/r/rig_mcp.py"], {"RIG_JOB_ID": "j1"}))

    def test_the_command_is_the_headless_profile_with_the_patch_and_the_task_as_one_argument(self):
        self.assertEqual(rig_dsh.command_for("/bin/dsh", "/j/dsh.patch.yml", "do it\nnow"),
                         ["/bin/dsh", "--profile", "headless", "--patch", "/j/dsh.patch.yml", "do it\nnow"])

    def test_telemetry_is_off_and_the_permission_mode_set(self):
        env = rig_dsh.environment("host", {"PATH": "/bin"})
        self.assertEqual((env["DSH_TELEMETRY_MODE"], env["DSH_PERMISSION_MODE"], env["PATH"]), ("DISABLED", "workspace-write", "/bin"))

    def test_a_long_brief_becomes_a_pointer_to_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            brief = Path(td) / "brief.md"
            brief.write_text("x" * (rig_dsh.MAX_TASK_BYTES + 1))
            self.assertIn(str(brief), rig_dsh.task_text(brief))
            brief.write_text("short brief")
            self.assertEqual(rig_dsh.task_text(brief), "short brief")


class Install(unittest.TestCase):
    def test_install_writes_a_valid_adapter_into_the_users_directory(self):
        import adapters
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = td
            try:
                folder = rig_dsh.install_adapter()
                self.assertEqual(folder, Path(td) / "rig" / "adapters" / "deepseek")
                manifest = adapters.validate(json.loads((folder / "adapter.json").read_text()), directory=folder)
                self.assertEqual((manifest["name"], manifest["binary"]), ("deepseek", "rig"))
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME")
                else:
                    os.environ["XDG_CONFIG_HOME"] = old


class Doctor(unittest.TestCase):
    def test_the_deepseek_check_appears_only_with_the_adapter_and_says_what_is_missing(self):
        import doctor
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            env = {"XDG_CONFIG_HOME": td, "DSH_HOME": str(Path(td) / "dsh-home"), "DEEPSEEK_API_KEY": ""}
            with mock.patch.dict(os.environ, env):
                self.assertIsNone(doctor.deepseek_check(), "no adapter, no row")
                rig_dsh.install_adapter()
                with mock.patch("shutil.which", return_value=None):
                    row = doctor.deepseek_check()
                self.assertEqual(row["status"], "missing")
                self.assertIn("dsh is not on PATH", row["detail"])
                with mock.patch("shutil.which", return_value="/bin/dsh"):
                    self.assertIn("no DeepSeek credentials", doctor.deepseek_check()["detail"])
                    with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k"}):
                        self.assertEqual(doctor.deepseek_check()["status"], "ready")

    def test_settings_counts_the_worker_installed_when_dsh_is(self):
        import ui_settings
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            (repo / ".rig").mkdir()
            (repo / ".rig" / "harness.toml").write_text('parent = "claude"\n\n[project]\nenabled = true\n')
            env = {"XDG_CONFIG_HOME": td, "RIG_HOME": str(Path(td) / "home")}
            with mock.patch.dict(os.environ, env):
                rig_dsh.install_adapter()
                import harness
                import importlib
                importlib.reload(harness)  # WORKERS is read once at import: pick up the adapter just installed
                try:
                    with mock.patch("shutil.which", side_effect=lambda n: "/bin/dsh" if n == "dsh" else None):
                        self.assertIn("deepseek", ui_settings.read(repo)["installed"])
                    with mock.patch("shutil.which", return_value=None):
                        self.assertNotIn("deepseek", ui_settings.read(repo)["installed"])
                finally:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                    importlib.reload(harness)


class Running(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.work, self.job = base / "work", base / "job"
        self.work.mkdir()
        self.brief = base / "brief.md"
        self.brief.write_text("Add a test.")
        self.mcp = base / "mcp.json"
        self.mcp.write_text(json.dumps({"mcpServers": SERVERS}))
        self.dsh = base / "dsh"
        # a stand-in: records how it was called, prints an answer
        self.dsh.write_text(f'#!/bin/sh\n{{ echo "cwd=$(pwd)"; echo "telemetry=$DSH_TELEMETRY_MODE"; echo "perm=$DSH_PERMISSION_MODE"; for a in "$@"; do echo "arg=$a"; done; }} > "{base}/called.txt"\necho "final answer"\n')
        self.dsh.chmod(self.dsh.stat().st_mode | stat.S_IEXEC)
        self.env = {**os.environ, "DSH_BIN": str(self.dsh), "DEEPSEEK_API_KEY": "k"}

    def tearDown(self):
        self.td.cleanup()

    def run_worker(self, *extra, env=None):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "rig_dsh.py"), "--work", str(self.work), "--brief-file", str(self.brief),
                               "--job-dir", str(self.job), "--mcp-config", str(self.mcp), *extra],
                              capture_output=True, text=True, env=env or self.env, timeout=30)

    def test_it_runs_dsh_in_the_checkout_and_passes_its_answer_through(self):
        done = self.run_worker("--mode", "host")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "final answer"), done.stderr)
        called = (self.job.parent / "called.txt").read_text().splitlines()
        self.assertIn(f"cwd={self.work.resolve()}", called)
        self.assertIn("telemetry=DISABLED", called)
        self.assertIn("perm=workspace-write", called)
        self.assertIn("arg=Add a test.", called)
        self.assertTrue((self.job / "dsh.patch.yml").is_file())
        self.assertEqual(len(json.loads((self.job / "dsh.patch.yml").read_text())), 2)

    def test_a_model_is_noted_not_passed(self):
        done = self.run_worker("--model", "deepseek-v4")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("takes its model from its profile", done.stderr)
        self.assertNotIn("deepseek-v4", (self.job.parent / "called.txt").read_text())

    def test_refusals_say_why_and_run_nothing(self):
        no_dsh = {k: v for k, v in self.env.items() if k != "DSH_BIN"}
        no_dsh["PATH"] = "/nonexistent"
        done = self.run_worker(env=no_dsh)
        self.assertEqual(done.returncode, 2)
        self.assertIn("dsh is not installed", done.stderr)
        no_key = {k: v for k, v in self.env.items() if k != "DEEPSEEK_API_KEY"}
        no_key["DSH_HOME"] = str(self.job.parent / "empty-dsh-home")
        done = self.run_worker(env=no_key)
        self.assertEqual(done.returncode, 2)
        self.assertIn("no DeepSeek credentials", done.stderr)
        self.mcp.write_text("{}")
        done = self.run_worker()
        self.assertEqual(done.returncode, 2)
        self.assertIn("names no MCP servers", done.stderr)
        self.assertFalse((self.job.parent / "called.txt").exists())


if __name__ == "__main__":
    unittest.main()
