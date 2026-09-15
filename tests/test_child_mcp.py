#!/usr/bin/env python3
"""Parsed child MCP readiness and a real JSON-RPC inbox handshake."""
from __future__ import annotations

import json
import os
import queue
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
sys.path.insert(0, str(ROOT / "tests"))

import child_mcp  # noqa: E402
import inbox as rig_inbox  # noqa: E402
import jobs  # noqa: E402
import mcp_test_support  # noqa: E402
import rig_mcp  # noqa: E402


def _text(msg: dict) -> str:
    result = msg.get("result") or msg
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        return str(content[0].get("text") or "")
    return ""


class ChildMcpReadiness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.bins = self.home / "bins"
        for name in ("grok", "codex", "claude", "opencode", "omp", "pi", "agy", "devin"):
            mcp_test_support.fake_bin(self.bins, name)
        self.launcher = mcp_test_support.seed_installed_mcp(self.home)
        self._env = mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "PATH": mcp_test_support.stub_path(self.bins),
        }, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        for key in ("OPENCODE_CONFIG", "OMP_MCP", "PI_CODING_AGENT_DIR", "PI_AGENT_DIR", "AGY_MCP"):
            os.environ.pop(key, None)

    def test_setup_configs_are_ready_when_binary_exists(self):
        for worker in ("grok", "codex", "claude", "opencode", "omp", "pi", "agy", "devin"):
            ready, reason = child_mcp.worker_mcp_ready(worker, home=self.home)
            self.assertTrue(ready, f"{worker}: {reason}")
        ready, reason = child_mcp.worker_mcp_ready("cursor", home=self.home)
        self.assertFalse(ready)
        self.assertIn("isolated job-scoped MCP", reason)

    def test_rejects_comment_only_disabled_malformed_and_invalid(self):
        grok = self.home / ".grok" / "config.toml"
        grok.write_text("# [mcp_servers.rig]\n# command = \"/bin/sh\"\nenabled = true\n")
        self.assertFalse(child_mcp.worker_mcp_ready("grok", home=self.home)[0])
        grok.write_text(
            f'[mcp_servers.rig]\ncommand = "{self.launcher}"\nargs = []\nenabled = false\n'
        )
        self.assertFalse(child_mcp.worker_mcp_ready("grok", home=self.home)[0])
        grok.write_text(
            f'[mcp_servers.rig]\ncommand = "{self.launcher}"\nargs = []\ndisabled = true\n'
        )
        self.assertFalse(child_mcp.worker_mcp_ready("grok", home=self.home)[0])
        grok.write_text("[mcp_servers.rig\ncommand = broken")
        self.assertFalse(child_mcp.worker_mcp_ready("grok", home=self.home)[0])
        grok.write_text(
            '[mcp_servers.rig]\ncommand = "/no/such/rig-mcp"\nargs = []\nenabled = true\n'
        )
        self.assertFalse(child_mcp.worker_mcp_ready("grok", home=self.home)[0])
        oc = self.home / ".config" / "opencode" / "opencode.json"
        oc.write_text("{")
        self.assertFalse(child_mcp.worker_mcp_ready("opencode", home=self.home)[0])
        oc.write_text(json.dumps({
            "mcp": {"rig": {"type": "local", "command": [str(self.launcher)], "enabled": False}},
        }))
        self.assertFalse(child_mcp.worker_mcp_ready("opencode", home=self.home)[0])
        oc.write_text(json.dumps({
            "mcp": {"servers": {"rig": {"type": "remote", "url": "http://127.0.0.1", "enabled": True}}},
        }))
        self.assertFalse(child_mcp.worker_mcp_ready("opencode", home=self.home)[0])
        omp = self.home / ".omp" / "mcp.json"
        omp.write_text(json.dumps({"mcpServers": {"rig": {"command": str(self.launcher), "enabled": False}}}))
        self.assertFalse(child_mcp.worker_mcp_ready("omp", home=self.home)[0])
        omp.write_text(json.dumps({
            "mcpServers": {"rig": {"command": str(self.launcher), "disabled": True}},
        }))
        self.assertFalse(child_mcp.worker_mcp_ready("omp", home=self.home)[0])
        omp.write_text(json.dumps({
            "mcpServers": {"rig": {"command": str(self.launcher), "enabled": ["true"]}},
        }))
        self.assertFalse(child_mcp.worker_mcp_ready("omp", home=self.home)[0])
        omp.write_text(json.dumps({
            "mcpServers": {"rig": {"command": str(self.launcher), "disabled": {"ok": True}}},
        }))
        self.assertFalse(child_mcp.worker_mcp_ready("omp", home=self.home)[0])

    def test_simple_toml_root_keys_before_section_keep_rig_ready(self):
        grok = self.home / ".grok" / "config.toml"
        grok.write_text(
            f'model = "grok-4.6"\n'
            f'[mcp_servers.rig]\ncommand = "{self.launcher}"\nargs = []\nenabled = true\n'
        )
        with mock.patch.object(child_mcp, "_toml_loads", return_value=None):
            ready, reason = child_mcp.worker_mcp_ready("grok", home=self.home)
        self.assertTrue(ready, reason)
        with mock.patch.object(child_mcp, "_toml_loads", return_value=None):
            parsed = child_mcp._simple_toml(grok.read_text())
        self.assertEqual(parsed.get("model"), "grok-4.6")
        self.assertEqual(parsed["mcp_servers"]["rig"]["command"], str(self.launcher))
        with mock.patch.object(child_mcp, "_toml_loads", return_value=None):
            grok.write_text(
                f'model = "grok-4.6"\n'
                f'[mcp_servers.rig]\ncommand = "{self.launcher}"\nargs = []\ndisabled = true\n'
            )
            self.assertFalse(child_mcp.worker_mcp_ready("grok", home=self.home)[0])
            grok.write_text("model = \"x\"\n[mcp_servers.rig\ncommand = broken")
            self.assertFalse(child_mcp.worker_mcp_ready("grok", home=self.home)[0])

    def test_override_env_paths_and_opencode_servers_shape(self):
        custom = self.home / "custom-opencode.json"
        custom.write_text(json.dumps({
            "mcp": {"servers": {"rig": {"type": "local", "command": [str(self.launcher)], "enabled": True}}},
        }))
        os.environ["OPENCODE_CONFIG"] = str(custom)
        self.assertTrue(child_mcp.worker_mcp_ready("opencode", home=self.home)[0])
        other = self.home / "other-omp.json"
        other.write_text(json.dumps({"mcpServers": {"rig": {"command": str(self.launcher)}}}))
        os.environ["OMP_MCP"] = str(other)
        self.assertTrue(child_mcp.worker_mcp_ready("omp", home=self.home)[0])

    def test_display_status_and_success_gate_text(self):
        self.assertEqual(child_mcp.display_status({}), child_mcp.LEGACY)
        self.assertEqual(child_mcp.display_status({}, running=True), child_mcp.UNKNOWN)
        self.assertEqual(child_mcp.display_status({"child_mcp_protocol": 1}), child_mcp.UNKNOWN)
        self.assertEqual(child_mcp.display_status({"child_mcp_connected_at": "x"}), child_mcp.UNKNOWN)
        self.assertEqual(
            child_mcp.display_status({"child_mcp_status": child_mcp.CONNECTED}),
            child_mcp.CONNECTED,
        )
        job_dir = self.home / "jobs" / "done"
        job_dir.mkdir(parents=True)
        (job_dir / "meta.json").write_text(json.dumps({
            "job_id": "done", "worker": "grok", "status": "ok",
        }))
        self.assertEqual(child_mcp.require_success(job_dir), child_mcp.MISSING_REASON)
        self.assertEqual(child_mcp.require_success(job_dir), "child MCP handshake missing")


