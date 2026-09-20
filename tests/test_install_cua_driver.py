"""Opt-in Cua Driver installer: no live network, preference and flag matrix."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install_cua_driver", ROOT / "scripts" / "install-cua-driver.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class InstallCuaDriver(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = self.stack.enter_context(patch.dict(os.environ, {
            "HOME": str(self.home),
            "RIG_HOME": str(self.home / ".rig"),
        }, clear=True))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))

    def pref(self):
        path = self.home / ".rig" / "cua-driver.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def test_skip_env_does_not_write_preference(self):
        os.environ["RIG_SKIP_CUA_DRIVER"] = "1"
        with patch.object(mod, "run_upstream") as upstream:
            self.assertEqual(mod.main([]), 0)
            upstream.assert_not_called()
        self.assertIsNone(self.pref())
        self.assertIn("RIG_SKIP_CUA_DRIVER=1", self.output.getvalue())

    def test_no_tty_skips_without_declining(self):
        with patch.object(mod, "has_tty", return_value=False), patch.object(mod, "run_upstream") as upstream:
            self.assertEqual(mod.main([]), 0)
            upstream.assert_not_called()
        self.assertIsNone(self.pref())
        self.assertIn("no TTY", self.output.getvalue())

    def test_yes_env_installs_and_remembers(self):
        os.environ["RIG_INSTALL_CUA_DRIVER"] = "1"
        with patch.object(mod, "run_upstream", return_value=True) as upstream, \
             patch.object(mod, "print_success"):
            self.assertEqual(mod.main([]), 0)
            upstream.assert_called_once()
        pref = self.pref()
        self.assertTrue(pref["opt_in"])
        self.assertEqual(pref["source"], "env")
        self.assertEqual(oct(os.stat(self.home / ".rig" / "cua-driver.json").st_mode & 0o777), "0o600")

    def test_flag_no_writes_declined(self):
        with patch.object(mod, "run_upstream") as upstream:
            self.assertEqual(mod.main(["--no-cua-driver", "--shell-ui"]), 0)
            upstream.assert_not_called()
        pref = self.pref()
        self.assertFalse(pref["opt_in"])
        self.assertEqual(pref["source"], "flag")

    def test_declined_preference_skips_without_prompt(self):
        mod.write_preference(False, "prompt")
        with patch.object(mod, "prompt_tty") as prompt, patch.object(mod, "run_upstream") as upstream:
            self.assertEqual(mod.main([]), 0)
            prompt.assert_not_called()
            upstream.assert_not_called()
        self.assertIn("declined", self.output.getvalue())

    def test_opt_in_true_upgrades_without_prompt(self):
        mod.write_preference(True, "prompt")
        with patch.object(mod, "prompt_tty") as prompt, \
             patch.object(mod, "run_upstream", return_value=True) as upstream, \
             patch.object(mod, "print_success"):
            self.assertEqual(mod.main([]), 0)
            prompt.assert_not_called()
            upstream.assert_called_once()

    def test_skip_env_wins_this_run_without_rewriting_opt_in(self):
        mod.write_preference(True, "prompt")
        os.environ["RIG_SKIP_CUA_DRIVER"] = "1"
        with patch.object(mod, "run_upstream") as upstream:
            self.assertEqual(mod.main([]), 0)
            upstream.assert_not_called()
        self.assertTrue(self.pref()["opt_in"])

    def test_upstream_failure_still_zero(self):
        os.environ["RIG_INSTALL_CUA_DRIVER"] = "1"
        with patch.object(mod, "run_upstream", return_value=False):
            self.assertEqual(mod.main([]), 0)
        self.assertIn("continuing Rig installation", self.output.getvalue())

    def test_run_upstream_timeout_is_failure(self):
        with patch.object(mod.shutil, "which", side_effect=lambda name: "/bin/" + name), \
             patch.object(mod.subprocess, "run", side_effect=subprocess.TimeoutExpired("bash", 120)):
            self.assertFalse(mod.run_upstream())

    def test_tty_yes_and_no(self):
        with patch.object(mod, "has_tty", return_value=True), \
             patch.object(mod, "prompt_tty", return_value=True), \
             patch.object(mod, "run_upstream", return_value=True), \
             patch.object(mod, "print_success"):
            self.assertEqual(mod.main([]), 0)
        self.assertTrue(self.pref()["opt_in"])
        mod.preference_path().unlink()
        self.output.truncate(0)
        self.output.seek(0)
        with patch.object(mod, "has_tty", return_value=True), \
             patch.object(mod, "prompt_tty", return_value=False), \
             patch.object(mod, "run_upstream") as upstream:
            self.assertEqual(mod.main([]), 0)
            upstream.assert_not_called()
        self.assertFalse(self.pref()["opt_in"])


if __name__ == "__main__":
    unittest.main()
