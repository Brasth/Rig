"""Bootstrap behavior with all package and tmux processes isolated by mocks."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import signal
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_tmux", ROOT / "scripts/install-tmux.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class InstallTmux(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.which = self.stack.enter_context(patch.object(bootstrap.shutil, "which", return_value=None))
        self.system = self.stack.enter_context(patch.object(bootstrap.platform, "system", return_value="Linux"))
        self.uid = self.stack.enter_context(patch.object(bootstrap.os, "geteuid", return_value=0))
        self.process = self.stack.enter_context(patch.object(bootstrap.subprocess, "run"))
        self.popen = self.stack.enter_context(patch.object(bootstrap.subprocess, "Popen"))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))

    def binaries(self, *names):
        self.which.side_effect = lambda name: "/fake/" + name if name in names else None

    def test_version_acceptance(self):
        self.binaries("tmux")
        for version, accepted in [("tmux 3.2a", False), ("tmux 3.3", True),
                                  ("tmux 3.4a", True), ("tmux 4.0", True), ("unknown", False)]:
            with self.subTest(version=version):
                self.process.return_value = subprocess.CompletedProcess([], 0, version)
                self.assertEqual(bootstrap.compatible(), accepted)
        self.assertEqual(self.process.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.process.side_effect = subprocess.TimeoutExpired("tmux", 5)
        self.assertFalse(bootstrap.compatible())

    def test_skip_and_compatible_do_not_install(self):
        with patch.object(bootstrap, "commands") as commands:
            os.environ["RIG_SKIP_TMUX_INSTALL"] = "1"
            self.assertEqual(bootstrap.main(), 0)
            commands.assert_not_called()
            del os.environ["RIG_SKIP_TMUX_INSTALL"]
            with patch.object(bootstrap, "compatible", return_value=True):
                self.assertEqual(bootstrap.main(), 0)
            commands.assert_not_called()

    def test_brew_install_and_upgrade(self):
        self.system.return_value = "Darwin"
        for present, action in [(False, "install"), (True, "upgrade")]:
            self.binaries("brew", *(["tmux"] if present else []))
            self.assertEqual(bootstrap.commands(), [["/fake/brew", action, "tmux"]])

    def test_linux_managers(self):
        self.binaries("apt-get")
        operations = bootstrap.commands()
        self.assertEqual(operations, [["/fake/apt-get", "update"], ["/fake/apt-get", "install", "-y", "tmux"]])
        self.binaries("dnf")
        self.assertEqual(bootstrap.commands(), [["/fake/dnf", "install", "-y", "tmux"]])
        self.binaries("dnf", "tmux")
        self.assertEqual(bootstrap.commands(), [["/fake/dnf", "upgrade", "-y", "tmux"]])

    def test_no_privileges_or_unsupported_platform(self):
        self.uid.return_value = 501
        self.binaries("apt-get")
        self.assertEqual(bootstrap.commands(), [])
        self.binaries("apt-get", "sudo")
        self.process.return_value.returncode = 1
        self.assertEqual(bootstrap.commands(), [])
        self.assertEqual(self.process.call_args.args[0], ["/fake/sudo", "-n", "true"])
        self.system.return_value = "Other"
        self.assertEqual(bootstrap.commands(), [])

    def test_rechecks_version_and_fails_soft(self):
        for upgraded in (False, True):
            with self.subTest(upgraded=upgraded), patch.object(bootstrap, "compatible", side_effect=[False, upgraded]), patch.object(bootstrap, "commands", return_value=[["fake", "install"]]), patch.object(bootstrap, "run", return_value=True) as run:
                self.assertEqual(bootstrap.main(), 0)
                self.assertEqual(run.call_args.args[1]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertIn("continuing Rig installation", self.output.getvalue())
        with patch.object(bootstrap, "compatible", return_value=False), patch.object(bootstrap, "commands", side_effect=OSError("unavailable")):
            self.assertEqual(bootstrap.main(), 0)

    def test_timeout_terminates_process_group(self):
        process = MagicMock()
        process.pid = 123456
        process.wait.side_effect = [subprocess.TimeoutExpired("fake", 300), None, None]
        self.popen.return_value.__enter__.return_value = process
        with patch.object(bootstrap.os, "killpg") as killpg:
            self.assertFalse(bootstrap.run(["fake"], {}))
            self.assertEqual([call.args for call in killpg.call_args_list], [(123456, signal.SIGTERM), (123456, signal.SIGKILL)])
        self.assertEqual(self.popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(self.popen.call_args.kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
