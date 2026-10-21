#!/usr/bin/env python3
"""The board's colour theme: kept for all repos or one repo, read with the repo's choice first, and over the API."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_api  # noqa: E402
import rig_shell  # noqa: E402
import ui_theme  # noqa: E402


class Theme(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.repo = base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        self.harness = self.repo / ".rig" / "harness.toml"
        self.harness.write_text('parent = "claude"\n\n[project]\nenabled = true\n')
        self.home = base / "home" / ".rig"
        self.env = mock.patch.dict(os.environ, {"RIG_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def test_the_default_then_all_repos_then_this_repo(self):
        self.assertEqual(ui_theme.read(self.repo), {"theme": "rig", "scope": "default", "themes": list(ui_theme.THEMES)})
        self.assertEqual(ui_theme.save(self.repo, "dracula")["scope"], "all")
        self.assertEqual((self.home / "config.toml").read_text(), '[ui]\ntheme = "dracula"\n')
        self.assertEqual(ui_theme.read(self.repo)["theme"], "dracula")
        ui_theme.save(self.repo, "Light", "repo")
        self.assertEqual(ui_theme.read(self.repo)["theme"], "light", "the repo's choice wins")
        self.assertIn('[ui]\ntheme = "light"\n', self.harness.read_text())
        ui_theme.save(self.repo, "material", "repo")
        self.assertEqual(self.harness.read_text().count("theme ="), 1, "replaced, never repeated")
        import harness
        self.assertEqual((harness.parse_harness(self.harness)["parent"], harness.parse_harness(self.harness)["project"]["enabled"]),
                         ("claude", True), "the rest of the harness is untouched")

    def test_refusals(self):
        with self.assertRaisesRegex(ui_theme.ThemeError, "not solarized"):
            ui_theme.save(self.repo, "solarized")
        with self.assertRaisesRegex(ui_theme.ThemeError, "all or repo"):
            ui_theme.save(self.repo, "dark", "everywhere")
        self.harness.unlink()
        with self.assertRaisesRegex(ui_theme.ThemeError, "rig init"):
            ui_theme.save(self.repo, "dark", "repo")
        self.assertFalse(self.harness.exists(), "a theme never creates the harness")
        (self.home).mkdir(parents=True)
        (self.home / "config.toml").write_text('[ui]\ntheme = "neon"\n')
        self.assertEqual(ui_theme.read(self.repo)["theme"], "rig", "an unknown saved theme is the default")

    def test_over_the_api(self):
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda method, params=None: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
                                                          "token": "t" * 64})
        rows = {r["method"]: r["mutates"] for r in call("api.methods")["result"]["methods"]}
        self.assertEqual((rows["ui.theme"], rows["ui.set_theme"]), (False, True))
        self.assertEqual(call("ui.set_theme", {"theme": "dark", "scope": "repo"})["result"]["theme"], "dark")
        self.assertEqual(call("ui.theme")["result"]["scope"], "repo")
        for bad in ({"theme": 5}, {"theme": "dark", "scope": "x"}, {"theme": "neon"}):
            self.assertEqual(call("ui.set_theme", bad)["error"]["code"], local_api.INVALID_PARAMS, bad)

    def test_rig_shell_theme_is_for_one_board(self):
        self.assertIn("RIG_SHELL_THEME=light", rig_shell.board_env(self.repo, "", "light"))
        with self.assertRaisesRegex(rig_shell.ShellError, "--theme is one of"):
            rig_shell.board_env(self.repo, "", "neon")


if __name__ == "__main__":
    unittest.main()
