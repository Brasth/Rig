#!/usr/bin/env python3
import os
import shlex
import shutil
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

import local_api  # noqa: E402
import rig_shell  # noqa: E402


def make_repo(root: Path, parent: str = "codex") -> Path:
    (root / ".git").mkdir(exist_ok=True)
    (root / ".rig").mkdir(exist_ok=True)
    (root / ".rig" / "harness.toml").write_text(f'parent = "{parent}"\n\n[project]\nenabled = true\n\n[workers]\ngrok = true\n')
    return root


class PlanIsPure(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.td.name))

    def tearDown(self):
        self.td.cleanup()

    def test_three_panes_in_one_window_with_parent_focused(self):
        steps = rig_shell.plan(self.repo, ["/bin/codex"], "/opt/rig-shell")
        verbs = [s[0] for s in steps]
        self.assertEqual(verbs.count("new-session"), 1)
        self.assertEqual(verbs.count("split-window"), 2)
        self.assertEqual(steps[-1][:3], ["select-pane", "-t", f"{rig_shell.session_name(self.repo)}:rig.0"])
        titles = [s[-1] for s in steps if s[0] == "select-pane" and "-T" in s]
        self.assertEqual(titles, ["parent", "board", "log"])

    def test_each_pane_runs_the_right_program_in_the_repo(self):
        steps = rig_shell.plan(self.repo, ["/bin/codex", "--flag"], "/opt/rig-shell")
        commands = [shlex.split(s[-1]) for s in steps if s[0] in ("new-session", "split-window")]
        parent, board, logs = commands
        self.assertEqual(parent[-2:], ["/bin/codex", "--flag"])
        self.assertEqual(board[-3:], ["/opt/rig-shell", "--repo", str(self.repo.resolve())])
        self.assertEqual(logs[-3:-1], ["logs", "--repo"])
        for command in commands:
            self.assertIn("RIG_SHELL_ACTIVE=1", command)
            self.assertIn(f"RIG_REPO={self.repo.resolve()}", command)
        for step in steps:
            if step[0] in ("new-session", "split-window"):
                self.assertEqual(step[step.index("-c") + 1], str(self.repo.resolve()))

    def test_paths_with_spaces_and_quotes_cannot_break_out(self):
        nasty = Path(self.td.name) / "it's a $(touch pwned) repo"
        make_repo(nasty.mkdir() or nasty)
        steps = rig_shell.plan(nasty, ["/bin/codex"], "/opt/rig shell")
        for step in (s for s in steps if s[0] in ("new-session", "split-window")):
            words = shlex.split(step[-1])
            self.assertTrue(any(str(nasty.resolve()) in w for w in words))
            self.assertNotIn("$(touch", " ".join(w for w in words if "repo" not in w))
        self.assertFalse((Path.cwd() / "pwned").exists())

    def test_session_name_is_stable_per_repo_and_differs_between_repos(self):
        other = make_repo(Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, other, True)
        self.assertEqual(rig_shell.session_name(self.repo), rig_shell.session_name(self.repo))
        self.assertNotEqual(rig_shell.session_name(self.repo), rig_shell.session_name(other))


