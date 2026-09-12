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

    def test_agy_creates_when_missing(self):
        cfg = self.home / ".gemini" / "config" / "mcp_config.json"
        msg = install_ui.install_agy_mcp(cfg, self.script)
        data = json.loads(cfg.read_text())
        self.assertEqual(data["mcpServers"]["rig"]["command"], self.launcher)
        self.assertIn("created mcp_config.json", msg)
        self.assertIn("agy", msg)

    def test_refresh_launcher_keep_message(self):
        cfg = self.home / ".omp" / "mcp.json"
        install_ui.install_omp_mcp(cfg, self.script)
        msg = install_ui.install_omp_mcp(cfg, self.script)
        self.assertIn("refreshed launcher", msg)


class CodexQueueHook(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_merge_keeps_other_submit_hooks(self):
        path = self.home / ".codex" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 /tmp/other.py",
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
        )
        msg = install_ui.merge_user_prompt_submit_hook(
            path, "python3 /tmp/queue_submit_hook.py"
        )
        self.assertIn("set", msg)
        data = json.loads(path.read_text())
        groups = data["hooks"]["UserPromptSubmit"]
        cmds = [
            item.get("command")
            for g in groups
            for item in g.get("hooks") or []
        ]
        self.assertIn("python3 /tmp/other.py", cmds)
        self.assertTrue(any("queue_submit_hook" in str(c) for c in cmds))
        again = install_ui.merge_user_prompt_submit_hook(
            path, "python3 /tmp/queue_submit_hook.py --refresh"
        )
        self.assertIn("refreshed", again)
        data = json.loads(path.read_text())
        groups = data["hooks"]["UserPromptSubmit"]
        cmds = [
            item.get("command")
            for g in groups
            for item in g.get("hooks") or []
        ]
        self.assertEqual(sum(1 for c in cmds if "queue_submit_hook" in str(c)), 1)
        self.assertIn("python3 /tmp/queue_submit_hook.py --refresh", cmds)

    def test_enable_codex_hooks_keeps_existing(self):
        cfg = self.home / ".codex" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("[features]\ncodex_hooks = false\n")
        msg = install_ui.enable_codex_hooks_feature(cfg)
        self.assertIn("keep", msg)
        self.assertIn("codex_hooks = false", cfg.read_text())
        missing = self.home / ".codex" / "fresh.toml"
        msg = install_ui.enable_codex_hooks_feature(missing)
        self.assertIn("set", msg)
        self.assertIn("codex_hooks = true", missing.read_text())

    def test_skip_invalid_hooks_json(self):
        path = self.home / "hooks.json"
        path.write_text("not-json")
        msg = install_ui.merge_user_prompt_submit_hook(path, "python3 x")
        self.assertIn("skip", msg)
        self.assertEqual(path.read_text(), "not-json")

    def test_agy_probe_and_merge(self):
        fake = self.home / "agy"
        fake.write_bytes(b"PreInvocation\nPreToolUse\n")
        self.assertFalse(install_ui.agy_binary_has_user_prompt_submit(fake))
        fake.write_bytes(b"PreInvocation\nUserPromptSubmit\nPreToolUse\n")
        self.assertTrue(install_ui.agy_binary_has_user_prompt_submit(fake))
        path = self.home / ".gemini" / "config" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"other": {"enabled": True, "Stop": []}}))
        msg = install_ui.merge_agy_user_prompt_submit_hook(
            path, "python3 /tmp/queue_submit_hook.py"
        )
        self.assertIn("set", msg)
        data = json.loads(path.read_text())
        self.assertIn("other", data)
        self.assertEqual(
            data["rig-queue"]["UserPromptSubmit"][0]["command"],
            "python3 /tmp/queue_submit_hook.py",
        )
        data["rig-queue"]["enabled"] = False
        path.write_text(json.dumps(data))
        skip = install_ui.merge_agy_user_prompt_submit_hook(path, "python3 /tmp/x.py")
        self.assertIn("keep", skip)
        self.assertIn("disabled", skip)

    def test_agy_install_skips_when_binary_lacks_event(self):
        fake = self.home / "agy-bin"
        fake.write_bytes(b"PreInvocation only")
        real_which = install_ui.shutil.which

        def which(name):
            if name == "agy":
                return str(fake)
            return real_which(name)

        install_ui.shutil.which = which
        try:
            msg = install_ui.install_agy_queue_hook(self.home / ".rig")
        finally:
            install_ui.shutil.which = real_which
        self.assertIn("skip agy queue hook", msg)
        self.assertIn("UserPromptSubmit", msg)

    def test_marked_file_skips_foreign(self):
        src = self.home / "src.js"
        src.write_text("// queue_submit_hook\nexport default function () {}\n")
        dest = self.home / "extensions" / "rig-queue.js"
        dest.parent.mkdir()
        dest.write_text("export default function other() {}\n")
        msg = install_ui.install_marked_file(src, dest)
        self.assertIn("skip", msg)
        self.assertIn("export default function other", dest.read_text())
        dest.write_text("// queue_submit_hook old\n")
        msg = install_ui.install_marked_file(src, dest)
        self.assertIn("refreshed", msg)
        self.assertIn("export default function ()", dest.read_text())


if __name__ == "__main__":
    unittest.main()
