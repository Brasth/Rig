import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ask
import jobs
import work_queue


class ActionRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.folder = self.repo / ".rig" / "jobs" / "job"
        self.folder.mkdir(parents=True)
        jobs.patch_meta(self.folder, job_id="job", status="running", pid=os.getpid(),
                        model="claude-sonnet-5", worker="claude")

    def test_pending_cancel_cannot_mutate_new_claim(self):
        queue = self.repo / ".rig" / "queue"
        queue.mkdir()
        value = {"id": "q", "status": "claimed", "reservation_id": "held"}
        path = queue / "q.json"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "changed"):
            work_queue.cancel_item(self.repo, "q", expected_status="pending")
        self.assertEqual(json.loads(path.read_text()), value)

    def test_pending_cancel_is_allowed(self):
        queue = self.repo / ".rig" / "queue"
        queue.mkdir()
        (queue / "q.json").write_text(json.dumps({"id": "q", "status": "pending"}))
        self.assertEqual(work_queue.cancel_item(self.repo, "q", expected_status="pending")["status"], "cancelled")

    def test_stale_display_cannot_answer_repeated_tool_id(self):
        ask.write_ask(self.folder, "Bash", {"command": "first"}, "same-tool")
        shown = jobs.load_job(self.folder)
        new = ask.write_ask(self.folder, "Bash", {"command": "second"}, "same-tool")
        self.assertIn("changed", jobs.answer_pending(shown, "allow"))
        self.assertFalse(ask.reply_path(self.folder).exists())
        self.assertEqual(ask.load_ask(self.folder)["ask_id"], new["ask_id"])

    def test_duplicate_same_reply_is_idempotent_opposite_rejected(self):
        pending = ask.write_ask(self.folder, "Bash", {}, "tool")
        first = ask.write_reply(self.folder, "allow", expected=pending)
        self.assertEqual(ask.write_reply(self.folder, "allow", expected=pending), first)
        with self.assertRaisesRegex(ValueError, "differently"):
            ask.write_reply(self.folder, "deny", expected=pending)

    def test_concurrent_opposite_answers_publish_exactly_one(self):
        pending = ask.write_ask(self.folder, "Bash", {}, "tool")
        gate, results = threading.Barrier(2), []
        def answer(behavior):
            gate.wait()
            try:
                results.append(ask.write_reply(self.folder, behavior, expected=pending)["behavior"])
            except ValueError:
                results.append("rejected")
        threads = [threading.Thread(target=answer, args=(value,)) for value in ("allow", "deny")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(results.count("rejected"), 1)
        self.assertIn(ask._read_json(ask.reply_path(self.folder))["behavior"], results)

    def test_job_attempt_change_rejects_old_permission(self):
        jobs.patch_meta(self.folder, attempt_id="a", reservation_id="r")
        pending = ask.write_ask(self.folder, "Bash", {}, "tool")
        jobs.patch_meta(self.folder, attempt_id="b")
        with self.assertRaisesRegex(ValueError, "earlier job attempt"):
            ask.write_reply(self.folder, "allow", expected=pending)

    def test_finished_job_rejects_stale_display_reply(self):
        ask.write_ask(self.folder, "Bash", {}, "tool")
        shown = jobs.load_job(self.folder)
        jobs.patch_meta(self.folder, status="ok")
        self.assertIn("no longer waiting", jobs.answer_pending(shown, "allow"))
        self.assertFalse(ask.reply_path(self.folder).exists())

    def test_legacy_request_can_be_answered_but_unbound_reply_is_ignored(self):
        ask.ask_path(self.folder).write_text(json.dumps({"tool_name": "Bash", "input": {}, "tool_use_id": "old"}))
        ask.reply_path(self.folder).write_text(json.dumps({"behavior": "allow", "tool_use_id": "old"}))
        pending = ask.load_ask(self.folder)
        self.assertTrue(pending["ask_id"].startswith("legacy-"))
        reply = ask.write_reply(self.folder, "deny", expected=pending)
        self.assertEqual(ask.wait_reply(self.folder, timeout=1), reply)
        self.assertIsNone(ask.load_ask(self.folder))

    def test_unmatched_reply_never_hides_new_ask(self):
        old = ask.write_ask(self.folder, "Bash", {}, "old")
        new = ask.write_ask(self.folder, "Bash", {}, "new")
        ask.reply_path(self.folder).write_text(json.dumps({**old, "behavior": "allow"}))
        self.assertEqual(ask.load_ask(self.folder)["ask_id"], new["ask_id"])
        self.assertEqual(ask.wait_reply(self.folder, timeout=0.01)["behavior"], "deny")

    def test_original_waiter_cannot_consume_or_delete_replacement(self):
        ask.write_ask(self.folder, "Bash", {"command": "original"}, "tool")
        def replace():
            pending = ask.write_ask(self.folder, "Bash", {"command": "replacement"}, "tool")
            ask.write_reply(self.folder, "allow", expected=pending)
        writer = threading.Thread(target=replace)
        writer.start()
        writer.join(2)
        self.assertEqual(ask.wait_reply(self.folder, timeout=0.01)["behavior"], "deny")
        ask.consume_ask(self.folder)
        self.assertTrue(ask.ask_path(self.folder).exists())
        self.assertTrue(ask.reply_path(self.folder).exists())


if __name__ == "__main__":
    unittest.main()
