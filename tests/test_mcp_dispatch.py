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

PICK_KEYS = {
    "kind",
    "worker",
    "spawn",
    "model",
    "effort",
    "native_agent",
    "reason",
    "parent_writes",
}
DISPATCH_TOOLS = (
    "rig_session",
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
    "rig_job_message",
)
CHILD_TOOLS = (
    "rig_job_doing",
    "rig_job_note",
    "rig_job_ask",
    "permission_prompt",
    "rig_job_inbox",
    "rig_job_show",
    "rig_memory",
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
                "RIG_JOB_ID",
                "RIG_JOB_DIR",
            )
        }
        os.environ["PATH"] = _stub_path(self.bins)
        os.environ["RIG_PARENT"] = "grok"
        os.environ["RIG_SKIP_MODEL_CATALOG"] = "1"
        os.environ.pop("CLAUDECODE", None)
        os.environ.pop("CLAUDE_CODE", None)
        os.environ.pop("RIG_THREAD", None)
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)

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
        self.assertEqual(names[0], "rig_session")
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
        self.assertIn("rig_job_message", listed_names)
        self.assertNotIn("rig_job_doing", listed_names)
        self.assertNotIn("permission_prompt", listed_names)

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
        job_dir = self.repo / ".rig" / "jobs" / job_id
        (job_dir / "stdout.log").write_text(
            '{"type":"tool_call","toolName":"read_file","rawInput":{"path":"README.md"}}\n'
        )
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
        result = json.loads((job_dir / "result.json").read_text())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"], "one-line result")
        self.assertFalse((job_dir / "stdout.log").exists())
        activity = json.loads((job_dir / "activity.json").read_text())
        self.assertTrue(any("read_file" in str(line) for line in activity.get("lines") or []))
        logged = rig_mcp.call_tool("rig_job_log", {"repo": str(self.repo), "id": job_id})
        self.assertIn("read_file", self._text(logged))

    def test_job_finish_keeps_undecodable_log(self):
        start = rig_mcp.call_tool(
            "rig_job_start",
            {"repo": str(self.repo), "worker": "grok", "role": "explorer"},
        )
        job_id = self._text(start).strip()
        job_dir = self.repo / ".rig" / "jobs" / job_id
        (job_dir / "stdout.log").write_text(
            'remote managed settings (permissions.deny): Invalid permission rule '
            '"Bash(eval $(wget*))" was skipped: Mismatched parentheses.\n'
        )
        out = rig_mcp.call_tool(
            "rig_job_finish",
            {
                "id": job_id,
                "repo": str(self.repo),
                "status": "ok",
                "summary": "native ok",
            },
        )
        self.assertNotIn("isError", out)
        self.assertTrue((job_dir / "stdout.log").is_file())
        self.assertFalse((job_dir / "activity.json").is_file())

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

    def test_child_tools_hide_parent_and_write_doing(self):
        job_id = "child-mcp"
        job_dir = self.repo / ".rig" / "jobs" / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "worker": "grok",
                    "role": "implement",
                    "status": "running",
                    "pid": os.getpid(),
                }
            )
        )
        os.environ["RIG_JOB_ID"] = job_id
        os.environ["RIG_JOB_DIR"] = str(job_dir)
        try:
            names = [t["name"] for t in rig_mcp.listed_tools()]
            for name in CHILD_TOOLS:
                self.assertIn(name, names)
            self.assertNotIn("rig_pick", names)
            self.assertNotIn("rig_job_wait", names)
            self.assertNotIn("rig_job_message", names)
            blocked = rig_mcp.call_tool("rig_pick", {"case": "x", "repo": str(self.repo)})
            self.assertTrue(blocked.get("isError"))
            doing = rig_mcp.call_tool("rig_job_doing", {"text": "edit jobs.py"})
            self.assertNotIn("isError", doing)
            self.assertIn("edit jobs.py", self._text(doing))
            job = jobs.load_job(job_dir)
            self.assertEqual(job["doing"], "edit jobs.py")
            note = rig_mcp.call_tool("rig_job_note", {"text": "checked tests"})
            self.assertNotIn("isError", note)
            msg = rig_mcp.call_tool(
                "rig_job_message",
                {"id": job_id, "text": "keep going", "repo": str(self.repo)},
            )
            self.assertTrue(msg.get("isError"))
        finally:
            os.environ.pop("RIG_JOB_ID", None)
            os.environ.pop("RIG_JOB_DIR", None)

        sent = rig_mcp.call_tool(
            "rig_job_message",
            {"id": job_id, "text": "use the listed files", "repo": str(self.repo)},
        )
        self.assertNotIn("isError", sent, sent)
        os.environ["RIG_JOB_ID"] = job_id
        os.environ["RIG_JOB_DIR"] = str(job_dir)
        try:
            pulled = rig_mcp.call_tool("rig_job_inbox", {})
            self.assertEqual(self._text(pulled), "use the listed files")
            empty = rig_mcp.call_tool("rig_job_inbox", {})
            self.assertEqual(self._text(empty), "(empty)")
        finally:
            os.environ.pop("RIG_JOB_ID", None)
            os.environ.pop("RIG_JOB_DIR", None)
        shown = jobs.load_job(job_dir)
        self.assertIn("edit jobs.py", "\n".join(shown["activities"]))
        self.assertIn("checked tests", "\n".join(shown["activities"]))
        os.environ["RIG_JOB_ID"] = job_id
        os.environ["RIG_JOB_DIR"] = str(job_dir)
        try:
            mem = rig_mcp.call_tool("rig_memory", {"repo": str(self.repo)})
            self.assertNotIn("isError", mem)
            other = rig_mcp.call_tool(
                "rig_job_show",
                {"id": "not-this-job", "repo": str(self.repo)},
            )
            self.assertTrue(other.get("isError"))
            self.assertIn("own job", self._text(other))
            own = rig_mcp.call_tool("rig_job_show", {"repo": str(self.repo)})
            self.assertNotIn("isError", own)
            self.assertIn(job_id, self._text(own))
        finally:
            os.environ.pop("RIG_JOB_ID", None)
            os.environ.pop("RIG_JOB_DIR", None)

    def test_child_ask_round_trips_allow(self):
        import threading
        import time

        job_id = "child-ask"
        job_dir = self.repo / ".rig" / "jobs" / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "meta.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "worker": "claude",
                    "role": "implement",
                    "status": "running",
                    "pid": os.getpid(),
                }
            )
        )
        os.environ["RIG_JOB_ID"] = job_id
        os.environ["RIG_JOB_DIR"] = str(job_dir)

        def later():
            time.sleep(0.2)
            jobs.answer_pending(jobs.load_job(job_dir), "allow")

        threading.Thread(target=later, daemon=True).start()
        try:
            out = rig_mcp.call_tool(
                "rig_job_ask",
                {"preview": "ssh to the vm", "repo": str(self.repo)},
            )
            self.assertNotIn("isError", out)
            self.assertIn("allow", self._text(out))
        finally:
            os.environ.pop("RIG_JOB_ID", None)
            os.environ.pop("RIG_JOB_DIR", None)

    def test_parent_cannot_call_child_doing(self):
        out = rig_mcp.call_tool("rig_job_doing", {"text": "nope", "repo": str(self.repo)})
        self.assertTrue(out.get("isError"))

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

    def test_session_returns_memory_jobs_status_pick(self):
        rig_mcp.call_tool(
            "rig_memory_add",
            {"repo": str(self.repo), "fact": "session packs four calls"},
        )
        out = rig_mcp.call_tool(
            "rig_session",
            {"repo": str(self.repo), "case": "add a header", "role": "implement"},
        )
        self.assertNotIn("isError", out)
        text = self._text(out)
        self.assertIn("# memory", text)
        self.assertIn("session packs four calls", text)
        self.assertIn("# jobs", text)
        self.assertIn("# status", text)
        self.assertIn("live=grok", text)
        self.assertIn("# pick", text)
        self.assertIn('"parent_writes": true', text)
        self.assertIn('"spawn": "native"', text)

    def test_session_stay_does_not_spawn(self):
        out = rig_mcp.call_tool(
            "rig_session",
            {"repo": str(self.repo), "case": "anything", "role": "stay"},
        )
        text = self._text(out)
        self.assertIn('"spawn": "stay"', text)
        self.assertNotIn("run-worker.sh", text)

    def test_session_needs_case(self):
        out = rig_mcp.call_tool("rig_session", {"repo": str(self.repo)})
        self.assertTrue(out.get("isError"))
        self.assertIn("case", self._text(out).lower())

    def test_pick_exclude_skips_native(self):
        out = rig_mcp.call_tool(
            "rig_pick",
            {
                "repo": str(self.repo),
                "case": "add a header",
                "role": "implement",
                "exclude": "grok",
            },
        )
        choice = json.loads(self._text(out))
        self.assertEqual(choice["spawn"], "none")
        self.assertFalse(choice["parent_writes"])


if __name__ == "__main__":
    unittest.main()
