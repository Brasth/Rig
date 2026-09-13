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
