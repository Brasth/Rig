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
    merged["RIG_SKIP_MODEL_CATALOG"] = "1"
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
        self.assertIn("claude -p", out)
        self.assertIn("stream-json", out)
        self.assertIn("--verbose", out)
        self.assertIn("acceptEdits", out)
        self.assertIn("Write", out)
        self.assertIn("--no-session-persistence", out)
        self.assertNotIn("--setting-sources=", out)
        self.assertIn("--permission-prompt-tool", out)
        self.assertIn("mcp__rig-ask__permission_prompt", out)
        self.assertIn("--mcp-config", out)
        self.assertNotIn("--bare", out)
        self.assertNotIn("--dangerously-skip-permissions", out)
        self.assertNotRegex(out, r"--output-format json\b")
        would = out.split("would run:", 1)[1]
        prompt_at = would.find("Fix the container helper")
        self.assertGreater(prompt_at, -1, out)
        self.assertLess(would.find("--model"), prompt_at, out)
        self.assertLess(would.find("--mcp-config"), prompt_at, out)
        mcp = self.repo / ".rig" / "jobs" / "claude-stream" / "mcp.json"
        self.assertTrue(mcp.is_file(), out)
        cfg = json.loads(mcp.read_text())
        servers = cfg.get("mcpServers") or cfg
        self.assertIn("rig-ask", servers)
        ask = servers["rig-ask"]
        self.assertIn("python3", ask["command"])
        self.assertTrue(os.path.isabs(ask["command"]), ask["command"])
        self.assertTrue(os.path.isabs(ask["args"][0]), ask["args"])
        self.assertTrue(str(ask["args"][0]).endswith("claude-ask.py"), ask["args"])

    def test_claude_haiku_dry_run_omits_effort_flag(self):
        env = {
            "RIG_MODEL": "claude-haiku-4-5-20251001",
            "RIG_EFFORT": "low",
            "RIG_ROLE": "explore",
        }
        proc = run_worker(self.repo, "claude", "claude-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("claude-haiku-4-5-20251001", out)
        self.assertIn("stream-json", out)
        self.assertNotIn("--effort", out)
        meta = json.loads((self.repo / ".rig" / "jobs" / "claude-stream" / "meta.json").read_text())
        self.assertEqual(meta.get("effort"), "low")
        self.assertEqual(meta.get("model"), "claude-haiku-4-5-20251001")

    def test_claude_sonnet_dry_run_passes_effort(self):
        env = {
            "RIG_MODEL": "claude-sonnet-5",
            "RIG_EFFORT": "medium",
        }
        proc = run_worker(self.repo, "claude", "claude-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("--effort medium", out)
        self.assertIn("claude-sonnet-5", out)
        self.assertIn("stream-json", out)

    def test_wrapper_pauses_timeout_while_ask_pending(self):
        src = RUN.read_text()
        self.assertIn("ask.json", src)
        self.assertIn("ask-reply.json", src)
        self.assertIn("do not spawn another worker", src)
        self.assertIn("IN_ASK", src)
        detect = (ROOT / "scripts" / "detect-binaries.sh").read_text()
        self.assertIn("Do not use computer-use, chrome-profile, or Figma MCP", detect)
        self.assertIn("Follow skill file paths listed in the brief", detect)
        self.assertIn("Restart the work clock", src)


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


class OpenCodeOmpPiWorkerArgv(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n\n[workers]\ncodex = false\ngrok = false\nclaude = false\n'
            "cursor = false\nopencode = true\nomp = true\npi = true\nagy = true\n"
        )
        jobs = self.repo / ".rig" / "jobs" / "print-stream"
        jobs.mkdir(parents=True)
        self.brief = jobs / "brief.md"
        self.brief.write_text("You are a worker, not the orchestrator.\nFix the helper.\n")
        self.bins = self.repo / "bins"
        self.bins.mkdir()
        for name in ("opencode", "omp", "pi", "agy"):
            path = self.bins / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)

    def tearDown(self):
        self.td.cleanup()

    def _env(self, extra: dict | None = None) -> dict:
        env = {
            "PATH": f"{self.bins}:/usr/bin:/bin",
            "RIG_PARENT": "grok",
            "RIG_ROLE": "implement",
            "RIG_MODEL": "",
            "RIG_EFFORT": "",
        }
        if extra:
            env.update(extra)
        return env

    def test_opencode_dry_run_run_json_auto(self):
        proc = run_worker(
            self.repo, "opencode", "print-stream", str(self.brief), env=self._env()
        )
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("opencode run", out)
        self.assertIn("--format json", out)
        self.assertIn("--dir", out)
        self.assertIn("--auto", out)
        self.assertIn("-m openai/gpt-5.6-luna", out)
        self.assertIn("--variant high", out)
        self.assertNotIn("--interactive", out)
        self.assertNotIn("gpt-5.6-sol", out)
        self.assertNotIn("openai/gpt-5.6-sol", out)
        self.assertNotIn("claude-fable", out)

    def test_omp_dry_run_print_json_write(self):
        proc = run_worker(self.repo, "omp", "print-stream", str(self.brief), env=self._env())
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("omp", out)
        self.assertIn("-p", out)
        self.assertIn("--mode json", out)
        self.assertIn("--cwd", out)
        self.assertIn("--approval-mode write", out)
        self.assertIn("--no-session", out)
        self.assertIn("--model grok-4.6", out)
        self.assertIn("--thinking high", out)
        self.assertNotIn("--auto-approve", out)
        self.assertNotIn("--plan-yolo", out)
        self.assertNotIn("gpt-5.6-sol", out)
        self.assertNotIn("claude-fable", out)

    def test_pi_dry_run_print_json_approve(self):
        proc = run_worker(self.repo, "pi", "print-stream", str(self.brief), env=self._env())
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("pi", out)
        self.assertIn("-p", out)
        self.assertIn("--mode json", out)
        self.assertIn("--approve", out)
        self.assertIn("--no-session", out)
        self.assertIn("--model grok-4.6", out)
        self.assertIn("--thinking high", out)
        self.assertNotIn("--auto-approve", out)
        self.assertNotIn("gpt-5.6-sol", out)
        self.assertNotIn("claude-fable", out)

    def test_agy_dry_run_print_json_accept_edits(self):
        settings = self.repo / "agy-settings.json"
        settings.write_text("{}\n")
        env = self._env(
            {
                "AGY_SETTINGS": str(settings),
                "RIG_TIMEOUT": "1200",
            }
        )
        proc = run_worker(self.repo, "agy", "print-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertIn(proc.returncode, (0, 127), out)
        self.assertIn("would run:", out, out)
        self.assertIn("agy", out)
        self.assertIn("-p", out)
        self.assertIn("--output-format json", out)
        self.assertIn("--mode accept-edits", out)
        self.assertIn("--print-timeout 1200s", out)
        self.assertIn("--disable-slash-commands", out)
        self.assertIn("--model gemini-3.8-flash-high", out)
        self.assertIn("--effort high", out)
        self.assertNotIn("--dangerously-skip-permissions", out)
        self.assertNotIn("gpt-5.6-sol", out)
        self.assertNotIn("claude-fable", out)
        self.assertEqual(settings.read_text(), "{}\n")
        bak = self.repo / ".rig" / "jobs" / "print-stream" / "agy-settings.bak"
        self.assertFalse(bak.exists(), out)

    def test_agy_dry_run_passes_model_and_effort_when_set(self):
        env = self._env({"RIG_MODEL": "gemini-foo", "RIG_EFFORT": "high", "RIG_TIMEOUT": "90"})
        proc = run_worker(self.repo, "agy", "print-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertIn("--model gemini-foo", out, out)
        self.assertIn("--effort high", out, out)
        self.assertIn("--print-timeout 90s", out, out)
        self.assertNotIn("--dangerously-skip-permissions", out)

    def test_agy_live_denied_actions_fails_and_restores_settings(self):
        settings = self.repo / "agy-settings.json"
        settings.write_text('{"keep": true, "permissions": {"allow": ["read(*)"]}}\n')
        agent = self.bins / "agy"
        agent.write_text(
            "#!/bin/sh\n"
            'echo \'{"status":"SUCCESS","response":"blocked","denied_actions":'
            '[{"action":"command","display_name":"RunCommand"}]}\'\n'
            "exit 0\n"
        )
        env = self._env(
            {
                "RIG_LIVE": "1",
                "AGY_SETTINGS": str(settings),
            }
        )
        proc = run_worker(self.repo, "agy", "print-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        result = json.loads(
            (self.repo / ".rig" / "jobs" / "print-stream" / "result.json").read_text()
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            "denied_actions" in result["summary"] or "blocked" in result["summary"],
            result["summary"],
        )
        data = json.loads(settings.read_text())
        self.assertTrue(data["keep"])
        self.assertEqual(data["permissions"]["allow"], ["read(*)"])
        self.assertNotIn("command(*)", data["permissions"]["allow"])

    def test_agy_live_empty_denied_actions_ok(self):
        settings = self.repo / "agy-settings.json"
        settings.write_text("{}\n")
        agent = self.bins / "agy"
        agent.write_text(
            "#!/bin/sh\n"
            'echo \'{"status":"SUCCESS","response":"fixed the helper","denied_actions":[]}\'\n'
            "exit 0\n"
        )
        env = self._env({"RIG_LIVE": "1", "AGY_SETTINGS": str(settings)})
        proc = run_worker(self.repo, "agy", "print-stream", str(self.brief), env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        result = json.loads(
            (self.repo / ".rig" / "jobs" / "print-stream" / "result.json").read_text()
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("fixed the helper", result["summary"])
        self.assertEqual(json.loads(settings.read_text()), {})


if __name__ == "__main__":
    unittest.main()
