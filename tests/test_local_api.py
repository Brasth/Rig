#!/usr/bin/env python3
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_api  # noqa: E402
import rig_mcp  # noqa: E402
from mcp_tools.schemas import PARENT_TOOL_NAMES  # noqa: E402

TOKEN = "t" * 64


def make_repo(root: Path) -> Path:
    (root / ".git").mkdir()
    (root / ".rig").mkdir()
    (root / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n\n[workers]\ngrok = true\n")
    return root


def rpc(method, params=None, token=TOKEN, request_id=7):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}, "token": token}


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.td.name))
        self.api = local_api.Api(self.repo, TOKEN)
        self.clean_env = mock.patch.dict(os.environ, {}, clear=False)
        self.clean_env.start()
        for key in ("RIG_JOB_ID", "RIG_JOB_DIR"):
            os.environ.pop(key, None)

    def tearDown(self):
        self.clean_env.stop()
        self.td.cleanup()

    def test_every_response_carries_version_and_id(self):
        reply = self.api.dispatch(rpc("api.ping"))
        self.assertEqual((reply["rig_api"], reply["id"], reply["jsonrpc"]), (1, 7, "2.0"))
        self.assertTrue(reply["result"]["ok"])

    def test_token_is_required_and_checked(self):
        for token in (None, "", "wrong", TOKEN[:-1]):
            message = rpc("api.ping", token=token)
            if token is None:
                del message["token"]
            reply = self.api.dispatch(message)
            self.assertEqual(reply["error"]["code"], local_api.UNAUTHORIZED, token)
            self.assertNotIn("result", reply)

    def test_malformed_requests(self):
        self.assertEqual(self.api.dispatch([])["error"]["code"], local_api.INVALID_REQUEST)
        self.assertEqual(self.api.dispatch({"jsonrpc": "1.0", "method": "x"})["error"]["code"], local_api.INVALID_REQUEST)
        self.assertEqual(self.api.dispatch(rpc("nope"))["error"]["code"], local_api.METHOD_NOT_FOUND)
        bad = rpc("jobs.list")
        bad["params"] = [1]
        self.assertEqual(self.api.dispatch(bad)["error"]["code"], local_api.INVALID_PARAMS)

    def test_registry_maps_only_real_parent_tools(self):
        for method, (tool, _, _) in local_api.METHODS.items():
            self.assertIn(tool, PARENT_TOOL_NAMES, method)
        listed = {row["method"] for row in self.api.dispatch(rpc("api.methods"))["result"]["methods"]}
        self.assertTrue(set(local_api.METHODS) <= listed)

    def test_client_cannot_redirect_the_repository(self):
        seen = {}

        def fake(name, args, *a, **k):
            seen.update(name=name, args=args)
            return {"content": [{"type": "text", "text": "ok"}]}

        with mock.patch.object(rig_mcp, "call_tool", fake):
            self.api.dispatch(rpc("queue.list", {"repo": "/etc", "status": "pending"}))
        self.assertEqual(seen["name"], "rig_queue_list")
        self.assertEqual(seen["args"]["repo"], str(self.repo.resolve()))
        self.assertEqual(seen["args"]["status"], "pending")

    def test_tool_error_becomes_json_rpc_error(self):
        fake = lambda *a, **k: {"content": [{"type": "text", "text": "Rig is disabled"}], "isError": True}  # noqa: E731
        with mock.patch.object(rig_mcp, "call_tool", fake):
            reply = self.api.dispatch(rpc("job.cancel", {"id": "x"}))
        self.assertEqual(reply["error"]["code"], local_api.SERVER_ERROR)
        self.assertEqual(reply["error"]["message"], "Rig is disabled")

    def test_owner_tokens_are_redacted_in_text_and_json(self):
        body = json.dumps({"job": "j1", "owner_token": "SECRET-1", "nested": [{"owner_token": "SECRET-2"}]})
        fake = lambda *a, **k: {"content": [{"type": "text", "text": body}]}  # noqa: E731
        with mock.patch.object(rig_mcp, "call_tool", fake):
            reply = self.api.dispatch(rpc("jobs.show", {"id": "j1"}))
        wire = json.dumps(reply)
        self.assertNotIn("SECRET-1", wire)
        self.assertNotIn("SECRET-2", wire)
        self.assertEqual(reply["result"]["json"]["job"], "j1")

    def test_result_matches_the_mcp_tool_text(self):
        direct = rig_mcp.call_tool("rig_queue_list", {"repo": str(self.repo)})
        reply = self.api.dispatch(rpc("queue.list"))
        self.assertNotIn("error", reply)
        self.assertEqual(reply["result"]["text"], direct["content"][0]["text"])

    def test_non_json_text_has_no_json_field(self):
        fake = lambda *a, **k: {"content": [{"type": "text", "text": "plain"}]}  # noqa: E731
        with mock.patch.object(rig_mcp, "call_tool", fake):
            result = self.api.dispatch(rpc("status"))["result"]
        self.assertEqual(result, {"text": "plain"})