class Resolution(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.td.name))

    def tearDown(self):
        self.td.cleanup()

    def test_override_wins_and_is_split_like_a_shell(self):
        self.assertEqual(rig_shell.parent_command(self.repo, "", "sleep 5 'a b'"), ["sleep", "5", "a b"])

    def test_unrunnable_or_missing_parents_are_refused_with_a_reason(self):
        for name in ("cursor", "devin", "nonsense"):
            with self.assertRaisesRegex(rig_shell.ShellError, "cannot run in a pane"):
                rig_shell.parent_command(self.repo, name)
        with mock.patch.object(rig_shell.harness, "find_worker_bin", return_value=""):
            with self.assertRaisesRegex(rig_shell.ShellError, "not on PATH"):
                rig_shell.parent_command(self.repo, "codex")
        make_repo(self.repo, parent="")
        with self.assertRaisesRegex(rig_shell.ShellError, "no parent set"):
            rig_shell.parent_command(self.repo)

    def test_parent_comes_from_config_when_not_given(self):
        with mock.patch.object(rig_shell.harness, "find_worker_bin", return_value="/usr/bin/codex") as find:
            self.assertEqual(rig_shell.parent_command(self.repo), ["/usr/bin/codex"])
        find.assert_called_once_with("codex")

    def test_shell_binary_env_override_and_helpful_failure(self):
        fake = Path(self.td.name) / "rig-shell"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        with mock.patch.dict(os.environ, {"RIG_SHELL_BIN": str(fake)}):
            self.assertEqual(rig_shell.shell_binary(), str(fake))
        empty = Path(self.td.name) / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"RIG_SHELL_BIN": "", "RIG_HOME": str(empty), "PATH": str(empty)}), \
                mock.patch.object(rig_shell, "ROOT", empty):
            with self.assertRaisesRegex(rig_shell.ShellError, "go build"):
                rig_shell.shell_binary()

    def test_server_args_isolation(self):
        self.assertEqual(rig_shell.server_args({"RIG_SHELL_TMUX_SOCKET": "t1"}), ["-L", "t1", "-f", "/dev/null"])
        self.assertEqual(rig_shell.server_args({"TMUX": "/tmp/x,1,0"}), [])
        self.assertEqual(rig_shell.server_args({}), ["-L", "rig-shell", "-f", "/dev/null"])


class LogPane(unittest.TestCase):
    def test_pick_job_prefers_needs_input_then_running_then_newest(self):
        rows = [{"id": "a", "label": "Failed"}, {"id": "b", "label": "Running"}, {"id": "c", "label": "Needs input"}]
        self.assertEqual(rig_shell.pick_job(rows)["id"], "c")
        self.assertEqual(rig_shell.pick_job(rows[:2])["id"], "b")
        self.assertEqual(rig_shell.pick_job(rows[:1])["id"], "a")
        self.assertIsNone(rig_shell.pick_job([]))

    def test_log_frame_states(self):
        repo = Path("/x")
        with mock.patch.object(local_api, "call", side_effect=ConnectionError("down")):
            self.assertIn("not running", rig_shell.log_frame(repo))
        view = {"result": {"view": {"jobs": []}}}
        with mock.patch.object(local_api, "call", return_value=view):
            self.assertIn("No jobs yet", rig_shell.log_frame(repo))
        replies = [{"result": {"view": {"jobs": [{"id": "j1", "label": "Running", "worker": "codex"}]}}},
                   {"result": {"text": "line one\nline two"}}]
        with mock.patch.object(local_api, "call", side_effect=replies) as call:
            frame = rig_shell.log_frame(repo)
        self.assertIn("Running · j1 · codex", frame)
        self.assertIn("line two", frame)
        self.assertEqual(call.call_args_list[1].args[1:], ("jobs.log", {"id": "j1", "lines": 30}))
        with mock.patch.object(local_api, "call", return_value={"error": {"message": "Rig is disabled"}}):
            self.assertIn("Rig is disabled", rig_shell.log_frame(repo))


def tmux_ok() -> bool:
    try:
        rig_shell.tmux_binary()
        return True
    except rig_shell.ShellError:
        return False


