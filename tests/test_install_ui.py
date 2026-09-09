#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "install_ui", ROOT / "scripts" / "install-ui.py"
)
install_ui = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(install_ui)


class InstallMcp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        self.rig = self.home / ".rig"
        self.scripts = self.rig / "scripts"
        self.scripts.mkdir(parents=True)
        (self.scripts / "rig-mcp.sh").write_text("#!/bin/sh\n")
        (self.scripts / "rig-statusline.sh").write_text("#!/bin/sh\n")
        (self.scripts / "rig_mcp.py").write_text("# mcp\n")

    def tearDown(self):
        self.td.cleanup()

    def test_creates_config_when_missing(self):
        cfg = self.home / ".codex" / "config.toml"
        self.assertFalse(cfg.is_file())
        msg = install_ui.install_mcp(cfg, self.scripts / "rig_mcp.py", "codex")
        self.assertTrue(cfg.is_file(), msg)
        text = cfg.read_text()
        self.assertIn("[mcp_servers.rig]", text)
        self.assertIn("rig-mcp.sh", text)
        self.assertIn("enabled = true", text)
        self.assertIn("created config.toml", msg)

    def test_adds_section_to_existing_config(self):
        cfg = self.home / ".grok" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('[cli]\ninstaller = "npm"\n')
        msg = install_ui.install_mcp(cfg, self.scripts / "rig_mcp.py", "grok")
        text = cfg.read_text()
        self.assertIn("[cli]", text)
        self.assertIn("[mcp_servers.rig]", text)
        self.assertIn("set grok [mcp_servers.rig]", msg)
        self.assertNotIn("skip", msg)

    def test_statusline_writes_without_existing_config(self):
        grok = self.home / ".grok"
        msg = install_ui.install_statusline(self.rig, grok)
        cfg = grok / "config.toml"
        self.assertTrue(cfg.is_file(), msg)
        self.assertIn("rig-statusline", cfg.read_text())
        self.assertNotIn("skip", msg)


class InstallJsonMcp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        self.scripts = self.home / ".rig" / "scripts"
        self.scripts.mkdir(parents=True)
        (self.scripts / "rig-mcp.sh").write_text("#!/bin/sh\n")
        (self.scripts / "rig_mcp.py").write_text("# mcp\n")
        self.script = self.scripts / "rig_mcp.py"
        self.launcher = str((self.scripts / "rig-mcp.sh").resolve())

    def tearDown(self):
        self.td.cleanup()

    def test_opencode_creates_when_missing(self):
        cfg = self.home / ".config" / "opencode" / "opencode.json"
        self.assertFalse(cfg.is_file())
        msg = install_ui.install_opencode_mcp(cfg, self.script)
        self.assertTrue(cfg.is_file(), msg)
        data = json.loads(cfg.read_text())
        self.assertEqual(data["mcp"]["rig"]["type"], "local")
        self.assertEqual(data["mcp"]["rig"]["command"], [self.launcher])
        self.assertTrue(data["mcp"]["rig"]["enabled"])
        self.assertNotIn("servers", data["mcp"])
        self.assertIn("created opencode.json", msg)
        self.assertTrue(cfg.read_text().endswith("\n"))

    def test_opencode_keeps_plugin_and_model(self):
        cfg = self.home / "opencode.json"
        cfg.write_text(json.dumps({"plugin": ["foo"], "model": "bar"}) + "\n")
        msg = install_ui.install_opencode_mcp(cfg, self.script)
        data = json.loads(cfg.read_text())
        self.assertEqual(data["plugin"], ["foo"])
        self.assertEqual(data["model"], "bar")
        self.assertEqual(data["mcp"]["rig"]["command"], [self.launcher])
        self.assertIn("set opencode mcp.rig", msg)
        self.assertNotIn("created", msg)

    def test_opencode_mcp_servers_existing(self):
        cfg = self.home / "opencode.json"
        cfg.write_text(
            json.dumps({"mcp": {"servers": {"other": {"type": "local"}}}}) + "\n"
        )
        install_ui.install_opencode_mcp(cfg, self.script)
        data = json.loads(cfg.read_text())
        self.assertIn("rig", data["mcp"]["servers"])
        self.assertEqual(data["mcp"]["servers"]["rig"]["command"], [self.launcher])
        self.assertIn("other", data["mcp"]["servers"])
        self.assertNotIn("rig", {k: v for k, v in data["mcp"].items() if k != "servers"})

    def test_opencode_jsonc_strips_comments(self):
        cfg = self.home / "opencode.json"
        cfg.write_text('{\n  // comment\n  "plugin": ["x"],\n  /* block */\n}\n')
        install_ui.install_opencode_mcp(cfg, self.script)
        text = cfg.read_text()
        data = json.loads(text)
        self.assertEqual(data["plugin"], ["x"])
        self.assertIn("rig", data["mcp"])
        self.assertNotIn("//", text)
        self.assertNotIn("/*", text)

    def test_omp_creates_when_missing(self):
        cfg = self.home / ".omp" / "mcp.json"
        msg = install_ui.install_omp_mcp(cfg, self.script)
        data = json.loads(cfg.read_text())
        self.assertEqual(data["mcpServers"]["rig"]["command"], self.launcher)
        self.assertIn("created mcp.json", msg)

    def test_omp_keeps_other_servers(self):
        cfg = self.home / ".omp" / "mcp.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps({"plugin": True, "mcpServers": {"other": {"command": "x"}}})
            + "\n"
        )
        install_ui.install_omp_mcp(cfg, self.script)
        data = json.loads(cfg.read_text())
        self.assertTrue(data["plugin"])
        self.assertEqual(data["mcpServers"]["other"]["command"], "x")
        self.assertEqual(data["mcpServers"]["rig"]["command"], self.launcher)

    def test_pi_creates_when_missing(self):
        cfg = self.home / ".pi" / "agent" / "mcp.json"
        msg = install_ui.install_pi_mcp(cfg, self.script)
        data = json.loads(cfg.read_text())
        self.assertEqual(data["mcpServers"]["rig"]["command"], self.launcher)
        self.assertIn("created mcp.json", msg)

    def test_refresh_launcher_keep_message(self):
        cfg = self.home / ".omp" / "mcp.json"
        install_ui.install_omp_mcp(cfg, self.script)
        msg = install_ui.install_omp_mcp(cfg, self.script)
        self.assertIn("refreshed launcher", msg)


if __name__ == "__main__":
    unittest.main()
