#!/usr/bin/env python3
"""Warnings about what will trip the next turn: known, cheap, never blocking."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import shell_doctor  # noqa: E402
import turn_warnings  # noqa: E402


class TurnWarnings(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = Path(self.td.name).resolve() / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "codex"\n\n[project]\nenabled = true\n')
        self.home = Path(self.td.name) / "home"
        (self.home / ".codex").mkdir(parents=True)
        home = mock.patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        turn_warnings._cache.clear()
        turn_warnings._running.clear()

    def test_a_signed_out_server_names_the_fix(self):
        row = shell_doctor.finding("codex-mcp-clerk", "warning", "codex: clerk is not signed in", "x")
        with mock.patch.object(shell_doctor, "codex_servers_signed_out", return_value=[row]), \
                mock.patch.object(shell_doctor, "signed_out", return_value=None):
            self.assertEqual(turn_warnings.compute(self.repo), ["clerk is not signed in · type /mcp login clerk"])

    def test_a_project_config_codex_ignores_is_named_until_the_project_is_trusted(self):
        (self.repo / ".codex").mkdir()
        (self.repo / ".codex" / "config.toml").write_text('[mcp_servers.asana]\nurl = "https://x"\n')
        self.assertIn("ignored", turn_warnings.untrusted_project_config(self.repo)[0])
        (self.home / ".codex" / "config.toml").write_text(f'[projects."{self.repo}"]\ntrust_level = "trusted"\n')
        self.assertEqual(turn_warnings.untrusted_project_config(self.repo), [])

    def test_no_project_config_no_warning(self):
        self.assertEqual(turn_warnings.untrusted_project_config(self.repo), [])

    def test_current_answers_at_once_and_refreshes_in_the_background(self):
        started = []

        def slow(repo):
            started.append(time.monotonic())
            return ["late"]

        with mock.patch.object(turn_warnings, "compute", side_effect=slow):
            self.assertEqual(turn_warnings.current(self.repo), [], "the first ask never waits for the checks")
            for _ in range(50):
                time.sleep(0.05)
                if turn_warnings.current(self.repo) == ["late"]:
                    break
            self.assertEqual(turn_warnings.current(self.repo), ["late"])
            self.assertEqual(len(started), 1, "one refresh, not one per ask")

    def test_a_check_that_breaks_gives_no_warning(self):
        with mock.patch.object(shell_doctor, "codex_servers_signed_out", side_effect=RuntimeError("boom")):
            self.assertEqual(turn_warnings.compute(self.repo), [])


if __name__ == "__main__":
    unittest.main()
