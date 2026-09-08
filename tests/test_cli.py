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


if __name__ == "__main__":
    unittest.main()
