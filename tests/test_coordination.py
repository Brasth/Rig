#!/usr/bin/env python3
"""Child coordination requests cannot expand frozen contracts; parent may reply or stop."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import coordination
import workflow_state as wf


def _repo():
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name).resolve()
    (repo / ".git").mkdir()
    (repo / ".rig").mkdir()
    (repo / "a.py").write_text("a\n")
    (repo / "b.py").write_text("b\n")
    (repo / ".rig" / "harness.toml").write_text(
        'parent = "codex"\n[workers]\ngrok = true\n[orchestration]\nmode = "adaptive"\n'
        "[queue]\nmax_running = 3\n"
    )
    return temp, repo


class Coordination(unittest.TestCase):
    def setUp(self):
        self.temp, self.repo = _repo()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"RIG_OWNER_SESSION": "coord-tests"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.created = wf.create_workflow(
            self.repo,
            {"title": "t", "case": "c", "nodes": [{"id": "w1", "role": "implement", "files": ["a.py"]}]},
            owner_session="coord-tests",
        )
        self.wid = self.created["workflow_id"]
        self.token = self.created["owner_token"]
        self.job_id = "job-coord"
        folder = self.repo / ".rig" / "jobs" / self.job_id
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({
            "job_id": self.job_id, "worker": "grok", "role": "implement", "status": "running",
            "workflow_id": self.wid, "workflow_node_id": "w1",
        }))

    def test_kinds_and_pending_public_record(self):
        for kind in ("dependency", "contract", "scope"):
            result = coordination.request(self.repo, self.job_id, kind, f"need {kind}", {"note": "ok"})
            self.assertEqual(result["workflow_id"], self.wid)
            self.assertEqual(result["request"]["kind"], kind)
            self.assertNotIn("owner_token", json.dumps(result))
        rows = coordination.pending(self.repo, self.wid)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["status"] == "pending" for row in rows))
        self.assertNotIn(self.token, json.dumps(rows))

    def test_rejects_expansion_keys_including_nested(self):
        for payload in (
            {"files": ["b.py"]},
            {"resources": [{"name": "db", "access": "read"}]},
            {"effects": "production"},
            {"depends_on": ["w2"]},
            {"dependencies": ["w2"]},
            {"role": "hard"},
            {"nodes": [{"id": "x"}]},
            {"contracts": {}},
            {"nested": {"files": ["b.py"]}},
            {"items": [{"dependencies": ["n2"]}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(coordination.CoordinationError, "never expands"):
                    coordination.request(self.repo, self.job_id, "scope", "please expand", payload)

    def test_invalid_kind_text_payload_and_unbound_job(self):
        with self.assertRaisesRegex(coordination.CoordinationError, "dependency\\|contract\\|scope"):
            coordination.request(self.repo, self.job_id, "files", "nope")
        with self.assertRaisesRegex(coordination.CoordinationError, "needs text"):
            coordination.request(self.repo, self.job_id, "scope", "  ")
        with self.assertRaisesRegex(coordination.CoordinationError, "object"):
            coordination.request(self.repo, self.job_id, "scope", "hello", payload=["x"])
        other = self.repo / ".rig" / "jobs" / "unbound"
        other.mkdir()
        (other / "meta.json").write_text(json.dumps({
            "job_id": "unbound", "worker": "grok", "role": "implement", "status": "running",
        }))
        with self.assertRaisesRegex(coordination.CoordinationError, "not bound"):
            coordination.request(self.repo, "unbound", "scope", "hello")

    def test_parent_reply_and_stop_require_credentials(self):
        asked = coordination.request(self.repo, self.job_id, "dependency", "need review order")
        request_id = asked["request"]["id"]
        with self.assertRaisesRegex(wf.WorkflowError, "credentials"):
            coordination.reply(self.repo, self.wid, request_id, text="ok")
        public = coordination.reply(
            self.repo, self.wid, request_id, decision="reply", text="keep going",
            owner_token=self.token, owner_session="coord-tests",
        )
        self.assertNotIn(self.token, json.dumps(public))
        item = next(row for row in public["coordination"] if row["id"] == request_id)
        self.assertEqual(item["status"], "replied")
        again = coordination.reply(
            self.repo, self.wid, request_id, decision="reply", text="ignored",
            owner_token=self.token,
        )
        item = next(row for row in again["coordination"] if row["id"] == request_id)
        self.assertEqual(item["status"], "replied")
        second = coordination.request(self.repo, self.job_id, "scope", "too broad")
        stopped = coordination.reply(
            self.repo, self.wid, second["request"]["id"], decision="stop", text="no",
            owner_token=self.token,
        )
        item = next(row for row in stopped["coordination"] if row["id"] == second["request"]["id"])
        self.assertEqual(item["status"], "stopped")
        with self.assertRaisesRegex(coordination.CoordinationError, "reply or stop"):
            coordination.reply(
                self.repo, self.wid, second["request"]["id"], decision="expand",
                owner_token=self.token,
            )
        with self.assertRaisesRegex(coordination.CoordinationError, "needs text"):
            coordination.reply(
                self.repo, self.wid, "missing", decision="reply", text="",
                owner_token=self.token,
            )


if __name__ == "__main__":
    unittest.main()
