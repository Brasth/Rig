#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harness  # noqa: E402
import jobs  # noqa: E402
import rig_mcp  # noqa: E402
import route  # noqa: E402

PICK_KEYS = {"kind", "worker", "spawn", "model", "effort", "native_agent", "reason"}
DISPATCH_TOOLS = (
    "rig_pick",
    "rig_status",
    "rig_job_start",
    "rig_job_finish",
    "rig_job_record",
)
EXISTING_TOOLS = (
    "rig_jobs",
    "rig_job_show",
    "rig_job_log",
    "rig_job_wait",
    "rig_job_allow",
    "rig_job_deny",
    "rig_memory",
    "rig_memory_add",
)


def _stub_path(extra: Path | None = None) -> str:
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


def _mcp_ndjson(messages: list, env: dict | None = None, timeout: float = 3) -> subprocess.CompletedProcess:
    payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "rig_mcp.py")],
        input=payload,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=merged,
    )


def _grok_only_harness() -> str:
    return (
        'parent = "codex"\n\n[workers]\n'
        "codex = false\ngrok = true\nclaude = false\ncursor = false\n"
        "opencode = false\nomp = false\npi = false\nagy = false\n"
    )


class McpDispatch(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig" / "jobs").mkdir(parents=True)
        (self.repo / ".rig" / "harness.toml").write_text(_grok_only_harness())
        self.bins = self.repo / "bins"
        self.bins.mkdir()
        _fake_bin(self.bins, "grok")
        self._env = {
            k: os.environ.get(k)
            for k in (
                "PATH",
                "RIG_PARENT",
                "CLAUDECODE",
                "CLAUDE_CODE",
                "RIG_THREAD",
                "RIG_SKIP_MODEL_CATALOG",
            )
        }
        os.environ["PATH"] = _stub_path(self.bins)
        os.environ["RIG_PARENT"] = "grok"
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1"
        os.environ.pop("CLAUDECODE", None)
        os.environ.pop("CLAUDE_CODE", None)
        os.environ.pop("RIG_THREAD", None)

    def tearDown(self):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def _text(self, out: dict) -> str:
        return out["content"][0]["text"]

    def test_tools_list_includes_dispatch(self):
        names = [t["name"] for t in rig_mcp.TOOLS]
        for name in DISPATCH_TOOLS + EXISTING_TOOLS:
            self.assertIn(name, names)
        self.assertNotIn("rig_spawn", names)
        self.assertFalse(any("run-worker" in n or n.endswith("_spawn") for n in names))

        proc = _mcp_ndjson(
            [
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
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        listed = next(m for m in lines if m.get("id") == 2)
        listed_names = [t["name"] for t in listed["result"]["tools"]]
        for name in DISPATCH_TOOLS:
            self.assertIn(name, listed_names)

    def test_live_parent_honors_rig_parent(self):
        os.environ["RIG_PARENT"] = "grok"
        self.assertEqual(harness.live_parent(), "grok")
        os.environ["RIG_PARENT"] = "codex"
        self.assertEqual(harness.live_parent(), "codex")

    def test_pick_grok_parent_is_native_not_run_worker(self):
        out = rig_mcp.call_tool(
            "rig_pick",
            {"case": "add a header", "role": "implement", "repo": str(self.repo)},
        )
        self.assertNotIn("isError", out)
        choice = json.loads(self._text(out))
        self.assertEqual(set(choice), PICK_KEYS)
        self.assertEqual(choice["spawn"], "native")
        self.assertEqual(choice["worker"], "grok")
        self.assertNotEqual(choice["spawn"], "run-worker")
        expected = route.pick("grok", harness.effective_workers(self.repo, "grok"), "implement", "add a header")
        self.assertEqual(choice, expected)
        self.assertNotIn("grok", harness.effective_workers(self.repo, "grok"))

    def test_pick_stay_is_stay(self):
        out = rig_mcp.call_tool(
            "rig_pick",
            {"case": "anything", "role": "stay", "repo": str(self.repo)},
        )
        choice = json.loads(self._text(out))
        self.assertEqual(choice["spawn"], "stay")
        self.assertEqual(choice["worker"], "grok")

    def test_pick_json_keys_match_route(self):
        out = rig_mcp.call_tool(
            "rig_pick",
            {"case": "locate the auth middleware", "role": "explore", "repo": str(self.repo)},
        )
        choice = json.loads(self._text(out))
        self.assertEqual(set(choice), PICK_KEYS)
        live = harness.live_parent()
        expected = route.pick(
            live,
            harness.effective_workers(self.repo, live),
            "explore",
            "locate the auth middleware",
        )
        self.assertEqual(choice, expected)

    def test_pick_invalid_role_errors(self):
        out = rig_mcp.call_tool(
            "rig_pick",
            {"case": "add a header", "role": "nope", "repo": str(self.repo)},
        )
        self.assertTrue(out.get("isError"))
        self.assertIn("role", self._text(out).lower())
        self.assertNotIn("native", self._text(out))

    def test_status_live_from_rig_parent(self):
        os.environ["RIG_PARENT"] = "grok"
        out = rig_mcp.call_tool("rig_status", {"repo": str(self.repo)})
        text = self._text(out)
        self.assertNotIn("isError", out)
        self.assertIn("live=grok", text)
        self.assertIn("preferred=codex", text)
        self.assertIn("jobs:", text)
        self.assertRegex(text, r"effective:\s+\S+")

    def test_job_start_writes_running_meta_no_child(self):
        os.environ["RIG_THREAD"] = "parent-thread-mcp"
        out = rig_mcp.call_tool(
            "rig_job_start",
            {
                "repo": str(self.repo),
                "worker": "grok",
                "role": "implement",
                "summary": "add a header",
            },
        )
        self.assertNotIn("isError", out)
        job_id = self._text(out).strip().splitlines()[-1]
        self.assertTrue(job_id)
        job_dir = self.repo / ".rig" / "jobs" / job_id
        meta = json.loads((job_dir / "meta.json").read_text())
        self.assertEqual(meta["status"], "running")
        self.assertEqual(meta["worker"], "grok")
        self.assertEqual(meta["role"], "implement")
        self.assertEqual(meta.get("thread"), "parent-thread-mcp")
        self.assertTrue((job_dir / "started_at").is_file())
        self.assertNotIn("pid", meta)
        listed = jobs.list_jobs(self.repo)
        self.assertTrue(any(j["job_id"] == job_id and j["status"] == "running" for j in listed))

    def test_job_finish_writes_result(self):
        start = rig_mcp.call_tool(
            "rig_job_start",
            {"repo": str(self.repo), "worker": "grok", "role": "explorer"},
        )
        job_id = self._text(start).strip()
        (self.repo / ".rig" / "jobs" / job_id / "stdout.log").write_text("noise\n")
        out = rig_mcp.call_tool(
            "rig_job_finish",
            {
                "id": job_id,
                "repo": str(self.repo),
                "status": "ok",
                "summary": "one-line result",
            },
        )
        self.assertNotIn("isError", out)
        text = self._text(out)
        self.assertIn(job_id, text)
        self.assertIn("status=ok", text)
        self.assertIn("result.json", text)
        result = json.loads((self.repo / ".rig" / "jobs" / job_id / "result.json").read_text())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"], "one-line result")
        self.assertFalse((self.repo / ".rig" / "jobs" / job_id / "stdout.log").exists())

    def test_job_finish_fail(self):
        start = rig_mcp.call_tool(
            "rig_job_start",
            {"repo": str(self.repo), "worker": "grok", "id": "finish-fail"},
        )
        self.assertEqual(self._text(start).strip(), "finish-fail")
        out = rig_mcp.call_tool(
            "rig_job_finish",
            {"id": "finish-fail", "repo": str(self.repo), "status": "fail", "summary": "boom"},
        )
        result = json.loads(
            (self.repo / ".rig" / "jobs" / "finish-fail" / "result.json").read_text()
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("status=fail", self._text(out))

    def test_job_finish_needs_id(self):
        out = rig_mcp.call_tool("rig_job_finish", {"repo": str(self.repo)})
        self.assertTrue(out.get("isError"))

    def test_job_record_writes_ok(self):
        out = rig_mcp.call_tool(
            "rig_job_record",
            {
                "repo": str(self.repo),
                "worker": "grok",
                "role": "explorer",
                "status": "ok",
                "summary": "one-line result",
            },
        )
        self.assertNotIn("isError", out)
        text = self._text(out)
        self.assertIn("status=ok", text)
        self.assertIn("result.json", text)
        job_id = text.split()[1]
        result = json.loads((self.repo / ".rig" / "jobs" / job_id / "result.json").read_text())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["worker"], "grok")
        self.assertEqual(result["role"], "explorer")

    def test_existing_wait_allow_memory_still_work(self):
        listed = rig_mcp.call_tool("rig_jobs", {"repo": str(self.repo)})
        self.assertNotIn("isError", listed)
        mem = rig_mcp.call_tool("rig_memory", {"repo": str(self.repo)})
        self.assertIn("no memory yet", self._text(mem))
        added = rig_mcp.call_tool(
            "rig_memory_add",
            {"repo": str(self.repo), "fact": "MCP pick uses live parent"},
        )
        self.assertEqual(self._text(added), "added")


if __name__ == "__main__":
    unittest.main()
