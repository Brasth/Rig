"""Queue receipts and claims survive delivery retries and short-lived CLI calls."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import admission
import queue_submit_hook as hook
import work_queue as queue


class QueueRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        (self.repo / ".rig/harness.toml").write_text('parent = "codex"\n[workers]\ngrok = true\n')

    def cli(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "scripts/work_queue.py"), *arguments,
                               "--repo", str(self.repo)], capture_output=True, text=True)

    def test_duplicate_key_returns_original_but_identical_text_without_key_is_new(self):
        first = queue.add_item(self.repo, "same text", idempotency_key="delivery-1")
        repeated = queue.add_item(self.repo, "changed payload", idempotency_key="delivery-1")
        self.assertEqual(repeated, first)
        other = queue.add_item(self.repo, "same text")
        self.assertNotEqual(other["id"], first["id"])
        queue.cancel_item(self.repo, first["id"])
        self.assertEqual(queue.add_item(self.repo, "same text", idempotency_key="delivery-1")["status"], "cancelled")

    def test_cli_busy_lock_fails_without_committing_or_retrying(self):
        folder = self.repo / ".rig/queue"
        folder.mkdir()
        with (folder / ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            started = time.monotonic()
            proc = self.cli("add", "busy task", "--idempotency-key", "busy-delivery")
            elapsed = time.monotonic() - started
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("busy", proc.stderr.lower())
        self.assertLess(elapsed, 3)
        self.assertEqual(queue.list_items(self.repo), [])

    def test_hook_receipt_does_not_depend_on_status_scan(self):
        payload = {"prompt": "/queue task  with\nbody --priority 8", "cwd": str(self.repo),
                   "idempotency_key": "delivery-2"}
        with mock.patch.object(queue, "format_block", side_effect=RuntimeError("status unavailable")):
            first = hook.handle(payload)
            repeated = hook.handle(payload)
        self.assertEqual(first, repeated)
        items = queue.list_items(self.repo)
        self.assertEqual(len(items), 1)
        self.assertIn(items[0]["id"], first["reason"])
        self.assertEqual(items[0]["text"], "task  with\nbody --priority 8")
        self.assertEqual(items[0]["priority"], 0)

    def test_parser_requires_exact_command_and_cancel_tokens(self):
        for text in ("/queueing task", "$queue-next task", "/prompts:queueing", "$rig-queue-more"):
            self.assertIsNone(queue.parse_slash(text))
        for text in ("cancellation review", "cancelation fix", "cancelled task"):
            self.assertEqual(queue.parse_slash("/queue " + text)["text"], text)
        parsed = queue.parse_slash("/queue -p 2 --worker=grok fix  command\n--worker claude")
        self.assertEqual(parsed, {"action": "add", "priority": 2, "worker": "grok",
                                  "text": "fix  command\n--worker claude"})
        self.assertEqual(queue.parse_slash("/queue -- cancel this request")["text"], "cancel this request")

    def test_cli_claim_survives_command_exit_and_can_be_consumed(self):
        item = queue.add_item(self.repo, "claim work")
        proc = self.cli("claim", item["id"], "--files", "a.py", "--owner-pid", str(os.getpid()),
                        "--owner-session", "durable-claim", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        claim = json.loads(proc.stdout)
        self.assertEqual(claim["owner"]["pid"], os.getpid())
        self.assertEqual(admission._process_state(claim["owner"]), "alive")
        credentials_path = Path(claim["credentials_path"])
        self.assertEqual(credentials_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(credentials_path.read_text())["owner_token"], claim["owner_token"])
        # Only binary availability is irrelevant to this real subprocess handoff.
        with mock.patch.object(admission, "_validate"):
            consumed = admission.reserve(self.repo, queue_id=item["id"], job_id="worker-job",
                                          worker="grok", files=["a.py"], owner_session="durable-claim",
                                          **admission.credentials(claim))
        self.assertTrue(consumed["claim_consumed"])
        self.assertEqual(consumed["stage"], "reserved")

    def test_human_claim_exposes_private_path_not_token(self):
        item = queue.add_item(self.repo, "human claim")
        proc = self.cli("claim", item["id"], "--files", "a.py", "--owner-pid", str(os.getpid()),
                        "--owner-session", "human-claim")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = self.repo / ".rig/queue/credentials" / (item["id"] + ".json")
        token = json.loads(path.read_text())["owner_token"]
        self.assertIn(str(path), proc.stdout)
        self.assertNotIn(token, proc.stdout)

    def test_invalid_explicit_owner_does_not_claim(self):
        item = queue.add_item(self.repo, "unclaimed work")
        proc = self.cli("claim", item["id"], "--owner-pid", "1", "--owner-session", "invalid")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(queue.load_item(self.repo, item["id"])["status"], "pending")
        self.assertEqual(admission.list_reservations(self.repo), [])

    def test_cancel_same_owner_unlaunched_claim_releases_capacity(self):
        item = queue.add_item(self.repo, "cancel work")
        claim = queue.claim_next(self.repo, item_id=item["id"], files=["a.py"], owner_session="same")
        result = queue.cancel_item(self.repo, item["id"], owner_session="same")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(admission.get_reservation(self.repo, claim["reservation_id"])["stage"], "released")
        self.assertEqual(queue.slot_count(self.repo), 0)

    def test_cancel_other_owner_preserves_reservation_and_reports_reason(self):
        item = queue.add_item(self.repo, "cancel held work")
        claim = queue.claim_next(self.repo, item_id=item["id"], files=["a.py"], owner_session="first")
        result = queue.cancel_item(self.repo, item["id"], owner_session="different")
        self.assertEqual(result["status"], "cancelled")
        self.assertIn("reservation remains held", result["held_reason"])
        self.assertEqual(admission.get_reservation(self.repo, claim["reservation_id"])["stage"], "reserved")


if __name__ == "__main__":
    unittest.main()
