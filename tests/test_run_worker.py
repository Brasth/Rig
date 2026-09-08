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

    def test_wrapper_pauses_timeout_while_ask_pending(self):
        src = RUN.read_text()
        self.assertIn("ask.json", src)
        self.assertIn("ask-reply.json", src)
        self.assertIn("do not spawn another worker", src)


class CursorWorkerArgv(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = false\ngrok = false\nclaude = false\ncursor = true\n'
        )
        jobs = self.repo / ".rig" / "jobs" / "cursor-stream"
        jobs.mkdir(parents=True)
        self.brief = jobs / "brief.md"
        self.brief.write_text("You are a worker, not the orchestrator.\nFix the helper.\n")
        self.bins = self.repo / "bins"
        self.bins.mkdir()
        agent = self.bins / "cursor-agent"
        agent.write_text("#!/bin/sh\nexit 0\n")
        agent.chmod(0o755)

    def tearDown(self):
        self.td.cleanup()

    def test_cursor_dry_run_stream_json_force_trust(self):
        env = {
            "PATH": f"{self.bins}:/usr/bin:/bin",
            "RIG_PARENT": "grok",
            "RIG_MODEL": "composer-2.5",
            "RIG_ROLE": "implement",
        }
        proc = run_worker(self.repo, "cursor", "cursor-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("cursor-agent", out)
        self.assertIn("stream-json", out)
        self.assertIn("--stream-partial-output", out)
        self.assertIn("--force", out)
        self.assertIn("--trust", out)
        self.assertIn("--workspace", out)
        self.assertIn("composer-2.5", out)
        self.assertNotIn("--worktree", out)
        self.assertNotIn("gpt-5.6-sol", out)
        self.assertNotIn("claude-fable", out)
        self.assertNotIn("--yolo", out)
        self.assertNotIn("--mode=ask", out)

    def test_cursor_explore_is_ask_mode(self):
        env = {
            "PATH": f"{self.bins}:/usr/bin:/bin",
            "RIG_PARENT": "grok",
            "RIG_MODEL": "composer-2.5-fast",
            "RIG_ROLE": "explore",
        }
        proc = run_worker(self.repo, "cursor", "cursor-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertIn("--mode=ask", out, out)


if __name__ == "__main__":
    unittest.main()
