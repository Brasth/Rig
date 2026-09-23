#!/usr/bin/env python3
"""Protocol boundary tests for the persistent Cua Driver transport."""
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import cua_transport


SERVER = r'''#!/usr/bin/env python3
import json, os, sys
log = open(os.environ["CUA_TEST_LOG"], "a", encoding="utf-8")
for raw in sys.stdin:
    msg = json.loads(raw)
    log.write(json.dumps(msg) + "\n"); log.flush()
    if "id" not in msg:
        continue
    if msg["method"] == "tools/call" and msg["params"]["name"] == "click" and os.environ.get("CUA_TEST_EOF") == "1":
        sys.exit(0)
    result = {"structuredContent": {"ok": True, "tool": msg["params"].get("name", msg["method"])}}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''


class CuaTransportTests(unittest.TestCase):
    def setUp(self):
        cua_transport.close_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(cua_transport.close_all)
        root = Path(self.tmp.name)
        self.log = root / "protocol.jsonl"
        self.binary = root / "fake-cua-driver"
        self.binary.write_text(textwrap.dedent(SERVER))
        self.binary.chmod(0o755)
        self.old_log = os.environ.get("CUA_TEST_LOG")
        self.old_eof = os.environ.get("CUA_TEST_EOF")
        os.environ["CUA_TEST_LOG"] = str(self.log)

    def tearDown(self):
        if self.old_log is None:
            os.environ.pop("CUA_TEST_LOG", None)
        else:
            os.environ["CUA_TEST_LOG"] = self.old_log
        if self.old_eof is None:
            os.environ.pop("CUA_TEST_EOF", None)
        else:
            os.environ["CUA_TEST_EOF"] = self.old_eof

    def _messages(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_one_mcp_child_initializes_starts_session_and_handles_multiple_tools(self):
        first = cua_transport.call(str(self.binary), "session-a", "get_window_state", {"pid": 1}, 3)
        second = cua_transport.call(str(self.binary), "session-a", "click", {"pid": 1}, 3)
        self.assertTrue(first["structuredContent"]["ok"])
        self.assertTrue(second["structuredContent"]["ok"])
        messages = self._messages()
        calls = [item for item in messages if item.get("method") == "tools/call"]
        self.assertEqual([call["params"]["name"] for call in calls], [
            "start_session", "get_window_state", "click",
        ])
        self.assertEqual(calls[0]["params"]["arguments"], {"session": "session-a"})
        self.assertEqual(sum(1 for item in messages if item.get("method") == "initialize"), 1)

    def test_eof_never_replays_mutation_and_next_call_creates_fresh_connection(self):
        os.environ["CUA_TEST_EOF"] = "1"
        with self.assertRaises(cua_transport.TransportError):
            cua_transport.call(str(self.binary), "session-b", "click", {"pid": 1}, 3)
        os.environ.pop("CUA_TEST_EOF")
        cua_transport.call(str(self.binary), "session-b", "get_window_state", {"pid": 1}, 3)
        calls = [item["params"]["name"] for item in self._messages() if item.get("method") == "tools/call"]
        self.assertEqual(calls.count("click"), 1)
        self.assertEqual(calls.count("start_session"), 2)
        self.assertEqual(calls[-1], "get_window_state")
