#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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
            self.assertNotIn("rig_job_cancel", names)
            self.assertNotIn("rig_job_message", names)
            self.assertNotIn("rig_queue_add", names)
            self.assertNotIn("rig_queue_claim", names)
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
                            self.assertEqual(set(payload), {"memory", "jobs", "status", "pick"})
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
        message = {
            "jsonrpc": "2.0", "id": "pinned-request", "method": "tools/call",
            "params": {"name": "rig_job_wait", "arguments": {
                "repo": str(self.repo), "id": "original", "timeout": 0,
            }},
        }
        with mock.patch.object(rig_mcp.threading, "Thread") as thread:
            self.assertIsNone(rig_mcp.handle(message))
            run_wait = thread.call_args.kwargs["target"]
        target.rename(self.repo / "retired-target")
        replacement = self._session_job("new-original-target", "running")
        try:
            rig_mcp._abort_wait("pinned-request")
            self.assertFalse((replacement / "cancel.json").exists())
            with mock.patch.object(rig_mcp, "write_message") as write:
                run_wait()
            result = write.call_args.args[0]["result"]
            self.assertTrue(result.get("isError"))
            self.assertIn("original-target", self._text(result))
        finally:
            rig_mcp._inflight_waits.pop("pinned-request", None)
            rig_mcp._wait_threads.remove(thread.return_value)

    def test_child_wait_rejected_before_history_lookup_or_registration(self):
        message = {
            "jsonrpc": "2.0", "id": "child-wait", "method": "tools/call",
            "params": {"name": "rig_job_wait", "arguments": {"repo": str(self.repo)}},
        }
        with (
            mock.patch.dict(os.environ, {"RIG_JOB_ID": "child"}),
            mock.patch.object(jobs, "resolve_job_paths", side_effect=AssertionError("child history access")),
            mock.patch.object(rig_mcp.threading, "Thread", side_effect=AssertionError("child wait thread")),
        ):
            reply = rig_mcp.handle(message)
        self.assertTrue(reply["result"].get("isError"))
        self.assertIn("not a child tool", self._text(reply["result"]))
        self.assertNotIn("child-wait", rig_mcp._inflight_waits)

    def test_wait_lookup_io_failure_is_an_error_without_registering_a_wait(self):
        message = {
            "jsonrpc": "2.0", "id": "missing-history", "method": "tools/call",
            "params": {"name": "rig_job_wait", "arguments": {
                "repo": str(self.repo), "id": "partial",
            }},
        }
        for error in (FileNotFoundError("history disappeared"), PermissionError("history unreadable")):
            with self.subTest(error=type(error).__name__):
                with (
                    mock.patch.object(jobs, "resolve_job_paths", side_effect=error),
                    mock.patch.object(rig_mcp.threading, "Thread", side_effect=AssertionError("unexpected wait thread")),
                ):
                    reply = rig_mcp.handle(message)
                self.assertTrue(reply["result"].get("isError"))
                self.assertIn(str(error), self._text(reply["result"]))
                self.assertNotIn("missing-history", rig_mcp._inflight_waits)
                self.assertEqual(rig_mcp.handle({"method": "ping", "id": "alive"})["result"], {})

    def test_exact_mcp_wait_loads_targets_once_without_history_enumeration(self):
        self._session_job("exact-one", "running")
        self._session_job("exact-two", "running")
        message = {
            "jsonrpc": "2.0", "id": "exact-request", "method": "tools/call",
            "params": {"name": "rig_job_wait", "arguments": {
                "repo": str(self.repo), "ids": ["exact-one", "exact-two"], "timeout": 0,
            }},
        }
        with (
            mock.patch.object(rig_mcp.threading, "Thread") as thread,
            mock.patch.object(jobs, "list_jobs", side_effect=AssertionError("unexpected history scan")),
            mock.patch.object(jobs, "load_job", wraps=jobs.load_job) as load,
            mock.patch.object(rig_mcp, "write_message") as write,
        ):
            self.assertIsNone(rig_mcp.handle(message))
            thread.call_args.kwargs["target"]()
            self.assertEqual(load.call_count, 2)
            self.assertNotIn("isError", write.call_args.args[0]["result"])
        rig_mcp._wait_threads.remove(thread.return_value)

    def test_wait_without_id_scans_once_and_cancel_keeps_selected_job(self):
        target = self._session_job("selected-running", "running")
        message = {
            "jsonrpc": "2.0", "id": "default-request", "method": "tools/call",
            "params": {"name": "rig_job_wait", "arguments": {
                "repo": str(self.repo), "timeout": 0,
            }},
        }
        with (
            mock.patch.object(rig_mcp.threading, "Thread") as thread,
            mock.patch.object(jobs, "list_jobs", wraps=jobs.list_jobs) as listing,
        ):
            self.assertIsNone(rig_mcp.handle(message))
            self.assertEqual(listing.call_count, 1)
            run_wait = thread.call_args.kwargs["target"]
        newer = self._session_job("new-asking", "ask")
        try:
            rig_mcp._abort_wait("default-request")
            self.assertTrue((target / "cancel.json").is_file())
            self.assertFalse((newer / "cancel.json").exists())
            with (
                mock.patch.object(jobs, "list_jobs", side_effect=AssertionError("unexpected rescan")),
                mock.patch.object(rig_mcp, "write_message"),
            ):
                run_wait()
        finally:
            rig_mcp._inflight_waits.pop("default-request", None)
            rig_mcp._wait_threads.remove(thread.return_value)

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
