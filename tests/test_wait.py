#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ask  # noqa: E402
import jobs  # noqa: E402
import rig_mcp  # noqa: E402


def _run_rig(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged["RIG_HOME"] = str(ROOT)
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    merged["RIG_SKIP_UPDATE_CHECK"] = "1"
    if env:
        merged.update(env)
    return subprocess.run(
        [str(ROOT / "bin" / "rig"), *args],
        cwd=repo,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def _alarm_run(seconds: float, fn):
    def _raise(_signum, _frame):
        raise TimeoutError(f"hung after {seconds}s")

    prev = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prev)


def _mcp_ndjson(messages: list, timeout: float = 3) -> subprocess.CompletedProcess:
    payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    return subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "rig_mcp.py")],
        input=payload,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _init_and_wait(repo: Path, job_id: str, meta: dict | None = None) -> list:
    params = {
        "name": "rig_job_wait",
        "arguments": {"repo": str(repo), "id": job_id},
    }
    if meta is not None:
        params["_meta"] = meta
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": params,
        },
    ]


class WaitContract(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        self.d = self.repo / ".rig" / "jobs" / "wait-job"
        self.d.mkdir(parents=True)
        (self.d / "brief.md").write_text("do the work\n")
        self._write_meta("running")

    def tearDown(self):
        self.td.cleanup()

    def _write_meta(self, status: str) -> None:
        (self.d / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": "wait-job",
                    "worker": "claude",
                    "role": "implement",
                    "status": status,
                    "pid": os.getpid(),
                    "model": "claude-sonnet-5",
                    "effort": "medium",
                    "summary": "done" if status == "ok" else "",
                }
            )
        )

    def _finish_ok(self) -> None:
        self._write_meta("ok")
        (self.d / "result.json").write_text(json.dumps({"status": "ok"}))

    def test_timeout_0_running_is_124(self):
        code, text = jobs.wait_job(self.repo, "wait-job", timeout=0)
        self.assertEqual(code, 124)
        self.assertIn("RUNNING wait-job", text)

    def test_omit_timeout_already_ask_is_2(self):
        ask.write_ask(self.d, "Bash", {"command": "git status"}, "t-ask")
        code, text = _alarm_run(3, lambda: jobs.wait_job(self.repo, "wait-job"))
        self.assertEqual(code, 2)
        self.assertIn("ASK wait-job", text)

    def test_omit_timeout_already_ok_is_0(self):
        self._finish_ok()
        code, text = _alarm_run(3, lambda: jobs.wait_job(self.repo, "wait-job"))
        self.assertEqual(code, 0)
        self.assertIn("wait-job", text)

    def test_timeout_cap_running_is_124_quickly(self):
        t0 = time.time()
        code, text = jobs.wait_job(self.repo, "wait-job", timeout=0.4)
        elapsed = time.time() - t0
        self.assertEqual(code, 124)
        self.assertIn("RUNNING wait-job", text)
        self.assertLess(elapsed, 1.0)

    def test_mcp_omit_timeout_already_ask(self):
        ask.write_ask(self.d, "Bash", {"command": "git status"}, "t-mcp-ask")
        out = _alarm_run(
            3,
            lambda: rig_mcp.call_tool(
                "rig_job_wait",
                {"repo": str(self.repo), "id": "wait-job"},
            ),
        )
        self.assertIn("ASK", out["content"][0]["text"])
        self.assertNotIn("isError", out)

    def test_mcp_omit_timeout_already_ok(self):
        self._finish_ok()
        out = _alarm_run(
            3,
            lambda: rig_mcp.call_tool(
                "rig_job_wait",
                {"repo": str(self.repo), "id": "wait-job"},
            ),
        )
        text = out["content"][0]["text"]
        self.assertNotIn("isError", out)
        self.assertIn("wait-job", text)
        self.assertIn("ok", text.lower())

    def test_blocking_wait_until_ok(self):
        def later():
            time.sleep(0.3)
            self._finish_ok()

        threading.Thread(target=later, daemon=True).start()
        t0 = time.time()
        code, text = _alarm_run(
            3, lambda: jobs.wait_job(self.repo, "wait-job", timeout=None)
        )
        elapsed = time.time() - t0
        self.assertEqual(code, 0)
        self.assertIn("wait-job", text)
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 3.0)

    def test_jobs_py_wait_cli_no_timeout_finished(self):
        self._finish_ok()
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "jobs.py"),
                "wait",
                "wait-job",
                "--repo",
                str(self.repo),
            ],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("wait-job", proc.stdout)

    def test_on_tick_running_then_ok(self):
        ticks = []

        def later():
            time.sleep(0.3)
            self._finish_ok()

        threading.Thread(target=later, daemon=True).start()
        code, _text = _alarm_run(
            3, lambda: jobs.wait_job(self.repo, "wait-job", on_tick=ticks.append)
        )
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(ticks), 1)
        self.assertEqual(ticks[0]["effective"], "running")

    def test_on_tick_change_only(self):
        ticks = []
        code, _text = _alarm_run(
            3,
            lambda: jobs.wait_job(
                self.repo, "wait-job", timeout=0.45, on_tick=ticks.append
            ),
        )
        self.assertEqual(code, 124)
        self.assertLess(len(ticks), 10)
        self.assertGreaterEqual(len(ticks), 1)
        self.assertEqual(ticks[0]["effective"], "running")
        running = [t for t in ticks if t.get("effective") == "running"]
        self.assertEqual(len(running), 1)

    def test_mcp_stdio_progress_with_token(self):
        (self.d / "stdout.log").write_text(
            '{"type":"tool_call","toolName":"read_file","rawInput":{"path":"README.md"}}\n'
        )

        def later():
            time.sleep(0.3)
            self._finish_ok()

        threading.Thread(target=later, daemon=True).start()
        proc = _mcp_ndjson(
            _init_and_wait(self.repo, "wait-job", {"progressToken": "tok-1"}),
            timeout=3,
        )
        self.assertIn("notifications/progress", proc.stdout, proc.stderr)
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        progress = [m for m in lines if m.get("method") == "notifications/progress"]
        self.assertTrue(progress, proc.stdout)
        first = progress[0]
        self.assertNotIn("id", first)
        self.assertEqual(first.get("jsonrpc"), "2.0")
        self.assertEqual(first["params"]["progressToken"], "tok-1")
        self.assertEqual(first["params"]["progress"], 1)
        self.assertIn("running", first["params"]["message"])
        self.assertIn("wait-job", first["params"]["message"])
        progress_idx = next(
            i for i, m in enumerate(lines) if m.get("method") == "notifications/progress"
        )
        result_idx = next(i for i, m in enumerate(lines) if m.get("id") == 2)
        self.assertLess(progress_idx, result_idx)
        result = lines[result_idx]["result"]
        self.assertIn("wait-job", result["content"][0]["text"])
        self.assertNotIn("isError", result)

    def test_mcp_stdio_no_progress_without_meta(self):
        def later():
            time.sleep(0.3)
            self._finish_ok()

        threading.Thread(target=later, daemon=True).start()
        proc = _mcp_ndjson(_init_and_wait(self.repo, "wait-job"), timeout=3)
        self.assertNotIn("notifications/progress", proc.stdout, proc.stderr)
        self.assertIn('"id": 2', proc.stdout)
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        result = next(m for m in lines if m.get("id") == 2)
        self.assertIn("wait-job", result["result"]["content"][0]["text"])


class InitAgentsWait(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_init_agents_one_wait_not_loop(self):
        proc = _run_rig(self.repo, "init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = (self.repo / "AGENTS.md").read_text()
        start = text.split("<!-- rig:start -->", 1)[1].split("<!-- rig:end -->", 1)[0]
        self.assertIn("rig job wait", text)
        self.assertIn("rig job allow", text)
        self.assertIn("Never kill", text)
        self.assertIn("rig_job_wait", text)
        self.assertIn("MCP", start)
        self.assertNotIn("Loop `rig job wait`", text)
        self.assertNotIn("'", start)


if __name__ == "__main__":
    unittest.main()
