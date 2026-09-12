#!/usr/bin/env python3
"""OMP/Pi extension paints a read-only HUD via jobs.py hud."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OmpQueueWidget(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text('parent = "omp"\n')

    def tearDown(self):
        self.td.cleanup()

    def test_queue_parks_and_set_widget(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        ext = ROOT / "adapters" / "omp" / "extensions" / "rig-queue.js"
        marker = "rig-widget-park-marker"
        script = f"""
import {{ pathToFileURL }} from "url";
const widgets = [];
const statuses = [];
const notes = [];
const events = {{}};
const pi = {{
  on(name, fn) {{ events[name] = fn; }},
  registerCommand(name, spec) {{ this.cmd = spec; }},
}};
const mod = await import(pathToFileURL({json.dumps(str(ext))}).href);
mod.default(pi);
const ctx = {{
  cwd: {json.dumps(str(self.repo))},
  ui: {{
    notify(text) {{ notes.push(String(text)); }},
    setWidget(id, lines, where) {{ widgets.push({{ id, lines, where }}); }},
    setStatus(id, text) {{ statuses.push({{ id, text }}); }},
  }},
}};
await pi.cmd.handler({json.dumps(marker)}, ctx);
if (events.session_start) await events.session_start({{}}, ctx);
console.log(JSON.stringify({{ widgets, statuses, notes }}));
"""
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        env["RIG_HOME"] = str(ROOT)
        proc = subprocess.run(
            [node, "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=str(self.repo),
            env=env,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(data["widgets"], data)
        self.assertEqual(data["widgets"][0]["id"], "rig")
        self.assertEqual(data["widgets"][0]["where"], "below")
        joined = "\n".join(data["widgets"][0]["lines"])
        self.assertIn("QUEUE", joined)
        qdir = self.repo / ".rig" / "queue"
        self.assertTrue(any(qdir.glob("*.json")), "park should write a queue file")
