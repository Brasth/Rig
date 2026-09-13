"""Bounded request execution preserves cancellation and response lifetimes."""
import queue
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from mcp_runtime import Runtime


class RuntimeTests(unittest.TestCase):
    def runtime(self, execute, abort=lambda request: None):
        replies = queue.Queue()
        runtime = Runtime(execute, replies.put, abort)
        self.addCleanup(lambda: runtime.shutdown(timeout=0.15))
        return runtime, replies

    def test_numeric_and_string_ids_have_independent_cancellation(self):
        entered, aborted = queue.Queue(), queue.Queue()
        finish = threading.Event()
        self.addCleanup(finish.set)
        def execute(request):
            entered.put(request)
            request.bound.set()
            finish.wait(2)
            return {"value": request.id}
        runtime, replies = self.runtime(execute, aborted.put)
        runtime.start(7, "rig_job_wait", {}, {})
        runtime.start("7", "rig_job_wait", {}, {})
        requests = [entered.get(timeout=1), entered.get(timeout=1)]
        runtime.cancel(7)
        self.assertEqual(type(aborted.get(timeout=1).id), int)
        finish.set()
        for request in requests:
            request.thread.join(1)
        self.assertEqual(replies.get(timeout=1)["id"], "7")
        self.assertTrue(replies.empty())

    def test_cancel_before_binding_and_repeated_cancel_preserve_single_stop(self):
        entered, aborted = queue.Queue(), queue.Queue()
        bind, finish = threading.Event(), threading.Event()
        self.addCleanup(bind.set)
        self.addCleanup(finish.set)
        def execute(request):
            entered.put(request)
            bind.wait(2)
            request.targets = ["attached-a", "attached-b"]
            request.bound.set()
            finish.wait(2)
            return {}
        runtime, replies = self.runtime(execute, aborted.put)
        runtime.start("waiting", "rig_job_wait", {}, {})
        request = entered.get(timeout=1)
        runtime.cancel("waiting")
        runtime.cancel("waiting")
        self.assertTrue(request.stop.is_set())
        self.assertTrue(request.cancelled)
        self.assertFalse(runtime.progress_allowed(request))
        self.assertTrue(aborted.empty())
        bind.set()
        self.assertEqual(aborted.get(timeout=1).targets, ["attached-a", "attached-b"])
        finish.set()
        request.thread.join(1)
        self.assertTrue(aborted.empty())
        self.assertTrue(replies.empty())

    def test_saturated_ordinary_lane_keeps_cancellation_lane_available(self):
        entered, finish = queue.Queue(), threading.Event()
        self.addCleanup(finish.set)
        def execute(request):
            if request.name == "rig_job_cancel":
                return {"accepted": True}
            entered.put(request)
            finish.wait(3)
            return {}
        runtime, replies = self.runtime(execute)
        for number in range(8):
            self.assertIsNone(runtime.start(number, "rig_session", {}, {}))
        requests = [entered.get(timeout=1) for _ in range(8)]
        refused = runtime.start(9, "rig_session", {}, {})
        self.assertIn("busy", refused["error"]["message"])
        self.assertIsNone(runtime.start(10, "rig_job_cancel", {}, {}))
        self.assertTrue(replies.get(timeout=1)["result"]["accepted"])
        finish.set()
        for request in requests:
            request.thread.join(1)

    def test_completed_registry_entries_are_pruned_and_capacity_reusable(self):
        runtime, replies = self.runtime(lambda request: {"ok": True})
        for number in range(40):
            self.assertIsNone(runtime.start(number, "rig_session", {}, {}))
            self.assertEqual(replies.get(timeout=1)["id"], number)
        with runtime.lock:
            self.assertEqual(runtime.requests, {})
        self.assertIsNone(runtime.start(0, "rig_session", {}, {}))
        self.assertEqual(replies.get(timeout=1)["id"], 0)

    def test_system_exit_is_an_error_response_and_next_request_succeeds(self):
        def execute(request):
            if request.id == 1:
                raise SystemExit("target missing")
            return {"ok": True}
        runtime, replies = self.runtime(execute)
        runtime.start(1, "rig_session", {}, {})
        failed = replies.get(timeout=1)["result"]
        self.assertTrue(failed["isError"])
        self.assertIn("target missing", failed["content"][0]["text"])
        runtime.start(2, "rig_session", {}, {})
        self.assertTrue(replies.get(timeout=1)["result"]["ok"])

    def test_shutdown_is_bounded_and_detaches_without_worker_cancellation(self):
        entered, aborted = queue.Queue(), queue.Queue()
        def execute(request):
            entered.put(request)
            request.bound.set()
            request.stop.wait(3)
            return {}
        runtime, replies = self.runtime(execute, aborted.put)
        runtime.start(1, "rig_job_wait", {}, {})
        request = entered.get(timeout=1)
        started = time.monotonic()
        runtime.shutdown(timeout=0.15)
        self.assertLess(time.monotonic() - started, 0.5)
        request.thread.join(1)
        self.assertTrue(request.detached)
        self.assertFalse(request.cancelled)
        self.assertTrue(aborted.empty())
        self.assertTrue(replies.empty())


if __name__ == "__main__":
    unittest.main()
