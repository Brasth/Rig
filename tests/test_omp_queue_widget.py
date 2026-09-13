#!/usr/bin/env python3
"""OMP/Pi extension paints a read-only HUD via jobs.py hud."""
from __future__ import annotations

import json
import os
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

    def test_omp_and_pi_keep_actions_visible_for_new_and_legacy_hud_payloads(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        kit = self.repo / "fixture-kit" / "scripts"
        kit.mkdir(parents=True)
        (kit / "jobs.py").write_text("import os,sys\nassert sys.argv[1:]==['hud','--json']\nprint(os.environ['RIG_TEST_HUD'])\n")
        before = {str(path.relative_to(self.repo)): path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}
        cases = [
            ({"status": "ask", "display_state": "needs-input", "lines": ["rig ASK job-new", "job-new: rig job allow job-new / rig job deny job-new"]}, "rig job allow job-new"),
            ({"status": "ask", "text": "rig ASK job-old\nagent codex\npermission question\nrig job allow job-old / rig job deny job-old"}, "rig job allow job-old"),
            ({"status": "running", "lines": ["rig needs-input owner-job", "files held", "rig job reconcile owner-job"]}, "rig job reconcile owner-job"),
            ({"status": "idle", "lines": ["rig idle", "QUEUE 0"]}, "QUEUE 0"),
        ]
        for adapter in ("omp", "pi"):
            for payload, expected in cases:
                with self.subTest(adapter=adapter, payload=payload):
                    extension = ROOT / "adapters" / adapter / "extensions" / "rig-queue.js"
                    script = f"""
import {{ pathToFileURL }} from 'url';
const events = {{}}, widgets = [], statuses = [];
const pi = {{ on(name, fn) {{ events[name] = fn; }}, registerCommand() {{}} }};
const mod = await import(pathToFileURL({json.dumps(str(extension))}).href);
mod.default(pi);
const ctx = {{ cwd: {json.dumps(str(self.repo))}, ui: {{
  setWidget(id, lines) {{ widgets.push(lines); }},
  setStatus(id, text) {{ statuses.push(text); }}
}} }};
await events.session_start({{}}, ctx);
await events.turn_end({{}}, ctx);
console.log(JSON.stringify({{widgets,statuses}}));
"""
                    result = subprocess.run([node, "--input-type=module"], input=script, text=True, capture_output=True,
                                            cwd=self.repo, env={**os.environ, "RIG_HOME": str(kit.parent),
                                                               "RIG_TEST_HUD": json.dumps(payload)}, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    rendered = json.loads(result.stdout.strip().splitlines()[-1])
                    self.assertEqual(len(rendered["statuses"]), 2)
                    self.assertTrue(all(expected in text for text in rendered["statuses"]), rendered)
                    self.assertTrue(all(any(expected in line for line in lines) for lines in rendered["widgets"]), rendered)
        after = {str(path.relative_to(self.repo)): path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}
        self.assertEqual(after, before, "HUD refresh must not mutate jobs, queue, or reservations")