class ChildMcpJsonRpc(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig" / "jobs").mkdir(parents=True)
        (self.repo / ".rig" / "harness.toml").write_text(
            'parent = "codex"\n[workers]\ngrok = true\n'
        )
        self.job_id = "child-job"
        self.job_dir = self.repo / ".rig" / "jobs" / self.job_id
        now = jobs.iso_now()
        jobs.write_job_files(
            self.job_dir, self.job_id, "grok", "implement", "running", 0, now, "", "",
            kind="wrapper", executor_kind="wrapper", execution_mode="live",
            model="grok-4.6", effort="high", files=["a.py"], capture_evidence=False,
        )
        child_mcp.mark_unknown(self.job_dir)
        rig_inbox.write_inbox(self.job_dir, "parent says do a.py only")
        env = os.environ.copy()
        env.update({
            "RIG_JOB_ID": self.job_id,
            "RIG_JOB_DIR": str(self.job_dir),
            "RIG_REPO": str(self.repo),
            "RIG_SKIP_MODEL_CATALOG": "1",
        })
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "scripts" / "rig_mcp.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(self.repo),
            env=env,
        )
        self.addCleanup(self.close)
        self.messages = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        }})
        self.response(1)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream:
                stream.close()

    def _read(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self.messages.put(json.loads(line))

    def send(self, message):
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.proc.stdin.flush()

    def response(self, rid, timeout=4):
        deadline = time.monotonic() + timeout
        while True:
            value = self.messages.get(timeout=max(0.001, deadline - time.monotonic()))
            if type(value.get("id")) is type(rid) and value.get("id") == rid:
                return value
            if time.monotonic() >= deadline:
                self.fail(f"no response for {rid!r}")

    def call(self, rid, name, arguments=None):
        self.send({"id": rid, "method": "tools/call", "params": {
            "name": name, "arguments": arguments or {},
        }})
        return self.response(rid)

    def test_jsonrpc_handshake_inbox_then_notes(self):
        listed = self.send_list()
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(set(names), set(rig_mcp.CHILD_TOOL_ORDER))
        self.assertNotIn("rig_job_launch", names)
        self.assertNotIn("rig_pick", names)
        note = self.call(3, "rig_job_note", {"text": "too early"})
        self.assertTrue((note.get("result") or {}).get("isError"))
        self.assertEqual(_text(note), child_mcp.INBOX_FIRST)
        show = self.call(4, "rig_job_show", {"id": self.job_id, "repo": str(self.repo)})
        self.assertTrue((show.get("result") or {}).get("isError"))
        self.assertEqual(_text(show), child_mcp.INBOX_FIRST)
        memory = self.call(5, "rig_memory", {"repo": str(self.repo)})
        self.assertTrue((memory.get("result") or {}).get("isError"))
        self.assertEqual(_text(memory), child_mcp.INBOX_FIRST)
        launch = self.call(6, "rig_job_launch", {"brief": "nope"})
        self.assertTrue((launch.get("result") or {}).get("isError"))
        self.assertIn("not a child tool", _text(launch))

        pulled = self.call(7, "rig_job_inbox", {})
        self.assertFalse((pulled.get("result") or {}).get("isError"))
        self.assertEqual(_text(pulled), "parent says do a.py only")
        meta = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(meta.get("child_mcp_status"), child_mcp.CONNECTED)
        self.assertEqual(int(meta.get("child_mcp_protocol")), child_mcp.PROTOCOL)
        self.assertTrue(str(meta.get("child_mcp_connected_at") or "").endswith("Z"))
        self.assertFalse((self.job_dir / "inbox.json").exists())

        again = self.call(8, "rig_job_inbox", {})
        self.assertEqual(_text(again), "(empty)")
        self.assertEqual(json.loads((self.job_dir / "meta.json").read_text())["child_mcp_status"], child_mcp.CONNECTED)

        note_ok = self.call(9, "rig_job_note", {"text": "working a.py"})
        self.assertFalse((note_ok.get("result") or {}).get("isError"))
        doing = self.call(10, "rig_job_doing", {"text": "editing a.py"})
        self.assertFalse((doing.get("result") or {}).get("isError"))
        activity = json.loads((self.job_dir / "activity.json").read_text())
        blob = json.dumps(activity)
        self.assertIn("working a.py", blob)
        self.assertIn("editing a.py", blob)
        meta = json.loads((self.job_dir / "meta.json").read_text())
        self.assertEqual(meta.get("doing"), "editing a.py")

        other = self.call(11, "rig_job_show", {"id": "someone-else", "repo": str(self.repo)})
        self.assertTrue((other.get("result") or {}).get("isError"))
        self.assertIn("own job", _text(other))
        foreign_root = self.repo / "foreign-repo"
        foreign_root.mkdir()
        (foreign_root / ".git").mkdir()
        (foreign_root / ".rig").mkdir()
        (foreign_root / ".rig" / "harness.toml").write_text('parent = "codex"\n')
        foreign = self.call(12, "rig_memory", {"repo": str(foreign_root)})
        self.assertTrue((foreign.get("result") or {}).get("isError"))
        self.assertIn("different repo", _text(foreign))

    def send_list(self):
        self.send({"id": 2, "method": "tools/list"})
        return self.response(2)

    def test_permission_bootstrap_does_not_mark_connected(self):
        self.send({"id": 20, "method": "tools/call", "params": {
            "name": "permission_prompt",
            "arguments": {"tool_name": "Bash", "input": {"command": "ls"}},
        }})
        ask = self.job_dir / "ask.json"
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if ask.is_file():
                break
            time.sleep(0.02)
        self.assertTrue(ask.is_file())
        meta = json.loads((self.job_dir / "meta.json").read_text())
        self.assertNotEqual(meta.get("child_mcp_status"), child_mcp.CONNECTED)
        self.assertFalse(child_mcp.handshake_connected(self.job_dir))
        self.assertIsNone(self.proc.poll())


class DevinRepoMcp(unittest.TestCase):
    def test_install_restores_existing_config_and_releases_own_lock_only(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            job_dir = repo / ".rig" / "jobs" / "devin-job"
            job_dir.mkdir(parents=True)
            config = repo / ".devin" / "mcp_config.local.json"
            original = {"mcpServers": {"other": {"command": "keep"}}}
            config.parent.mkdir()
            config.write_text(json.dumps(original) + "\n")

            state = child_mcp.install_devin_repo_mcp(job_dir, "devin-job", repo)
            installed = json.loads(config.read_text())
            self.assertIn("rig", installed["mcpServers"])
            self.assertEqual((repo / ".rig" / "devin.lock").read_text().strip(), "devin-job")

            child_mcp.restore_devin_repo_mcp(job_dir, repo)
            self.assertEqual(json.loads(config.read_text()), original)
            self.assertFalse((repo / ".rig" / "devin.lock").exists())
            self.assertEqual(state["job_id"], "devin-job")

            lock = repo / ".rig" / "devin.lock"
            lock.write_text("other-job\n")
            child_mcp.release_devin_lock(repo, "")
            self.assertTrue(lock.exists())


if __name__ == "__main__":
    unittest.main()
