#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import admission  # noqa: E402
import harness  # noqa: E402
import jobs  # noqa: E402
import rig_mcp  # noqa: E402
import route  # noqa: E402

PICK_KEYS = {
    "provider",
    "provider_source",
    "kind",
    "worker",
    "spawn",
    "model",
    "effort",
    "native_agent",
    "reason",
    "parent_writes",
    "classification_source",
    "classification_rule",
    "executor_kind",
    "model_source",
    "routing",
    "execution_strategy",
}
DISPATCH_TOOLS = (
    "rig_session",
    "rig_pick",
    "rig_status",
    "rig_job_start",
    "rig_job_finish",
    "rig_job_record",
)
BILLING_TOOLS = (
    "rig_billing_report",
    "rig_billing_import",
    "rig_billing_sync",
    "rig_benchmark_report",
    "rig_benchmark_create",
    "rig_benchmark_outcome",
)
EXISTING_TOOLS = (
    "rig_jobs",
    "rig_job_show",
    "rig_job_log",
    "rig_job_wait",
    "rig_job_allow",
    "rig_job_deny",
    "rig_job_cancel",
    "rig_memory",
    "rig_memory_add",
    "rig_job_message",
    "rig_queue_add",
    "rig_queue_list",
    "rig_queue_cancel",
    "rig_queue_claim",
    "rig_queue_unclaim",
    "rig_queue_spawned",
    "rig_job_recover_cancelled",
    "rig_job_recover_parent_write",
    "rig_job_recover_wrapper_receipt",
    "rig_job_break_glass_close",
)
WORKFLOW_TOOLS = (
    "rig_workflow_create",
    "rig_workflows",
    "rig_workflow_show",
    "rig_workflow_advance",
    "rig_workflow_wait",
    "rig_workflow_extend",
    "rig_workflow_resolve",
    "rig_workflow_approve",
    "rig_workflow_cancel",
    "rig_workflow_report",
    "rig_job_coordination_reply",
)
CHILD_TOOLS = (
    "rig_job_doing",
    "rig_job_note",
    "rig_job_ask",
    "permission_prompt",
    "rig_job_inbox",
    "rig_job_show",
    "rig_memory",
    "rig_job_coordination_request",
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
                "RIG_REPO",
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
        os.environ.pop("RIG_REPO", None)

    def tearDown(self):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.td.cleanup()

    def _text(self, out: dict) -> str:
        return out["content"][0]["text"]

    def _ownership(self, start):
        lease = start["structuredContent"]
        return {**{key: lease[key] for key in ("reservation_id", "attempt_id", "owner_token")},
                "owner_session": lease["owner"].get("session_id", "")}

    def _completed(self, start, status="ok"):
        lease = start["structuredContent"]
        completion = {"kind": "parent_task", "completed": True} if lease["owner"]["kind"] == "parent" else {
            "kind": "native_child", "agent_id": lease["job_id"] + "-agent", "terminal": True, "outcome": status}
        return {**self._ownership(start), "completion": completion}

    def test_tools_list_includes_dispatch(self):
        names = [t["name"] for t in rig_mcp.TOOLS]
        for name in DISPATCH_TOOLS + EXISTING_TOOLS + WORKFLOW_TOOLS + BILLING_TOOLS:
            self.assertIn(name, names)
        self.assertEqual(names[0], "rig_session")
        self.assertNotIn("rig_spawn", names)
        self.assertNotIn("rig_job_coordination_request", names)
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
        self.assertIn("rig_workflow_create", listed_names)
        self.assertIn("rig_job_coordination_reply", listed_names)
        self.assertNotIn("rig_job_doing", listed_names)
        self.assertNotIn("permission_prompt", listed_names)
        self.assertNotIn("rig_job_coordination_request", listed_names)

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
                **self._completed(start),
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
                **self._completed(start),
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
            {"id": "finish-fail", "repo": str(self.repo), "status": "fail", "summary": "boom", **self._completed(start, "fail")},
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

    def test_finish_status_enum_includes_cancelled_record_does_not(self):
        schema = {tool["name"]: tool for tool in rig_mcp.TOOLS}
        self.assertIn("cancelled", schema["rig_job_finish"]["inputSchema"]["properties"]["status"]["enum"])
        self.assertNotIn("cancelled", schema["rig_job_record"]["inputSchema"]["properties"]["status"]["enum"])
        denied = rig_mcp.call_tool(
            "rig_job_record",
            {"repo": str(self.repo), "worker": "grok", "status": "cancelled", "summary": "no"},
        )
        self.assertTrue(denied.get("isError"))
        self.assertIn("ok|fail|timeout", self._text(denied))

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
            self.assertNotIn("rig_cu_capture", names)
            self.assertNotIn("rig_cu_act", names)
            self.assertNotIn("rig_cu_confirm", names)
            self.assertNotIn("rig_cu_record", names)
            self.assertNotIn("rig_job_wait", names)
            self.assertNotIn("rig_job_cancel", names)
            self.assertNotIn("rig_job_recover_cancelled", names)
            self.assertNotIn("rig_job_recover_parent_write", names)
            self.assertNotIn("rig_job_recover_wrapper_receipt", names)
            self.assertNotIn("rig_job_break_glass_close", names)
            self.assertNotIn("rig_billing_import", names)
            self.assertNotIn("rig_job_message", names)
            self.assertNotIn("rig_queue_add", names)
            self.assertNotIn("rig_queue_claim", names)
            self.assertIn("rig_job_coordination_request", names)
            for name in WORKFLOW_TOOLS:
                self.assertNotIn(name, names)
            blocked = rig_mcp.call_tool("rig_pick", {"case": "x", "repo": str(self.repo)})
            self.assertTrue(blocked.get("isError"))
            recover = rig_mcp.call_tool(
                "rig_job_recover_cancelled",
                {"id": job_id, "repo": str(self.repo), "reservation_id": "x", "attempt_id": "y",
                 "owner_token": "z", "rationale": "child must not recover"},
            )
            self.assertTrue(recover.get("isError"))
            self.assertIn("not a child tool", self._text(recover))
            parent_recover = rig_mcp.call_tool(
                "rig_job_recover_parent_write",
                {"id": job_id, "repo": str(self.repo), "confirmed_stopped": True,
                 "rationale": "child must not recover parent writes"},
            )
            self.assertTrue(parent_recover.get("isError"))
            self.assertIn("not a child tool", self._text(parent_recover))
            receipt = rig_mcp.call_tool(
                "rig_job_recover_wrapper_receipt",
                {"id": job_id, "repo": str(self.repo)},
            )
            self.assertTrue(receipt.get("isError"))
            self.assertIn("not a child tool", self._text(receipt))
            glass = rig_mcp.call_tool(
                "rig_job_break_glass_close",
                {"id": job_id, "repo": str(self.repo), "credentials_path": "/tmp/x",
                 "confirmed_stopped": True, "rationale": "child must not break-glass"},
            )
            self.assertTrue(glass.get("isError"))
            self.assertIn("not a child tool", self._text(glass))
            inbox0 = rig_mcp.call_tool("rig_job_inbox", {})
            self.assertNotIn("isError", inbox0)
            self.assertEqual(self._text(inbox0), "(empty)")
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
            inbox0 = rig_mcp.call_tool("rig_job_inbox", {})
            self.assertNotIn("isError", inbox0)
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

    def _session_job(self, job_id, status="ok", *, files=None, mtime=None):
        path = self.repo / ".rig" / "jobs" / job_id
        path.mkdir()
        (path / "meta.json").write_text(json.dumps({
            "job_id": job_id, "worker": "grok", "role": "implement",
            "status": status, "summary": job_id, "files": files or [],
            "model": "grok-build-0.2", "effort": "high",
            "executor_kind": "native_child", "model_source": "selected",
        }))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_session_reuses_one_history_and_worker_snapshot(self):
        root = self.repo / ".rig" / "jobs"
        self._session_job("known-writer", "running", files=["scripts/jobs.py"])
        self._session_job("unknown-writer", "running")
        (root / "empty").mkdir()
        (root / "corrupt").mkdir()
        (root / "corrupt" / "meta.json").write_text("{invalid")
        original_iterdir = Path.iterdir
        for compact in (False, True):
            for as_json in (False, True):
                with self.subTest(compact=compact, as_json=as_json):
                    scans = []

                    def count_iterdir(path):
                        if path == root:
                            scans.append(path)
                        return original_iterdir(path)

                    with (
                        mock.patch.object(Path, "iterdir", count_iterdir),
                        mock.patch.object(jobs, "load_job", wraps=jobs.load_job) as load,
                        mock.patch.object(harness, "effective_workers", wraps=harness.effective_workers) as workers,
                    ):
                        text = rig_mcp.format_session(
                            self.repo, "advise", "stay", as_json=as_json, compact=compact,
                        )
                    self.assertEqual(len(scans), 1)
                    self.assertEqual(load.call_count, 4)
                    self.assertEqual(workers.call_count, 1)
                    if as_json:
                        payload = json.loads(text)
                        self.assertEqual(len(payload["jobs"]), 2)
                        if compact:
                            self.assertEqual(payload["status"]["jobs"], 4)
                            self.assertEqual(payload["history"]["invalid_directories"], 2)
                        else:
                            self.assertEqual(set(payload), {"memory", "jobs", "workflows", "status", "pick"})
                            self.assertIsInstance(payload["memory"], str)
                            self.assertIsInstance(payload["status"], str)
                            self.assertIsInstance(payload["pick"], dict)
                            self.assertIn("jobs: 4", payload["status"])
                    else:
                        self.assertEqual(text.count("STATE                 AGENT"), 1 if compact else 2)
                        self.assertIn("occupied  unknown", text)
                        self.assertIn("unknown-writer", text)
                        self.assertIn("jobs: 4", text)

    def test_session_labeled_occupancy_uses_the_same_loaded_jobs(self):
        self._session_job("known-writer", "running", files=["scripts/jobs.py"])
        for as_json in (False, True):
            with self.subTest(as_json=as_json):
                with (
                    mock.patch.object(jobs, "list_jobs", wraps=jobs.list_jobs) as listing,
                    mock.patch.object(jobs, "load_job", wraps=jobs.load_job) as load,
                ):
                    text = rig_mcp.format_session(self.repo, "advise", "stay", as_json=as_json)
                self.assertEqual(listing.call_count, 1)
                self.assertEqual(load.call_count, 1)
                self.assertIn("scripts/jobs.py", text)

    def test_compact_history_keeps_all_active_and_bounds_terminal_rows(self):
        root = self.repo / ".rig" / "jobs"
        for index in range(1000):
            self._session_job(f"terminal-{index:04d}", mtime=1000 + index)
        for status in ("running", "ask", "reserved"):
            self._session_job(f"active-{status}", status, mtime=1)
        (root / "empty").mkdir()
        (root / "corrupt").mkdir()
        (root / "corrupt" / "meta.json").write_text("[]")
        for limit in (0, 10, 100):
            with self.subTest(limit=limit):
                payload = json.loads(rig_mcp.format_session(
                    self.repo, "advise", "stay", as_json=True, compact=True,
                    terminal_limit=limit,
                ))
                self.assertEqual(payload["schema_version"], 2)
                self.assertEqual(payload["mode"], "compact")
                self.assertIsInstance(payload["memory"], str)
                self.assertIsInstance(payload["status"], dict)
                self.assertEqual(payload["history"], {
                    "total": 1003, "shown": 3 + limit, "omitted": 1000 - limit,
                    "invalid_directories": 2,
                })
                self.assertEqual(payload["status"]["jobs"], 1005)
                rows = payload["jobs"]
                self.assertEqual({row["job_id"] for row in rows[:3]}, {
                    "active-running", "active-ask", "active-reserved",
                })
                self.assertEqual([row["job_id"] for row in rows[3:]], [
                    f"terminal-{index:04d}" for index in range(999, 999 - limit, -1)
                ])
                for row in rows:
                    self.assertNotIn("activities", row)
                    self.assertNotIn("log", row)
                    self.assertNotIn("dir", row)
                    self.assertEqual(row["model_source"], "selected")

    def test_compact_mcp_schema_and_validation(self):
        schema = next(tool["inputSchema"] for tool in rig_mcp.TOOLS if tool["name"] == "rig_session")
        self.assertEqual(schema["properties"]["compact"]["default"], False)
        self.assertEqual(schema["properties"]["terminal_limit"]["default"], 10)
        self.assertEqual(schema["properties"]["terminal_limit"]["minimum"], 0)
        self.assertEqual(schema["properties"]["terminal_limit"]["maximum"], 100)
        args = {"repo": str(self.repo), "case": "advise", "role": "stay", "compact": True}
        out = rig_mcp.call_tool("rig_session", args)
        self.assertEqual(json.loads(self._text(out))["mode"], "compact")
        for field, values in (("compact", [1, "true", None]), ("terminal_limit", [-1, 101, True, 1.5, "10", None])):
            for value in values:
                with self.subTest(field=field, value=value):
                    out = rig_mcp.call_tool("rig_session", {**args, field: value})
                    self.assertTrue(out.get("isError"))
                    self.assertIn(field, self._text(out))

    def test_parent_metadata_pick_and_session_is_explicit(self):
        for name in ("rig_pick", "rig_session"):
            for model in ("", "actual-parent-model"):
                with self.subTest(tool=name, model=model):
                    args = {"repo": str(self.repo), "case": "fix header", "role": "implement"}
                    if name == "rig_session":
                        args["compact"] = True
                    if model:
                        args.update(parent_model=model, parent_effort="high")
                    out = rig_mcp.call_tool(name, args)
                    self.assertNotIn("isError", out, out)
                    choice = json.loads(self._text(out))
                    if name == "rig_session":
                        choice = choice["pick"]
                    self.assertEqual(choice["model"], model)
                    self.assertEqual(choice["effort"], "high" if model else "")
                    self.assertEqual(choice["model_source"], "observed" if model else "unknown")
                    self.assertEqual(choice["executor_kind"], "parent")

    def test_parent_job_metadata_start_and_record(self):
        for name in ("rig_job_start", "rig_job_record"):
            for explicit in (False, True):
                with self.subTest(tool=name, explicit=explicit):
                    job_id = f"{name}-{explicit}"
                    args = {
                        "repo": str(self.repo), "worker": "grok", "role": "parent", "id": job_id,
                    }
                    if explicit:
                        args.update(model="actual-parent-model", effort="high", executor_kind="parent")
                    out = rig_mcp.call_tool(name, args)
                    self.assertNotIn("isError", out, out)
                    meta = json.loads((self.repo / ".rig" / "jobs" / job_id / "meta.json").read_text())
                    self.assertEqual(meta["executor_kind"], "parent")
                    self.assertEqual(meta["model"], "actual-parent-model" if explicit else "")
                    self.assertEqual(meta["model_source"], "observed" if explicit else "unknown")
                    if name == "rig_job_start":
                        done = rig_mcp.call_tool("rig_job_finish", {"repo": str(self.repo), "id": job_id, **self._completed(out)})
                        self.assertFalse(done.get("isError"), done)
                        finished = jobs.load_job(self.repo / ".rig" / "jobs" / job_id)
                        self.assertEqual(finished["model"], meta["model"])
                        self.assertEqual(finished["model_source"], meta["model_source"])
                        closed = rig_mcp.call_tool("rig_job_close", {"repo": str(self.repo), "id": job_id,
                            "rationale": "Metadata fixture complete", **self._ownership(out)})
                        self.assertFalse(closed.get("isError"), closed)

    def test_mcp_execution_metadata_validation(self):
        for tool in ("rig_job_start", "rig_job_record"):
            for values in ({"executor_kind": "wrapper"}, {"model": 1}, {"effort": True}):
                with self.subTest(tool=tool, values=values):
                    out = rig_mcp.call_tool(tool, {"repo": str(self.repo), **values})
                    self.assertTrue(out.get("isError"))
        self.assertEqual(jobs.list_jobs(self.repo), [])

    def test_wait_request_pins_partial_target_for_refresh_and_cancel(self):
        target = self._session_job("original-target", "running")
        bound, resume = threading.Event(), threading.Event()
        results = []
        runtime = rig_mcp.Runtime(rig_mcp._execute_request, results.append, rig_mcp._abort_request)
        original = jobs.wait_job
        def held_wait(*args, **kwargs):
            bound.set()
            resume.wait(2)
            return original(*args, **kwargs)
        with mock.patch.object(rig_mcp, "_runtime", runtime), mock.patch.object(jobs, "wait_job", side_effect=held_wait):
            runtime.start("pinned-request", "rig_job_wait", {"repo": str(self.repo), "id": "original"}, {})
            self.assertTrue(bound.wait(2))
            target.rename(self.repo / "retired-target")
            replacement = self._session_job("new-original-target", "running")
            runtime.cancel("pinned-request")
            resume.set()
            runtime.shutdown()
        self.assertFalse((replacement / "cancel.json").exists())
        self.assertFalse(target.exists())
        self.assertEqual(results, [])

    def test_child_wait_rejected_before_history_lookup_or_registration(self):
        message = {"id": "child-wait", "method": "tools/call", "params": {
            "name": "rig_job_wait", "arguments": {"repo": str(self.repo)}}}
        with (mock.patch.dict(os.environ, {"RIG_JOB_ID": "child"}),
              mock.patch.object(jobs, "resolve_job_paths", side_effect=AssertionError("child history access")),
              mock.patch.object(rig_mcp.threading, "Thread", side_effect=AssertionError("child wait thread"))):
            reply = rig_mcp.handle(message)
        self.assertTrue(reply["result"].get("isError"))
        self.assertIn("not a child tool", self._text(reply["result"]))

    def test_wait_lookup_io_failure_returns_error_and_prunes_request(self):
        for error in (FileNotFoundError("history disappeared"), PermissionError("history unreadable")):
            with self.subTest(error=type(error).__name__):
                arrived = threading.Event()
                results = []
                def write(value):
                    results.append(value)
                    arrived.set()
                runtime = rig_mcp.Runtime(rig_mcp._execute_request, write, rig_mcp._abort_request)
                with mock.patch.object(jobs, "resolve_job_paths", side_effect=error):
                    runtime.start("missing-history", "rig_job_wait", {"repo": str(self.repo), "id": "partial"}, {})
                    self.assertTrue(arrived.wait(2))
                self.assertTrue(results[0]["result"].get("isError"))
                self.assertIn(str(error), self._text(results[0]["result"]))
                self.assertEqual(runtime.requests, {})
                self.assertEqual(rig_mcp.handle({"method": "ping", "id": "alive"})["result"], {})

    def test_exact_mcp_wait_loads_targets_once_without_history_enumeration(self):
        from mcp_runtime import Request
        self._session_job("exact-one", "running")
        self._session_job("exact-two", "running")
        request = Request("exact-request", "rig_job_wait", {
            "repo": str(self.repo), "ids": ["exact-one", "exact-two"], "timeout": 0}, {})
        with (mock.patch.object(jobs, "list_jobs", side_effect=AssertionError("unexpected history scan")),
              mock.patch.object(jobs, "load_job", wraps=jobs.load_job) as load):
            result = rig_mcp._execute_request(request)
        self.assertEqual(load.call_count, 2)
        self.assertNotIn("isError", result)

    def test_wait_without_id_scans_once_and_cancel_keeps_selected_job(self):
        from mcp_runtime import Request
        original = self._session_job("original", "running")
        request = Request("default-request", "rig_job_wait", {"repo": str(self.repo), "timeout": 0}, {})
        with mock.patch.object(jobs, "list_jobs", wraps=jobs.list_jobs) as listing:
            rig_mcp._execute_request(request)
        self.assertEqual(listing.call_count, 1)
        replacement = self._session_job("new-job", "running")
        rig_mcp._abort_request(request)
        self.assertTrue(jobs.cancellation.requested(original))
        self.assertFalse(jobs.cancellation.requested(replacement))

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

    def _workflow_spec(self, nodes=None, **extra):
        (self.repo / "a.py").write_text("a\n")
        (self.repo / "b.py").write_text("b\n")
        return {
            "title": "wf",
            "case": "implement a.py",
            "nodes": nodes or [{"id": "w1", "role": "implement", "files": ["a.py"]}],
            **extra,
        }

    def _create_workflow(self, **extra):
        out = rig_mcp.call_tool(
            "rig_workflow_create",
            {"repo": str(self.repo), "spec": self._workflow_spec(**extra), "owner_session": "mcp-wf"},
        )
        self.assertNotIn("isError", out, self._text(out))
        return out

    def test_workflow_parent_tools_create_list_show_report_omit_token_text(self):
        created = self._create_workflow()
        text = self._text(created)
        payload = json.loads(text)
        lease = created["structuredContent"]
        token = lease["owner_token"]
        self.assertNotIn(token, text)
        self.assertNotIn("owner_token", payload)
        self.assertEqual(payload["credentials_path"], lease["credentials_path"])
        self.assertTrue(Path(lease["credentials_path"]).is_file())
        listed = rig_mcp.call_tool("rig_workflows", {"repo": str(self.repo)})
        self.assertNotIn("isError", listed)
        self.assertIn(lease["workflow_id"], self._text(listed))
        self.assertNotIn(token, self._text(listed))
        shown = rig_mcp.call_tool("rig_workflow_show", {"repo": str(self.repo), "id": lease["workflow_id"]})
        self.assertNotIn("isError", shown)
        self.assertNotIn(token, self._text(shown))
        shown_obj = json.loads(self._text(shown))
        self.assertIn("events", shown_obj)
        reported = rig_mcp.call_tool("rig_workflow_report", {"repo": str(self.repo), "id": lease["workflow_id"]})
        self.assertNotIn("isError", reported)
        self.assertIn("wall_time_s", self._text(reported))
        self.assertNotIn(token, self._text(reported))
        waited = rig_mcp.call_tool(
            "rig_workflow_wait", {"repo": str(self.repo), "id": lease["workflow_id"], "timeout": 0},
        )
        self.assertNotIn("isError", waited)
        cancelled = rig_mcp.call_tool(
            "rig_workflow_cancel",
            {"repo": str(self.repo), "id": lease["workflow_id"], "owner_token": token,
             "owner_session": "mcp-wf", "rationale": "done"},
        )
        self.assertNotIn("isError", cancelled, self._text(cancelled))
        self.assertNotIn(token, self._text(cancelled))

    def test_workflow_auth_forwarding_and_schema_errors(self):
        created = self._create_workflow()
        lease = created["structuredContent"]
        wid = lease["workflow_id"]
        denied = rig_mcp.call_tool(
            "rig_workflow_extend",
            {"repo": str(self.repo), "id": wid, "nodes": [{"id": "extra", "role": "review", "files": ["b.py"]}]},
        )
        self.assertTrue(denied.get("isError"))
        self.assertIn("credentials", self._text(denied).lower())
        extended = rig_mcp.call_tool(
            "rig_workflow_extend",
            {"repo": str(self.repo), "id": wid, "owner_token": lease["owner_token"],
             "owner_session": "mcp-wf",
             "nodes": [{"id": "extra", "role": "review", "files": ["b.py"], "depends_on": ["w1"]}]},
        )
        self.assertNotIn("isError", extended, self._text(extended))
        self.assertIn("extra", self._text(extended))
        missing = rig_mcp.call_tool("rig_workflow_create", {"repo": str(self.repo), "spec": []})
        self.assertTrue(missing.get("isError"))
        self.assertIn("object", self._text(missing))
        no_id = rig_mcp.call_tool("rig_workflow_show", {"repo": str(self.repo)})
        self.assertTrue(no_id.get("isError"))
        parent_child = rig_mcp.call_tool(
            "rig_job_coordination_request",
            {"kind": "scope", "text": "nope", "repo": str(self.repo)},
        )
        self.assertTrue(parent_child.get("isError"))
        self.assertIn("not a parent tool", self._text(parent_child))

    def test_workflow_approve_resolve_and_advance_forward_owner(self):
        created = self._create_workflow(nodes=[
            {"id": "w1", "role": "implement", "files": ["a.py"], "effects": "production"},
        ])
        lease = created["structuredContent"]
        wid, token = lease["workflow_id"], lease["owner_token"]
        approved = rig_mcp.call_tool(
            "rig_workflow_approve",
            {"repo": str(self.repo), "id": wid, "node_id": "w1", "rationale": "allow prod",
             "owner_token": token, "owner_session": "mcp-wf"},
        )
        self.assertNotIn("isError", approved, self._text(approved))
        launches = []

        def pick(repo, node, spec, state, exclude=""):
            return {"worker": "grok", "spawn": "run-worker", "parent_writes": False,
                    "model": "grok-4.6", "effort": "high", "routing": {}}

        def launch(repo, *, node, spec, state, choice, **kwargs):
            job_id = f"job-{node['id']}"
            folder = Path(repo) / ".rig" / "jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "meta.json").write_text(json.dumps({
                "job_id": job_id, "status": "running", "role": node["role"], "files": node.get("files") or [],
                "worker": "grok", "reservation_id": "res", "attempt_id": "att",
            }))
            launches.append(node["id"])
            return {"kind": "wrapper", "job": {"job_id": job_id, "reservation_id": "res", "attempt_id": "att"},
                    "workflow_attempt": 1}

        with mock.patch("workflow_scheduler._default_launch", launch), mock.patch("workflow_scheduler._pick_node", pick):
            advanced = rig_mcp.call_tool(
                "rig_workflow_advance",
                {"repo": str(self.repo), "id": wid, "owner_token": token, "owner_session": "mcp-wf"},
            )
        self.assertNotIn("isError", advanced, self._text(advanced))
        self.assertEqual(launches, ["w1"])
        import workflow_state as wf
        spec, state = wf.load_pair(self.repo, wid, required=True)
        state["nodes"]["w1"].update(status="failed", ran=True, launched=True, job_id="")
        state["failure"] = {"node_id": "w1", "reason": "boom"}
        wf.save_state(self.repo, state)
        resolved = rig_mcp.call_tool(
            "rig_workflow_resolve",
            {"repo": str(self.repo), "id": wid, "node_id": "w1", "action": "retry",
             "owner_token": token, "owner_session": "mcp-wf"},
        )
        self.assertNotIn("isError", resolved, self._text(resolved))
        self.assertIn("pending", self._text(resolved))

    def test_child_coordination_after_handshake_hides_parent_workflow_tools(self):
        created = self._create_workflow()
        lease = created["structuredContent"]
        job_id = "child-coord"
        job_dir = self.repo / ".rig" / "jobs" / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "meta.json").write_text(json.dumps({
            "job_id": job_id, "worker": "grok", "role": "implement", "status": "running",
            "workflow_id": lease["workflow_id"], "workflow_node_id": "w1",
        }))
        os.environ["RIG_JOB_ID"] = job_id
        os.environ["RIG_JOB_DIR"] = str(job_dir)
        os.environ["RIG_REPO"] = str(self.repo)
        try:
            blocked = rig_mcp.call_tool(
                "rig_job_coordination_request",
                {"kind": "scope", "text": "need a decision"},
            )
            self.assertTrue(blocked.get("isError"))
            self.assertIn("rig_job_inbox", self._text(blocked))
            hidden = rig_mcp.call_tool(
                "rig_workflow_create",
                {"spec": self._workflow_spec(), "repo": str(self.repo)},
            )
            self.assertTrue(hidden.get("isError"))
            self.assertIn("not a child tool", self._text(hidden))
            inbox0 = rig_mcp.call_tool("rig_job_inbox", {})
            self.assertNotIn("isError", inbox0)
            expanded = rig_mcp.call_tool(
                "rig_job_coordination_request",
                {"kind": "scope", "text": "add files", "payload": {"files": ["b.py"]}},
            )
            self.assertTrue(expanded.get("isError"))
            self.assertIn("never expands", self._text(expanded))
            asked = rig_mcp.call_tool(
                "rig_job_coordination_request",
                {"kind": "dependency", "text": "need review first", "payload": {"note": "order"}},
            )
            self.assertNotIn("isError", asked, self._text(asked))
            request_id = asked["structuredContent"]["request"]["id"]
            self.assertNotIn(lease["owner_token"], self._text(asked))
        finally:
            os.environ.pop("RIG_JOB_ID", None)
            os.environ.pop("RIG_JOB_DIR", None)
            os.environ.pop("RIG_REPO", None)
        replied = rig_mcp.call_tool(
            "rig_job_coordination_reply",
            {"repo": str(self.repo), "id": lease["workflow_id"], "request_id": request_id,
             "decision": "reply", "text": "proceed", "owner_token": lease["owner_token"],
             "owner_session": "mcp-wf"},
        )
        self.assertNotIn("isError", replied, self._text(replied))
        waited = rig_mcp.call_tool(
            "rig_workflow_wait", {"repo": str(self.repo), "id": lease["workflow_id"], "timeout": 0},
        )
        self.assertNotIn("isError", waited)
        os.environ["RIG_JOB_ID"] = job_id
        os.environ["RIG_JOB_DIR"] = str(job_dir)
        os.environ["RIG_REPO"] = str(self.repo)
        try:
            inbox0 = rig_mcp.call_tool("rig_job_inbox", {})
            self.assertNotIn("isError", inbox0)
            child_reply = rig_mcp.call_tool(
                "rig_job_coordination_reply",
                {"id": lease["workflow_id"], "request_id": request_id, "text": "nope"},
            )
            self.assertTrue(child_reply.get("isError"))
            self.assertIn("not a child tool", self._text(child_reply))
        finally:
            os.environ.pop("RIG_JOB_ID", None)
            os.environ.pop("RIG_JOB_DIR", None)
            os.environ.pop("RIG_REPO", None)

    def test_parent_cu_tools_hidden_when_not_effective(self):
        os.environ["RIG_REPO"] = str(self.repo)
        names = [t["name"] for t in rig_mcp.listed_tools()]
        self.assertNotIn("rig_cu_capture", names)
        self.assertNotIn("rig_cu_act", names)
        self.assertNotIn("rig_cu_confirm", names)
        self.assertNotIn("rig_cu_record", names)

    def test_parent_cu_tools_listed_when_effective(self):
        os.environ["RIG_REPO"] = str(self.repo)
        with mock.patch("computer_use.tools_listed", return_value=True):
            names = [t["name"] for t in rig_mcp.listed_tools()]
        self.assertIn("rig_cu_capture", names)
        self.assertIn("rig_cu_act", names)
        self.assertIn("rig_cu_confirm", names)
        self.assertIn("rig_cu_record", names)

    def test_listed_tools_survives_cu_helper_crash(self):
        os.environ["RIG_REPO"] = str(self.repo)
        with mock.patch("computer_use.tools_listed", side_effect=AttributeError("harness")):
            names = [t["name"] for t in rig_mcp.listed_tools()]
        self.assertIn("rig_session", names)
        self.assertNotIn("rig_cu_capture", names)
        self.assertNotIn("rig_cu_act", names)
        self.assertNotIn("rig_cu_confirm", names)
        self.assertNotIn("rig_cu_record", names)

    def test_child_cu_call_refused_for_grok_codex_devin(self):
        for worker, job_id in (("grok", "child-grok-cu"), ("codex", "child-codex-cu"), ("devin", "child-devin-cu")):
            job_dir = self.repo / ".rig" / "jobs" / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "meta.json").write_text(json.dumps({
                "job_id": job_id, "worker": worker, "role": "implement", "status": "running",
            }))
            os.environ["RIG_JOB_ID"] = job_id
            os.environ["RIG_JOB_DIR"] = str(job_dir)
            os.environ["RIG_REPO"] = str(self.repo)
            try:
                names = [t["name"] for t in rig_mcp.listed_tools()]
                self.assertNotIn("rig_cu_capture", names, worker)
                self.assertNotIn("rig_cu_record", names, worker)
                denied = rig_mcp.call_tool("rig_cu_capture", {"repo": str(self.repo)})
                self.assertTrue(denied.get("isError"), worker)
                self.assertIn("not a child tool", self._text(denied))
            finally:
                os.environ.pop("RIG_JOB_ID", None)
                os.environ.pop("RIG_JOB_DIR", None)

    def test_parent_cu_capture_returns_evidence(self):
        os.environ["RIG_REPO"] = str(self.repo)
        ev = {
            "ok": True, "effective": True, "action": "capture", "snapshot_id": "drv-1",
            "pid": 1, "window_id": 2, "addressed": {"element_token": "", "index": None, "label": ""},
            "effect": "captured", "before_png": "", "after_png": "/tmp/a.png",
            "elements": [{"index": 1, "role": "AXButton", "label": "1", "element_token": "tok-1"}],
            "brief_block": "Child must not click.", "hint": "act only with a token",
        }
        with mock.patch("computer_use.cu_capture", return_value=ev) as capture:
            out = rig_mcp.call_tool(
                "rig_cu_capture",
                {"repo": str(self.repo), "pid": 1, "window_id": 2, "bundle_id": "com.apple.calculator"},
            )
        self.assertNotIn("isError", out)
        self.assertEqual(out["structuredContent"]["snapshot_id"], "drv-1")
        self.assertIn("Child must not click", self._text(out))
        capture.assert_called_once()
        kwargs = capture.call_args.kwargs
        self.assertEqual(kwargs.get("bundle_id"), "com.apple.calculator")
        self.assertEqual(kwargs.get("pid"), 1)
        self.assertEqual(kwargs.get("profile_key"), "")
        self.assertEqual(kwargs.get("url"), "")

    def test_parent_cu_act_passes_xy(self):
        os.environ["RIG_REPO"] = str(self.repo)
        ev = {
            "ok": True, "effective": True, "action": "click", "snapshot_id": "drv-1",
            "pid": 1, "window_id": 2,
            "addressed": {"kind": "px", "element_token": "", "index": None, "label": "", "ref": "", "x": 10, "y": 20},
            "effect": "unverifiable", "before_png": "", "after_png": "", "elements": [],
            "brief_block": "Child must not click.", "hint": "",
        }
        with mock.patch("computer_use.cu_act", return_value=ev) as act:
            out = rig_mcp.call_tool(
                "rig_cu_act",
                {"repo": str(self.repo), "snapshot_id": "drv-1", "action": "click", "x": 10, "y": 20},
            )
        self.assertNotIn("isError", out)
        kwargs = act.call_args.kwargs
        self.assertEqual(kwargs.get("x"), 10.0)
        self.assertEqual(kwargs.get("y"), 20.0)
        self.assertEqual(kwargs.get("element_token"), "")

    def test_parent_cu_record_passes_action(self):
        os.environ["RIG_REPO"] = str(self.repo)
        ev = {
            "ok": True, "effective": True, "action": "record_start",
            "effect": "recorded", "recording_path": "/repo/.rig/cu-evidence/run/recording.mp4",
            "output_dir": "/repo/.rig/cu-evidence/run", "brief_block": "Child must not click.",
            "hint": "stop with rig_cu_record action=stop",
        }
        with mock.patch("computer_use.cu_record", return_value=ev) as record:
            out = rig_mcp.call_tool(
                "rig_cu_record",
                {"repo": str(self.repo), "action": "start", "output_dir": ".rig/cu-evidence/run"},
            )
        self.assertNotIn("isError", out)
        self.assertEqual(out["structuredContent"]["action"], "record_start")
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs.get("action"), "start")
        self.assertEqual(kwargs.get("output_dir"), ".rig/cu-evidence/run")
        self.assertIsNone(kwargs.get("record_video"))

    def _wrapper_job(self, job_id="wrap-stopped", files=None, stopped=False):
        files = ["a.py"] if files is None else list(files)
        for name in files:
            target = self.repo / name
            if not target.exists():
                target.write_text(name + "\n")
        owner = admission.caller_owner("parent", owner_session="mcp-wrap")
        lease = admission.reserve(
            self.repo, job_id=job_id, worker="grok", role="implement", files=files,
            access="write", owner=owner, owner_session="mcp-wrap",
        )
        job = self.repo / ".rig" / "jobs" / job_id
        job.mkdir(parents=True, exist_ok=True)
        (job / "meta.json").write_text(json.dumps({
            "job_id": job_id, "worker": "grok", "role": "implement", "files": files,
            "status": "ok" if stopped else "running", "executor_kind": "wrapper", "kind": "wrapper",
            "reservation_id": lease["reservation_id"], "attempt_id": lease["attempt_id"],
            "ownership_established": True,
        }))
        path = admission.write_credentials(self.repo, {**lease, **admission.credentials(lease)})
        if stopped:
            admission.finish(
                self.repo, status="ok", completion={"kind": "parent_task", "completed": True},
                **admission.credentials(lease), owner_session="mcp-wrap",
            )
        rec_path = self.repo / ".rig" / "reservations" / f"{lease['reservation_id']}.json"
        return lease, path, rec_path

    def test_close_accepts_credentials_path_without_exposing_token(self):
        start = rig_mcp.call_tool(
            "rig_job_start",
            {"repo": str(self.repo), "worker": "grok", "role": "parent", "id": "path-close",
             "owner_session": "mcp-path"},
        )
        self.assertNotIn("isError", start, start)
        lease = start["structuredContent"]
        token = lease["owner_token"]
        path = lease["credentials_path"]
        finished = rig_mcp.call_tool(
            "rig_job_finish",
            {"repo": str(self.repo), "id": "path-close", **self._completed(start)},
        )
        self.assertFalse(finished.get("isError"), finished)
        closed = rig_mcp.call_tool(
            "rig_job_close",
            {"repo": str(self.repo), "id": "path-close", "rationale": "Close via credentials_path",
             "credentials_path": path, "owner_session": lease["owner"].get("session_id", "")},
        )
        self.assertFalse(closed.get("isError"), closed)
        self.assertNotIn(token, self._text(closed))
        self.assertNotIn("owner_token", json.loads(self._text(closed)))

    def test_credentials_path_denies_insecure_and_mismatched_artifacts(self):
        start = rig_mcp.call_tool(
            "rig_job_start",
            {"repo": str(self.repo), "worker": "grok", "role": "parent", "id": "path-deny",
             "owner_session": "mcp-path"},
        )
        lease = start["structuredContent"]
        token = lease["owner_token"]
        path = Path(lease["credentials_path"])
        os.chmod(path, 0o644)
        denied = rig_mcp.call_tool(
            "rig_job_close",
            {"repo": str(self.repo), "id": "path-deny", "rationale": "insecure",
             "credentials_path": str(path), "owner_session": lease["owner"].get("session_id", "")},
        )
        self.assertTrue(denied.get("isError"))
        self.assertIn("0600", self._text(denied))
        self.assertNotIn(token, self._text(denied))
        os.chmod(path, 0o600)
        mismatched = rig_mcp.call_tool(
            "rig_job_close",
            {"repo": str(self.repo), "id": "path-deny", "rationale": "mismatch",
             "credentials_path": str(path), "reservation_id": "other-reservation",
             "owner_session": lease["owner"].get("session_id", "")},
        )
        self.assertTrue(mismatched.get("isError"))
        self.assertNotIn(token, self._text(mismatched))

    def test_wrapper_receipt_recovery_is_read_only_and_redacted(self):
        lease, path, rec_path = self._wrapper_job("wrap-ok", stopped=True)
        token = lease["owner_token"]
        before = rec_path.read_bytes()
        out = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-ok"},
        )
        self.assertNotIn("isError", out, out)
        payload = json.loads(self._text(out))
        self.assertEqual(payload["credentials_path"], str(path))
        self.assertEqual(payload["reservation_id"], lease["reservation_id"])
        self.assertEqual(payload["attempt_id"], lease["attempt_id"])
        self.assertEqual(payload["executor_kind"], "wrapper")
        self.assertNotIn("owner_token", payload)
        self.assertNotIn("owner_token", out.get("structuredContent") or {})
        self.assertNotIn(token, self._text(out))
        self.assertEqual(rec_path.read_bytes(), before)
        closed = rig_mcp.call_tool(
            "rig_job_close",
            {"repo": str(self.repo), "id": "wrap-ok", "rationale": "handoff close",
             "credentials_path": payload["credentials_path"], "owner_session": "mcp-wrap"},
        )
        self.assertFalse(closed.get("isError"), closed)
        self.assertNotIn(token, self._text(closed))
        released = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-ok"},
        )
        self.assertTrue(released.get("isError"))
        self.assertIn("released", self._text(released))
        self.assertNotIn(token, self._text(released))

    def test_wrapper_receipt_recovery_rejects_active_parent_and_bad_artifacts(self):
        start = rig_mcp.call_tool(
            "rig_job_start",
            {"repo": str(self.repo), "worker": "grok", "role": "parent", "id": "not-wrap",
             "owner_session": "mcp-path"},
        )
        parent_denied = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "not-wrap"},
        )
        self.assertTrue(parent_denied.get("isError"))
        self.assertIn("non-wrapper", self._text(parent_denied))
        self.assertNotIn(start["structuredContent"]["owner_token"], self._text(parent_denied))
        finished = rig_mcp.call_tool(
            "rig_job_finish",
            {"repo": str(self.repo), "id": "not-wrap", **self._completed(start)},
        )
        self.assertFalse(finished.get("isError"), finished)
        closed = rig_mcp.call_tool(
            "rig_job_close",
            {"repo": str(self.repo), "id": "not-wrap",
             "rationale": "Release not-wrap after non-wrapper assertion", **self._ownership(start)},
        )
        self.assertFalse(closed.get("isError"), closed)
        lease, path, _rec_path = self._wrapper_job("wrap-active")
        active = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-active"},
        )
        self.assertTrue(active.get("isError"))
        self.assertIn("active work", self._text(active))
        self.assertNotIn(lease["owner_token"], self._text(active))
        lease2, path2, rec_path2 = self._wrapper_job("wrap-bad", files=["b.py"], stopped=True)
        path2.write_text("{")
        os.chmod(path2, 0o600)
        before = rec_path2.read_bytes()
        malformed = rig_mcp.call_tool(
            "rig_job_recover_wrapper_receipt",
            {"repo": str(self.repo), "id": "wrap-bad"},
        )
        self.assertTrue(malformed.get("isError"))
        self.assertIn("malformed", self._text(malformed))
        self.assertNotIn(lease2["owner_token"], self._text(malformed))
        self.assertEqual(rec_path2.read_bytes(), before)
        alias = path.parent / "alias-credentials.json"
        alias.symlink_to(path)
        linked = rig_mcp.call_tool(
            "rig_job_close",
            {"repo": str(self.repo), "id": "wrap-active", "rationale": "symlink",
             "credentials_path": str(alias), "owner_session": "mcp-wrap"},
        )
        self.assertTrue(linked.get("isError"))
        self.assertNotIn(lease["owner_token"], self._text(linked))

    def _failed_wrapper(self, job_id="wrap-fail", status="fail"):
        files = ["fail.py"]
        (self.repo / "fail.py").write_text("x\n")
        owner = admission.caller_owner("wrapper", owner_session="mcp-wrap")
        lease = admission.reserve(
            self.repo, job_id=job_id, worker="grok", role="implement", files=files,
            access="write", owner=owner, owner_session="mcp-wrap",
        )
        job = self.repo / ".rig" / "jobs" / job_id
        job.mkdir(parents=True, exist_ok=True)
        (job / "meta.json").write_text(json.dumps({
            "job_id": job_id, "worker": "grok", "role": "implement", "files": files,
            "status": status, "executor_kind": "wrapper", "kind": "wrapper",
            "reservation_id": lease["reservation_id"], "attempt_id": lease["attempt_id"],
            "ownership_established": True,
        }))
        path = admission.write_credentials(self.repo, {**lease, **admission.credentials(lease)})
        rec_path = self.repo / ".rig" / "reservations" / f"{lease['reservation_id']}.json"
        record = json.loads(rec_path.read_text())
        record.update(stopped=True, execution_status=status, stage="verifying", slot_held=False)
        rec_path.write_text(json.dumps(record, indent=2) + "\n")
        return lease, path

    def test_break_glass_close_mcp_success_idempotent_and_redacted(self):
        lease, path = self._failed_wrapper()
        token = lease["owner_token"]
        first = rig_mcp.call_tool("rig_job_break_glass_close", {
            "repo": str(self.repo), "id": "wrap-fail",
            "credentials_path": str(path), "confirmed_stopped": True,
            "rationale": "MCP audited close",
        })
        self.assertFalse(first.get("isError"), first)
        payload = json.loads(self._text(first))
        self.assertEqual(payload["applied"], 1)
        self.assertEqual(payload["recovery"]["outcome"], "released")
        self.assertNotIn(token, self._text(first))
        self.assertNotIn("owner_token", self._text(first))
        self.assertNotIn(token, json.dumps(first.get("structuredContent") or {}))
        again = rig_mcp.call_tool("rig_job_break_glass_close", {
            "repo": str(self.repo), "id": "wrap-fail",
            "credentials_path": str(path), "confirmed_stopped": True,
            "rationale": "repeat",
        })
        self.assertFalse(again.get("isError"), again)
        self.assertEqual(json.loads(self._text(again))["applied"], 0)
        self.assertNotIn(token, self._text(again))

    def test_break_glass_close_mcp_rejects_raw_token_and_ok_status(self):
        lease, path = self._failed_wrapper("wrap-token")
        denied = rig_mcp.call_tool("rig_job_break_glass_close", {
            "repo": str(self.repo), "id": "wrap-token",
            "credentials_path": str(path), "confirmed_stopped": True,
            "rationale": "nope", "owner_token": lease["owner_token"],
        })
        self.assertTrue(denied.get("isError"))
        self.assertIn("raw owner tokens", self._text(denied))
        self.assertNotIn(lease["owner_token"], self._text(denied))
        ok_lease, ok_path, _rec_path = self._wrapper_job("wrap-ok-status", stopped=True)
        rejected = rig_mcp.call_tool("rig_job_break_glass_close", {
            "repo": str(self.repo), "id": "wrap-ok-status",
            "credentials_path": str(ok_path), "confirmed_stopped": True,
            "rationale": "ok jobs use ordinary close",
        })
        self.assertTrue(rejected.get("isError"))
        self.assertIn("ok or unknown", self._text(rejected))
        self.assertNotIn(ok_lease["owner_token"], self._text(rejected))


if __name__ == "__main__":
    unittest.main()
