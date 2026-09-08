#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts" / "run-worker.sh"


def run_worker(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["RIG_HOME"] = str(ROOT)
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    merged["RIG_PARENT"] = "grok"
    merged["RIG_LIVE"] = "0"
    if env:
        merged.update(env)
    return subprocess.run(
        [str(RUN), *args],
        cwd=repo,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


class ClaudeWorkerArgv(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = false\ngrok = true\nclaude = true\n'
        )
        jobs = self.repo / ".rig" / "jobs" / "claude-stream"
        jobs.mkdir(parents=True)
        self.brief = jobs / "brief.md"
        self.brief.write_text("You are a worker, not the orchestrator.\nFix the container helper.\n")

    def tearDown(self):
        self.td.cleanup()

    def test_claude_dry_run_streams_and_keeps_oauth(self):
        proc = run_worker(self.repo, "claude", "claude-stream", str(self.brief))
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("stream-json", out)
        self.assertIn("--verbose", out)
        self.assertIn("acceptEdits", out)
        self.assertIn("Write", out)
        self.assertIn("--no-session-persistence", out)
        self.assertIn("--setting-sources=", out)
        self.assertIn("--permission-prompt-tool", out)
        self.assertIn("mcp__rig-ask__permission_prompt", out)
        self.assertIn("--mcp-config", out)
        self.assertNotIn("--bare", out)
        self.assertNotIn("--dangerously-skip-permissions", out)
        self.assertNotRegex(out, r"--output-format json\b")
        mcp = self.repo / ".rig" / "jobs" / "claude-stream" / "mcp.json"
        self.assertTrue(mcp.is_file(), out)
        cfg = json.loads(mcp.read_text())
        self.assertIn("rig-ask", cfg.get("mcpServers") or cfg)



if __name__ == "__main__":
    unittest.main()
