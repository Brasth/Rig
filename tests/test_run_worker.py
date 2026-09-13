#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts" / "run-worker.sh"
sys.path.insert(0, str(ROOT / "scripts"))


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
        self.assertTrue(str(ask["args"][0]).endswith("rig_mcp.py"), ask["args"])
        env = ask.get("env") or {}
        self.assertEqual(env.get("RIG_JOB_ID"), "claude-stream")
        self.assertEqual(Path(env.get("RIG_JOB_DIR") or "").name, "claude-stream")
        self.assertTrue((env.get("RIG_JOB_DIR") or "").endswith("claude-stream"))

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

    def test_wrapper_syntax_works_with_system_bash(self):
        proc = subprocess.run(["/bin/bash", "-n", str(RUN)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


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


class WrapperChangeEvidence(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.bins = self.root / "bins"
        self.bins.mkdir()
        for args in (
            ["init", "-q"], ["config", "user.email", "test@example.invalid"],
            ["config", "user.name", "Rig Test"],
        ):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        (self.repo / ".gitignore").write_text(".rig/\n")
        for name in ("dirty file.txt", "delete me.txt", "rename from.txt", "unrelated.txt", "plain.txt"):
            (self.repo / name).write_text("original\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True, capture_output=True)
        (self.repo / "dirty file.txt").write_text("dirty before child\n")
        self.job_dir = self.repo / ".rig" / "jobs" / "evidence"
        self.job_dir.mkdir(parents=True)
        self.brief = self.job_dir / "brief.md"
        self.brief.write_text("You are a worker, not the orchestrator.\nApply the scoped change.\n")
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "grok"\n[workers]\ncodex = true\ngrok = false\nclaude = false\nopencode = true\n'
        )
        self.scope = ["dirty file.txt", "delete me.txt", "rename from.txt", "renamed file.txt", "added file.txt", "line\nbreak.txt"]
        self._worker(
            "(repo / 'dirty file.txt').write_text('changed by child\\n')\n"
            "(repo / 'delete me.txt').unlink()\n"
            "(repo / 'rename from.txt').rename(repo / 'renamed file.txt')\n"
            "(repo / 'added file.txt').write_text('added\\n')\n"
            "(repo / 'line\\nbreak.txt').write_text('newline path\\n')\n"
            "(repo / 'unrelated.txt').write_text('outside the declared scope\\n')\n"
            "claims = {'schema_version': 1, 'summary': 'verified', 'checks': [{'argv': ['touch', str(repo / 'must-not-execute')]}]}\n"
            "(pathlib.Path(os.environ['RIG_JOB_DIR']) / 'worker-evidence.json').write_text(json.dumps(claims))\n"
            "print('worker exited successfully; claimed verified')\n"
        )

    def _worker(self, body):
        script = f"#!{sys.executable}\nimport json, os, pathlib\nrepo = pathlib.Path.cwd()\n" + body
        for worker in ("codex", "opencode"):
            path = self.bins / worker
            path.write_text(script)
            path.chmod(0o755)

    def _run(self, **extra):
        env = {
            "PATH": f"{self.bins}:{os.environ.get('PATH', '')}", "RIG_LIVE": "1",
            "RIG_PARENT": "grok", "RIG_ROLE": "implement", "RIG_MODEL": "gpt-5.6-luna",
            "RIG_EFFORT": "low", "RIG_JOB_FILES_JSON": json.dumps(self.scope),
        }
        env.update(extra)
        return run_worker(self.repo, "codex", "evidence", str(self.brief), env=env)

    def _accepted_writer(self, job_id, files):
        import admission
        import change_evidence
        import jobs
        import verification

        harness = self.repo / ".rig" / "harness.toml"
        harness.write_text(harness.read_text().replace("claude = false", "claude = true"))
        session = "wrapper-review-tests"
        with patch.dict(os.environ, {"RIG_PARENT": "grok", "RIG_SKIP_MODEL_CATALOG": "1"}):
            details = jobs.start_job(
                self.repo, worker="claude", role="implement", job_id=job_id, live="grok",
                model="claude-sonnet-5", files=files, native_agent_id=job_id + "-agent",
                owner_session=session, return_details=True,
            )
            credentials = admission.credentials(details)
            jobs.finish_job(
                self.repo, job_id, owner_session=session, **credentials,
                completion={"kind": "native_child", "agent_id": job_id + "-agent", "terminal": True, "outcome": "ok"},
            )
            writer_dir = self.repo / ".rig" / "jobs" / job_id
            verification.record_requirements(self.repo, writer_dir, [], ["Check the content"],
                                             owner_session=session, **credentials)
            snapshot_id = change_evidence.snapshot(self.repo, files)["snapshot_id"]
            verification.accept(self.repo, writer_dir, "accept", snapshot_id, rationale="Content checked", next="review",
                                owner_session=session, **credentials)
        return {"RIG_" + key.upper(): value for key, value in credentials.items()} | {
            "RIG_OWNER_SESSION": session,
        }, snapshot_id

    def test_live_change_evidence_preserves_dirty_files_spaces_and_renames(self):
        proc = self._run(RIG_JOB_FILES="ignored-legacy-path")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads((self.job_dir / "result.json").read_text())
        meta = json.loads((self.job_dir / "meta.json").read_text())
        evidence = json.loads((self.job_dir / "change-evidence.json").read_text())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["execution_mode"], "live")
        self.assertEqual(result["executor_kind"], "wrapper")
        self.assertEqual(result["model_source"], "selected")
        self.assertEqual(result["provider"], "openai")
        self.assertFalse(result["model_inferred"])
        self.assertEqual(set(result["files_changed"]), set(self.scope))
        self.assertEqual(meta["files"], self.scope)
        self.assertIn("unrelated.txt", evidence["observed_changed_paths"])
        self.assertNotIn("unrelated.txt", result["files_changed"])
        self.assertFalse((self.repo / "must-not-execute").exists())
        self.assertFalse((self.job_dir / "verification.json").exists())

    def test_unknown_scope_records_uncertain_observations(self):
        proc = self._run(RIG_JOB_FILES_JSON="[]")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        evidence = json.loads((self.job_dir / "change-evidence.json").read_text())
        result = json.loads((self.job_dir / "result.json").read_text())
        self.assertEqual(evidence["attribution"], "uncertain")
        self.assertIn("dirty file.txt", evidence["observed_changed_paths"])
        self.assertEqual(result["files_changed"], [])
        self.assertEqual(result["status"], "ok")

    def test_dry_run_does_not_execute_or_claim_verification(self):
        proc = self._run(RIG_LIVE="0")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads((self.job_dir / "result.json").read_text())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["execution_mode"], "dry_run")
        self.assertEqual(result["files_changed"], [])
        self.assertEqual((self.repo / "dirty file.txt").read_text(), "dirty before child\n")
        self.assertFalse((self.job_dir / "change-evidence.json").exists())
        self.assertFalse((self.job_dir / "verification.json").exists())

    def test_invalid_json_scope_refuses_before_child(self):
        proc = self._run(RIG_JOB_FILES_JSON='{"files": ["plain.txt"]}')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("JSON array", proc.stderr)
        self.assertEqual((self.repo / "dirty file.txt").read_text(), "dirty before child\n")

    def test_actual_review_model_same_provider_is_refused(self):
        proc = self._run(RIG_ROLE="review", RIG_MODEL="claude-opus-5", RIG_WRITER_PROVIDER="anthropic")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("matches writer provider anthropic", proc.stderr)
        self.assertEqual((self.repo / "dirty file.txt").read_text(), "dirty before child\n")

    def test_standalone_review_persists_known_provider_independence(self):
        self._worker("print('review finished')\n")
        proc = self._run(RIG_ROLE="review", RIG_WRITER_MODEL="claude-sonnet-5", RIG_WRITER_CLI="cursor")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads((self.job_dir / "result.json").read_text())
        self.assertEqual(result["independence"], "confirmed")
        self.assertEqual(result["writer_provider"], "anthropic")
        self.assertEqual(result["writer_cli"], "cursor")
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["review_mode"], "standalone")

    def test_legacy_file_scope_is_still_accepted(self):
        self._worker("(repo / 'plain.txt').write_text('legacy scope edit\\n')\n")
        env = {
            "PATH": f"{self.bins}:{os.environ.get('PATH', '')}", "RIG_LIVE": "1",
            "RIG_PARENT": "grok", "RIG_ROLE": "implement", "RIG_MODEL": "gpt-5.6-luna",
            "RIG_EFFORT": "low", "RIG_JOB_FILES": "plain.txt",
        }
        with patch.dict(os.environ):
            os.environ.pop("RIG_JOB_FILES_JSON", None)
            proc = run_worker(self.repo, "codex", "evidence", str(self.brief), env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads((self.job_dir / "result.json").read_text())
        self.assertEqual(result["files_changed"], ["plain.txt"])

    def test_json_scope_preserves_literal_brackets_and_surrounding_spaces(self):
        name = " [literal].txt "
        (self.repo / name).write_text("before\n")
        self._worker(f"(repo / {name!r}).write_text('literal path edited\\n')\n")
        proc = self._run(RIG_JOB_FILES_JSON=json.dumps([name]))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads((self.job_dir / "result.json").read_text())
        evidence = json.loads((self.job_dir / "change-evidence.json").read_text())
        self.assertEqual(result["files"], [name])
        self.assertEqual(result["files_changed"], [name])
        self.assertEqual(evidence["files"], [name])
        self.assertFalse((self.repo / name.strip()).exists())

    def test_reviewer_prompt_and_metadata_share_accepted_writer_context(self):
        import admission
        import jobs

        literal = " [accepted].txt "
        files = ["plain.txt", literal]
        (self.repo / literal).write_text("accepted literal path\n")
        capture = (
            "import sys\n"
            "args = sys.argv[1:]\n"
            "prompt_file = args[args.index('--prompt-file') + 1] if '--prompt-file' in args else ''\n"
            "prompt = pathlib.Path(prompt_file).read_text() if prompt_file else args[-1]\n"
            "received = {'argv': args, 'prompt': prompt, 'prompt_file': prompt_file}\n"
            "(pathlib.Path(os.environ['RIG_JOB_DIR']) / 'received-review.json').write_text(json.dumps(received))\n"
            "print('review finished')\n"
        )
        self._worker(capture)
        grok = self.bins / "grok"
        grok.write_text((self.bins / "codex").read_text())
        grok.chmod(0o755)
        uuidgen = self.bins / "uuidgen"
        uuidgen.write_text("#!/bin/sh\necho 00000000-0000-0000-0000-000000000001\n")
        uuidgen.chmod(0o755)
        (self.repo / ".rig" / "harness.toml").write_text('[workers]\ncodex = true\ngrok = true\nclaude = false\n')
        for worker, parent, model in (("codex", "grok", "gpt-5.6-terra"), ("grok", "codex", "grok-4.6")):
            with self.subTest(worker=worker):
                writer_id = f"writer-{worker}"
                ownership_env, snapshot_id = self._accepted_writer(writer_id, files)
                job_id = f"review-{worker}"
                folder = self.repo / ".rig" / "jobs" / job_id
                folder.mkdir()
                brief = folder / "brief.md"
                original = b"You are a worker, not the orchestrator.\nReview the accepted change and explain findings.\n"
                brief.write_bytes(original)
                env = {
                    "PATH": f"{self.bins}:{os.environ.get('PATH', '')}", "RIG_LIVE": "1",
                    "RIG_PARENT": parent, "RIG_ROLE": "review", "RIG_MODEL": model,
                    "RIG_WRITER_JOB_ID": writer_id, "RIG_REVIEW_MODE": "independent",
                    "RIG_JOB_FILES_JSON": json.dumps(files),
                    **ownership_env,
                }
                proc = run_worker(self.repo, worker, job_id, str(brief), env=env)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                result = json.loads((folder / "result.json").read_text())
                received = json.loads((folder / "received-review.json").read_text())
                context = json.loads(received["prompt"].split("```json\n", 1)[1].split("\n```", 1)[0])
                self.assertEqual(brief.read_bytes(), original)
                self.assertTrue(received["prompt"].startswith(original.decode()))
                self.assertEqual(context["writer_job_id"], writer_id)
                self.assertEqual(context["writer_snapshot_id"], snapshot_id)
                self.assertEqual(context["writer_files"], sorted(files))
                for field in ("writer_job_id", "writer_snapshot_id", "writer_files"):
                    self.assertEqual(result[field], context[field])
                self.assertEqual(Path(result["review_brief"]).resolve(), (folder / "review-brief.md").resolve())
                if worker == "grok":
                    self.assertEqual(received["prompt_file"], result["review_brief"])
                else:
                    self.assertEqual(received["argv"][-1], received["prompt"])
                reviewer_credentials = admission.credentials(json.loads((folder / "owner-credentials.json").read_text()))
                jobs.close_job(self.repo, job_id, **reviewer_credentials,
                               owner_session=ownership_env["RIG_OWNER_SESSION"], rationale="Prompt transport checked")

    def test_independent_review_revalidates_writer_before_launch(self):
        import route
        ownership_env, _ = self._accepted_writer("writer", ["plain.txt"])
        choice = route.pick("grok", ["codex"], "review", "review diff", repo=self.repo,
                            writer_job_id="writer", review_mode="independent")
        self.assertEqual(choice["independence"], "confirmed")
        (self.repo / "plain.txt").write_text("changed after pick\n")
        proc = self._run(RIG_ROLE="review", RIG_REVIEW_MODE="independent", RIG_WRITER_JOB_ID="writer", **ownership_env)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("content_changed", proc.stderr)
        self.assertEqual((self.repo / "dirty file.txt").read_text(), "dirty before child\n")


class WrapperAdmission(unittest.TestCase):
    _worker = WrapperChangeEvidence._worker
    _accepted_writer = WrapperChangeEvidence._accepted_writer

    def setUp(self):
        WrapperChangeEvidence.setUp(self)
        self.processes = []
        self.addCleanup(self._stop_processes)
        self._worker(
            "import time\n"
            "folder = pathlib.Path(os.environ['RIG_JOB_DIR'])\n"
            "files = json.loads(os.environ['RIG_JOB_FILES_JSON'])\n"
            "if files: (repo / files[0]).write_text('worker edited\\n')\n"
            "(folder / 'executed').write_text(str(os.getpid()))\n"
            "while not (folder / 'continue').exists(): time.sleep(0.02)\n"
            "print('completed')\n"
        )

    def _stop_processes(self):
        for folder in (self.repo / ".rig" / "jobs").iterdir():
            if folder.is_dir():
                (folder / "continue").touch()
        (self.root / "continue-native").touch()
        (self.root / "continue-fault").touch()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

    def _wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("wrapper did not reach the expected synchronization boundary")

    def _env(self, files, **extra):
        return dict(os.environ, RIG_HOME=str(ROOT), RIG_PARENT="grok", RIG_LIVE="1",
                    RIG_SKIP_MODEL_CATALOG="1", RIG_MODEL="gpt-5.6-luna", RIG_EFFORT="low",
                    RIG_ROLE="implement", RIG_OWNER_SESSION="wrapper-admission-tests",
                    RIG_JOB_FILES_JSON=json.dumps(files), PATH=f"{self.bins}:{os.environ.get('PATH', '')}") | extra

    def _start(self, job_id, files, *, barrier=None, **extra):
        folder = self.repo / ".rig" / "jobs" / job_id
        folder.mkdir(exist_ok=True)
        brief = folder / "brief.md"
        brief.write_text("You are a worker, not the orchestrator.\nEdit only the listed file.\n")
        command = [str(RUN), "codex", job_id, str(brief)]
        if barrier:
            script = "import os,pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile not p.exists(): time.sleep(.01)\nos.execv(sys.argv[2],sys.argv[2:])\n"
            command = [sys.executable, "-c", script, str(barrier), *command]
        process = subprocess.Popen(command, cwd=self.repo, env=self._env(files, **extra),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process, folder

    def _result(self, process):
        stdout, stderr = process.communicate(timeout=15)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def _lease(self, folder):
        import admission
        return next(row for row in admission.list_reservations(self.repo, include_released=True)
                    if row.get("job_id") == folder.name)

    def _pause_admission_call(self, operation):
        marker = self.root / "paused-admission"
        shim = self.bins / "python3"
        shim.write_text(
            f"#!{sys.executable}\nimport os,pathlib,sys,time\n"
            f"if sys.argv[1:4] == ['-', {str(ROOT / 'scripts' / 'admission.py')!r}, {operation!r}]:\n"
            " parent=os.getppid()\n"
            f" pathlib.Path({str(marker)!r}).write_text(str(parent))\n"
            f" while not pathlib.Path({str(self.root / 'continue-fault')!r}).exists():\n"
            "  try: os.kill(parent,0)\n"
            "  except ProcessLookupError: raise SystemExit(23)\n"
            "  time.sleep(.02)\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
        )
        shim.chmod(0o755)
        return marker

    def _race(self, same_scope, native=False):
        import admission
        if not same_scope:
            with (self.repo / ".rig" / "harness.toml").open("a") as stream:
                stream.write("[queue]\nmax_running = 1\n")
        barrier = self.root / "start-race"
        first, first_dir = self._start("race-one", ["plain.txt"], barrier=barrier)
        second_scope = ["plain.txt" if same_scope else "unrelated.txt"]
        if native:
            script = (
                "import json,pathlib,sys,time\n"
                f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
                "import admission,jobs\n"
                "repo,barrier,done=map(pathlib.Path,sys.argv[1:4])\n"
                "while not barrier.exists(): time.sleep(.01)\n"
                "try:\n"
                " lease=jobs.start_job(repo,worker='parent',role='parent',job_id='race-native',files=json.loads(sys.argv[4]),owner_session='race-native',return_details=True)\n"
                " (repo/json.loads(sys.argv[4])[0]).write_text('native edited\\n')\n"
                " done.write_text('admitted')\n"
                " while not done.with_name('continue-native').exists(): time.sleep(.02)\n"
                " jobs.finish_job(repo,'race-native',owner_session='race-native',completion={'kind':'parent_task','completed':True},**admission.credentials(lease))\n"
                "except BaseException as error:\n"
                " done.write_text('refused: '+str(error))\n"
                " raise SystemExit(1)\n"
            )
            native_marker = self.root / "native-result"
            second = subprocess.Popen([sys.executable, "-c", script, str(self.repo), str(barrier),
                                       str(native_marker), json.dumps(second_scope)], cwd=self.repo,
                                      env=self._env(second_scope), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.processes.append(second)
            second_ready = native_marker.exists
            second_dir = None
        else:
            second, second_dir = self._start("race-two", second_scope, barrier=barrier)
            second_ready = lambda: (second_dir / "executed").exists() or second.poll() is not None
        barrier.touch()
        self._wait(lambda: ((first_dir / "executed").exists() or first.poll() is not None) and second_ready())
        rows = admission.list_reservations(self.repo)
        self.assertEqual(sum(row["slot_held"] for row in rows), 1, rows)
        first_admitted = (first_dir / "executed").exists()
        second_admitted = native_marker.read_text() == "admitted" if native else (second_dir / "executed").exists()
        self.assertEqual(int(first_admitted) + int(second_admitted), 1)
        (first_dir / "continue").touch()
        (self.root / "continue-native").touch()
        if second_dir:
            (second_dir / "continue").touch()
        results = [self._result(first), self._result(second)]
        self.assertEqual(sorted(result.returncode for result in results), [0, 1], results)
        retained = admission.list_reservations(self.repo)
        self.assertEqual(len(retained), 1)
        self.assertFalse(retained[0]["slot_held"], retained)
        self.assertEqual(retained[0]["stage"], "verifying")

    def test_two_wrappers_cannot_exceed_global_cap(self):
        self._race(same_scope=False)

    def test_two_wrappers_cannot_overlap_scope(self):
        self._race(same_scope=True)

    def test_wrapper_and_native_parent_share_global_cap(self):
        self._race(same_scope=False, native=True)

    def test_same_job_wrong_token_and_dry_run_cannot_overwrite_live_attempt(self):
        process, folder = self._start("owned", ["plain.txt"])
        self._wait(lambda: (folder / "executed").exists())
        original = (folder / "brief.md").read_bytes(), (folder / "meta.json").read_bytes()
        saved = json.loads((folder / "owner-credentials.json").read_text())
        alternate = self.root / "alternate.md"
        alternate.write_text("a competing brief must never overwrite the active brief\n")
        for extra in (
            {"RIG_RESERVATION_ID": saved["reservation_id"], "RIG_ATTEMPT_ID": saved["attempt_id"], "RIG_OWNER_TOKEN": "wrong-token"},
            {"RIG_LIVE": "0"},
        ):
            result = run_worker(self.repo, "codex", "owned", str(alternate), env=self._env(["plain.txt"], **extra))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(((folder / "brief.md").read_bytes(), (folder / "meta.json").read_bytes()), original)
        self.assertEqual((folder / "owner-credentials.json").stat().st_mode & 0o777, 0o600)
        (folder / "continue").touch()
        result = self._result(process)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(folder / "owner-credentials.json"), result.stderr)
        for text in (result.stdout, result.stderr, (folder / "meta.json").read_text(), (folder / "result.json").read_text()):
            self.assertNotIn(saved["owner_token"], text)

    def test_queue_claim_consumed_once_and_dry_run_does_not_consume(self):
        import admission
        import work_queue
        with patch.dict(os.environ, self._env(["plain.txt"])):
            item = work_queue.add_item(self.repo, "queued scoped change")
            claim = work_queue.claim_next(self.repo, item_id=item["id"], worker="codex", files=["plain.txt"],
                                          owner_session="wrapper-admission-tests")
        ownership = {"RIG_" + key.upper(): value for key, value in admission.credentials(claim).items()}
        ownership["RIG_QUEUE_ID"] = item["id"]
        preview, preview_dir = self._start("preview", ["plain.txt"], RIG_LIVE="0", **ownership)
        preview_result = self._result(preview)
        self.assertEqual(preview_result.returncode, 0, preview_result.stderr)
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "claimed")
        self.assertFalse((preview_dir / "owner-credentials.json").exists())
        process, folder = self._start("queue-live", ["plain.txt"], **ownership)
        self._wait(lambda: (folder / "executed").exists())
        row = work_queue.load_item(self.repo, item["id"])
        self.assertEqual(row["status"], "spawned")
        self.assertEqual(row["job_id"], "queue-live")
        duplicate = run_worker(self.repo, "codex", "duplicate", str(self.brief), env=self._env(["plain.txt"], **ownership))
        self.assertNotEqual(duplicate.returncode, 0)
        (folder / "continue").touch()
        result = self._result(process)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(work_queue.load_item(self.repo, item["id"])["status"], "done")
        self.assertEqual(self._lease(folder)["stage"], "verifying")

    def test_dry_run_registration_cannot_overwrite_racing_live_launch(self):
        import admission
        marker = self.root / "preview-return-paused"
        shim = self.bins / "python3"
        shim.write_text(
            f"#!{sys.executable}\nimport os,pathlib,subprocess,sys,time\n"
            f"if sys.argv[1:4] == ['-', {str(ROOT / 'scripts' / 'admission.py')!r}, 'reserve'] and os.environ.get('RIG_LIVE') == '0':\n"
            f" result=subprocess.run([{sys.executable!r}, *sys.argv[1:]],input=sys.stdin.buffer.read(),capture_output=True)\n"
            f" pathlib.Path({str(marker)!r}).touch()\n"
            f" while not pathlib.Path({str(self.root / 'continue-fault')!r}).exists(): time.sleep(.02)\n"
            " sys.stdout.buffer.write(result.stdout)\n"
            " sys.stderr.buffer.write(result.stderr)\n"
            " raise SystemExit(result.returncode)\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
        )
        shim.chmod(0o755)
        preview, folder = self._start("preview-race", ["plain.txt"], RIG_LIVE="0")
        self._wait(marker.exists)
        alternate = self.root / "live-brief.md"
        alternate.write_text("Competing live brief\n")
        live = subprocess.Popen([str(RUN), "codex", folder.name, str(alternate)], cwd=self.repo,
                                env=self._env(["plain.txt"]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(live)
        self._wait(lambda: live.poll() is not None or (folder / "executed").exists())
        (self.root / "continue-fault").touch()
        (folder / "continue").touch()
        live_result, preview_result = self._result(live), self._result(preview)
        self.assertEqual(live_result.returncode, 1, live_result.stderr)
        self.assertEqual(preview_result.returncode, 0, preview_result.stderr)
        self.assertFalse((folder / "executed").exists())
        self.assertEqual(json.loads((folder / "meta.json").read_text())["execution_mode"], "dry_run")
        self.assertEqual(admission.list_reservations(self.repo), [])
        self.assertEqual((self.repo / "plain.txt").read_text(), "original\n")

    def test_interrupted_dry_run_metadata_does_not_consume_live_capacity(self):
        import admission
        marker = self.root / "dry-meta-paused"
        shim = self.bins / "python3"
        shim.write_text(
            f"#!{sys.executable}\nimport os,pathlib,subprocess,sys,time\n"
            "if len(sys.argv)>10 and sys.argv[1]=='-' and sys.argv[2].endswith('/meta.json') and os.environ.get('RIG_LIVE')=='0':\n"
            f" result=subprocess.run([{sys.executable!r}, *sys.argv[1:]],input=sys.stdin.buffer.read(),capture_output=True)\n"
            f" pathlib.Path({str(marker)!r}).touch()\n"
            f" while not pathlib.Path({str(self.root / 'continue-fault')!r}).exists(): time.sleep(.02)\n"
            " sys.stdout.buffer.write(result.stdout)\n"
            " sys.stderr.buffer.write(result.stderr)\n"
            " raise SystemExit(result.returncode)\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
        )
        shim.chmod(0o755)
        preview, folder = self._start("interrupted-preview", ["plain.txt"], RIG_LIVE="0")
        self._wait(marker.exists)
        preview.kill()
        preview.wait(timeout=5)
        (self.root / "continue-fault").touch()
        self._result(preview)
        meta = json.loads((folder / "meta.json").read_text())
        self.assertEqual(meta["execution_mode"], "dry_run")
        self.assertEqual(meta["status"], "reserved")
        with patch.dict(os.environ, self._env(["plain.txt"])):
            live = admission.reserve(self.repo, job_id="after-preview", worker="codex", files=["plain.txt"],
                                     owner_session="wrapper-admission-tests")
        self.assertEqual(len(admission.list_reservations(self.repo)), 1)
        self.assertEqual(live["job_id"], "after-preview")

    def test_metadata_failure_compensates_before_child_edits(self):
        folder = self.repo / ".rig" / "jobs" / "meta-fault"
        folder.mkdir()
        (folder / "meta.json.tmp").mkdir()
        process, folder = self._start("meta-fault", ["plain.txt"])
        result = self._result(process)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((folder / "executed").exists())
        self.assertEqual((self.repo / "plain.txt").read_text(), "original\n")
        self.assertEqual(self._lease(folder)["stage"], "released")

    def test_failed_reviewer_registration_keeps_accepted_writer_protection(self):
        import admission
        ownership, _ = self._accepted_writer("writer", ["plain.txt"])
        folder = self.repo / ".rig" / "jobs" / "failed-review"
        folder.mkdir()
        (folder / "meta.json.tmp").mkdir()
        process, folder = self._start("failed-review", ["plain.txt"], RIG_ROLE="review",
                                      RIG_REVIEW_MODE="independent", RIG_WRITER_JOB_ID="writer", **ownership)
        result = self._result(process)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((folder / "executed").exists())
        record = self._lease(folder)
        self.assertFalse(record["slot_held"])
        self.assertNotEqual(record["stage"], "released")
        with patch.dict(os.environ, self._env(["plain.txt"])):
            with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
                admission.reserve(self.repo, job_id="conflict", worker="codex", files=["plain.txt"],
                                  owner_session="another-owner")

    def test_wrapper_death_before_activation_never_releases_child_to_edit(self):
        import admission
        marker = self._pause_admission_call("activate")
        process, folder = self._start("crashed-before-activation", ["plain.txt"])
        self._wait(marker.exists)
        ready = json.loads(next(folder.glob("launch-ready-*.json")).read_text())
        self.assertFalse(Path(ready["gate_path"]).exists())
        process.kill()
        process.wait(timeout=5)
        self._result(process)
        self._wait(lambda: admission._process_state(ready) == "dead")
        self.assertFalse((folder / "executed").exists())
        self.assertEqual((self.repo / "plain.txt").read_text(), "original\n")
        admission.reconcile(self.repo, job_id=folder.name, apply=True)
        self.assertEqual(self._lease(folder)["stage"], "released")
        self.assertEqual(admission.reconcile(self.repo, job_id=folder.name, apply=True)["applied"], 0)

    def test_cancelled_reviewer_before_gate_keeps_accepted_writer_protection(self):
        import admission
        import jobs
        ownership, _ = self._accepted_writer("writer", ["plain.txt"])
        marker = self._pause_admission_call("commit")
        process, folder = self._start("cancelled-review", ["plain.txt"], RIG_ROLE="review",
                                      RIG_REVIEW_MODE="independent", RIG_WRITER_JOB_ID="writer", **ownership)
        self._wait(marker.exists)
        jobs.cancel_job(self.repo, folder.name)
        (self.root / "continue-fault").touch()
        result = self._result(process)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertFalse((folder / "executed").exists())
        record = self._lease(folder)
        self.assertFalse(record["slot_held"], record)
        self.assertNotEqual(record["stage"], "released")
        self.assertFalse(Path(record["process"]["gate_path"]).exists())
        with patch.dict(os.environ, self._env(["plain.txt"])):
            with self.assertRaisesRegex(admission.AdmissionError, "overlap"):
                admission.reserve(self.repo, job_id="conflict", worker="codex", files=["plain.txt"],
                                  owner_session="another-owner")

    def test_parent_cancel_preserves_final_evidence_and_cancelled_result(self):
        import jobs
        process, folder = self._start("cancel-live", ["plain.txt"])
        self._wait(lambda: (folder / "executed").exists())
        jobs.cancel_job(self.repo, "cancel-live")
        result = self._result(process)
        self.assertEqual(result.returncode, 130, result.stderr)
        meta = json.loads((folder / "meta.json").read_text())
        evidence = json.loads((folder / "change-evidence.json").read_text())
        self.assertEqual(meta["status"], "cancelled")
        self.assertEqual(meta["files_changed"], ["plain.txt"])
        self.assertEqual(evidence["scoped_changed_paths"], ["plain.txt"])
        self.assertFalse(self._lease(folder)["slot_held"])
        self.assertEqual(self._lease(folder)["stage"], "verifying")


if __name__ == "__main__":
    unittest.main()
