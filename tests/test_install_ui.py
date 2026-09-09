#!/usr/bin/env python3
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


if __name__ == "__main__":
    unittest.main()