@unittest.skipUnless(tmux_ok(), "tmux 3.3+ not available")
class RealTmuxLayout(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.td.name))
        fake = Path(self.td.name) / "rig-shell"
        fake.write_text("#!/bin/sh\nexec sleep 60\n")
        fake.chmod(0o755)
        self.socket = f"rigtest{os.getpid()}"
        self.env = mock.patch.dict(os.environ, {"RIG_SHELL_TMUX_SOCKET": self.socket, "RIG_SHELL_BIN": str(fake)})
        self.env.start()
        os.environ.pop("TMUX", None)
        self.tmux = rig_shell.tmux_binary()

    def tearDown(self):
        subprocess.run([self.tmux, "-L", self.socket, "kill-server"], capture_output=True)
        for pid in subprocess.run(["pgrep", "-f", f"local_api.py serve --repo {self.repo.resolve()}"],
                                  capture_output=True, text=True).stdout.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
        self.env.stop()
        self.td.cleanup()

    def panes(self):
        out = rig_shell.tmux(self.tmux, ["list-panes", "-t", rig_shell.session_name(self.repo) + ":rig", "-F",
                                         "#{pane_index} #{pane_title} #{pane_width} #{pane_height} #{pane_current_command}"]).stdout
        return [line.split() for line in out.strip().splitlines()]

    def test_start_builds_three_titled_panes_is_idempotent_and_kills_cleanly(self):
        session = rig_shell.start(self.repo, parent_cmd="sleep 60", attach=False, layout="split")
        self.assertEqual(session, rig_shell.session_name(self.repo))
        panes = self.panes()
        self.assertEqual([p[1] for p in panes], ["parent", "board", "log"])
        parent, board, logs = panes
        self.assertGreater(int(parent[2]), int(board[2]), "parent pane is the wide one")
        self.assertEqual(board[2], logs[2], "board and log share the right column")
        self.assertLess(int(logs[3]), int(board[3]) + 1)
        rig_shell.start(self.repo, parent_cmd="sleep 60", attach=False, layout="split")
        self.assertEqual(len(self.panes()), 3, "a second start must reuse the session")
        self.assertTrue(rig_shell.kill(self.repo))
        self.assertFalse(rig_shell.kill(self.repo))

    def test_failed_layout_leaves_no_session_behind(self):
        with mock.patch.object(rig_shell, "plan", return_value=[["new-session", "-d", "-s", rig_shell.session_name(self.repo), "sleep 60"],
                                                                 ["split-window", "-t", "nonexistent:9.9", "sleep 1"]]):
            with self.assertRaises(rig_shell.ShellError):
                rig_shell.start(self.repo, parent_cmd="sleep 60", attach=False, layout="split")
        self.assertFalse(rig_shell.session_exists(self.tmux, rig_shell.session_name(self.repo)))

    def test_cli_print_and_detach(self):
        env = dict(os.environ)
        script = str(ROOT / "scripts" / "rig_shell.py")
        printed = subprocess.run([sys.executable, script, "print", "--repo", str(self.repo), "--layout", "split", "--parent-cmd", "sleep 60"],
                                 env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertEqual(len(printed.stdout.strip().splitlines()), 11)
        started = subprocess.run([sys.executable, script, "--repo", str(self.repo), "--layout", "split", "--parent-cmd", "sleep 60", "--detach"],
                                 env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn("is ready", started.stdout)
        self.assertEqual(len(self.panes()), 3)

    def test_the_default_layout_is_one_window_holding_the_board_as_the_design_screen(self):
        session = rig_shell.start(self.repo, attach=False)
        panes = self.panes()
        self.assertEqual(len(panes), 1, "tmux only hosts the board; the board draws every column itself")
        steps = rig_shell.layout_steps(self.repo, "board", "", "")
        command = steps[0][-1]
        for want in ("RIG_SHELL_START=Session", "RIG_EXPERIMENT_CHAT=1", "--repo"):
            self.assertIn(want, command)
        self.assertTrue(rig_shell.kill(self.repo), session)

    def test_the_board_layout_checks_the_parent_by_name_only(self):
        with self.assertRaisesRegex(rig_shell.ShellError, "cannot run in the board"):
            rig_shell.layout_steps(self.repo, "board", "bash", "")
        with self.assertRaisesRegex(rig_shell.ShellError, "--layout split"):
            rig_shell.layout_steps(self.repo, "board", "", "sleep 60")
        steps = rig_shell.layout_steps(self.repo, "board", "claude", "")  # need not be installed: the board reports that
        self.assertIn("RIG_SHELL_PARENT=claude", steps[0][-1])

    def test_disabled_or_uninitialized_projects_are_refused(self):
        (self.repo / ".rig" / "harness.toml").write_text("[project]\nenabled = false\n")
        with self.assertRaises(ValueError):
            rig_shell.start(self.repo, parent_cmd="sleep 1", attach=False, layout="split")


if __name__ == "__main__":
    unittest.main()
