#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RIG = ROOT / "bin" / "rig"


def run_rig(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["RIG_HOME"] = str(ROOT)
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    if env:
        merged.update(env)
    return subprocess.run(
        [str(RIG), *args],
        cwd=repo,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


class CliMemoryAndThread(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        proc = run_rig(self.repo, "init")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def tearDown(self):
        self.td.cleanup()

    def test_memory_add_and_show(self):
        add = run_rig(self.repo, "memory", "add", "Pin full Claude model ids")
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertEqual(add.stdout.strip(), "added")
        again = run_rig(self.repo, "memory", "add", "pin full Claude model ids")
        self.assertEqual(again.stdout.strip(), "exists")
        shown = run_rig(self.repo, "memory")
        self.assertIn("Pin full Claude model ids", shown.stdout)

    def test_job_start_stamps_parent_thread(self):
        proc = run_rig(
            self.repo,
            "job",
            "start",
            "--worker",
            "grok",
            "--role",
            "implement",
            env={"RIG_THREAD": "parent-thread-cli"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        job_id = proc.stdout.strip().splitlines()[-1]
        meta = json.loads((self.repo / ".rig" / "jobs" / job_id / "meta.json").read_text())
        self.assertEqual(meta.get("thread"), "parent-thread-cli")
        listed = run_rig(self.repo, "jobs")
        self.assertIn("parent-thread-cli", listed.stdout)
        self.assertIn(job_id, listed.stdout)


def _stub_path(extra: Path | None = None) -> str:
    import sys

    parts = [
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        str(Path(sys.executable).resolve().parent),
    ]
    if extra:
        parts.insert(0, str(extra))
    return ":".join(parts)


def _fake_bin(folder: Path, name: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


class InitPresence(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        self.bins = self.repo / "bins"
        self.bins.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_new_init_cursor_on_when_cli_present(self):
        _fake_bin(self.bins, "cursor-agent")
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path(self.bins)})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"cursor\s*=\s*true")
        self.assertRegex(text, r"grok\s*=\s*false")
        self.assertRegex(text, r"claude\s*=\s*false")

    def test_new_init_cursor_off_when_cli_missing(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"cursor\s*=\s*false")

    def test_existing_harness_flags_not_flipped(self):
        rig_dir = self.repo / ".rig"
        rig_dir.mkdir()
        (rig_dir / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = false\ngrok = true\nclaude = false\n'
        )
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path(self.bins)})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        text = (rig_dir / "harness.toml").read_text()
        self.assertRegex(text, r"claude\s*=\s*false")
        self.assertRegex(text, r"cursor\s*=\s*false")
        self.assertNotRegex(text, r"cursor\s*=\s*true")

    def test_workers_cursor_on(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        on = run_rig(self.repo, "workers", "cursor=on", env={"PATH": _stub_path()})
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertIn("cursor = true", on.stdout)
        text = (self.repo / ".rig" / "harness.toml").read_text()
        self.assertRegex(text, r"cursor\s*=\s*true")

    def test_doctor_lists_cursor_and_apps(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = run_rig(self.repo, "doctor", env={"PATH": _stub_path(), "RIG_PARENT": "codex"})
        self.assertEqual(doc.returncode, 0, doc.stderr)
        self.assertIn("cursor", doc.stdout)
        self.assertIn("Apps (not spawnable)", doc.stdout)
        self.assertIn("grok-bot", doc.stdout)
        self.assertIn("curl https://cursor.com/install", doc.stdout)

    def test_job_start_accepts_cursor(self):
        run_rig(self.repo, "init", env={"PATH": _stub_path()})
        proc = run_rig(
            self.repo,
            "job",
            "start",
            "--worker",
            "cursor",
            "--role",
            "implement",
            env={"PATH": _stub_path()},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_init_agents_tells_parent_to_answer_ask(self):
        proc = run_rig(self.repo, "init", env={"PATH": _stub_path()})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = (self.repo / "AGENTS.md").read_text()
        self.assertIn("rig job wait", text)
        self.assertIn("rig job allow", text)
        self.assertIn("Never kill", text)
        self.assertIn("Never spawn another worker", text)
        self.assertIn("background", text)


if __name__ == "__main__":
    unittest.main()
