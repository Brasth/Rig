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

    def test_two_line_hud_keeps_actions_with_old_and_new_payloads(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        source = ROOT / "adapters" / "opencode" / "tui" / "rig-hud.tsx"
        cases = [
            ({"status": "ask", "display_state": "needs-input", "lines": ["rig ASK new-job", "new-job: rig job allow new-job / rig job deny new-job"]}, "rig job allow new-job"),
            ({"status": "ask", "text": "rig ASK old-job\nagent codex\npermission question\nrig job allow old-job / rig job deny old-job"}, "rig job allow old-job"),
            ({"status": "running", "lines": ["rig needs-input owner-job", "files held", "rig job reconcile owner-job"]}, "rig job reconcile owner-job"),
            ({"status": "idle", "lines": ["rig idle", "QUEUE 0"]}, "QUEUE 0"),
        ]
        script = f"""
import * as module from 'node:module';
import {{ readFileSync }} from 'node:fs';
import {{ join }} from 'node:path';
if (typeof module.stripTypeScriptTypes !== 'function') {{
  console.log(JSON.stringify({{unsupported:true}}));
}} else {{
  const source = readFileSync({json.dumps(str(source))}, 'utf8');
  const helpers = source.slice(source.indexOf('function jobsPy'), source.indexOf('function HudLines'));
  const code = module.stripTypeScriptTypes(helpers);
  const calls = [];
  const run = new Function('spawnSync', 'existsSync', 'homedir', 'join', code + "\\nreturn compactLines(hudLines('/fixture'), 2);");
  const results = {json.dumps([payload for payload, _ in cases])}.map((payload) => run(
    (binary, args) => {{ calls.push([binary,args]); return {{stdout:JSON.stringify(payload)}}; }},
    () => true, () => '/fixture', join
  ));
  console.log(JSON.stringify({{results,calls}}));
}}
"""
        result = subprocess.run([node, "--input-type=module"], input=script, capture_output=True, text=True,
                                cwd=self.repo, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout.strip().splitlines()[-1])
        if rendered.get("unsupported"):
            self.skipTest("Node TypeScript stripping is unavailable")
        for lines, (_, expected) in zip(rendered["results"], cases):
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(any(expected in line for line in lines), lines)
        self.assertTrue(all(binary == "python3" and args[1:] == ["hud", "--json"] for binary, args in rendered["calls"]))
        self.assertFalse((self.repo / ".rig" / "queue").exists())


if __name__ == "__main__":
    unittest.main()
