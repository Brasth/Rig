#!/usr/bin/env python3
"""The Settings tab's data: effective harness settings, read-only, over the local API."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import local_api  # noqa: E402
import settings_view  # noqa: E402


def make_repo(root: Path, harness: str | None) -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".rig").mkdir(exist_ok=True)
    if harness is not None:
        (root / ".rig" / "harness.toml").write_text(harness)
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.env = mock.patch.dict(os.environ, {"RIG_HOME": str(self.base / "home")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def view(self, harness):
        return settings_view.view(make_repo(self.base / "repo", harness))

    def lines(self, view, title):
        return [l["text"] for s in view["sections"] if s["title"] == title for l in s["lines"]]


class Content(Base):
    FULL = (
        'parent = "codex"\n\n[project]\nenabled = true\n\n[workers]\nclaude = true\ngrok = true\ncodex = false\n\n'
        '[queue]\nmax_running = 4\nmax_per_worker = 2\n[queue.workers]\ngrok = 1\n\n'
        '[sandbox]\nprovider = "devcontainer"\n[sandbox.workers]\nclaude = "mount-ro"\ncodex = "none"\n\n'
        '[worktrees]\nmode = "per-job"\ncontainer = "per-job"\n\n[experiments]\nchat = true\n\n[mcp]\nprofile = "core"\n')

    def test_every_section_reflects_the_file(self):
        view = self.view(self.FULL)
        self.assertEqual([s["title"] for s in view["sections"]],
                         ["This project", "Who does what", "Routing", "Orchestration", "Queue", "Sandbox", "Worktrees", "Tools", "Changing these"])
        self.assertIn("parent     codex", self.lines(view, "This project"))
        self.assertIn("state      enabled", self.lines(view, "This project"))
        who = self.lines(view, "Who does what")
        self.assertIn("● claude", who)
        self.assertIn("● grok", who)
        self.assertNotIn("● codex", who)
        self.assertTrue(any(l.startswith("off: ") and "codex" in l for l in who))
        queue = self.lines(view, "Queue")
        self.assertEqual(queue[:2], ["running    4 at once", "per worker 2"])
        self.assertIn("  grok     1 at once", queue)
        sandbox = self.lines(view, "Sandbox")
        self.assertIn("provider   devcontainer", sandbox)
        self.assertIn("  claude   credentials mounted read-only", sandbox)
        self.assertIn("  codex    no credentials mounted", sandbox)
        self.assertEqual(self.lines(view, "Worktrees"), ["mode       per-job", "container  per-job"])
        self.assertEqual(self.lines(view, "Tools"), ["mcp        core", "computer   off", "chat       on (experiment)"])
        self.assertFalse(view["attention"])

    def test_defaults_when_the_file_says_little(self):
        view = self.view("[project]\nenabled = true\n[workers]\nclaude = true\n")
        self.assertIn("parent     not set (pass --parent to rig shell)", self.lines(view, "This project"))
        self.assertEqual(self.lines(view, "Queue")[:2], ["running    3 at once", "per worker no limit"])
        self.assertEqual(self.lines(view, "Worktrees"), ["mode       off", "container  shared"])

    def test_problems_are_flagged_not_hidden(self):
        view = self.view('[project]\nenabled = false\n\n[sandbox]\nprovider = "vm"\n\n[worktrees]\nmode = "sometimes"\n')
        self.assertTrue(view["attention"])
        self.assertIn("state      disabled", self.lines(view, "This project"))
        self.assertTrue(any(l.startswith("! unknown [sandbox] provider") for l in self.lines(view, "Sandbox")))
        self.assertTrue(any(l.startswith("! unknown [worktrees] mode") for l in self.lines(view, "Worktrees")))
        tones = {l["text"]: l["tone"] for s in view["sections"] for l in s["lines"]}
        self.assertEqual(tones["no worker is enabled"], "attention")

    def test_an_uninitialised_project_says_how_to_start(self):
        view = self.view(None)
        text = self.lines(view, "This project")
        self.assertIn("file       .rig/harness.toml (not created: run rig init)", text)
        self.assertIn("state      uninitialized", text)
        self.assertTrue(view["attention"])

    def test_it_never_writes_anything(self):
        repo = make_repo(self.base / "repo", self.FULL)
        before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
        stamp = (repo / ".rig" / "harness.toml").stat().st_mtime_ns
        settings_view.view(repo)
        self.assertEqual(sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*")), before)
        self.assertEqual((repo / ".rig" / "harness.toml").stat().st_mtime_ns, stamp)

    def test_go_shell_fixture_matches_the_view_builder(self):
        repo = make_repo(self.base / "repo", self.FULL)
        fresh = {"view": settings_view.view(repo)}
        path = ROOT / "shell" / "testdata" / "settings.json"
        if os.environ.get("RIG_UPDATE_FIXTURES") == "1":
            path.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n")
        self.assertEqual(json.loads(path.read_text()), fresh,
                         "run: RIG_UPDATE_FIXTURES=1 python3 tests/test_settings_view.py Content")


class OverTheWire(Base):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.base / "repo", "[project]\nenabled = true\n[workers]\nclaude = true\n")
        env = {k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}}
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "local_api.py"), "serve", "--repo", str(self.repo)],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                local_api.call(self.repo, "api.ping", timeout=0.5)
                return
            except OSError:
                time.sleep(0.05)
        self.fail("server did not start")

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        super().tearDown()

    def test_the_method_is_listed_as_a_read_and_answers_with_the_view(self):
        methods = {m["method"]: m for m in local_api.call(self.repo, "api.methods")["result"]["methods"]}
        self.assertFalse(methods["settings.view"]["mutates"])
        view = local_api.call(self.repo, "settings.view")["result"]["view"]
        self.assertEqual(view["version"], settings_view.VIEW_VERSION)
        self.assertIn("● claude", [l["text"] for s in view["sections"] if s["title"] == "Who does what" for l in s["lines"]])


if __name__ == "__main__":
    unittest.main()
