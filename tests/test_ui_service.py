"""UI observer lifetimes, receipts, milestone truth, and input-safe status."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ui_actions import Actions
from ui_notices import Notices, status_line
from ui_service import Service
from ui_snapshot import MAX_UI_WORKFLOWS, Collector, collect_workflows, public_workflow_row


def write_workflow(repo, workflow_id, *, status="running", nodes=None, node_state=None,
                   failure=None, parent_action=None, owner_token="", coordination=None,
                   title="", cancel_requested=False):
    folder = Path(repo) / ".rig" / "workflows" / workflow_id
    folder.mkdir(parents=True, exist_ok=True)
    nodes = nodes or [{"id": "n1", "role": "implement", "files": ["a.py"], "required": True, "depends_on": []}]
    node_state = node_state or {node["id"]: {"status": "pending", "accepted": False} for node in nodes}
    spec = {
        "version": 1, "workflow_id": workflow_id, "title": title or workflow_id,
        "case": title or workflow_id, "nodes": nodes, "spec_hash": "hash-" + workflow_id,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    state = {
        "version": 1, "workflow_id": workflow_id, "status": status, "nodes": node_state,
        "updated_at": "2026-01-02T00:00:00+00:00", "parent_action": parent_action,
        "failure": failure, "coordination": coordination or [],
        "cancel_requested": cancel_requested or status == "cancel-requested",
    }
    if owner_token:
        state["owner_token"] = owner_token
        (folder / "owner-credentials.json").write_text(json.dumps({
            "workflow_id": workflow_id, "owner_token": owner_token,
        }))
    (folder / "spec.json").write_text(json.dumps(spec))
    (folder / "state.json").write_text(json.dumps(state))
    return folder


def active_nodes(accepted=1, running=1, ask=0, pending=1):
    nodes, state, index = [], {}, 1
    for _ in range(accepted):
        nid = f"a{index}"; index += 1
        nodes.append({"id": nid, "role": "implement", "files": [f"{nid}.py"], "required": True, "depends_on": []})
        state[nid] = {"status": "accepted", "accepted": True}
    for _ in range(running):
        nid = f"r{index}"; index += 1
        nodes.append({"id": nid, "role": "mini", "files": [f"{nid}.py"], "required": True, "depends_on": []})
        state[nid] = {"status": "running", "accepted": False}
    for _ in range(ask):
        nid = f"k{index}"; index += 1
        nodes.append({"id": nid, "role": "hard", "files": [f"{nid}.py"], "required": True, "depends_on": []})
        state[nid] = {"status": "ask", "accepted": False, "job_id": "ask-job"}
    for _ in range(pending):
        nid = f"p{index}"; index += 1
        nodes.append({"id": nid, "role": "verify", "files": [f"{nid}.py"], "required": True, "depends_on": []})
        state[nid] = {"status": "pending", "accepted": False}
    return nodes, state


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 100
        self.notices = Notices(Path(self.temp.name) / "notices.json", clock=lambda: self.now)

    def job(self, state, **extra):
        return {"job_id": "one", "attempt_id": "first", "task": "Fix login", "worker": "claude",
                "display_state": state, "effective": "running" if state == "working" else "ok", **extra}

    def test_baseline_is_quiet_and_updates_are_deduplicated(self):
        self.notices.update({"jobs": [self.job("completed-unverified")], "pending": []})
        self.assertEqual(self.notices.items, [])
        value = {"jobs": [self.job("verified")], "pending": []}
        self.notices.update(value)
        self.notices.update(value)
        self.assertEqual(len(self.notices.items), 1)
        self.assertEqual(self.notices.items[0]["text"], "Fix login verified")
        self.notices.ack("a", all=True)
        self.assertFalse(self.notices.list("a")[0]["unread"])
        self.assertTrue(self.notices.list("b")[0]["unread"])

    def test_stop_request_does_not_claim_termination(self):
        self.notices.update({"jobs": [], "pending": []})
        self.notices.update({"jobs": [self.job("needs-input", cancellation_state="stop-unconfirmed")], "pending": []})
        notice = self.notices.items[0]
        self.assertIn("unconfirmed", notice["text"])
        self.assertNotIn("Stopped:", notice["text"])

    def test_restart_does_not_report_unchecked_history_as_new_completion(self):
        self.notices.update({"jobs": [], "pending": []})
        self.notices.update({"jobs": [self.job("verified")], "pending": []})
        restarted = Notices(self.notices.path, clock=lambda: self.now)
        restarted.update({"jobs": [self.job("completed-unverified")], "pending": []})
        self.assertEqual(len(restarted.items), 1)
        self.assertEqual(restarted.items[0]["text"], "Fix login verified")

    def test_status_retains_attention_without_a_toast(self):
        snap = {"jobs": [self.job("needs-input", effective="ask", display_reason="Approve tool")], "pending": []}
        line = status_line(snap, now=100)
        self.assertIn("!1", line)
        self.assertIn("Approve tool", line)
        self.assertIn("age unknown", line)
        snap["error"] = "refresh failed"
        self.assertIn("status stale", status_line(snap, now=100))

    def test_control_codes_and_notice_bursts_do_not_become_terminal_commands(self):
        self.notices.add("\x1b[2Jhello\nworld")
        self.notices.add("second")
        line = status_line({"jobs": [], "pending": []}, self.notices.list(), now=100)
        self.assertNotIn("\x1b", line)
        self.assertNotIn("\n", line)
        self.assertIn("2 updates", line)

    def test_workflow_attention_and_blocker_notices_without_tokens(self):
        secret = "cafebabedeadbeefcafebabedeadbeef"
        self.notices.update({"jobs": [], "pending": [], "workflows": []})
        row = public_workflow_row({
            "workflow_id": "wf-attention", "status": "attention", "accepted": 1, "required": 3,
            "running": 0, "ask": 1, "blocker": "parent acceptance required",
            "next_parent_action": {"kind": "allow_or_deny", "node_id": "k1", "owner_token": secret},
            "owner_token": secret,
        })
        self.assertNotIn(secret, json.dumps(row))
        self.notices.update({"jobs": [], "pending": [], "workflows": [row]})
        self.assertEqual(len(self.notices.items), 1)
        self.assertTrue(self.notices.items[0]["attention"])
        self.assertIn("wf-attention", self.notices.items[0]["text"])
        self.assertNotIn(secret, self.notices.items[0]["text"])
        blocked = public_workflow_row({
            "workflow_id": "wf-blocked", "status": "blocked", "accepted": 0, "required": 1,
            "running": 0, "ask": 0, "blocker": "unresolved failure on n1",
            "next_parent_action": {"kind": "resolve", "node_id": "n1"},
        })
        self.notices.update({"jobs": [], "pending": [], "workflows": [row, blocked]})
        texts = [item["text"] for item in self.notices.items]
        self.assertTrue(any("blocked" in text and "wf-blocked" in text for text in texts))
        self.assertNotIn(secret, json.dumps(self.notices.items))

    def test_status_line_names_active_and_attention_workflows(self):
        snap = {
            "jobs": [self.job("working")], "pending": [],
            "workflows": [
                {"workflow_id": "wf-run", "status": "running", "accepted": 1, "required": 2,
                 "running": 1, "ask": 0, "blocker": "", "next_parent_action": {"kind": "advance"}},
                {"workflow_id": "wf-block", "status": "blocked", "accepted": 0, "required": 1,
                 "running": 0, "ask": 0, "blocker": "unresolved failure on n1",
                 "next_parent_action": {"kind": "resolve", "node_id": "n1"}},
            ],
        }
        line = status_line(snap, now=100)
        self.assertIn("2 wf", line)
        self.assertIn("wf!1", line)
        self.assertIn("wf-block", line)
        self.assertNotIn("%", line)
        self.assertNotIn("ETA", line)
        self.assertNotIn("savings", line)


class WorkflowSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()

    def test_no_workflow_snapshot_keeps_jobs_and_empty_collection(self):
        folder = self.repo / ".rig" / "jobs" / "job-one"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({
            "job_id": "job-one", "worker": "claude", "status": "ok", "task": "plain job",
        }))
        snap = Collector(self.repo).collect()
        self.assertIsNone(snap["error"])
        self.assertEqual(snap["workflows"], [])
        self.assertEqual(len(snap["jobs"]), 1)
        self.assertEqual(snap["jobs"][0]["job_id"], "job-one")

    def test_one_active_workflow_is_factual(self):
        nodes, state = active_nodes(accepted=1, running=1, ask=0, pending=1)
        write_workflow(self.repo, "wf-active", status="running", nodes=nodes, node_state=state,
                       title="ship it")
        snap = Collector(self.repo).collect()
        self.assertEqual(len(snap["workflows"]), 1)
        row = snap["workflows"][0]
        self.assertEqual(row["workflow_id"], "wf-active")
        self.assertEqual((row["accepted"], row["required"], row["running"], row["ask"]), (1, 3, 1, 0))
        self.assertEqual(row["next_parent_action"]["kind"], "advance")
        blob = json.dumps(row)
        self.assertNotIn("percent", blob.lower())
        self.assertNotIn("eta", blob.lower())
        self.assertNotIn("savings", blob.lower())

    def test_blocked_and_attention_workflows_surface_blocker_and_action(self):
        nodes, state = active_nodes(accepted=1, running=0, ask=1, pending=1)
        write_workflow(self.repo, "wf-ask", status="attention", nodes=nodes, node_state=state)
        failed = [{"id": "n1", "role": "implement", "files": ["a.py"], "required": True, "depends_on": []}]
        write_workflow(self.repo, "wf-fail", status="blocked", nodes=failed,
                       node_state={"n1": {"status": "failed", "accepted": False}},
                       failure={"node_id": "n1", "reason": "node failed"})
        rows = {row["workflow_id"]: row for row in Collector(self.repo).collect()["workflows"]}
        self.assertEqual(rows["wf-ask"]["ask"], 1)
        self.assertEqual(rows["wf-ask"]["next_parent_action"]["kind"], "allow_or_deny")
        self.assertEqual(rows["wf-fail"]["blocker"], "unresolved failure on n1")
        self.assertEqual(rows["wf-fail"]["next_parent_action"]["kind"], "resolve")
        self.assertEqual(set(rows), {"wf-ask", "wf-fail"})

    def test_multiple_active_workflows_and_malformed_legacy_are_tolerated(self):
        for index in range(3):
            nodes, state = active_nodes(accepted=0, running=1, ask=0, pending=0)
            write_workflow(self.repo, f"wf-live-{index}", status="running", nodes=nodes, node_state=state)
        bad = self.repo / ".rig" / "workflows" / "legacy-bad"
        bad.mkdir(parents=True)
        (bad / "spec.json").write_text("{")
        (bad / "state.json").write_text("not-json")
        missing = self.repo / ".rig" / "workflows" / "legacy-partial"
        missing.mkdir()
        (missing / "spec.json").write_text(json.dumps({"workflow_id": "legacy-partial", "nodes": []}))
        snap = Collector(self.repo).collect()
        self.assertIsNone(snap["error"])
        ids = [row["workflow_id"] for row in snap["workflows"]]
        self.assertEqual(sorted(ids), ["wf-live-0", "wf-live-1", "wf-live-2"])
        self.assertNotIn("legacy-bad", ids)
        self.assertNotIn("legacy-partial", ids)

    def test_owner_tokens_are_scrubbed_from_snapshot(self):
        secret = "aabbccddeeff00112233445566778899"
        nodes, state = active_nodes(accepted=0, running=0, ask=0, pending=1)
        write_workflow(self.repo, "wf-secret", status="planned", nodes=nodes, node_state=state,
                       owner_token=secret, parent_action={"kind": "advance", "owner_token": secret})
        snap = Collector(self.repo).collect()
        blob = json.dumps(snap)
        self.assertNotIn(secret, blob)
        self.assertNotIn("owner_token", blob)
        self.assertEqual(snap["workflows"][0]["next_parent_action"]["kind"], "advance")

    def test_snapshot_bounds_workflow_collection(self):
        for index in range(MAX_UI_WORKFLOWS + 8):
            status = "blocked" if index < 3 else "running"
            nodes, state = active_nodes(accepted=0, running=int(status == "running"), ask=0, pending=1)
            write_workflow(self.repo, f"wf-{index:02d}", status=status, nodes=nodes, node_state=state,
                           failure={"node_id": "a1", "reason": "node failed"} if status == "blocked" else None)
        rows = collect_workflows(self.repo)
        self.assertEqual(len(rows), MAX_UI_WORKFLOWS)
        self.assertEqual(sum(row["status"] == "blocked" for row in rows), 3)
        self.assertTrue(all(row["status"] == "blocked" for row in rows[:3]))


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.actions = Actions(self.repo)

    def completed(self, action_id):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = self.actions.get(action_id)
            if value["status"] != "pending":
                return value
            time.sleep(0.005)
        self.fail("action failed to finish")

    def test_runtime_socket_path_fits_macos_even_with_a_long_temp_directory(self):
        from ui_store import runtime_directory
        with patch("tempfile.gettempdir", return_value="/var/folders/" + "x" * 120):
            endpoint = runtime_directory(self.repo) / "control.sock"
        self.assertLess(len(str(endpoint).encode()), 104)

    def test_disconnected_client_does_not_cancel_accepted_action(self):
        entered, release = threading.Event(), threading.Event()
        def execute(request):
            entered.set()
            release.wait(1)
            return {"id": "committed"}
        self.actions.execute = execute
        receipt = self.actions.submit({"op": "enqueue", "action_id": "one", "text": "task", "submission_id": "submission"})
        self.assertEqual(receipt["status"], "pending")
        self.assertTrue(entered.wait(1))
        release.set()
        self.assertEqual(self.completed("one")["result"], {"id": "committed"})
        reloaded = Actions(self.repo)
        self.assertEqual(reloaded.get("one")["status"], "done")

    def test_same_action_id_cannot_be_reused_for_a_different_target(self):
        self.actions.execute = lambda _: {"ok": True}
        request = {"op": "stop", "action_id": "one", "target": "first"}
        self.actions.submit(request)
        self.completed("one")
        self.assertEqual(self.actions.submit(request)["status"], "done")
        with self.assertRaisesRegex(ValueError, "different request"):
            self.actions.submit({**request, "target": "replacement"})

    def test_restart_does_not_reexecute_uncertain_action(self):
        from ui_store import write
        write(self.actions.path("one"), {"action_id": "one", "status": "pending", "request": "{}"})
        reloaded = Actions(self.repo)
        self.assertEqual(reloaded.get("one")["status"], "unknown")

    def test_status_reader_does_not_wait_for_action_lane(self):
        class Collector:
            def collect(self): return {"jobs": [], "pending": [], "slots": 0, "cap": 3, "error": ""}
        service = Service(self.repo, collector=Collector())
        gate = threading.Event()
        def slow_action(payload):
            gate.wait(1)
            return {"id": payload["op"] + str(id(payload)), "text": "task"}
        service.actions.execute = slow_action
        for index in range(2):
            service.handle({"op": "enqueue", "action_id": str(index)})
        started = time.monotonic()
        self.assertTrue(service.handle({"op": "ping"})["ok"])
        self.assertIn("status_line", service.handle({"op": "snapshot"}))
        self.assertLess(time.monotonic() - started, 0.1)
        gate.set()
        deadline = time.monotonic() + 2
        while service.actions.running and time.monotonic() < deadline: time.sleep(0.01)
        self.assertFalse(service.actions.running)


if __name__ == "__main__":
    unittest.main()