class Served(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.td.name))
        self.env = {k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}}
        self.script = str(ROOT / "scripts" / "local_api.py")
        self.proc = None

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        self.td.cleanup()

    def start(self):
        self.proc = subprocess.Popen([sys.executable, self.script, "serve", "--repo", str(self.repo)],
                                     env=self.env, stderr=subprocess.PIPE, text=True)
        endpoint, token = local_api.runtime_paths(self.repo)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if endpoint.exists() and token.exists():
                try:
                    local_api.call(self.repo, "api.ping")
                    return endpoint, token
                except OSError:
                    pass
            time.sleep(0.05)
        self.fail("API did not start")

    def test_serves_over_private_socket_and_cleans_up(self):
        endpoint, token = self.start()
        self.assertEqual(stat.S_IMODE(endpoint.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(endpoint.parent.stat().st_mode), 0o700)
        reply = local_api.call(self.repo, "api.ping")
        self.assertEqual(reply["result"]["repo"], str(self.repo.resolve()))
        listed = local_api.call(self.repo, "queue.list")
        self.assertNotIn("error", listed)
        second = subprocess.run([sys.executable, self.script, "serve", "--repo", str(self.repo)],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(second.returncode, 2, second.stderr)
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.assertFalse(endpoint.exists())
        self.assertFalse(token.exists())

    def test_garbage_and_wrong_token_over_the_wire(self):
        import socket
        endpoint, _ = self.start()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(str(endpoint))
            stream.sendall(b"not json\n")
            reply = json.loads(stream.makefile().readline())
        self.assertEqual(reply["error"]["code"], local_api.PARSE_ERROR)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(str(endpoint))
            stream.sendall(json.dumps(rpc("api.ping", token="bad")).encode() + b"\n")
            reply = json.loads(stream.makefile().readline())
        self.assertEqual(reply["error"]["code"], local_api.UNAUTHORIZED)

    def test_cli_call_without_server_fails_cleanly(self):
        done = subprocess.run([sys.executable, self.script, "call", "api.ping", "--repo", str(self.repo)],
                              env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not running", done.stderr)

    def test_worker_child_and_uninitialized_repo_cannot_serve(self):
        child = subprocess.run([sys.executable, self.script, "serve", "--repo", str(self.repo)],
                               env={**self.env, "RIG_JOB_ID": "j1"}, capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, 1)
        self.assertIn("worker child", child.stderr)
        (self.repo / ".rig" / "harness.toml").unlink()
        bare = subprocess.run([sys.executable, self.script, "serve", "--repo", str(self.repo)],
                              env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(bare.returncode, 1)
        self.assertIn("uninitialized", bare.stderr)


class Doc(unittest.TestCase):
    def test_generated_doc_is_current(self):
        self.assertEqual((ROOT / "docs" / "local-api.md").read_text(), local_api.render_doc(),
                         "run: python3 scripts/local_api.py doc > docs/local-api.md")


if __name__ == "__main__":
    unittest.main()
