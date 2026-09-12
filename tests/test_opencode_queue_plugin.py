#!/usr/bin/env python3
"""Execute the OpenCode plugin: park then throw so prompt() is skipped."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OpenCodeQueuePlugin(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "opencode"\n\n[workers]\ngrok = false\n'
        )

    def tearDown(self):
        self.td.cleanup()

    def test_command_execute_before_parks_and_throws(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        plugin = ROOT / "adapters" / "opencode" / "plugin" / "rig-queue.js"
        marker = "rig-plugin-throw-marker"
        script = f"""
import {{ pathToFileURL }} from "url";
const mod = await import(pathToFileURL({json.dumps(str(plugin))}).href);
const hooks = await mod.RigQueuePlugin({{ directory: {json.dumps(str(self.repo))} }});
const output = {{ parts: [{{ type: "text", text: "old" }}] }};
let threw = "";
try {{
  await hooks["command.execute.before"](
    {{ command: "queue", arguments: {json.dumps(marker)} }},
    output,
  );
}} catch (e) {{
  threw = String(e && e.message ? e.message : e);
}}
console.log(JSON.stringify({{
  threw,
  noReply: output.noReply === true,
  parts: (output.parts || []).map((p) => p.text),
}}));
"""
        env = os.environ.copy()
        env["RIG_HOME"] = str(ROOT)
        proc = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(data["threw"], "__RIG_QUEUE_HANDLED__")
        self.assertTrue(data["noReply"])
        self.assertTrue(any("queued" in str(t).lower() for t in data["parts"]))
        items = list((self.repo / ".rig" / "queue").glob("*.json"))
        self.assertEqual(len(items), 1)
        body = json.loads(items[0].read_text())
        self.assertEqual(body.get("text"), marker)

    def test_chat_message_parks_without_requiring_slash_command(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        plugin = ROOT / "adapters" / "opencode" / "plugin" / "rig-queue.js"
        marker = "rig-plugin-chat-marker"
        script = f"""
import {{ pathToFileURL }} from "url";
const mod = await import(pathToFileURL({json.dumps(str(plugin))}).href);
const hooks = await mod.RigQueuePlugin({{ directory: {json.dumps(str(self.repo))} }});
const output = {{ parts: [{{ type: "text", text: "$queue park {marker}" }}] }};
await hooks["chat.message"]({{}}, output);
console.log(JSON.stringify({{ parts: (output.parts || []).map((p) => p.text) }}));
"""
        env = os.environ.copy()
        env["RIG_HOME"] = str(ROOT)
        proc = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(any("queued" in str(t).lower() for t in data["parts"]))
        items = list((self.repo / ".rig" / "queue").glob("*.json"))
        self.assertEqual(len(items), 1)
        self.assertIn(marker, json.loads(items[0].read_text()).get("text", ""))


if __name__ == "__main__":
    unittest.main()
