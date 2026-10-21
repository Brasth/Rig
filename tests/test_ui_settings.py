#!/usr/bin/env python3
"""The board's Settings screen: real settings only, checked values, and the one file each is kept in."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harness  # noqa: E402
import local_api  # noqa: E402
import ui_settings  # noqa: E402


class Settings(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.repo = base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".rig").mkdir()
        self.harness = self.repo / ".rig" / "harness.toml"
        self.harness.write_text('parent = "claude"\n\n[project]\nenabled = true\n\n[workers]\ngrok = true\n')
        self.env = mock.patch.dict(os.environ, {"RIG_HOME": str(base / "home" / ".rig")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def test_what_the_screen_shows(self):
        view = ui_settings.read(self.repo)
        self.assertEqual((view["parent"], view["enabled"], view["max_running"], view["objective"], view["sandbox"], view["worktrees"]),
                         ("claude", True, 3, "balanced", "host", "off"))
        self.assertTrue(view["workers"]["grok"])
        self.assertFalse(view["workers"]["codex"])
        self.assertIn("[workers]", view["harness_text"])
        self.assertEqual((view["bell"], view["bell_scope"]), (False, "default"))

    def test_changes_land_in_the_harness_and_parse_back(self):
        ui_settings.save(self.repo, "workers.codex", True)
        ui_settings.save(self.repo, "queue.max_running", "2")
        ui_settings.save(self.repo, "routing.objective", "quality")
        ui_settings.save(self.repo, "sandbox.provider", "devcontainer")
        ui_settings.save(self.repo, "worktrees.mode", "per-job")
        parsed = harness.parse_harness(self.harness)
        self.assertEqual((parsed["workers"]["codex"], parsed["queue"]["max_running"], parsed["routing"]["objective"],
                          parsed["sandbox"]["provider"], parsed["worktrees"]["mode"]), ("true", 2, "quality", "devcontainer", "per-job"))
        self.assertEqual(parsed["parent"], "claude")
        self.assertTrue(parsed["project"]["enabled"])
        ui_settings.save(self.repo, "workers.codex", False)
        self.assertEqual(self.harness.read_text().count("codex ="), 1)

    def test_the_bell_for_all_repos_or_this_one(self):
        ui_settings.save(self.repo, "ui.bell", "true", "all")
        self.assertEqual((ui_settings.read(self.repo)["bell"], ui_settings.read(self.repo)["bell_scope"]), (True, "all"))
        ui_settings.save(self.repo, "ui.bell", "false", "repo")
        self.assertEqual((ui_settings.read(self.repo)["bell"], ui_settings.read(self.repo)["bell_scope"]), (False, "repo"))

    def test_refusals(self):
        for key, value, scope, message in (("billing.budget", "20", "repo", "not a setting"),
                                           ("queue.max_running", "99", "repo", "one of"),
                                           ("routing.objective", "fastest", "repo", "one of"),
                                           ("workers.codex", "true", "all", "kept in repo")):
            with self.assertRaisesRegex(ui_settings.SettingsError, message):
                ui_settings.save(self.repo, key, value, scope)
        self.harness.unlink()
        with self.assertRaisesRegex(ui_settings.SettingsError, "rig init"):
            ui_settings.save(self.repo, "workers.codex", True)
        self.assertFalse(self.harness.exists(), "a setting never creates the harness")

    def test_over_the_api(self):
        api = local_api.Api(self.repo, "t" * 64)
        call = lambda method, params=None: api.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
                                                          "token": "t" * 64})
        rows = {r["method"]: r["mutates"] for r in call("api.methods")["result"]["methods"]}
        self.assertEqual((rows["settings.app"], rows["settings.set"]), (False, True))
        self.assertEqual(call("settings.set", {"key": "queue.max_running", "value": 4})["result"]["max_running"], 4)
        self.assertEqual(call("settings.app")["result"]["max_running"], 4)
        self.assertEqual(call("settings.set", {"key": "nope", "value": "x"})["error"]["code"], local_api.INVALID_PARAMS)
        self.assertEqual(call("settings.set", {"key": "ui.bell"})["error"]["code"], local_api.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
