#!/usr/bin/env python3
"""Phase 4.1: the shell without tmux (Logs tab data, dc exec shell command, --no-tmux)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import env_shell  # noqa: E402
import local_api  # noqa: E402
import rig_shell  # noqa: E402
from test_worktree_jobs import git, make_repo  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "dc"
FAKE = r'''#!/bin/sh
echo "$*" >> "$FAKE_DC_LOG"
case "$1" in
  --version) echo "dc 0.23.0" ;;
  ls) cat "$FAKE_LS" ;;
  *) exit 64 ;;
esac
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.repo = make_repo(self.base, mode="per-job", extra='container = "per-job"\n').resolve()
        fake = self.base / "dc"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.ls = self.base / "ls.json"
        self.ls.write_text("[]")
        self.log = self.base / "dc.log"
        self.env = mock.patch.dict(os.environ, {"RIG_DC_BIN": str(fake), "FAKE_DC_LOG": str(self.log), "FAKE_LS": str(self.ls)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def rows(self, **folders):
        self.ls.write_text(json.dumps([{"id": "abc123", "name": "box", "status": status, "local_folder": folder, "kind": "devcontainer"}
                                       for folder, status in folders.items()]))


class ShellCommand(Base):
    def test_the_repository_container_when_it_is_running(self):
        self.rows(**{str(self.repo): "running"})
        plan = env_shell.plan(self.repo)
        self.assertEqual((plan["available"], plan["workspace"]), (True, str(self.repo)))
        self.assertEqual(plan["argv"][1:], ["exec", "--no-start", str(self.repo)], "never dc exec without --no-start")

    def test_a_job_with_its_own_container_gets_its_worktree(self):
        import worktrees
        folder = self.repo / ".rig" / "jobs" / "j1"
        folder.mkdir(parents=True)
        record = worktrees.create_job(self.repo, folder)
        self.rows(**{record["path"]: "running"})
        plan = env_shell.plan(self.repo, "j1")
        self.assertEqual((plan["available"], plan["argv"][-1]), (True, record["path"]))

    def test_every_refusal_says_why_and_offers_no_command(self):
        self.rows(**{str(self.repo): "exited"})
        stopped = env_shell.plan(self.repo)
        self.assertEqual((stopped["available"], stopped["argv"]), (False, []))
        self.assertIn("rig env up", stopped["reason"])
        self.assertIn("no such job", env_shell.plan(self.repo, "ghost")["reason"])
        self.assertIn("no such job", env_shell.plan(self.repo, "../x")["reason"])
        with mock.patch.dict(os.environ, {"RIG_DC_BIN": ""}), mock.patch("dc_client.shutil.which", return_value=None):
            self.assertIn("not installed", env_shell.plan(self.repo)["reason"])

    def test_through_the_api(self):
        self.rows(**{str(self.repo): "running"})
        api = local_api.Api(self.repo, "t" * 64)
        reply = api.dispatch({"jsonrpc": "2.0", "id": 1, "method": "env.shell", "params": {}, "token": "t" * 64})
        self.assertTrue(reply["result"]["available"])
        bad = api.dispatch({"jsonrpc": "2.0", "id": 2, "method": "env.shell", "params": {"id": 5}, "token": "t" * 64})
        self.assertEqual(bad["error"]["code"], local_api.INVALID_PARAMS)


class PanePlans(Base):
    def test_the_parent_plan_names_the_cli_the_directory_and_the_environment(self):
        bindir = self.base / "pbin"
        bindir.mkdir()
        (bindir / "codex").write_text("#!/bin/sh\n")
        (bindir / "codex").chmod(0o755)
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
        with mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "RIG_BIN": "/opt/rig/bin/rig"}):
            plan = rig_shell.parent_pane_plan(self.repo)
        self.assertEqual((plan["available"], plan["label"], plan["dir"]), (True, "codex", str(self.repo)))
        self.assertEqual(Path(plan["argv"][0]).name, "codex")
        self.assertEqual(plan["env"], ["RIG_SHELL_ACTIVE=1", f"RIG_REPO={self.repo}", "RIG_BIN=/opt/rig/bin/rig"])

    def test_no_parent_or_one_that_cannot_run_in_a_pane_is_explained(self):
        (self.repo / ".rig" / "harness.toml").write_text('[project]\nenabled = true\n')
        with mock.patch("harness.preferred_parent", return_value=""):
            plan = rig_shell.parent_pane_plan(self.repo)
        self.assertEqual((plan["available"], plan["argv"]), (False, []))
        self.assertIn("no parent set", plan["reason"])
        with mock.patch("harness.preferred_parent", return_value="cursor"):
            self.assertIn("cannot run in a pane", rig_shell.parent_pane_plan(self.repo)["reason"])
        with mock.patch("harness.preferred_parent", return_value="codex"), mock.patch("harness.find_worker_bin", return_value=""):
            self.assertIn("not on PATH", rig_shell.parent_pane_plan(self.repo)["reason"])

    def test_the_container_shell_plan_carries_what_a_pane_needs(self):
        self.rows(**{str(self.repo): "running"})
        plan = env_shell.plan(self.repo)
        self.assertEqual((plan["dir"], plan["label"]), (str(self.repo), "container shell"))
        self.assertIn(f"RIG_REPO={self.repo}", plan["env"])
        self.assertEqual(plan["argv"][1:3], ["exec", "--no-start"])

    def test_through_the_api_read_only(self):
        api = local_api.Api(self.repo, "t" * 64)
        rows = {r["method"]: r["mutates"] for r in api.dispatch({"jsonrpc": "2.0", "id": 1, "method": "api.methods", "params": {}, "token": "t" * 64})["result"]["methods"]}
        self.assertIs(rows["shell.parent"], False)
        reply = api.dispatch({"jsonrpc": "2.0", "id": 2, "method": "shell.parent", "params": {}, "token": "t" * 64})
        self.assertIn("available", reply["result"])


class LogFollow(Base):
    def job(self, job_id, status="running", text="line one\nline two\n"):
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({"job_id": job_id, "worker": "claude", "role": "worker", "status": status,
                                                      "dir": str(folder), "started": "2026-10-20T08:00:00Z"}))
        (folder / "stdout.log").write_text(text)

    def call(self, params):
        api = local_api.Api(self.repo, "t" * 64)
        return api.dispatch({"jsonrpc": "2.0", "id": 1, "method": "log.follow", "params": params, "token": "t" * 64})

    def test_a_named_job_returns_its_tail_as_plain_text(self):
        self.job("j1", text="alpha\nbeta\ngamma\n")
        result = self.call({"id": "j1", "lines": 2})["result"]
        self.assertEqual(result["job"]["id"], "j1")
        self.assertIn("gamma", result["text"])
        self.assertNotIn("alpha", result["text"])

    def test_without_an_id_the_job_worth_watching_is_picked(self):
        self.job("old", status="ok", text="done long ago\n")
        self.job("live", status="running", text="working\n")
        result = self.call({})["result"]
        self.assertEqual((result["job"]["id"], result["job"]["worker"]), ("live", "claude"))
        self.assertIn("working", result["text"])

    def test_no_jobs_is_an_empty_answer_not_an_error(self):
        self.assertEqual(self.call({})["result"], {"job": None, "text": "", "error": False})

    def test_bad_parameters_are_refused(self):
        for params in ({"lines": 0}, {"lines": 501}, {"lines": True}, {"id": 5}):
            self.assertEqual(self.call(params)["error"]["code"], local_api.INVALID_PARAMS, params)

    def test_owner_tokens_in_a_log_never_leave_the_api(self):
        self.job("j1", text='{"owner_token": "abcdef0123456789"}\n')
        self.assertNotIn("abcdef0123456789", json.dumps(self.call({"id": "j1"})))


class NoTmux(Base):
    def run_shell(self, *args, path):
        binary = self.base / "rig-shell"
        binary.write_text("#!/bin/sh\necho \"args=$* active=$RIG_SHELL_ACTIVE repo=$RIG_REPO\" > \"$OUT\"\n")
        binary.chmod(0o755)
        out = self.base / "out.txt"
        env = dict(os.environ, RIG_SHELL_BIN=str(binary), OUT=str(out), PATH=path)
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "rig_shell.py"), *args, "--repo", str(self.repo)],
                              capture_output=True, text=True, env=env)
        return done, out

    def test_the_board_runs_without_tmux_installed(self):
        empty = self.base / "empty"
        empty.mkdir()
        done, out = self.run_shell("start", "--no-tmux", path=str(empty))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(out.read_text().strip(), f"args=--repo {self.repo} active=1 repo={self.repo}")

    def test_print_shows_the_one_command(self):
        done, _ = self.run_shell("print", "--no-tmux", path=os.environ["PATH"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(done.stdout.strip().splitlines()), 1)
        self.assertIn("--repo", done.stdout)
        self.assertNotIn("tmux", done.stdout)

    def test_without_tmux_the_default_layout_explains_the_alternative(self):
        empty = self.base / "empty"
        empty.mkdir()
        done, _ = self.run_shell("start", path=str(empty))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("rig shell --no-tmux", done.stderr)


if __name__ == "__main__":
    unittest.main()
